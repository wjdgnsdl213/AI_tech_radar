"""실제 서비스·리뷰 근거 검증. GET/SELECT만 사용하며 모델을 호출하지 않는다."""
from __future__ import annotations
import argparse
import json
import time
import httpx
from sqlalchemy import select
from src.db import get_engine, items
from src.reviews import EDITORIAL_DIR, read_editorial


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8021")
    args = parser.parse_args()
    refs = {}
    for path in EDITORIAL_DIR.glob("*.json"):
        review = read_editorial(path.stem)
        refs.update({s["id"]: s for s in review["sources"]})
    with get_engine().connect() as c:
        rows = c.execute(select(items.c.id, items.c.url).where(items.c.id.in_(refs))).all()
    assert set(refs) == {r.id for r in rows}, "리뷰에 없는 기사 ID가 있습니다."
    for r in rows:
        assert r.url == refs[r.id]["url"], f"근거 URL 불일치: {r.id}"
    print(f"verified {len(refs)} unique source IDs and exact database URLs")
    with httpx.Client(base_url=args.base, timeout=45) as client:
        for url in ("/api/reviews?kind=monthly&period=2026-09", "/api/reviews?kind=weekly&period=2026-W37",
                    "/api/reviews?kind=weekly&period=2026-W36", "/api/reviews/periods?kind=weekly",
                    "/api/issues?q=AI&days=30", "/api/task-candidates", "/api/monthly?month=2026-09", "/report.md?month=2026-09",
                    "/report?month=2026-09"):
            start = time.monotonic()
            response = client.get(url)
            assert response.status_code == 200, f"{url}: {response.status_code}"
            if url.startswith("/api/reviews?"):
                data = response.json()
                assert data["editorial"] and not data["editorial"].get("legacy")
                assert data["editorial"]["sources"]
            if url.startswith("/report"):
                assert "9월 1~11일" in response.text
                assert "3·4절은 예시" not in response.text
            print(json.dumps({"url": url, "status": response.status_code,
                              "seconds": round(time.monotonic() - start, 2)}, ensure_ascii=False))
        for url in ("/api/reviews?kind=monthly&period=2026-13", "/api/reviews?kind=weekly&period=2026-W99",
                    "/api/issues?q=&days=90"):
            assert client.get(url).status_code == 422
    print("Read-only smoke checks passed")


if __name__ == "__main__":
    main()
