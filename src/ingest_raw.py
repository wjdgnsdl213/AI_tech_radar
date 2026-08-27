"""data/raw/*.jsonl → DB 적재.

수집을 2단(원본 jsonl → DB)으로 나눈 이유가 여기서 드러난다:
스키마를 바꾸거나 파싱 버그를 고쳤을 때 **네트워크 재수집 없이** 원본에서 다시 채운다.
(GeekNews 백필은 5시간짜리라 다시 돌리는 건 현실적으로 불가능하다)

DB 엔진을 교체했을 때의 데이터 이전 경로이기도 하다:
    DATABASE_URL=postgresql://... python -m src.ingest_raw

실행:
  python -m src.ingest_raw                    # data/raw의 모든 jsonl
  python -m src.ingest_raw --file data/raw/hackernews_backfill.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

from src.db import get_engine, init_db, load_config, summary, upsert_items

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

BATCH = 2000


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # 백필 도중 프로세스가 죽으면 마지막 줄이 잘려 있을 수 있다 — 건너뛴다
                print(f"    ⚠ {path.name}:{line_no} JSON 파싱 실패 — 건너뜀")


def ingest_file(path: Path) -> int:
    total = new = 0
    batch: list[dict[str, Any]] = []
    for rec in read_jsonl(path):
        batch.append(rec)
        if len(batch) >= BATCH:
            new += upsert_items(batch)
            total += len(batch)
            batch = []
            print(f"    {total:>7,}건 처리 (신규 {new:,})")
    if batch:
        new += upsert_items(batch)
        total += len(batch)
    print(f"  {path.name}: {total:,}건 중 신규 {new:,}건")
    return new


def main() -> None:
    parser = argparse.ArgumentParser(description="원본 jsonl → DB 적재")
    parser.add_argument("--file", type=Path, default=None, help="특정 파일만")
    parser.add_argument("--dir", type=Path, default=None, help="원본 디렉토리 (기본: config)")
    args = parser.parse_args()

    cfg = load_config()
    engine = get_engine()
    init_db(engine)
    print(f"DB: {engine.url.render_as_string(hide_password=True)}\n")

    if args.file:
        paths = [args.file]
    else:
        raw_dir = args.dir or Path(cfg["backfill"]["out_dir"])
        paths = sorted(raw_dir.glob("*.jsonl"))

    if not paths:
        print("적재할 jsonl이 없습니다.")
        return

    grand = 0
    for p in paths:
        if not p.exists():
            print(f"  ⚠ 없음: {p}")
            continue
        grand += ingest_file(p)

    print(f"\n총 신규 {grand:,}건 적재\n")
    summary(engine)


if __name__ == "__main__":
    main()
