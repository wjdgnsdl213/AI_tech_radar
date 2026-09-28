"""직접 작성한 리뷰와 날짜별 관측값. 모델 호출이나 DB 쓰기는 하지 않는다."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import Date, and_, case, cast, func, or_, select
from sqlalchemy.engine import Connection, Engine
from src.db import digests, item_axes, items

KST = timezone(timedelta(hours=9))
EDITORIAL_DIR = Path(__file__).resolve().parents[1] / "content" / "reviews"


def current_period(kind: str, now: datetime | None = None) -> str:
    local = (now or datetime.now(timezone.utc)).astimezone(KST)
    return local.strftime("%Y-%m") if kind == "monthly" else f"{local.isocalendar().year}-W{local.isocalendar().week:02d}"


def period_window(kind: str, period: str, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(KST)
    if kind == "monthly" and re.fullmatch(r"\d{4}-\d{2}", period):
        year, month = map(int, period.split("-"))
        start = datetime(year, month, 1, tzinfo=KST)
        end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=KST)
        before = start - timedelta(days=1)
        previous = before.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        label = f"{year}년 {month}월"
    elif kind == "weekly" and re.fullmatch(r"\d{4}-W\d{2}", period):
        start = datetime.fromisocalendar(int(period[:4]), int(period[6:]), 1).replace(tzinfo=KST)
        end, previous = start + timedelta(days=7), start - timedelta(days=7)
        label = f"{start.year}년 {start.month}/{start.day}–{(end - timedelta(days=1)).month}/{(end - timedelta(days=1)).day}"
    else:
        raise ValueError("주차 또는 월 형식을 확인해 주세요.")
    cutoff = max(start, min(now, end))
    # 진행 중에는 직전 기간에서도 동일한 경과시간만 센다. 종료 기간은 전체끼리 비교.
    previous_end = min(start, previous + (cutoff - start)) if now < end else start
    return {"start": start, "end": end, "cutoff": cutoff, "previous_start": previous,
            "previous_end": previous_end, "label": label,
            "status": "upcoming" if now < start else "in_progress" if now < end else "closed"}


def safe_url(value: str) -> str:
    try:
        u = urlparse(value or "")
        return value if u.scheme in {"http", "https"} and u.netloc else ""
    except ValueError:
        return ""


def validate_editorial(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("리뷰는 JSON 객체여야 합니다.")
    period = data.get("period", "")
    kind = "weekly" if "W" in period else "monthly"
    window = period_window(kind, period)
    as_of = datetime.fromisoformat(data.get("as_of", "")).date()
    if not window["start"].date() <= as_of < window["end"].date():
        raise ValueError("리뷰 기준일은 해당 기간 안이어야 합니다.")
    if not isinstance(data.get("summary"), str) or not data["summary"].strip():
        raise ValueError("리뷰 요약이 필요합니다.")
    sources = data.get("sources", [])
    ids = set()
    for source in sources:
        if not isinstance(source.get("id"), int) or not source.get("title") or not safe_url(source.get("url", "")):
            raise ValueError("유효한 기사 ID, 제목, 원문 주소가 필요합니다.")
        ids.add(source["id"])
    for part in data.get("sections", []) + data.get("tasks", []):
        if not part.get("title") or not set(part.get("source_ids", [])).issubset(ids):
            raise ValueError("절 또는 과제의 근거가 리뷰 출처에 없습니다.")
    if data.get("final") and as_of != (window["end"] - timedelta(days=1)).date():
        raise ValueError("확정 리뷰는 기간 마지막 날까지 반영해야 합니다.")
    return data


def read_editorial(period: str) -> dict[str, Any] | None:
    period_window("weekly" if "W" in period else "monthly", period)
    path = EDITORIAL_DIR / f"{period}.json"
    if not path.exists():
        return None
    data = validate_editorial(json.loads(path.read_text(encoding="utf-8")))
    if data["period"] != period:
        raise ValueError("리뷰 파일명과 기간이 일치하지 않습니다.")
    return data


def editorial_text(data: dict[str, Any]) -> str:
    lines = [data["summary"]]
    for section in data.get("sections", []):
        lines += ["", section["title"], section.get("body", "")]
    if data.get("sources"):
        lines += ["", "근거 기사"] + [f"· {s['title']} — {s['url']}" for s in data["sources"]]
    return "\n".join(lines)


def _conditions(start: datetime, end: datetime) -> tuple:
    # SQLite는 timezone 정보를 보존하지 않는다. 양 엔진에 동일한 UTC 값을 전달한다.
    return (items.c.kept.is_(True), items.c.published_at >= start.astimezone(timezone.utc),
            items.c.published_at < end.astimezone(timezone.utc))


def _article_rows(conn, conditions: tuple, limit: int = 30, chronological: bool = False, offset: int = 0) -> list[dict]:
    stmt = select(items.c.id, items.c.title, items.c.summary, items.c.url, items.c.source,
                  items.c.published_at, items.c.insight).where(*conditions)
    order = (items.c.published_at.asc(), items.c.id.asc()) if chronological else (
        func.coalesce(items.c.cross_score, 0).desc(), items.c.published_at.desc(), items.c.id.desc())
    rows = conn.execute(stmt.order_by(*order).offset(offset).limit(limit)).mappings().all()
    axes: dict[int, list[str]] = {}
    if rows:
        for item_id, axis in conn.execute(select(item_axes).where(item_axes.c.item_id.in_([r["id"] for r in rows]))):
            axes.setdefault(item_id, []).append(axis)
    out = []
    for row in rows:
        r = dict(row)
        stamp = r.pop("published_at")
        if stamp and stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        r.update(published=stamp.astimezone(KST).strftime("%Y-%m-%d") if stamp else "",
                 axes=axes.get(r["id"], []), url=safe_url(r["url"]), summary=(r["summary"] or "")[:500])
        out.append(r)
    return out


def _observations(conn: Connection, conditions: tuple, previous: tuple) -> tuple[int, int, datetime | None, dict[str, int]]:
    """현재·직전 기간을 함께 집계해 왕복 요청과 날짜 전송량을 줄인다."""
    current = case((and_(*conditions), 1), else_=0)
    stamp = items.c.published_at
    if conn.dialect.name == "postgresql":
        day = cast(func.timezone("Asia/Seoul", stamp), Date)
    elif conn.dialect.name == "sqlite":
        day = func.date(stamp, "+9 hours")
    else:
        day = None
    if day is not None:
        stmt = select(current, day, func.count(), func.max(stamp)).group_by(current, day)
    else:
        stmt = select(current, stamp)
    rows = conn.execute(stmt.where(or_(and_(*conditions), and_(*previous))))
    total, prev_total, latest, buckets = 0, 0, None, {}
    for row in rows:
        if day is not None:
            is_current, local_day, count, last = row
            key = str(local_day)
        else:
            is_current, last = row
            count = 1
            utc = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
            key = utc.astimezone(KST).date().isoformat()
        if is_current:
            total += count
            buckets[key] = buckets.get(key, 0) + count
            if latest is None or last > latest:
                latest = last
        else:
            prev_total += count
    return total, prev_total, latest, buckets


def review_data(engine: Engine, kind: str, period: str = "", now: datetime | None = None) -> dict[str, Any]:
    period = period or current_period(kind, now)
    w = period_window(kind, period, now)
    conds = _conditions(w["start"], w["cutoff"])
    prev_conds = _conditions(w["previous_start"], w["previous_end"])
    editorial = read_editorial(period)
    with engine.connect() as conn:
        total, prev_total, latest, buckets = _observations(conn, conds, prev_conds)
        if latest and latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        legacy = None
        if not editorial:
            legacy = conn.execute(select(digests).where(digests.c.week == period)).mappings().first()
        if not editorial and legacy:
            # 수집 PC의 발행이 웹 배포보다 앞서도 본문·출처를 요약 칸에 섞지 않는다.
            snapshot = (legacy["body"] or {}).get("editorial_snapshot")
            if isinstance(snapshot, dict) and snapshot.get("period") == period:
                try:
                    editorial = validate_editorial(snapshot)
                except (ValueError, TypeError, KeyError, AttributeError):
                    # 오래되었거나 불완전한 스냅샷은 기존 lead로 폴백한다.
                    pass
        if not editorial and legacy and legacy["lead"]:
            editorial = {"period": period, "title": w["label"] + " 리뷰", "summary": legacy["lead"],
                         "as_of": "", "generated_at": str(legacy["generated_at"]), "legacy": True,
                         "sections": [], "sources": [], "tasks": (legacy["body"] or {}).get("tasks", [])}
        # 같은 축의 현재·직전 건수를 한 번에 읽는다. 중복 축은 기존대로 각각 센다.
        axis_rows = conn.execute(select(item_axes.c.axis,
            func.sum(case((and_(*conds), 1), else_=0)),
            func.sum(case((and_(*prev_conds), 1), else_=0))).select_from(
                item_axes.join(items, items.c.id == item_axes.c.item_id))
            .where(or_(and_(*conds), and_(*prev_conds))).group_by(item_axes.c.axis)).all()
        current_axes = {axis: current for axis, current, _ in axis_rows}
        prev_axes = {axis: previous for axis, _, previous in axis_rows}
        articles = _article_rows(conn, conds)
    status = "final" if w["status"] == "closed" and editorial and editorial.get("final") else w["status"]
    end_day = (w["end"] - timedelta(days=1)).date()
    return {"kind": kind, "period": period, "label": w["label"], "status": status,
            "start": w["start"].date().isoformat(), "end": end_day.isoformat(),
            "through": min(w["cutoff"].date(), end_day).isoformat(),
            "data_as_of": latest.astimezone(KST).isoformat(timespec="minutes") if latest else "",
            "kept": total, "editorial": editorial, "articles": articles,
            "series": [{"date": d, "count": n} for d, n in sorted(buckets.items())],
            "previous": {"period": current_period(kind, w["previous_start"]), "kept": prev_total,
                         "start": w["previous_start"].date().isoformat(),
                         "until_exclusive": w["previous_end"].isoformat(),
                         "same_elapsed": w["status"] == "in_progress"},
            "axes": [{"axis": a, "n": current_axes.get(a, 0), "previous": prev_axes.get(a, 0),
                      "share": round(current_axes.get(a, 0) / total * 100, 1) if total else 0,
                      "previous_share": round(prev_axes.get(a, 0) / prev_total * 100, 1) if prev_total else None}
                     for a in ("ai", "bigdata", "smallbiz")]}


def available_periods(engine: Engine, kind: str, now: datetime | None = None) -> list[dict[str, str]]:
    if kind not in {"weekly", "monthly"}:
        raise ValueError("기간 종류가 올바르지 않습니다.")
    periods = {current_period(kind, now)}
    for path in EDITORIAL_DIR.glob("*.json"):
        if ("W" in path.stem) == (kind == "weekly"):
            periods.add(path.stem)
    with engine.connect() as conn:
        for p in conn.execute(select(digests.c.week)).scalars():
            if p and ("W" in p) == (kind == "weekly"):
                periods.add(p)
        earliest = conn.execute(select(func.min(items.c.published_at)).where(items.c.kept.is_(True))).scalar_one_or_none()
    cursor = period_window(kind, current_period(kind, now), now)["start"]
    # 최근 24개월/104주를 기본 탐색 범위로 제공. 그 이전 직접 작성 리뷰는 위에서 추가.
    for _ in range(24 if kind == "monthly" else 104):
        if earliest and cursor.astimezone(timezone.utc).replace(tzinfo=None) < earliest.replace(tzinfo=None):
            break
        periods.add(current_period(kind, cursor))
        cursor = period_window(kind, current_period(kind, cursor), now)["previous_start"]
    return [{"period": p, "label": period_window(kind, p, now)["label"]} for p in sorted(periods, reverse=True)]


def issue_data(engine: Engine, query: str, days: int = 90, now: datetime | None = None,
               page: int = 1, size: int = 50) -> dict[str, Any]:
    query = query.strip()
    if not query or len(query) > 100 or not 7 <= days <= 365 or page < 1:
        raise ValueError("키워드(1~100자)와 기간(7~365일)을 확인해 주세요.")
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    conds = (*_conditions(start, now), or_(items.c.title.contains(query, autoescape=True),
                                          items.c.summary.contains(query, autoescape=True)))
    size = min(100, max(1, size))
    with engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(items).where(*conds)).scalar_one()
        articles = _article_rows(conn, conds, size, True, (page - 1) * size)
        sources = [{"source": s, "count": n} for s, n in conn.execute(select(items.c.source, func.count())
                    .where(*conds).group_by(items.c.source).order_by(func.count().desc()))]
    return {"query": query, "days": days, "page": page, "size": size, "total": total,
            "articles": articles, "sources": sources, "through": now.astimezone(KST).date().isoformat()}


def task_candidates(engine: Engine) -> list[dict[str, Any]]:
    candidates: dict[str, dict] = {}
    with engine.connect() as conn:
        rows = conn.execute(select(digests.c.week, digests.c.body).order_by(digests.c.week.desc()).limit(60)).all()
    for period, body in rows:
        for task in (body or {}).get("tasks", []):
            key = period + ":" + task.get("title", "")
            candidates[key] = {**task, "key": key, "period": period, "sources": []}
    for path in sorted(EDITORIAL_DIR.glob("*.json")):
        review = read_editorial(path.stem)
        for task in (review or {}).get("tasks", []):
            key = review["period"] + ":" + task["title"]
            candidates[key] = {**task, "key": key, "period": review["period"],
                               "sources": [s for s in review.get("sources", []) if s["id"] in task.get("source_ids", [])]}
    return sorted(candidates.values(), key=lambda t: t["period"], reverse=True)
