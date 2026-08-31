"""연관어 망 응답 캐시.

'소상공인'처럼 흔한 말은 3.7초가 걸린다(15,390건). 클릭 한 번에 4초를 기다리면
망을 이리저리 헤집어 보는 게 불가능해진다 — 그런데 그렇게 헤집는 게 이 화면의
용도다. 같은 질의가 반복되는 비율이 높아(추천 키워드, 홉 슬라이더 왕복,
더블클릭 재중심) 캐시가 잘 듣는다.

★ 만료를 시간이 아니라 **데이터 버전**으로 잡는다.
  TTL만 쓰면 파이프라인을 돌린 뒤에도 한동안 옛 결과가 나가는데, 언제까지
  옛것인지 사용자가 알 방법이 없다. 대신 items와 item_keywords의 최댓값을
  버전으로 삼아, 수집·추출이 돌면 캐시 전체가 자연히 빗나가게 한다.
  버전 조회 자체는 30초 캐시한다 — 매 요청마다 두 번 왕복할 이유는 없다.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

from sqlalchemy import func, select

from src.db import get_engine, items, kw_engine
from src.extract import item_keywords

_STAMP_TTL = 30.0        # 데이터 버전을 다시 확인하는 주기(초)
_MAX_ENTRIES = 256       # 항목당 수십 KB — 망 응답 기준으로 넉넉하다


class Cache:
    """데이터 버전이 바뀌면 통째로 버려지는 LRU."""

    def __init__(self, max_entries: int = _MAX_ENTRIES) -> None:
        self._d: OrderedDict[Any, Any] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_entries
        self._stamp: tuple | None = None
        self._stamp_at = 0.0
        self.hits = 0
        self.misses = 0

    # ── 데이터 버전 ────────────────────────────────────────────────
    def _fresh_stamp(self) -> tuple:
        """items와 item_keywords의 최댓값. 둘 다 append 위주라 단조 증가한다."""
        with get_engine().connect() as c:
            a = c.execute(select(func.max(items.c.id))).scalar_one_or_none()
        try:
            with kw_engine().connect() as c:
                b = c.execute(select(func.count()).select_from(item_keywords)).scalar_one()
        except Exception:
            b = -1          # 키워드 DB가 아직 없을 수 있다 — 캐시를 막을 일은 아니다
        return (a, b)

    def stamp(self) -> tuple:
        now = time.time()
        if self._stamp is None or now - self._stamp_at > _STAMP_TTL:
            self._stamp = self._fresh_stamp()
            self._stamp_at = now
        return self._stamp

    # ── 조회 ──────────────────────────────────────────────────────
    def get_or_call(self, key: tuple, fn: Callable[[], Any]) -> Any:
        st = self.stamp()
        with self._lock:
            if self._stamp_key != st:
                self._d.clear()
                self._stamp_key = st
            if key in self._d:
                self._d.move_to_end(key)
                self.hits += 1
                return self._d[key]
        # ★ 락 밖에서 계산한다. 3초짜리 질의를 락 안에서 돌리면 다른 요청이
        #   전부 막힌다. 같은 키가 동시에 두 번 계산될 수는 있지만(드물고 무해),
        #   한 요청이 서버 전체를 세우는 것보다 낫다.
        val = fn()
        with self._lock:
            self._d[key] = val
            self._d.move_to_end(key)
            while len(self._d) > self._max:
                self._d.popitem(last=False)
            self.misses += 1
        return val

    _stamp_key: tuple | None = None

    def info(self) -> dict[str, Any]:
        with self._lock:
            return {"entries": len(self._d), "hits": self.hits, "misses": self.misses,
                    "max": self._max}


ego_cache = Cache()


def warm(keywords: list[str], call: Callable[..., Any]) -> None:
    """자주 열릴 키워드를 미리 계산해 둔다.

    첫 사용자가 대기 시간을 다 뒤집어쓰지 않게 하는 게 목적이다.
    데몬 스레드에서 조용히 돌고, 실패해도 서버를 멈추지 않는다.
    WARM_CACHE=0 으로 끌 수 있다 — 개발 중 서버를 자주 켤 때 거슬린다.
    """
    if os.getenv("WARM_CACHE", "1") == "0":
        return

    def run() -> None:
        t0 = time.time()
        for kw in keywords:
            for hops in (1, 2):
                try:
                    # 화면(app.js loadEgo)이 보내는 값과 같아야 캐시 키가 맞는다
                    call(kw=kw, hops=hops, per_hop=8 if hops > 1 else 14,
                         min_cooc=0, max_nodes=46)
                except Exception as e:
                    print(f"[cache] {kw} {hops}홉 예열 실패: {e}", flush=True)
        print(f"[cache] {len(keywords)}개 키워드 예열 {time.time() - t0:.1f}초",
              flush=True)

    threading.Thread(target=run, daemon=True, name="cache-warm").start()
