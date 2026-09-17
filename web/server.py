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

import csv
import hashlib
import threading
import html as html_mod
import io
import os
import re
import sys
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import (FileResponse, HTMLResponse, PlainTextResponse,
                               Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.types import Scope
from sqlalchemy import func, select, text

from pathlib import Path

from src.db import digests, get_engine, item_axes, items, load_config
from src.digest import build, render_html

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

app = FastAPI(title="SAB Trend")


class VersionedStaticFiles(StaticFiles):
    """버전이 붙은 공개 정적 파일은 재사용하고 나머지는 재검증한다."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code in {200, 304}:
            versioned = bool(Request(scope).query_params.get("v"))
            response.headers["Cache-Control"] = "public, max-age=3600" if versioned else "no-cache"
        return response

# ── SPA ──
# 화면은 web/static의 SPA가 그린다(sobiz web/ 패턴). 서버는 JSON만 낸다.
# 예전 서버 렌더 화면은 /legacy 아래로 남겨뒀다 — SPA가 안 뜨는 환경에서도
# 다이제스트를 볼 수 있어야 하고, 메일 HTML 렌더와 같은 함수를 쓰기 때문이다.
from web.api import router as api_router  # noqa: E402

app.include_router(api_router)
from web.reviews_api import router as reviews_router
app.include_router(reviews_router)
from web.team_profile_api import router as team_profile_router
app.include_router(team_profile_router)


@app.on_event("startup")
def _warm_cache() -> None:
    """자주 열릴 키워드의 연관어 망을 미리 계산해 둔다.

    첫 사용자가 3~4초를 통째로 뒤집어쓰지 않게 하는 게 목적이다.
    추천 목록(=문서 빈도 상위)이 곧 실제로 많이 열리는 키워드다.
    데몬 스레드에서 돌고 실패해도 서버를 멈추지 않는다. WARM_CACHE=0으로 끈다.
    """
    from web.api import ego, home, suggest
    from web.cache import warm
    try:
        # ★ 인자를 전부 명시해야 한다. 라우트 함수를 파이썬 함수로 직접 부르면
        #   FastAPI가 값을 채워주지 않아 기본값이 Query 객체 그대로 넘어간다
        #   (실측: "type 'Query' is not supported"로 예열이 통째로 실패했다).
        #   화면이 보내는 값과 똑같이 넣어야 캐시 키도 맞는다.
        kws = [i["keyword"] for i in suggest(q="", limit=12)["items"]]
    except Exception as e:            # DB가 아직 없을 수 있다
        print(f"[cache] 예열 건너뜀: {e}", flush=True)
        return
    warm(kws, ego)

    # 홈은 첫 화면이다. 서버 기동 후 첫 요청은 연결 풀·TLS·모듈 임포트가 전부
    # 처음이라 10초 넘게 걸린다(실측 10.7초 → 이후 1.5초). 첫 사용자가 그걸
    # 뒤집어쓰지 않게 미리 한 번 부른다.
    def _warm_home() -> None:
        import time
        t0 = time.time()
        try:
            home()
            print(f"[cache] 홈 예열 {time.time() - t0:.1f}초", flush=True)
        except Exception as e:
            print(f"[cache] 홈 예열 실패: {e}", flush=True)

    threading.Thread(target=_warm_home, daemon=True, name="warm-home").start()
# 로고는 저장소 루트의 logo/ 에 있다. static으로 옮기면 원본 위치가 둘이 되므로
# 그 폴더를 그대로 붙인다.
_LOGO = Path(__file__).resolve().parents[1] / "logo"
if _LOGO.exists():
    app.mount("/logo", StaticFiles(directory=str(_LOGO)), name="logo")

_STATIC = Path(__file__).parent / "static"
if _STATIC.exists():
    app.mount("/static", VersionedStaticFiles(directory=str(_STATIC)), name="static")
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
        f"<nav><b>📡 SAB Trend</b><a href='/'>새 화면</a>"
        f"<a href='/legacy'>최신</a><a href='/legacy/search'>검색</a>"
        f"<a href='/legacy/weeks'>회차</a></nav>{body}</div></body></html>")


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
            f" <a href='/legacy/item/{r.id}' class=mut>·상세</a>{ins}</td>"
            f"<td class=ax>{E(ax)}</td>"
            f"<td class=n>{r.cross_score:.1f}</td>"
            f"<td class=n>{E(r.source)}</td></tr>")
    return "".join(out) + "</table></div>"


def _asset_version() -> str:
    """모든 JS·CSS 내용으로 만든 버전 문자열.

    ★ 이게 없으면 배포해도 사용자 화면이 안 바뀐다.
      실제로 겪었다 — HTML은 새로 받아왔는데 app.js는 캐시된 옛 버전이라
      새 화면 구조에 옛 스크립트가 붙어서, 제목이 비고 주차 선택이 빈 채로 떴다.
      파일이 바뀌면 URL이 바뀌므로 브라우저가 반드시 새로 받는다.
    """
    digest = hashlib.sha256()
    for asset in sorted(_STATIC.iterdir()):
        if asset.suffix in {".js", ".css"} and asset.is_file():
            digest.update(asset.name.encode("utf-8") + b"\0")
            digest.update(asset.read_bytes())
    return digest.hexdigest()[:16]


def _mask_url(url: str) -> str:
    """비밀번호를 가린 연결 문자열. 로그·진단 화면에 그대로 쓸 수 있게."""
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", url or "")


def db_status() -> dict[str, Any]:
    """지금 어느 DB에 붙어 있고 데이터가 있는지.

    ★ 이게 없어서 배포 후 한참 헤맸다.
      DATABASE_URL이 비어 있으면 database_url()이 조용히 sqlite로 떨어진다.
      그러면 빈 파일이 만들어지고 **오류 없이** 모든 화면이 0으로 보인다.
      "URL은 뜨는데 데이터가 안 나온다"의 정체가 이것인데, 어디에도 표시되지
      않아서 화면만 보고는 알 수 없었다.
    """
    from src.db import database_url

    url = database_url()
    out: dict[str, Any] = {
        "url": _mask_url(url),
        "dialect": url.split(":")[0],
        "from_env": bool(os.getenv("DATABASE_URL")),
    }
    try:
        with get_engine().connect() as c:
            out["items"] = c.execute(select(func.count()).select_from(items)).scalar_one()
            out["kept"] = c.execute(select(func.count()).select_from(items)
                                    .where(items.c.kept.is_(True))).scalar_one()
        out["ok"] = True
    except Exception as exc:
        out["ok"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"

    kw = Path("data/keywords.db")
    out["keywords_db"] = (f"{kw.stat().st_size / 1e6:.0f}MB" if kw.exists() else "없음")
    return out


def _is_deployed() -> bool:
    """클라우드에서 도는 중인가.

    Railway·Render·Fly는 각자 표식 환경변수를 넣는다. 어느 하나라도 있으면
    배포 환경으로 본다. 직접 켜고 싶으면 DEPLOYED=1을 준다.
    """
    return bool(os.getenv("DEPLOYED")
                or any(k.startswith(("RAILWAY_", "RENDER", "FLY_"))
                       for k in os.environ))


@app.on_event("startup")
def _log_db() -> None:
    """어디에 붙었는지 **부팅 로그에 남긴다.**

    ★ 배포 환경에서 sqlite로 떨어졌으면 **띄우지 않고 죽는다.**
      그 상태로 서버가 뜨면 화면은 멀쩡한데 데이터만 0이라, 무엇이 잘못됐는지
      알 방법이 없다(실제로 그렇게 한참 헤맸다). 조용히 틀린 것보다 크게
      실패하는 쪽이 낫다. 로컬에서는 그대로 두므로 개발에는 영향이 없다.
    """
    s = db_status()
    if not s["from_env"]:
        print("[db] ⚠ DATABASE_URL 환경변수가 없습니다 — config 기본값으로 떨어졌습니다.",
              flush=True)
        print("[db]   배포 환경이라면 데이터가 비어 보일 것입니다. 변수를 확인하세요.",
              flush=True)
    print(f"[db] {s['url']}", flush=True)
    if _is_deployed() and s["dialect"].startswith("sqlite"):
        print("[db] ✗ 배포 환경인데 sqlite로 떨어졌습니다. 서버를 시작하지 않습니다.",
              flush=True)
        print("[db]   → 서비스(프로젝트 아님)의 Variables에 DATABASE_URL을 넣고",
              flush=True)
        print("[db]     따옴표 없이 postgresql+psycopg://... 로 시작하는지 확인하세요.",
              flush=True)
        raise RuntimeError("DATABASE_URL이 설정되지 않았습니다 (sqlite로 떨어짐)")
    if s["ok"]:
        print(f"[db] 연결 OK — 전체 {s['items']:,}건 · 통과 {s['kept']:,}건", flush=True)
    else:
        print(f"[db] ✗ 연결 실패 — {s.get('error')}", flush=True)


@app.get("/api/diag")
def diag() -> dict[str, Any]:
    """설정이 먹었는지 확인하는 화면. 인증이 켜져 있으면 인증 뒤에 있다."""
    s = db_status()
    s["warm_cache"] = os.getenv("WARM_CACHE", "1") != "0"
    return s


@app.get("/", response_class=HTMLResponse)
def spa():
    """SPA 진입점. 정적 파일이 없으면 예전 화면으로 넘긴다."""
    index = _STATIC / "index.html"
    if not index.exists():
        return home()
    html = index.read_text(encoding="utf-8").replace("{{V}}", _asset_version())
    # HTML 자체는 캐시하지 않는다. 이걸 캐시하면 버전 문자열이 낡아서
    # 캐시 무효화 장치가 통째로 무력해진다.
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, must-revalidate"})


# ── 월간보고서 ──────────────────────────────────────────────────────
# SPA 안이 아니라 별도 주소로 낸다. 인쇄가 목적이라 사이드바·메뉴가 없어야 하고,
# 주소를 그대로 공유하면 상대도 같은 문서를 본다.
@app.get("/report", response_class=HTMLResponse)
def report_html(month: str = Query("")):
    from src.report import collect, latest_month, render_html
    m = month or latest_month()
    if not m:
        return HTMLResponse("<p>대상 월이 없습니다.</p>", status_code=404)
    return HTMLResponse(render_html(collect(m, load_config()), toolbar=True),
                        headers={"Cache-Control": "no-cache"})


@app.get("/report.md", response_class=PlainTextResponse)
def report_md(month: str = Query("")):
    from src.report import collect, latest_month, render_md
    m = month or latest_month()
    if not m:
        return PlainTextResponse("대상 월이 없습니다.", status_code=404)
    body = render_md(collect(m, load_config()))
    # 브라우저에서 바로 내려받게 한다 — 붙여넣기용이라 파일이 편하다
    return PlainTextResponse(body, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition":
                                      f'attachment; filename="report_{m}.md"'})


@app.get("/legacy", response_class=HTMLResponse)
def home():
    with get_engine().connect() as conn:
        week = conn.execute(select(func.max(digests.c.week))).scalar_one_or_none()
        if not week:
            week = conn.execute(select(func.max(items.c.published_week))
                                .where(items.c.kept.is_(True))).scalar_one_or_none()
    if not week:
        return page("SAB Trend", "<p class=mut>아직 데이터가 없습니다. "
                    "<code>python -m src.collect</code> 부터 실행하세요.</p>")
    return digest_view(week)


@app.get("/legacy/digest/{week}", response_class=HTMLResponse)
def digest_view(week: str):
    d = build(week, CFG)
    if not d["crossing"] and not any(d["by_axis"].values()):
        return page(week, f"<p class=mut>{E(week)} 주차에 항목이 없습니다.</p>")
    # 메일과 같은 렌더 함수를 쓴다 — 구성이 갈라지지 않게(파이프라인 이중화 금지)
    return page(f"{week} 다이제스트", render_html(d, mail=False))


@app.get("/legacy/weeks", response_class=HTMLResponse)
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
        body.append(f"<tr><td><a href='/legacy/digest/{E(w)}'>{E(w)}</a></td>"
                    f"<td class=n>{n:,}건</td></tr>")
    return page("회차", "".join(body) + "</table></div>")


def _search_conds(q: str, axis: str, since: str, until: str, kept_only: int):
    """SPA·CSV·기존 검색이 한국 시간 기준의 같은 조건을 공유한다."""
    from web.api import search_conds
    return search_conds(q, axis, since, until, kept_only)


@app.get("/legacy/search", response_class=HTMLResponse)
def search(q: str = Query("", description="제목·요약 검색어"),
           axis: str = Query("", description="축"),
           since: str = Query(""), until: str = Query(""),
           kept_only: int = Query(1), page_no: int = Query(1, alias="p")):
    where = _search_conds(q, axis, since, until, kept_only)
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
    qs = (f"q={E(q)}&axis={E(axis)}&since={E(since)}&until={E(until)}"
          f"&kept_only={kept_only}")

    nav = []
    if page_no > 1:
        nav.append(f"<a href='?q={E(q)}&axis={E(axis)}&since={E(since)}&until={E(until)}"
                   f"&kept_only={kept_only}&p={page_no - 1}'>← 이전</a>")
    if offset + PAGE < total:
        nav.append(f"<a href='?q={E(q)}&axis={E(axis)}&since={E(since)}&until={E(until)}"
                   f"&kept_only={kept_only}&p={page_no + 1}'>다음 →</a>")

    body = (f"<h2>검색</h2>{form}"
            f"<p class=mut>{total:,}건 중 {offset + 1}~{offset + len(rows)}"
            f" · <a href='/search.csv?{qs}'>CSV 내려받기</a>"
            f" <span style='font-size:12px'>(보고서 인용 목록용, 최대 2,000행)</span></p>"
            f"{rows_table(rows, axes)}"
            f"<p style='margin-top:16px;display:flex;gap:16px'>{''.join(nav)}</p>")
    return page("검색", body)


@app.get("/search.csv")
def search_csv(q: str = Query(""), axis: str = Query(""),
               since: str = Query(""), until: str = Query(""),
               kept_only: int = Query(1), limit: int = Query(2000)):
    """검색 결과를 CSV로. **이게 S4(기안·보고서 근거)의 마지막 한 걸음이다.**

    화면에서 눈으로 읽는 것과 보고서에 붙이는 건 다른 일이다. 표를 드래그해서
    옮기면 서식이 깨지고 링크가 날아간다. 인용 목록은 파일로 나가야 쓸 수 있다.

    화면과 같은 _search_conds를 쓴다 — 필터가 갈라지면 "화면에 보이는 것과
    받은 파일이 다르다"가 되고, 그러면 아무도 이 기능을 안 믿는다.
    """
    where = _search_conds(q, axis, since, until, kept_only)
    stmt = select(items.c.id, items.c.title, items.c.url, items.c.source,
                  items.c.published_at, items.c.cross_score, items.c.relevance,
                  items.c.insight)
    if where is not None:
        stmt = stmt.where(where)
    stmt = stmt.order_by(items.c.cross_score.desc(),
                         items.c.published_at.desc()).limit(max(1, min(limit, 5000)))
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).all()
        axes = _axes_map(conn, [r.id for r in rows])

    buf = io.StringIO()
    # utf-8-sig: BOM이 없으면 엑셀이 한글을 깨뜨린다
    buf.write("﻿")
    w = csv.writer(buf)
    w.writerow(["발행일", "제목", "출처", "축", "교차점수", "관련도", "AI해설", "링크"])
    from web.api import _kst_date
    for r in rows:
        w.writerow([
            _kst_date(r.published_at),
            r.title or "", r.source,
            " ".join(LABELS.get(a, a) for a in sorted(axes.get(r.id, []))),
            f"{r.cross_score:.1f}" if r.cross_score is not None else "",
            f"{r.relevance:.4f}" if r.relevance is not None else "",
            r.insight or "", r.url or "",
        ])
    buf.seek(0)
    stamp = datetime.now().strftime("%Y%m%d")
    name = f"trend_radar_{stamp}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/legacy/item/{item_id}", response_class=HTMLResponse)
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
    """DB에 닿는지만 본다.

    ★ count(*)를 쓰지 않는다. 헬스체크는 배포 중에도, 그 뒤로도 60초마다
      계속 불린다. 8만 5천 행을 매번 세는 건 확인하려는 것(=연결이 살아 있나)에
      비해 비싸고, 행이 늘수록 더 비싸진다. SELECT 1이면 충분하다.
      항목 수가 궁금하면 /api/stats 가 있다.
    """
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return "ok"


if __name__ == "__main__":
    import uvicorn
    w = CFG.get("web", {})
    uvicorn.run("web.server:app", host=w.get("host", "0.0.0.0"),
                port=int(w.get("port", 8000)))
