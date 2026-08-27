"""시드 품질 진단 — 시드를 고치기 전에 "지금 시드가 뭘 잡고 뭘 놓치는지" 보여준다.

시드 문장은 필터 품질의 최대 입력인데, 눈으로만 보면 잘 썼는지 알 수가 없다.
그래서 이미 쌓인 DB 항목에 시드 centroid를 실제로 적용해보고,
  · 유사도 분포 (임계값을 어디에 둘지)
  · 상위 통과 항목 (시드가 무엇을 끌어당기는지)
  · 경계선 항목 (임계값 근처 — 여기가 판단이 갈리는 지점)
  · 소스별 통과율 (한 소스만 독식하면 시드가 편향된 것)
을 보여준다. 시드 → 진단 → 수정을 반복하는 게 이 파일의 용도다.

실행:
  python -m src.seed_check                      # 전체 진단
  python -m src.seed_check --sample 5000        # 표본만 (빠르게)
  python -m src.seed_check --threshold 0.45
  python -m src.seed_check --query "상권분석"    # 특정 키워드 항목이 몇 점인지
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from sqlalchemy import func, select

from src.db import get_engine, items, load_config

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)


def load_seeds(path: str) -> list[str]:
    """시드 파일을 읽는다. '#' 주석과 빈 줄은 무시."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [s.strip() for s in lines if s.strip() and not s.strip().startswith("#")]


def embed(model, texts: list[str], batch_size: int) -> np.ndarray:
    """정규화된 임베딩 — 정규화하면 내적이 곧 코사인 유사도라 계산이 단순해진다."""
    return model.encode(
        texts, batch_size=batch_size, convert_to_numpy=True,
        normalize_embeddings=True, show_progress_bar=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="시드 품질 진단")
    parser.add_argument("--sample", type=int, default=0, help="표본 수 (0=전체)")
    parser.add_argument("--threshold", type=float, default=None, help="임계값 (기본: config)")
    parser.add_argument("--seeds", default=None, help="시드 파일 경로")
    parser.add_argument("--query", default=None, help="제목에 이 문자열이 든 항목만 점수 확인")
    parser.add_argument("--top", type=int, default=15, help="표시할 상위 건수")
    args = parser.parse_args()

    cfg = load_config()
    fcfg = cfg["filter"]
    seed_path = args.seeds or fcfg["seed_path"]
    threshold = args.threshold if args.threshold is not None else float(fcfg["threshold"])

    seeds = load_seeds(seed_path)
    if not seeds:
        sys.exit(f"시드가 비어 있습니다: {seed_path}")
    print(f"시드 {len(seeds)}문장  ({seed_path})")
    print(f"모델 {fcfg['model']}  |  임계값 {threshold}\n")

    # ── 항목 로드 ──
    engine = get_engine()
    stmt = select(items.c.id, items.c.source, items.c.title, items.c.summary)
    if args.query:
        stmt = stmt.where(items.c.title.contains(args.query))
    if args.sample:
        # 무작위 표본 — 특정 시기·소스에 쏠리지 않게
        stmt = stmt.order_by(func.random()).limit(args.sample)
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
    if not rows:
        sys.exit("대상 항목이 없습니다.")
    print(f"대상 항목 {len(rows):,}건\n")

    # 제목+요약을 한 덩어리로 본다 (수집 원칙상 본문은 없다)
    docs = [f"{r.title or ''} {r.summary or ''}".strip() for r in rows]

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(fcfg["model"])
    batch = int(fcfg.get("batch_size", 64))

    print("시드 임베딩…")
    seed_vecs = embed(model, seeds, batch)
    centroid = seed_vecs.mean(axis=0)
    centroid /= np.linalg.norm(centroid)

    print("항목 임베딩…")
    doc_vecs = embed(model, docs, batch)
    sims = doc_vecs @ centroid       # 정규화했으므로 내적 = 코사인

    # ── ① 분포 ──
    print(f"\n{'=' * 62}\n① 유사도 분포\n{'=' * 62}")
    for q in [50, 75, 90, 95, 99]:
        print(f"  상위 {100 - q:>2}%  ≥ {np.percentile(sims, q):.3f}")
    print(f"  평균 {sims.mean():.3f}   최대 {sims.max():.3f}   최소 {sims.min():.3f}")

    print(f"\n  임계값별 통과량:")
    for t in [0.35, 0.40, 0.45, 0.50, 0.55, 0.60]:
        n = int((sims >= t).sum())
        mark = "  ← 현재" if abs(t - threshold) < 1e-9 else ""
        print(f"    {t:.2f}  {n:>7,}건  ({n / len(sims) * 100:5.1f}%){mark}")

    # ── ② 소스별 통과율 — 한 소스가 독식하면 시드가 편향된 것 ──
    print(f"\n{'=' * 62}\n② 소스별 통과율 (임계값 {threshold})\n{'=' * 62}")
    srcs = np.array([r.source for r in rows])
    for s in sorted(set(srcs)):
        m = srcs == s
        passed = int((sims[m] >= threshold).sum())
        print(f"  {s:<14} {passed:>7,} / {int(m.sum()):>7,}  ({passed / m.sum() * 100:5.1f}%)"
              f"   평균 {sims[m].mean():.3f}")

    # ── ③ 상위 — 시드가 무엇을 끌어당기는가 ──
    print(f"\n{'=' * 62}\n③ 상위 {args.top}건 — 시드가 끌어당기는 것\n{'=' * 62}")
    for i in np.argsort(-sims)[: args.top]:
        print(f"  {sims[i]:.3f} [{rows[i].source:<10}] {(rows[i].title or '')[:62]}")

    # ── ④ 경계선 — 판단이 갈리는 지점. 임계값 조정의 근거 ──
    print(f"\n{'=' * 62}\n④ 경계선 (임계값 ±0.02) — 여기가 애매한 구간\n{'=' * 62}")
    edge = np.where(np.abs(sims - threshold) <= 0.02)[0]
    for i in edge[: args.top]:
        print(f"  {sims[i]:.3f} [{rows[i].source:<10}] {(rows[i].title or '')[:62]}")
    if len(edge) == 0:
        print("  (해당 구간 없음)")

    print(f"\n{'=' * 62}")
    print("읽는 법:")
    print("  ③에 우리 업무와 무관한 게 많다  → 시드가 너무 일반적. 업무 맥락을 더 구체적으로")
    print("  ②에서 한 소스만 통과율이 높다   → 시드가 그 소스 문체에 편향. 다른 축 문장 보강")
    print("  ④가 대부분 '통과시키고 싶다'    → 임계값을 낮춘다")
    print("  전체 통과율이 5% 미만/50% 초과  → 시드 재작성 또는 임계값 재조정")


if __name__ == "__main__":
    main()
