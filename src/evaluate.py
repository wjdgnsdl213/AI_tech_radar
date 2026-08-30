"""필터 품질 측정 — 2주차 완료 기준인 precision ≥ 85%를 재는 도구.

왜 사람이 라벨을 달아야 하나:
    통과율·유사도 분포는 "얼마나 걸렀나"만 알려준다. "제대로 걸렀나"는 사람이
    50건을 직접 보고 판정해야 나온다. 이게 없으면 임계값을 감으로 만지게 되고,
    다이제스트가 노이즈여도 숫자상으로는 멀쩡해 보인다.

두 단계로 쓴다:
    ① python -m src.evaluate --make-labels      라벨링용 CSV를 만든다
       → data/labels/labels.csv 를 열어 label 열에 1(관련)/0(무관)을 채운다
    ② python -m src.evaluate                    채운 CSV로 지표를 계산한다

표본 설계 — 통과분만 보면 안 된다:
    precision(통과분 중 맞은 비율)만 재면 "임계값을 극단적으로 높여 3건만 통과"
    시켜도 100%가 나온다. 놓친 것(recall)을 같이 봐야 하므로 통과·탈락을
    반반 뽑는다. 탈락분에 관련 항목이 많으면 임계값이 너무 높은 것이다.

라벨링 기준(팀 관점):
    1 = 우리 팀 업무(소상공인 도메인에 AI·빅데이터 적용)에 참고가 되는가
    0 = 아니다
    애매하면 0. "읽을 시간이 아깝지 않은가"가 실질 기준이다.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from sqlalchemy import func, select

from src.db import get_engine, item_axes, items, load_config

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

LABEL_PATH = Path("data/labels/labels.csv")
# AI가 만든 잠정 라벨. 사람이 채운 labels.csv와 절대 섞지 않는다 —
# 필터를 만든 쪽이 그 필터를 채점하면 수치가 낙관적으로 편향된다.
AI_LABEL_PATH = Path("data/labels/labels_ai.csv")
FIELDS = ["label", "id", "kept", "relevance", "cross_score", "axes", "source",
          "published", "title", "url"]


def make_labels(n: int, force: bool, seed_note: str = "") -> None:
    """통과·탈락 반반으로 무작위 표본을 뽑아 라벨링용 CSV를 만든다."""
    if LABEL_PATH.exists() and not force:
        sys.exit(f"이미 있습니다: {LABEL_PATH}\n"
                 "  덮어쓰려면 --force (채워둔 라벨이 날아갑니다)")

    engine = get_engine()
    half = n // 2
    with engine.connect() as conn:
        if conn.execute(select(func.count()).select_from(items)
                        .where(items.c.kept.isnot(None))).scalar_one() == 0:
            sys.exit("kept가 비어 있습니다. 먼저 python -m src.filter 를 실행하세요.")

        def pick(kept: bool, limit: int):
            return conn.execute(
                select(items.c.id, items.c.source, items.c.title, items.c.url,
                       items.c.relevance, items.c.cross_score, items.c.published_at,
                       items.c.kept)
                .where(items.c.kept.is_(kept))
                .order_by(func.random()).limit(limit)
            ).all()

        rows = list(pick(True, half)) + list(pick(False, n - half))
        axes_map: dict[int, list[str]] = {}
        for item_id, axis in conn.execute(select(item_axes.c.item_id, item_axes.c.axis)):
            axes_map.setdefault(item_id, []).append(axis)

    LABEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LABEL_PATH, "w", encoding="utf-8-sig", newline="") as f:
        # utf-8-sig: 엑셀이 BOM 없으면 한글을 깨뜨린다
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({
                "label": "", "id": r.id, "kept": int(bool(r.kept)),
                "relevance": f"{r.relevance:.4f}" if r.relevance is not None else "",
                "cross_score": r.cross_score, "source": r.source,
                "axes": "+".join(sorted(axes_map.get(r.id, []))),
                "published": str(r.published_at)[:10] if r.published_at else "",
                "title": r.title or "", "url": r.url or "",
            })

    kept_n = sum(1 for r in rows if r.kept)
    print(f"라벨링용 표본 {len(rows)}건 생성 → {LABEL_PATH}")
    print(f"  통과분 {kept_n}건 / 탈락분 {len(rows) - kept_n}건")
    print("\n다음:")
    print(f"  1. {LABEL_PATH} 를 엑셀로 연다")
    print("  2. label 열에 1(우리 팀에 관련) / 0(무관) 을 채운다. 애매하면 0")
    print("  3. python -m src.evaluate  로 지표를 본다")
    if seed_note:
        print(f"\n{seed_note}")


def refresh(path: Path) -> None:
    """라벨은 그대로 두고 모델 판정 열(kept/relevance/cross_score/axes)만 DB에서 다시 읽는다.

    임계값이나 시드를 바꿔 다시 돌리면 kept가 바뀌는데, 라벨 CSV에는 예전 판정이
    박혀 있다. 이걸 안 갱신하면 옛 필터를 채점하게 된다. 라벨링은 한 번만 하고
    필터는 여러 번 고치는 게 정상이므로 갱신 경로가 필요하다.
    """
    if not path.exists():
        sys.exit(f"라벨 파일이 없습니다: {path}")
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    ids = [int(r["id"]) for r in rows]
    engine = get_engine()
    with engine.connect() as conn:
        cur = {r.id: r for r in conn.execute(
            select(items.c.id, items.c.kept, items.c.relevance, items.c.cross_score)
            .where(items.c.id.in_(ids)))}
        axes_map: dict[int, list[str]] = {}
        for item_id, axis in conn.execute(select(item_axes.c.item_id, item_axes.c.axis)):
            axes_map.setdefault(item_id, []).append(axis)
    changed = 0
    for r in rows:
        row = cur.get(int(r["id"]))
        if not row:
            continue
        before = r["kept"]
        r["kept"] = str(int(bool(row.kept)))
        r["relevance"] = f"{row.relevance:.4f}" if row.relevance is not None else ""
        r["cross_score"] = row.cross_score
        r["axes"] = "+".join(sorted(axes_map.get(int(r["id"]), [])))
        changed += before != r["kept"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print(f"{path} 갱신 — 판정이 바뀐 항목 {changed}건 (라벨은 그대로)")


def evaluate(path: Path = LABEL_PATH) -> None:
    if not path.exists():
        sys.exit(f"라벨 파일이 없습니다: {path}\n"
                 "  먼저 python -m src.evaluate --make-labels 를 실행하세요.")
    if path == AI_LABEL_PATH:
        print("⚠️  AI 잠정 라벨 기준입니다. 필터를 만든 쪽이 그 필터를 채점한 것이라\n"
              "    실제보다 낙관적으로 나올 수 있습니다. 사람 라벨로 반드시 재측정하세요.\n")

    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    labeled = [r for r in rows if str(r.get("label", "")).strip() in ("0", "1")]
    if not labeled:
        sys.exit(f"label 열이 비어 있습니다. 1/0을 채운 뒤 다시 실행하세요 ({path})")

    # 혼동행렬 — kept(모델 판정) × label(사람 판정)
    tp = sum(1 for r in labeled if r["kept"] == "1" and r["label"] == "1")
    fp = sum(1 for r in labeled if r["kept"] == "1" and r["label"] == "0")
    fn = sum(1 for r in labeled if r["kept"] == "0" and r["label"] == "1")
    tn = sum(1 for r in labeled if r["kept"] == "0" and r["label"] == "0")

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    print(f"라벨 {len(labeled)}건 (전체 표본 {len(rows)}건 중)\n")
    print(f"{'=' * 52}\n혼동행렬\n{'=' * 52}")
    print(f"                 사람: 관련   사람: 무관")
    print(f"  모델: 통과      {tp:>6}       {fp:>6}")
    print(f"  모델: 탈락      {fn:>6}       {tn:>6}")

    print(f"\n{'=' * 52}\n지표\n{'=' * 52}")
    goal = " ✅ 목표 달성" if precision >= 0.85 else " ❌ 목표 0.85 미달"
    print(f"  precision  {precision:.3f}{goal}")
    print(f"    → 통과시킨 것 중 실제로 관련 있는 비율. 다이제스트 신뢰도")
    print(f"  recall     {recall:.3f}")
    print(f"    → 관련 있는 것 중 건진 비율. 낮으면 임계값이 너무 높다")
    print(f"  F1         {f1:.3f}")

    # ── 임계값을 옮기면 어떻게 되나 — relevance가 있으므로 재라벨링 없이 계산된다 ──
    scored = [r for r in labeled if r.get("relevance")]
    if scored:
        print(f"\n{'=' * 52}\n임계값 후보별 (라벨 {len(scored)}건 기준)\n{'=' * 52}")
        print(f"  {'임계값':<8}{'precision':>11}{'recall':>9}{'통과':>7}")
        # 대비 점수 눈금(-0.14~+0.14). 코사인 눈금이 아니다 — filter.py 참조
        for t in (0.00, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06):
            p_tp = sum(1 for r in scored if float(r["relevance"]) >= t and r["label"] == "1")
            p_fp = sum(1 for r in scored if float(r["relevance"]) >= t and r["label"] == "0")
            p_fn = sum(1 for r in scored if float(r["relevance"]) < t and r["label"] == "1")
            p = p_tp / (p_tp + p_fp) if p_tp + p_fp else 0.0
            rc = p_tp / (p_tp + p_fn) if p_tp + p_fn else 0.0
            print(f"  {t:<8.2f}{p:>11.3f}{rc:>9.3f}{p_tp + p_fp:>7}")
        print("\n  ⚠️ 표본이 통과·탈락 반반이라 여기 통과 수는 실제 비율이 아니다.")
        print("     precision·recall의 상대 비교용으로만 읽는다.")

    # ── 오류 사례 — 무엇을 고쳐야 하는지는 여기서 나온다 ──
    for kept, label, title in (("1", "0", "❌ 오통과 (precision을 깎는 것)"),
                               ("0", "1", "⚠️ 놓침 (recall을 깎는 것)")):
        bad = [r for r in labeled if r["kept"] == kept and r["label"] == label]
        if not bad:
            continue
        print(f"\n{'=' * 52}\n{title} — {len(bad)}건\n{'=' * 52}")
        for r in sorted(bad, key=lambda x: -float(x.get("relevance") or 0))[:10]:
            print(f"  {float(r.get('relevance') or 0):.3f} [{r['source']:<10}] "
                  f"{r['title'][:48]}")
            if r.get("axes"):
                print(f"         ↳ {r['axes']}")

    print(f"\n{'=' * 52}\n읽는 법\n{'=' * 52}")
    print("  precision 낮다  → 임계값을 올리거나 시드에 업무 맥락을 더 구체적으로")
    print("  recall 낮다     → 임계값을 내리거나 시드에 빠진 주제를 추가")
    print("  오통과가 특정 소스·축에 몰린다 → 그 축 키워드가 너무 넓다(config.axes)")


def main() -> None:
    parser = argparse.ArgumentParser(description="필터 품질 측정 (precision ≥ 85%)")
    parser.add_argument("--make-labels", action="store_true",
                        help="라벨링용 무작위 표본 CSV 생성")
    parser.add_argument("-n", type=int, default=50, help="표본 수 (기본 50)")
    parser.add_argument("--force", action="store_true", help="기존 labels.csv 덮어쓰기")
    parser.add_argument("--ai", action="store_true",
                        help="AI 잠정 라벨(labels_ai.csv)로 측정")
    parser.add_argument("--refresh", action="store_true",
                        help="라벨은 두고 모델 판정 열만 DB에서 다시 읽는다")
    args = parser.parse_args()

    load_config()   # config가 깨져 있으면 여기서 바로 알린다
    target = AI_LABEL_PATH if args.ai else LABEL_PATH
    if args.make_labels:
        make_labels(args.n, args.force)
        return
    if args.refresh:
        refresh(target)
    evaluate(target)


if __name__ == "__main__":
    main()
