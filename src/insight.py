"""AI 해설 레이어 — L1 항목 해설 / L2 주간 요약.

파이프라인에서의 위치:
    … → filter → score → **insight** → digest → 메일/웹

두 층위를 만든다(PLAN §4):
    L1  항목별 "우리 팀에 왜 중요한가" 1~2문장. 교차 점수 상위 N건. Haiku
    L2  "이번 주 흐름" 3줄 + 섹션 리드. 주 1회. Sonnet

★ 반드시 지키는 두 원칙 (CLAUDE.md)
    ① 근거 없는 말을 만들지 않는다
       제목·요약에 없는 사실을 쓰지 못하게 프롬프트에 못박고, 해설은 항상 원문
       링크와 함께 렌더된다. 환각 한 번이면 팀 신뢰가 즉사한다.
    ② AI는 설명까지, 판단은 사람이
       "도입해야 한다" 같은 결론은 내지 않는다. 무엇이 일어났고 왜 우리와 관련
       있는지까지가 AI 몫이다.

★ LLM이 죽어도 다이제스트는 나가야 한다
    해설은 부가 정보다. API 장애·키 누락·rate limit으로 실패하면 그 항목만
    해설 없이 두고 파이프라인은 계속 간다(config insight.fail_open).

실행:
  python -m src.insight                    # L1 + L2
  python -m src.insight --l1               # 항목 해설만
  python -m src.insight --l2 --week 2026-W35
  python -m src.insight --dry-run          # API 호출 없이 프롬프트·비용만 확인
  python -m src.insight --limit 5          # 몇 건만 실제로 호출해보기
  python -m src.insight --regenerate       # 이미 해설이 있는 항목도 다시
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import bindparam, func, select

from src.db import digests, get_engine, init_db, item_axes, items, load_config

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

# 100만 토큰당 달러. 비용 추정 출력용 (2026-06 기준, shared/live-sources.md 참조)
PRICING = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
}

L1_RULES = """\
당신은 사내 AI·빅데이터팀의 트렌드 레이더가 쓰는 해설자다.
아래 팀 프로파일을 읽고, 주어진 항목이 **이 팀에게 왜 중요한지**를 한국어 1~2문장으로 쓴다.

반드시 지킬 것:
- 제목과 요약에 있는 사실만 쓴다. 없는 내용을 추측하거나 지어내지 않는다.
- "도입해야 한다", "검토가 필요하다" 같은 판단·권고는 쓰지 않는다.
  무엇이 일어났고 그것이 팀 업무와 어떻게 닿는지까지만 쓴다.
- 제목을 그대로 반복하지 않는다. 팀 업무 맥락으로 연결하는 게 목적이다.
- 정보가 부족해 팀과의 연결을 말할 수 없으면 정확히 `관련 낮음` 다섯 글자만 출력한다.
- 군더더기 없이 문장만 출력한다. 머리말·따옴표·마크다운을 붙이지 않는다."""

L2_RULES = """\
당신은 사내 AI·빅데이터팀의 주간 트렌드 다이제스트 첫머리를 쓰는 편집자다.
아래 팀 프로파일과 이번 주 상위 항목 목록을 읽고 **'이번 주 흐름'을 3줄**로 쓴다.

반드시 지킬 것:
- 주어진 항목에 있는 사실만 쓴다. 목록에 없는 사건을 끌어오지 않는다.
- 각 줄은 개별 기사 요약이 아니라 **여러 항목을 관통하는 흐름**이어야 한다.
  묶이지 않으면 억지로 묶지 말고 가장 중요한 항목을 그대로 언급한다.
- 판단·권고·전망을 쓰지 않는다. 일어난 일과 팀 업무와의 접점까지만 쓴다.
- 각 줄은 `· `로 시작하고 한 줄에 60자 안팎. 정확히 3줄만 출력한다.
- 머리말·맺음말·마크다운을 붙이지 않는다."""


def load_team_profile(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    # '> TODO:' 같은 작성용 메모는 프롬프트에 넣지 않는다 — 모델이 지시로 오해한다
    lines = [l for l in p.read_text(encoding="utf-8").splitlines()
             if not l.strip().startswith("> TODO")]
    return "\n".join(lines).strip()


def build_client():
    """Anthropic 클라이언트. 키가 없으면 None을 돌려주고 호출부가 fail-open 처리한다.

    ⚠️ SDK는 **키가 없어도 생성자가 성공한다.** 인증 확인은 첫 요청 시점에 일어나고,
       그때 APIError가 아니라 TypeError가 난다("Could not resolve authentication
       method"). 그래서 생성자만 try로 감싸면 못 잡고, 워커 스레드에서 터져
       배치 전체가 죽는다(실측). 여기서 자격증명 유무를 미리 확인한다.
    """
    load_dotenv()
    try:
        import anthropic
    except ImportError:
        print("  ⚠ anthropic 패키지가 없습니다 (pip install anthropic)")
        return None
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        print("  ⚠ ANTHROPIC_API_KEY가 없습니다 (.env에 추가하세요)")
        return None

    # 조직 계정에서 발급한 identity-linked 키는 workspace id를 함께 보내야 한다.
    # 안 보내면 모든 요청이 400으로 떨어진다:
    #   "anthropic-workspace-id is required when authenticating with an
    #    identity-linked API key"
    # 개인 키에는 이 헤더가 필요 없고, 넣어도 무해하지 않으므로 있을 때만 붙인다.
    headers = {}
    ws = os.getenv("ANTHROPIC_WORKSPACE_ID")
    if ws:
        headers["anthropic-workspace-id"] = ws
    try:
        return anthropic.Anthropic(default_headers=headers or None)
    except Exception as exc:
        print(f"  ⚠ Anthropic 클라이언트를 만들 수 없습니다: {exc}")
        return None


def _system_blocks(rules: str, profile: str) -> list[dict[str, Any]]:
    """system 프롬프트. 항목마다 동일하므로 캐시 대상으로 표시한다.

    L1은 주 200~300건을 같은 system으로 호출한다. 프롬프트 캐싱은 접두사 일치라
    여기(고정 규칙 + 팀 프로파일)를 캐시하고 항목별 내용은 user 쪽에 둔다.
    ⚠️ 캐시는 접두사가 약 1024토큰 이상일 때만 걸린다. 팀 프로파일이 짧으면
       조용히 캐시되지 않는다 — usage.cache_read_input_tokens로 확인할 것.
    """
    text = rules + (f"\n\n[팀 프로파일]\n{profile}" if profile else "")
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _item_prompt(title: str, summary: str, source: str, axes: list[str],
                 published: str, labels: dict[str, str]) -> str:
    axis_names = ", ".join(labels.get(a, a) for a in axes) or "없음"
    return (f"제목: {title}\n"
            f"요약: {summary or '(요약 없음)'}\n"
            f"출처: {source} · {published}\n"
            f"걸린 축: {axis_names}")


def run_l1(cfg: dict[str, Any], args: argparse.Namespace) -> int:
    """교차 점수 상위 항목에 해설을 붙인다. 붙인 건수를 반환."""
    icfg = cfg["insight"]
    model = icfg["l1_model"]
    max_items = args.limit or int(icfg.get("l1_max_items", 300))
    max_tokens = int(icfg.get("l1_max_tokens", 200))
    fail_open = bool(icfg.get("fail_open", True))
    labels = {ax: spec.get("label", ax) for ax, spec in cfg["axes"].items()}
    profile = load_team_profile(icfg.get("team_profile_path", ""))

    engine = get_engine()
    stmt = (select(items.c.id, items.c.title, items.c.summary, items.c.source,
                   items.c.published_at)
            .where(items.c.kept.is_(True))
            .order_by(items.c.cross_score.desc(), items.c.relevance.desc())
            .limit(max_items))
    if not args.regenerate:
        # 이미 같은 모델로 해설이 붙은 항목은 건너뛴다. 모델·프롬프트를 바꿔
        # 다시 돌릴 때를 위해 insight_model을 함께 본다.
        stmt = stmt.where((items.c.insight.is_(None)) | (items.c.insight_model != model))
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
        # 대상 항목 것만 읽는다 — 조건 없이 읽으면 4만 7천 행 전수 스캔이다
        axes_map: dict[int, list[str]] = {}
        if rows:
            for item_id, axis in conn.execute(
                    select(item_axes.c.item_id, item_axes.c.axis)
                    .where(item_axes.c.item_id.in_([r.id for r in rows]))):
                axes_map.setdefault(item_id, []).append(axis)
    if not rows:
        print("  해설할 항목이 없습니다 (이미 전부 붙었거나 kept가 비어 있음)")
        return 0

    print(f"  대상 {len(rows)}건  |  모델 {model}  |  항목당 최대 {max_tokens}토큰")

    system = _system_blocks(L1_RULES, profile)
    prompts = [
        (r.id, _item_prompt(r.title or "", r.summary or "", r.source,
                            sorted(axes_map.get(r.id, [])),
                            str(r.published_at)[:10] if r.published_at else "-", labels))
        for r in rows
    ]

    if args.dry_run:
        print(f"\n  ── 프롬프트 예시 (첫 항목) ──\n{system[0]['text'][:400]}…")
        print(f"\n  [user]\n{prompts[0][1]}")
        inp, out = PRICING.get(model, (1.0, 5.0))
        # 대략치: system 약 700토큰 + 항목 200토큰, 출력 max_tokens 전량 가정
        est = len(prompts) * ((900 * inp) + (max_tokens * out)) / 1_000_000
        print(f"\n  예상 비용 상한 약 ${est:.2f} (캐시 적중 시 더 낮음)")
        return 0

    client = build_client()
    if client is None:
        if fail_open:
            print("  ⏭ 해설 없이 진행합니다 (fail_open)")
            return 0
        sys.exit("ANTHROPIC_API_KEY가 없습니다.")

    import anthropic

    stats = {"ok": 0, "skip": 0, "fail": 0, "in": 0, "out": 0, "cached": 0}

    def annotate(job: tuple[int, str]) -> tuple[int, str | None]:
        item_id, user_text = job
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_text}],
            )
        except anthropic.NotFoundError as exc:
            print(f"    ❌ 모델을 찾을 수 없습니다: {model} — {exc}")
            raise
        except anthropic.AuthenticationError:
            print("    ❌ API 키 인증 실패")
            raise
        except anthropic.RateLimitError:
            # SDK가 이미 재시도했는데도 남은 경우 — 이 항목만 포기한다
            stats["fail"] += 1
            return item_id, None
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            # 타입만 찍으면 원인을 못 찾는다. 400은 메시지에 이유가 들어 있다.
            msg = getattr(exc, "message", None) or str(exc)
            if stats["fail"] == 0:          # 같은 오류가 300줄 쏟아지는 걸 막는다
                print(f"    ⚠ 실패: {type(exc).__name__}: {msg[:220]}")
            stats["fail"] += 1
            return item_id, None
        except Exception as exc:
            # 예상 못 한 예외(SDK 인증 TypeError 등)가 워커에서 터지면 배치 전체가
            # 죽는다. 해설은 부가 정보이므로 그 항목만 포기하고 계속 간다.
            print(f"    ⚠ 항목 {item_id} 실패: {type(exc).__name__}: {str(exc)[:80]}")
            stats["fail"] += 1
            return item_id, None

        stats["in"] += resp.usage.input_tokens
        stats["out"] += resp.usage.output_tokens
        stats["cached"] += getattr(resp.usage, "cache_read_input_tokens", 0) or 0
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        if not text or text.startswith("관련 낮음"):
            stats["skip"] += 1
            return item_id, None
        stats["ok"] += 1
        return item_id, text

    t0 = time.time()
    results: list[tuple[int, str | None]] = []
    try:
        # 300건을 순차로 돌리면 10분 넘는다. rate limit은 SDK가 재시도로 흡수한다.
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for n, res in enumerate(pool.map(annotate, prompts), start=1):
                results.append(res)
                if n % 50 == 0:
                    print(f"    {n}/{len(prompts)}건 ({time.time() - t0:.0f}초)")
    except (anthropic.AuthenticationError, anthropic.NotFoundError):
        if not fail_open:
            raise
        print("  ⏭ 해설 없이 진행합니다 (fail_open)")
        return 0

    payload = [{"b_id": i, "b_text": t, "b_model": model, "b_at": datetime.now(timezone.utc)}
               for i, t in results if t]
    if payload:
        stmt_up = (items.update()
                   .where(items.c.id == bindparam("b_id"))
                   .values(insight=bindparam("b_text"),
                           insight_model=bindparam("b_model"),
                           insight_at=bindparam("b_at")))
        with engine.begin() as conn:
            conn.execute(stmt_up, payload)

    inp_price, out_price = PRICING.get(model, (1.0, 5.0))
    cost = (stats["in"] * inp_price + stats["out"] * out_price) / 1_000_000
    print(f"\n  해설 {stats['ok']}건 · 관련낮음 {stats['skip']}건 · 실패 {stats['fail']}건"
          f"  ({time.time() - t0:.0f}초)")
    print(f"  토큰 입력 {stats['in']:,} (캐시 적중 {stats['cached']:,}) / 출력 {stats['out']:,}"
          f"  ≈ ${cost:.3f}")
    if stats["cached"] == 0 and len(prompts) > 5:
        print("  ⚠ 캐시 적중 0 — system 접두사가 1024토큰 미만이면 캐시가 안 걸린다"
              " (팀 프로파일을 채우면 걸린다)")
    return stats["ok"]


def run_l2(cfg: dict[str, Any], args: argparse.Namespace) -> str | None:
    """해당 주차의 '이번 주 흐름' 3줄을 만들어 digests.lead에 저장한다."""
    icfg = cfg["insight"]
    model = icfg["l2_model"]
    labels = {ax: spec.get("label", ax) for ax, spec in cfg["axes"].items()}
    profile = load_team_profile(icfg.get("team_profile_path", ""))
    top_n = int(cfg.get("digest", {}).get("top_per_axis", 5)) * 3

    engine = get_engine()
    week = args.week
    with engine.connect() as conn:
        if not week:
            # 지정이 없으면 데이터가 있는 가장 최근 주차
            week = conn.execute(
                select(func.max(items.c.published_week)).where(items.c.kept.is_(True))
            ).scalar_one_or_none()
        if not week:
            print("  주차를 정할 수 없습니다 (kept 항목 없음)")
            return None
        rows = conn.execute(
            select(items.c.id, items.c.title, items.c.source, items.c.cross_score)
            .where(items.c.kept.is_(True), items.c.published_week == week)
            .order_by(items.c.cross_score.desc(), items.c.relevance.desc())
            .limit(top_n)
        ).all()
        axes_map: dict[int, list[str]] = {}
        if rows:
            for item_id, axis in conn.execute(
                    select(item_axes.c.item_id, item_axes.c.axis)
                    .where(item_axes.c.item_id.in_([r.id for r in rows]))):
                axes_map.setdefault(item_id, []).append(axis)

    if not rows:
        print(f"  {week} 주차에 통과 항목이 없습니다")
        return None
    print(f"  주차 {week}  |  상위 {len(rows)}건  |  모델 {model}")

    listing = "\n".join(
        f"- [{'+'.join(labels.get(a, a) for a in sorted(axes_map.get(r.id, [])))}] "
        f"{r.title}" for r in rows)
    user_text = f"[{week} 주차 상위 항목]\n{listing}"

    if args.dry_run:
        print(f"\n  ── L2 프롬프트 ──\n{user_text[:800]}")
        return None

    client = build_client()
    if client is None:
        if icfg.get("fail_open", True):
            print("  ⏭ 주간 요약 없이 진행합니다 (fail_open)")
            return None
        sys.exit("ANTHROPIC_API_KEY가 없습니다.")

    import anthropic
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            # 여러 항목을 관통하는 흐름을 찾는 일이라 생각을 켠다.
            # Sonnet 5는 adaptive가 유일한 on-mode다(budget_tokens는 400).
            thinking={"type": "adaptive"},
            system=_system_blocks(L2_RULES, profile),
            messages=[{"role": "user", "content": user_text}],
        )
    except Exception as exc:
        print(f"  ⚠ 주간 요약 실패: {type(exc).__name__}: {str(exc)[:120]}")
        if icfg.get("fail_open", True):
            return None
        raise

    if resp.stop_reason == "refusal":
        print("  ⚠ 모델이 응답을 거부했습니다")
        return None
    lead = "".join(b.text for b in resp.content if b.type == "text").strip()

    with engine.begin() as conn:
        exists = conn.execute(select(digests.c.week).where(digests.c.week == week)).first()
        vals = {"generated_at": datetime.now(timezone.utc), "lead": lead}
        if exists:
            conn.execute(digests.update().where(digests.c.week == week).values(**vals))
        else:
            conn.execute(digests.insert().values(week=week, body={}, **vals))

    inp_price, out_price = PRICING.get(model, (2.0, 10.0))
    cost = (resp.usage.input_tokens * inp_price + resp.usage.output_tokens * out_price) / 1_000_000
    print(f"\n  ── 이번 주 흐름 ({week}) ──\n{lead}\n")
    print(f"  토큰 입력 {resp.usage.input_tokens:,} / 출력 {resp.usage.output_tokens:,}"
          f"  ≈ ${cost:.4f}")
    return lead


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 해설 (L1 항목 / L2 주간)")
    parser.add_argument("--l1", action="store_true", help="항목 해설만")
    parser.add_argument("--l2", action="store_true", help="주간 요약만")
    parser.add_argument("--week", default=None, help="L2 주차 (예: 2026-W35)")
    parser.add_argument("--limit", type=int, default=0, help="L1 처리 상한 (테스트용)")
    parser.add_argument("--workers", type=int, default=4, help="L1 동시 호출 수")
    parser.add_argument("--regenerate", action="store_true",
                        help="이미 해설이 있는 항목도 다시 생성")
    parser.add_argument("--dry-run", action="store_true",
                        help="API를 호출하지 않고 프롬프트·예상 비용만 출력")
    args = parser.parse_args()

    cfg = load_config()
    if not cfg.get("insight", {}).get("enabled", True):
        sys.exit("config에서 insight.enabled가 꺼져 있습니다.")
    init_db(get_engine())

    do_l1 = args.l1 or not args.l2
    do_l2 = args.l2 or not args.l1

    if do_l1:
        print(f"{'=' * 62}\nL1 — 항목 해설\n{'=' * 62}")
        run_l1(cfg, args)
    if do_l2:
        print(f"\n{'=' * 62}\nL2 — 이번 주 흐름\n{'=' * 62}")
        run_l2(cfg, args)

    print("\n  다음: python -m src.digest")


if __name__ == "__main__":
    main()
