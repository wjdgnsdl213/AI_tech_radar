"""연관어 그래프 + 브릿지 노드 (F9).

★ 이 그래프의 읽는 법을 먼저 정한다 (PLAN §5의 경고)
    "예쁜데 뭘 봐야 할지 모르겠다"를 피하는 게 이 파일의 목표다.

       [AI 클러스터]            [소상공인 클러스터]
       합성데이터 ─┐            ┌─ 상권분석
       LLM       ─┤            ├─ 매출예측
       RAG       ─┘            └─ 정책효과
                  ↘ 가명정보 ↙        ← 브릿지 노드
                    (빅데이터)

    **두 클러스터를 잇는 노드 = 팀이 봐야 할 과제 후보.**
    교차 점수(F2)의 시각적 버전이라 장식이 아니라 기능이다.

★ 왜 NPMI인가 — 처음 시도가 실패했기 때문이다
    "세 축 문서에 고루 나오는 키워드"를 브릿지로 잡아봤더니 상위가 전부
    일반명사였다(정보 257건, 개인 151건, 보호 67건, 해결 54건…).
    어디에나 나오는 말이니 당연히 고루 걸린다.

    NPMI는 그 문제를 정면으로 푼다. 두 단어가 **우연히 같이 나올 확률보다
    얼마나 더 자주** 붙어 나오는지를 재기 때문에, 모든 문서에 나오는 말은
    누구와도 NPMI가 0 근처가 된다. 빈도가 아니라 결합의 특이성을 본다.

        NPMI(a,b) = log(p(a,b) / (p(a)·p(b))) / -log(p(a,b))
        범위 -1 ~ +1.  0 = 독립,  +1 = 항상 같이 나옴

★ 브릿지 점수
    노드가 가진 NPMI 간선 중 **다른 축 노드로 향하는 비중**이다.
    자기 축 안에서만 이어진 노드는 0에 가깝고, 두 축을 실제로 잇는 노드가 높다.
    여기에 "양쪽 다 실제로 이어져 있어야 한다"는 조건(각 축 최소 간선 수)을 건다.

실행:
  python -m src.graph                    # 브릿지 상위 출력
  python -m src.graph --out reports/     # 노드·간선 CSV + JSON
  python -m src.graph --min-df 20 --npmi 0.3
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

from sqlalchemy import select

from src.db import get_engine, init_db, item_axes, items, kw_engine, load_config
from src.extract import STOPWORDS, item_keywords

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)


def load_data(engine=None, kept_only: bool = True):
    """문서별 키워드 집합과 축 집합을 읽는다."""
    engine = engine or get_engine()
    # 키워드는 파생 인덱스 DB에 있다(src/db.kw_engine). kept를 그 테이블에 같이
    # 박아뒀으므로 본 DB와 조인하지 않고 여기서 바로 거른다.
    docs: dict[int, set[str]] = defaultdict(set)
    stmt = select(item_keywords.c.item_id, item_keywords.c.keyword)
    if kept_only:
        stmt = stmt.where(item_keywords.c.kept.is_(True))
    with kw_engine().connect() as kc:
        for i, k in kc.execute(stmt):
            docs[i].add(k)
    with engine.connect() as conn:
        axes: dict[int, set[str]] = defaultdict(set)
        for i, a in conn.execute(select(item_axes.c.item_id, item_axes.c.axis)):
            if i in docs:
                axes[i].add(a)
    return docs, axes


def drop_fragments(df: Counter, ratio: float = 0.9) -> set[str]:
    """더 긴 키워드의 조각인 것들을 골라낸다 (trend._fragment_of와 같은 규칙).

    '인공지능'이 있으면 '인공'은 그래프에 넣지 않는다 — 같은 개념이 두 노드로
    갈라지면 간선이 나뉘어 브릿지 점수가 희석된다.
    """
    keys = sorted(df, key=len, reverse=True)
    drop: set[str] = set()
    for i, short in enumerate(keys):
        n = df[short]
        for longer in keys[:i]:
            if len(longer) > len(short) and short in longer and df[longer] >= n * ratio:
                drop.add(short)
                break
    return drop


def build(docs: dict[int, set[str]], axes: dict[int, set[str]],
          min_df: int, min_cooc: int, npmi_cut: float,
          max_nodes: int) -> dict[str, Any]:
    """공기빈도 → NPMI 간선 → 축 성향 → 브릿지 점수."""
    n_docs = len(docs)
    df = Counter(k for ks in docs.values() for k in ks)

    # 후보 좁히기: 빈도 하한 + 불용어 + 조각 제거.
    # 이걸 안 하면 간선 수가 수백만이 되고 브릿지 상위가 일반명사로 덮인다.
    cand = {k for k, n in df.items() if n >= min_df and k not in STOPWORDS}
    cand -= drop_fragments(Counter({k: df[k] for k in cand}))
    cand = set(sorted(cand, key=lambda k: -df[k])[:max_nodes])

    cooc = Counter()
    for ks in docs.values():
        ks = sorted(ks & cand)
        if len(ks) > 1:
            cooc.update(combinations(ks, 2))

    # ── NPMI ──
    edges = []
    for (a, b), c in cooc.items():
        if c < min_cooc:
            continue
        pa, pb, pab = df[a] / n_docs, df[b] / n_docs, c / n_docs
        # pab가 1이면 log가 0이 되어 나눗셈이 터진다(모든 문서에 같이 나오는 경우)
        denom = -math.log(pab)
        if denom <= 0:
            continue
        npmi = math.log(pab / (pa * pb)) / denom
        if npmi >= npmi_cut:
            edges.append({"source": a, "target": b, "npmi": round(npmi, 4), "cooc": c})

    # ── 노드의 축 성향 ──
    axis_hits: dict[str, Counter] = defaultdict(Counter)
    for i, ks in docs.items():
        for a in axes.get(i, ()):
            for k in ks & cand:
                axis_hits[k][a] += 1

    nodes: dict[str, dict[str, Any]] = {}
    for k in cand:
        h = axis_hits[k]
        total = sum(h.values())
        share = {a: round(v / total, 3) for a, v in h.items()} if total else {}
        nodes[k] = {
            "keyword": k, "df": df[k],
            "axis": max(h, key=h.get) if h else None,     # 대표 축
            "axis_share": share,
            "degree": 0, "bridge": 0.0, "spans": [],
        }

    # ── 브릿지 점수 = 다른 축으로 향하는 간선 비중 ──
    out_w: dict[str, float] = defaultdict(float)
    all_w: dict[str, float] = defaultdict(float)
    partner_axes: dict[str, Counter] = defaultdict(Counter)
    for e in edges:
        a, b = e["source"], e["target"]
        w = e["npmi"]
        for x, y in ((a, b), (b, a)):
            nodes[x]["degree"] += 1
            all_w[x] += w
            ax_y = nodes[y]["axis"]
            if ax_y:
                partner_axes[x][ax_y] += 1
            if ax_y and ax_y != nodes[x]["axis"]:
                out_w[x] += w

    for k, nd in nodes.items():
        if all_w[k] > 0:
            nd["bridge"] = round(out_w[k] / all_w[k], 3)
        # 양쪽에 실제로 이어져 있어야 브릿지다. 한쪽으로만 튄 건 브릿지가 아니라
        # 그냥 그 축 키워드다 — 최소 2개 축에 각각 2개 이상 간선을 요구한다.
        nd["spans"] = sorted(a for a, c in partner_axes[k].items() if c >= 2)

    return {"nodes": nodes, "edges": edges, "n_docs": n_docs}


def bridges(g: dict[str, Any], top: int, min_degree: int = 8) -> list[dict[str, Any]]:
    """브릿지 후보. 연결이 적으면 비율이 흔들리므로 최소 차수를 요구한다.

    실측: 차수 4~6짜리는 간선 하나만 바뀌어도 브릿지 비율이 0.2씩 튄다.
    '연결 5개 중 4개가 다른 축'과 '연결 20개 중 16개가 다른 축'은 같은 0.8이지만
    후자만 실제로 두 클러스터를 잇는다. 그래서 차수 하한을 두고,
    같은 비율이면 연결이 많은 쪽을 위로 올린다.
    """
    out = [n for n in g["nodes"].values()
           if len(n["spans"]) >= 2 and n["degree"] >= min_degree]
    out.sort(key=lambda n: (-n["bridge"], -n["degree"]))
    return out[:top]


def main() -> None:
    cfg = load_config()
    gcfg = cfg.get("graph", {})
    p = argparse.ArgumentParser(description="연관어 그래프 + 브릿지 노드 (F9)")
    p.add_argument("--min-df", type=int, default=int(gcfg.get("min_df", 15)))
    p.add_argument("--min-cooc", type=int, default=int(gcfg.get("min_cooc", 5)))
    p.add_argument("--npmi", type=float, default=float(gcfg.get("npmi_cut", 0.25)))
    p.add_argument("--max-nodes", type=int, default=int(gcfg.get("max_nodes", 400)))
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--min-degree", type=int, default=int(gcfg.get("min_degree", 8)),
                   help="브릿지 후보의 최소 연결 수")
    p.add_argument("--out", default=None, help="CSV·JSON 저장 디렉토리")
    args = p.parse_args()

    engine = get_engine()
    init_db(engine)
    labels = {a: s.get("label", a) for a, s in cfg["axes"].items()}

    docs, axes = load_data(engine)
    if not docs:
        sys.exit("item_keywords가 비어 있습니다. python -m src.extract 를 먼저 실행하세요.")
    print(f"문서 {len(docs):,}건  |  df≥{args.min_df} · 공기≥{args.min_cooc} · NPMI≥{args.npmi}")

    g = build(docs, axes, args.min_df, args.min_cooc, args.npmi, args.max_nodes)
    print(f"노드 {len(g['nodes']):,}개  |  간선 {len(g['edges']):,}개\n")

    print(f"{'=' * 66}\n🕸️ 브릿지 노드 — 두 축을 잇는 키워드 = 과제 후보\n{'=' * 66}")
    print(f"  {'키워드':<18}{'문서':>5}{'연결':>5}{'브릿지':>7}  잇는 축")
    for n in bridges(g, args.top, args.min_degree):
        spans = "+".join(labels.get(a, a) for a in n["spans"])
        print(f"  {n['keyword']:<18}{n['df']:>5}{n['degree']:>5}{n['bridge']:>7.2f}  {spans}")

    print(f"\n{'=' * 66}\n축 안에 묶인 키워드 (대조군 — 브릿지가 아닌 것)\n{'=' * 66}")
    pure = [n for n in g["nodes"].values() if n["degree"] >= 3 and n["bridge"] < 0.25]
    pure.sort(key=lambda n: -n["degree"])
    for n in pure[:8]:
        print(f"  {n['keyword']:<18}{n['df']:>5}{n['degree']:>5}{n['bridge']:>7.2f}  "
              f"{labels.get(n['axis'], n['axis'])} 전속")

    if args.out:
        d = Path(args.out)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "graph_nodes.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["keyword", "df", "axis", "degree", "bridge", "spans"])
            for n in sorted(g["nodes"].values(), key=lambda x: -x["degree"]):
                w.writerow([n["keyword"], n["df"], n["axis"], n["degree"],
                            n["bridge"], "+".join(n["spans"])])
        with open(d / "graph_edges.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["source", "target", "npmi", "cooc"])
            for e in sorted(g["edges"], key=lambda x: -x["npmi"]):
                w.writerow([e["source"], e["target"], e["npmi"], e["cooc"]])
        (d / "graph.json").write_text(json.dumps(
            {"nodes": list(g["nodes"].values()), "edges": g["edges"]},
            ensure_ascii=False), encoding="utf-8")
        print(f"\n저장 → {d}/graph_nodes.csv · graph_edges.csv · graph.json")


if __name__ == "__main__":
    main()
