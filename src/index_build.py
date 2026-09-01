"""압축 키워드 인덱스 — 로컬 keywords.db를 본 DB(Supabase)의 두 표로 요약한다.

★ 왜
  item_keywords 원본은 240만 행 · 310MB다. 저장소로 주고받기엔 크고, 배포하면
  컨테이너가 뜰 때마다 10분에 걸쳐 다시 만들어야 한다(볼륨이 없으면). 그동안
  급상승·기관·연관어 세 화면이 빈다.
  그런데 화면이 쓰는 건 원본이 아니라 **집계 결과**다. 두 개만 올리면 된다:
      kw_week      (키워드, 주차) 문서 수   → 급상승 · 기관     약 85만 행
      kw_neighbor  키워드별 상위 이웃       → 연관어           약 56만 행
  기사 목록은 items 제목·요약을 직접 훑는다(표가 필요 없다).

★ 연관어가 특히 크게 달라진다
  지금은 요청마다 공기(co-occurrence)를 실시간으로 센다 — '소상공인' 2홉이면
  질의 165회다(실측). 이웃을 미리 계산해 두면 **1~2회**로 끝난다.
  대신 이웃은 만든 시점에 고정된다. 매일 파이프라인이 다시 만들므로 하루 단위로는
  최신이다.

★ 무엇을 버리나 (정직하게)
  · df가 낮은 키워드는 이웃을 만들지 않는다. 아주 드문 말을 검색하면 망이 안 나온다.
    급상승 하한(min_weekly_freq=5)과 비슷한 기준이라 실사용 차이는 작다.
  · 이웃은 상위 N개까지만 둔다. 그 아래는 어차피 화면에 안 그려진다.
  탐색이 전체 코퍼스를 본다는 원칙은 그대로다 — 통과분으로 줄이는 게 아니라
  집계 방식만 바꾼다. Ollama·LangChain 같은 말은 그대로 나온다.

실행:
  python -m src.index_build              # 전체 다시 만들기
  python -m src.index_build --dry-run    # 크기만 확인
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import delete, func, select, text

from src.db import (get_engine, item_axes, items, kw_engine, kw_item,
                    kw_meta, kw_neighbor, kw_week, load_config)
from src.extract import STOPWORDS, is_org_keyword, item_keywords

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

MIN_DF = 10          # 이웃을 만들 키워드의 최소 문서 빈도
TOP_N = 20           # 키워드당 이웃 수
MIN_COOC = 2         # 이보다 적게 함께 나온 쌍은 버린다
MAX_ITEMS = 60       # 키워드당 담을 기사 수 상한.
#   화면은 40건까지만 보여준다. 200으로 뒀더니 이 표만 125MB였다 —
#   무료 한도(500MB)에서 25MB밖에 안 남아 items가 크는 걸 못 버틴다.
CHUNK = 5000         # 적재 배치


def build_week_counts() -> list[dict[str, Any]]:
    """(키워드, 주차) 문서 수. 급상승·기관이 쓴다.

    통과분(kept)만 센다 — 급상승은 다이제스트에 실리는 것이라 필터 기준이 같아야
    하고, 기관도 같은 지면을 본다.

    ★ kept는 **본 DB에서 읽는다.** 로컬 인덱스에도 kept 열이 있지만 그건 추출한
      시점의 값이라, 필터를 다시 돌리면 낡는다(실제로 판정 방식을 axes로 바꾸자
      3,779 → 50,956건이 됐는데 로컬 값은 옛것 그대로였다). 판정의 출처는
      하나여야 한다.
    """
    with get_engine().connect() as c:
        kept = {i for (i,) in c.execute(
            select(items.c.id).where(items.c.kept.is_(True)))}
    print(f"  통과 항목 {len(kept):,}건 (본 DB 기준)")
    agg: Counter = Counter()
    with kw_engine().connect() as c:
        for k, w, i in c.execute(
                select(item_keywords.c.keyword, item_keywords.c.week,
                       item_keywords.c.item_id)
                .where(item_keywords.c.week.isnot(None))):
            if i in kept:
                agg[(k, w)] += 1
    return [{"keyword": k, "week": w, "n": n} for (k, w), n in agg.items()]


def build_neighbors() -> tuple[list[dict[str, Any]], dict[str, int]]:
    """키워드별 상위 이웃(NPMI 순).

    한 번만 훑는다. 키워드마다 따로 세면 5만 번 질의라 몇 시간이 걸린다 —
    문서를 한 번 돌면서 그 안의 모든 쌍을 세는 쪽이 30초면 끝난다(실측).
    대신 쌍이 860만 개까지 늘어 메모리를 1GB 넘게 쓴다. 이 작업은 PC에서
    돌리는 것이므로 괜찮다(컨테이너는 만들어진 표를 읽기만 한다).
    """
    t0 = time.time()
    with kw_engine().connect() as c:
        df = {k: n for k, n in c.execute(
            select(item_keywords.c.keyword,
                   func.count(func.distinct(item_keywords.c.item_id)))
            .group_by(item_keywords.c.keyword))}
        n_docs = c.execute(select(func.count(func.distinct(item_keywords.c.item_id)))
                           ).scalar_one() or 1
    big = {k for k, n in df.items() if n >= MIN_DF}
    print(f"  df≥{MIN_DF} 키워드 {len(big):,}개 / 전체 {len(df):,}개  "
          f"({time.time() - t0:.0f}초)")

    docs: dict[int, list[str]] = defaultdict(list)
    with kw_engine().connect() as c:
        for i, k in c.execute(select(item_keywords.c.item_id, item_keywords.c.keyword)):
            if k in big:
                docs[i].append(k)
    print(f"  문서 {len(docs):,}개")

    pair: Counter = Counter()
    for ks in docs.values():
        u = sorted(set(ks))
        for a in range(len(u)):
            for b in range(a + 1, len(u)):
                pair[(u[a], u[b])] += 1
    del docs
    print(f"  쌍 {len(pair):,}개")

    def npmi(a: str, b: str, n: int) -> float:
        pa, pb, pab = df[a] / n_docs, df[b] / n_docs, n / n_docs
        d = -math.log(pab)
        return math.log(pab / (pa * pb)) / d if d > 0 else 0.0

    ranked: dict[str, list[tuple[float, str, int]]] = defaultdict(list)
    for (a, b), n in pair.items():
        if n < MIN_COOC:
            continue
        # ★ 같은 말의 n-gram 변종은 이웃이 아니다.
        #   'AI모델'의 상위 20개가 자체AI모델·최신AI모델·국산AI모델·오픈AI모델로
        #   전부 채워졌다(실측). 서로를 포함하는 쌍을 여기서 버려야 20칸이
        #   **다른 말**로 채워진다. 화면에서 걸러도 이미 늦다 — 그때는 20칸이
        #   변종으로 차 있어서 지울수록 노드가 사라진다(1홉 8개 → 3개였다).
        if a in b or b in a:
            continue
        if a in STOPWORDS or b in STOPWORDS:
            continue
        v = npmi(a, b, n)
        if v < 0.15:            # 화면에서도 이 아래는 선을 긋지 않는다
            continue
        ranked[a].append((v, b, n))
        ranked[b].append((v, a, n))
    del pair

    out: list[dict[str, Any]] = []
    for k, lst in ranked.items():
        lst.sort(reverse=True)
        for v, nb, n in lst[:TOP_N]:
            out.append({"keyword": k, "neighbor": nb, "npmi": round(v, 4),
                        "cooc": n, "df": df.get(nb, 0)})
    print(f"  이웃 {len(out):,}행 (키워드 {len(ranked):,}개 × 최대 {TOP_N})  "
          f"({time.time() - t0:.0f}초)")
    return out, df


def build_meta(df: dict[str, int]) -> list[dict[str, Any]]:
    """키워드별 문서 빈도와 주제 축.

    ★ 축을 미리 정해 두는 이유
      화면에서 실시간으로 세려면 키워드마다 기사 제목·요약을 훑어야 하는데
      하나에 7.2초가 걸린다(실측). 노드 30개면 3분이다.
      여기서는 로컬 인덱스(키워드→문서)와 본 DB의 축(문서→축)을 파이썬에서
      맞붙인다 — 두 번의 전수 조회로 끝난다.
    """
    with get_engine().connect() as c:
        doc_axis: dict[int, list[str]] = defaultdict(list)
        for i, a in c.execute(select(item_axes.c.item_id, item_axes.c.axis)):
            doc_axis[i].append(a)
    big = {k for k, n in df.items() if n >= MIN_DF}
    hits: dict[str, Counter] = defaultdict(Counter)
    with kw_engine().connect() as c:
        for i, k in c.execute(select(item_keywords.c.item_id, item_keywords.c.keyword)):
            if k in big:
                for a in doc_axis.get(i, ()):
                    hits[k][a] += 1
    out = []
    for k in big:
        h = hits.get(k)
        out.append({"keyword": k, "df": df[k],
                    "axis": (max(h, key=h.get) if h else None)})
    print(f"  키워드 성질 {len(out):,}행")
    return out


def build_items(df: dict[str, int]) -> list[dict[str, Any]]:
    """키워드가 나온 기사. 노드를 눌렀을 때 목록을 여는 데 쓴다.

    최근 것부터 상한만큼만 담는다 — '소상공인'은 1만 5천 건인데 화면은 40건만
    보여준다. 상한이 없으면 이 표가 원본만큼 커져서 옮기는 의미가 없다.

    ★ 기관 키워드만 예외다 — 상한도 MIN_DF도 걸지 않는다.
      기관 화면은 8주를 **주차별로 쪼개서** 보여주고, 각 칸을 누르면 그 주 기사가
      나와야 한다. 최근 60건만 담으면 오래된 주가 통째로 비어 "표에는 33건인데
      목록은 0건"이 된다(실측: 중소벤처기업부는 8주에 122건).
      MIN_DF(10)도 못 건다 — 표는 8주 합계 3건부터 띄우기 때문이다.
      비용은 없다시피 하다: 기관 키워드는 2,025개 17,357행으로 이 표의 2%다.
    """
    org = {k for k in df if is_org_keyword(k)}
    keep = {k for k, n in df.items() if n >= MIN_DF} | org
    per: dict[str, list[int]] = defaultdict(list)
    with kw_engine().connect() as c:
        # item_id가 클수록 최근이다(단조 증가). 내림차순으로 읽어 앞에서 자른다.
        for k, i in c.execute(
                select(item_keywords.c.keyword, item_keywords.c.item_id)
                .order_by(item_keywords.c.item_id.desc())):
            if k not in keep:
                continue
            if k in org or len(per[k]) < MAX_ITEMS:
                per[k].append(i)
    out = [{"keyword": k, "item_id": i} for k, ids in per.items() for i in ids]
    n_org = sum(len(per[k]) for k in org if k in per)
    print(f"  키워드→기사 {len(out):,}행  (그중 기관 {n_org:,}행, 상한 없음)")
    return out


def load(table, rows: list[dict[str, Any]], label: str) -> None:
    """통째로 갈아끼운다. 부분 갱신이 아니라 재생성이라 지우고 넣는 게 단순하다.

    ★ DELETE가 아니라 TRUNCATE다.
      Postgres의 DELETE는 죽은 행을 남긴다(MVCC). 매일 다시 만들면 표가 계속
      부풀어서, 한 번 돌렸더니 kw_item이 125MB → 136MB가 되고 무료 한도(500MB)를
      넘겼다. TRUNCATE는 바로 회수한다.
      (이미 부푼 건 VACUUM FULL로 되돌린다 — 353MB까지 줄었다.)
    """
    engine = get_engine()
    with engine.begin() as c:
        c.execute(text(f"TRUNCATE TABLE {table.name}")
                  if c.dialect.name == "postgresql" else delete(table))
    for i in range(0, len(rows), CHUNK):
        with engine.begin() as c:
            c.execute(table.insert(), rows[i:i + CHUNK])
        if (i // CHUNK) % 20 == 0 and i:
            print(f"    {i:,}/{len(rows):,}")
    print(f"  {label} {len(rows):,}행 적재")


def main() -> int:
    ap = argparse.ArgumentParser(description="압축 키워드 인덱스 생성")
    ap.add_argument("--dry-run", action="store_true", help="적재하지 않고 크기만")
    args = ap.parse_args()
    load_config()

    print("■ (키워드, 주차) 집계")
    weeks = build_week_counts()
    print(f"  {len(weeks):,}행")

    print("\n■ 이웃")
    nbrs, df = build_neighbors()

    print("" + chr(10) + "■ 키워드 성질 (크기·색)")
    meta = build_meta(df)

    print("" + chr(10) + "■ 키워드→기사")
    kitems = build_items(df)

    if args.dry_run:
        print(f"  적재하지 않았습니다 — kw_week {len(weeks):,}행 · "
              f"kw_neighbor {len(nbrs):,}행 · kw_meta {len(meta):,}행 · "
              f"kw_item {len(kitems):,}행")
        return 0

    print("\n■ 적재")
    t0 = time.time()
    load(kw_week, weeks, "kw_week")
    load(kw_meta, meta, "kw_meta")
    load(kw_item, kitems, "kw_item")
    load(kw_neighbor, nbrs, "kw_neighbor")
    print(f"  ({time.time() - t0:.0f}초)")
    print("\n  다음: 웹을 다시 띄우면 급상승·기관·연관어가 이 표를 씁니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
