"""메일 push — 주간 다이제스트를 **짧게** 보낸다.

★ 전문을 메일로 밀면 안 읽힌다 (PLAN §3)
    메일은 흐름 3줄 + 교집합 3건 + '전체 보기' 링크까지다. 짧은 메일이 웹으로
    유입시키는 구조이지, 메일 자체가 다이제스트인 게 아니다.
    push(메일)와 pull(웹)은 역할이 다르다 — push는 정기 노출, pull은 인용·기획.

★ 파이프라인을 이중화하지 않는다
    여기서 항목을 다시 고르지 않는다. digest.build()가 정한 구성을 그대로 받아
    mail=True로 렌더만 한다. 메일과 웹이 다른 항목을 보여주면 안 된다.

실행:
  python -m src.mailer --dry-run          # 보내지 않고 미리보기 (권장 첫 실행)
  python -m src.mailer --week 2026-W35
  python -m src.mailer --to me@example.com
"""

from __future__ import annotations

import argparse
import os
import smtplib
import sys
from email.message import EmailMessage
from email.utils import formataddr, formatdate

from dotenv import load_dotenv

from src.db import get_engine, init_db, load_config
from src.digest import (SERVICE_NAME, build, latest_week, render_html,
                        render_markdown, week_label)

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)


def _recipients(arg: str | None) -> list[str]:
    raw = arg or os.getenv("MAIL_TO", "")
    return [a.strip() for a in raw.split(",") if a.strip()]


def send(subject: str, text: str, html: str, to: list[str] | None = None) -> bool:
    """메일 한 통을 보낸다. 보냈으면 True, 설정이 없어 건너뛰면 False.

    ★ 미설정은 **실패가 아니라 건너뜀**이다 — 예외를 올리지 않고 False를 준다.
      메일은 선택 기능이라, 여기서 실패로 처리하면 주간 배치가 매주 실패로
      기록되고 그러면 작업 스케줄러의 실패 표시를 늘 무시하게 된다.

    다이제스트 발송과 키워드 알림이 같은 경로를 쓴다 — SMTP 분기(465=SSL /
    그 외=STARTTLS)와 본문 조립 순서를 두 곳에 두면 한쪽만 고치게 된다.
    """
    host = os.getenv("SMTP_HOST")
    to = to or _recipients(None)
    if not host or not to:
        print("SMTP_HOST 또는 MAIL_TO가 .env에 없습니다 - 메일 발송을 건너뜁니다.")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((SERVICE_NAME,
                              os.getenv("MAIL_FROM") or os.getenv("SMTP_USER", "")))
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    # 텍스트를 먼저 넣고 HTML을 대안으로 붙인다 - 순서가 반대면 텍스트만 보인다
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    port = int(os.getenv("SMTP_PORT", "587"))
    user, pw = os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD")
    try:
        # 465는 처음부터 SSL, 그 외(587 등)는 평문 연결 후 STARTTLS로 올린다
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        with server:
            if user and pw:
                server.login(user, pw)
            server.send_message(msg)
    except smtplib.SMTPException as exc:
        print(f"메일 발송 실패: {type(exc).__name__}: {exc}")
        return False
    except OSError as exc:
        print(f"SMTP 서버에 연결하지 못했습니다: {exc}")
        return False
    print(f"발송 완료 — 수신자 {len(to)}명 · {subject}")
    return True


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="주간 다이제스트 메일 발송 (짧게)")
    parser.add_argument("--week", default=None, help="주차 (예: 2026-W35)")
    parser.add_argument("--to", default=None, help="수신자 (쉼표 구분). 기본은 .env의 MAIL_TO")
    parser.add_argument("--dry-run", action="store_true", help="보내지 않고 내용만 출력")
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
        sys.exit(f"{week} 주차에 보낼 항목이 없습니다.")
    d["_mail_crossing"] = cfg.get("digest", {}).get("mail_crossing", 3)

    wcfg = cfg.get("web", {})
    base = os.getenv("WEB_BASE_URL", f"http://localhost:{wcfg.get('port', 8000)}")
    # SPA는 주차를 쿼리로 받는다(?week=…#digest). 서버 렌더 경로가 아니다.
    web_url = f"{base.rstrip('/')}/?week={week}#digest"

    # 제목도 사람이 읽는 표기로. 받은편지함에서 '2026-W35'는 아무 뜻이 없다.
    subject = f"[SAB Trend] {week_label(week)} — 교집합 {len(d['crossing'])}건"
    text = render_markdown(d, mail=True, web_url=web_url)
    html = render_html(d, mail=True, web_url=web_url)

    if not d.get("lead"):
        print("⚠ '이번 주 흐름'(L2)이 비어 있습니다 — insight를 먼저 돌리면 메일이 좋아집니다\n")

    if args.dry_run:
        print(f"제목: {subject}\n수신: {', '.join(_recipients(args.to)) or '(미설정)'}\n")
        print(text)
        print(f"\n--- HTML {len(html):,}자 생성됨 (실제 발송 시 이쪽이 본문) ---")
        return

    if not send(subject, text, html, _recipients(args.to)):
        return


if __name__ == "__main__":
    main()
