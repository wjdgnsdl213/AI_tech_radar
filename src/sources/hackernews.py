"""Hacker News 어댑터 (Algolia Search API).

(c) 인프라·플랫폼 운영 관점의 실무 사례가 주 수확물. 2007년부터 전체 아카이브가
공개돼 있고 인증이 필요 없어서, 이 프로젝트 baseline의 핵심 소스다.

검증(2026-08-26):
    GET https://hn.algolia.com/api/v1/search_by_date
        ?tags=story&numericFilters=created_at_i>1672531200,created_at_i<1672617600
    → 2023-01-01 하루치 반환, nbHits 586 / nbPages 196, 무인증

페이징 전략 — page 파라미터를 쓰지 않고 시간 커서로 역주행한다:
    Algolia는 page 깊이에 상한이 있어 오래된 구간을 못 파고든다. search_by_date가
    최신순 정렬이므로, 매 배치의 가장 오래된 created_at_i를 다음 상한으로 삼아
    과거로 계속 밀고 내려가면 상한 없이 전 구간을 훑을 수 있다.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import requests

from src.sources.base import Batch, Item, Source, clean_text


class HackerNewsSource(Source):
    name = "hackernews"

    def __init__(self, cfg: dict[str, Any], common: dict[str, Any] | None = None,
                 query: str = "") -> None:
        super().__init__(cfg, common)
        self.query = query
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.common.get("user_agent", "ai-tech-radar")})

    def tasks(self) -> list[str]:
        # 쿼리별로 따로 훑는다 — 체크포인트도 쿼리 단위로 갈린다
        return list(self.cfg.get("queries") or [""])

    def with_task(self, task: str) -> "HackerNewsSource":
        return HackerNewsSource(self.cfg, self.common, query=task)

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        """실패 시 지수 백오프로 재시도."""
        retries = int(self.cfg.get("max_retries", 3))
        backoff = float(self.cfg.get("retry_backoff", 2.0))
        for attempt in range(1, retries + 1):
            try:
                resp = self.session.get(self.cfg["api_url"], params=params, timeout=20)
                if resp.status_code == 429:
                    time.sleep(backoff * attempt)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                if attempt == retries:
                    raise
                print(f"    요청 실패({exc}) — {attempt}회차 재시도 대기")
                time.sleep(backoff * attempt)
        return {}

    def fetch(self, since: datetime, until: datetime, cursor: str | None) -> Batch:
        # 커서는 "다음 배치의 상한 타임스탬프". 첫 호출이면 until에서 출발한다.
        upper = int(cursor) if cursor else int(until.timestamp())
        lower = int(since.timestamp())
        if upper <= lower:
            return Batch(items=[], cursor=None)

        filters = [f"created_at_i>{lower}", f"created_at_i<{upper}"]
        min_points = self.cfg.get("min_points")
        if min_points:
            filters.append(f"points>={int(min_points)}")

        params: dict[str, Any] = {
            "tags": "story",
            "numericFilters": ",".join(filters),
            "hitsPerPage": int(self.cfg.get("hits_per_page", 100)),
        }
        if self.query:
            # ⚠️ Algolia 기본 설정으로 query를 넘기면 필터 역할을 못 한다.
            #   실측(2026-08-30, 최근 30일·points>=30):
            #     query=RAG 기본값                  → 224건 (GrapheneOS·치킨 리콜… 전부 무관)
            #     + restrictSearchableAttributes    → 131건 (여전히 무관)
            #     + typoTolerance=false             →   3건 (정상)
            #   원인은 removeWordsIfNoResults다 — 매칭이 적으면 Algolia가 쿼리를
            #   통째로 버리고 날짜순 전체를 돌려준다. 그래서 셋을 함께 못박는다:
            #     · removeWordsIfNoResults=none   쿼리를 버리지 않는다 (핵심)
            #     · restrictSearchableAttributes  제목에서만 찾는다 (url·author 오탐 차단)
            #     · typoTolerance=false           'rag'가 'rage'를 잡는 걸 막는다
            params.update({
                "query": self.query,
                "removeWordsIfNoResults": "none",
                "restrictSearchableAttributes": "title",
                "typoTolerance": "false",
            })

        data = self._get(params)
        hits = data.get("hits", [])
        time.sleep(float(self.cfg.get("request_interval", 0.3)))

        if not hits:
            return Batch(items=[], cursor=None, note="구간 끝")

        items: list[Item] = []
        oldest = upper
        for h in hits:
            ts = h.get("created_at_i")
            if ts is None:
                continue
            oldest = min(oldest, int(ts))
            items.append(
                Item(
                    source=self.name,
                    source_id=str(h.get("objectID")),
                    title=clean_text(h.get("title") or h.get("story_title")),
                    # HN 스토리는 요약이 없다. 셀프포스트 본문(story_text)이 있으면
                    # 앞부분만 요약 대용으로 쓰고, 없으면 제목만으로 간다.
                    summary=clean_text(h.get("story_text"))[:500],
                    url=h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                    published_at=h.get("created_at", ""),
                    meta={
                        "points": h.get("points"),
                        "num_comments": h.get("num_comments"),
                        "author": h.get("author"),
                        "query": self.query,
                    },
                )
            )

        # 같은 초에 여러 건이 몰리면 커서가 안 움직여 무한루프가 된다 → 1초 당겨준다
        next_cursor = str(oldest - 1) if oldest - 1 > lower else None
        note = datetime.fromtimestamp(oldest).strftime("%Y-%m-%d까지")
        return Batch(items=items, cursor=next_cursor, note=note)
