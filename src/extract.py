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

★ 통과 항목이 아니라 **전체 코퍼스**에서 뽑는다
    필터(kept)는 다이제스트를 위한 것이다 — 밀어주는 지면은 좁으니 좁게 걸러야 한다.
    그런데 탐색은 반대다. 'ollama', '퀀트투자' 같은 말을 찾아보려면 95%를 버린
    3,046건 안에는 아예 없다. push(다이제스트)와 pull(탐색)은 기준이 달라야 한다.
    급상승·브릿지는 여전히 통과분만 본다(trend.py / graph.py에서 걸러 쓴다).

★ 1회성 키워드는 저장하지 않는다
    n-gram이라 항목당 34개가 나오고 전체 6.4만 건이면 220만 행이다. 그중 대부분이
    한 문서에만 나오는 말이라 공기 관계를 만들 수 없다 — 저장해도 쓸 데가 없고
    DB만 불린다. min_df로 자른다.

실행:
  python -m src.extract                # 전체 코퍼스 재추출 (멱등)
  python -m src.extract --scope kept   # 통과 항목만 (예전 동작)
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

from sqlalchemy import (Boolean, Column, Index, MetaData, String, Table,
                        func, select)

from src.db import PK_INT, get_engine, init_db, items, kw_engine, load_config

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

# ── item_keywords: 항목 × 키워드 ──────────────────────────────────
# radar.db가 아니라 파생 인덱스 DB(data/keywords.db)에 산다 — src/db.kw_engine 참조.
kw_metadata = MetaData()
item_keywords = Table(
    "item_keywords", kw_metadata,
    Column("item_id", PK_INT, nullable=False),
    Column("keyword", String(64), nullable=False),
    Column("week", String(8)),            # 집계 키를 같이 박아 조인을 줄인다
    # 이 항목이 필터를 통과했는지. 키워드 DB와 본 DB가 다른 파일이라 조인을 못 하는데,
    # 급상승(trend)과 브릿지(graph)는 통과분만 봐야 하므로 여기 같이 박아둔다.
    # 탐색(ego)은 이 값을 무시하고 전체를 본다.
    Column("kept", Boolean),
    Index("ix_kw_keyword", "keyword"),
    Index("ix_kw_week", "week", "keyword"),
    Index("ix_kw_kept", "kept", "keyword"),
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
    # ego 네트워크 이웃 상위에 끼던 것들
    "사이", "예비", "이상", "상황", "방식", "형태", "대비", "동안", "이후",
    "당시", "직접", "각각", "일부", "전체", "다양", "주요", "관련성",
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


def init_kw_db(engine=None) -> None:
    """키워드 인덱스 테이블 보장."""
    kw_metadata.create_all(engine or kw_engine())


def replace_keywords(rows: Iterable[tuple[int, str | None, list[str], bool]],
                     engine=None) -> int:
    """항목별 키워드를 통째로 교체한다. 한 트랜잭션에서 delete+insert."""
    engine = engine or kw_engine()
    rows = list(rows)
    if not rows:
        return 0
    ids = [r[0] for r in rows]
    payload = [{"item_id": i, "keyword": k, "week": w, "kept": bool(kp)}
               for i, w, kws, kp in rows for k in kws]
    with engine.begin() as conn:
        for i in range(0, len(ids), 500):     # SQLite 파라미터 상한(999) 회피
            conn.execute(item_keywords.delete()
                         .where(item_keywords.c.item_id.in_(ids[i:i + 500])))
        if payload:
            conn.execute(item_keywords.insert(), payload)
    return len(payload)


def _run_low_memory(rows, extract, args, engine) -> None:
    """두 번 훑는 대신 메모리를 아낀다 — 컨테이너용.

    ★ 왜 필요한가
      기본 경로는 85,000건의 키워드를 **한꺼번에** 리스트에 들고 있다가
      df 필터에서 복사본을 하나 더 만든다. 실측 최대 RSS 924MB다.
      메모리가 작은 배포 환경(512MB~1GB)에서는 여기서 OOM으로 죽고,
      화면에는 연관어·급상승·기관만 조용히 비어 보인다.

    ★ 어떻게 아끼나
      df 필터는 "전체를 세어본 뒤"에만 정할 수 있어서 한 번에 못 끝낸다.
      그래서 두 번 훑는다 — 1회차는 세기만(Counter 하나), 2회차는 다시 뽑아
      배치 단위로 쓰고 즉시 버린다. CPU는 두 배가 되지만 메모리는 거의 안 쓴다.
      부팅 때 한 번 도는 작업이라 시간보다 안 죽는 게 중요하다.
    """
    counter = Counter()
    print(f"  [1/2] 키워드 세는 중… ({len(rows):,}건)")
    for n, r in enumerate(rows, 1):
        counter.update(extract(f"{r.title or ''} {r.summary or ''}"))
        if n % 5000 == 0:
            print(f"    {n:,}/{len(rows):,}")
    keep = ({k for k, c in counter.items() if c >= args.min_df}
            if args.min_df > 1 else None)
    print(f"  고유 키워드 {len(counter):,}개"
          + (f" → df≥{args.min_df} {len(keep):,}개" if keep is not None else ""))
    del counter

    print(f"  [2/2] 적재 중…")
    buf, total = [], 0
    for n, r in enumerate(rows, 1):
        kws = extract(f"{r.title or ''} {r.summary or ''}")
        if keep is not None:
            kws = [k for k in kws if k in keep]
        buf.append((r.id, r.published_week, kws, bool(r.kept)))
        if len(buf) >= args.batch:
            total += replace_keywords(buf, kw_engine())
            buf.clear()          # 쓴 건 즉시 버린다 — 이게 메모리를 잡는 핵심
        if n % 5000 == 0:
            print(f"    {n:,}/{len(rows):,}")
    if buf:
        total += replace_keywords(buf, kw_engine())
    with kw_engine().connect() as conn:
        in_db = conn.execute(select(func.count()).select_from(item_keywords)).scalar_one()
    _checkpoint_wal()
    print(f"  {total:,}개 적재 (테이블 총 {in_db:,}행)")


def _checkpoint_wal() -> None:
    """WAL을 본 파일에 반영하고 잘라낸다.

    ★ 왜 필요한가
      keywords.db는 다른 PC로 그냥 복사해 쓰는 파일이다(저장소에 넣기엔 크고,
      본 DB에서 다시 만들 수 있는 파생물이라 넣지 않는다). 그런데 WAL 모드에서는
      최근 쓴 내용이 -wal 파일에 남아 있을 수 있어, .db만 복사하면 그만큼이
      빠진다. 끝낼 때 반영해 두면 **파일 하나로 온전해진다.**
      실측: 정리 전 -wal이 870MB까지 자라 있었다(내용은 이미 반영된 뒤였지만,
      그 크기로는 복사할 때 무엇이 필요한지 판단할 수 없다).
    """
    try:
        with kw_engine().connect() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as exc:
        print(f"  (WAL 정리 건너뜀: {type(exc).__name__})")


def main() -> None:
    parser = argparse.ArgumentParser(description="키워드 추출 (F8 트렌드의 입력)")
    parser.add_argument("--sample", type=int, default=0, help="표본 수 (0=전체)")
    parser.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않는다")
    parser.add_argument("--top", type=int, default=25, help="상위 키워드 출력 수")
    parser.add_argument("--max-ngram", type=int, default=4)
    parser.add_argument("--batch", type=int, default=2000)
    parser.add_argument("--low-memory", action="store_true",
                        help="두 번 훑어 메모리를 아낀다 (컨테이너 배포용, 시간 2배)")
    parser.add_argument("--scope", default="all", choices=["all", "kept"],
                        help="all=전체 코퍼스(탐색용, 기본) / kept=통과분만")
    parser.add_argument("--min-df", type=int, default=2,
                        help="이 문서 수 미만 키워드는 저장하지 않는다 (1회성 제거)")
    args = parser.parse_args()

    load_config()
    engine = get_engine()
    init_db(engine)
    init_kw_db()         # 파생 인덱스 DB 보장

    stmt = select(items.c.id, items.c.title, items.c.summary,
                  items.c.published_week, items.c.kept)
    if args.scope == "kept":
        stmt = stmt.where(items.c.kept.is_(True))
    if args.sample:
        stmt = stmt.order_by(func.random()).limit(args.sample)
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
    if not rows:
        sys.exit("항목이 없습니다. 먼저 python -m src.collect 를 실행하세요.")

    print(f"대상 {len(rows):,}건 ({args.scope})  |  최대 {args.max_ngram}-gram "
          f"|  df<{args.min_df} 제외")
    extract = KeywordExtractor(args.max_ngram)

    if args.low_memory and not (args.dry_run or args.sample):
        _run_low_memory(rows, extract, args, engine)
        return

    t0 = time.time()
    result: list[tuple[int, str | None, list[str], bool]] = []
    counter = Counter()
    for n, r in enumerate(rows, 1):
        kws = extract(f"{r.title or ''} {r.summary or ''}")
        result.append((r.id, r.published_week, kws, bool(r.kept)))
        counter.update(kws)
        if n % 1000 == 0:
            print(f"  {n:,}/{len(rows):,}")
    elapsed = time.time() - t0

    # 1회성 키워드 제거. n-gram이라 항목당 34개가 나오는데 대부분이 한 문서에만
    # 나오는 말이라 공기 관계를 만들 수 없다 — 저장해도 쓸 데가 없고 DB만 불린다.
    # (실측: 이 컷 없이 전체 코퍼스를 넣었더니 220만 행 / radar.db가 72MB→284MB)
    if args.min_df > 1:
        keep = {k for k, n in counter.items() if n >= args.min_df}
        before = sum(len(ks) for _, _, ks, _ in result)
        result = [(i, w, [k for k in ks if k in keep], kp) for i, w, ks, kp in result]
        after = sum(len(ks) for _, _, ks, _ in result)
        print(f"  ({elapsed:.1f}초)  고유 키워드 {len(counter):,}개 "
              f"→ df≥{args.min_df} {len(keep):,}개")
        print(f"  저장 행 {before:,} → {after:,} "
              f"({after / max(before, 1) * 100:.0f}%)\n")
    else:
        print(f"  ({elapsed:.1f}초)  고유 키워드 {len(counter):,}개\n")

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
        # ★ 본 DB 엔진(engine)이 아니라 키워드 인덱스 DB에 쓴다.
        total += replace_keywords(result[i:i + args.batch], kw_engine())
    with kw_engine().connect() as conn:
        in_db = conn.execute(select(func.count()).select_from(item_keywords)).scalar_one()
    _checkpoint_wal()
    print(f"  {total:,}개 적재 (테이블 총 {in_db:,}행)")
    print("\n  다음: python -m src.trend")


if __name__ == "__main__":
    main()
