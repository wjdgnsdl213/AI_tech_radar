"""팀 공용 업무 조회·수정 API."""
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from src.team_profile import ProfileConflict, read_profile, save_profile

router = APIRouter(prefix="/api")


class ProfileUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=50000)
    revision: int = Field(ge=0)


@router.get("/team-profile")
def get_profile(response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        return read_profile()
    except SQLAlchemyError as exc:
        raise HTTPException(503, "팀 업무를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.") from exc


@router.put("/team-profile")
def update_profile(body: ProfileUpdate, request: Request, response: Response):
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "같은 사이트에서만 팀 업무를 수정할 수 있습니다.")
    response.headers["Cache-Control"] = "no-store"
    try:
        return save_profile(body.content, body.revision)
    except ProfileConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(503, "팀 업무를 저장하지 못했습니다. 입력 내용을 유지한 채 다시 시도해 주세요.") from exc
