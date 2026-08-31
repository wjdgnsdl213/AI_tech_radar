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

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import and_, func, or_, select

from src.db import digests, get_engine, item_axes, items, kw_engine, load_config
from src.digest import week_label
from web.cache import ego_cache

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
    with get_engine().connect() as c:
        body = c.execute(select(digests.c.body)
                         .where(digests.c.week == week)).scalar_one_or_none()
    tasks = (body or {}).get("tasks", []) if isinstance(body, dict) else []
    return {
        "week": week, "week_label": week_label(week),
        "lead": d.get("lead"), "tasks": tasks, "total_kept": d["total_kept"],
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
    """캐시를 거쳐 _ego를 부른다. 실제 계산은 아래 _ego에 있다.

    '소상공인'(15,390건)은 3.7초가 걸린다. 홉 슬라이더를 왕복하거나 노드를
    더블클릭하며 헤집는 게 이 화면의 용도인데, 그때마다 4초를 기다리면 못 쓴다.
    같은 질의가 반복되는 비율이 높아 캐시가 잘 듣는다.
    """
    key = (kw, hops, per_hop, min_cooc, max_nodes)
    return ego_cache.get_or_call(
        key, lambda: _ego(kw, hops, per_hop, min_cooc, max_nodes))


def _ego(kw: str, hops: int, per_hop: int,
         min_cooc: int, max_nodes: int) -> dict[str, Any]:
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

        # ── ★ 홉을 **실제 간선 기준**으로 다시 매긴다 ──
        #   위 확장 루프는 탐색 순서로 hop을 붙인다. 그런데 중심의 직접 이웃은
        #   budget에 잘려서, 중심과 함께 나오는 말인데도 예산 밖이면 이웃을 통해
        #   2홉으로 발견된다. 그 뒤 아래 간선 계산은 모든 쌍을 보므로 그 노드에도
        #   중심과의 선이 그어진다 — 화면에는 "2홉인데 중심에 붙어 있는" 노드가 된다.
        #   실측: 'AI모델' 2홉 21개 중 20개가 중심과 직접 연결이었다.
        #   간선이 곧 보이는 관계이므로, 홉도 그 간선 위의 최단 거리여야 뜻이 맞는다.
        adj: dict[str, set[str]] = {k: set() for k in names}
        for e2 in edges:
            adj[e2["source"]].add(e2["target"])
            adj[e2["target"]].add(e2["source"])
        dist = {center: 0}
        queue = [center]
        while queue:
            cur = queue.pop(0)
            for nb in adj[cur]:
                if nb not in dist:
                    dist[nb] = dist[cur] + 1
                    queue.append(nb)
        for k, nd in nodes.items():
            if nd["center"]:
                continue
            # 중심과 이어지지 않는 노드(간선이 전부 NPMI 컷 아래)는 탐색 홉을 남긴다
            nd["hop"] = dist.get(k, nd["hop"])

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
      고른다. 그게 이 데이터에서 충분히 통한다 —
      소상공인시장진흥공단·행정안전부·한국관광공사·경기도시장상권진흥원 같은 게
      그대로 잡힌다.
      ⚠ 완전하지 않다: 접미사가 없는 기관(카카오·SKT)은 안 잡힌다.

    ★ 두 가지를 반드시 걸러야 쓸 수 있다 (실측으로 드러났다)
      1) 일반명사: '센터'·'기관'을 접미사에 넣으면 데이터센터·지원센터·공공기관이
         상위를 덮는다. 기관이 아니다. 접미사를 기관 고유의 것만 남긴다.
      2) n-gram 조각: '중소벤처기업부'와 함께 '벤처기업부'·'기업부'가,
         '경기도시장상권진흥원'과 함께 '시장상권진흥원'·'상권진흥원'이 같이 잡힌다.
         trend._fragment_of가 이미 푸는 문제라 그대로 쓴다.
    """
    from src.extract import item_keywords
    from src.trend import _fragment_of, prev_weeks

    # 기관에만 붙는 접미사. '센터'·'기관'·'지원'은 일반명사를 끌고 와서 뺐다.
    SUFFIX = ("공단", "진흥원", "연구원", "재단", "공사", "위원회", "협회",
              "대학교", "은행", "조합", "부", "처", "청")
    with get_engine().connect() as c:
        cur = c.execute(select(func.max(items.c.published_week))
                        .where(items.c.kept.is_(True))).scalar_one_or_none()
    if not cur:
        return {"rows": [], "weeks": []}
    span = [cur, *prev_weeks(cur, max(0, weeks - 1))]

    with kw_engine().connect() as c:
        rows = c.execute(
            select(item_keywords.c.keyword, item_keywords.c.week,
                   func.count(func.distinct(item_keywords.c.item_id)))
            .where(item_keywords.c.week.in_(span), item_keywords.c.kept.is_(True))
            .group_by(item_keywords.c.keyword, item_keywords.c.week)).all()

    agg: dict[str, Counter] = {}
    for kw, wk, n in rows:
        # 5자 미만은 기관명이라기엔 짧다 — '통신부'·'진흥원' 같은 조각이 걸린다
        if len(kw) < 5 or not kw.endswith(SUFFIX):
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
    return {"rows": out[:limit],
            "weeks": [{"week": w, "label": week_label(w)} for w in sorted(span)]}


@router.get("/org_items")
def org_items(kw: str = Query(...), week: str = Query(""),
              limit: int = Query(40)) -> dict[str, Any]:
    """기관 표의 주차 칸을 눌렀을 때 그 주의 기사를 돌려준다.

    keyword 인덱스에서 문서 id를 얻고 본 DB에서 기사를 읽는다 — 두 DB가 갈려
    있어서(item_keywords는 keywords.db) 조인을 못 한다.
    """
    from src.extract import item_keywords

    with kw_engine().connect() as c:
        q = select(item_keywords.c.item_id).where(item_keywords.c.keyword == kw)
        if week:
            q = q.where(item_keywords.c.week == week)
        ids = [i for (i,) in c.execute(q.distinct())]
    if not ids:
        return {"keyword": kw, "week": week, "items": [], "total": 0}
    with get_engine().connect() as c:
        rows = c.execute(
            select(items.c.id, items.c.title, items.c.url, items.c.source,
                   items.c.published_at, items.c.insight, items.c.kept)
            .where(items.c.id.in_(ids[:2000]))
            .order_by(items.c.kept.desc(), items.c.published_at.desc())
            .limit(limit)).all()
        ax = _axes_of(c, [r.id for r in rows])
    return {"keyword": kw, "week": week, "total": len(ids),
            "items": [{"id": r.id, "title": r.title or "", "url": r.url or "",
                       "source": r.source, "insight": r.insight or None,
                       "published": str(r.published_at)[:10] if r.published_at else "",
                       "axes": sorted(ax.get(r.id, []))} for r in rows]}


@router.get("/regulatory")
def regulatory(limit: int = Query(60), days: int = Query(0),
               q: str = Query("")) -> dict[str, Any]:
    """규제 1차 출처에서 온 항목 (HTTP 경로). 실제 조회는 _regulatory에 있다."""
    return _regulatory(limit, days, q)


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
    out = []
    for r in rows:
        m = r.meta if isinstance(r.meta, dict) else {}
        out.append({"id": r.id, "title": r.title or "", "summary": r.summary or "",
                    "insight": r.insight or None,
                    "url": r.url or "", "source": r.source,
                    "published": str(r.published_at)[:10] if r.published_at else "",
                    "dept": m.get("부처", ""), "kind": m.get("종류", ""),
                    "revision": m.get("제개정", ""), "effective": m.get("시행일자", "")})
    return {"items": out, "total": total, "sources": srcs}


@router.get("/monthly")
def monthly(month: str = Query("")) -> dict[str, Any]:
    """월간 리뷰. digests에 week='YYYY-MM' 키로 저장돼 있다.

    ★ 고를 수 있는 달은 **리뷰가 실제로 만들어진 달**만 낸다.
      코퍼스에는 2019년치 GeekNews 백필까지 있어서 기사 기준으로 뽑으면
      84개월이 나오는데, 그중 대부분은 리뷰가 없어 골라도 빈 화면이 된다.
    """
    from src.insight import _week_month
    with get_engine().connect() as c:
        # 'YYYY-MM' 형태(7자)만 월간이다. 주차 키는 'YYYY-Www'로 8자다.
        months = sorted(
            (w for (w,) in c.execute(select(digests.c.week))
             if w and len(w) == 7 and w[4] == "-"), reverse=True)
        if not month:
            month = months[0] if months else ""
        row = c.execute(select(digests.c.lead, digests.c.generated_at)
                        .where(digests.c.week == month)).first() if month else None
        weeks = [w for (w,) in c.execute(select(items.c.published_week).distinct()
                                         .where(items.c.kept.is_(True))) if w]
        mine = [w for w in weeks if _week_month(w) == month]
        n = c.execute(select(func.count()).select_from(items).where(
            items.c.kept.is_(True),
            items.c.published_week.in_(mine or ["_"]))).scalar_one() if month else 0

    def label(x: str) -> str:
        return f"{x[:4]}년 {int(x[5:])}월" if len(x) == 7 else x

    return {"month": month, "label": label(month) if month else "",
            "months": [{"month": x, "label": label(x)} for x in months],
            "lead": row[0] if row else None,
            "generated": str(row[1])[:16] if row and row[1] else "",
            "weeks": len(mine), "kept": n}


@router.get("/home")
def home() -> dict[str, Any]:
    """메인 화면이 쓰는 것들을 **한 번에** 낸다.

    원격 DB(Supabase)라 왕복 하나가 곧 지연이다. 화면을 열 때마다 5~6번 부르면
    체감이 확 나빠져서, 홈이 필요한 만큼만 모아 한 응답으로 돌려준다.
    """
    from src.digest import latest_week

    with get_engine().connect() as c:
        week = latest_week(c) or ""
        # 소스별 수집 현황 — 수집기가 조용히 멈춘 걸 알아채는 유일한 화면이다.
        #   스케줄러가 "성공"으로 보고하면서 실제로는 아무것도 안 받는 상황이
        #   가능하므로(잠금 버그가 실제로 그랬다) 여기서 눈에 보이게 둔다.
        labels = CFG.get("source_labels") or {}
        health = [
            {"source": s, "label": labels.get(s, s), "total": n,
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
        tr = trend(week="", top=10)
    except Exception:
        tr = {"rows": [], "week_label": ""}
    return {
        "week": week, "week_label": week_label(week) if week else "",
        "lead": d.get("lead"), "total_kept": d.get("total_kept", 0),
        "crossing": sec.get("crossing", [])[:5],
        "regulatory": _regulatory(6, 0, "")["items"],
        "trending": (tr.get("rows") or [])[:10],
        "health": health,
    }


@router.get("/newsletter")
def newsletter(week: str = Query("")) -> dict[str, Any]:
    """뉴스레터 상태와 **실제로 나갈 메일 그대로**의 미리보기.

    ★ 지금까지는 메일이 어떻게 생겼는지 확인하려면 CLI를 돌려야 했다.
      보내기 전에 눈으로 볼 수 없는 발송물은 언젠가 이상한 채로 나간다.
      digest.render_html(mail=True)를 그대로 부른다 — 미리보기용 코드를 따로
      두면 실제 메일과 갈라진다.

    ★ 수신자·키워드는 **읽기 전용**이다.
      이 화면에는 로그인이 없다. 같은 망에 있는 누구나 열 수 있는 화면에서
      수신자를 고칠 수 있으면 안 된다. 바꾸는 건 .env와 config.yaml에서 한다.
    """
    import os

    from src.digest import SERVICE_NAME, build, latest_week, render_html

    with get_engine().connect() as c:
        week = week or latest_week(c) or ""
    preview, subject = "", ""
    if week:
        d = build(week, CFG)
        preview = render_html(d, mail=True, web_url=os.getenv("WEB_BASE_URL") or None)
        # mailer가 만드는 제목과 같은 형식이어야 미리보기의 뜻이 있다
        subject = f"[{SERVICE_NAME}] {week_label(week)} — 교집합 {len(d['crossing'])}건"

    to = [x.strip() for x in (os.getenv("MAIL_TO") or "").replace(";", ",").split(",")
          if x.strip()]
    acfg = CFG.get("alerts") or {}
    return {
        "week": week, "week_label": week_label(week) if week else "",
        "subject": subject, "preview": preview,
        "smtp": {
            "configured": bool(os.getenv("SMTP_HOST")) and bool(to),
            "host": os.getenv("SMTP_HOST") or "",
            "port": os.getenv("SMTP_PORT") or "587",
            "from": os.getenv("MAIL_FROM") or os.getenv("SMTP_USER") or "",
            "to": to,
        },
        "alerts": {
            "enabled": bool(acfg.get("enabled", True)),
            "days": int(acfg.get("days", 7)),
            "keywords": [k for k in (acfg.get("keywords") or []) if str(k).strip()],
        },
        "schedule": {"digest": "매주 월요일 07:30", "alert": "매일 06:00"},
    }


@router.get("/cache")
def cache_info() -> dict[str, Any]:
    """캐시가 실제로 듣고 있는지 확인용. 안 맞으면 여기부터 본다."""
    return ego_cache.info()


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
        # 법령은 부처·종류·제개정·시행일이 판단에 필요하다. meta에 들어 있다.
        "meta": r.meta if isinstance(r.meta, dict) else {},
        "related": [{"id": x.id, "title": x.title, "url": x.url, "source": x.source,
                     "published": str(x.published_at)[:10] if x.published_at else "",
                     "cross_score": x.cross_score, "insight": x.insight or None,
                     "axes": sorted(rax.get(x.id, []))} for x in rel],
    }
