"""핵심 판정 로직 테스트.

여기 있는 함수들은 전부 **실측을 보고 값을 정한 것들**이다. 임계값 하나만 흔들려도
결과가 조용히 나빠지는데, 파이프라인은 여전히 정상 종료하므로 눈치채기 어렵다.
그래서 "왜 이 값인가"의 근거가 된 실제 사례를 그대로 테스트로 박아둔다.

DB도 네트워크도 모델도 쓰지 않는다 — 순수 함수만 본다.

실행:  pytest tests/ -q
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.digest import _bigrams, is_syndicated
from src.extract import _acceptable, _joinable
from src.prefilter import AxisMatcher, parse_keywords
from src.score import compute
from src.trend import _fragment_of, prev_weeks


# ── prefilter: 축 키워드 매칭 3종 규칙 ──────────────────────────────
AXES = {
    "ai": {"label": "AI", "keywords": ["AI, 인공지능, RAG", "machine learning"]},
    "bigdata": {"label": "빅데이터", "keywords": ["데이터플랫폼, 공공데이터, 개인정보",
                                                "dbt, data platform"]},
    "smallbiz": {"label": "소상공인", "keywords": ["소상공인, 상권, 점포", "small business"]},
}


@pytest.fixture(scope="module")
def matcher() -> AxisMatcher:
    return AxisMatcher(AXES)


def test_config_keywords_split_on_commas():
    """config는 가독성 때문에 한 줄에 쉼표로 여러 개를 적는다. 줄이 아니라 키워드 단위여야 한다."""
    assert parse_keywords(AXES["ai"]) == ["AI", "인공지능", "RAG", "machine learning"]


def test_ascii_keyword_needs_boundary(matcher):
    """'AI'가 'SAID'/'RAID' 안에서 걸리면 AI 축이 노이즈로 덮인다."""
    assert "ai" not in matcher.match("He SAID it was a RAID array")


def test_ascii_keyword_matches_with_korean_particle(matcher):
    r"""'AI가', 'AI를'처럼 조사가 붙는 건 한국어 기사에서 흔한 형태다.

    \b를 쓰면 여기서 실패한다 — 파이썬 정규식은 한글도 단어 문자로 보기 때문에
    A와 가 사이에 경계가 없다. 이걸 놓치면 AI 축이 통째로 비어버린다.
    """
    assert "ai" in matcher.match("AI가 상권을 분석한다")
    assert "ai" in matcher.match("AI를 도입했다")


def test_long_korean_keyword_ignores_spacing(matcher):
    """sobiz 기사에 '공공 데이터', '인공 지능'처럼 벌어진 표기가 실제로 많다."""
    assert "bigdata" in matcher.match("공공 데이터 개방 확대")
    assert "bigdata" in matcher.match("공공데이터 개방 확대")
    assert "ai" in matcher.match("인공 지능 도입")


def test_short_korean_keyword_does_not_cross_word_boundary(matcher):
    """짧은 키워드에 공백 제거를 적용하면 단어 경계를 넘어 오탐이 난다.

    실측으로 나온 두 사례다:
        '제번스의 역설 관점 포함' → '관점포함' → 점포 ✗
        '더 이상 권장하지'        → '이상권장'  → 상권 ✗
    """
    assert "smallbiz" not in matcher.match("제번스의 역설 관점 포함")
    assert "smallbiz" not in matcher.match("더 이상 권장하지 않는다")
    # 진짜로 나오면 당연히 잡혀야 한다
    assert "smallbiz" in matcher.match("골목 상권 활성화")


def test_multi_axis_item(matcher):
    hits = matcher.match("소상공인 상권분석에 AI와 공공데이터를 활용한다")
    assert set(hits) == {"ai", "bigdata", "smallbiz"}


# ── score: 교차 점수 ────────────────────────────────────────────────
COUNT_W = {1: 0.5, 2: 3.0, 3: 8.0}
AXIS_W = {"ai": 1.0, "bigdata": 1.2, "smallbiz": 1.2}


def test_zero_axis_scores_zero():
    assert compute([], COUNT_W, AXIS_W, 1) == 0.0


def test_crossing_beats_many_single_axis():
    """축 수 가중치가 곱셈으로 들어가는 이유가 이것이다.

    덧셈이면 1축짜리 여러 건이 2축 한 건을 이겨서 '교집합 우선'이 깨진다.
    """
    one = compute(["ai"], COUNT_W, AXIS_W, 1)
    two = compute(["ai", "bigdata"], COUNT_W, AXIS_W, 1)
    three = compute(["ai", "bigdata", "smallbiz"], COUNT_W, AXIS_W, 1)
    assert one < two < three
    assert two > one * 4          # 1축을 네 건 모아도 2축 한 건을 못 넘는다


def test_min_axes_gate():
    assert compute(["ai"], COUNT_W, AXIS_W, 2) == 0.0
    assert compute(["ai", "bigdata"], COUNT_W, AXIS_W, 2) > 0


def test_unknown_axis_count_does_not_crash():
    """축이 4개로 늘어도 터지지 않아야 한다 — config에 없는 축 수는 최대 가중치로."""
    assert compute(["ai", "bigdata", "smallbiz", "x"], COUNT_W, AXIS_W, 1) > 0


# ── digest: 지면 안 신디케이션 제거 ─────────────────────────────────
MODU = [
    "SKT '모두의 AI ' 사업자 선정 ··· 대국민 서비스 계획",
    "전 국민 위한 '모두의 AI'… 카카오·SKT·KT 최종 선정",
    "SKT, 정부 '모두의 AI' 사업자 지정…10월 대국민 서비스 개방",
]
LOTTE = [
    "롯데리아, 고객 DB도 가맹점 성적표에 넣었다…'Top-Store' 96곳 선정",
    "롯데리아, 가맹점 관리도 '성과·데이터'로 … 매출·고객관리 평가 전면 개편",
    "롯데GRS, 프랜차이즈 가맹 관리 체계 '데이터 기반 성과 중심'으로 개편",
]
UNRELATED = [
    "치매검진·교통CCTV 등 고가치 공공데이터 개방 1년 앞당긴다",
    "대구대 재학생 20명, AI 활용 상권분석 및 사업계획 실습 진행",
]


def test_same_event_is_caught():
    """임베딩 군집(컷 0.85)을 통과한 신디케이션이 지면 5칸 중 3칸을 먹었던 사례."""
    assert is_syndicated(MODU[1], [MODU[0]])
    assert is_syndicated(MODU[2], [MODU[0]])


def test_different_events_are_kept():
    seen = [MODU[0]]
    for t in UNRELATED:
        assert not is_syndicated(t, seen)


def test_chain_is_caught_when_rejected_titles_are_kept_in_seen():
    """롯데 3건: 0-1=0.231, 1-2=0.269인데 0-2=0.071이다.

    탈락시킨 1을 seen에서 빼면 2가 0하고만 비교돼 살아남는다.
    build()가 탈락분도 seen에 남기는 이유가 이것이다.
    """
    assert is_syndicated(LOTTE[1], [LOTTE[0]])          # 직접 이어짐
    assert not is_syndicated(LOTTE[2], [LOTTE[0]])       # 건너뛰면 안 잡힌다
    assert is_syndicated(LOTTE[2], [LOTTE[0], LOTTE[1]])  # 사슬로는 잡힌다


def test_very_short_title_is_left_alone():
    """제목이 너무 짧으면 우연 일치가 나므로 건드리지 않는다."""
    assert not is_syndicated("AI", ["AI 기본법 시행령 개정안 공청회"])


def test_bigrams_ignore_punctuation_and_spacing():
    assert _bigrams("공공 데이터") == _bigrams("공공데이터")
    assert _bigrams("A-B-C") == _bigrams("ABC")


# ── extract: 키워드 후보 ────────────────────────────────────────────
def test_korean_nouns_glue_english_does_not():
    """영어를 한글처럼 붙이면 'Specnewfeaturesfor' 같은 쓰레기가 나온다."""
    ko = [("공공", "NNG"), ("데이터", "NNG")]
    en = [("Apache", "SL"), ("Iceberg", "SL")]
    assert _joinable(ko, 0, 2) == "공공데이터"
    assert _joinable(en, 0, 2) == "Apache Iceberg"


def test_english_ngram_capped_at_two():
    en = [("new", "SL"), ("features", "SL"), ("for", "SL")]
    assert _joinable(en, 0, 3) is None


def test_english_function_words_rejected():
    """대문자로 시작하거나 전부 대문자인 것만 남긴다 — 고유명사·약어가 그 형태다."""
    assert not _acceptable("new")
    assert not _acceptable("features for")
    assert _acceptable("Apache")
    assert _acceptable("CCTV")
    assert _acceptable("RAG")


def test_generic_korean_nouns_rejected_but_compounds_kept():
    """'분석' 단독은 정보가 없지만 '상권분석'은 살아야 한다."""
    assert not _acceptable("분석")
    assert _acceptable("상권분석")


# ── trend ───────────────────────────────────────────────────────────
def test_prev_weeks_crosses_year_boundary():
    """'YYYY-Www'를 문자열로 빼면 연초에서 틀린다. 2026-W01의 직전은 2025-W52다."""
    assert prev_weeks("2026-W01", 2) == ["2025-W52", "2025-W51"]
    assert prev_weeks("2026-W35", 3) == ["2026-W34", "2026-W33", "2026-W32"]


def test_prev_weeks_rejects_garbage():
    assert prev_weeks("not-a-week", 3) == []


def test_fragment_detection():
    """n-gram이 '인공지능'과 함께 '인공'·'지능'을 만든다.

    조각 판정 기준은 "자기를 포함하는 더 긴 키워드가 있고, 그쪽 빈도가 자기의 90%
    이상"이다. 즉 **거의 항상 그 긴 말의 일부로만 나오는가**를 본다.
    """
    counts = {"인공지능": 137, "인공": 138, "지능": 154, "데이터": 500, "공공데이터": 221}
    # '인공'은 138번 중 137번이 '인공지능'이었다 → 조각
    assert _fragment_of("인공", counts)
    assert not _fragment_of("인공지능", counts)
    # '지능'(154)은 '인공지능'(137)보다 많다. '지능정보화' 같은 다른 복합어에도
    # 쓰인다는 뜻이라 한 단어의 조각으로 볼 수 없다 → 남긴다.
    # (이런 일반명사는 _fragment_of가 아니라 extract.STOPWORDS가 담당한다)
    assert not _fragment_of("지능", counts)
    # '데이터'도 같은 이유로 조각이 아니다
    assert not _fragment_of("데이터", counts)


# ── 웹 정적 파일 ────────────────────────────────────────────────────
STATIC = Path(__file__).resolve().parents[1] / "web" / "static"


def test_hidden_attribute_beats_author_display():
    """`[hidden]{display:none !important}` 규칙이 있어야 한다.

    브라우저 기본 스타일시트의 [hidden]{display:none}은 **작성자 규칙에 진다.**
    그래서 .modal{display:flex} 같은 걸 쓰면 JS에서 el.hidden = true 로 바꿔도
    요소가 계속 보인다. 실제로 이것 때문에 급상승 팝업이 닫히지 않았고,
    연관어 도구막대도 검색 전부터 떠 있었다.

    el.hidden으로 여닫는 요소가 여럿이라 규칙 하나로 못박아 두고 여기서 지킨다.
    """
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert re.search(r"\[hidden\]\s*\{[^}]*display\s*:\s*none\s*!important", css), (
        "style.css에 [hidden]{display:none !important} 가 없다 — "
        "display를 지정한 요소는 hidden으로 숨겨지지 않는다"
    )


def test_js_hidden_targets_exist_in_html():
    """app.js가 hidden을 조작하는 id는 index.html에 있어야 한다."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    used = set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)\.hidden", js))
    assert used <= ids, f"HTML에 없는 id를 숨기려 한다: {sorted(used - ids)}"


def test_graph_does_not_capture_pointer():
    """연관어 지도에서 setPointerCapture를 쓰면 안 된다.

    포인터를 캡처하면 그 뒤의 click·dblclick이 **캡처한 요소로 재타겟**된다.
    e.target이 항상 컨테이너(div#graph-svg)가 되어 closest('[data-node]')가
    null이 되고, 노드 클릭·더블클릭이 통째로 죽는다. 확대·이동을 넣으면서
    실제로 이렇게 깨졌다 — 끌기는 멀쩡히 동작해서 눈치채기 어려웠다.

    드래그 중 포인터가 요소 밖으로 나가는 건 window 리스너로 해결한다.
    """
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    # 호출만 본다 — "쓰면 안 된다"고 적어둔 주석까지 잡으면 안 된다
    assert not re.search(r"\.setPointerCapture\s*\(", js), (
        "setPointerCapture 호출이 다시 들어왔다 — 노드 클릭/더블클릭이 죽는다. "
        "window에 pointermove/pointerup을 붙여서 처리할 것"
    )


# ── 연관어 망 캐시 ──────────────────────────────────────────────────
def test_cache_reuses_and_evicts_and_invalidates():
    """캐시가 조용히 옛 답을 내주면 알아채기 가장 어려운 종류의 버그가 된다.

    '소상공인' 연관어 망은 3.6초가 걸려서 캐시 없이는 못 쓴다. 대신 파이프라인을
    돌린 뒤에도 옛 망이 나가면 안 되므로, 만료를 시간이 아니라 데이터 버전으로
    잡았다. 여기서 지키는 건 세 가지다 — 재사용, 용량 제한, 버전 바뀌면 폐기.
    """
    from web.cache import Cache

    c = Cache(max_entries=2)
    ver = [(1, 1)]
    c.stamp = lambda: ver[0]        # DB를 보지 않고 버전을 직접 쥔다
    calls: list[str] = []

    def make(v):
        def f():
            calls.append(v)
            return v
        return f

    assert c.get_or_call("a", make("a")) == "a"
    assert c.get_or_call("a", make("a")) == "a"
    assert calls == ["a"]                       # 두 번째는 계산하지 않는다

    # 용량을 넘기면 가장 오래 안 쓴 것부터 버린다
    c.get_or_call("b", make("b"))
    c.get_or_call("a", make("a"))               # a를 다시 써서 최신으로
    c.get_or_call("c", make("c"))               # 여기서 b가 밀려난다
    assert c.info()["entries"] == 2
    assert calls == ["a", "b", "c"]
    c.get_or_call("b", make("b"))
    assert calls == ["a", "b", "c", "b"]        # b는 다시 계산됐다

    # 수집·추출이 돌면(버전 변화) 전부 버린다
    ver[0] = (2, 1)
    c.get_or_call("a", make("a"))
    assert calls[-1] == "a"
    assert c.info()["entries"] == 1
