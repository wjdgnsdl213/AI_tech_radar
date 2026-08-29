"""GeekNews 과거분 백필 — Internet Archive(Wayback Machine) 경유.

라이브 어댑터(geeknews.py)와 목적이 같고 수단만 다르다.

왜 별도 경로가 필요한가:
    GeekNews를 직접 훑으면 하루 150건이 한계다(그 이상은 403). 전체 약 32,900건을
    받으려면 7개월이 걸린다. 반면 아카이브에는 이미 28,526건이 저장돼 있고,
    이건 **GeekNews 서버에 요청을 단 한 번도 보내지 않고** 가져올 수 있다.

    즉 이건 차단 우회가 아니다. 원 서버 부담이 0이 되는 쪽으로 부담을 옮기는 것이고,
    Wayback은 애초에 이 용도로 공개된 아카이브다. 라이브 수집은 그대로 유지한다
    (아카이브에 없는 최신분·누락분을 채우는 역할).

수집 대상과 원칙은 라이브와 동일하다:
    · og:title / og:description(제목·요약)만 저장. 본문 DOM은 건드리지 않는다
    · 파싱은 geeknews.parse_topic_html()을 공유 — 경로에 따라 결과가 갈리면 안 된다
    · source="geeknews"로 넣는다. source_id가 topic id라 이미 받은 건은 자동 중복 제거된다

발행일:
    스냅샷 시각이 아니라 페이지에 박힌 실제 발행일을 쓴다.
    (검증: id=5672는 2025년에 저장됐지만 발행일 2022-01-03을 정확히 갖고 있다)

주의: Wayback도 429로 속도를 제한한다. 거부 신호를 받으면 즉시 멈추고 다음 실행에서 재개한다.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from src.sources.base import Batch, Item, Source
from src.sources.geeknews import Blocked, parse_topic_html

# CDX 인덱스의 URL 형태가 지저분하다. 같은 글이 여러 변형으로 들어있다:
#   ?id=7563&  /  ?id=19394&2  /  ?id=5672&amp;fbclid=...  /  /topic/32534.md
# 그래서 URL을 그대로 쓰지 않고 topic id를 뽑아 id 단위로 합친다.
_QUERY_ID_RE = re.compile(r"[?&]id=(\d+)")
_PATH_ID_RE = re.compile(r"/topic/(\d+)(?:\.\w+)?$")


def _topic_id(original: str) -> int | None:
    m = _QUERY_ID_RE.search(original) or _PATH_ID_RE.search(original)
    return int(m.group(1)) if m else None


class GeekNewsWaybackSource(Source):
    # ★ 라이브 수집분과 같은 source로 넣는다. source_id(topic id)가 같으면
    #   upsert_items가 중복을 걸러내므로 두 경로를 동시에 돌려도 안전하다.
    name = "geeknews"

    def __init__(self, cfg: dict[str, Any], common: dict[str, Any] | None = None) -> None:
        super().__init__(cfg, common)
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": self.common.get("user_agent", "ai-tech-radar")}
        )
        self._index: dict[int, str] | None = None      # topic id -> 스냅샷 timestamp
        self._ordered: list[int] | None = None         # id 내림차순 처리 순서

    # 인덱스 -------------------------------------------------------------
    def _fetch_index(self) -> dict[int, str]:
        """아카이브된 topic 페이지 목록을 CDX API로 받는다(약 32,000행 / 2MB).

        한 번에 다 받아 캐시한다. 매 배치마다 받으면 아카이브 쪽에 불필요한 부담이고,
        수만 건짜리 백필 도중 인덱스가 바뀌면 재개 지점이 흔들린다.
        """
        print("    CDX 인덱스 요청 중… (수십 초 걸릴 수 있음)")
        resp = self.session.get(
            self.cfg["cdx_url"], timeout=int(self.cfg.get("index_timeout", 180))
        )
        if resp.status_code in (403, 429):
            raise Blocked(f"CDX HTTP {resp.status_code}")
        resp.raise_for_status()

        # 응답이 잘려 오는 경우가 있어 통째로 json.loads 하지 않고 줄 단위로 파싱한다.
        # 마지막 줄이 깨져도 앞부분은 쓸 수 있어야 한다.
        index: dict[int, str] = {}
        for line in resp.text.splitlines():
            line = line.strip().rstrip(",")
            if not line.startswith("["):
                continue
            try:
                original, ts = json.loads(line)
            except (ValueError, TypeError):
                continue
            if original == "original":      # 헤더 행
                continue
            tid = _topic_id(original)
            if tid is None:
                continue
            # 같은 id의 스냅샷이 여러 개면 가장 최근 것 — 레이아웃이 새것에 가깝다
            if tid not in index or ts > index[tid]:
                index[tid] = ts
        return index

    def _known_ids(self) -> set[str]:
        """이미 DB에 있는 geeknews 항목 id. 아카이브에 다시 요청하지 않기 위해서다.

        중복 자체는 upsert가 막아주지만 그건 요청을 보낸 뒤의 얘기다.
        라이브로 이미 받은 수백~수천 건을 다시 긁는 건 순수한 낭비라 여기서 뺀다.
        """
        if not self.cfg.get("skip_existing", True):
            return set()
        # 소스 어댑터가 DB를 아는 건 계층상 예외적이지만, 이 최적화는 요청 수를
        # 직접 줄이므로 값어치가 있다. 실패해도 수집은 계속돼야 하니 감싼다.
        try:
            from sqlalchemy import select

            from src.db import get_engine, items
            with get_engine().connect() as conn:
                rows = conn.execute(
                    select(items.c.source_id).where(items.c.source == "geeknews")
                )
                return {r[0] for r in rows}
        except Exception as exc:
            print(f"    (기존 항목 조회 실패 — 전체 재수집으로 진행: {exc})")
            return set()

    def _load_index(self) -> list[int]:
        """캐시를 읽거나 새로 받아 'id 내림차순 목록'을 만든다."""
        if self._ordered is not None:
            return self._ordered

        cache = Path(self.cfg["index_cache"])
        ttl_days = float(self.cfg.get("index_ttl_days", 7))
        index: dict[int, str] | None = None

        if cache.exists():
            age_days = (time.time() - cache.stat().st_mtime) / 86400
            if age_days < ttl_days:
                raw = json.loads(cache.read_text(encoding="utf-8"))
                index = {int(k): v for k, v in raw.items()}
                print(f"    CDX 캐시 사용 ({len(index):,}건, {age_days:.1f}일 전)")

        if index is None:
            index = self._fetch_index()
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(
                json.dumps({str(k): v for k, v in index.items()}), encoding="utf-8"
            )
            print(f"    CDX 인덱스 {len(index):,}건 저장 -> {cache}")

        self._index = index
        known = self._known_ids()
        ordered = [t for t in sorted(index, reverse=True) if str(t) not in known]
        if known:
            print(f"    이미 보유 {len(known):,}건 제외 -> 대상 {len(ordered):,}건")
        self._ordered = ordered
        return ordered

    # 스냅샷 1건 ---------------------------------------------------------
    def _fetch_snapshot(self, tid: int) -> Item | None:
        """'id_' 접미사를 붙여 원본 그대로의 스냅샷을 받는다.

        접미사가 없으면 Wayback이 툴바 스크립트와 링크 재작성을 주입한 HTML을 준다.
        메타태그만 읽더라도 원본을 받아야 파싱이 라이브와 완전히 같아진다.
        """
        ts = (self._index or {}).get(tid)
        if not ts:
            return None
        origin = self.cfg["topic_url"].format(id=tid)
        url = self.cfg["snapshot_url"].format(timestamp=ts, url=origin)

        retries = int(self.cfg.get("max_retries", 2))
        backoff = float(self.cfg.get("retry_backoff", 5.0))
        for attempt in range(1, retries + 1):
            try:
                resp = self.session.get(url, timeout=int(self.cfg.get("timeout", 60)))
                if resp.status_code in (403, 429):
                    raise Blocked(f"HTTP {resp.status_code} at id={tid}")
                if resp.status_code >= 400:
                    return None      # 404 등 — 그 스냅샷은 없다. 정상 상황
                break
            except Blocked:
                raise
            except requests.RequestException as exc:
                if attempt == retries:
                    print(f"    id={tid} 실패({type(exc).__name__}) — 건너뜀")
                    return None
                time.sleep(backoff * attempt)
        else:
            return None

        # 라이브와 동일: 서버가 ISO-8859-1이라 주장하지만 실제 본문은 UTF-8이다
        resp.encoding = "utf-8"
        item = parse_topic_html(resp.text, origin, tid, via="wayback")
        if item is not None:
            item.meta["snapshot"] = ts
        return item

    # Source 인터페이스 ---------------------------------------------------
    def fetch(self, since: datetime, until: datetime, cursor: str | None) -> Batch:
        """커서 = 다음에 처리할 topic id. 인덱스를 id 내림차순으로 훑는다."""
        ordered = self._load_index()
        if not ordered:
            return Batch(items=[], cursor=None, note="대상 없음(이미 전부 보유)")

        start = 0 if cursor is None else next(
            (i for i, t in enumerate(ordered) if t <= int(cursor)), len(ordered)
        )
        if start >= len(ordered):
            return Batch(items=[], cursor=None, note="인덱스 끝 도달")

        interval = float(self.cfg.get("request_interval", 1.0))
        batch_size = int(self.cfg.get("batch_size", 50))
        items: list[Item] = []
        i = start

        while i < len(ordered) and len(items) < batch_size:
            tid = ordered[i]
            try:
                item = self._fetch_snapshot(tid)
            except Blocked as exc:
                # 지금까지 받은 건 살리고 커서를 남긴 채 종료 -> 다음 실행이 여기서 이어간다
                print(f"    [중단] 아카이브 접근 거부({exc})")
                return Batch(items=items, cursor=str(tid), note="서버 거부로 중단")
            i += 1
            time.sleep(interval)
            if item is None:
                continue

            # id 내림차순 = 발행일 내림차순이므로, since 이전이 나오면 끝이다
            if item.published_at:
                try:
                    pub = datetime.fromisoformat(item.published_at.replace("Z", "+00:00"))
                    if pub.tzinfo is None:
                        pub = pub.replace(tzinfo=timezone.utc)
                    if pub < since:
                        items.append(item)
                        return Batch(items=items, cursor=None,
                                     note=f"since({since:%Y-%m-%d}) 도달")
                except ValueError:
                    pass
            items.append(item)

        if i >= len(ordered):
            return Batch(items=items, cursor=None, note="인덱스 전체 완료")
        return Batch(items=items, cursor=str(ordered[i]), note=f"id={ordered[i]}까지")
