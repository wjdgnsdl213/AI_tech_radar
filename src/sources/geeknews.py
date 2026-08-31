"""GeekNews(news.hada.io) 어댑터.

(a) 분석 · (c) 인프라 관점의 국내 실무 정보가 한글 요약으로 정리돼 있어 가치가 높다.

두 가지 경로를 쓴다:
  1) 전방 수집(RSS) — 완전히 안전. 피드가 애초에 요약(~120자)만 주므로
     "제목+요약만 저장" 원칙과 구조적으로 충돌할 수 없다.
  2) 백필(topic id 역주행) — 피드는 최근 20~30건만 주기 때문에 과거를 못 가져온다.
     topic id가 순차라 역방향 순회가 가능하다(검증: id=32898이 2026-08-26 최신).

⚠️ 백필은 개인이 운영하는 사이트를 직접 훑는 일이다. 다음을 반드시 지킨다:
     · 요청 간격 ≥ 1.5초 (config.sources.geeknews.request_interval)
     · User-Agent에 프로젝트명 + 담당자 연락처 명시
     · og: 메타태그(제목·요약)만 읽고 본문 DOM은 건드리지 않는다
     · robots.txt Disallow 경로(/api/, /auth/, /login, /settings/)는 접근하지 않는다
     · 수집 범위 상한(backfill_max_items)을 두고, 업무시간 외 실행을 권장
   불편하면 config에서 backfill: false로 끄면 된다. GeekNews 항목 상당수가 HN발이라
   HN 백필로 상당 부분 커버된다.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

import feedparser
import requests
from bs4 import BeautifulSoup

from src.sources.base import Batch, Item, Source, clean_text

_ID_RE = re.compile(r"[?&]id=(\d+)")
_TITLE_SUFFIX_RE = re.compile(r"\s*\|\s*GeekNews\s*$")


_JSONLD_DATE_RE = re.compile(r'"datePublished"\s*:\s*"([^"]+)"')

# 삭제된 topic은 404가 아니라 200 + 안내 페이지로 돌아온다. 제목이 이걸로 나오면
# 글이 아니라 "없음" 신호다. 발행일이 없는 채로 적재되면 트렌드 집계에 빈 행이 낀다.
_NOT_FOUND_TITLE = "그 뉴스를 못찾으신다면"

# 과도한 요청에 대해 403/429가 아니라 200 + 익명화된 봇 체크 페이지로 응답할 때가 있다
# (og: 메타태그 자체가 없어 title이 <title>브라우저 확인</title>로만 떨어진다).
# 이걸 놓치면 Blocked가 터지지 않아 차단 상태로 계속 요청하며 빈 글을 실제 글처럼 적재한다.
# (2026-08-30 실사고: 76건이 title="브라우저 확인"으로 발행일 없이 적재됨)
_BOT_CHALLENGE_TITLE = "브라우저 확인"


def _published_at(soup) -> str:
    """발행일을 여러 경로로 시도한다.

    이 프로젝트는 트렌드 분석이 목적이라 published_at이 비면 그 항목은 사실상 쓸 수 없다.
    그런데 GeekNews는 시기별로 레이아웃이 달라서 한 곳만 보면 놓친다:
      · article:published_time — 구·신 레이아웃 모두 존재. 가장 신뢰할 수 있다
      · JSON-LD datePublished  — 위와 동일 값을 담고 있어 이중 안전장치
      · <time datetime>        — 현재 레이아웃에만 있다 (2024년 스냅샷엔 없음)
    """
    tag = soup.find("meta", property="article:published_time")
    if tag and tag.get("content"):
        return tag["content"].strip()

    m = _JSONLD_DATE_RE.search(str(soup))
    if m:
        return m.group(1).strip()

    t = soup.find("time")
    if t and t.get("datetime"):
        return t["datetime"].strip()

    return ""


def parse_topic_html(html: str, url: str, tid: int, via: str) -> Item | None:
    """topic 페이지 HTML → Item. 라이브 수집과 Wayback 백필이 공유한다.

    og: 메타태그만 읽는다. 사이트가 배포용으로 내놓는 값이라 본문 DOM을 파싱하는 것보다
    "제목+요약만 저장" 원칙에 맞고 레이아웃 변경에도 강하다.

    두 어댑터가 같은 함수를 쓰는 이유: 파싱이 갈라지면 같은 글이 경로에 따라 다른
    제목·발행일로 들어와 중복 제거와 트렌드 집계가 동시에 틀어진다.
    """
    soup = BeautifulSoup(html, "html.parser")

    def og(prop: str) -> str:
        tag = soup.find("meta", property=f"og:{prop}")
        return clean_text(tag.get("content")) if tag and tag.get("content") else ""

    title = _TITLE_SUFFIX_RE.sub(
        "", og("title") or clean_text(soup.title.string if soup.title else ""))
    if title == _BOT_CHALLENGE_TITLE:
        # "없는 글"과 달리 이건 서버가 우리를 거부하고 있다는 신호다 — 계속 두드리면 안 된다.
        raise Blocked(f"봇 체크 페이지 응답 at id={tid}")
    if not title or _NOT_FOUND_TITLE in title:
        return None

    return Item(
        source="geeknews",
        source_id=str(tid),
        title=title,
        summary=og("description"),
        url=url,
        published_at=_published_at(soup),
        meta={"via": via},
    )


class Blocked(Exception):
    """서버가 접근을 거부했다(403). 즉시 중단하고 다음 날 재개한다.

    이전 구현은 403을 '실패한 항목'으로 보고 건너뛰며 계속 요청해서, 한 번의
    실행에서 403을 199회 두드렸다. 거부 신호를 받으면 두드리기를 멈추는 게 맞다.
    """


def _topic_id(link: str) -> str | None:
    m = _ID_RE.search(link or "")
    return m.group(1) if m else None


class GeekNewsSource(Source):
    name = "geeknews"

    def __init__(self, cfg: dict[str, Any], common: dict[str, Any] | None = None) -> None:
        super().__init__(cfg, common)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.common.get("user_agent", "sab-trend")})

    # ── 전방 수집 (RSS) ──────────────────────────────────────────────
    def fetch_recent(self) -> list[Item]:
        """Atom 피드에서 최신 항목을 가져온다. collect.py(반복 수집)가 쓴다."""
        feed = feedparser.parse(self.cfg["rss_url"])
        items: list[Item] = []
        for e in feed.entries:
            link = getattr(e, "link", "") or getattr(e, "id", "")
            tid = _topic_id(link)
            if not tid:
                continue
            content = ""
            if getattr(e, "content", None):
                content = e.content[0].get("value", "")
            items.append(
                Item(
                    source=self.name,
                    source_id=tid,
                    title=clean_text(getattr(e, "title", "")),
                    summary=clean_text(content),
                    url=link,
                    published_at=getattr(e, "published", "") or getattr(e, "updated", ""),
                    meta={"author": getattr(e, "author", None), "via": "rss"},
                )
            )
        return items

    def latest_topic_id(self) -> int | None:
        """피드 최상단 항목의 topic id — 백필 역주행의 출발점."""
        ids = [int(i.source_id) for i in self.fetch_recent() if i.source_id.isdigit()]
        return max(ids) if ids else None

    # ── 백필 (topic id 역주행) ───────────────────────────────────────
    def _fetch_topic(self, tid: int) -> Item | None:
        """topic 페이지를 직접 받아 파싱한다. 파싱은 parse_topic_html()이 담당."""
        url = self.cfg["topic_url"].format(id=tid)
        retries = int(self.cfg.get("max_retries", 2))
        backoff = float(self.cfg.get("retry_backoff", 3.0))
        for attempt in range(1, retries + 1):
            try:
                resp = self.session.get(url, timeout=15)
                if resp.status_code == 404:
                    return None          # 삭제되거나 비어있는 id — 정상 상황
                if resp.status_code in (403, 429):
                    # 거부·속도제한 신호. 재시도하지 않고 이번 실행을 끝낸다.
                    raise Blocked(f"HTTP {resp.status_code} at id={tid}")
                resp.raise_for_status()
                break
            except Blocked:
                raise
            except requests.RequestException as exc:
                if attempt == retries:
                    print(f"    id={tid} 실패({exc}) — 건너뜀")
                    return None
                time.sleep(backoff * attempt)
        else:
            return None

        # 서버가 Content-Type에 ISO-8859-1을 실어 보내지만 실제 본문은 UTF-8이다.
        # requests의 자동 추론에 맡기면 한글이 깨진다(예: id=31000 → 'ì¸ì').
        resp.encoding = "utf-8"
        return parse_topic_html(resp.text, url, tid, via="backfill")

    def fetch(self, since: datetime, until: datetime, cursor: str | None) -> Batch:
        """커서 = 다음에 읽을 topic id. 최신 id에서 시작해 1씩 내려간다."""
        if not self.cfg.get("backfill", False):
            return Batch(items=[], cursor=None, note="backfill 비활성화(config)")

        if cursor is None:
            latest = self.latest_topic_id()
            if latest is None:
                return Batch(items=[], cursor=None, note="피드에서 최신 id를 못 찾음")
            start = latest
        else:
            start = int(cursor)

        # 상한: 최신 id 기준 backfill_max_items개까지만 (대략 1~2년치)
        max_items = int(self.cfg.get("backfill_max_items", 12000))
        floor_id = max(1, start - max_items)
        if start <= floor_id:
            return Batch(items=[], cursor=None, note="수집 범위 상한 도달")

        interval = float(self.cfg.get("request_interval", 1.5))
        if interval < 1.5:
            # 예의상의 하한을 코드에서도 못박는다 — config 실수로 사이트에 부담 주지 않게
            interval = 1.5

        items: list[Item] = []
        tid = start
        # 한 배치는 50건 — 체크포인트 갱신 주기를 촘촘히 유지한다(1.5초 × 50 ≈ 75초)
        for _ in range(50):
            if tid <= floor_id:
                break
            try:
                item = self._fetch_topic(tid)
            except Blocked as exc:
                # 서버가 거부했다. 지금까지 받은 건 저장하고 커서를 남긴 채 종료한다.
                # cursor를 유지해야 다음 실행이 여기서 이어간다.
                print(f"    ⛔ 접근 거부({exc}) — 이번 실행 중단. 다음 실행에서 재개")
                return Batch(items=items, cursor=str(tid), note="서버 거부로 중단")
            tid -= 1
            time.sleep(interval)
            if item is None:
                continue
            # since 이전 항목이 나오면 종료 (발행일을 못 읽은 항목은 통과시킨다)
            if item.published_at:
                try:
                    pub = datetime.fromisoformat(item.published_at.replace("Z", "+00:00"))
                    if pub.tzinfo is None:
                        pub = pub.replace(tzinfo=timezone.utc)
                    if pub < since:
                        items.append(item)
                        return Batch(items=items, cursor=None, note=f"since({since:%Y-%m-%d}) 도달")
                except ValueError:
                    pass
            items.append(item)

        next_cursor = str(tid) if tid > floor_id else None
        return Batch(items=items, cursor=next_cursor, note=f"id={tid}까지")
