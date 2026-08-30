"""웹 — 다이제스트 아카이브 + 검색 (F6, 시나리오 S2·S4).

★ 이게 숨은 킬러다 (PLAN §2)
    공공기관 팀은 기안·보고서를 계속 쓰는데 매번 근거를 새로 찾는다.
    축적 DB에서 "소상공인 AI 도입 현황" 근거 20건을 링크째 뽑아주면 그것만으로
    도구가 살아남는다. 그래서 /search는 **인용 목록을 바로 뽑을 수 있어야** 한다.

★ 표 중심, 그래프 없음
    sobiz에서 오래 걸린 건 D3 그래프였지 표·검색이 아니다(PLAN §9).
    연관어 그래프는 2차로 미룬다.

경로:
    /                 최신 다이제스트
    /digest/{week}    회차 아카이브
    /search           키워드·축·기간 필터 → 표  ★ S2·S4
    /item/{id}        상세 (원문 링크 + AI 해설 + 같은 축 관련 항목)

실행:
  uvicorn web.server:app --reload
  python -m web.server            # config의 host/port로 실행
"""

from __future__ import annotations

import html as html_mod
import sys
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import String, and_, cast, func, or_, select

from src.db import digests, get_engine, item_axes, items, load_config
from src.digest import build, render_html

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

app = FastAPI(title="AI·빅데이터 트렌드 레이더")
CFG = load_config()
LABELS = {ax: spec.get("label", ax) for ax, spec in CFG["axes"].items()}
PAGE = int(CFG.get("web", {}).get("page_size", 50))

E = html_mod.escape
CSS = """
:root{--bg:#f9fafb;--fg:#111;--mut:#6b7280;--line:#e5e7eb;--card:#fff;--acc:#1d4ed8}
@media(prefers-color-scheme:dark){:root{--bg:#0b0f17;--fg:#e5e7eb;--mut:#9ca3af;
--line:#1f2937;--card:#111827;--acc:#60a5fa}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,
BlinkMacSystemFont,'Segoe UI','Malgun Gothic',sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:24px 20px 64px}
a{color:var(--acc)}
nav{display:flex;gap:16px;align-items:baseline;margin-bottom:24px;
border-bottom:1px solid var(--line);padding-bottom:12px}
nav b{font-size:16px}
form.s{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px}
input,select,button{padding:8px 10px;border:1px solid var(--line);border-radius:6px;
background:var(--card);color:var(--fg);font-size:14px}
button{background:var(--acc);color:#fff;border:0;cursor:pointer}
.tblwrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:14px;min-width:720px}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--mut);font-weight:600;white-space:nowrap}
td.n{white-space:nowrap;color:var(--mut);font-size:13px}
.ax{font-size:11px;color:#059669;white-space:nowrap}
.mut{color:var(--mut)}
.ins{margin-top:6px;font-size:13px;color:var(--mut)}
"""


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang=ko><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{E(title)}</title><style>{CSS}</style><body><div class=wrap>"
        f"<nav><b>📡 트렌드 레이더</b><a href='/'>최신</a><a href='/search'>검색</a>"
        f"<a href='/weeks'>회차</a></nav>{body}</div></body></html>")


def _axes_map(conn, ids: list[int]) -> dict[int, list[str]]:
    if not ids:
        return {}
    out: dict[int, list[str]] = {}
    for item_id, axis in conn.execute(
            select(item_axes.c.item_id, item_axes.c.axis)
            .where(item_axes.c.item_id.in_(ids))):
        out.setdefault(item_id, []).append(axis)
    return out


def rows_table(rows: list[Any], axes: dict[int, list[str]]) -> str:
    """검색 결과 표. 보고서에 붙일 인용 목록을 그대로 뽑을 수 있어야 한다."""
    if not rows:
        return "<p class=mut>결과가 없습니다.</p>"
    out = ["<div class=tblwrap><table><tr><th>발행일</th><th>제목</th><th>축</th>"
           "<th>교차</th><th>출처</th></tr>"]
    for r in rows:
        ax = " · ".join(LABELS.get(a, a) for a in sorted(axes.get(r.id, [])))
        ins = f"<div class=ins>💡 {E(r.insight)}</div>" if getattr(r, "insight", None) else ""
        out.append(
            f"<tr><td class=n>{E(str(r.published_at)[:10] if r.published_at else '-')}</td>"
            f"<td><a href='{E(r.url or '#')}' target=_blank rel=noopener>{E(r.title or '')}</a>"
            f" <a href='/item/{r.id}' class=mut>·상세</a>{ins}</td>"
            f"<td class=ax>{E(ax)}</td>"
            f"<td class=n>{r.cross_score:.1f}</td>"
            f"<td class=n>{E(r.source)}</td></tr>")
    return "".join(out) + "</table></div>"


@app.get("/", response_class=HTMLResponse)
def home():
    with get_engine().connect() as conn:
        week = conn.execute(select(func.max(digests.c.week))).scalar_one_or_none()
        if not week:
            week = conn.execute(select(func.max(items.c.published_week))
                                .where(items.c.kept.is_(True))).scalar_one_or_none()
    if not week:
        return page("트렌드 레이더", "<p class=mut>아직 데이터가 없습니다. "
                    "<code>python -m src.collect</code> 부터 실행하세요.</p>")
    return digest_view(week)


@app.get("/digest/{week}", response_class=HTMLResponse)
def digest_view(week: str):
    d = build(week, CFG)
    if not d["crossing"] and not any(d["by_axis"].values()):
        return page(week, f"<p class=mut>{E(week)} 주차에 항목이 없습니다.</p>")
    # 메일과 같은 렌더 함수를 쓴다 — 구성이 갈라지지 않게(파이프라인 이중화 금지)
    return page(f"{week} 다이제스트", render_html(d, mail=False))


@app.get("/weeks", response_class=HTMLResponse)
def weeks():
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(items.c.published_week, func.count().label("n"))
            .where(items.c.kept.is_(True), items.c.published_week.isnot(None))
            .group_by(items.c.published_week)
            .order_by(items.c.published_week.desc()).limit(60)).all()
    body = ["<h2>회차 아카이브</h2><div class=tblwrap><table>"
            "<tr><th>주차</th><th>통과 항목</th></tr>"]
    for w, n in rows:
        body.append(f"<tr><td><a href='/digest/{E(w)}'>{E(w)}</a></td>"
                    f"<td class=n>{n:,}건</td></tr>")
    return page("회차", "".join(body) + "</table></div>")


@app.get("/search", response_class=HTMLResponse)
def search(q: str = Query("", description="제목·요약 검색어"),
           axis: str = Query("", description="축"),
           since: str = Query(""), until: str = Query(""),
           kept_only: int = Query(1), page_no: int = Query(1, alias="p")):
    conds = []
    if q:
        conds.append(or_(items.c.title.contains(q), items.c.summary.contains(q)))
    if kept_only:
        conds.append(items.c.kept.is_(True))
    if since:
        conds.append(cast(items.c.published_at, String) >= since)
    if until:
        conds.append(cast(items.c.published_at, String) <= until + "T23:59:59")
    if axis in CFG["axes"]:
        conds.append(items.c.id.in_(
            select(item_axes.c.item_id).where(item_axes.c.axis == axis)))

    where = and_(*conds) if conds else None
    stmt = select(items.c.id, items.c.title, items.c.url, items.c.source,
                  items.c.published_at, items.c.cross_score, items.c.insight)
    cnt = select(func.count()).select_from(items)
    if where is not None:
        stmt, cnt = stmt.where(where), cnt.where(where)
    offset = max(0, (page_no - 1) * PAGE)
    stmt = stmt.order_by(items.c.cross_score.desc(),
                         items.c.published_at.desc()).limit(PAGE).offset(offset)

    with get_engine().connect() as conn:
        rows = conn.execute(stmt).all()
        total = conn.execute(cnt).scalar_one()
        axes = _axes_map(conn, [r.id for r in rows])

    opts = "".join(f"<option value='{a}'{' selected' if a == axis else ''}>"
                   f"{E(LABELS[a])}</option>" for a in CFG["axes"])
    form = (f"<form class=s method=get>"
            f"<input name=q value='{E(q)}' placeholder='키워드' size=24>"
            f"<select name=axis><option value=''>전체 축</option>{opts}</select>"
            f"<input name=since value='{E(since)}' placeholder='YYYY-MM-DD' size=11>"
            f"<input name=until value='{E(until)}' placeholder='YYYY-MM-DD' size=11>"
            f"<label class=mut><input type=checkbox name=kept_only value=1"
            f"{' checked' if kept_only else ''}> 통과분만</label>"
            f"<button>검색</button></form>")

    nav = []
    if page_no > 1:
        nav.append(f"<a href='?q={E(q)}&axis={E(axis)}&since={E(since)}&until={E(until)}"
                   f"&kept_only={kept_only}&p={page_no - 1}'>← 이전</a>")
    if offset + PAGE < total:
        nav.append(f"<a href='?q={E(q)}&axis={E(axis)}&since={E(since)}&until={E(until)}"
                   f"&kept_only={kept_only}&p={page_no + 1}'>다음 →</a>")

    body = (f"<h2>검색</h2>{form}"
            f"<p class=mut>{total:,}건 중 {offset + 1}~{offset + len(rows)}</p>"
            f"{rows_table(rows, axes)}"
            f"<p style='margin-top:16px;display:flex;gap:16px'>{''.join(nav)}</p>")
    return page("검색", body)


@app.get("/item/{item_id}", response_class=HTMLResponse)
def item_view(item_id: int):
    with get_engine().connect() as conn:
        r = conn.execute(select(items).where(items.c.id == item_id)).first()
        if not r:
            return page("없음", "<p class=mut>해당 항목이 없습니다.</p>")
        my_axes = [a for (a,) in conn.execute(
            select(item_axes.c.axis).where(item_axes.c.item_id == item_id))]
        rel = []
        if my_axes:
            rel = conn.execute(
                select(items.c.id, items.c.title, items.c.url, items.c.source,
                       items.c.published_at, items.c.cross_score, items.c.insight)
                .where(items.c.kept.is_(True), items.c.id != item_id,
                       items.c.id.in_(select(item_axes.c.item_id)
                                      .where(item_axes.c.axis.in_(my_axes))))
                .order_by(items.c.cross_score.desc()).limit(10)).all()
        rel_axes = _axes_map(conn, [x.id for x in rel])

    ax = " · ".join(LABELS.get(a, a) for a in sorted(my_axes))
    ins = (f"<div style='background:#eff6ff20;border-left:3px solid var(--acc);"
           f"padding:10px 14px;margin:14px 0'>💡 {E(r.insight)}</div>"
           if r.insight else "")
    body = (f"<h2>{E(r.title or '')}</h2>"
            f"<p class=mut>{E(r.source)} · {E(str(r.published_at)[:10] if r.published_at else '-')}"
            f" · <a href='{E(r.url or '#')}' target=_blank rel=noopener>원문 보기</a></p>"
            f"<p>{E(r.summary or '')}</p>{ins}"
            f"<p class=ax>축: {E(ax) or '없음'} · 교차 점수 "
            f"{r.cross_score if r.cross_score is not None else '-'} · 관련도 "
            f"{f'{r.relevance:.4f}' if r.relevance is not None else '-'}</p>"
            f"<h3 style='margin-top:28px'>같은 축의 관련 항목</h3>"
            f"{rows_table(rel, rel_axes)}")
    return page(r.title or "항목", body)


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    with get_engine().connect() as conn:
        n = conn.execute(select(func.count()).select_from(items)).scalar_one()
    return f"ok items={n}"


if __name__ == "__main__":
    import uvicorn
    w = CFG.get("web", {})
    uvicorn.run("web.server:app", host=w.get("host", "0.0.0.0"),
                port=int(w.get("port", 8000)))
