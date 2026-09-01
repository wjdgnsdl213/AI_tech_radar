"""급상승 키워드 (F8) — "이번 주에 갑자기 많이 나온 말"을 찾는다.

파이프라인에서의 위치:
    … → filter → score → extract → **trend** → digest(📈 섹션)

급상승 점수 = 이번 주 비중 / (직전 N주 평균 비중 + eps)
    비중 = 그 주 통과 항목 중 이 키워드가 나온 문서의 비율

★ 빈도가 아니라 비중인 이유 (실측으로 드러났다)
    처음엔 원시 빈도로 비교했는데, 2026-W35에 네이버 수집을 켜면서 통과 항목이
    직전 주들의 20배가 되자 **상위 15개가 전부 25~50배 급상승**으로 찍혔다.
    수집량이 늘면 모든 키워드가 같이 늘어난다 — 그건 트렌드가 아니다.
    비중으로 보면 수집량 변화가 분자·분모에서 상쇄된다.

★ eps("문서 하나만큼의 비중")를 더하는 이유
    직전 비중이 0인 신규 키워드에서 0으로 나누는 걸 막고, 1건짜리가 무한대 점수로
    최상단을 먹는 것도 막는다. min_weekly_freq가 하한을 한 번 더 자른다.
    이 둘이 없으면 급상승 목록이 오탈자와 인명으로 채워진다.

★ n-gram 조각 제거
    '인공지능'과 함께 '인공'·'지능'이, '마이데이터'와 함께 '마이'가 같이 잡힌다.
    조각은 원래 키워드와 빈도가 거의 같으므로 그걸로 판별해 뺀다(_fragment_of).

'상승률'만 보면 안 되므로 출력에 이번 주 건수와 비중을 항상 같이 적는다.

실행:
  python -m src.trend                    # 최근 주차
  python -m src.trend --week 2026-W35
  python -m src.trend --weeks 4          # 비교할 직전 주 수
  python -m src.trend --out reports/     # CSV로 저장 (빅데이터팀 재활용용, PLAN §3-B)
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from src.db import get_engine, init_db, items, kw_week, load_config

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)


def prev_weeks(week: str, n: int) -> list[str]:
    """ISO 주차 문자열에서 직전 n개 주차를 만든다.

    'YYYY-Www'를 문자열로 빼면 연말·연초에서 틀린다(2026-W01의 직전은 2025-W52 또는
    W53이고, 어느 쪽인지는 그 해에 달렸다). 실제 날짜로 7일씩 빼서 다시 주차를 구한다.
    """
    from datetime import date, timedelta
    y, _, w = week.partition("-W")
    try:
        d = date.fromisocalendar(int(y), int(w), 1)
    except ValueError:
        return []
    out = []
    for i in range(1, n + 1):
        p = d - timedelta(weeks=i)
        iy, iw, _ = p.isocalendar()
        out.append(f"{iy}-W{iw:02d}")
    return out


def _fragment_of(kw: str, counts: dict[str, int], ratio: float = 0.9) -> bool:
    """이 키워드가 사실상 더 긴 키워드의 조각인가.

    n-gram으로 뽑으니 '인공지능'과 함께 '인공'·'지능'이, '마이데이터'와 함께 '마이'가
    같이 나온다. 조각은 원래 키워드와 빈도가 거의 같으므로 그걸로 판별한다:
    자기를 포함하는 더 긴 키워드가 있고 그쪽 빈도가 자기의 ratio 이상이면 조각이다.

    '데이터'처럼 여러 복합어에 두루 쓰이는 말은 자기 빈도가 훨씬 커서 안 걸린다.
    """
    n = counts[kw]
    for other, m in counts.items():
        if other != kw and kw in other and m >= n * ratio:
            return True
    return False


def rising(week: str, back: int, min_freq: int, engine=None) -> list[dict[str, Any]]:
    """급상승 키워드 목록. 점수 내림차순.

    ★ 빈도가 아니라 **비중**(그 주 통과 항목 중 몇 %에 나왔나)으로 비교한다.
      원시 빈도로 비교하면 그 주에 수집량이 늘었을 때 모든 키워드가 동시에
      '급상승'한다. 실측(2026-W35): 네이버 수집을 처음 켠 주라 통과 항목이
      직전 주들의 20배가 됐고, 상위 15개가 전부 25~50배 급상승으로 찍혔다.
      비중으로 보면 수집량 변화가 분자·분모에서 상쇄된다.
    """
    engine = engine or get_engine()
    past = prev_weeks(week, back)

    def freq(weeks: list[str]) -> Counter:
        """(키워드, 주차) 집계 표에서 읽는다.

        ★ 원본 item_keywords(240만 행)가 아니라 kw_week(6만 7천 행)를 본다.
          원본은 로컬 파일이라 배포하면 컨테이너가 매번 다시 만들어야 했다.
          집계는 본 DB에 있어서 어디서 실행하든 바로 쓸 수 있다.
          집계는 통과분(kept)만 세어 만든다 — 급상승은 다이제스트에 실리는
          것이므로 필터 기준이 같아야 한다. 원본 조건과 동일하다.
        """
        if not weeks:
            return Counter()
        with engine.connect() as conn:
            rows = conn.execute(
                select(kw_week.c.keyword, func.sum(kw_week.c.n))
                .where(kw_week.c.week.in_(weeks))
                .group_by(kw_week.c.keyword)).all()
        return Counter({k: int(n) for k, n in rows})

    def item_count(weeks: list[str]) -> int:
        if not weeks:
            return 0
        with engine.connect() as conn:
            return conn.execute(
                select(func.count()).select_from(items)
                .where(items.c.kept.is_(True), items.c.published_week.in_(weeks))
            ).scalar_one()

    now, before = freq([week]), freq(past)
    n_now = max(item_count([week]), 1)
    n_before = max(item_count(past), 1)
    # 부드럽게 하는 항: "그 주에 문서 하나에 나온 정도"의 비중.
    # 신규 키워드에서 0으로 나누는 걸 막고, 1건짜리가 최상단을 먹는 것도 막는다.
    eps = 1.0 / n_now

    cand = {kw: n for kw, n in now.items() if n >= min_freq}
    out = []
    for kw, n in cand.items():
        if _fragment_of(kw, cand):
            continue
        share_now = n / n_now
        share_before = before.get(kw, 0) / n_before
        out.append({
            "keyword": kw,
            "week": week,
            "count": n,
            "share": round(share_now * 100, 2),          # %
            "prev_share": round(share_before * 100, 2),
            "score": round(share_now / (share_before + eps), 2),
            "is_new": before.get(kw, 0) == 0,
        })
    out.sort(key=lambda r: (-r["score"], -r["count"]))
    return out


def series(keyword: str, weeks: list[str], engine=None) -> list[tuple[str, int]]:
    """키워드 하나의 주차별 시계열 (PLAN §3-B의 '키워드 × 주차 × 언급량')."""
    with (engine or get_engine()).connect() as conn:
        rows = dict(conn.execute(
            select(kw_week.c.week, kw_week.c.n)
            .where(kw_week.c.keyword == keyword, kw_week.c.week.in_(weeks))).all())
    return [(w, int(rows.get(w, 0))) for w in weeks]


def main() -> None:
    cfg = load_config()
    tcfg = cfg.get("trend", {})
    parser = argparse.ArgumentParser(description="급상승 키워드 (F8)")
    parser.add_argument("--week", default=None, help="기준 주차 (예: 2026-W35)")
    parser.add_argument("--weeks", type=int, default=int(tcfg.get("compare_weeks", 4)),
                        help="비교할 직전 주 수")
    parser.add_argument("--min-freq", type=int, default=int(tcfg.get("min_weekly_freq", 5)),
                        help="이번 주 최소 문서 빈도")
    parser.add_argument("--top", type=int, default=int(tcfg.get("top_n", 15)))
    parser.add_argument("--out", default=None, help="CSV 저장 디렉토리")
    args = parser.parse_args()

    engine = get_engine()
    init_db(engine)
    week = args.week
    with engine.connect() as conn:
        if not week:
            week = conn.execute(select(func.max(items.c.published_week))
                                .where(items.c.kept.is_(True))).scalar_one_or_none()
    with engine.connect() as conn:
        have = conn.execute(select(func.count()).select_from(kw_week)).scalar_one()
    if not week:
        sys.exit("통과 항목이 없습니다.")
    if not have:
        sys.exit("kw_week가 비어 있습니다. 먼저 python -m src.index_build 를 실행하세요.")

    past = prev_weeks(week, args.weeks)
    print(f"기준 {week}  |  비교 {past[-1]}~{past[0]} ({args.weeks}주)  "
          f"|  최소 빈도 {args.min_freq}")

    rows = rising(week, args.weeks, args.min_freq, engine)
    if not rows:
        print("\n조건을 넘는 키워드가 없습니다. --min-freq 를 낮춰보세요.")
        return

    print(f"\n{'=' * 62}\n📈 급상승 키워드 상위 {args.top}\n{'=' * 62}")
    print(f"  {'키워드':<20}{'이번주':>7}{'비중%':>8}{'직전%':>8}{'급상승':>8}")
    for r in rows[:args.top]:
        tag = "  ★신규" if r["is_new"] else ""
        print(f"  {r['keyword']:<20}{r['count']:>7}{r['share']:>8.2f}"
              f"{r['prev_share']:>8.2f}{r['score']:>8.2f}{tag}")

    # 상위 몇 개는 시계열도 보여준다 — 한 주만 튄 건지 추세인지가 여기서 갈린다
    show = [r["keyword"] for r in rows[:5]]
    all_weeks = list(reversed(past)) + [week]
    print(f"\n{'=' * 62}\n주차별 추이 (상위 5)\n{'=' * 62}")
    print(f"  {'키워드':<22}" + "".join(f"{w[-3:]:>7}" for w in all_weeks))
    for kw in show:
        cells = "".join(f"{n:>7}" for _, n in series(kw, all_weeks, engine))
        print(f"  {kw:<22}{cells}")

    if args.out:
        path = Path(args.out) / f"trend_{week}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\n저장 → {path}  ({len(rows):,}행)")

    print("\n  다음: python -m src.digest  (📈 섹션에 반영된다)")


if __name__ == "__main__":
    main()
