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

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import and_, func, or_, select

from src.db import digests, get_engine, item_axes, items, load_config
from src.digest import week_label

router = APIRouter(prefix="/api")
CFG = load_config()
LABELS = {a: s.get("label", a) for a, s in CFG["axes"].items()}


def _parse_date(s: str) -> datetime | None:
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def search_conds(q: str, axis: str, since: str, until: str, kept_only: int):
    """검색 조건. 화면·CSV·API가 공유해야 결과가 갈라지지 않는다."""
    conds = []
    if q:
        conds.append(or_(items.c.title.contains(q), items.c.summary.contains(q)))
    if kept_only:
        conds.append(items.c.kept.is_(True))
    if since and (d := _parse_date(since)):
        conds.append(items.c.published_at >= d)
    if until and (d := _parse_date(until)):
        conds.append(items.c.published_at <= d.replace(hour=23, minute=59, second=59))
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
    sections = [{"key": "crossing", "label": "🔥 교집합", "items": d["crossing"]}]
    if d["regulatory"]:
        sections.append({"key": "regulatory", "label": "⚠️ 규제 알림",
                         "items": d["regulatory"]})
    for ax, ps in d["by_axis"].items():
        sections.append({"key": ax, "label": LABELS.get(ax, ax), "items": ps})
    return {
        "week": week, "week_label": week_label(week),
        "lead": d.get("lead"), "total_kept": d["total_kept"],
        "sections": sections, "trending": d.get("trending", []),
        "empty": not d["crossing"] and not any(d["by_axis"].values()),
    }


@router.get("/search")
def search(q: str = Query(""), axis: str = Query(""), since: str = Query(""),
           until: str = Query(""), kept_only: int = Query(1),
           page: int = Query(1), size: int = Query(50)) -> dict[str, Any]:
    where = search_conds(q, axis, since, until, kept_only)
    size = max(1, min(size, 200))
    stmt = select(items.c.id, items.c.title, items.c.summary, items.c.url,
                  items.c.source, items.c.published_at, items.c.cross_score,
                  items.c.relevance, items.c.insight)
    cnt = select(func.count()).select_from(items)
    if where is not None:
        stmt, cnt = stmt.where(where), cnt.where(where)
    off = max(0, (page - 1) * size)
    stmt = stmt.order_by(items.c.cross_score.desc(),
                         items.c.published_at.desc()).limit(size).offset(off)
    with get_engine().connect() as c:
        rows = c.execute(stmt).all()
        total = c.execute(cnt).scalar_one()
        ax = _axes_of(c, [r.id for r in rows])
    return {
        "total": total, "page": page, "size": size,
        "items": [{
            "id": r.id, "title": r.title or "", "summary": (r.summary or "")[:300],
            "url": r.url or "", "source": r.source,
            "published": str(r.published_at)[:10] if r.published_at else "",
            "cross_score": r.cross_score, "relevance": r.relevance,
            "insight": r.insight or None, "axes": sorted(ax.get(r.id, [])),
        } for r in rows],
    }


@router.get("/trend")
def trend(week: str = Query(""), top: int = Query(20)) -> dict[str, Any]:
    from src.trend import prev_weeks, rising, series
    tcfg = CFG.get("trend", {})
    if not week:
        with get_engine().connect() as c:
            week = c.execute(select(func.max(items.c.published_week))
                             .where(items.c.kept.is_(True))).scalar_one_or_none() or ""
    if not week:
        return {"week": None, "rows": []}
    back = int(tcfg.get("compare_weeks", 4))
    rows = rising(week, back, int(tcfg.get("min_weekly_freq", 5)))[:top]
    axis_weeks = list(reversed(prev_weeks(week, back))) + [week]
    for r in rows[:8]:
        r["series"] = [{"week": w, "n": n} for w, n in series(r["keyword"], axis_weeks)]
    return {"week": week, "week_label": week_label(week),
            "weeks": [{"week": w, "label": week_label(w)} for w in axis_weeks],
            "rows": rows}


@router.get("/graph")
def graph(top: int = Query(24)) -> dict[str, Any]:
    """연관어 그래프. 브릿지 노드가 이 화면의 읽는 법이다."""
    from src.graph import bridges, build, load_data
    g = CFG.get("graph", {})
    docs, axes = load_data()
    if not docs:
        return {"nodes": [], "edges": [], "bridges": [], "empty": True}
    G = build(docs, axes, int(g.get("min_df", 15)), int(g.get("min_cooc", 5)),
              float(g.get("npmi_cut", 0.25)), int(g.get("max_nodes", 400)))
    br = bridges(G, top, int(g.get("min_degree", 8)))
    keep = {n["keyword"] for n in G["nodes"].values() if n["degree"] >= 3}
    return {
        "nodes": [n for n in G["nodes"].values() if n["keyword"] in keep],
        "edges": [e for e in G["edges"]
                  if e["source"] in keep and e["target"] in keep],
        "bridges": br, "empty": False,
    }


@router.get("/suggest")
def suggest(q: str = Query(""), limit: int = Query(12)) -> dict[str, Any]:
    """키워드 자동완성. 검색형 네트워크의 입구다.

    빈도순으로 그냥 내면 '상공인', '전통' 같은 n-gram 조각이 상위를 먹는다.
    사용자가 처음 보는 목록이라 여기가 지저분하면 기능 전체가 못 미더워 보인다.
    → 더 긴 키워드에 포함되면서 빈도가 비슷한 것은 조각으로 보고 뺀다.
    """
    from src.extract import STOPWORDS, item_keywords
    with get_engine().connect() as c:
        stmt = (select(item_keywords.c.keyword, func.count().label("n"))
                .group_by(item_keywords.c.keyword)
                .order_by(func.count().desc()).limit(limit * 8))
        if q:
            stmt = stmt.where(item_keywords.c.keyword.contains(q))
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


@router.get("/ego")
def ego(kw: str = Query(...), limit: int = Query(24)) -> dict[str, Any]:
    """키워드 하나를 중심으로 한 연관어 망(ego network).

    ★ 전체 그래프를 미리 그려두지 않고 검색할 때마다 만든다.
      전체 코퍼스를 400 노드로 압축하면 어느 주제에도 안 맞는 지도가 되고,
      일반어가 상위를 먹는다. "지금 보는 주제 주변이 어떻게 생겼나"가 실제 질문이다.
      계산도 훨씬 싸다 — 전체 그래프는 40만 쌍을 훑지만 여기는 이웃만 본다.

    NPMI는 graph.py와 같은 식이다. 화면마다 다른 지표를 쓰면 값이 갈린다.
    """
    import math
    from collections import Counter, defaultdict

    from src.extract import STOPWORDS, item_keywords

    with get_engine().connect() as c:
        kept = {i for (i,) in c.execute(
            select(items.c.id).where(items.c.kept.is_(True)))}
        docs: dict[int, set[str]] = defaultdict(set)
        for i, k in c.execute(select(item_keywords.c.item_id, item_keywords.c.keyword)):
            if i in kept:
                docs[i].add(k)
        axis_of: dict[int, set[str]] = defaultdict(set)
        for i, a in c.execute(select(item_axes.c.item_id, item_axes.c.axis)):
            if i in docs:
                axis_of[i].add(a)

    n_docs = len(docs) or 1
    df = Counter(k for ks in docs.values() for k in ks)
    if kw not in df:
        return {"center": kw, "empty": True, "nodes": [], "edges": []}

    host_docs = [i for i, ks in docs.items() if kw in ks]

    def npmi(a: str, b: str, cooc: int) -> float:
        pa, pb, pab = df[a] / n_docs, df[b] / n_docs, cooc / n_docs
        d = -math.log(pab)
        return math.log(pab / (pa * pb)) / d if d > 0 else 0.0

    # 중심 키워드와 함께 나온 것들.
    # 동시등장이 2건이면 NPMI가 크게 흔들려 지역명·조각이 상위에 낀다(실측:
    # 상권분석 이웃에 '안산', '사이', '예비'가 들어왔다). 중심 기사 수에 비례해
    # 하한을 올리되, 기사가 많은 키워드에서 너무 빡빡해지지 않게 상한을 둔다.
    min_cooc = max(3, min(len(host_docs) // 15, 6))
    co = Counter()
    for i in host_docs:
        co.update(docs[i] - {kw})
    cand = [(k, n) for k, n in co.items()
            if n >= min_cooc and k not in STOPWORDS and df[k] >= 5
            and kw not in k and k not in kw]          # 조각·상위어 제외
    # n-gram 조각을 걷어낸다. 안 하면 좁은 ego 지면이 같은 말의 조각으로 덮인다.
    # 실측: 공공데이터 이웃이 관계부처 / 관계부처협의 / 즉시실무 / 즉시실무협의체로,
    #       마이데이터 이웃이 초본 / 등록초본 / 주민등록초본으로 채워졌다.
    # 규칙은 graph.drop_fragments와 같다 — 더 긴 말이 있고 동시등장이 비슷하면 조각이다.
    ranked = sorted(((npmi(kw, k, n), k, n) for k, n in cand), reverse=True)
    scored: list[tuple[float, str, int]] = []
    for s, k, n in ranked:
        # 비율 0.5: n-gram에서 더 긴 말에 포함되는 짧은 말은 거의 항상 조각이다.
        # 0.8로 조였더니 '관계부처'(9건)가 '관계부처협의'(7건)의 조각으로 안 잡혔다.
        # 여러 복합어에 두루 쓰이는 말('개방' 20건)은 자기 빈도가 훨씬 커서 살아남는다.
        if any(k != o and k in o and m >= n * 0.5 for _, o, m in ranked):
            continue                       # 더 긴 말의 조각
        if any(o in k and n >= m * 0.5 for _, o, m in scored):
            continue                       # 이미 뽑은 것의 확장형(같은 말)
        scored.append((s, k, n))
        if len(scored) >= limit:
            break
    names = [kw] + [k for _, k, _ in scored]
    nameset = set(names)

    # 이웃끼리의 간선도 그린다 — 이게 있어야 '망'으로 보인다
    pair = Counter()
    for ks in docs.values():
        sel = sorted(ks & nameset)
        for x in range(len(sel)):
            for y in range(x + 1, len(sel)):
                pair[(sel[x], sel[y])] += 1
    edges = []
    for (a, b), n in pair.items():
        if n < 2:
            continue
        v = npmi(a, b, n)
        if v >= 0.15:
            edges.append({"source": a, "target": b, "npmi": round(v, 4), "cooc": n})

    axis_hits: dict[str, Counter] = defaultdict(Counter)
    for i, ks in docs.items():
        for k in ks & nameset:
            for a in axis_of.get(i, ()):
                axis_hits[k][a] += 1
    nodes = []
    for k in names:
        h = axis_hits[k]
        tot = sum(h.values())
        nodes.append({
            "keyword": k, "df": df[k], "center": k == kw,
            "axis": max(h, key=h.get) if h else None,
            "axis_share": {a: round(v / tot, 3) for a, v in h.items()} if tot else {},
            "npmi": next((round(s, 3) for s, kk, _ in scored if kk == k), 1.0),
            "cooc": next((n for _, kk, n in scored if kk == k), df[k]),
        })
    return {"center": kw, "empty": False, "nodes": nodes, "edges": edges,
            "docs": len(host_docs)}


@router.get("/keyword/{kw}")
def keyword(kw: str, limit: int = Query(20)) -> dict[str, Any]:
    """키워드가 나온 기사들. 연관어 그래프에서 노드를 클릭하면 이걸 부른다.

    그래프가 "예쁜데 뭘 봐야 할지 모르겠다"로 끝나지 않으려면 노드에서 실제 기사로
    내려갈 수 있어야 한다. 브릿지 노드를 발견하는 것과 그게 왜 브릿지인지 확인하는 건
    다른 일이고, 후자가 없으면 과제 후보로 쓸 수 없다.
    """
    from src.extract import item_keywords
    with get_engine().connect() as c:
        ids = [i for (i,) in c.execute(
            select(item_keywords.c.item_id).where(item_keywords.c.keyword == kw))]
        if not ids:
            return {"keyword": kw, "total": 0, "items": []}
        rows = c.execute(
            select(items.c.id, items.c.title, items.c.url, items.c.source,
                   items.c.published_at, items.c.cross_score, items.c.insight)
            .where(items.c.id.in_(ids), items.c.kept.is_(True))
            .order_by(items.c.cross_score.desc(), items.c.published_at.desc())
            .limit(limit)).all()
        ax = _axes_of(c, [r.id for r in rows])
    return {
        "keyword": kw, "total": len(ids),
        "items": [{"id": r.id, "title": r.title or "", "url": r.url or "",
                   "source": r.source,
                   "published": str(r.published_at)[:10] if r.published_at else "",
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
    return {
        "id": r.id, "title": r.title, "summary": r.summary, "url": r.url,
        "source": r.source, "published": str(r.published_at)[:10] if r.published_at else "",
        "cross_score": r.cross_score, "relevance": r.relevance,
        "insight": r.insight or None, "axes": sorted(my),
        "related": [{"id": x.id, "title": x.title, "url": x.url, "source": x.source,
                     "published": str(x.published_at)[:10] if x.published_at else "",
                     "cross_score": x.cross_score, "insight": x.insight or None,
                     "axes": sorted(rax.get(x.id, []))} for x in rel],
    }
