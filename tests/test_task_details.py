"""추천의 실행 계획이 원본부터 보고서까지 유지되는지 검증한다."""
import json
from pathlib import Path


def test_current_editorials_have_specific_plans_and_sources():
    from src.reviews import validate_editorial
    for path in Path("content/reviews").glob("*.json"):
        review = validate_editorial(json.loads(path.read_text(encoding="utf-8")))
        for task in review["tasks"]:
            for key in ("team_fit", "objective", "approach", "deliverables", "success_criteria", "cautions", "duration"):
                assert task.get(key), (path, task["title"], key)
            assert task["approach"].count("단계") >= 3
            assert task["source_ids"]


def test_task_parser_preserves_old_format_and_new_details():
    from src.insight import _parse_tasks
    old = "제목: 기존 과제\n관찰: 사실\n함의: 관련성\n확인: 질문?"
    assert _parse_tasks(old)[0]["ask"] == "질문?"
    task = _parse_tasks(old + "\n업무연결: 상담\n목표: 오류 확인\n기간: 2주 제안\n진행: 표본 선정 → 검증\n산출물: 검증표\n검토기준: 출처 일치\n유의사항: 권한 확인")[0]
    assert task["approach"] == "표본 선정 → 검증"
    assert task["success_criteria"] == "출처 일치"


def test_report_includes_detailed_plan_without_rendering_raw_html():
    from src.report import render_html, render_md
    from sqlalchemy import create_engine
    from src.db import metadata, load_config
    from src.report import collect
    from unittest.mock import patch
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with patch("src.report.get_engine", return_value=engine):
        report = collect("2026-09", load_config())
    report["tasks"] = [{"title":"검토", "approach":"<script>bad</script>\n표본 검증", "deliverables":"결과표"}]
    assert "표본 검증" in render_md(report)
    html = render_html(report)
    assert "결과표" in html and "&lt;script&gt;" in html
    assert "<script>bad</script>" not in html
    engine.dispose()
