"""키워드 추출 — 통과 항목에서 명사 키워드를 뽑아 `item_keywords`에 넣는다.

파이프라인에서의 위치:
    … → filter → score → **extract** → trend(급상승) → digest

F8(키워드 트렌드)의 입력이다. PLAN §3-B가 "키워드 × 주차 × 언급량 시계열을 CSV/DB로
제공해 빅데이터팀이 자기 분석에 재활용한다"고 한 것도 이 테이블을 말한다.

형태소 분석은 kiwipiepy를 쓴다(sobiz에서 검증됨). 다만 한·영 혼재라 그대로 쓰면 안 된다:

    ① 복합어가 쪼개진다
       '지능정보화위원회' → 지능 / 정보 / 위원회
       그래서 **연속된 명사를 n-gram으로 다시 붙인다**. 'AI거버넌스', '공공데이터'처럼
       실제로 쓰이는 형태가 이때 복원된다.

    ② 영어를 한글처럼 붙이면 쓰레기가 된다
       'Spec new features for' → 'Specnewfeaturesfor'
       영어 토큰은 공백으로 잇고 2-gram까지만 만든다.

    ③ 영어 기능어가 명사로 잡힌다
       new / for / features 같은 것들. 대문자로 시작하거나 전부 대문자인 토큰만
       남긴다 — 고유명사(Apache, Iceberg)와 약어(AI, CCTV, RAG)가 정확히 그 형태다.

실행:
  python -m src.extract                # 통과 항목 전체 재추출 (멱등)
  python -m src.extract --sample 300   # 표본으로 품질 확인
  python -m src.extract --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from typing import Any, Iterable

from sqlalchemy import Column, Index, MetaData, String, Table, func, select

from src.db import PK_INT, get_engine, init_db, items, load_config, metadata

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

# ── item_keywords: 항목 × 키워드 ──────────────────────────────────
# 새 테이블이라 metadata.create_all이 알아서 만든다(기존 테이블에 컬럼을 더하는
# ALTER와 달리 테이블 추가는 create_all이 처리한다).
item_keywords = Table(
    "item_keywords", metadata,
    Column("item_id", PK_INT, nullable=False),
    Column("keyword", String(64), nullable=False),
    Column("week", String(8)),            # 집계 키를 같이 박아 조인을 줄인다
    Index("ix_kw_keyword", "keyword"),
    Index("ix_kw_week", "week", "keyword"),
    Index("ix_kw_item", "item_id"),
)

NOUN_TAGS = {"NNG", "NNP"}     # 일반명사 · 고유명사
FOREIGN_TAG = "SL"             # 영문
# 체언 접두사. '소상공인'이 '소'(XPN) + '상공인'(NNG)으로 쪼개진다 — 이걸 안 붙이면
# 이 프로젝트에서 가장 중요한 키워드가 통째로 '상공인'이 된다.
# ('초거대', '재개발', '신사업'도 같은 구조다)
PREFIX_TAG = "XPN"
_ASCII = re.compile(r"^[\x00-\x7F]+$")
_HAS_DIGIT_ONLY = re.compile(r"^[\d\W_]+$")

# 뉴스 제목에 흔하지만 키워드로는 의미 없는 것들. 실측 상위 목록을 보고 추린다.
STOPWORDS = {
    "기자", "사진", "제공", "관련", "지난해", "올해", "내년", "지난", "이번",
    "경우", "대상", "지원", "추진", "확대", "강화", "개최", "진행", "계획",
    "발표", "실시", "위해", "통해", "대한", "때문", "가능", "필요", "중요",
    "예정", "기준", "대비", "이상", "이하", "최근", "현재", "오늘", "내용",
    "부문", "분야", "관계자", "설명", "밝혔다", "전했다", "따르면", "일보",
    "뉴스", "취재", "종합", "단독", "속보", "인터뷰", "칼럼", "사설",
    # 단독으로 서면 아무 정보가 없는 일반명사. 복합어의 일부로는 살아남는다
    # ('분석'은 버리지만 '상권분석'은 남는다).
    "데이터", "지역", "활용", "기반", "사업", "운영", "활성", "분석", "서비스",
    "구축", "시장", "경제", "디지털", "기업", "현장", "플랫폼", "금융", "관리",
    "정책", "소비", "산업", "기술", "혁신", "성장", "협력", "확산", "도입",
    "고도", "전환", "개선", "제공", "구현", "적용", "확보", "마련", "조성",
    # 연관어 그래프에서 브릿지 상위를 덮던 것들(src/graph.py 실측).
    # 어느 문맥에나 붙어서 축을 가리지 않고 이어지는 말들이다.
    "관계", "개인", "사전", "바탕", "경험", "안내", "선정", "제작", "설계",
    "위험", "정부", "사업자", "대상자", "참여", "운용", "추천", "공개",
    "발굴", "육성", "확충", "구성", "결과", "계기", "중심", "차원", "수준",
}


def _joinable(run: list[tuple[str, str]], i: int, n: int) -> str | None:
    """연속 명사 run[i:i+n]을 하나의 키워드로 합친다. 못 합치면 None.

    한글끼리는 붙이고(공공 + 데이터 → 공공데이터), 영어가 끼면 공백으로 잇는다
    (Apache Iceberg). 영어만으로 3단어 이상 붙이는 건 거의 항상 쓰레기라 막는다.
    """
    parts = run[i:i + n]
    if not parts:
        return None
    all_ascii = all(_ASCII.match(f) for f, _ in parts)
    if all_ascii and n > 2:
        return None
    sep = " " if all_ascii and n > 1 else ""
    return sep.join(f for f, _ in parts)


def _acceptable(kw: str) -> bool:
    if len(kw) < 2 or len(kw) > 40:
        return False
    if _HAS_DIGIT_ONLY.match(kw):
        return False
    if kw in STOPWORDS:
        return False
    if _ASCII.match(kw):
        # 영어는 고유명사·약어만 — 소문자 기능어(new, for, features)를 걸러낸다
        head = kw.split()[0]
        if not (head[:1].isupper() or head.isupper()):
            return False
        if len(kw) < 3:
            return False
    return True


class KeywordExtractor:
    def __init__(self, max_ngram: int = 4) -> None:
        from kiwipiepy import Kiwi      # 로딩이 느려 지연 임포트
        self.kiwi = Kiwi()
        self.max_ngram = max_ngram

    def __call__(self, text: str) -> list[str]:
        out: list[str] = []
        run: list[tuple[str, str]] = []

        def flush() -> None:
            for i in range(len(run)):
                for n in range(1, min(self.max_ngram, len(run) - i) + 1):
                    kw = _joinable(run, i, n)
                    if kw and _acceptable(kw):
                        out.append(kw)
            run.clear()

        for tok in self.kiwi.tokenize(text or ""):
            noun = (tok.tag in NOUN_TAGS or tok.tag == FOREIGN_TAG) and len(tok.form) > 1
            # 접두사는 한 글자여도 run에 넣는다 — 뒤 명사와 붙어야 의미가 산다.
            # 혼자 남으면 _acceptable의 길이 조건에서 걸러진다.
            prefix = tok.tag == PREFIX_TAG
            if noun or prefix:
                run.append((tok.form, tok.tag))
            else:
                flush()
        flush()
        # 한 문서 안에서 같은 키워드는 한 번만 센다(문서 빈도 기준)
        return sorted(set(out))


def replace_keywords(rows: Iterable[tuple[int, str | None, list[str]]],
                     engine=None) -> int:
    """항목별 키워드를 통째로 교체한다. 한 트랜잭션에서 delete+insert."""
    engine = engine or get_engine()
    rows = list(rows)
    if not rows:
        return 0
    ids = [r[0] for r in rows]
    payload = [{"item_id": i, "keyword": k, "week": w}
               for i, w, kws in rows for k in kws]
    with engine.begin() as conn:
        for i in range(0, len(ids), 500):     # SQLite 파라미터 상한(999) 회피
            conn.execute(item_keywords.delete()
                         .where(item_keywords.c.item_id.in_(ids[i:i + 500])))
        if payload:
            conn.execute(item_keywords.insert(), payload)
    return len(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="키워드 추출 (F8 트렌드의 입력)")
    parser.add_argument("--sample", type=int, default=0, help="표본 수 (0=전체)")
    parser.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않는다")
    parser.add_argument("--top", type=int, default=25, help="상위 키워드 출력 수")
    parser.add_argument("--max-ngram", type=int, default=4)
    parser.add_argument("--batch", type=int, default=2000)
    args = parser.parse_args()

    load_config()
    engine = get_engine()
    init_db(engine)      # item_keywords 테이블 보장

    stmt = (select(items.c.id, items.c.title, items.c.summary, items.c.published_week)
            .where(items.c.kept.is_(True)))
    if args.sample:
        stmt = stmt.order_by(func.random()).limit(args.sample)
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
    if not rows:
        sys.exit("통과 항목이 없습니다. 먼저 python -m src.filter 를 실행하세요.")

    print(f"대상 {len(rows):,}건  |  최대 {args.max_ngram}-gram")
    extract = KeywordExtractor(args.max_ngram)

    t0 = time.time()
    result: list[tuple[int, str | None, list[str]]] = []
    counter = Counter()
    for n, r in enumerate(rows, 1):
        kws = extract(f"{r.title or ''} {r.summary or ''}")
        result.append((r.id, r.published_week, kws))
        counter.update(kws)
        if n % 1000 == 0:
            print(f"  {n:,}/{len(rows):,}")
    print(f"  ({time.time() - t0:.1f}초)  고유 키워드 {len(counter):,}개\n")

    print(f"{'=' * 56}\n상위 {args.top} 키워드 (문서 빈도)\n{'=' * 56}")
    for kw, c in counter.most_common(args.top):
        print(f"  {c:>5}  {kw}")

    if args.dry_run or args.sample:
        why = "--dry-run" if args.dry_run else "--sample (부분 결과라 적재하지 않는다)"
        print(f"\n⏭  DB에 반영하지 않았습니다 ({why})")
        return

    print("\nitem_keywords 적재 중…")
    total = 0
    for i in range(0, len(result), args.batch):
        total += replace_keywords(result[i:i + args.batch], engine)
    with engine.connect() as conn:
        in_db = conn.execute(select(func.count()).select_from(item_keywords)).scalar_one()
    print(f"  {total:,}개 적재 (테이블 총 {in_db:,}행)")
    print("\n  다음: python -m src.trend")


if __name__ == "__main__":
    main()
