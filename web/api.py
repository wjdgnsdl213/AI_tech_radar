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

from src.db import digests, get_engine, item_axes, items, kw_engine, load_config
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
        # 차트 축에 그대로 쓰이므로 사람이 읽는 표기를 함께 싣는다.
        # 'W31'은 몇 월인지 알 수 없다.
        r["series"] = [{"week": w, "label": week_label(w), "n": n}
                       for w, n in series(r["keyword"], axis_weeks)]
    return {"week": week, "week_label": week_label(week),
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
    with kw_engine().connect() as c:      # 키워드는 파생 인덱스 DB에 있다
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
def ego(kw: str = Query(...), hops: int = Query(1), per_hop: int = Query(12),
        min_cooc: int = Query(0), max_nodes: int = Query(46)) -> dict[str, Any]:
    """키워드 하나를 중심으로 한 연관어 망. 홉 수를 지정할 수 있다.

    ★ 전체 코퍼스를 본다 (kept 필터를 걸지 않는다)
      필터는 다이제스트를 위한 것이다 — 밀어주는 지면은 좁으니 좁게 걸러야 한다.
      탐색은 반대다. 'Ollama'(48건), 'LangChain'(12건), 'Kubernetes'(65건) 같은 말은
      통과분 3,046건 안에는 거의 없다. push와 pull은 기준이 달라야 한다.

    ★ 인덱스 질의로만 만든다
      220만 행을 매번 메모리에 올리면 요청마다 몇 초가 걸린다. 필요한 건
      "이 키워드가 나온 문서"와 "그 문서에 또 뭐가 있나"뿐이라 인덱스로 좁혀 읽는다.

    NPMI 식은 graph.py와 같다. 화면마다 다른 지표를 쓰면 값이 갈린다.
    """
    import math
    from collections import Counter

    from src.extract import STOPWORDS, item_keywords

    hops = max(1, min(hops, 3))
    per_hop = max(3, min(per_hop, 30))
    eng = kw_engine()                     # 키워드는 파생 인덱스 DB에 있다

    def chunked(seq, n=400):
        seq = list(seq)
        for i in range(0, len(seq), n):
            yield seq[i:i + n]

    with eng.connect() as c:
        n_docs = c.execute(select(func.count(func.distinct(item_keywords.c.item_id)))
                           ).scalar_one() or 1

        # 대소문자가 달라도 찾게 한다 — 'ollama'로 쳐도 'Ollama'가 나와야 한다.
        # 대소문자 변형이 여러 개면 **문서가 가장 많은 것**을 고른다.
        # (실측: 'CLAUDE' 2건과 'Claude' 873건이 따로 있어 LIMIT 1이 적은 쪽을 집었다)
        center = c.execute(
            select(item_keywords.c.keyword)
            .where(func.lower(item_keywords.c.keyword) == kw.strip().lower())
            .group_by(item_keywords.c.keyword)
            .order_by(func.count().desc()).limit(1)).scalar_one_or_none()
        if not center:
            return {"center": kw, "empty": True, "nodes": [], "edges": [],
                    "reason": "코퍼스에 없는 키워드입니다."}

        def docs_of(words: list[str]) -> dict[str, set[int]]:
            out: dict[str, set[int]] = {w: set() for w in words}
            for part in chunked(words):
                for w, i in c.execute(
                        select(item_keywords.c.keyword, item_keywords.c.item_id)
                        .where(item_keywords.c.keyword.in_(part))):
                    out[w].add(i)
            return out

        def df_of(words: list[str]) -> dict[str, int]:
            out: dict[str, int] = {}
            for part in chunked(words):
                for w, n in c.execute(
                        select(item_keywords.c.keyword, func.count())
                        .where(item_keywords.c.keyword.in_(part))
                        .group_by(item_keywords.c.keyword)):
                    out[w] = n
            return out

        def neighbours_of(word: str, host: set[int], exclude: set[str]) -> list[tuple]:
            """host 문서들에 word와 함께 나온 키워드를 NPMI 순으로."""
            co = Counter()
            for part in chunked(host):
                for k, n in c.execute(
                        select(item_keywords.c.keyword, func.count())
                        .where(item_keywords.c.item_id.in_(part))
                        .group_by(item_keywords.c.keyword)):
                    co[k] += n
            floor = min_cooc or max(3, min(len(host) // 15, 6))
            cand = {k: n for k, n in co.items()
                    if n >= floor and k not in STOPWORDS and k not in exclude
                    and word not in k and k not in word}
            if not cand:
                return []
            dfs = df_of(list(cand) + [word])
            dw = dfs.get(word, 1)

            def npmi(k: str, n: int) -> float:
                pa, pb, pab = dw / n_docs, dfs.get(k, 1) / n_docs, n / n_docs
                d = -math.log(pab)
                return math.log(pab / (pa * pb)) / d if d > 0 else 0.0

            ranked = sorted(((npmi(k, n), k, n) for k, n in cand.items()), reverse=True)
            # n-gram 조각 제거 — 좁은 지면이 같은 말의 조각으로 덮이는 걸 막는다
            out: list[tuple] = []
            for sc, k, n in ranked:
                if any(k != o and k in o and m >= n * 0.5 for _, o, m in ranked):
                    continue
                # out은 4-튜플이다. 3개로 언팩하면 ValueError로 500이 난다(실측).
                if any(o in k and n >= m * 0.5 for _, o, m, _d in out):
                    continue
                out.append((sc, k, n, dfs.get(k, 0)))
                if len(out) >= per_hop:
                    break
            return out

        # ── 홉을 넓혀 나간다 ──
        nodes: dict[str, dict[str, Any]] = {
            center: {"keyword": center, "hop": 0, "center": True,
                     "npmi": 1.0, "cooc": 0, "df": 0}}
        frontier = [center]
        doc_cache = docs_of([center])
        # ★ 총 노드 수에 상한을 둔다.
        #   홉마다 노드가 per_hop배로 늘어 3홉이면 100개를 넘는다. 그러면 겹치지 않게
        #   그리려고 캔버스를 키우게 되고, 화면 폭에 맞춰 축소되면서 글자가 다시
        #   작아진다(실측: 3홉 119노드 → viewBox 2940px). 넓혀서 푸는 문제가 아니다.
        #   깊은 홉일수록 가지를 좁혀, 멀리 보되 굵은 줄기만 남긴다.
        for hop in range(1, hops + 1):
            nxt: list[str] = []
            room = max_nodes - len(nodes)
            if room <= 0:
                break
            budget = max(2, room // max(1, len(frontier)))
            for w in frontier:
                got = 0
                for sc, k, n, d in neighbours_of(w, doc_cache[w], set(nodes)):
                    if k in nodes or got >= budget or len(nodes) >= max_nodes:
                        continue
                    # 조각 판정은 홉을 넘어서도 해야 한다. 홉 안에서만 걸면
                    # 2홉에 '대전결제'와 '대전결제데이터', '서울시상권'과
                    # '서울시상권분석'이 따로 올라온다(실측).
                    if any(k in o or o in k for o in nodes if len(o) > 2 and len(k) > 2):
                        continue
                    nodes[k] = {"keyword": k, "hop": hop, "center": False,
                                "npmi": round(sc, 3), "cooc": n, "df": d,
                                "via": w}
                    nxt.append(k)
                    got += 1
            if not nxt:
                break
            frontier = nxt
            if hop < hops:
                doc_cache.update(docs_of(nxt))

        names = list(nodes)
        dfs = df_of(names)
        for k, nd in nodes.items():
            nd["df"] = dfs.get(k, nd.get("df", 0))

        # ── 노드끼리의 간선 ──
        sets = docs_of(names)
        edges = []
        for x in range(len(names)):
            for y in range(x + 1, len(names)):
                a, b = names[x], names[y]
                n = len(sets[a] & sets[b])
                if n < 2:
                    continue
                pa, pb, pab = dfs.get(a, 1) / n_docs, dfs.get(b, 1) / n_docs, n / n_docs
                d = -math.log(pab)
                v = math.log(pab / (pa * pb)) / d if d > 0 else 0.0
                if v >= 0.15:
                    edges.append({"source": a, "target": b,
                                  "npmi": round(v, 4), "cooc": n})

        # 주제 성향 — 색에 쓴다
        axis_hits: dict[str, Counter] = {k: Counter() for k in names}
        all_docs = set().union(*sets.values()) if sets else set()
        pairs: list[tuple[int, str]] = []
    # 축은 본 DB에 있다 — 키워드 DB와 다른 파일이라 커넥션을 따로 연다
    with get_engine().connect() as mc:
        for part in chunked(all_docs):
            pairs += list(mc.execute(select(item_axes.c.item_id, item_axes.c.axis)
                                     .where(item_axes.c.item_id.in_(part))))
        by_doc: dict[int, list[str]] = {}
        for i, a in pairs:
            by_doc.setdefault(i, []).append(a)
        for k in names:
            for i in sets[k]:
                for a in by_doc.get(i, ()):
                    axis_hits[k][a] += 1

    for k, nd in nodes.items():
        h = axis_hits[k]
        tot = sum(h.values())
        nd["axis"] = max(h, key=h.get) if h else None
        nd["axis_share"] = {a: round(v / tot, 3) for a, v in h.items()} if tot else {}

    return {"center": center, "empty": False, "hops": hops,
            "nodes": list(nodes.values()), "edges": edges,
            "docs": len(sets.get(center, ()))}


@router.get("/keyword/{kw}")
def keyword(kw: str, limit: int = Query(20)) -> dict[str, Any]:
    """키워드가 나온 기사들. 연관어 그래프에서 노드를 클릭하면 이걸 부른다.

    그래프가 "예쁜데 뭘 봐야 할지 모르겠다"로 끝나지 않으려면 노드에서 실제 기사로
    내려갈 수 있어야 한다. 브릿지 노드를 발견하는 것과 그게 왜 브릿지인지 확인하는 건
    다른 일이고, 후자가 없으면 과제 후보로 쓸 수 없다.
    """
    from src.extract import item_keywords
    with kw_engine().connect() as kc:
        ids = [i for (i,) in kc.execute(
            select(item_keywords.c.item_id).where(item_keywords.c.keyword == kw))]
    if not ids:
        return {"keyword": kw, "total": 0, "items": []}
    # ★ kept로 거르지 않는다.
    #   키워드는 전체 코퍼스에서 뽑는데 기사만 통과분으로 좁히면 "48건인데 기사 없음"이
    #   된다(실측: Ollama 48건 중 통과 4건, Kubernetes 65건 중 4건).
    #   대신 통과분을 위로 올리고 각 항목에 표시를 단다.
    with get_engine().connect() as c:
        rows = []
        for part in (ids[i:i + 400] for i in range(0, len(ids), 400)):
            rows += list(c.execute(
                select(items.c.id, items.c.title, items.c.url, items.c.source,
                       items.c.published_at, items.c.cross_score, items.c.insight,
                       items.c.kept)
                .where(items.c.id.in_(part))))
        rows.sort(key=lambda r: (not bool(r.kept), -(r.cross_score or 0),
                                 str(r.published_at or "")), reverse=False)
        rows = rows[:limit]
        ax = _axes_of(c, [r.id for r in rows])
    return {
        "keyword": kw, "total": len(ids),
        "kept": sum(1 for r in rows if r.kept),
        "items": [{"id": r.id, "title": r.title or "", "url": r.url or "",
                   "source": r.source, "kept": bool(r.kept),
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
