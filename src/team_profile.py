"""파일 기본값과 DB에 저장한 팀 공용 업무를 한 곳에서 읽는다."""
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, inspect, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.db import get_engine, load_config

team_profiles = Table(
    "team_profiles", MetaData(),
    Column("id", Integer, primary_key=True),
    Column("content", Text, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("updated_at", String(40), nullable=False),
)


class ProfileConflict(ValueError):
    pass


def seed_content(path: str | None = None) -> str:
    if path is None:
        path = load_config().get("insight", {}).get("team_profile_path", "")
    p = Path(path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[1] / p
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def read_profile(path: str | None = None) -> dict:
    engine = get_engine()
    if not inspect(engine).has_table(team_profiles.name):
        return {"content": seed_content(path), "revision": 0, "updated_at": ""}
    with engine.connect() as conn:
        row = conn.execute(select(team_profiles).where(team_profiles.c.id == 1)).mappings().first()
    return ({"content": row["content"], "revision": row["revision"], "updated_at": row["updated_at"]}
            if row else {"content": seed_content(path), "revision": 0, "updated_at": ""})


def save_profile(content: str, revision: int) -> dict:
    if not content.strip() or len(content) > 50000:
        raise ValueError("팀 업무를 1~50,000자로 입력해 주세요.")
    engine = get_engine()
    # 첫 저장 시 이 테이블만 추가한다. 조회·해설 생성은 DB를 변경하지 않는다.
    team_profiles.create(engine, checkfirst=True)
    result = {"content": content.strip(), "revision": revision + 1,
              "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        with engine.begin() as conn:
            if revision == 0:
                conn.execute(team_profiles.insert().values(id=1, **result))
            else:
                changed = conn.execute(team_profiles.update().where(
                    team_profiles.c.id == 1, team_profiles.c.revision == revision).values(**result))
                if changed.rowcount != 1:
                    raise ProfileConflict("다른 사용자가 팀 업무를 수정했습니다. 입력 내용을 복사한 뒤 최신 내용을 불러와 합쳐 주세요.")
    except IntegrityError as exc:
        raise ProfileConflict("다른 사용자가 먼저 저장했습니다. 최신 내용을 불러와 합쳐 주세요.") from exc
    return result


def profile_for_prompt(path: str) -> str:
    try:
        content = read_profile(path)["content"]
    except SQLAlchemyError:
        # 해설 배치에서 DB 장애가 나도 기존 파일 맥락은 사용할 수 있다.
        content = seed_content(path)
    return "\n".join(line for line in content.splitlines()
                     if not line.strip().startswith("> TODO")).strip()
