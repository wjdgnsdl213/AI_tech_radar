"""AI 해설 레이어 — L1 항목 해설 / L2 주간 요약.

파이프라인에서의 위치:
    … → filter → score → **insight** → digest → 메일/웹

두 층위를 만든다(PLAN §4):
    L1  항목별 **기사 요약** 한두 문장. 교차 점수 상위 N건. Haiku
        ⚠️ PLAN §3은 "우리 팀에 왜 중요한가"로 적었지만 실사용 피드백으로 바꿨다.
           기사를 안 읽은 상태에서 의미부터 들으면 따라가기 어렵고, 원본 요약은
           소스마다 품질이 들쭉날쭉하다(HN은 story_text가 대부분 비어 있다).
           "왜 중요한가"는 L2(이번 주 흐름)와 축·교차 점수가 이미 표현한다.
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
당신은 사내 AI·빅데이터팀의 트렌드 브리핑을 쓰는 편집자다.
주어진 항목이 **무슨 일인지**를 한국어 개조식으로 정리한다.
읽는 사람이 원문을 열지 않고 이것만 보고도 내용을 파악할 수 있어야 한다.

출력 형식 (이 형식만 쓴다):
- 각 줄은 `· `로 시작한다. **1~3줄.**
- 한 줄은 한 가지만 담고 명사형이나 「~함」으로 끝낸다.
- 줄 하나는 60~80자.

★ 재료가 한 가지뿐이면 **한 줄로 끝낸다.** 한 사실을 억지로 두세 줄로
  쪼개지 않는다. 원문 요약이 100자 남짓인 기사가 대부분이라, 줄 수를
  채우려 들면 같은 말이 반복되거나 없는 내용이 붙는다.

반드시 지킬 것:
- 제목과 요약에 있는 사실만 쓴다. 없는 내용을 추측하거나 지어내지 않는다.
- **판단·권고·전망·의미부여를 쓰지 않는다.** 아래 표현은 하나도 쓰지 말 것:
    …해야 한다 / …필요하다 / …가능성이 높다 / …전망이다 / …주목된다
    …참고할 만하다 / …활용할 수 있다 / …시사한다 / …의미가 있다
  일어난 일만 사실로 적는다.
  (나쁨: · 소상공인 AI 도입 수용도를 파악하는 데 활용할 수 있음
   좋음: · 티오더가 자사 매장관리 서비스 사용자의 절반이 AI 기능으로 매장을
          운영 중이라고 밝힘)
- 제목을 그대로 옮기지 않는다. 제목에 없는 구체를 채운다:
    누가(주체·기관·기업) / 무엇을 했는지 / 수치·규모 / 시점 / 대상·범위
- 제목 외에 아무 정보가 없어 무슨 일인지 알 수 없으면
  정확히 `내용 부족` 네 글자만 출력한다.
- 머리말·맺음말·마크다운·번호를 붙이지 않는다. `· ` 줄만 출력한다."""

L1_REG_RULES = """\
당신은 사내 AI·빅데이터팀에 법령·행정규칙 변경을 알리는 담당자다.
주어진 법령/고시가 **무엇을 바꿨는지**를 한국어 개조식으로 정리한다.

출력 형식 (이 형식만 쓴다):
- 각 줄은 `· `로 시작한다. 2~4줄.
- 한 줄은 한 가지만 담고 명사형이나 「~함」으로 끝낸다. 서술형 문장을 쓰지 않는다.
- 마지막 줄은 시행일이다: `· 시행 2026-08-28` (본문에 시행일이 있을 때만)
- 줄 하나는 60자 안팎.

예:
· 공공기관이 AI 도입 시 투명성·신뢰성 영향평가를 받도록 신설
· 성능 검증 목적의 제한 운영은 평가 면제
· 적용 대상은 데이터기반행정을 수행하는 공공기관
· 시행 2026-08-28 (일부 조항은 2027-02-28)

반드시 지킬 것:
- ★ 주어진 본문에 있는 것만 쓴다. 의무·기한·과태료·적용대상을 **절대 추측하지
  않는다.** 규제 요약에서 없는 의무를 지어내는 것은 요약이 없는 것보다 나쁘다.
  본문에 안 나오면 그 줄을 아예 쓰지 않는다.
- 담는 순서: ① 무엇이 달라졌나(신설·변경) ② 누구에게 적용되나 ③ 시행일
  ②는 본문에 대상이 명시됐을 때만 쓴다.
- 「~해야 함」은 본문이 실제로 그렇게 정한 경우에만 쓴다.
  당신의 권고·대응방안·주의사항·전망을 덧붙이지 않는다.
- 타법개정처럼 조문 번호·제명만 바뀐 것이면 그렇게 적는다.
  억지로 의미를 부여하지 않는다 — 실제로 영향이 없는 개정이 많다.
- 제목만 있고 내용이 없으면 정확히 `내용 부족` 네 글자만 출력한다.
- 머리말·맺음말·마크다운·번호를 붙이지 않는다. `· ` 줄만 출력한다."""

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


def _text_of(msg) -> str:
    """응답에서 **텍스트 블록**만 골라낸다.

    content[0]을 그대로 쓰면 안 된다. Sonnet 5처럼 적응형 사고를 쓰는 모델은
    첫 블록이 ThinkingBlock이라 .text가 없어서 AttributeError로 죽는다
    (실측: L3에서 터졌다). L1·L2는 원래 type=="text"만 골라 쓰고 있었는데,
    같은 로직이 세 군데로 흩어지면 새로 추가하는 쪽이 또 틀린다. 하나로 모은다.
    """
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()


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
    ws = (os.getenv("ANTHROPIC_WORKSPACE_ID") or "").strip()
    if ws:
        # 형식이 아니면 보내지 않는다. 잘못된 값을 보내면 개인 키에서도 전 요청이
        # 400으로 떨어지는데, 에러 메시지가 "workspace-id is required"라서
        # '값이 없다'로 읽혀 원인을 찾기 어렵다(실측).
        if ws.startswith("wrkspc"):
            headers["anthropic-workspace-id"] = ws
        else:
            print(f"  ⚠ ANTHROPIC_WORKSPACE_ID 형식이 아닙니다({ws[:12]}…) — 무시합니다.")
            print("    조직 키가 아니면 .env에서 이 줄을 비워두세요.")
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
    # 규제 모드: 대상도 규칙도 다르다. 법령은 kept 필터를 태우지 않으므로
    # (소스 기반 판정) 여기서도 kept를 보지 않고 소스로 고른다.
    reg = bool(getattr(args, "reg", False))
    reg_sources = [n for n, sc in (cfg.get("sources") or {}).items()
                   if isinstance(sc, dict) and sc.get("regulatory")]
    fail_open = bool(icfg.get("fail_open", True))
    labels = {ax: spec.get("label", ax) for ax, spec in cfg["axes"].items()}
    profile = load_team_profile(icfg.get("team_profile_path", ""))

    engine = get_engine()
    stmt = (select(items.c.id, items.c.title, items.c.summary, items.c.source,
                   items.c.published_at)
            .limit(max_items))
    if reg:
        stmt = (stmt.where(items.c.source.in_(reg_sources))
                .order_by(items.c.published_at.desc()))
    else:
        stmt = (stmt.where(items.c.kept.is_(True))
                .order_by(items.c.cross_score.desc(), items.c.relevance.desc()))
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

    system = _system_blocks(L1_REG_RULES if reg else L1_RULES, profile)
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

    class Fatal(Exception):
        """설정·인증 문제. 재시도해도 같은 결과라 배치를 즉시 접는다."""

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
        except anthropic.BadRequestError as exc:
            # 400은 요청 자체가 잘못된 것이다(키가 workspace를 요구한다든지).
            # 300건을 다 두드려도 전부 같은 400이 난다 — 이 프로젝트가 GeekNews
            # 403에서 이미 겪은 실수다("403을 받고 199회를 더 두드린 게 문제였다").
            # 거부 신호를 받으면 두드리기를 멈춘다.
            msg = getattr(exc, "message", None) or str(exc)
            raise Fatal(f"{type(exc).__name__}: {msg[:240]}") from exc
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            # 일시적 오류는 그 항목만 포기하고 계속 간다.
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
        text = _text_of(resp)
        if not text or text.startswith("내용 부족") or text.startswith("관련 낮음"):
            # '내용 부족' = 제목 말고는 아무것도 없는 항목. 지면에 올려도 읽을 게 없다.
            # ⚠️ 예전 '관련 낮음'은 팀 무관 판정이었고 필터가 놓친 것을 짚어주는
            #    제2의 의견이었다. 요약으로 바꾸면서 그 신호는 사라졌다 —
            #    무관한 항목도 요약은 되기 때문이다.
            # 빈 문자열로 표시해 '시도했는데 연결을 못 찾음'과 '아직 안 함'을 구분한다.
            stats["skip"] += 1
            return item_id, ""
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
    except (anthropic.AuthenticationError, anthropic.NotFoundError, Fatal) as exc:
        print(f"\n  ⛔ 중단: {exc}")
        print("     같은 오류가 모든 항목에서 나므로 나머지는 시도하지 않습니다.")
        if not fail_open:
            raise
        print("  ⏭ 해설 없이 진행합니다 (fail_open)")
        return 0

    # t가 None이면 실패(재시도 여지가 있으니 기록하지 않는다).
    # t가 ""이면 '관련 낮음' — 모델이 판단한 결과이므로 기록한다.
    payload = [{"b_id": i, "b_text": t, "b_model": model, "b_at": datetime.now(timezone.utc)}
               for i, t in results if t is not None]
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
    lead = _text_of(resp)

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


L3_RULES = """\
당신은 사내 AI·빅데이터팀의 **월간 리뷰**를 쓰는 편집자다.
한 달치 주간 요약과 상위 항목을 읽고 그 달의 흐름을 정리한다.

주간 요약과 다른 점:
- 주간은 "이번 주에 무슨 일이 있었나"다. 월간은 **"여러 주에 걸쳐 무엇이
  이어졌나"**다. 한 주에만 나온 단발 사건은 흐름이 아니다.
- 여러 주에 반복해 나온 주제, 점점 커진 주제, 사라진 주제를 짚는다.
  이건 주어진 자료를 묶는 일이지 없는 사실을 만드는 게 아니다.

출력 형식:
- 각 줄은 `· `로 시작한다. **4~6줄.**
- 줄 하나는 70~100자.
- 마지막 한 줄은 규제·법령 동향이 자료에 있을 때만 그것을 다룬다.

반드시 지킬 것:
- 주어진 자료에 있는 사실만 쓴다. 목록에 없는 사건·기관·수치를 끌어오지 않는다.
- 대응 방안·권고·전망을 쓰지 않는다. 무엇이 일어났고 무엇이 이어졌는지까지만.
- 한 줄에 한 흐름. 억지로 묶지 말고, 묶이지 않으면 가장 큰 항목을 그대로 적는다.
- 머리말·맺음말·마크다운을 붙이지 않는다. `· ` 줄만 출력한다."""


def _week_month(week: str) -> str:
    """'2026-W35' → '2026-08'. 그 주의 목요일이 속한 달로 본다.

    월요일과 일요일이 다른 달에 걸치는 주가 있는데, ISO 8601은 목요일이 속한
    해·달을 그 주의 것으로 본다. digest.week_label도 같은 기준을 쓴다.
    """
    from datetime import date
    try:
        y, w = week.split("-W")
        d = date.fromisocalendar(int(y), int(w), 4)
        return f"{d.year:04d}-{d.month:02d}"
    except Exception:
        return ""


TASK_RULES = """\
당신은 사내 AI·빅데이터팀의 월간 리뷰에서 **'과제 후보'** 절을 쓰는 분석 담당자다.
한 달치 자료(주간 요약·상위 기사 제목·법령 변경)를 읽고, 팀이 실제로 검토할 만한
후보를 3~4개 뽑는다.

★ 이 절은 요약이 아니다.
  "무슨 일이 있었나"는 앞 절에서 이미 다뤘다. 여기서는 **여러 자료를 겹쳐야
  보이는 것**을 쓴다. 한 기사만 보고 알 수 있는 건 후보가 아니다.
  좋은 후보의 조건:
    · 서로 다른 자료 둘 이상이 같은 방향을 가리킨다
      (예: 법령이 의무를 신설했는데 + 지자체 사례가 이미 나오고 있다)
    · 우리 팀 업무(소상공인 데이터 분석·AI 활용·공공데이터)와 닿는다
    · 지금 검토할 이유가 있다 (시행일이 다가온다, 사례가 늘고 있다 등)

각 후보를 아래 네 줄로 쓴다. 라벨을 그대로 쓴다:
제목: (한 줄, 20자 안팎)
관찰: 자료에서 실제로 확인된 것. **어떤 자료인지 밝힌다** (법령명·기관명·기사 주제).
함의: 그래서 우리 팀에 무엇을 뜻하는지. 여기가 유일하게 해석이 허용되는 줄이다.
확인: 팀이 다음에 확인하거나 정해야 할 것 하나. **질문 형태**로 쓴다.

반드시 지킬 것:
- '관찰'에는 주어진 자료에 있는 것만 쓴다. 없는 기관·수치·법령을 만들지 않는다.
- '함의'는 관찰에서 곧바로 이어져야 한다. 자료에 근거가 없는 전망·단정은 쓰지 않는다.
  "…할 것으로 보인다"보다 "…가 필요해진다"처럼 자료에 붙은 서술을 쓴다.
- '확인'은 우리가 답을 모르는 것이어야 한다. 이미 자료에 답이 있으면 후보가 아니다.
- 겹치는 후보를 만들지 않는다. 3~4개가 서로 다른 것을 가리켜야 한다.
- 근거가 약하면 개수를 줄인다. 억지로 4개를 채우지 않는다. 하나도 없으면
  정확히 `후보 없음` 네 글자만 출력한다.
- 후보 사이는 빈 줄로 나눈다. 머리말·맺음말·마크다운·번호를 붙이지 않는다."""


def _parse_tasks(text: str) -> list[dict[str, str]]:
    """'제목:/관찰:/함의:/확인:' 네 줄 묶음을 딕셔너리 목록으로."""
    if not text or text.strip() == "후보 없음":
        return []
    out, cur = [], {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if cur:
                out.append(cur)
                cur = {}
            continue
        for key, label in (("title", "제목"), ("fact", "관찰"),
                           ("mean", "함의"), ("ask", "확인")):
            if line.startswith(label + ":"):
                cur[key] = line[len(label) + 1:].strip()
                break
    if cur:
        out.append(cur)
    return [t for t in out if t.get("title") and t.get("fact")]


def run_tasks(cfg: dict[str, Any], month: str, material: str) -> list[dict[str, str]]:
    """과제 후보를 만든다. 실패하면 빈 목록 — 보고서는 그 절만 비우고 나간다."""
    icfg = cfg["insight"]
    model = icfg.get("l3_model", icfg["l2_model"])
    client = build_client()
    if client is None:
        print("  ⏭ 과제 후보 건너뜀 (API 키 없음)")
        return []

    import anthropic
    try:
        msg = client.messages.create(
            model=model, max_tokens=int(icfg.get("l3_max_tokens", 4000)),
            thinking={"type": "adaptive"},
            system=_system_blocks(TASK_RULES,
                                  load_team_profile(icfg.get("team_profile_path", ""))),
            messages=[{"role": "user", "content": material}])
    except anthropic.APIError as exc:
        print(f"  ⚠ 과제 후보 실패: {type(exc).__name__}: {exc}")
        return []
    tasks = _parse_tasks(_text_of(msg))
    print(f"  과제 후보 {len(tasks)}개")
    for t in tasks:
        print(f"    · {t.get('title', '')}")
    return tasks


def run_l3(cfg: dict[str, Any], args: argparse.Namespace) -> str | None:
    """한 달치 흐름을 만들어 digests(week='YYYY-MM')에 저장한다.

    ★ 재료는 **주간 요약(L2)들**이다. 그 달 기사를 전부 다시 읽히지 않는다.
      한 달이면 통과분만 800건이 넘어 프롬프트에 다 들어가지 않고, 넣어도
      모델이 주간과 같은 층위의 요약을 반복한다. 이미 한 번 압축된 주간
      요약을 재료로 쓰면 "여러 주에 걸쳐 이어진 것"이 보인다.

    digests 테이블을 주차와 함께 쓴다 — 키가 '2026-08'이라 '2026-W35'와 섞일 일이
    없고, 이 테이블은 어디서도 전수 조회하지 않고 키 하나로만 읽는다.
    """
    icfg = cfg["insight"]
    model = icfg.get("l3_model", icfg["l2_model"])
    profile = load_team_profile(icfg.get("team_profile_path", ""))

    engine = get_engine()
    month = args.month
    with engine.connect() as conn:
        if not month:
            last = conn.execute(select(func.max(items.c.published_at))
                                .where(items.c.kept.is_(True))).scalar_one_or_none()
            if not last:
                print("  통과 항목이 없습니다")
                return None
            month = f"{last.year:04d}-{last.month:02d}"

        weeks = [w for (w,) in conn.execute(
            select(items.c.published_week).distinct()
            .where(items.c.kept.is_(True))) if w]
        mine = sorted(w for w in weeks if _week_month(w) == month)
        leads = []
        for w in mine:
            lead = conn.execute(select(digests.c.lead)
                                .where(digests.c.week == w)).scalar_one_or_none()
            if lead:
                leads.append((w, lead))

        rows = conn.execute(
            select(items.c.title, items.c.published_week)
            .where(items.c.kept.is_(True), items.c.published_week.in_(mine or ["_"]))
            .order_by(items.c.cross_score.desc()).limit(30)).all()
        reg_srcs = [n for n, sc in (cfg.get("sources") or {}).items()
                    if isinstance(sc, dict) and sc.get("regulatory")]
        regs = conn.execute(
            select(items.c.title, items.c.published_at)
            .where(items.c.source.in_(reg_srcs or ["_"]),
                   items.c.published_week.in_(mine or ["_"]))
            .order_by(items.c.published_at.desc()).limit(10)).all()

    if not leads and not rows:
        print(f"  {month} 재료가 없습니다 (주간 요약도 통과 항목도 없음)")
        return None
    print(f"  {month}  |  주차 {len(mine)}개 · 주간요약 {len(leads)}개 · "
          f"상위 {len(rows)}건 · 규제 {len(regs)}건  |  모델 {model}")

    NL = chr(10)
    parts = [f"[{month} 주간 요약들]"]
    for w, lead in leads:
        parts.append(f"({w})" + NL + lead)
    parts.append(NL + "[그 달 상위 항목]")
    parts += [f"- ({r.published_week}) {r.title}" for r in rows]
    if regs:
        parts.append(NL + "[그 달 법령·규제]")
        parts += [f"- {str(x.published_at)[:10]} {x.title}" for x in regs]
    user_text = NL.join(parts)

    if args.dry_run:
        print(NL + "  ── L3 프롬프트 ──" + NL + user_text[:1200])
        return None

    client = build_client()
    if client is None:
        if icfg.get("fail_open", True):
            print("  ⏭ 월간 리뷰 없이 진행합니다 (fail_open)")
            return None
        sys.exit("ANTHROPIC_API_KEY가 없습니다.")

    import anthropic
    try:
        msg = client.messages.create(
            model=model, max_tokens=int(icfg.get("l3_max_tokens", 4000)),
            # ★ thinking을 반드시 명시한다.
            #   빼면 사고가 상한까지 폭주해 stop_reason=max_tokens로 끝나고
            #   본문이 한 글자도 안 나온다(실측: 2000토큰 전량이 사고, 텍스트 0자).
            #   adaptive를 명시하면 스스로 멈춘다(end_turn, 1,276토큰).
            #   max_tokens는 사고 + 출력을 합친 상한이라는 점도 같이 기억할 것.
            thinking={"type": "adaptive"},
            system=_system_blocks(L3_RULES, profile),
            messages=[{"role": "user", "content": user_text}])
    except anthropic.APIError as exc:
        print(f"  ⚠ 월간 리뷰 실패: {type(exc).__name__}: {exc}")
        return None
    text = _text_of(msg)
    print(NL + f"  ── {month} 월간 리뷰 ──" + NL + text + NL)

    # 과제 후보는 같은 재료를 다시 쓰되 프롬프트가 다르다 — 흐름 요약과 섞으면
    # 한쪽이 다른 쪽 형식에 끌려간다(요약 톤으로 후보를 쓰거나 그 반대).
    tasks = run_tasks(cfg, month, user_text)

    with engine.begin() as conn:
        exists = conn.execute(select(digests.c.week)
                              .where(digests.c.week == month)).first()
        if exists:
            conn.execute(digests.update().where(digests.c.week == month)
                         .values(lead=text,
                                 body={"kind": "monthly", "weeks": mine, "tasks": tasks},
                                 generated_at=datetime.now(timezone.utc)))
        else:
            conn.execute(digests.insert().values(
                week=month, lead=text,
                body={"kind": "monthly", "weeks": mine, "tasks": tasks},
                generated_at=datetime.now(timezone.utc)))
    u = msg.usage
    inp, out = PRICING.get(model, (1.0, 5.0))
    print(f"  토큰 입력 {u.input_tokens:,} / 출력 {u.output_tokens:,}"
          f"  ≈ ${(u.input_tokens * inp + u.output_tokens * out) / 1_000_000:.4f}")
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 해설 (L1 항목 / L2 주간)")
    parser.add_argument("--l1", action="store_true", help="항목 해설만")
    parser.add_argument("--l2", action="store_true", help="주간 요약만")
    parser.add_argument("--week", default=None, help="L2 주차 (예: 2026-W35)")
    parser.add_argument("--limit", type=int, default=0, help="L1 처리 상한 (테스트용)")
    parser.add_argument("--workers", type=int, default=4, help="L1 동시 호출 수")
    parser.add_argument("--l3", action="store_true", help="월간 리뷰만")
    parser.add_argument("--month", default=None, help="L3 대상 월 (예: 2026-08)")
    parser.add_argument("--reg", action="store_true",
                        help="법령·규제 항목에 요약을 붙인다 (규제 전용 프롬프트)")
    parser.add_argument("--regenerate", action="store_true",
                        help="이미 해설이 있는 항목도 다시 생성")
    parser.add_argument("--dry-run", action="store_true",
                        help="API를 호출하지 않고 프롬프트·예상 비용만 출력")
    args = parser.parse_args()

    cfg = load_config()
    if not cfg.get("insight", {}).get("enabled", True):
        sys.exit("config에서 insight.enabled가 꺼져 있습니다.")
    init_db(get_engine())

    # --reg는 법령 항목만 다루므로 L1 전용이다. 주간 흐름(L2)은 뉴스 기반이라
    # 여기서 같이 돌면 규제 실행마다 주간 요약이 덮어써진다.
    do_l1 = args.l1 or args.reg or not (args.l2 or args.l3)
    do_l2 = args.l2 or not (args.l1 or args.reg or args.l3)
    do_l3 = args.l3

    if do_l1:
        print(f"{'=' * 62}\nL1 — 항목 해설\n{'=' * 62}")
        run_l1(cfg, args)
    if do_l2:
        print(f"\n{'=' * 62}\nL2 — 이번 주 흐름\n{'=' * 62}")
        run_l2(cfg, args)

    if do_l3:
        print('\n' + '=' * 62 + '\n' + 'L3 — 월간 리뷰' + '\n' + '=' * 62)
        run_l3(cfg, args)

    print("\n  다음: python -m src.digest")


if __name__ == "__main__":
    main()
