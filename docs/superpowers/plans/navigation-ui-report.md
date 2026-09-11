# Navigation simplification UI report

Date: 2026-09-11

## Implemented

- Replaced the eight-item desktop/mobile navigation with four destinations: **브리핑 / 탐색 / 법령·규제 / 작업실**.
- Made the existing API-free Workspace review renderer the only visible briefing surface. Weekly is the default; weekly/monthly kind and period are encoded in the canonical hash.
- Kept monthly HTML/Markdown report links in the monthly briefing.
- Removed repeated briefing evidence: a source is rendered only at its first editorial citation, cited sources are excluded from the lower article list, and only uncited sources remain in the source detail.
- Collapsed comparison metrics and daily charts into detail controls and limited briefing task candidates to three. Task editing remains in 작업실.
- Moved collection health into a collapsible navigation footer.
- Added one persistent Explore context (keyword, axis, since, until) shared by article, chronological timeline, and graph views. The global search is hidden on Explore to avoid a second search entry.
- Added the chronological view using `/api/search?order=oldest` with the same curated search filters as the article view.
- Preserved graph relation metrics as whole-corpus values and labelled them as such. Graph evidence sends axis/since/until to `/api/keyword/{keyword}` and is labelled as all collected matching articles, while article/timeline remain curated (`kept_only=1`).
- Added an Explore start page with latest weekly rising topics, browser-local follows, separately labelled server alert keywords, and secondary rising/organization/cross discovery options.
- Preserved the `sab-research-v1` storage schema, saved scraps, notes, follows, task history, backup/restore, and exports.
- Added canonical routing plus compatibility for `#home`, `#digest`, `#reviews/...`, `#month/...`, `#search`, `#graph`, `#issues/...`, `#analysis/...`, `#trend`, and legacy `?week=` links.
- Guarded every asynchronous Explore renderer with latest-request tokens and captured context so older search, timeline, graph, keyword-evidence, or regulatory-crosslink responses cannot overwrite a newer query or view.
- Added a visible, retryable global startup error when `/api/meta` is unavailable.

## Files

- `web/static/index.html` — four-item navigation and integrated briefing/explore markup.
- `web/static/app.js` — canonical routing integration, shared Explore context, timeline rendering, discovery start, error/empty states, and health footer loading.
- `web/static/workspace.js` — weekly-default briefing, evidence deduplication, candidate preview, report links, follow integration, and links into Explore.
- `web/static/workspace.css` — Explore, collection-health, responsive, and validation styles.
- `web/static/navigation-state.js` — DOM-free route/context normalization and serialization.
- `tests/navigation-state.test.cjs` — legacy compatibility, round-trip context, period handling, and date validation tests.

## Verification

- `node --check web/static/app.js`
- `node --check web/static/workspace.js`
- `node --check web/static/navigation-state.js`
- `node --test tests/navigation-state.test.cjs tests/workspace.test.cjs tests/review-format.test.cjs` — 20 passing tests in the final local run before this report, including deferred-response, view-invalidation, and escaped startup-error behavior.
- `python -m scripts.verify_navigation` — article/timeline/CSV parity and filtered keyword evidence passed against the local API.
- Duplicate-ID scan of `index.html` returned no duplicates; the top-level navigation count is four.
- Independent Chrome checks by the root agent confirmed four navigation items, preserved two existing saved articles, weekly/monthly/past-period briefing, legacy digest canonicalization, report links, collapsed metrics, task candidate count, graph context resubmission, graph rendering, and date-filtered keyword evidence.

## API contracts used

- Articles: `/api/search?q=&axis=&since=&until=&kept_only=1&page=&size=` (default relevance order).
- Timeline: the same request plus `order=oldest`.
- Graph relations: `/api/ego?kw=&hops=&per_hop=` with no date parameters because the relation index is whole-corpus.
- Graph evidence: `/api/keyword/{keyword}?axis=&since=&until=&limit=40`.

## Limitations and deliberate boundaries

- The old home/digest/month/issues DOM remains unreachable as compatibility scaffolding because existing event bindings depend on many of its IDs. It has no top-level navigation or canonical route; all old hashes normalize to the new surfaces.
- Graph relations cannot be date-filtered without recomputing the precomputed neighbor index. The UI explicitly separates whole-corpus relationship metrics from date-filtered evidence.
- Article and timeline views retain the previous curated-only search behavior, while graph evidence retains its previous all-collected-articles behavior. This distinction is stated in the graph view.
- Rising topics are the latest weekly scope; organization and cross discovery are fixed recent-eight-week metrics. These scopes are labelled and do not pretend to follow the shared date range.
- Personal follows remain browser-local and do not edit server alert configuration.
