# Navigation backend review fixes — 2026-09-11

## Scope

- `web/api.py`: make chronological ordering explicit for null dates and preserve keyword ranking semantics for nullable database values.
- `web/server.py`: remove the unused UTC date parser and its now-unused imports.
- `tests/test_explore_api.py`: cover null-date placement, newest ordering, equal-key pagination, and nullable keyword ranking.

## Red evidence

Command:

`python -m pytest tests/test_explore_api.py -q`

Result before production changes: **2 failed, 6 passed**.

- `test_chronological_orders_put_unknown_dates_last`: SQLite returned `[10, 1, 2, 3, 4]` for oldest instead of `[1, 2, 3, 4, 10]`.
- `test_keyword_normalizes_null_kept_and_score_before_ordering`: raw nullable ordering returned `[31, 30, 32, 33]` instead of the normalized `[30, 31, 33, 32]`.

## Fix

- Added explicit `NULLS LAST` to oldest, newest, and date-based tie ordering.
- Ranked keyword rows by `coalesce(kept, false)` and `coalesce(cross_score, 0)`, matching the previous Python `bool(kept)` and `cross_score or 0` behavior across database engines.
- Retained item ID as the final deterministic tie-breaker.
- Removed `web.server._parse_date`, `timezone`, `and_`, and `or_` after confirming there were no remaining callers.

## Green evidence

Command:

`python -m pytest tests/test_explore_api.py -q`

Result after the fix: **8 passed, 4 warnings** in 0.64 seconds. The warnings are the existing FastAPI `on_event` deprecation warnings.

`git diff --check -- web/api.py web/server.py tests/test_explore_api.py` exited successfully. Git emitted only the existing line-ending conversion notices for `web/api.py` and `web/server.py`.
