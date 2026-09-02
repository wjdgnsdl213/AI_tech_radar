"""주간 다이제스트 — 한 번 생성해 두 형태로 렌더한다.

파이프라인에서의 위치:
    … → filter → score → insight → **digest** → mailer(짧게) + web(전문)

★ 파이프라인을 이중화하지 않는다 (CLAUDE.md)
    메일용과 웹용을 따로 만들면 둘이 갈라진다. 여기서 **구성(어떤 항목이 어느
    섹션에 들어가는가)을 한 번만 정해** digests.body에 저장하고, 메일과 웹은
    그 하나를 각자 렌더만 한다. 렌더 함수는 여기 같이 두어 섹션 정의가 한 곳에 있게 한다.

섹션 순서에 프로젝트의 핵심 원칙이 들어 있다(PLAN §3):
    ① 이번 주 흐름      L2가 쓴 3줄 (insight.py)
    ② 🔥 교집합         3축 전부 → 2축. **교차 점수가 정렬 1순위**
    ③ 축별 TOP N        AI / 빅데이터 / 소상공인
    ④ ⚠️ 규제 알림      소스 기반 판정(meta.regulatory)
    ⑤ 📈 급상승 키워드   trend.py (F8)
    교집합을 맨 위에 두는 게 "팀이 필터링에 쓰는 시간을 없앤다"의 실현이다.

실행:
  python -m src.digest                    # 가장 최근 주차
  python -m src.digest --week 2026-W35
  python -m src.digest --format html      # 웹/메일용 HTML
  python -m src.digest --out reports/     # 파일로 저장
  python -m src.digest --mail             # 메일용 짧은 형태 미리보기
"""

from __future__ import annotations

import argparse
import html as html_mod
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from src.db import digests, get_engine, init_db, item_axes, items, load_config

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

# 서비스 이름. 메일 제목·다이제스트 머리글·웹이 모두 여기를 본다.
SERVICE_NAME = "SAB Trend"


def _bigrams(text: str) -> set[str]:
    """제목의 글자 2-gram 집합. 조사·띄어쓰기 차이에 둔감하다."""
    s = "".join(ch for ch in (text or "") if ch.isalnum())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def is_syndicated(title: str, seen: list[str], threshold: float = 0.22) -> bool:
    """지면에 이미 올라간 제목과 같은 사건인가.

    filter.py의 중복 제거(임베딩 군집, 컷 0.85)를 통과했는데도 같은 사건이 남는다.
    실측: '모두의 AI' 사업자 선정 기사 3건이 교집합 1~3위를 차지했다. 제목이
    매체마다 달라 임베딩 유사도가 컷 아래로 떨어진 경우다.

    다이제스트는 지면이 5칸뿐이라 한 사건이 3칸을 먹으면 그 주가 통째로 버려진다.
    그래서 여기서 한 번 더 거른다 — 이번엔 임베딩이 아니라 제목 글자 2-gram으로.
    같은 사건 기사는 고유명사를 공유하므로 표면형만으로도 잡히고, 임베딩 캐시
    파일에 의존하지 않아 다이제스트를 어디서든 만들 수 있다.

    자카드가 아니라 **포함도**(교집합 / 작은 쪽 크기)를 쓴다. 매체마다 제목 길이가
    크게 달라서 자카드는 분모가 커지며 값이 눌린다. 실측(모두의 AI 3건 + 무관 2건):
        같은 사건   자카드 0.184~0.452  포함도 0.350~0.700
        다른 사건   자카드 0.000~0.071  포함도 0.000~0.150
    포함도 쪽 골이 훨씬 넓다.

    ★ seen에는 **탈락시킨 제목도 넣는다.** filter.py에서 겪은 사슬 문제와 같다:
      롯데리아 기사 3건이 0-1=0.231, 1-2=0.269인데 0-2=0.071이었다. 탈락한 1을
      비교 대상에서 빼면 2가 0하고만 비교돼 살아남는다. 1을 남겨두면 사슬로 잡힌다.
      (같은 사건 0.071~0.700 / 다른 사건 0.000~0.150 → 컷 0.22)
    """
    b = _bigrams(title)
    if len(b) < 4:      # 너무 짧은 제목은 우연 일치가 나므로 건드리지 않는다
        return False
    for prev in seen:
        p = _bigrams(prev)
        if len(p) < 4:
            continue
        if len(b & p) / min(len(b), len(p)) >= threshold:
            return True
    return False


def latest_week(conn) -> str | None:
    return conn.execute(
        select(func.max(items.c.published_week)).where(items.c.kept.is_(True))
    ).scalar_one_or_none()


def build(week: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """주차별 다이제스트 구성을 만든다. 렌더는 하지 않는다."""
    dcfg = cfg.get("digest", {})
    per_axis = int(dcfg.get("top_per_axis", 5))
    top_crossing = int(dcfg.get("top_crossing", 5))
    labels = {ax: spec.get("label", ax) for ax, spec in cfg["axes"].items()}

    # ★ 규제 알림(F7)은 **소스 기반 판정**이다 — 관련도 필터를 태우지 않는다.
    #   개인정보위 고시가 임베딩 필터 판단으로 지면에서 빠지면 F7이 성립하지 않는다.
    #   ("규제가 확정되기 전에 안다"가 목적인데 필터가 걸러버리면 알 방법이 없다)
    #   그렇다고 filter.py에서 강제로 kept=True를 주면 안 된다 — kept의 뜻이
    #   "관련도 필터를 통과했다"에서 흐려지고 precision 측정이 오염된다.
    #   그래서 지면을 만들 때 별도 경로로 집어온다.
    #   소스 이름으로 거르는 이유: meta는 JSON이라 엔진마다 질의 문법이 다르다.
    reg_sources = [name for name, sc in (cfg.get("sources") or {}).items()
                   if isinstance(sc, dict) and sc.get("regulatory")]

    COLS = (items.c.id, items.c.title, items.c.summary, items.c.url,
            items.c.source, items.c.published_at, items.c.cross_score,
            items.c.relevance, items.c.insight, items.c.meta)

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            select(*COLS)
            .where(items.c.kept.is_(True), items.c.published_week == week)
            .order_by(items.c.cross_score.desc(), items.c.relevance.desc())
        ).all()
        reg_rows = conn.execute(
            select(*COLS)
            .where(items.c.source.in_(reg_sources), items.c.published_week == week)
            .order_by(items.c.published_at.desc())
        ).all() if reg_sources else []
        seen_ids = {r.id for r in rows}
        rows = list(rows) + [r for r in reg_rows if r.id not in seen_ids]
        # 축은 이 주차 항목 것만 읽는다. 조건 없이 읽으면 4만 7천 행 전수 스캔이고,
        # 웹은 페이지를 열 때마다 build()를 부르므로 요청마다 그 비용을 낸다.
        ids = [r.id for r in rows]
        axes_map: dict[int, list[str]] = {}
        if ids:
            for item_id, axis in conn.execute(
                    select(item_axes.c.item_id, item_axes.c.axis)
                    .where(item_axes.c.item_id.in_(ids))):
                axes_map.setdefault(item_id, []).append(axis)
        lead = conn.execute(
            select(digests.c.lead).where(digests.c.week == week)
        ).scalar_one_or_none()

    def pack(r) -> dict[str, Any]:
        return {
            "id": r.id, "title": r.title or "", "summary": r.summary or "",
            "url": r.url or "", "source": r.source,
            "published": str(r.published_at)[:10] if r.published_at else "",
            "cross_score": r.cross_score, "relevance": r.relevance,
            "insight": r.insight, "axes": sorted(axes_map.get(r.id, [])),
        }

    packed = [pack(r) for r in rows]
    # 규제 판정: 소스가 우선이고, meta 스탬프는 예전 수집분을 위한 보조다.
    regulatory = [p for p, r in zip(packed, rows)
                  if r.source in reg_sources
                  or (isinstance(r.meta, dict) and r.meta.get("regulatory"))]
    reg_ids = {p["id"] for p in regulatory}

    # 규제는 자기 섹션에서만 보여준다 — ⚠️와 축별 지면에 같은 고시가 두 번 오르면
    # 다섯 칸뿐인 지면이 낭비된다.
    used: set[int] = set(reg_ids)
    titles: list[str] = []      # 지면 안에서 같은 사건이 반복되지 않게 쓰는 기록

    def take(p: dict[str, Any]) -> bool:
        """이 항목을 지면에 올릴지. 올리면 True.

        ⚠️ 해설이 비었다고 빼지 않는다.
          L1이 "왜 중요한가"를 쓰던 시절에는 빈 값이 '팀 무관' 판정이라 빼는 게
          맞았다. 지금 L1은 기사 요약이고, 빈 값은 '원본 요약이 없어 요약할 수
          없음'을 뜻한다. 그걸로 빼면 이런 게 지면에서 사라진다:
              "소진공, 서류 제출 간소화 '기업마이데이터' 도입"  (요약 없음)
          네이버 뉴스에는 요약이 비는 기사가 흔하고, 제목만으로도 충분히 읽힌다.
        """
        if p["id"] in used:
            return False
        used.add(p["id"])
        dup = is_syndicated(p["title"], titles)
        # 탈락시킨 제목도 남긴다 — 사슬로 이어진 같은 사건을 잡기 위해서다
        titles.append(p["title"])
        return not dup

    # ② 교집합 — 3축을 먼저 채우고 모자라면 2축으로 내려간다
    crossing: list[dict[str, Any]] = []
    for need in (3, 2):
        for p in packed:
            if len(crossing) >= top_crossing:
                break
            if len(p["axes"]) == need and take(p):
                crossing.append(p)

    # ③ 축별 — 교집합에 올라간 항목은 빼서 같은 항목이 두 번 안 보이게 한다.
    #
    # 여기서는 cross_score가 아니라 relevance 순으로 고른다. 교차 점수로 정렬하면
    # 교집합 상한을 넘긴 3축 항목이 모든 축 섹션의 위를 먹어서 "AI 축 TOP 5"가
    # 사실상 "교집합 6~20위"가 된다. 교집합 우선 원칙은 ② 섹션이 이미 표현했으니,
    # 축 섹션은 그 축에서 가장 관련도 높은 것을 보여주는 게 맞다.
    by_axis: dict[str, list[dict[str, Any]]] = {}
    for ax in cfg["axes"]:
        picked = []
        for p in sorted(packed, key=lambda x: -(x["relevance"] or 0)):
            if len(picked) >= per_axis:
                break
            if ax in p["axes"] and take(p):
                picked.append(p)
        by_axis[ax] = picked

    # ⑤ 급상승 키워드 — item_keywords가 없으면(extract 미실행) 조용히 건너뛴다.
    # 다이제스트가 이것 때문에 안 나가면 안 된다.
    trending: list[dict[str, Any]] = []
    try:
        from src.trend import rising
        tcfg = cfg.get("trend", {})
        trending = rising(week, int(tcfg.get("compare_weeks", 4)),
                          int(tcfg.get("min_weekly_freq", 5)))[:int(tcfg.get("top_n", 15))]
    except Exception as exc:
        print(f"  ⚠ 급상승 키워드 생략: {type(exc).__name__}: {str(exc)[:80]}")

    return {
        "week": week,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lead": lead,
        "labels": labels,
        "total_kept": len(packed),
        "crossing": crossing,
        "by_axis": by_axis,
        "regulatory": regulatory[:per_axis],
        "trending": trending,
    }


def save(d: dict[str, Any]) -> None:
    """digests 테이블에 구성을 저장한다. lead(L2)는 insight.py가 이미 넣었으므로 보존."""
    engine = get_engine()
    body = {
        "crossing": [p["id"] for p in d["crossing"]],
        "by_axis": {ax: [p["id"] for p in ps] for ax, ps in d["by_axis"].items()},
        "regulatory": [p["id"] for p in d["regulatory"]],
        "trending": [t["keyword"] for t in d.get("trending", [])],
        "total_kept": d["total_kept"],
    }
    with engine.begin() as conn:
        exists = conn.execute(
            select(digests.c.week).where(digests.c.week == d["week"])).first()
        if exists:
            conn.execute(digests.update().where(digests.c.week == d["week"])
                         .values(body=body, generated_at=datetime.now(timezone.utc)))
        else:
            conn.execute(digests.insert().values(
                week=d["week"], body=body, lead=d.get("lead"),
                generated_at=datetime.now(timezone.utc)))


# ── 렌더 ────────────────────────────────────────────────────────────
_WEEK_IN_TEXT = re.compile(r"\b(?:(\d{4})-)?[Ww](\d{1,2})(?!\d)")


def humanize_weeks(text: str, year: int | None = None) -> str:
    """글 속의 'W35'·'2026-W35'를 '8월 4주차'로 바꾼다.

    ★ AI가 쓴 월간 리뷰에 'W34~W35 걸쳐'처럼 ISO 주차가 그대로 박혀 나온다.
      프롬프트에 재료를 '(2026-W35)'로 넣어 줬으니 모델이 그대로 따라 쓴 것이다.
      재료 쪽은 고쳤지만, 이미 저장된 글은 다시 만들지 않으면 안 바뀐다 —
      월간 리뷰 한 편에 sonnet 호출이 들어가므로 표기 하나 때문에 다시 돌릴 일이
      아니다. 그래서 **읽는 순간 바꾼다.**

    연도가 글에 없으면(대개 그렇다) year로 보완한다. 그것도 없으면 그냥 둔다 —
    엉뚱한 해의 주차로 바꾸느니 원문이 낫다.

    본문 안에서는 해가 자명하므로 'o월 o주차'까지만 쓴다.
    """
    def sub(m: "re.Match[str]") -> str:
        y = int(m.group(1)) if m.group(1) else year
        w = int(m.group(2))
        # 주차는 1~53이다. 벗어나면 주차 표기가 아니라 다른 무엇이다(W99 같은 제품명).
        if not y or not 1 <= w <= 53:
            return m.group(0)
        lab = week_label(f"{y:04d}-W{w:02d}")
        return lab.split("년 ", 1)[1] if "년 " in lab else lab
    return _WEEK_IN_TEXT.sub(sub, text or "")


def week_label(week: str) -> str:
    """'2026-W35' → '2026년 8월 4주차'. 사람이 읽는 표기.

    ISO 주차 문자열은 내부 키로는 좋지만(정렬·집계가 쉽다) 화면에 그대로 내면
    아무도 몇 월인지 모른다. 서버·메일·웹이 같은 규칙을 써야 하므로 여기 한 곳에 둔다.

    ★ 기준일은 월요일이 아니라 **목요일**이다.
      ISO 주차는 '그 주의 목요일이 속한 해'로 정의된다. 월요일로 잡으면
      2026-W01(월요일 2025-12-29)이 '2025년 12월 5주차'가 되어 연초 주차가
      전년으로 밀린다. 목요일(2026-01-01)로 잡으면 '2026년 1월 1주차'가 된다.
    """
    y, _, w = week.partition("-W")
    if not w.isdigit():
        return week
    try:
        thu = date.fromisocalendar(int(y), int(w), 4)     # 4 = 목요일
    except ValueError:
        return week
    return f"{thu.year}년 {thu.month}월 {(thu.day - 1) // 7 + 1}주차"


# 기존 이름을 쓰던 곳이 있어 별칭으로 남긴다
_week_title = week_label


def render_markdown(d: dict[str, Any], mail: bool = False,
                    web_url: str | None = None) -> str:
    """텍스트/마크다운. mail=True면 짧게 — 전문은 웹으로 유도한다(PLAN §3).

    전문을 메일로 밀면 안 읽힌다. 짧은 메일이 웹으로 유입시키는 구조다.
    """
    L = d["labels"]
    out: list[str] = [f"# 📡 {SERVICE_NAME} | {_week_title(d['week'])}", ""]

    if d.get("lead"):
        out += ["## 이번 주 흐름", "", d["lead"], ""]

    def block(p: dict[str, Any], n: int | None = None) -> list[str]:
        head = f"{n}. " if n else "- "
        axes = " · ".join(L.get(a, a) for a in p["axes"])
        lines = [f"{head}**{p['title']}**",
                 f"   {p['source']} · {p['published']} · [원문]({p['url']})"]
        if p.get("insight"):
            lines.append(f"   💡 {p['insight']}")
        lines.append(f"   ↳ 축: {axes}  (교차 점수 {p['cross_score']:.1f})")
        return lines + [""]

    if d["crossing"]:
        n_axes = len(d["crossing"][0]["axes"])
        out += [f"## 🔥 교집합 — {n_axes}축", ""]
        limit = int(d.get("_mail_crossing", 3)) if mail else len(d["crossing"])
        for i, p in enumerate(d["crossing"][:limit], 1):
            out += block(p, i)

    if d["regulatory"]:
        out += ["## ⚠️ 규제 알림", ""]
        for p in d["regulatory"]:
            out += block(p)

    if not mail:
        for ax, ps in d["by_axis"].items():
            if not ps:
                continue
            out += [f"## {L.get(ax, ax)}", ""]
            for p in ps:
                out += block(p)

    # 📈 급상승은 메일에도 넣는다 — 한 줄이라 짧고, '이번 주에 뭐가 떴나'를
    # 링크를 안 눌러도 알 수 있게 해준다
    if d.get("trending"):
        top = d["trending"][:8] if mail else d["trending"]
        out += ["## 📈 급상승 키워드", ""]
        out.append(" · ".join(
            f"**{t['keyword']}**({t['count']}건{'·신규' if t['is_new'] else ''})"
            for t in top))
        out.append("")

    if mail:
        out += ["---", ""]
        out.append(f"이번 주 통과 항목 {d['total_kept']}건."
                   + (f" 전체 보기 → {web_url}" if web_url else ""))
    return "\n".join(out)


def render_html(d: dict[str, Any], mail: bool = False,
                web_url: str | None = None) -> str:
    """웹·메일 공통 HTML. 메일 클라이언트를 고려해 인라인 스타일만 쓴다."""
    L = d["labels"]
    e = html_mod.escape
    css_card = ("margin:0 0 18px;padding:14px 16px;border:1px solid #e5e7eb;"
                "border-radius:8px;background:#fff")
    parts = [
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
        "'Malgun Gothic',sans-serif;max-width:720px;margin:0 auto;padding:24px;"
        'color:#111;background:#f9fafb">',
        f'<h1 style="font-size:20px;margin:0 0 4px">📡 SAB Trend</h1>',
        f'<div style="color:#6b7280;font-size:13px;margin-bottom:20px">'
        f'{e(_week_title(d["week"]))} · 통과 {d["total_kept"]}건</div>',
    ]
    if d.get("lead"):
        lead_html = "<br>".join(e(l) for l in d["lead"].splitlines() if l.strip())
        parts.append('<div style="background:#eff6ff;border-left:3px solid #3b82f6;'
                     'padding:12px 14px;margin:0 0 20px;font-size:14px;line-height:1.7">'
                     f'{lead_html}</div>')

    def card(p: dict[str, Any]) -> str:
        axes = " · ".join(L.get(a, a) for a in p["axes"])
        h = [f'<div style="{css_card}">',
             f'<a href="{e(p["url"])}" style="color:#1d4ed8;text-decoration:none;'
             f'font-weight:600;font-size:15px">{e(p["title"])}</a>',
             f'<div style="color:#6b7280;font-size:12px;margin-top:4px">'
             f'{e(p["source"])} · {e(p["published"])}</div>']
        if p.get("insight"):
            h.append('<div style="margin-top:8px;font-size:13px;line-height:1.6;'
                     f'color:#374151">💡 {e(p["insight"])}</div>')
        h.append('<div style="margin-top:8px;font-size:12px;color:#059669">'
                 f'↳ {e(axes)} · 교차 점수 {p["cross_score"]:.1f}</div></div>')
        return "".join(h)

    def section(title: str, ps: list[dict[str, Any]]) -> None:
        if not ps:
            return
        parts.append(f'<h2 style="font-size:15px;margin:24px 0 10px">{e(title)}</h2>')
        parts.extend(card(p) for p in ps)

    if d["crossing"]:
        n_axes = len(d["crossing"][0]["axes"])
        limit = int(d.get("_mail_crossing", 3)) if mail else len(d["crossing"])
        section(f"🔥 교집합 — {n_axes}축", d["crossing"][:limit])
    section("⚠️ 규제 알림", d["regulatory"])
    if not mail:
        for ax, ps in d["by_axis"].items():
            section(L.get(ax, ax), ps)
    if d.get("trending"):
        top = d["trending"][:8] if mail else d["trending"]
        chips = "".join(
            f'<span style="display:inline-block;margin:0 6px 6px 0;padding:4px 10px;'
            f'border:1px solid #d1d5db;border-radius:99px;font-size:13px">'
            f'{e(t["keyword"])} <span style="color:#6b7280">{t["count"]}</span>'
            + ('<span style="color:#dc2626"> 신규</span>' if t["is_new"] else "")
            + "</span>" for t in top)
        parts.append('<h2 style="font-size:15px;margin:24px 0 10px">📈 급상승 키워드</h2>'
                     f'<div>{chips}</div>')

    if mail and web_url:
        parts.append(f'<div style="margin-top:24px;text-align:center">'
                     f'<a href="{e(web_url)}" style="display:inline-block;padding:10px 20px;'
                     f'background:#1d4ed8;color:#fff;border-radius:6px;'
                     f'text-decoration:none;font-size:14px">전체 보기</a></div>')
    parts.append("</div>")
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="주간 다이제스트 생성")
    parser.add_argument("--week", default=None, help="주차 (예: 2026-W35)")
    parser.add_argument("--format", default="md", choices=["md", "html"])
    parser.add_argument("--mail", action="store_true", help="메일용 짧은 형태")
    parser.add_argument("--out", default=None, help="저장할 디렉토리 (예: reports/)")
    parser.add_argument("--no-save", action="store_true", help="DB에 구성을 저장하지 않음")
    args = parser.parse_args()

    cfg = load_config()
    init_db(get_engine())
    week = args.week
    if not week:
        with get_engine().connect() as conn:
            week = latest_week(conn)
    if not week:
        sys.exit("통과 항목이 없습니다. 먼저 python -m src.filter 를 실행하세요.")

    d = build(week, cfg)
    if not d["crossing"] and not any(d["by_axis"].values()):
        sys.exit(f"{week} 주차에 통과 항목이 없습니다.")
    d["_mail_crossing"] = cfg.get("digest", {}).get("mail_crossing", 3)

    if not args.no_save:
        save(d)

    web_url = (f"http://localhost:{cfg.get('web', {}).get('port', 8000)}"
               f"/?week={week}#digest")
    render = render_html if args.format == "html" else render_markdown
    text = render(d, mail=args.mail, web_url=web_url)

    if args.out:
        suffix = "html" if args.format == "html" else "md"
        kind = "mail" if args.mail else "full"
        path = Path(args.out) / f"digest_{week}_{kind}.{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"저장 → {path}")
    else:
        print(text)

    if not args.out:
        print(f"\n---\n구성: 교집합 {len(d['crossing'])} · 규제 {len(d['regulatory'])} · "
              + " · ".join(f"{d['labels'][a]} {len(p)}" for a, p in d["by_axis"].items()))
        if not d.get("lead"):
            print("⚠ '이번 주 흐름'이 비어 있습니다 — python -m src.insight --l2 --week "
                  f"{week} 를 먼저 실행하세요")


if __name__ == "__main__":
    main()
