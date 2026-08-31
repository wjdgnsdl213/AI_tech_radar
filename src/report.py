"""월간보고서 — 한 달치를 인쇄용 HTML과 Markdown으로 뽑는다.

★ 왜 다이제스트와 따로 두나
  다이제스트는 "이번 주에 이걸 보세요"고, 보고서는 "지난 달에 무슨 일이 있었는지
  남겨두는 문서"다. 읽는 사람도 다르고(팀 내부 + 상급자) 인쇄를 전제한다.

★ 구성
  1. 이번 달 흐름          L3 월간 리뷰
  2. 법령·규제 변경         이 보고서의 고유한 값 — 다른 데서 정리해 주지 않는다
  3. 전월 대비 [예시]       ⚠ 아직 실데이터를 못 쓴다(아래)
  4. 과제 후보              여러 자료를 겹쳐야 보이는 것 — 이 보고서의 값
  5. 수집·처리 현황         근거 확인용이라 맨 뒤

★ 3이 아직 예시인 이유 — 지어낸 게 아니라 **못 쓰는** 것이다
  수집량 자체가 달마다 딴판이다. 2026-07은 과거분 임포트만, 08은 일일 수집을
  켠 뒤다(통과 640 → 1,828). 이 상태로 증감을 쓰면 "우리가 수집을 시작한 것"이
  "트렌드"로 보고된다. 주간 급상승에서 이미 겪은 함정이다.
  자리와 형태만 보여주고 문서 안에 예시임을 밝힌다.

★ 4는 요약이 아니라 해석이다
  앞 절들이 "무슨 일이 있었나"라면 여기는 "여러 자료를 겹치면 무엇이 보이나"다.
  법령 변경과 기사 흐름이 같은 방향을 가리킬 때가 후보가 된다.
  관찰(사실)과 함의(해석)를 줄로 갈라 적어서, 읽는 사람이 어디까지가 자료이고
  어디부터가 판단인지 알 수 있게 한다. 생성은 L3가 하고(insight.run_tasks)
  digests.body에 저장된다 — 보고서를 열 때마다 모델을 부르면 문서가 매번 달라진다.

실행:
  python -m src.report                      # 최근 달, 미리보기
  python -m src.report --month 2026-08 --out reports/
"""

from __future__ import annotations

import argparse
import html as html_mod
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from src.db import digests, get_engine, item_axes, items, load_config
from src.digest import SERVICE_NAME
from src.insight import _week_month

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

# 예시 절에 쓰는 값. 실데이터로 오해하지 않게 한 곳에 모아두고 문서에도 표시한다.
MOCK_DELTA = [
    ("마이데이터", 4.1, 1.2, "+2.9%p"),
    ("상권분석", 3.6, 2.9, "+0.7%p"),
    ("영향평가", 2.2, 0.4, "+1.8%p"),
    ("메타버스", 0.3, 1.9, "-1.6%p"),
]
MOCK_NOTE_3 = ("예시 수치입니다. 수집량이 달마다 크게 달라(2026-07은 과거분 임포트만, "
               "08은 일일 수집 시작) 지금 증감을 계산하면 '수집을 시작한 것'이 "
               "'트렌드'로 잡힙니다. 서너 달 안정적으로 수집된 뒤 실데이터로 바뀝니다.")


def month_label(m: str) -> str:
    return f"{m[:4]}년 {int(m[5:])}월" if len(m) == 7 and m[4] == "-" else m


def _ymd(s: str) -> str:
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 and s.isdigit() else "—"


def collect(month: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """보고서에 들어갈 것을 한 번에 모은다."""
    reg_srcs = [n for n, sc in (cfg.get("sources") or {}).items()
                if isinstance(sc, dict) and sc.get("regulatory")]
    labels = cfg.get("source_labels") or {}
    axis_label = {ax: s.get("label", ax) for ax, s in cfg["axes"].items()}

    with get_engine().connect() as c:
        weeks = sorted(w for (w,) in c.execute(
            select(items.c.published_week).distinct()) if w and _week_month(w) == month)
        wk = weeks or ["_"]

        row = c.execute(select(digests.c.lead, digests.c.body)
                        .where(digests.c.week == month)).first()
        lead = row[0] if row else None
        # 과제 후보는 L3가 만들어 digests.body에 넣어둔다. 보고서를 열 때마다
        # 모델을 부르면 문서가 열 때마다 달라진다 — 보고서는 고정돼야 한다.
        tasks = (row[1] or {}).get("tasks", []) if row and isinstance(row[1], dict) else []

        regs = c.execute(
            select(items.c.title, items.c.url, items.c.published_at,
                   items.c.meta, items.c.insight)
            .where(items.c.source.in_(reg_srcs or ["_"]),
                   items.c.published_week.in_(wk))
            .order_by(items.c.published_at.desc())).all()

        # 소스별 수집/통과. kept 합계를 SQL로 세면 방언마다 캐스팅이 달라지므로
        # 두 번 세는 쪽이 안전하다 — 월 단위라 행 수가 적다.
        health = []
        for s, n in c.execute(
                select(items.c.source, func.count())
                .where(items.c.published_week.in_(wk))
                .group_by(items.c.source).order_by(func.count().desc())):
            k = c.execute(select(func.count()).select_from(items)
                          .where(items.c.published_week.in_(wk),
                                 items.c.source == s,
                                 items.c.kept.is_(True))).scalar_one()
            health.append({"source": s, "label": labels.get(s, s),
                           "total": n, "kept": k})

        total = c.execute(select(func.count()).select_from(items)
                          .where(items.c.published_week.in_(wk))).scalar_one()
        kept = c.execute(select(func.count()).select_from(items)
                         .where(items.c.published_week.in_(wk),
                                items.c.kept.is_(True))).scalar_one()
        by_axis = c.execute(
            select(item_axes.c.axis, func.count(func.distinct(item_axes.c.item_id)))
            .select_from(item_axes.join(items, items.c.id == item_axes.c.item_id))
            .where(items.c.published_week.in_(wk), items.c.kept.is_(True))
            .group_by(item_axes.c.axis)).all()

    return {
        "month": month, "label": month_label(month), "weeks": weeks,
        "lead": lead, "tasks": tasks,
        "regs": [{"title": r.title or "", "url": r.url or "",
                  "date": str(r.published_at)[:10] if r.published_at else "",
                  "meta": r.meta if isinstance(r.meta, dict) else {},
                  "insight": r.insight or ""} for r in regs],
        "health": health, "total": total, "kept": kept,
        "by_axis": [{"axis": a, "label": axis_label.get(a, a), "n": n} for a, n in by_axis],
        "generated": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
    }


# ── Markdown ────────────────────────────────────────────────────────
def render_md(d: dict[str, Any]) -> str:
    out: list[str] = []
    add = out.append
    add(f"# {SERVICE_NAME} 월간보고서 — {d['label']}")
    add("")
    add(f"대상 기간 {d['label']} (주차 {len(d['weeks'])}개) · 생성 {d['generated']}")
    add("")
    add("## 1. 이번 달 흐름")
    add("")
    add(d["lead"] or "_이 달의 흐름 요약이 아직 생성되지 않았습니다._")
    add("")

    add(f"## 2. 법령·규제 변경 ({len(d['regs'])}건)")
    add("")
    if d["regs"]:
        add("| 발령일 | 소관부처 | 종류 | 구분 | 시행일 | 제목 |")
        add("|---|---|---|---|---|---|")
        for r in d["regs"]:
            m = r["meta"]
            add(f"| {r['date']} | {m.get('부처', '—')} | {m.get('종류', '—')} | "
                f"{m.get('제개정', '—')} | {_ymd(m.get('시행일자', ''))} | "
                f"[{r['title']}]({r['url']}) |")
        add("")
        for r in d["regs"]:
            if not r["insight"]:
                continue
            add(f"**{r['title']}**")
            add("")
            for line in r["insight"].splitlines():
                if line.strip():
                    add(line.strip())
            add("")
    else:
        add("_이 달에 수집된 법령·규제 항목이 없습니다._")
        add("")

    add("## 3. 전월 대비 주요 키워드  〔예시〕")
    add("")
    add(f"_{MOCK_NOTE_3}_")
    add("")
    add("| 키워드 | 이번 달 비중 | 전월 비중 | 변화 |")
    add("|---|---|---|---|")
    for k, a, b, delta in MOCK_DELTA:
        add(f"| {k} | {a}% | {b}% | {delta} |")
    add("")

    add("## 4. 과제 후보")
    add("")
    if d["tasks"]:
        add("_여러 자료가 같은 방향을 가리키는 것만 골랐습니다. "
            "**관찰**은 자료에서 확인된 사실, **함의**는 해석입니다._")
        add("")
        for i, t in enumerate(d["tasks"], 1):
            add(f"### {i}. {t.get('title', '')}")
            add("")
            add(f"- **관찰** {t.get('fact', '')}")
            add(f"- **함의** {t.get('mean', '')}")
            add(f"- **확인할 것** {t.get('ask', '')}")
            add("")
    else:
        add("_이 달 자료에서는 후보를 뽑지 못했습니다._")
        add("")

    add("## 5. 수집·처리 현황")
    add("")
    add(f"이 달 수집 {d['total']:,}건 중 필터 통과 {d['kept']:,}건 "
        f"({d['kept'] / max(d['total'], 1) * 100:.1f}%)")
    add("")
    add("| 소스 | 수집 | 통과 |")
    add("|---|---|---|")
    for h in d["health"]:
        add(f"| {h['label']} | {h['total']:,} | {h['kept']:,} |")
    add("")
    if d["by_axis"]:
        add("축별 통과: " + " · ".join(f"{a['label']} {a['n']:,}건" for a in d["by_axis"]))
        add("")
    add("---")
    add("")
    add(f"_{SERVICE_NAME} 자동 생성 문서 · 3·4절은 예시이며 실데이터가 아닙니다._")
    return "\n".join(out)


# ── 인쇄용 HTML ─────────────────────────────────────────────────────
CSS = """
:root{--ink:#0f172a;--mut:#64748b;--line:#d7dee8;--blue:#1d4ed8;--warn:#b45309}
*{box-sizing:border-box}
body{padding:34px 40px 60px;background:#fff;color:var(--ink);
  font:15px/1.72 Pretendard,'Malgun Gothic',system-ui,sans-serif;
  max-width:880px;margin:0 auto}
h1{font-size:25px;margin:0 0 4px;letter-spacing:-.5px}
h2{font-size:18px;margin:34px 0 10px;padding-bottom:7px;border-bottom:2px solid var(--ink)}
h3{font-size:15px;margin:18px 0 4px}
.sub{color:var(--mut);font-size:13.5px;margin:0 0 6px}
.lead{white-space:pre-line;background:#f6f8fc;border-left:3px solid var(--blue);
  padding:14px 16px;border-radius:0 6px 6px 0}
table{width:100%;border-collapse:collapse;margin:10px 0 4px;font-size:13.5px}
th,td{border:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}
th{background:#f2f5fa;font-weight:700;white-space:nowrap}
td.n{white-space:nowrap;font-variant-numeric:tabular-nums}
a{color:var(--ink);text-decoration:none}
.note{color:var(--mut);font-size:13px;margin:6px 0 10px;line-height:1.6}
.task{border:1px solid var(--line);border-radius:8px;padding:13px 16px;margin:0 0 11px}
.task-h{font-size:15.5px;font-weight:700;margin-bottom:8px}
.task-r{display:flex;gap:10px;margin:5px 0;font-size:13.5px;line-height:1.65}
.task-k{flex:none;width:62px;font-weight:700;color:var(--mut);font-size:12.5px;
  padding-top:2px}
.task-k.mean{color:var(--blue)}
.task-k.ask{color:var(--warn)}
.mocktag{display:inline-block;background:var(--warn);color:#fff;border-radius:4px;
  padding:1px 8px;font-size:12px;font-weight:700;margin-left:8px;vertical-align:middle}
.mock table{opacity:.72}
.reg-sum{white-space:pre-line;font-size:13.5px;color:#334155;margin:2px 0 14px 2px}
.foot{margin-top:40px;padding-top:12px;border-top:1px solid var(--line);
  color:var(--mut);font-size:12.5px}
.bar{margin:0 0 18px;display:flex;gap:8px}
.bar a{border:1px solid var(--line);border-radius:6px;padding:7px 13px;font-size:13.5px}
@media print{
  body{padding:0;font-size:11.5pt;max-width:none}
  h2{page-break-after:avoid}
  table,.task{page-break-inside:avoid}
  .noprint{display:none}
  a{color:#000}
}
"""


def render_html(d: dict[str, Any], toolbar: bool = False) -> str:
    e = html_mod.escape
    p: list[str] = [f"<style>{CSS}</style>"]
    if toolbar:
        p.append(f"<div class='bar noprint'>"
                 f"<a href='javascript:window.print()'>🖨 인쇄 · PDF로 저장</a>"
                 f"<a href='/report.md?month={e(d['month'])}'>⬇ Markdown</a></div>")
    p.append(f"<h1>{e(SERVICE_NAME)} 월간보고서 — {e(d['label'])}</h1>")
    p.append(f"<div class='sub'>대상 기간 {e(d['label'])} (주차 {len(d['weeks'])}개) · "
             f"생성 {e(d['generated'])}</div>")

    p.append("<h2>1. 이번 달 흐름</h2>")
    p.append(f"<div class='lead'>"
             f"{e(d['lead'] or '이 달의 흐름 요약이 아직 생성되지 않았습니다.')}</div>")

    p.append(f"<h2>2. 법령·규제 변경 "
             f"<span style='font-weight:400;color:#64748b'>{len(d['regs'])}건</span></h2>")
    if d["regs"]:
        p.append("<table><tr><th>발령일</th><th>소관부처</th><th>종류</th>"
                 "<th>구분</th><th>시행일</th><th>제목</th></tr>")
        for r in d["regs"]:
            m = r["meta"]
            p.append(f"<tr><td class='n'>{e(r['date'])}</td>"
                     f"<td>{e(m.get('부처', '—'))}</td><td>{e(m.get('종류', '—'))}</td>"
                     f"<td>{e(m.get('제개정', '—'))}</td>"
                     f"<td class='n'>{e(_ymd(m.get('시행일자', '')))}</td>"
                     f"<td><a href='{e(r['url'])}'>{e(r['title'])}</a></td></tr>")
        p.append("</table>")
        for r in d["regs"]:
            if r["insight"]:
                p.append(f"<h3>{e(r['title'])}</h3>"
                         f"<div class='reg-sum'>{e(r['insight'])}</div>")
    else:
        p.append("<p class='sub'>이 달에 수집된 법령·규제 항목이 없습니다.</p>")

    p.append("<h2>3. 전월 대비 주요 키워드<span class='mocktag'>예시</span></h2>")
    p.append(f"<p class='note'>{e(MOCK_NOTE_3)}</p>")
    p.append("<div class='mock'><table><tr><th>키워드</th><th>이번 달 비중</th>"
             "<th>전월 비중</th><th>변화</th></tr>")
    for k, a, b, delta in MOCK_DELTA:
        p.append(f"<tr><td>{e(k)}</td><td class='n'>{a}%</td>"
                 f"<td class='n'>{b}%</td><td class='n'>{e(delta)}</td></tr>")
    p.append("</table></div>")

    p.append("<h2>4. 과제 후보</h2>")
    if d["tasks"]:
        p.append("<p class='note'>여러 자료가 같은 방향을 가리키는 것만 골랐습니다. "
                 "<b>관찰</b>은 자료에서 확인된 사실, <b>함의</b>는 해석입니다.</p>")
        for i, tk in enumerate(d["tasks"], 1):
            p.append(f"<div class='task'><div class='task-h'>{i}. "
                     f"{e(tk.get('title', ''))}</div>"
                     f"<div class='task-r'><span class='task-k'>관찰</span>"
                     f"<span>{e(tk.get('fact', ''))}</span></div>"
                     f"<div class='task-r'><span class='task-k mean'>함의</span>"
                     f"<span>{e(tk.get('mean', ''))}</span></div>"
                     f"<div class='task-r'><span class='task-k ask'>확인할 것</span>"
                     f"<span>{e(tk.get('ask', ''))}</span></div></div>")
    else:
        p.append("<p class='sub'>이 달 자료에서는 후보를 뽑지 못했습니다.</p>")

    p.append("<h2>5. 수집·처리 현황</h2>")
    p.append(f"<p class='sub'>이 달 수집 {d['total']:,}건 중 필터 통과 "
             f"{d['kept']:,}건 ({d['kept'] / max(d['total'], 1) * 100:.1f}%)</p>")
    p.append("<table><tr><th>소스</th><th>수집</th><th>통과</th></tr>")
    for h in d["health"]:
        p.append(f"<tr><td>{e(h['label'])}</td><td class='n'>{h['total']:,}</td>"
                 f"<td class='n'>{h['kept']:,}</td></tr>")
    p.append("</table>")
    if d["by_axis"]:
        p.append("<p class='sub'>축별 통과: " + " · ".join(
            f"{e(a['label'])} {a['n']:,}건" for a in d["by_axis"]) + "</p>")

    p.append(f"<div class='foot'>{e(SERVICE_NAME)} · 자동 생성 문서 · "
             f"{e(d['generated'])}<br>3·4절은 예시이며 실데이터가 아닙니다.</div>")
    return "".join(p)


def latest_month() -> str:
    with get_engine().connect() as c:
        last = c.execute(select(func.max(items.c.published_at))
                         .where(items.c.kept.is_(True))).scalar_one_or_none()
    return f"{last.year:04d}-{last.month:02d}" if last else ""


def available_months() -> list[str]:
    """보고서를 뽑을 수 있는 달. 월간 리뷰가 있는 달을 먼저 둔다."""
    with get_engine().connect() as c:
        weeks = [w for (w,) in c.execute(select(items.c.published_week).distinct()
                                         .where(items.c.kept.is_(True))) if w]
    return sorted({_week_month(w) for w in weeks if _week_month(w)}, reverse=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="월간보고서")
    ap.add_argument("--month", default=None, help="대상 월 (예: 2026-08)")
    ap.add_argument("--format", choices=["md", "html", "both"], default="both")
    ap.add_argument("--out", default=None, help="저장할 디렉토리 (예: reports/)")
    args = ap.parse_args()

    cfg = load_config()
    month = args.month or latest_month()
    if not month:
        print("대상 월을 정할 수 없습니다.")
        return 1
    d = collect(month, cfg)
    print(f"{d['label']} — 주차 {len(d['weeks'])}개 · 수집 {d['total']:,} · "
          f"통과 {d['kept']:,} · 법령 {len(d['regs'])}건")

    outs: dict[str, str] = {}
    if args.format in ("md", "both"):
        outs[f"report_{month}.md"] = render_md(d)
    if args.format in ("html", "both"):
        outs[f"report_{month}.html"] = render_html(d)

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, body in outs.items():
            (out_dir / name).write_text(body, encoding="utf-8")
            print(f"  저장 → {out_dir / name}")
    else:
        print()
        print(next(iter(outs.values()))[:1600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
