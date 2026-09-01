"""이력 DB 계층 — SQLAlchemy Core 기반 (엔진 비종속).

왜 SQLAlchemy인가:
    이전 비용을 결정하는 건 "어떤 DB를 쓰느냐"가 아니라 "코드가 특정 DB에 얼마나
    묶여 있느냐"다. 드라이버를 직접 부르고 방언 문법(INSERT OR IGNORE)을 쓰면
    배포 시점에 무엇을 고르든 코드를 고쳐야 한다. Core(ORM 아님)로 얇게 감싸두면
    엔진 교체가 DATABASE_URL 한 줄이 된다.

      개발      DATABASE_URL=sqlite:///data/radar.db
      배포      DATABASE_URL=postgresql+psycopg://user:pw@host/aitrend
      사내 DB   DATABASE_URL=oracle+oracledb://...  /  mysql+pymysql://...

실행:
  python -m src.db --init      # 스키마 생성
  python -m src.db --summary   # 누적 현황
  python -m src.db --url       # 현재 접속 대상 확인
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml
from dotenv import load_dotenv
from sqlalchemy import (
    JSON, BigInteger, Boolean, Column, DateTime, Float, Index, Integer,
    MetaData, String, Table, Text, UniqueConstraint, create_engine, func, select,
)
from sqlalchemy.engine import Connection, Engine

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

metadata = MetaData()

# SQLite는 INTEGER PRIMARY KEY일 때만 rowid 별칭이 붙어 자동증가한다 — BIGINT면
# 자동증가가 안 돼 INSERT가 NOT NULL로 터진다. 반면 Postgres/Oracle에서는 BIGINT가 맞다.
# 방언별 variant로 양쪽을 동시에 만족시킨다.
PK_INT = BigInteger().with_variant(Integer, "sqlite")

# ── items: 수집 항목 하나 = 행 하나 ──────────────────────────────────
# 처리 단계가 컬럼을 채워 나간다 (수집 → score → filter → insight).
items = Table(
    "items", metadata,
    # 정수 대리키. (source, source_id) 문자열을 PK로 쓰면 조인 테이블이 무거워지고
    # Postgres/Oracle에서도 정수 PK가 유리하다. 자연키는 UNIQUE 제약으로 지킨다.
    Column("id", PK_INT, primary_key=True, autoincrement=True),
    Column("source", String(32), nullable=False),
    Column("source_id", String(128), nullable=False),

    Column("title", Text),
    Column("summary", Text),          # 요약만. 본문은 저장하지 않는다
    Column("url", Text),

    # ★ 발행일과 수집일은 반드시 분리한다.
    #   백필 데이터는 collected_at=오늘, published_at=몇 년 전이다.
    #   TEXT가 아니라 실제 시간 타입으로 둬야 범위 비교·정렬이 타임존과 무관하게 정확하다.
    Column("published_at", DateTime(timezone=True)),
    Column("collected_at", DateTime(timezone=True), nullable=False),
    # 주간 다이제스트·트렌드 집계가 전부 ISO 주 단위라, 매 쿼리마다 날짜에서
    # 주차를 계산하는 대신 적재 시점에 한 번 구해 저장한다(SQLite엔 ISO 주 함수가 없다).
    Column("published_week", String(8)),          # 'YYYY-Www'

    Column("meta", JSON),             # Postgres에선 JSONB로 매핑된다

    # ── 처리 단계 산출 (수집 시점엔 NULL) ──
    Column("cross_score", Float),     # 교차 점수 — 다이제스트 정렬 기준
    Column("relevance", Float),       # 시드 centroid 코사인 유사도
    Column("kept", Boolean),          # 필터 통과 여부
    Column("insight", Text),          # L1 AI 해설
    Column("insight_model", String(64)),          # 어느 모델이 썼는지(재생성 판단용)
    Column("insight_at", DateTime(timezone=True)),

    UniqueConstraint("source", "source_id", name="uq_items_source"),
    # 다이제스트의 핵심 쿼리가 "WHERE kept ORDER BY cross_score DESC"라
    # 단일 인덱스 두 개보다 복합 인덱스가 훨씬 빠르다.
    Index("ix_items_kept_score", "kept", "cross_score"),
    Index("ix_items_published", "published_at"),
    Index("ix_items_week", "published_week"),
    Index("ix_items_source", "source"),
)

# ── item_axes: 항목 × 축 (다대다) ────────────────────────────────────
# 'ai,bigdata' 같은 쉼표 문자열로 두면 "빅데이터 축만 보기"가 LIKE '%...%'가 되어
# 인덱스를 못 타고 부분문자열 충돌도 난다. 축은 config에서 늘어날 수 있으므로
# 불린 컬럼 대신 조인 테이블로 정규화한다.
item_axes = Table(
    "item_axes", metadata,
    Column("item_id", PK_INT, nullable=False),
    Column("axis", String(32), nullable=False),
    UniqueConstraint("item_id", "axis", name="uq_item_axis"),
    Index("ix_axes_axis", "axis", "item_id"),
)

# ── digests: 주간 다이제스트 (웹·메일이 같은 행을 읽는다) ─────────────
digests = Table(
    "digests", metadata,
    Column("week", String(8), primary_key=True),   # 'YYYY-Www'
    Column("generated_at", DateTime(timezone=True), nullable=False),
    Column("lead", Text),             # L2 '이번 주 흐름'
    Column("body", JSON),             # 섹션별 item id 목록
)


# ── 접속 ────────────────────────────────────────────────────────────
def load_config(path: str = "config.yaml") -> dict[str, Any]:
    """설정을 읽는다.

    ★ 현재 디렉터리에만 의존하지 않는다.
      상대 경로로 열면 **어디서 실행하느냐**에 따라 되거나 안 된다. 배포에서
      시작 명령의 작업 디렉터리가 다르면 임포트 단계에서 FileNotFoundError로
      죽는데, 로그만 보고는 원인이 경로라는 걸 알기 어렵다.
      먼저 현재 위치에서 찾고, 없으면 저장소 뿌리(이 파일의 상위)에서 찾는다.
    """
    p = Path(path)
    if not p.exists():
        alt = Path(__file__).resolve().parents[1] / path
        if alt.exists():
            p = alt
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def database_url(cfg: dict[str, Any] | None = None) -> str:
    """DATABASE_URL 우선, 없으면 config의 기본값.

    배포 시 코드를 건드리지 않고 환경변수만 바꿔 엔진을 교체하기 위한 진입점이다.
    """
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    cfg = cfg or load_config()
    return cfg.get("db", {}).get("url", "sqlite:///data/radar.db")


_engine: Engine | None = None


def get_engine(url: str | None = None) -> Engine:
    global _engine
    if _engine is None or url is not None:
        url = url or database_url()
        if url.startswith("sqlite"):
            # 파일 경로 보장 + 동시 읽기 개선(WAL). 백필이 쓰는 동안 웹이 읽을 수 있다.
            path = url.split("///")[-1]
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(url, future=True)
        if url.startswith("sqlite"):
            with _engine.begin() as conn:
                conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    return _engine


# ── 파생 인덱스 DB ───────────────────────────────────────────────────
# item_keywords는 **전체 코퍼스**에서 뽑은 키워드 인덱스라 180만 행이다.
# radar.db에 넣으면 46MB → 270MB가 되는데, radar.db는 git으로 관리되므로
# 커밋할 때마다 그 크기가 통째로 이력에 쌓인다.
#
# 이건 임베딩 캐시와 같은 성격이다 — **원본이 아니라 파생물**이고,
# python -m src.extract 로 80초면 다시 만든다. 그래서 별도 파일에 두고 gitignore 한다.
# 배포 시 한 DB에 몰고 싶으면 config의 db.keywords_url을 db.url과 같게 두면 된다.
_kw_engine: Engine | None = None


def kw_engine(url: str | None = None) -> Engine:
    """키워드 인덱스용 엔진. 기본은 data/keywords.db (gitignore 대상)."""
    global _kw_engine
    if _kw_engine is None or url is not None:
        if url is None:
            url = os.getenv("KEYWORDS_URL") or load_config().get("db", {}).get(
                "keywords_url", "sqlite:///data/keywords.db")
        if url.startswith("sqlite"):
            path = url.split("///")[-1]
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        _kw_engine = create_engine(url, future=True)
        if url.startswith("sqlite"):
            with _kw_engine.begin() as conn:
                conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    return _kw_engine


def init_db(engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    metadata.create_all(engine)


# ── 유틸 ────────────────────────────────────────────────────────────
def parse_dt(value: Any) -> datetime | None:
    """소스마다 제각각인 날짜 표기를 timezone-aware UTC로 정규화한다.

    HN='2023-01-01T23:59:16.000Z', GeekNews='2026-07-01T14:46:25+09:00',
    sobiz='2026-08-25T09:00:00+09:00' 등이 섞여 들어온다.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None
            else dt).astimezone(timezone.utc)


def iso_week(dt: datetime | None) -> str | None:
    """ISO 주차 문자열 'YYYY-Www'. 주간 집계의 그룹 키."""
    if dt is None:
        return None
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def _insert_ignore(conn: Connection, table: Table, rows: Sequence[dict[str, Any]],
                   conflict_cols: list[str]) -> int:
    """중복은 무시하고 삽입한다 (방언별 처리).

    처리 단계가 채운 컬럼(cross_score/insight 등)을 재수집이 덮어쓰면 안 되므로
    UPDATE가 아니라 DO NOTHING이다.
    """
    if not rows:
        return 0
    name = conn.engine.dialect.name

    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as _ins
        stmt = _ins(table).on_conflict_do_nothing(index_elements=conflict_cols)
        # ★ psycopg의 executemany는 rowcount를 -1로 준다. 그대로 쓰면 수집 로그가
        #   "신규 -1건"이 된다 — sqlite에서는 맞던 코드가 Postgres로 옮기며 깨졌다.
        #   실제로 삽입된 행만 RETURNING으로 돌려받아 센다. 충돌로 건너뛴 행은
        #   DO NOTHING이라 아무것도 반환하지 않으므로 개수가 곧 신규 건수다.
        res = conn.execute(stmt.returning(table.c[conflict_cols[0]]), list(rows))
        return len(res.all())

    if name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as _ins
        stmt = _ins(table).on_conflict_do_nothing(index_elements=conflict_cols)
    elif name == "mysql":
        stmt = table.insert().prefix_with("IGNORE")
    else:
        # Oracle/Tibero 등: 표준 문법에 없는 upsert라 행 단위로 우회한다(느리지만 동작)
        from sqlalchemy.exc import IntegrityError
        n = 0
        for row in rows:
            try:
                with conn.begin_nested():
                    conn.execute(table.insert(), row)
                n += 1
            except IntegrityError:
                pass
        return n

    result = conn.execute(stmt, list(rows))
    return result.rowcount or 0


def upsert_items(records: Iterable[dict[str, Any]], engine: Engine | None = None) -> int:
    """수집 항목을 누적한다. 이미 있는 (source, source_id)는 기존 행을 보존한다."""
    engine = engine or get_engine()
    rows: list[dict[str, Any]] = []
    for r in records:
        pub = parse_dt(r.get("published_at"))
        rows.append({
            "source": r["source"],
            "source_id": str(r["source_id"]),
            "title": r.get("title"),
            "summary": r.get("summary"),
            "url": r.get("url"),
            "published_at": pub,
            "published_week": iso_week(pub),
            "collected_at": parse_dt(r.get("collected_at")) or datetime.now(timezone.utc),
            "meta": r.get("meta") or {},
        })
    if not rows:
        return 0
    with engine.begin() as conn:
        return _insert_ignore(conn, items, rows, ["source", "source_id"])


def set_axes(item_id: int, axes: Iterable[str], engine: Engine | None = None) -> None:
    """항목의 축 태그를 교체한다."""
    engine = engine or get_engine()
    with engine.begin() as conn:
        conn.execute(item_axes.delete().where(item_axes.c.item_id == item_id))
        rows = [{"item_id": item_id, "axis": a} for a in axes]
        if rows:
            conn.execute(item_axes.insert(), rows)


def replace_axes_bulk(tagged: dict[int, Iterable[str]], engine: Engine | None = None) -> int:
    """여러 항목의 축 태그를 한 트랜잭션에서 교체한다. prefilter.py가 쓴다.

    set_axes를 항목마다 부르면 5만 건에 트랜잭션이 5만 개 열린다(SQLite에서 수 분).
    여기서는 delete를 IN 절로 묶고 insert를 executemany로 한 번에 보낸다.

    0축 항목도 키를 넘기면 기존 태그가 지워진다 — 키워드를 고쳐 다시 돌렸을 때
    예전 태그가 남아 있으면 안 되기 때문이다.
    """
    if not tagged:
        return 0
    ids = list(tagged)
    rows = [{"item_id": i, "axis": a} for i, axes in tagged.items() for a in axes]
    with engine_or(engine).begin() as conn:
        # 파라미터 상한(SQLite 기본 999)에 걸리지 않게 나눠서 지운다
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            conn.execute(item_axes.delete().where(item_axes.c.item_id.in_(chunk)))
        if rows:
            conn.execute(item_axes.insert(), rows)
    return len(rows)


def engine_or(engine: Engine | None) -> Engine:
    return engine or get_engine()


# ── 현황 ────────────────────────────────────────────────────────────
def summary(engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    with engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(items)).scalar_one()
        print(f"■ 누적 현황  ({engine.url.render_as_string(hide_password=True)})")
        print(f"  전체 항목  {total:>8,} 건\n")
        if not total:
            return

        rows = conn.execute(
            select(items.c.source, func.count().label("n"),
                   func.min(items.c.published_at), func.max(items.c.published_at))
            .group_by(items.c.source).order_by(func.count().desc())
        ).all()
        print(f"  {'소스':<14}{'건수':>9}   발행일 범위")
        for src, n, lo, hi in rows:
            lo_s = str(lo)[:10] if lo else "-"
            hi_s = str(hi)[:10] if hi else "-"
            print(f"  {src:<14}{n:>9,}   {lo_s} ~ {hi_s}")

        axis_rows = conn.execute(
            select(item_axes.c.axis, func.count()).group_by(item_axes.c.axis)
            .order_by(func.count().desc())
        ).all()
        if axis_rows:
            print("\n  축별:  " + "  ".join(f"{a} {n:,}" for a, n in axis_rows))

        def cnt(where) -> int:
            return conn.execute(select(func.count()).select_from(items).where(where)).scalar_one()

        print(f"\n  교차점수 계산 {cnt(items.c.cross_score.isnot(None)):>8,} 건")
        print(f"  필터 통과    {cnt(items.c.kept.is_(True)):>8,} 건")
        print(f"  AI 해설      {cnt(items.c.insight.isnot(None)):>8,} 건")
        print(f"  발행일 없음  {cnt(items.c.published_at.is_(None)):>8,} 건")


def main() -> None:
    parser = argparse.ArgumentParser(description="이력 DB 관리")
    parser.add_argument("--init", action="store_true", help="스키마 생성")
    parser.add_argument("--summary", action="store_true", help="누적 현황")
    parser.add_argument("--url", action="store_true", help="접속 대상 확인")
    args = parser.parse_args()

    engine = get_engine()
    if args.url:
        print(engine.url.render_as_string(hide_password=True))
        return

    init_db(engine)   # 항상 스키마 보장(멱등)
    if args.init and not args.summary:
        print(f"스키마 생성 완료 → {engine.url.render_as_string(hide_password=True)}")
    else:
        summary(engine)


if __name__ == "__main__":
    main()
