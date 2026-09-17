"""팀 공용 업무의 저장·충돌 방지·해설 연결을 실제 임시 DB로 확인한다."""
import importlib.util

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def profile(tmp_path, monkeypatch):
    assert importlib.util.find_spec("src.team_profile"), "팀 업무 저장 모듈이 필요합니다"
    from src import team_profile as module
    from web import team_profile_api
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    seed = tmp_path / "team.md"
    seed.write_text("# 팀 업무\n상권 분석", encoding="utf-8")
    monkeypatch.setattr(module, "get_engine", lambda: engine)
    monkeypatch.setattr(module, "load_config", lambda: {"insight": {"team_profile_path": str(seed)}})
    app = FastAPI()
    app.include_router(team_profile_api.router)
    with TestClient(app) as client:
        yield module, client
    engine.dispose()


def test_seed_then_shared_save_and_conflict(profile):
    module, client = profile
    original = client.get("/api/team-profile").json()
    assert original["content"] == "# 팀 업무\n상권 분석"
    assert original["revision"] == 0
    saved = client.put("/api/team-profile", json={"content": "AI 상담 품질 검증", "revision": 0})
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert client.get("/api/team-profile").json()["content"] == "AI 상담 품질 검증"
    assert module.profile_for_prompt("") == "AI 상담 품질 검증"
    assert client.put("/api/team-profile", json={"content": "덮어쓰기", "revision": 0}).status_code == 409
    assert client.get("/api/team-profile").json()["content"] == "AI 상담 품질 검증"
    assert client.put("/api/team-profile", json={"content": "데이터 품질 관리", "revision": 1}).json()["revision"] == 2


@pytest.mark.parametrize("content", ["", "   \n", "가" * 50001], ids=["empty", "blank", "too-long"])
def test_invalid_profile_preserves_existing_content(profile, content):
    _, client = profile
    assert client.put("/api/team-profile", json={"content": content, "revision": 0}).status_code == 422
    assert client.get("/api/team-profile").json()["revision"] == 0


def test_prompt_uses_saved_profile_and_filters_author_notes(profile):
    module, client = profile
    client.put("/api/team-profile", json={"content": "업무\n> TODO: 작성 메모\n데이터 분석", "revision": 0})
    assert module.profile_for_prompt("") == "업무\n데이터 분석"


def test_profile_write_rejects_cross_origin(profile):
    _, client = profile
    response = client.put("/api/team-profile", json={"content": "변경", "revision": 0},
                          headers={"origin": "https://another.example"})
    assert response.status_code == 403
