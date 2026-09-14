"""SPA가 쓰는 JSON API.

화면(web/static/app.js)과 데이터를 분리한다. 서버는 JSON만 내고 렌더는 브라우저가 한다 —
sobiz web/ 패턴과 같다.

★ 계산 로직을 여기서 다시 짜지 않는다
    다이제스트 구성은 src/digest.build(), 급상승은 src/trend.rising(),
    브릿지는 src/graph.build()가 이미 정한다. 여기서 또 고르면 메일·CSV와
    화면이 갈라진다(파이프라인 이중화 금지 — CLAUDE.md).
    이 파일이 하는 일은 그 결과를 JSON으로 옮기는 것뿐이다.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Query
from sqlalchemy import and_, func, or_, select

from src.db import (digests, get_engine, item_axes, items, kw_item, kw_meta,
                    kw_neighbor, kw_week, load_config)
from src.digest import humanize_weeks, week_label
from web.cache import ego_cache
from web.response_cache import cached

router = APIRouter(prefix="/api")
CFG = load_config()
LABELS = {a: s.get("label", a) for a, s in CFG["axes"].items()}


def _kst_date(value: datetime | None) -> str:
    if value is None:
        return ''
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone(timedelta(hours=9))).date().isoformat()


def _parse_date(s: str) -> datetime | None:
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").replace(tzinfo=timezone(timedelta(hours=9))).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def search_conds(q: str, axis: str, since: str, until: str, kept_only: int):
    """검색 조건. 화면·CSV·API가 공유해야 결과가 갈라지지 않는다."""
    conds = []
    if q:
        conds.append(or_(items.c.title.contains(q, autoescape=True), items.c.summary.contains(q, autoescape=True)))
    if kept_only:
        conds.append(items.c.kept.is_(True))
    if since and (d := _parse_date(since)):
        conds.append(items.c.published_at >= d)
    if until and (d := _parse_date(until)):
        conds.append(items.c.published_at < d + timedelta(days=1))
    if axis in CFG["axes"]:
        conds.append(items.c.id.in_(
            select(item_axes.c.item_id).where(item_axes.c.axis == axis)))
    return and_(*conds) if conds else None


def _axes_of(conn, ids: list[int]) -> dict[int, list[str]]:
    if not ids:
        return {}
    out: dict[int, list[str]] = {}
    for i, a in conn.execute(select(item_axes.c.item_id, item_axes.c.axis)
                             .where(item_axes.c.item_id.in_(ids))):
        out.setdefault(i, []).append(a)
    return out


@router.get("/meta")
def meta() -> dict[str, Any]:
    """축 라벨·색 등 화면이 시작할 때 한 번 받는 정보."""
    return {"axes": [{"key": k, "label": v} for k, v in LABELS.items()]}


@router.get("/stats")
def stats() -> dict[str, Any]:
    with get_engine().connect() as c:
        n = lambda w=None: c.execute(
            select(func.count()).select_from(items) if w is None
            else select(func.count()).select_from(items).where(w)).scalar_one()
        total, kept = n(), n(items.c.kept.is_(True))
        insight = n(and_(items.c.insight.isnot(None), items.c.insight != ""))
        week = c.execute(select(func.max(items.c.published_week))
                         .where(items.c.kept.is_(True))).scalar_one_or_none()
        this_week = n(and_(items.c.kept.is_(True),
                           items.c.published_week == week)) if week else 0
        # 3축 전부 걸린 항목
        three = c.execute(
            select(func.count()).select_from(
                select(item_axes.c.item_id).group_by(item_axes.c.item_id)
                .having(func.count() >= 3).subquery())).scalar_one()
        lo, hi = c.execute(select(func.min(items.c.published_at),
                                  func.max(items.c.published_at))).one()
        by_source = [{"source": s, "n": k} for s, k in c.execute(
            select(items.c.source, func.count()).group_by(items.c.source)
            .order_by(func.count().desc()))]
    return {"total": total, "kept": kept, "crossing3": three, "insight": insight,
            "week": week, "this_week": this_week, "by_source": by_source,
            "range": [str(lo)[:10] if lo else None, str(hi)[:10] if hi else None]}


@router.get("/weeks")
def weeks(limit: int = Query(80)) -> dict[str, Any]:
    with get_engine().connect() as c:
        rows = c.execute(
            select(items.c.published_week, func.count().label("n"))
            .where(items.c.kept.is_(True), items.c.published_week.isnot(None))
            .group_by(items.c.published_week)
            .order_by(items.c.published_week.desc()).limit(limit)).all()
        saved = {w for (w,) in c.execute(select(digests.c.week))}
    return {"weeks": [{"week": w, "label": week_label(w), "n": n, "saved": w in saved}
                      for w, n in rows]}


@router.get("/digest")
def digest(week: str = Query("")) -> dict[str, Any]:
    """다이제스트 구성. src.digest.build()가 정한 것을 그대로 넘긴다."""
    from src.digest import build, latest_week
    if not week:
        with get_engine().connect() as c:
            week = latest_week(c) or ""
    if not week:
        return {"week": None, "empty": True}
    d = build(week, CFG)
    # 이모지를 쓰지 않는다. 이번 주 탭은 본문 제목도 아이콘도 없는 규칙으로
    # 통일했는데(사용자 요청), 여기만 🔥·⚠️가 남아 한 화면에 두 규칙이 섞였다.
    sections = [{"key": "crossing", "label": "교집합", "items": d["crossing"]}]
    if d["regulatory"]:
        sections.append({"key": "regulatory", "label": "규제 알림",
                         "items": d["regulatory"]})
    for ax, ps in d["by_axis"].items():
        sections.append({"key": ax, "label": LABELS.get(ax, ax), "items": ps})
    with get_engine().connect() as c:
        body = c.execute(select(digests.c.body)
                         .where(digests.c.week == week)).scalar_one_or_none()
    tasks = (body or {}).get("tasks", []) if isinstance(body, dict) else []
    from src.reviews import read_editorial
    editorial = read_editorial(week)
    if editorial:
        tasks = editorial.get("tasks", [])
    return {
        "week": week, "week_label": week_label(week),
        "lead": editorial["summary"] if editorial else d.get("lead"),
        "tasks": tasks, "total_kept": d["total_kept"],
        "sections": sections, "trending": d.get("trending", []),
        "trend_groups": _trend_groups(week, 5),
        "empty": not d["crossing"] and not any(d["by_axis"].values()),
    }


@router.get("/search")
@cached(seconds=30)
def search(q: str = Query(""), axis: str = Query(""), since: str = Query(""),
           until: str = Query(""), kept_only: int = Query(1),
           page: int = Query(1), size: int = Query(50),
           order: Literal['relevance', 'oldest', 'newest'] = 'relevance') -> dict[str, Any]:
    where = search_conds(q, axis, since, until, kept_only)
    size = max(1, min(size, 200))
    stmt = select(items.c.id, items.c.title, items.c.summary, items.c.url,
                  items.c.source, items.c.published_at, items.c.cross_score,
                  items.c.relevance, items.c.insight)
    cnt = select(func.count()).select_from(items)
    if where is not None:
        stmt, cnt = stmt.where(where), cnt.where(where)
    off = max(0, (page - 1) * size)
    ordering = {
        'oldest': (items.c.published_at.asc().nullslast(), items.c.id.asc()),
        'newest': (items.c.published_at.desc().nullslast(), items.c.id.desc()),
        'relevance': (items.c.cross_score.desc(),
                      items.c.published_at.desc().nullslast(), items.c.id.desc()),
    }
    stmt = stmt.order_by(*ordering[order]).limit(size).offset(off)
    with get_engine().connect() as c:
        rows = c.execute(stmt).all()
        total = c.execute(cnt).scalar_one()
        ax = _axes_of(c, [r.id for r in rows])
    return {
        "total": total, "page": page, "size": size,
        "items": [{
            "id": r.id, "title": r.title or "", "summary": (r.summary or "")[:300],
            "url": r.url or "", "source": r.source,
            "published": _kst_date(r.published_at),
            "cross_score": r.cross_score, "relevance": r.relevance,
            "insight": r.insight or None, "axes": sorted(ax.get(r.id, [])),
        } for r in rows],
    }


# 급상승을 보여주는 자리가 셋이다(홈 카드 · 이번 주 사이드 · 분석 탭).
# 축 구성이 자리마다 다르면 같은 주를 보는데 목록이 달라 보인다. 여기서 한 번 정한다.
TREND_GROUPS = (("ai,bigdata", "AI·빅데이터"), ("smallbiz", "소상공인"))


def _trend_groups(week: str, per: int = 5) -> list[dict[str, Any]]:
    """축 묶음별 급상승. 후보 목록은 캐시를 타므로 묶음이 늘어도 왕복이 안 는다."""
    out = []
    for axis, label in TREND_GROUPS:
        try:
            rows = trend(week=week, top=per, axis=axis).get("rows", [])
        except Exception:
            rows = []
        out.append({"axis": axis, "label": label, "rows": rows})
    return out

@lru_cache(maxsize=1)
def _axis_terms() -> dict[str, tuple[str, ...]]:
    """축마다의 어휘. config의 axes[*].keywords를 쉼표로 풀어 소문자·무공백으로 만든다.

    ★ config에는 한 줄에 여러 개가 쉼표로 묶여 있다('AI, 인공지능, LLM, 생성형').
      줄 단위로 그냥 쓰면 매칭이 통째로 실패한다(실측: 후보 60개 중 0개 통과).
    """
    out: dict[str, tuple[str, ...]] = {}
    for ax, spec in (CFG.get("axes") or {}).items():
        terms: list[str] = []
        for group in (spec.get("keywords") or []):
            terms += [t.strip().lower().replace(" ", "")
                      for t in str(group).split(",") if t.strip()]
        out[ax] = tuple(terms)
    return out


def _on_axis(keyword: str, axes: list[str]) -> bool:
    """키워드 **자체**가 그 축의 어휘를 품고 있나.

    ★ 기사에 붙은 축만 보면 안 된다. 지자체 예산 기사가 예산 항목으로
      '인공지능(AI)'을 한 번 언급하면 그 기사는 정당하게 ai축이 되고, 거기 같이
      나온 '예산정책'·'거점국립대'·'메리츠증권'이 AI 급상승 상위를 차지한다
      (실측 지적: "AI와 무슨 관련이 있는지 모르겠다").
      축 태깅이 틀린 게 아니라 **일반 명사가 딸려 오는 것**이라, 축 점유율로는
      못 거른다 — 예산정책은 ai 점유가 95%였다.

    그래서 키워드 문자열 자체를 본다. 'AI서버'·'공공마이데이터'는 남고
    '예산정책'·'부산대'는 빠진다. 후보 1,847개 중 222개가 남아 지면은 충분하다.

    ⚠ 축 어휘에 없는 인접어(IoT·클라우드·반도체)도 같이 빠진다. 넣고 싶으면
      config의 axes[*].keywords에 추가하면 된다 — 판정의 출처가 거기 한 곳이다.
    """
    k = (keyword or "").lower().replace(" ", "")
    terms = _axis_terms()
    return any(t in k for ax in axes for t in terms.get(ax, ()))


@router.get("/trend")
def trend(week: str = Query(""), top: int = Query(20),
          axis: str = Query("")) -> dict[str, Any]:
    """급상승 키워드. axis를 주면 그 축의 키워드만 낸다.

    ★ 왜 축을 나눌 수 있어야 하나 (실측 2026-09-02)
      섞어서 뽑으면 AI가 지면을 독차지한다. 9월 1주차 상위 25개의 축 분포가
      **ai 24 / smallbiz 1 / bigdata 0**이었다. 코퍼스 자체는 smallbiz 56%인데도
      그렇다 — 소상공인 어휘(대출·지원·상권)는 주마다 안정적이라 '급상승'하지
      않고, AI 쪽은 신제품·행사로 매주 새 말이 튀기 때문이다.

      그래서 팀에 정작 중요한 신호가 통째로 묻혔다. 축을 나눠 보면 이런 게 나온다:
          빅데이터  공공마이데이터 22건 · 통계데이터 · 건강돌봄 · 돌봄플랫폼
          소상공인  모태펀드출자 · 특별채무조정 · 상생배달 · 스마트농업
      섞은 목록에는 이 중 하나도 없었다.

    ★ 축은 kw_meta에 이미 있는 값을 쓴다 — 그 키워드가 가장 많이 나온 축이다.
      "그 축 기사들 안에서의 급상승"을 제대로 재려면 kw_week를 축별로 쪼개야
      하는데, 46만 행이 세 배가 된다. DB가 417MB/500MB라 그럴 여유가 없고,
      실측해 보니 근사로도 위 목록이 그대로 나온다.

    ★ 자르기 전에 거른다. top으로 먼저 자르고 축을 거르면 빅데이터 탭이
      빈 채로 나온다 — 상위 25개에 빅데이터가 0개였다.
    """
    from src.trend import prev_weeks, rising
    tcfg = CFG.get("trend", {})
    if not week:
        with get_engine().connect() as c:
            week = c.execute(select(func.max(items.c.published_week))
                             .where(items.c.kept.is_(True))).scalar_one_or_none() or ""
    if not week:
        return {"week": None, "rows": []}
    back = int(tcfg.get("compare_weeks", 4))
    # ★ 후보 전체를 캐시한다. rising()이 배포본에서 한 번에 11초 걸린다 —
    #   칩을 눌러도 화면이 11초 동안 그대로라 "탭 내용이 똑같다"로 읽혔다(실측 지적).
    #   축 거르기와 자르기는 캐시된 목록 위에서 하므로 칩 전환이 즉시 끝난다.
    freq = int(tcfg.get("min_weekly_freq", 5))
    rows = ego_cache.get_or_call(("rising", week, back, freq),
                                 lambda: rising(week, back, freq))
    # 쉼표로 여러 축을 받는다 — 'ai,bigdata'처럼. 팀 이름이 AI·빅데이터팀이라
    # 그 둘은 한 묶음으로 보는 게 실제 업무 단위와 맞는다.
    want = [a for a in (axis or "").split(",") if a.strip()]
    if want:
        rows = [r for r in rows if _on_axis(r["keyword"], want)]
    # ★ 자른 뒤에 거르면 안 된다. 상위 25개에 빅데이터가 0개라 탭이 빈 채로 나온다.
    #   캐시된 목록을 그대로 넘기면 아래에서 series를 붙이며 캐시를 오염시킨다.
    rows = [dict(r) for r in rows[:top]]
    axis_weeks = list(reversed(prev_weeks(week, back))) + [week]
    # ★ 시계열은 **한 번에** 읽는다. 키워드마다 series()를 부르면 원격 DB에
    #   여덟 번 왕복해서, 캐시가 걸린 뒤에도 배포본이 5초씩 걸렸다(실측).
    #   같은 표에서 IN 하나로 가져오면 왕복이 한 번이다.
    heads = [r["keyword"] for r in rows[:8]]
    if heads:
        with get_engine().connect() as c:
            grid: dict[tuple[str, str], int] = {
                (k, w): int(n) for k, w, n in c.execute(
                    select(kw_week.c.keyword, kw_week.c.week, kw_week.c.n)
                    .where(kw_week.c.keyword.in_(heads),
                           kw_week.c.week.in_(axis_weeks)))}
        for r in rows[:8]:
            # 차트 축에 그대로 쓰이므로 사람이 읽는 표기를 함께 싣는다.
            # 'W31'은 몇 월인지 알 수 없다.
            r["series"] = [{"week": w, "label": week_label(w),
                            "n": grid.get((r["keyword"], w), 0)} for w in axis_weeks]
    return {"week": week, "week_label": week_label(week), "axis": axis,
            # 화면이 "직전 N주 평균과 비교"라고 정확히 쓸 수 있게 같이 낸다
            "compare_weeks": back,
            "weeks": [{"week": w, "label": week_label(w)} for w in axis_weeks],
            "rows": rows}


@router.get("/series")
def keyword_series(kw: str = Query(...), weeks: int = Query(8)) -> dict[str, Any]:
    """키워드 하나의 주차별 언급 추이. 급상승 팝업의 꺾은선이 쓴다.

    /api/trend는 상위 8개에만 series를 붙인다. 팝업은 아무 키워드나 열 수 있으므로
    따로 뽑는다. 주차 표기는 사람이 읽는 형태로 함께 낸다.
    """
    from src.trend import prev_weeks, series
    with get_engine().connect() as c:
        cur = c.execute(select(func.max(items.c.published_week))
                        .where(items.c.kept.is_(True))).scalar_one_or_none()
    if not cur:
        return {"keyword": kw, "series": []}
    axis = list(reversed(prev_weeks(cur, max(1, weeks - 1)))) + [cur]
    return {"keyword": kw, "series": [
        {"week": w, "label": week_label(w), "short": week_label(w).split("년 ")[-1], "n": n}
        for w, n in series(kw, axis)]}


# ── /graph(브릿지 노드)는 걷어냈다 ────────────────────────────────
# 브릿지 점수 = 다른 축으로 향하는 간선 비중. 축을 잇는 키워드가 곧 과제 후보라는
# 가설이었는데, 실제로 뜬 건 '유치·메뉴·파일·최저·언어' 같은 일반 명사였다.
# 흔한 말일수록 여러 축의 기사에 골고루 나오므로 **브릿지 점수가 사실상
# 일반성 점수로 작동한다** — 구조적인 문제라 임계값을 만져서 될 일이 아니다.
#
# 같은 질문("여러 축을 걸치는 게 뭔가")에 /cross 화면이 기사 수준에서 직접
# 답한다. 키워드 수준의 간접 지표를 함께 둘 이유가 없다.
# 계산 자체는 src/graph.py에 CLI로 남아 있다(python -m src.graph).

@router.get("/suggest")
def suggest(q: str = Query(""), limit: int = Query(12)) -> dict[str, Any]:
    """키워드 자동완성. 검색형 네트워크의 입구다.

    빈도순으로 그냥 내면 '상공인', '전통' 같은 n-gram 조각이 상위를 먹는다.
    사용자가 처음 보는 목록이라 여기가 지저분하면 기능 전체가 못 미더워 보인다.
    → 더 긴 키워드에 포함되면서 빈도가 비슷한 것은 조각으로 보고 뺀다.
    """
    from src.extract import STOPWORDS
    with get_engine().connect() as c:     # 집계 표에서 읽는다(kw_meta)
        stmt = (select(kw_meta.c.keyword, kw_meta.c.df)
                .order_by(kw_meta.c.df.desc()).limit(limit * 8))
        if q:
            stmt = stmt.where(kw_meta.c.keyword.contains(q))
        rows = [(k, n) for k, n in c.execute(stmt) if k not in STOPWORDS]

    out: list[dict[str, Any]] = []
    for k, n in rows:
        if any(k != o and k in o and m >= n * 0.5 for o, m in rows):
            continue                                   # 더 긴 말의 조각
        if any(o in k and n >= m * 0.5 for o, m in
               ((x["keyword"], x["n"]) for x in out)):
            continue                                   # 이미 뽑은 것의 확장형
        out.append({"keyword": k, "n": n})
        if len(out) >= limit:
            break
    return {"items": out}


# 입력·색인 표기 양쪽에 같은 공백 규칙을 적용한다(일반/복사된 특수 공백 포함).
_GRAPH_SPACES = ' \t\r\n\v\f\u00a0\u3000'
_GRAPH_SPACE_MAP = str.maketrans('', '', _GRAPH_SPACES)


def _graph_query_key(value: str) -> str:
    return (value or '').translate(_GRAPH_SPACE_MAP).lower()


@router.get("/ego")
def ego(kw: str = Query(...), hops: int = Query(1), per_hop: int = Query(12),
        min_cooc: int = Query(0), max_nodes: int = Query(46)) -> dict[str, Any]:
    """캐시를 거쳐 _ego를 부른다. 실제 계산은 아래 _ego에 있다.

    '소상공인'(15,390건)은 3.7초가 걸린다. 홉 슬라이더를 왕복하거나 노드를
    더블클릭하며 헤집는 게 이 화면의 용도인데, 그때마다 4초를 기다리면 못 쓴다.
    같은 질의가 반복되는 비율이 높아 캐시가 잘 듣는다.
    """
    normalized = _graph_query_key(kw)
    key = ('graph-normalized', normalized, hops, per_hop, min_cooc, max_nodes)
    return ego_cache.get_or_call(
        key, lambda: _ego(normalized, hops, per_hop, min_cooc, max_nodes))


def _ego(kw: str, hops: int, per_hop: int,
         min_cooc: int, max_nodes: int) -> dict[str, Any]:
    """키워드 하나를 중심으로 한 연관어 망.

    ★ 미리 계산된 이웃 표(kw_neighbor)를 읽는다.
      전에는 요청마다 공기(co-occurrence)를 실시간으로 셌다 — '소상공인' 2홉이면
      질의 165회다(실측). 원격 DB로 옮기면 왕복만 5~10초가 붙는다.
      이웃을 미리 만들어 두면 홉당 한 번으로 끝난다. 대신 이웃은 만든 시점에
      고정된다 — 매일 파이프라인이 다시 만드므로 하루 단위로는 최신이다.

    ★ 전체 코퍼스를 본다 (kept 필터를 걸지 않는다)
      필터는 다이제스트를 위한 것이다. 탐색은 반대다 — 'Ollama'·'LangChain'
      같은 말은 통과분 안에는 거의 없다. kw_neighbor는 전체 코퍼스로 만든다.
    """
    hops = max(1, min(hops, 3))
    per_hop = max(3, min(per_hop, 30))
    max_nodes = max(6, min(max_nodes, 120))
    q = _graph_query_key(kw)
    if not q:
        return {"center": kw, "empty": True, "nodes": [], "edges": [],
                "reason": "검색어가 없습니다."}

    with get_engine().connect() as c:
        # '모두의 AI'와 '모두의AI'도 같은 후보 집합에서 대표 표기를 고른다.
        # 통계는 합산하지 않는다. 같은 기사의 중복 집계를 피하기 위해 기존 망을 쓴다.
        normalized_keyword = func.lower(kw_meta.c.keyword)
        for space in _GRAPH_SPACES:
            normalized_keyword = func.replace(normalized_keyword, space, '')
        center = c.execute(
            select(kw_meta.c.keyword)
            .where(normalized_keyword == q)
            .order_by(kw_meta.c.df.desc(), kw_meta.c.keyword.asc()).limit(1)).scalar_one_or_none()
        if not center:
            return {"center": q, "empty": True, "nodes": [], "edges": [],
                    "reason": f"'{q}' — 자료가 적어 망을 그릴 수 없습니다 "
                              f"(문서 10건 이상인 키워드만 만듭니다)."}

        def neighbours(words: list[str]) -> dict[str, list]:
            """여러 키워드의 이웃을 **한 번에** 가져온다."""
            out: dict[str, list] = {w: [] for w in words}
            for k, nb, v, n in c.execute(
                    select(kw_neighbor.c.keyword, kw_neighbor.c.neighbor,
                           kw_neighbor.c.npmi, kw_neighbor.c.cooc)
                    .where(kw_neighbor.c.keyword.in_(words))
                    .order_by(kw_neighbor.c.keyword, kw_neighbor.c.npmi.desc())):
                if len(out[k]) < per_hop and (not min_cooc or n >= min_cooc):
                    out[k].append((v, nb, n))
            return out

        nodes: dict[str, dict[str, Any]] = {
            center: {"keyword": center, "hop": 0, "center": True,
                     "npmi": 1.0, "cooc": 0, "df": 0}}
        frontier = [center]
        for hop in range(1, hops + 1):
            room = max_nodes - len(nodes)
            if room <= 0:
                break
            budget = max(2, room // max(1, len(frontier)))
            nbrs = neighbours(frontier)
            nxt: list[str] = []
            for w in frontier:
                got = 0
                for v, k, n in nbrs.get(w, []):
                    if k in nodes or got >= budget or len(nodes) >= max_nodes:
                        continue
                    # 조각 판정은 홉을 넘어서도 한다. 홉 안에서만 걸면 '대전결제'와
                    # '대전결제데이터'가 따로 올라온다(실측).
                    if any(k in o or o in k for o in nodes
                           if len(o) > 2 and len(k) > 2):
                        continue
                    nodes[k] = {"keyword": k, "hop": hop, "center": False,
                                "npmi": v, "cooc": n, "df": 0, "via": w}
                    nxt.append(k)
                    got += 1
            if not nxt:
                break
            frontier = nxt

        names = list(nodes)
        # 노드끼리의 간선도 같은 표에서 한 번에 읽는다
        edges, seen = [], set()
        for a, b, v, n in c.execute(
                select(kw_neighbor.c.keyword, kw_neighbor.c.neighbor,
                       kw_neighbor.c.npmi, kw_neighbor.c.cooc)
                .where(kw_neighbor.c.keyword.in_(names),
                       kw_neighbor.c.neighbor.in_(names))):
            key = (a, b) if a < b else (b, a)
            if key not in seen:
                seen.add(key)
                edges.append({"source": a, "target": b, "npmi": v, "cooc": n})

        # 크기(df)와 색(axis)은 미리 계산해 둔 표에서 — 실시간으로 세면 키워드
        # 하나에 7초가 걸린다(실측)
        for k, d, ax in c.execute(
                select(kw_meta.c.keyword, kw_meta.c.df, kw_meta.c.axis)
                .where(kw_meta.c.keyword.in_(names))):
            if k in nodes:
                nodes[k]["df"] = d
                nodes[k]["axis"] = ax

    # ── 홉을 실제 간선 기준으로 다시 매긴다 ──
    #   확장은 예산(budget)에 잘리므로, 중심과 이어져 있는데도 2홉으로 밀리는
    #   노드가 생긴다. 간선이 곧 보이는 관계이니 홉도 그 위의 최단 거리여야 한다.
    adj: dict[str, set[str]] = {k: set() for k in names}
    for e in edges:
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])
    dist, queue = {center: 0}, [center]
    while queue:
        cur = queue.pop(0)
        for nb in adj[cur]:
            if nb not in dist:
                dist[nb] = dist[cur] + 1
                queue.append(nb)
    for k, nd in nodes.items():
        if not nd["center"]:
            nd["hop"] = dist.get(k, nd["hop"])
        nd.setdefault("axis", None)

    return {"center": center, "empty": False, "hops": hops,
            "nodes": list(nodes.values()), "edges": edges,
            "docs": nodes[center].get("df", 0)}


@router.get("/cross")
def cross(axes: int = Query(2), weeks: int = Query(8),
          limit: int = Query(60)) -> dict[str, Any]:
    """축이 겹치는 기사만 모은다.

    ★ 이 도구의 전제가 "팀의 업무는 세 축의 교집합에 있다"(CLAUDE.md)인데,
      정작 교집합은 다이제스트 다섯 칸에만 보였다. 그 주에 교차가 30건이어도
      5건만 나오고 나머지는 어디에서도 볼 수 없었다. 여기서 전부 본다.
    """
    from src.trend import prev_weeks

    with get_engine().connect() as c:
        cur = c.execute(select(func.max(items.c.published_week))
                        .where(items.c.kept.is_(True))).scalar_one_or_none()
        if not cur:
            return {"rows": [], "combos": [], "weeks": []}
        span = [cur, *prev_weeks(cur, max(0, weeks - 1))]

        # 축 조합을 파이썬에서 만든다 — GROUP BY로 문자열을 잇는 함수는 방언마다 다르다
        rows = c.execute(
            select(items.c.id, items.c.title, items.c.url, items.c.source,
                   items.c.published_at, items.c.published_week,
                   items.c.cross_score, items.c.insight)
            .where(items.c.kept.is_(True), items.c.published_week.in_(span))
            .order_by(items.c.cross_score.desc(),
                      items.c.published_at.desc())).all()
        amap: dict[int, list[str]] = {}
        ids = [r.id for r in rows]
        for part in [ids[i:i + 800] for i in range(0, len(ids), 800)]:
            for i, a in c.execute(select(item_axes.c.item_id, item_axes.c.axis)
                                  .where(item_axes.c.item_id.in_(part))):
                amap.setdefault(i, []).append(a)

    labels = {ax: s.get("label", ax) for ax, s in CFG["axes"].items()}
    out, combo = [], Counter()
    per_week: dict[str, Counter] = {}
    # 조합 분포는 축 개수와 무관하게 전체를 센다 — 옆 패널은 "전체에서 어떤
    # 조합이 얼마나 되나"를 보여주는 자리라, 지금 고른 축 수에 따라 바뀌면 안 된다.
    for r in rows:
        ax_all = sorted(amap.get(r.id, []))
        if len(ax_all) >= 2:
            combo["+".join(labels.get(a, a) for a in ax_all)] += 1
    for r in rows:
        ax = sorted(amap.get(r.id, []))
        # ★ '이상'이 아니라 '정확히 N축'이다. 2축에 3축을 포함하면 같은 기사가
        #   2축 목록과 3축 목록에 모두 나와, 무엇이 순수한 2축인지 알 수 없다.
        if len(ax) != axes:
            continue
        key = "+".join(labels.get(a, a) for a in ax)
        per_week.setdefault(r.published_week, Counter())[key] += 1
        if len(out) < limit:
            out.append({"id": r.id, "title": r.title or "", "url": r.url or "",
                        "source": r.source, "axes": ax, "combo": key,
                        "published": str(r.published_at)[:10] if r.published_at else "",
                        "week": r.published_week,
                        "cross_score": r.cross_score, "insight": r.insight or None})
    total = sum(sum(v.values()) for v in per_week.values())
    return {
        "rows": out, "total": total, "axes": axes,
        "combos": [{"combo": k, "n": v} for k, v in combo.most_common()],
        "weeks": [{"week": w, "label": week_label(w),
                   "n": sum(per_week.get(w, Counter()).values())}
                  for w in sorted(span)],
    }


@router.get("/orgs")
def orgs(weeks: int = Query(8), limit: int = Query(40)) -> dict[str, Any]:
    """반복 등장하는 기관.

    ★ 개체명 인식을 붙이지 않는다. 키워드 중 **기관 접미사로 끝나는 긴 말**만
      고른다. 소상공인시장진흥공단·행정안전부·한국관광공사가 그대로 잡힌다.
      ⚠ 접미사가 없는 기관(카카오·SKT)은 안 잡힌다.

    ★ 두 가지를 걸러야 쓸 수 있다 (실측)
      1) 일반명사: '센터'·'기관'을 넣으면 데이터센터·지원센터·공공기관이 상위를
         덮는다. 기관 고유 접미사만 남긴다.
      2) n-gram 조각: '중소벤처기업부'와 '벤처기업부'가 같이 잡힌다 —
         trend._fragment_of가 푸는 문제라 그대로 쓴다.
    """
    from src.extract import is_org_keyword
    from src.trend import _fragment_of, prev_weeks
    with get_engine().connect() as c:
        cur = c.execute(select(func.max(items.c.published_week))
                        .where(items.c.kept.is_(True))).scalar_one_or_none()
        if not cur:
            return {"rows": [], "brief": "", "weeks": []}
        span = [cur, *prev_weeks(cur, max(0, weeks - 1))]
        rows = c.execute(
            select(kw_week.c.keyword, kw_week.c.week, kw_week.c.n)
            .where(kw_week.c.week.in_(span))).all()

    agg: dict[str, Counter] = {}
    for kw, wk, n in rows:
        # 판정은 src.extract.is_org_keyword 한 곳에서 한다 — 인덱스 적재도 같은
        # 규칙을 쓴다. 갈라지면 표와 목록이 어긋난다.
        if not is_org_keyword(kw):
            continue
        agg.setdefault(kw, Counter())[wk] += n

    totals = {k: sum(v.values()) for k, v in agg.items()}
    out = []
    for kw, per in agg.items():
        tot = totals[kw]
        # 한 주에만 나온 건 '반복'이 아니다
        if tot < 3 or len(per) < 2 or _fragment_of(kw, totals):
            continue
        out.append({"keyword": kw, "total": tot, "weeks": len(per),
                    "series": [{"week": w, "label": week_label(w), "n": per.get(w, 0)}
                               for w in sorted(span)]})
    out.sort(key=lambda r: (-r["weeks"], -r["total"]))
    # 종합 글이 있으면 함께 낸다. 표는 '누가 몇 건'까지만 말하고 '무엇을 하고
    # 있나'는 못 말한다 — 40곳을 곳마다 읽게 하는 대신 한 편으로 묶어 둔 것이다.
    brief = ""
    with get_engine().connect() as c:
        body = c.execute(select(digests.c.body)
                         .where(digests.c.week == cur)).scalar_one_or_none()
    if isinstance(body, dict):
        brief = body.get("org_brief") or ""
    return {"rows": out[:limit], "brief": brief,
            "weeks": [{"week": w, "label": week_label(w)} for w in sorted(span)]}


@router.get("/org_items")
def org_items(kw: str = Query(""), week: str = Query(""),
              limit: int = Query(40)) -> dict[str, Any]:
    """기관 표의 주차 칸을 눌렀을 때 그 주 기사 (HTTP 경로). 조회는 _org_items."""
    return _org_items(kw, week, limit)


def _org_items(kw: str, week: str, limit: int = 40) -> dict[str, Any]:
    """기관 이름이 그 주에 나온 기사.

    ★ 본문 부분 문자열로 찾으면 안 된다 (한 번 그렇게 만들었다가 되돌렸다).
      추출기가 만든 키워드는 원문에 그 문자열이 없을 수도, 더 긴 이름의 일부일
      수도 있다. 기관 40곳 191칸을 전수 대조한 결과 21.5%가 어긋났다:
          이재명정부   표 34 / 본문검색  3   ← '이재명 정부'를 붙여 만든 n-gram
          신용보증재단 표  6 / 본문검색 18   ← '지역신용보증재단'이 딸려 걸린다
      표(kw_week)와 목록은 **같은 재료**에서 나와야 한다. 둘 다 item_keywords다.

    ★ 그래서 kw_item을 쓴다. 기관 키워드는 상한 없이 담는다(index_build 참고).
      kept로 거른다 — 표의 숫자가 통과분만 세기 때문이다(build_week_counts).
      전에 이걸 안 걸어 7건 대 78건으로 어긋났었다.
    """
    kw, week = (kw or "").strip(), (week or "").strip()
    if not kw or not week:
        return {"keyword": kw, "week": week, "label": "", "total": 0, "items": []}

    with get_engine().connect() as c:
        ids = [i for (i,) in c.execute(
            select(kw_item.c.item_id).where(kw_item.c.keyword == kw))]
        if not ids:
            return {"keyword": kw, "week": week, "label": week_label(week),
                    "total": 0, "items": []}
        rows = []
        for part in (ids[n:n + 400] for n in range(0, len(ids), 400)):
            rows += list(c.execute(
                select(items.c.id, items.c.title, items.c.url, items.c.source,
                       items.c.published_at, items.c.cross_score, items.c.insight)
                .where(items.c.id.in_(part),
                       items.c.published_week == week,
                       items.c.kept.is_(True))))
        total = len(rows)
        rows.sort(key=lambda r: (-(r.cross_score or 0), str(r.published_at or "")),
                  reverse=False)
        rows = rows[:max(1, min(limit, 200))]
        ax = _axes_of(c, [r.id for r in rows])
    return {
        "keyword": kw, "week": week, "label": week_label(week), "total": total,
        "items": [{"id": r.id, "title": r.title or "", "url": r.url or "",
                   "source": r.source, "kept": True,
                   "published": str(r.published_at)[:10] if r.published_at else "",
                   "cross_score": r.cross_score, "insight": r.insight or None,
                   "axes": sorted(ax.get(r.id, []))} for r in rows],
    }


@router.get("/regulatory")
def regulatory(limit: int = Query(60), days: int = Query(0),
               q: str = Query("")) -> dict[str, Any]:
    """규제 1차 출처에서 온 항목 (HTTP 경로). 실제 조회는 _regulatory에 있다."""
    return _regulatory(limit, days, q)


# "▸ 확인 필요: 이름 — 설명" — insight.py의 L1_REG_RULES가 team_profile과
# 명시적으로 연결될 때만, 어느 법령의 어느 부분이 그 업무의 무엇과 관련되는지
# 까지 붙이는 줄이다. AI 요약('· ' 줄들)과 화면에서 분리해서 보여주기 위해
# insight 텍스트에서 이 줄만 뽑아내고 나머지만 남긴다.
_CHECKLIST_RE = re.compile(r"^▸\s*확인\s*필요:\s*(.+)$\n?", re.MULTILINE)


def _split_checklist(insight: str | None) -> tuple[str | None, list[dict[str, str]]]:
    if not insight:
        return insight, []
    checklist = []
    for line in _CHECKLIST_RE.findall(insight):
        name, _, detail = line.strip().partition("—")
        checklist.append({"label": name.strip(), "detail": detail.strip()})
    clean = _CHECKLIST_RE.sub("", insight).rstrip() or None
    return clean, checklist


def _regulatory(limit: int, days: int = 0, q: str = "") -> dict[str, Any]:
    """규제 1차 출처에서 온 항목. 관련도 필터를 태우지 않는다.

    ★ 라우트 함수를 다른 라우트에서 직접 부르지 않는다.
      FastAPI가 값을 채워주는 건 HTTP 요청으로 들어올 때뿐이라, 파이썬 함수로
      부르면 안 넘긴 인자가 Query 객체 그대로 남는다. Query는 truthy라 조건문을
      통과해 버리고, 결국 `Query() - 1` 같은 데서 터진다(실측: /api/home 500).
      캐시 예열에서도 같은 함정에 걸렸다. 그래서 조회 로직은 순수 함수로 뺀다.

    판정은 **소스 기반**이다 — config에서 regulatory: true인 소스에서 왔으면
    제목과 무관하게 전부 규제로 본다. "규제가 확정되기 전에 안다"가 목적인데
    필터가 걸러버리면 알 방법이 없다.
    """
    srcs = [n for n, sc in (CFG.get("sources") or {}).items()
            if isinstance(sc, dict) and sc.get("regulatory")]
    if not srcs:
        return {"items": [], "total": 0, "sources": []}
    with get_engine().connect() as c:
        cond = [items.c.source.in_(srcs)]
        # 법령은 계속 쌓인다. 기간을 안 자르면 몇 달 뒤 오래된 것과 새 것이 섞인다.
        if days:
            cond.append(items.c.published_at
                        >= datetime.now(timezone.utc) - timedelta(days=days))
        if q:
            # 제목과 요약(제개정이유)을 함께 본다 — 제목에 안 나오는 말이 많다
            like = f"%{q}%"
            cond.append(or_(items.c.title.ilike(like), items.c.summary.ilike(like)))
        stmt = (select(items.c.id, items.c.title, items.c.summary, items.c.url,
                       items.c.source, items.c.published_at, items.c.meta,
                       items.c.insight)
                .where(*cond).order_by(items.c.published_at.desc()))
        rows = c.execute(stmt.limit(limit)).all()
        total = c.execute(select(func.count()).select_from(items)
                          .where(*cond)).scalar_one()
    # 목록의 개별 배지와 상단 집계 카드가 같은 파싱 결과를 공유한다
    # (_split_checklist를 두 번 정규식으로 다시 돌리지 않는다).
    checklist_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    out = []
    for r in rows:
        m = r.meta if isinstance(r.meta, dict) else {}
        clean_insight, item_checklist = _split_checklist(r.insight)
        for entry in item_checklist:
            checklist_map[entry["label"]].append(
                {"id": r.id, "title": r.title or "", "detail": entry["detail"]})
        out.append({"id": r.id, "title": r.title or "", "summary": r.summary or "",
                    "insight": clean_insight, "checklist": item_checklist,
                    "url": r.url or "", "source": r.source,
                    "published": str(r.published_at)[:10] if r.published_at else "",
                    "dept": m.get("부처", ""), "kind": m.get("종류", ""),
                    "revision": m.get("제개정", ""), "effective": m.get("시행일자", "")})
    checklist = [{"label": k, "count": len(v), "items": v}
                 for k, v in sorted(checklist_map.items(), key=lambda kv: -len(kv[1]))]
    return {"items": out, "total": total, "sources": srcs, "checklist": checklist}


@router.get("/monthly")
def monthly(month: str = Query("")) -> dict[str, Any]:
    """이전 월간 API도 직접 작성 리뷰·현재 월·날짜별 집계를 공유한다."""
    from src.reviews import available_periods, editorial_text, review_data
    from fastapi import HTTPException
    try:
        v = review_data(get_engine(), "monthly", month)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ed = v["editorial"]
    weeks = {datetime.fromisoformat(s["date"]).isocalendar()[:2] for s in v["series"]}
    return {"month": v["period"], "label": v["label"], "kept": v["kept"],
            "months": [{"month": p["period"], "label": p["label"]}
                       for p in available_periods(get_engine(), "monthly")],
            "lead": editorial_text(ed) if ed else None,
            "generated": ed.get("generated_at", "") if ed else "",
            "weeks": len(weeks), "status": v["status"], "as_of": ed.get("as_of", "") if ed else ""}


@router.get("/months")
def months() -> dict[str, Any]:
    """달 → 그 달의 주차. 사이드바 '지난 회차'가 2단으로 쓰는 목록.

    ★ 주차를 평면으로 늘어놓으면 못 쓴다.
      코퍼스에 백필분까지 들어와 주차가 365개다. 60개만 잘라 보여주고 있었는데,
      그러면 작년은 아예 닿을 수 없고 60개도 스크롤로 훑기엔 길다.
      달로 접으면 어느 규모에서도 목록이 12칸 안쪽이다.

    ★ 한 번에 다 준다. 달을 고를 때마다 왕복하면 선택창이 굼떠 보인다 —
      원격 DB라 왕복 하나가 곧 지연이다. 85개월 365주차를 합쳐도 한 응답이다.

    review는 그 달의 AI 리뷰가 실제로 있는지다. 없는 달을 골랐을 때 화면이
    비는 게 고장인지 아닌지, 화면이 스스로 말할 수 있어야 한다.
    """
    from src.insight import _week_month

    with get_engine().connect() as c:
        rows = c.execute(
            select(items.c.published_week, func.count())
            .where(items.c.kept.is_(True), items.c.published_week.isnot(None))
            .group_by(items.c.published_week)).all()
        reviewed = {w for (w,) in c.execute(select(digests.c.week))
                    if w and len(w) == 7 and w[4] == "-"}

    per: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for w, n in rows:
        m = _week_month(w)
        if m:
            per[m].append({"week": w, "label": week_label(w), "n": n})

    out = []
    for m in sorted(per, reverse=True):
        weeks = sorted(per[m], key=lambda x: x["week"], reverse=True)
        out.append({"month": m, "label": f"{m[:4]}년 {int(m[5:])}월",
                    "n": sum(x["n"] for x in weeks), "weeks": weeks,
                    "review": m in reviewed})
    return {"months": out}


@router.get("/month_view")
def month_view(month: str = Query("")) -> dict[str, Any]:
    """한 달을 한 화면으로. 리뷰 글 + 그 달의 실제 숫자.

    ★ 리뷰 글이 없는 달이 대부분이다(85개월 중 1개). 그렇다고 빈 화면을 낼 이유는
      없다 — 숫자는 전부 DB에서 지금 계산된다. 글이 없으면 글만 없다고 적는다.
      없는 해설을 지어내지 않는다. 근거 없는 문장 하나가 이 화면의 신뢰를 죽인다.
    """
    from src.insight import _week_month

    with get_engine().connect() as c:
        allw = [(w, n) for w, n in c.execute(
            select(items.c.published_week, func.count())
            .where(items.c.kept.is_(True), items.c.published_week.isnot(None))
            .group_by(items.c.published_week))]
        by_month: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for w, n in allw:
            m = _week_month(w)
            if m:
                by_month[m].append((w, n))
        if not month:
            month = max(by_month) if by_month else ""
        if not month or month not in by_month:
            return {"month": month, "label": "", "empty": True}

        mine = sorted(w for w, _ in by_month[month])
        row = c.execute(select(digests.c.lead, digests.c.generated_at)
                        .where(digests.c.week == month)).first()
        kept = sum(n for _, n in by_month[month])

        # 축별 — 이 달에 어떤 주제가 얼마나 있었나
        ax = dict(c.execute(
            select(item_axes.c.axis, func.count(func.distinct(item_axes.c.item_id)))
            .select_from(item_axes.join(items, items.c.id == item_axes.c.item_id))
            .where(items.c.kept.is_(True), items.c.published_week.in_(mine))
            .group_by(item_axes.c.axis)).all())

        # 전월 대비 — [예시]가 아니라 실제 값이다
        prev = _prev_month(month)
        prev_kept = sum(n for _, n in by_month.get(prev, []))

        top = c.execute(
            select(items.c.id, items.c.title, items.c.url, items.c.source,
                   items.c.published_at, items.c.cross_score, items.c.insight)
            .where(items.c.kept.is_(True), items.c.published_week.in_(mine))
            .order_by(func.coalesce(items.c.cross_score, 0).desc(),
                      items.c.published_at.desc()).limit(8)).all()
        axmap = _axes_of(c, [r.id for r in top])

    # 그 달에 많이 나온 기관 — kw_week를 주차로 걸러 더한다
    with get_engine().connect() as c:
        from src.extract import is_org_keyword
        orgrows = c.execute(select(kw_week.c.keyword, func.sum(kw_week.c.n))
                            .where(kw_week.c.week.in_(mine))
                            .group_by(kw_week.c.keyword)).all()
    orgs_ = sorted(((k, int(n)) for k, n in orgrows if is_org_keyword(k)),
                   key=lambda x: -x[1])[:8]

    return {
        "month": month, "label": f"{month[:4]}년 {int(month[5:])}월",
        # AI가 쓴 글에 'W34~W35'가 그대로 박혀 나온다. 읽는 순간 사람 표기로 바꾼다 —
        # 표기 하나 때문에 sonnet 호출을 다시 할 일이 아니다.
        "lead": humanize_weeks(row[0], int(month[:4])) if row and row[0] else None,
        "generated": str(row[1])[:16] if row and row[1] else "",
        "weeks": [{"week": w, "label": week_label(w),
                   "n": dict(by_month[month]).get(w, 0)} for w in sorted(mine, reverse=True)],
        "kept": kept,
        "prev": prev, "prev_label": f"{prev[:4]}년 {int(prev[5:])}월" if prev else "",
        "prev_kept": prev_kept,
        "delta": (kept - prev_kept) if prev_kept else None,
        "axes": [{"axis": a, "n": ax.get(a, 0)} for a in ("ai", "bigdata", "smallbiz")],
        "orgs": [{"keyword": k, "n": n} for k, n in orgs_],
        "top": [{"id": r.id, "title": r.title or "", "url": r.url or "",
                 "source": r.source, "kept": True,
                 "published": str(r.published_at)[:10] if r.published_at else "",
                 "cross_score": r.cross_score, "insight": r.insight or None,
                 "axes": sorted(axmap.get(r.id, []))} for r in top],
    }


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:])
    return f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}"


@router.get("/home")
@cached(seconds=60)
def home(week: str = Query("")) -> dict[str, Any]:
    """메인 화면이 쓰는 것들을 **한 번에** 낸다.

    원격 DB(Supabase)라 왕복 하나가 곧 지연이다. 화면을 열 때마다 5~6번 부르면
    체감이 확 나빠져서, 홈이 필요한 만큼만 모아 한 응답으로 돌려준다.

    ★ week를 받는다. 사이드바에서 주차를 바꿨는데 홈만 최신 주에 머물러 있으면,
      선택창은 7월을 가리키는데 화면은 9월이라 고장으로 읽힌다(실측 지적).
      '지난 회차'는 화면을 옮기는 게 아니라 **보고 있는 주차를 바꾸는** 조작이다.
      수집 현황만은 늘 지금 값이다 — 수집기가 멈췄는지는 주차와 무관한 정보다.
    """
    from src.digest import latest_week

    with get_engine().connect() as c:
        week = (week or "").strip() or (latest_week(c) or "")
        # 소스별 수집 현황 — 수집기가 조용히 멈춘 걸 알아채는 유일한 화면이다.
        #   스케줄러가 "성공"으로 보고하면서 실제로는 아무것도 안 받는 상황이
        #   가능하므로(잠금 버그가 실제로 그랬다) 여기서 눈에 보이게 둔다.
        labels = CFG.get("source_labels") or {}
        # ★ '지금도 수집하는 소스'만 지각을 경고한다.
        #   sobiz_news는 자매 프로젝트에서 한 번 옮겨온 데이터라 sources 설정에
        #   아예 없다. 그런데 마지막 수집일로만 판정하니 영구히 빨갛게 떴다 —
        #   고칠 수 없는 경고가 늘 켜져 있으면 수집 현황 자체를 안 보게 된다.
        live = {name for name, spec in (CFG.get("sources") or {}).items()
                if (spec or {}).get("enabled")}
        health = [
            {"source": s, "label": labels.get(s, s), "total": n, "live": s in live,
             "latest": str(p)[:10] if p else "", "collected": str(cl)[:10] if cl else ""}
            for s, n, p, cl in c.execute(
                select(items.c.source, func.count(), func.max(items.c.published_at),
                       func.max(items.c.collected_at))
                .group_by(items.c.source).order_by(func.count().desc()))
        ]
    # 인자를 전부 명시한다 — 위 _regulatory의 주석 참고
    d = digest(week=week) if week else {"sections": [], "lead": None, "total_kept": 0}
    sec = {s["key"]: s["items"] for s in d.get("sections", [])}
    try:
        # ★ 고른 주차를 그대로 넘긴다. 예전엔 week=""로 박혀 있어서 홈이 과거
        #   주차를 보고 있어도 급상승만 최신 주를 보여줬다 — 한 화면 안에서
        #   두 주차가 섞이는 게 제일 나쁘다.
        tr = trend(week=week, top=10)
    except Exception:
        tr = {"rows": [], "week_label": ""}
    return {
        "week": week, "week_label": week_label(week) if week else "",
        "lead": d.get("lead"), "total_kept": d.get("total_kept", 0),
        # 법령은 다섯 줄. 급상승은 열 줄을 유지한다 — 순위 목록이라 다섯 줄로
        # 자르면 "10위 안"이라는 감각이 사라진다. 카드 높이가 어긋나는 건
        # 급상승 줄이 법령 줄보다 낮아 실제로는 크게 벌어지지 않는다.
        "crossing": sec.get("crossing", [])[:5],
        "regulatory": _regulatory(5, 0, "")["items"],
        "trending": (tr.get("rows") or [])[:10],
        # 축을 나눠 함께 보낸다. 섞으면 AI가 목록을 독차지해 소상공인 신호가
        # 통째로 묻힌다(실측: 상위 25개 중 ai 24 / smallbiz 1 / bigdata 0).
        "trend_groups": _trend_groups(week, 5),
        "health": health,
    }


# ── /newsletter 화면은 걷어냈다 ──────────────────────────────────
# 보낼 수 없는 메일의 미리보기였다(smtp.configured=False). 메뉴에서는 이미
# 내려가 있었고 패널·로더만 죽은 채로 남아 있었다.
# 메일 발송 경로는 src/mailer.py(CLI)라 이 화면과 무관하게 살아 있다.
# 여기 묻혀 있던 alerts.keywords는 아래 /watch로 끌어올렸다 — 설정에만 있고
# 화면에 없어서, 이미 동작하는 기능을 아무도 못 보고 있었다.


@router.get("/watch")
def watch(days: int = Query(0)) -> dict[str, Any]:
    """관심 키워드에 걸린 최근 항목.

    ★ 통계로는 못 잡는 것을 잡는 자리다.
      급상승은 건수 기반이라 '적게 보도되지만 중요한' 건을 영영 못 띄운다 —
      Claude Fable 5.1 공개가 그 주에 5건뿐이라 순위 밖이었다(실측).
      등록해 둔 말은 건수와 무관하게 걸린다.

    ★ kept로 거르지 않는다. 관심 키워드는 사용자가 직접 적은 신호라
      통계 필터보다 위다. find_hits가 규제 소스도 함께 본다.
    """
    from src.alerts import find_hits

    acfg = CFG.get("alerts") or {}
    kws = [k for k in (acfg.get("keywords") or []) if str(k).strip()]
    days = int(days) or int(acfg.get("days", 7))
    if not kws:
        return {"days": days, "keywords": [], "groups": [], "total": 0}
    try:
        # seen을 비워 넘긴다 — 화면은 '아직 안 알린 것'이 아니라 '최근 걸린 것' 전부다
        hits = find_hits(CFG, kws, days, set())
    except Exception as exc:
        return {"days": days, "keywords": kws, "groups": [], "total": 0,
                "error": str(exc)[:120]}
    groups = [{"keyword": k, "rows": v} for k, v in hits.items() if v]
    groups.sort(key=lambda g: -len(g["rows"]))
    return {"days": days, "keywords": kws, "groups": groups,
            "total": sum(len(g["rows"]) for g in groups)}


@router.get("/index_status")
def index_status() -> dict[str, Any]:
    """압축 키워드 인덱스가 준비됐는지.

    급상승·기관·연관어 세 화면이 이 표들을 쓴다. 이제 본 DB에 있으므로 배포한
    컨테이너도 그대로 읽는다 — 예전처럼 컨테이너가 뜰 때마다 다시 만들 필요가 없다.
    비어 있다면 python -m src.index_build 를 아직 안 돌린 것이다.
    """
    with get_engine().connect() as c:
        n_week = c.execute(select(func.count()).select_from(kw_week)).scalar_one()
        n_nb = c.execute(select(func.count()).select_from(kw_neighbor)).scalar_one()
        n_meta = c.execute(select(func.count()).select_from(kw_meta)).scalar_one()
    ready = n_week > 0 and n_nb > 0 and n_meta > 0
    return {"ready": ready, "building": not ready,
            "rows": n_nb, "weeks": n_week, "keywords": n_meta}


@router.get("/cache")
def cache_info() -> dict[str, Any]:
    """캐시가 실제로 듣고 있는지 확인용. 안 맞으면 여기부터 본다."""
    return ego_cache.info()


@router.get("/keyword/{kw}")
def keyword(kw: str, limit: int = Query(20), since: str = '', until: str = '', axis: str = '') -> dict[str, Any]:
    """키워드가 나온 기사들. 연관어 그래프에서 노드를 클릭하면 이걸 부른다.

    그래프가 "예쁜데 뭘 봐야 할지 모르겠다"로 끝나지 않으려면 노드에서 실제 기사로
    내려갈 수 있어야 한다. 브릿지 노드를 발견하는 것과 그게 왜 브릿지인지 확인하는 건
    다른 일이고, 후자가 없으면 과제 후보로 쓸 수 없다.
    """
    # ★ kept로 거르지 않는다.
    #   키워드는 전체 코퍼스에서 뽑는데 기사만 통과분으로 좁히면 "48건인데 기사 없음"이
    #   된다(실측: Ollama 48건 중 통과 4건, Kubernetes 65건 중 4건).
    #   대신 통과분을 위로 올리고 각 항목에 표시를 단다.
    conditions = [items.c.id.in_(select(kw_item.c.item_id).where(kw_item.c.keyword == kw))]
    dates = search_conds('', axis, since, until, 0)
    if dates is not None:
        conditions.append(dates)
    with get_engine().connect() as c:
        total = c.execute(select(func.count()).select_from(items).where(*conditions)).scalar_one()
        rows = c.execute(select(items.c.id, items.c.title, items.c.url, items.c.source,
                                items.c.published_at, items.c.cross_score, items.c.insight, items.c.kept)
                         .where(*conditions).order_by(
                             func.coalesce(items.c.kept, False).desc(),
                             func.coalesce(items.c.cross_score, 0).desc(),
                             items.c.published_at.desc().nullslast(), items.c.id.desc())
                         .limit(max(1, min(limit, 200)))).all()
        ax = _axes_of(c, [r.id for r in rows])
    return {
        "keyword": kw, "total": total,
        "kept": sum(1 for r in rows if r.kept),
        "items": [{"id": r.id, "title": r.title or "", "url": r.url or "",
                   "source": r.source, "kept": bool(r.kept),
                   "published": _kst_date(r.published_at),
                   "cross_score": r.cross_score, "insight": r.insight or None,
                   "axes": sorted(ax.get(r.id, []))} for r in rows],
    }


@router.get("/item/{item_id}")
def item(item_id: int) -> dict[str, Any]:
    with get_engine().connect() as c:
        r = c.execute(select(items).where(items.c.id == item_id)).first()
        if not r:
            return {"error": "not found"}
        my = [a for (a,) in c.execute(
            select(item_axes.c.axis).where(item_axes.c.item_id == item_id))]
        rel = c.execute(
            select(items.c.id, items.c.title, items.c.url, items.c.source,
                   items.c.published_at, items.c.cross_score, items.c.insight)
            .where(items.c.kept.is_(True), items.c.id != item_id,
                   items.c.id.in_(select(item_axes.c.item_id)
                                  .where(item_axes.c.axis.in_(my or ["_"]))))
            .order_by(items.c.cross_score.desc()).limit(8)).all() if my else []
        rax = _axes_of(c, [x.id for x in rel])
    clean_insight, checklist = _split_checklist(r.insight)
    return {
        "id": r.id, "title": r.title, "summary": r.summary, "url": r.url,
        "source": r.source, "published": str(r.published_at)[:10] if r.published_at else "",
        "cross_score": r.cross_score, "relevance": r.relevance,
        "insight": clean_insight, "checklist": checklist, "axes": sorted(my),
        # 법령은 부처·종류·제개정·시행일이 판단에 필요하다. meta에 들어 있다.
        "meta": r.meta if isinstance(r.meta, dict) else {},
        "related": [{"id": x.id, "title": x.title, "url": x.url, "source": x.source,
                     "published": str(x.published_at)[:10] if x.published_at else "",
                     "cross_score": x.cross_score, "insight": x.insight or None,
                     "axes": sorted(rax.get(x.id, []))} for x in rel],
    }
