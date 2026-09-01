"""LLM 분류기를 필터로 쓸 수 있는지 라벨 50건으로 재본다 (실험용, 파이프라인 아님).

★ 왜 이 실험을 하나
  임베딩 필터는 값을 못 했다(precision 0.560 / recall 0.519). 사람이 가른 경계가
  주제가 아니라 **성격**이었기 때문이다 — 기관·정책·산업의 움직임인가,
  개발자 개인의 도구·의견인가. 그 경계는 문장으로 설명할 수 있어 보이고,
  설명할 수 있으면 LLM이 읽을 수 있다. 그게 이 실험의 가설이다.

★ 순환 논리를 피하려고 2겹 교차검증을 쓴다
  경계를 설명한 문장 자체가 이 50건을 보고 쓴 것이다. 같은 50건으로 채점하면
  당연히 잘 나온다. 그래서 few-shot 예시는 **반대쪽 겹에서만** 뽑고 채점은
  못 본 겹에서 한다. 두 방향 다 돌려 둘 다 보고한다.
  프롬프트의 서술 자체에 남는 누수는 없앨 수 없다 — 그래서 예시가 없는
  zero-shot도 같이 재서, 점수가 예시 덕인지 서술 덕인지 갈라 본다.

★ n=50이라 구간이 넓다. 0.60의 95% 신뢰구간이 0.46~0.74다.
  여기서 나오는 숫자는 "쓸 만한가"를 가르는 용도지 소수점을 비교할 물건이 아니다.
"""
from __future__ import annotations

import csv
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db import load_config                                    # noqa: E402
from src.insight import _text_of, build_client, load_team_profile  # noqa: E402

LABELS = Path("data/labels/labels.csv")

# 경계 서술. 시드와 달리 '무엇에 대한 글인가'가 아니라 '누구의 움직임인가'를 묻는다.
RULES = """너는 공공기관 **AI·빅데이터팀**의 주간 트렌드 지면 편집자다.
팀 업무는 (a) 데이터 분석·리포팅 (b) 인프라·플랫폼 운영 (c) 과제 기획·관리이고,
AI 모델을 직접 만들어 서비스하는 팀이 **아니다**.

기사 하나를 보고 지면에 올릴지 정한다. 기준은 주제어가 아니라 **성격**이다.

올린다(Y):
- 기관·기업·정부가 실제로 **한 일**: 도입·발주·협약·투자·조직 신설·시범사업
- 정책·법령·예산·지침의 변화, 공공데이터·플랫폼 운영의 변화
- 산업 판도의 변화: 시장 규모, 기업 간 경쟁 구도, 인프라 투자
- 소상공인·중소기업 대상 지원사업, 상권·결제·플랫폼 관련 움직임

올리지 않는다(N):
- 개발자 개인이 쓰는 도구·라이브러리·프레임워크 출시나 사용기
- 기술 튜토리얼, 벤치마크, 코드 이야기, "만들어 봤다"류 프로젝트 공개
- 논평·에세이·전망만 있고 일어난 일이 없는 글
- AI를 소재로만 쓴 연예·스포츠·부동산·주가 단신

한 글자로만 답한다: Y 또는 N. 설명하지 않는다."""


def load_rows() -> list[dict]:
    with LABELS.open(encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if r.get("label") in ("0", "1")]


def as_prompt(r: dict) -> str:
    return (f"출처: {r['source']}\n날짜: {r['published']}\n"
            f"축: {r['axes'] or '없음'}\n제목: {r['title']}")


def folds(rows: list[dict], seed: int = 7) -> tuple[list[dict], list[dict]]:
    """라벨 비율을 유지한 채 반으로 가른다(층화). 한쪽에 정답이 몰리면 못 잰다."""
    rng = random.Random(seed)
    a, b = [], []
    for label in ("1", "0"):
        grp = [r for r in rows if r["label"] == label]
        rng.shuffle(grp)
        a += grp[::2]
        b += grp[1::2]
    return a, b


def classify(client, model: str, system: list[dict], rows: list[dict],
             shots: list[dict]) -> list[str]:
    blanks: list[int] = []
    msgs_prefix: list[dict] = []
    for s in shots:
        msgs_prefix += [{"role": "user", "content": as_prompt(s)},
                        {"role": "assistant", "content": "Y" if s["label"] == "1" else "N"}]

    def one(r: dict) -> str:
        # ★ max_tokens를 넉넉히 준다. 적응형 사고를 쓰는 모델은 4토큰이면 전부
        #   temperature는 SDK 1.2.0에서 사라졌다 — 같은 설정에서도 실행마다
        #   precision이 0.74~0.80으로 흔들린다. 그래서 씨앗 여러 개의 다수결로 본다.
        #   사고에 쓰고 텍스트가 빈 채로 돌아와 전부 N으로 읽힌다(실측 함정).
        resp = client.messages.create(
            model=model, max_tokens=512, system=system,
            messages=msgs_prefix + [{"role": "user", "content": as_prompt(r)}])
        text = _text_of(resp)
        if not text:
            blanks.append(1)
        return "1" if text.upper().startswith("Y") else "0"

    with ThreadPoolExecutor(max_workers=8) as pool:
        out = list(pool.map(one, rows))
    if blanks:
        print(f"    ⚠ 빈 응답 {len(blanks)}건 — 사고가 출력을 다 먹었습니다. 점수를 믿지 마세요.")
    return out


def score(rows: list[dict], pred: list[str]) -> dict:
    tp = sum(1 for r, p in zip(rows, pred) if r["label"] == "1" and p == "1")
    fp = sum(1 for r, p in zip(rows, pred) if r["label"] == "0" and p == "1")
    fn = sum(1 for r, p in zip(rows, pred) if r["label"] == "1" and p == "0")
    tn = sum(1 for r, p in zip(rows, pred) if r["label"] == "0" and p == "0")
    return {"precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "acc": (tp + tn) / len(rows), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "n": len(rows)}


def line(tag: str, s: dict) -> str:
    return (f"  {tag:<22} precision {s['precision']:.3f}  recall {s['recall']:.3f}  "
            f"정확도 {s['acc']:.3f}   (n={s['n']}  tp{s['tp']} fp{s['fp']} "
            f"fn{s['fn']} tn{s['tn']})")


def main() -> None:
    rows = load_rows()
    pos = sum(1 for r in rows if r["label"] == "1")
    print(f"라벨 {len(rows)}건 (관련 {pos} / 무관 {len(rows) - pos})\n")

    cfg = load_config()
    icfg = cfg["insight"]
    model = os.getenv("EVAL_MODEL", icfg["l1_model"])
    client = build_client()
    if client is None:
        sys.exit("Anthropic 클라이언트를 만들 수 없습니다 (ANTHROPIC_API_KEY 확인)")
    profile = load_team_profile(icfg.get("team_profile_path", ""))
    system = [{"type": "text", "text": RULES + ("\n\n" + profile if profile else "")}]
    print(f"모델 {model}\n")

    # zero-shot: 예시 없이. 전체 50건에 한 번.
    pred = classify(client, model, system, rows, shots=[])
    print(line("zero-shot 전체", score(rows, pred)))
    print()

    # ★ 씨앗을 여러 개 돌린다. 한 번 갈라 본 2겹은 분할 운을 못 걷어낸다 —
    #   첫 실험에서 같은 설정의 두 겹이 recall 0.500 / 0.846으로 갈렸다.
    #   겹 하나가 n=25라 그 정도 흔들림은 당연하다. 평균과 폭을 같이 봐야 한다.
    seeds = [int(x) for x in os.getenv("EVAL_SEEDS", "7,11,23,42,101").split(",")]
    runs: list[dict] = []
    votes: dict[str, list[str]] = {}
    for seed in seeds:
        a, b = folds(rows, seed)
        merged_rows: list[dict] = []
        merged_pred: list[str] = []
        for test, train in ((a, b), (b, a)):
            merged_pred += classify(client, model, system, test, shots=train)
            merged_rows += test
        s_ = score(merged_rows, merged_pred)
        runs.append(s_)
        for r, pr in zip(merged_rows, merged_pred):
            votes.setdefault(r["id"], []).append(pr)
        print(line(f"few-shot 씨앗{seed}", s_))

    def spread(key: str) -> str:
        vals = sorted(r[key] for r in runs)
        return f"{sum(vals) / len(vals):.3f}  (범위 {vals[0]:.3f}~{vals[-1]:.3f})"

    print(f"\nfew-shot 씨앗 {len(seeds)}회 평균")
    print(f"    precision {spread('precision')}")
    print(f"    recall    {spread('recall')}")


    # ── 축 1개짜리 뭉치를 갈라낼 수 있는가 ──
    # 여기가 이 실험의 진짜 질문이다. 2축 이상은 이미 precision 0.800이라
    # LLM이 보탤 게 없다. 문제는 통과분의 대부분인 1축 38건이고, 그 안에서
    # 관련은 58%다. 교차 점수는 곧 축 개수라서 이 뭉치 안을 못 가른다.
    by_id = {r["id"]: r for r in rows}
    n_axes = {i: len([a for a in (r["axes"] or "").split("+") if a.strip()])
              for i, r in by_id.items()}
    for bucket, keep in (("1축만", lambda k: n_axes[k] == 1),
                         ("2축 이상", lambda k: n_axes[k] >= 2)):
        sub = [k for k in votes if keep(k)]
        if not sub:
            continue
        # 씨앗 5회 다수결 — 분할 운과 모델 흔들림을 평균으로 걷어낸다
        pred = ["1" if votes[k].count("1") * 2 > len(votes[k]) else "0" for k in sub]
        base = sum(1 for k in sub if by_id[k]["label"] == "1") / len(sub)
        print(line(f"  L {bucket} (원래 {base:.0%})", score([by_id[k] for k in sub], pred)))

    print("\n  기준선 (같은 50건 실측)")
    print("    임베딩 필터        precision 0.560  recall 0.519")
    print("    축 1개 이상        precision 0.605  recall 0.963")

    # 틀린 것만 본다 — 숫자보다 이게 다음 판단을 만든다
    wrong = [(r, p) for r, p in zip(merged_rows, merged_pred) if r["label"] != p]  # 마지막 씨앗
    if wrong:
        print(f"\n  틀린 {len(wrong)}건 (마지막 씨앗)")
        for r, p in wrong:
            kind = "놓침 FN" if p == "0" else "오탐 FP"
            print(f"    [{kind}] {r['source']:<11} {r['title'][:48]}")


if __name__ == "__main__":
    main()
