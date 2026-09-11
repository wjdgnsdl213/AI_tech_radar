# Navigation implementation — 2026-09-11

Plan: 2026-09-11-navigation-simplification.md
User approved proceeding in current folder, preserving preceding uncommitted edits. No push, deployment, or model calls.

## Interface review

| Work | Producer / consumer | Decision |
| --- | --- | --- |
| Briefing + navigation | Workspace review view / app route and menu | Single weekly-default review view, canonical routes with old aliases |
| Explore + backend | Unified query / search and keyword endpoints | Inclusive KST date bounds; oldest ordering shares search conditions |
| Graph + date selector | Precomputed whole-corpus relations / dated article results | Keep relation algorithm; label whole-corpus scope separately from filtered article dates |
| Personal storage + navigation | Existing ResearchStore / new follows and work screens | Keep storage schema and key unchanged |

## Progress

- Backend: 9 regression tests pass. KST bounds, search/export parity, literal query, relevance/chronological ordering, keyword date/axis total, null ordering and stable paging.
- Frontend: four-menu implementation and 20 Node tests pass; Chrome verified primary workflows, mobile, saved data and old links.
- Review: backend null sorting findings fixed. Frontend stale responses, graph date presets and startup failure visibility fixed; scoped reviewer approved after four actual-source deferred-response checks, including blank/invalid graph contexts and both nested empty-state awaits.
- Integration details: see navigation-qa.md.

## Test evidence

`python -m pytest tests/ -q`: 55 passed. Existing FastAPI startup deprecation warnings only.
`node --test tests/navigation-state.test.cjs tests/workspace.test.cjs tests/review-format.test.cjs`: 20 passed.
`python -m scripts.verify_navigation`: article/timeline/CSV 95-result parity; keyword evidence 3 of 60; passed without model calls or writes.
`python -m scripts.verify_reviews`: 10 unique source IDs and exact URLs verified; nine endpoints returned 200.
