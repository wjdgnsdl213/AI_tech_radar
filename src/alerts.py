"""키워드 알림 — 지켜보는 말이 나오면 메일로 알린다.

★ 왜 필요한가
  다이제스트는 "이번 주 전체에서 중요한 것"을 다섯 칸에 담는다. 그런데 팀마다
  "이건 나오면 무조건 봐야 한다"는 말이 따로 있다(마이데이터, 가명정보처럼).
  그런 말은 그 주 상위 다섯에 못 들어도 놓치면 안 된다. 지면 경쟁과 무관하게
  따로 걸러 보낸다.

★ 무엇을 보는가
  · 통과 항목(kept)의 제목·요약
  · 법령·규제 항목 전부 — 관련도 필터를 안 태우므로 여기서도 그대로 본다
  · 그 주 급상승 목록에 올랐는지

★ 같은 걸 두 번 알리지 않는다
  이미 알린 항목 id를 체크포인트에 남긴다. 이게 없으면 매일 같은 기사를
  다시 보내게 되고, 며칠이면 아무도 안 읽는 메일이 된다.

설정: config.yaml의 alerts.keywords / .env의 MAIL_TO
실행:
  python -m src.alerts               # 새로 걸린 것만 알린다
  python -m src.alerts --dry-run     # 보내지 않고 내용만
  python -m src.alerts --all         # 이미 알린 것도 포함해 다시 본다
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import or_, select

from src.db import get_engine, items, load_config
from src.digest import SERVICE_NAME
from src.mailer import send

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)


def _state_path(cfg: dict[str, Any]) -> Path:
    return Path(cfg["backfill"]["checkpoint_dir"]) / "alert_state.json"


def load_state(p: Path) -> set[int]:
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text(encoding="utf-8")).get("sent", []))
    except Exception:
        return set()          # 깨졌으면 처음부터 — 알림이 멈추는 것보다 낫다


def save_state(p: Path, sent: set[int]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    # 무한정 쌓이지 않게 최근 것만 남긴다 (id는 단조 증가한다)
    keep = sorted(sent)[-5000:]
    p.write_text(json.dumps({"sent": keep, "at": datetime.now(timezone.utc).isoformat()},
                            ensure_ascii=False), encoding="utf-8")


def find_hits(cfg: dict[str, Any], keywords: list[str], days: int,
              seen: set[int]) -> dict[str, list[dict[str, Any]]]:
    """키워드별로 걸린 항목. 이미 알린 것은 뺀다."""
    reg_srcs = [n for n, sc in (cfg.get("sources") or {}).items()
                if isinstance(sc, dict) and sc.get("regulatory")]
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out: dict[str, list[dict[str, Any]]] = {}
    with get_engine().connect() as c:
        for kw in keywords:
            like = f"%{kw}%"
            rows = c.execute(
                select(items.c.id, items.c.title, items.c.url, items.c.source,
                       items.c.published_at, items.c.insight)
                .where(items.c.published_at >= since,
                       or_(items.c.kept.is_(True), items.c.source.in_(reg_srcs or ["_"])),
                       or_(items.c.title.ilike(like), items.c.summary.ilike(like)))
                .order_by(items.c.published_at.desc()).limit(20)).all()
            fresh = [r for r in rows if r.id not in seen]
            if fresh:
                out[kw] = [{"id": r.id, "title": r.title or "", "url": r.url or "",
                            "source": r.source, "insight": r.insight or "",
                            "is_reg": r.source in reg_srcs,
                            "published": str(r.published_at)[:10]} for r in fresh]
    return out


def render(hits: dict[str, list[dict[str, Any]]], labels: dict[str, str]) -> tuple[str, str]:
    n = sum(len(v) for v in hits.values())
    lines = [f"{SERVICE_NAME} 키워드 알림 — {n}건", ""]
    h = [f'<div style="font-family:system-ui,sans-serif;max-width:660px">',
         f'<h2 style="font-size:18px;margin:0 0 14px">📌 키워드 알림 · {n}건</h2>']
    for kw, rows in hits.items():
        lines.append(f"[{kw}] {len(rows)}건")
        h.append(f'<h3 style="font-size:15px;margin:18px 0 6px;color:#1d4ed8">'
                 f'{html_mod.escape(kw)} <span style="color:#64748b;font-weight:400">'
                 f'{len(rows)}건</span></h3>')
        for r in rows:
            tag = "⚖️ 법령" if r["is_reg"] else labels.get(r["source"], r["source"])
            lines.append(f"  · [{tag}] {r['title']}  {r['url']}")
            h.append(
                f'<div style="padding:8px 0;border-bottom:1px solid #e5e9f0">'
                f'<a href="{html_mod.escape(r["url"])}" '
                f'style="color:#0f172a;font-weight:600;text-decoration:none">'
                f'{html_mod.escape(r["title"])}</a>'
                f'<div style="font-size:12px;color:#64748b;margin-top:3px">'
                f'{html_mod.escape(tag)} · {r["published"]}</div>'
                + (f'<div style="font-size:13px;color:#334155;margin-top:5px;'
                   f'white-space:pre-line">{html_mod.escape(r["insight"])}</div>'
                   if r["insight"] else "")
                + '</div>')
        lines.append("")
    h.append("</div>")
    return "\n".join(lines), "".join(h)


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="키워드 알림")
    ap.add_argument("--dry-run", action="store_true", help="보내지 않고 내용만")
    ap.add_argument("--all", action="store_true", help="이미 알린 것도 포함")
    ap.add_argument("--days", type=int, default=0, help="며칠치를 볼까 (기본: config)")
    args = ap.parse_args()

    cfg = load_config()
    acfg = cfg.get("alerts") or {}
    keywords = [k for k in (acfg.get("keywords") or []) if str(k).strip()]
    if not acfg.get("enabled", True) or not keywords:
        print("알림 키워드가 없습니다 — config.yaml의 alerts.keywords를 채우세요.")
        return 0

    days = args.days or int(acfg.get("days", 7))
    sp = _state_path(cfg)
    seen = set() if args.all else load_state(sp)
    print(f"키워드 {len(keywords)}개 · 최근 {days}일 · 이미 알림 {len(seen)}건")

    hits = find_hits(cfg, keywords, days, seen)
    total = sum(len(v) for v in hits.values())
    if not total:
        print("  새로 걸린 항목이 없습니다.")
        return 0
    for kw, rows in hits.items():
        print(f"  [{kw}] {len(rows)}건")
        for r in rows[:3]:
            print(f"     · {r['title'][:56]}")

    text, html = render(hits, cfg.get("source_labels") or {})
    if args.dry_run:
        print(f"\n── 본문 미리보기 ──\n{text[:900]}")
        return 0

    subject = f"[{SERVICE_NAME}] 키워드 알림 — {total}건 ({', '.join(list(hits)[:3])}"
    subject += " 외)" if len(hits) > 3 else ")"
    if send(subject, text, html):
        save_state(sp, seen | {r["id"] for rows in hits.values() for r in rows})
        print(f"  {total}건 알림 · 상태 저장")
    else:
        # 못 보냈으면 상태를 남기지 않는다 — 다음 실행에서 다시 시도해야 한다
        print("  발송하지 못해 상태를 저장하지 않았습니다 (다음 실행에서 재시도)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
