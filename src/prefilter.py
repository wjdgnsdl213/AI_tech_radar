"""①키워드 prefilter — 축 키워드로 항목에 축을 태깅한다. 무료 단계.

파이프라인에서의 위치:
    수집 → **prefilter(무료)** → filter(임베딩, 비쌈) → score(교차 점수) → insight

왜 임베딩 앞에 두나:
    임베딩은 5만 건에 GPU로도 분 단위, CPU면 시간 단위다. 축 키워드가 하나도 안 걸리는
    항목("Emacs 31.1 새 기능")은 어느 축에도 속하지 않으니 임베딩에 태울 이유가 없다.
    여기서 볼륨을 줄이는 게 PLAN §8의 2단 방어 중 첫 단이다.

왜 축 태깅까지 여기서 하나:
    "이 항목이 어느 축에 걸리나"를 계산해야 prefilter 판정(min_axis_hits)이 나온다.
    같은 계산을 score.py가 다시 하면 두 곳의 결과가 갈릴 수 있어, 여기서 한 번 계산해
    item_axes에 적재하고 score.py는 그걸 읽어 점수만 매긴다.

실행:
  python -m src.prefilter                  # 전체 항목 재태깅 (멱등)
  python -m src.prefilter --dry-run        # DB를 건드리지 않고 분포만 확인
  python -m src.prefilter --sample 5000    # 표본으로 빠르게 감 잡기
  python -m src.prefilter --axis smallbiz --show 20   # 특정 축에 뭐가 걸리는지 확인
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from typing import Any, Iterable

from sqlalchemy import func, select

from src.db import get_engine, item_axes, items, load_config, replace_axes_bulk

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

_WS_RE = re.compile(r"\s+")
_ASCII_RE = re.compile(r"^[\x00-\x7F]+$")


def parse_keywords(spec: dict[str, Any]) -> list[str]:
    """config의 축 키워드를 납작한 목록으로 편다.

    config는 가독성 때문에 'AI, 인공지능, LLM, 생성형'처럼 한 줄에 여러 개를 쉼표로
    묶어 적는다. 줄 단위가 아니라 키워드 단위로 매칭해야 하므로 여기서 쪼갠다.
    """
    return [k.strip() for line in spec.get("keywords") or [] for k in str(line).split(",")
            if k.strip()]


# 이 길이 이상인 한글 키워드만 '공백 제거' 매칭을 쓴다. 근거는 _compile 주석 참조.
SQUISH_MIN_LEN = 4


def _alt(keywords: Iterable[str]) -> str:
    """정규식 대안 절. 긴 것부터 둬야 'data platform'이 'data'보다 먼저 잡힌다."""
    return "|".join(re.escape(k) for k in sorted(set(keywords), key=len, reverse=True))


def _compile(keywords: Iterable[str]) -> tuple[re.Pattern | None, re.Pattern | None,
                                               re.Pattern | None]:
    """(ASCII, 짧은 한글, 긴 한글) 세 패턴. 나누는 이유가 각각 있다.

    ① ASCII 키워드(AI·RAG·dbt)는 경계를 봐야 한다. 'AI'가 'SAID'에 걸리면 안 된다.
       그런데 \\b는 못 쓴다 — 파이썬 정규식에서 한글도 단어 문자라 'AI가'의 A와 가
       사이에 경계가 없어 \\bAI\\b가 매칭에 실패한다. 한국어 기사에서 조사가 붙는 건
       흔한 형태라 이걸 놓치면 AI 축이 통째로 비어버린다.
       → 앞뒤가 영숫자가 아닐 것만 요구하는 lookaround를 쓴다.

    ② 긴 한글 키워드는 공백을 지운 텍스트에 대고 찾는다. config는 '데이터플랫폼'처럼
       붙여 적었는데 기사는 '데이터 플랫폼'으로 띄어 쓰는 쪽이 훨씬 많다. 양쪽에서
       공백을 없애면 띄어쓰기 흔들림을 전부 흡수한다. sobiz 기사에 '인공 지능',
       '공공 데이터'처럼 벌어진 표기가 실제로 많아 이게 없으면 대량으로 놓친다.

    ③ 그런데 짧은 키워드에 같은 걸 하면 단어 경계를 넘어 오탐이 터진다. 실측:
         '제번스의 역설 관점 포함' → '관점포함' → 점포 ✗
         '더 이상 권장하지'        → '이상권장'  → 상권 ✗
       그래서 4글자 미만 한글 키워드는 원문 그대로만 찾는다. 짧은 키워드는 애초에
       한 단어(상권·점포·폐업·자영업)라 공백이 낄 일이 없어 잃는 것도 없다.
    """
    ascii_kws = [k for k in keywords if _ASCII_RE.match(k)]
    ko = [k for k in keywords if not _ASCII_RE.match(k)]
    ko_long = [_WS_RE.sub("", k) for k in ko if len(_WS_RE.sub("", k)) >= SQUISH_MIN_LEN]
    ko_short = [k for k in ko if len(_WS_RE.sub("", k)) < SQUISH_MIN_LEN]

    ascii_pat = (re.compile(rf"(?<![A-Za-z0-9])(?:{_alt(ascii_kws)})(?![A-Za-z0-9])",
                            re.IGNORECASE) if ascii_kws else None)
    short_pat = re.compile(_alt(ko_short)) if ko_short else None
    long_pat = re.compile(_alt(ko_long)) if ko_long else None
    return ascii_pat, short_pat, long_pat


class AxisMatcher:
    """축별 키워드 매처. score.py도 이걸 그대로 쓴다."""

    def __init__(self, axes_cfg: dict[str, Any]) -> None:
        self.labels = {ax: spec.get("label", ax) for ax, spec in axes_cfg.items()}
        self.keywords = {ax: parse_keywords(spec) for ax, spec in axes_cfg.items()}
        self.patterns = {ax: _compile(kws) for ax, kws in self.keywords.items()}

    def match(self, text: str) -> dict[str, list[str]]:
        """축 → 걸린 키워드 목록. 안 걸린 축은 아예 넣지 않는다."""
        squished = _WS_RE.sub("", text)
        hits: dict[str, list[str]] = {}
        for ax, (ascii_pat, short_pat, long_pat) in self.patterns.items():
            found: list[str] = []
            if ascii_pat:
                found += [m.group(0).lower() for m in ascii_pat.finditer(text)]
            if short_pat:
                found += [m.group(0) for m in short_pat.finditer(text)]
            if long_pat:
                found += [m.group(0) for m in long_pat.finditer(squished)]
            if found:
                # 같은 키워드가 제목·요약에 반복돼도 한 번으로 센다
                hits[ax] = sorted(set(found))
        return hits


def doc_text(title: str | None, summary: str | None) -> str:
    """매칭 대상 텍스트. 수집 원칙상 본문은 없고 제목+요약이 전부다."""
    return f"{title or ''} {summary or ''}".strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="축 키워드 prefilter + 축 태깅")
    parser.add_argument("--dry-run", action="store_true", help="DB를 쓰지 않고 분포만 출력")
    parser.add_argument("--sample", type=int, default=0, help="표본 수 (0=전체)")
    parser.add_argument("--axis", default=None, help="이 축에 걸린 항목 예시를 본다")
    parser.add_argument("--show", type=int, default=10, help="예시 출력 건수")
    parser.add_argument("--batch", type=int, default=5000, help="DB 반영 배치 크기")
    args = parser.parse_args()

    cfg = load_config()
    matcher = AxisMatcher(cfg["axes"])
    min_hits = int(cfg.get("prefilter", {}).get("min_axis_hits", 1))

    print(f"축 {len(matcher.keywords)}개  |  키워드 "
          + " / ".join(f"{matcher.labels[a]} {len(k)}" for a, k in matcher.keywords.items()))
    print(f"min_axis_hits {min_hits}  (이 미만이면 임베딩 단계로 넘기지 않는다)\n")

    engine = get_engine()
    stmt = select(items.c.id, items.c.source, items.c.title, items.c.summary)
    if args.sample:
        stmt = stmt.order_by(func.random()).limit(args.sample)
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
    if not rows:
        sys.exit("대상 항목이 없습니다. 먼저 python -m src.collect 를 실행하세요.")
    print(f"대상 {len(rows):,}건 매칭 중…")

    t0 = time.time()
    tagged: dict[int, list[str]] = {}
    axis_count = Counter()          # 축별 걸린 항목 수
    n_axes_dist = Counter()         # 몇 축에 걸렸나의 분포
    by_source = Counter()           # 소스별 통과 수
    src_total = Counter()
    kw_count = Counter()            # 키워드별 기여도
    examples: list[tuple[str, str, list[str]]] = []

    for r in rows:
        hits = matcher.match(doc_text(r.title, r.summary))
        axes = sorted(hits)
        tagged[r.id] = axes
        n_axes_dist[len(axes)] += 1
        src_total[r.source] += 1
        for ax in axes:
            axis_count[ax] += 1
            for kw in hits[ax]:
                kw_count[f"{ax}:{kw}"] += 1
        if len(axes) >= min_hits:
            by_source[r.source] += 1
        if args.axis and args.axis in hits and len(examples) < args.show:
            examples.append((r.source, r.title or "", hits[args.axis]))

    elapsed = time.time() - t0
    passed = sum(n for k, n in n_axes_dist.items() if k >= min_hits)

    # ── ① 축 수 분포 — 교집합이 얼마나 나오는지가 이 프로젝트의 핵심 지표다 ──
    print(f"  ({elapsed:.1f}초)\n")
    print(f"{'=' * 62}\n① 걸친 축 수 분포\n{'=' * 62}")
    for n in sorted(n_axes_dist):
        cnt = n_axes_dist[n]
        bar = "█" * int(cnt / len(rows) * 40)
        tag = "  ← 버려짐" if n < min_hits else ("  ★ 교집합" if n >= 2 else "")
        print(f"  {n}축  {cnt:>7,}건 ({cnt / len(rows) * 100:5.1f}%) {bar}{tag}")
    print(f"\n  prefilter 통과 {passed:,}건 / {len(rows):,}건 "
          f"({passed / len(rows) * 100:.1f}%) → 임베딩 대상")

    # ── ② 축별 ──
    print(f"\n{'=' * 62}\n② 축별 항목 수\n{'=' * 62}")
    for ax in matcher.keywords:
        n = axis_count[ax]
        print(f"  {matcher.labels[ax]:<8} {n:>7,}건 ({n / len(rows) * 100:5.1f}%)")

    # ── ③ 소스별 통과율 — 한 소스만 통과하면 키워드가 편향된 것 ──
    print(f"\n{'=' * 62}\n③ 소스별 통과율\n{'=' * 62}")
    for s in sorted(src_total, key=lambda x: -src_total[x]):
        print(f"  {s:<14}{by_source[s]:>7,} / {src_total[s]:>7,}"
              f"  ({by_source[s] / src_total[s] * 100:5.1f}%)")

    # ── ④ 키워드 기여도 — 한 키워드가 독식하면 그게 노이즈원이다 ──
    print(f"\n{'=' * 62}\n④ 키워드 기여도 상위 15\n{'=' * 62}")
    for kw, n in kw_count.most_common(15):
        print(f"  {n:>7,}  {kw}")

    if args.axis:
        print(f"\n{'=' * 62}\n⑤ '{args.axis}' 축 예시\n{'=' * 62}")
        for src, title, kws in examples:
            print(f"  [{src:<10}] {title[:56]}")
            print(f"               ↳ {', '.join(kws)}")

    # ── 적재 ──
    if args.dry_run or args.sample:
        why = "--dry-run" if args.dry_run else "--sample (부분 결과라 적재하지 않는다)"
        print(f"\n⏭  DB에 반영하지 않았습니다 ({why})")
        return

    print(f"\nitem_axes 적재 중…")
    total = 0
    ids = list(tagged)
    for i in range(0, len(ids), args.batch):
        chunk = {k: tagged[k] for k in ids[i:i + args.batch]}
        total += replace_axes_bulk(chunk, engine)
    with engine.connect() as conn:
        in_db = conn.execute(select(func.count()).select_from(item_axes)).scalar_one()
    print(f"  태그 {total:,}개 적재 완료 (item_axes 총 {in_db:,}행)")
    print("\n  다음: python -m src.score")


if __name__ == "__main__":
    main()
