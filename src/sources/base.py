"""소스 어댑터 공통 스키마와 인터페이스.

소스마다 응답 형태가 제각각(Algolia JSON / Atom 피드 / 네이버 API / 공공 API)이라,
어댑터가 전부 Item 하나로 정규화한 뒤에야 파이프라인 뒷단이 소스를 신경 쓰지 않는다.

핵심 설계 — 커서 기반 배치:
    백필은 수만 건 · 시간 단위 작업이라 중간에 죽으면 처음부터 다시 돌릴 수 없다.
    그래서 소스는 "한 배치 + 다음 재개 지점(cursor)"을 돌려주고, 드라이버(backfill.py)가
    배치마다 체크포인트를 저장한다. 커서의 의미는 소스가 알아서 정한다
    (HN=타임스탬프, GeekNews=topic id) — 드라이버는 문자열로만 취급한다.
"""

from __future__ import annotations

import html
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(text: str | None) -> str:
    """HTML 태그·엔티티를 걷어내고 공백을 정규화한다.

    GeekNews content는 <ul><li> 마크업이고 네이버 API는 <b> 태그를 섞어 보내므로
    모든 어댑터가 이걸 통과시킨다.
    """
    if not text:
        return ""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", text))).strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Item:
    """모든 소스가 이 형태로 정규화된다.

    published_at(발행일)과 collected_at(수집일)을 반드시 분리한다 —
    백필 데이터는 수집일이 오늘이지만 발행일은 몇 년 전이다.
    이걸 안 나누면 트렌드 계산이 전부 망가진다.
    """

    source: str                     # 'hackernews' | 'geeknews' | 'naver_news' | ...
    source_id: str                  # 소스 내 고유 ID (중복 적재 방지 키)
    title: str
    summary: str                    # 요약만. 본문은 저장하지 않는다
    url: str
    published_at: str               # 원문 발행일 (ISO8601)
    collected_at: str = field(default_factory=now_iso)
    meta: dict[str, Any] = field(default_factory=dict)   # 소스 고유 필드(points 등)

    def key(self) -> str:
        """DB 유일 키. 소스가 다르면 같은 URL이라도 별도 항목으로 둔다
        (같은 뉴스의 소스 간 중복 통합은 filter 단계의 dedup이 담당)."""
        return f"{self.source}:{self.source_id}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Batch:
    """한 번의 fetch 결과.

    cursor가 None이면 더 가져올 게 없다는 뜻(수집 종료).
    드라이버는 이 배치를 저장한 뒤에 cursor를 체크포인트에 기록한다 —
    순서가 반대면 저장 실패 시 데이터가 유실된다.
    """

    items: list[Item]
    cursor: str | None
    note: str = ""          # 진행 로그에 찍을 부가 정보 (예: "2024-03-11까지")


class Source(ABC):
    """소스 어댑터 공통 인터페이스.

    구현체는 name과 fetch()만 채우면 백필·수집 드라이버가 그대로 돌려준다.
    """

    name: str = "base"

    def __init__(self, cfg: dict[str, Any], common: dict[str, Any] | None = None) -> None:
        self.cfg = cfg
        self.common = common or {}

    @abstractmethod
    def fetch(self, since: datetime, until: datetime, cursor: str | None) -> Batch:
        """[since, until] 구간을 커서부터 이어서 한 배치 가져온다.

        cursor=None이면 처음부터 시작. 반환된 Batch.cursor를 다음 호출에 그대로 넘긴다.
        """

    def tasks(self) -> list[str]:
        """백필을 쪼갤 단위. 기본은 단일 작업.

        HN처럼 쿼리별로 따로 훑어야 하는 소스는 쿼리 목록을 돌려주고,
        드라이버가 작업별로 체크포인트를 따로 관리한다.
        """
        return [""]

    def with_task(self, task: str) -> "Source":
        """작업 단위(예: HN 쿼리)를 바인딩한 인스턴스를 돌려준다. 기본은 자기 자신."""
        return self
