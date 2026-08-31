"""교차 점수 — 이 프로젝트의 정렬 1순위 기준.

핵심 원칙(CLAUDE.md): **축을 많이 걸칠수록 높게 띄운다.**
팀의 실제 업무는 AI·빅데이터·소상공인 세 축의 교집합에 있다. 팀이 필터링에 쓰는
시간을 없애는 게 이 도구의 존재 이유이고, 그 답이 이 점수다.

    0축 "Emacs 31.1 새 기능"                 →  0      버림
    1축 "Qwen 신규 MoE 모델 공개"             →  0.5    참고
    2축 "가명정보 결합 고시 개정"              →  7.2    중요
    3축 "중기부, 소상공인 상권분석에 AI 도입"   → 27.2   최상단

계산식:
    cross_score = axis_count_weight[걸친 축 수] × Σ axis_weight[각 축]

    축 수 가중치가 곱셈으로 들어가는 게 요점이다. 1축 0.5 / 2축 3.0 / 3축 8.0이라
    "1축짜리 여러 건"이 "2축 한 건"을 이길 수 없다. 덧셈이면 그게 안 된다.
    값은 config.yaml의 score 섹션에서 조정한다(시드 진단 근거는 거기 주석 참조).

축 태깅은 prefilter.py가 이미 item_axes에 넣어뒀다. 여기서 다시 매칭하지 않는다 —
두 곳에서 따로 계산하면 결과가 갈릴 수 있다.

실행:
  python -m src.score                # 전체 재계산 (멱등)
  python -m src.score --dry-run      # DB를 건드리지 않고 분포만
  python -m src.score --top 20       # 상위 항목 확인
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter, defaultdict

from sqlalchemy import bindparam, func, select

from src.db import get_engine, item_axes, items, load_config

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)


def compute(axes: list[str], count_weight: dict[int, float],
            axis_weight: dict[str, float], min_axes: int) -> float:
    """축 목록 → 교차 점수. min_axes 미만이면 0(다이제스트에 안 올라간다)."""
    n = len(axes)
    if n < min_axes:
        return 0.0
    # config에 없는 축 수(4축 이상)는 최대 가중치로 처리한다 — 축이 늘어나도 안 터지게
    cw = count_weight.get(n) or (max(count_weight.values()) if count_weight else 1.0)
    return round(cw * sum(axis_weight.get(a, 1.0) for a in axes), 4)


def main() -> None:
    parser = argparse.ArgumentParser(description="교차 점수 계산")
    parser.add_argument("--dry-run", action="store_true", help="DB를 쓰지 않고 분포만 출력")
    parser.add_argument("--top", type=int, default=15, help="상위 항목 출력 건수")
    parser.add_argument("--batch", type=int, default=5000, help="DB 반영 배치 크기")
    args = parser.parse_args()

    cfg = load_config()
    scfg = cfg.get("score", {})
    # YAML은 {1: 0.5} 를 정수 키로 읽지만, 따옴표로 적히면 문자열이 된다 — 양쪽 다 받는다
    count_weight = {int(k): float(v) for k, v in (scfg.get("axis_count_weight") or {}).items()}
    axis_weight = {k: float(v) for k, v in (scfg.get("axis_weight") or {}).items()}
    min_axes = int(scfg.get("min_axes", 1))
    labels = {ax: spec.get("label", ax) for ax, spec in cfg["axes"].items()}

    print(f"축 수 가중치 {count_weight}")
    print(f"축별 가중치   {axis_weight}")
    print(f"min_axes {min_axes} (미만이면 0점)\n")

    engine = get_engine()
    with engine.connect() as conn:
        all_ids = [r[0] for r in conn.execute(select(items.c.id))]
        pairs = conn.execute(select(item_axes.c.item_id, item_axes.c.axis)).all()
    if not all_ids:
        sys.exit("항목이 없습니다. 먼저 python -m src.collect 를 실행하세요.")

    axes_by_item: dict[int, list[str]] = defaultdict(list)
    for item_id, axis in pairs:
        axes_by_item[item_id].append(axis)
    if not axes_by_item:
        sys.exit("item_axes가 비어 있습니다. 먼저 python -m src.prefilter 를 실행하세요.")

    print(f"항목 {len(all_ids):,}건  |  축 태그 {len(pairs):,}개 → 점수 계산 중…")
    t0 = time.time()

    # 태그가 없는 항목도 0점으로 명시한다 — 키워드를 고쳐 다시 돌렸을 때
    # 예전 점수가 남아 있으면 다이제스트에 유령 항목이 올라온다
    scores: dict[int, float] = {}
    combo = Counter()
    for item_id in all_ids:
        axes = sorted(axes_by_item.get(item_id, []))
        scores[item_id] = compute(axes, count_weight, axis_weight, min_axes)
        combo["+".join(labels.get(a, a) for a in axes) or "(없음)"] += 1

    print(f"  ({time.time() - t0:.1f}초)\n")

    # ── ① 점수 구간 분포 ──
    dist = Counter(scores.values())
    print(f"{'=' * 62}\n① 점수 분포\n{'=' * 62}")
    for sc in sorted(dist, reverse=True):
        n = dist[sc]
        bar = "█" * max(0, int(n / len(all_ids) * 40))
        print(f"  {sc:>7.2f}점  {n:>7,}건 ({n / len(all_ids) * 100:5.1f}%) {bar}")

    # ── ② 축 조합별 — 어떤 교집합이 실제로 잡히는지 ──
    print(f"\n{'=' * 62}\n② 축 조합별 항목 수\n{'=' * 62}")
    for name, n in combo.most_common():
        star = "  ★" if "+" in name else ""
        print(f"  {name:<28}{n:>8,}건{star}")

    # ── ③ 상위 항목 — 다이제스트 최상단에 올라갈 것들 ──
    with engine.connect() as conn:
        rows = conn.execute(
            select(items.c.id, items.c.source, items.c.title, items.c.published_at)
        ).all()
    meta = {r.id: r for r in rows}
    top = sorted(scores.items(), key=lambda kv: -kv[1])[: args.top]
    print(f"\n{'=' * 62}\n③ 교차 점수 상위 {args.top}건\n{'=' * 62}")
    for item_id, sc in top:
        r = meta.get(item_id)
        if not r:
            continue
        axes = "+".join(labels.get(a, a) for a in sorted(axes_by_item.get(item_id, [])))
        when = str(r.published_at)[:10] if r.published_at else "-"
        print(f"  {sc:>6.2f} [{r.source:<10}] {when}  {(r.title or '')[:50]}")
        print(f"         ↳ {axes}")

    if args.dry_run:
        print("\n⏭  DB에 반영하지 않았습니다 (--dry-run)")
        return

    # ── 적재 ──
    print(f"\ncross_score 적재 중…")
    stmt = (items.update()
            .where(items.c.id == bindparam("b_id"))
            .values(cross_score=bindparam("b_score")))
    payload = [{"b_id": i, "b_score": s} for i, s in scores.items()]
    with engine.begin() as conn:
        for i in range(0, len(payload), args.batch):
            conn.execute(stmt, payload[i:i + args.batch])

    with engine.connect() as conn:
        done = conn.execute(
            select(func.count()).select_from(items).where(items.c.cross_score.isnot(None))
        ).scalar_one()
        crossing = conn.execute(
            select(func.count()).select_from(items).where(items.c.cross_score >= 3.0)
        ).scalar_one()
    print(f"  {done:,}건 갱신 완료 (2축 이상 {crossing:,}건)")
    print("\n  다음: python -m src.filter  (임베딩 필터 — torch 설치 필요)")


if __name__ == "__main__":
    main()
