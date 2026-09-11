"""리뷰·이슈·과제 후보의 읽기 전용 경계."""
from fastapi import APIRouter, HTTPException, Query
from src.db import get_engine
from src.reviews import available_periods, issue_data, review_data, task_candidates

router = APIRouter(prefix="/api")


@router.get("/reviews/periods")
def periods(kind: str = Query("monthly", pattern="^(monthly|weekly)$")):
    return {"periods": available_periods(get_engine(), kind)}


@router.get("/reviews")
def review(kind: str = Query("monthly", pattern="^(monthly|weekly)$"), period: str = ""):
    try:
        return review_data(get_engine(), kind, period)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/issues")
def issue(q: str = Query(..., min_length=1, max_length=100), days: int = Query(90, ge=7, le=365),
          page: int = Query(1, ge=1)):
    try:
        return issue_data(get_engine(), q, days, page=page)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/task-candidates")
def candidates():
    return {"tasks": task_candidates(get_engine())}
