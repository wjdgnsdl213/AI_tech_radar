"""네이버 뉴스 검색 API 어댑터 — 소상공인·AI·빅데이터·규제 뉴스를 한 경로로 수집한다.

sobiz-trend-radar의 검증된 수집기(`src/collect.py`)를 어댑터 인터페이스로 옮긴 것이다.
재시도·rate limit 준수·최신순 조기 종료 로직은 그대로 가져왔다.

★ 이 소스는 백필이 불가능하다 — 그래서 매일 돌려야 한다.
    API가 start ≤ 1000 까지만 페이징을 허용하고 기간 지정 파라미터가 없다.
    쿼리당 최대 1,000건, 그것도 최신순 앞에서부터다. 즉 **오늘 안 받으면 오늘 뉴스는
    영영 못 받는다.** (과거분은 sobiz DB 임포트로 한 번 채워뒀다 — import_sobiz.py)
    필터·점수는 나중에 소급 적용되지만 수집은 소급되지 않는다는 게 이 소스의 전제다.

source_id는 import_sobiz.py와 같은 sha1(link)[:16] 규칙을 쓴다.
sobiz 임포트분(source='sobiz_news')과 이 소스(source='naver_news')는 DB상 별도 행이
되지만(UNIQUE는 source까지 포함), 같은 기사면 해시가 같아 나중에 짝을 찾을 수 있다.
소스 간 중복 통합은 설계대로 filter 단계의 dedup이 담당한다.
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from src.sources.base import Batch, Item, Source, clean_text

MAX_DISPLAY = 100    # 1회 호출 최대 건수 (API 제한)
MAX_START = 1000     # start 파라미터 상한 (API 제한) — 쿼리당 사실상의 수집 한계


class AuthError(RuntimeError):
    """API 키 문제. 재시도해도 소용없으므로 이 소스만 중단한다.

    sobiz 원본은 여기서 sys.exit 했지만, 여러 소스를 순회하는 수집 엔진에서는
    한 소스의 키 문제가 나머지 소스를 죽이면 안 된다 — 예외로 올려 드라이버가 격리한다.
    """


def _source_id(link: str) -> str:
    """link가 길고 쿼리스트링이 붙어 있어 해시로 안정적인 짧은 ID를 만든다.
    (import_sobiz.py와 동일한 규칙 — 같은 기사면 같은 해시가 나와야 한다)"""
    return hashlib.sha1(link.encode("utf-8")).hexdigest()[:16]


class NaverNewsSource(Source):
    name = "naver_news"

    def __init__(self, cfg: dict[str, Any], common: dict[str, Any] | None = None,
                 query: str = "") -> None:
        super().__init__(cfg, common)
        self.query = query
        client_id = os.getenv("NAVER_CLIENT_ID")
        client_secret = os.getenv("NAVER_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise AuthError(
                "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET이 없습니다. "
                ".env.example을 .env로 복사한 뒤 키를 입력하세요."
            )
        self.session = requests.Session()
        self.session.headers.update({
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
            "User-Agent": self.common.get("user_agent", "sab-trend"),
        })

    def tasks(self) -> list[str]:
        # 쿼리별로 따로 훑는다. 쿼리 목록이 곧 수집 주제 범위다
        # (소상공인 / AI / 빅데이터 / 규제 — config.sources.naver_news.queries)
        return list(self.cfg.get("queries") or [""])

    def with_task(self, task: str) -> "NaverNewsSource":
        return NaverNewsSource(self.cfg, self.common, query=task)

    def _get(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """검색 결과 한 페이지. 실패 시 지수 백오프로 재시도."""
        retries = int(self.cfg.get("max_retries", 3))
        backoff = float(self.cfg.get("retry_backoff", 2.0))
        for attempt in range(1, retries + 1):
            try:
                resp = self.session.get(self.cfg["api_url"], params=params, timeout=15)
                if resp.status_code == 429:
                    time.sleep(backoff * attempt)
                    continue
                if resp.status_code in (401, 403):
                    raise AuthError(
                        f"네이버 API 인증 실패 (HTTP {resp.status_code}): {resp.text[:200]}\n"
                        "  개발자센터에서 키와 '검색' API 사용 설정을 확인하세요."
                    )
                resp.raise_for_status()
                return resp.json().get("items", [])
            except requests.RequestException as exc:
                if attempt == retries:
                    raise
                print(f"    요청 실패({exc}) — {attempt}회차 재시도 대기")
                time.sleep(backoff * attempt)
        return []

    def fetch(self, since: datetime, until: datetime, cursor: str | None) -> Batch:
        """[since, until] 구간을 커서(=다음 start 오프셋)부터 한 페이지 가져온다.

        API에 기간 파라미터가 없어 최신순으로 받아 내려가다 since보다 오래된 기사가
        나오면 그 쿼리를 끝낸다(그 뒤는 전부 더 오래됐다).
        """
        start = int(cursor) if cursor else 1
        if start > MAX_START:
            # 쿼리당 상한. 여기에 자주 걸린다면 쿼리를 더 좁게 쪼개야 한다는 신호다
            return Batch(items=[], cursor=None, note=f"start 상한({MAX_START}) 도달")

        page = self._get({
            "query": self.query,
            "start": start,
            "display": MAX_DISPLAY,
            "sort": self.cfg.get("sort", "date"),
        })
        time.sleep(float(self.cfg.get("request_interval", 0.11)))   # 초당 10회 제한 준수

        if not page:
            return Batch(items=[], cursor=None, note="결과 끝")

        items: list[Item] = []
        reached_old = False
        oldest: datetime | None = None
        for row in page:
            link = row.get("originallink") or row.get("link") or ""
            if not link:
                continue
            try:
                pub = parsedate_to_datetime(row["pubDate"])
            except (KeyError, TypeError, ValueError):
                continue
            oldest = pub if oldest is None else min(oldest, pub)
            if pub < since:
                reached_old = True
                continue
            if pub > until:
                continue
            items.append(
                Item(
                    source=self.name,
                    source_id=_source_id(link),
                    title=clean_text(row.get("title")),
                    summary=clean_text(row.get("description")),
                    url=link,
                    published_at=pub.isoformat(),
                    meta={"query": self.query, "naver_link": row.get("link")},
                )
            )

        # since보다 오래된 기사가 나왔으면 이 쿼리는 끝. 아니면 다음 페이지로.
        next_start = start + MAX_DISPLAY
        next_cursor = None if (reached_old or next_start > MAX_START) else str(next_start)
        note = f"{oldest:%Y-%m-%d}까지" if oldest else ""
        return Batch(items=items, cursor=next_cursor, note=note)
