"""법령 본문 보충 — 목록에서 받은 항목에 '무엇이 왜 바뀌었나'를 채운다.

★ 왜 별도 단계인가
  수집 어댑터(lawgokr)는 목록 API만 부른다. 목록에는 이름·부처·발령일밖에 없어서
  "개인정보 처리 방법에 관한 고시 일부개정"이 **무엇을 바꿨는지** 알 수 없다.
  본문은 항목마다 한 번씩 더 불러야 하는데, 어댑터 안에서 하면 이미 받아둔
  항목까지 매일 다시 부르게 된다(정부 API에 하루 수십 번씩 헛질의).
  그래서 "본문이 아직 없는 것만" 골라 채우는 단계를 따로 둔다.

★ 무엇을 담는가
  법제처 응답에는 **제개정이유**가 있다 — 정부가 직접 쓴 "왜 바꿨고 무엇이
  달라지나"다. 이게 요약의 재료로 가장 좋다. 없으면 조문 앞부분으로 대신한다.
  별표는 담지 않는다 — 서식 파일 목록이라 3만 자가 넘는데 내용이 없다.

실행:
  python -m src.law_detail            # 본문 없는 것만
  python -m src.law_detail --all      # 전부 다시
  python -m src.law_detail --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests
from dotenv import load_dotenv
from sqlalchemy import bindparam, select, update

from src.db import get_engine, items, load_config

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

SERVICE = "http://www.law.go.kr/DRF/lawService.do"
MAX_CHARS = 1200          # 요약 재료로 이 이상은 필요 없다 (제개정이유가 보통 200~1700자)
SOURCE = "law_go_kr"


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join(t.strip() for t in node.itertext() if t and t.strip())


def fetch_detail(target: str, seq: str, oc: str, ua: str,
                 timeout: float = 30) -> str:
    """제개정이유 우선, 없으면 조문 앞부분. 실패하면 빈 문자열."""
    key = "ID" if target == "admrul" else "MST"
    r = requests.get(SERVICE, params={"OC": oc, "target": target, key: seq, "type": "XML"},
                     headers={"User-Agent": ua}, timeout=timeout)
    r.raise_for_status()
    root = ET.fromstring(r.content)

    reason = _text(root.find("제개정이유"))
    if reason:
        # '◇ 제ㆍ개정이유' 같은 머리표는 화면에서 군더더기다
        reason = reason.replace("◇", " ").replace("[일부개정]", " ")
    body = reason
    if len(body) < 60:
        arts = [_text(n) for n in root.iter() if n.tag in ("조문내용", "조문")]
        body = (body + " " + " ".join(arts)).strip()
    return " ".join(body.split())[:MAX_CHARS]


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="법령 본문 보충")
    ap.add_argument("--all", action="store_true", help="이미 채운 것도 다시 받는다")
    ap.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않는다")
    ap.add_argument("--limit", type=int, default=0, help="처리 상한")
    args = ap.parse_args()

    cfg = load_config()
    scfg = cfg.get("sources", {}).get(SOURCE, {})
    oc = os.getenv("LAW_GO_KR_OC") or scfg.get("oc") or "test"
    ua = (cfg.get("backfill", {}).get("user_agent", "sab-trend/0.1")
          .replace("{contact}", os.getenv("CONTACT_EMAIL", "unknown")))
    interval = float(scfg.get("request_interval", 0.4))

    engine = get_engine()
    with engine.connect() as c:
        rows = c.execute(
            select(items.c.id, items.c.title, items.c.summary, items.c.meta)
            .where(items.c.source == SOURCE)
            .order_by(items.c.published_at.desc())
        ).all()

    # 본문이 들어간 것은 요약이 길다. 목록만 받은 건 메타데이터 한 줄(보통 80자 미만).
    todo = [r for r in rows if args.all or len(r.summary or "") < 200]
    if args.limit:
        todo = todo[:args.limit]
    print(f"법령 본문 보충 — 전체 {len(rows)}건 중 대상 {len(todo)}건  (OC={oc})")
    if not todo:
        print("  채울 것이 없습니다.")
        return 0

    updates: list[dict[str, Any]] = []
    fail = 0
    for i, r in enumerate(todo, 1):
        meta = r.meta if isinstance(r.meta, dict) else {}
        target = meta.get("target") or ""
        seq = str(r.id)
        # source_id가 'admrul:2100000284146' 형태다 — 여기서 일련번호를 얻는다
        with engine.connect() as c:
            sid = c.execute(select(items.c.source_id)
                            .where(items.c.id == r.id)).scalar_one()
        if ":" in sid:
            target, seq = sid.split(":", 1)
        if target not in ("admrul", "law"):
            continue
        time.sleep(interval)
        try:
            body = fetch_detail(target, seq, oc, ua)
        except Exception as exc:
            fail += 1
            print(f"  ✗ {r.title[:34]} — {type(exc).__name__}: {exc}")
            continue
        if not body:
            continue
        # 목록에서 만든 메타데이터 한 줄을 앞에 남긴다 — 부처·시행일은 계속 쓸모 있다
        head = (r.summary or "").strip()
        merged = f"{head}\n{body}" if head and head not in body else body
        updates.append({"b_id": r.id, "b_sum": merged[:MAX_CHARS + 200]})
        if i <= 3 or i % 20 == 0:
            print(f"  [{i}/{len(todo)}] {r.title[:36]} → {len(body)}자")

    print(f"\n  받아온 {len(updates)}건 · 실패 {fail}건")
    if args.dry_run:
        if updates:
            print(f"\n  예시:\n  {updates[0]['b_sum'][:300]}")
        return 0
    if updates:
        with engine.begin() as c:
            c.execute(update(items).where(items.c.id == bindparam("b_id"))
                      .values(summary=bindparam("b_sum")), updates)
        print(f"  DB 반영 완료")
    print("\n  다음: python -m src.insight --reg")
    return 1 if fail and not updates else 0


if __name__ == "__main__":
    raise SystemExit(main())
