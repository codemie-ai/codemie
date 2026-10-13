# Router guard tests (EPMCDME-15651, step 0) Implementation Plan

**Goal:** Two guards for every later split step: a full OpenAPI snapshot and a route-precedence baseline, both from `main.py` built with the conditional routers on. Base commit `e55bea3005`. Inputs: `spec.md`, `technical-analysis.md` (same dir).

**Architecture:** A fresh-interpreter probe builds the app under a pinned profile and dumps `app.openapi()` plus the ordered route table as JSON (precedent: `tests/codemie/rest_api/test_main_retrieval_backend_disabled.py`; `main.py` registers Prometheus collectors at import, so no in-process reload). Two pure helpers are unit-tested alone; two guard tests compare the dump with committed baselines.

**Conventions:** `G` = `tests/codemie/rest_api/router_guards/`, imported as `tests.codemie.rest_api.router_guards.<module>`; every new file lives there. Nothing under `src/` changes. New `.py` files start with the Apache header (copy L1-13 of `test_main_retrieval_backend_disabled.py`). Per task: write the named tests, run `poetry run pytest <file> -n 0` and see them fail, implement, re-run to pass. Commit per task using the repository's existing convention.

```python
# G/baseline.py:       canonical_json(obj) -> str;  assert_matches_baseline(live, path) -> None
# G/route_overlap.py:  paths_overlap(a, b) -> bool;  overlapping_pairs(routes) -> list[OverlapPair]
# G/guarded_app.py:    load_guarded_app_dump() -> dict   (cached: openapi, routes, contacts_attempted)
```

### Task 1: Baseline compare helper

**Files:** Create `G/__init__.py` (header only), `G/baseline.py`, `G/test_baseline.py`.

Test-first: yes — with `tmp_path`: equal live/file passes; one changed value raises AssertionError with `-`/`+` diff lines and the regenerate command; the file's bytes are unchanged after that failure; `canonical_json` has sorted keys, 2-space indent, trailing newline.

- [ ] Implement: exact text equality of `canonical_json(live)` and the file, no normalization, no update flag or env switch; `difflib.unified_diff` capped near 200 lines; message names `python -m tests.codemie.rest_api.router_guards.regenerate_baselines`.

### Task 2: Path overlap matcher

**Files:** Create `G/route_overlap.py`, `G/test_route_overlap.py`.

Test-first: yes — `paths_overlap`: `/c/users` vs `/c/{id}` true; `/c/users` vs `/c/{id}/x` false; `/c/{x}` vs `/c/{y}/b` false; `/c/folder/{f:path}` vs `/c/{id}/history/{i}` true. `overlapping_pairs` (`OverlapPair(method, earlier, later)`): GET and POST on one path are not paired; generic-first input order is kept, not corrected; an exact duplicate (method, path) collapses to its first occurrence.

- [ ] Implement as an exact intersection of the two Starlette patterns: `{x}` = one or more non-`/` characters, `{x:path}` = any characters (empty and `/` included), any other converter treated as `{x}` (over-reports, never under-reports). Prune first on aligned plain-literal segments so the all-pairs pass (about 1k routes, grouped by method) takes seconds.

### Task 3: Guarded app probe

**Files:** Create `G/app_probe.py`, `G/guarded_app.py`, `G/test_guarded_app_profile.py`.

Test-first: yes — the dump holds `GET /v1/index/{index_id}`, `POST /v1/local-auth/login`, `POST /v1/workflows/generate`, one path each from `budget_router.py` and `user_management_router.py`, and `contacts_attempted == []`; fails (ModuleNotFoundError) until the probe exists. Covers criteria 5 and 6.

- [ ] `app_probe.py` (script, output path in `argv[1]`): stub optional native packages as `tests/conftest.py` L33-37; patch `PostgresClient.get_engine`, `StateImportService.import_indexes` (runs at `main.py` L1027-1028) and `codemie.enterprise.litellm.is_litellm_enabled` to True (from-imported at `main.py` L1079, so patch before importing); make `socket.socket.connect`/`connect_ex` record the address in `contacts_attempted` and raise; import `codemie.rest_api.main`; write `jsonable_encoder(app.openapi())`, routes with `.methods` as `{path, methods}` in `app.routes` order, and `contacts_attempted`.
- [ ] `guarded_app.py`: `PROFILE_ENV` over a scrubbed environment (PATH, HOME, `PYTHONPATH=src`; nothing else inherited). Pins: `ENABLE_USER_MANAGEMENT=true`, `IDP_PROVIDER=local`, `RETRIEVAL_BACKEND=elasticsearch`, `ELASTIC_URL`, `LLM_PROXY_ENABLED=true`, `LLM_PROXY_BUDGET_CHECK_ENABLED=true`, `WORKFLOW_GENERATION_ENABLED=true`, `MCP_AUTH_ENABLED=false`, `MCP_AUTH_TMS_ENABLED=false`, `ENV=local`, and fixed values for config that reaches the schema: `APP_VERSION` (constant, so release bumps do not break the snapshot), `API_ROOT_PATH`, `PASSWORD_MIN_LENGTH`, `ANALYTICS_DEFAULT_PAGE_SIZE`. `load_guarded_app_dump()` is a `functools.cache` over `subprocess.run([sys.executable, probe, out], timeout=180)`; failure raises with the child's stderr.
- [ ] If flags-on cannot be made hermetic, fall back to the `tests/.env.test` profile, list the uncovered routers in the `guarded_app.py` docstring, narrow the test, and report the gap for the MR (spec fallback).

### Task 4: OpenAPI snapshot guard

**Files:** Create `G/regenerate_baselines.py`, `G/test_openapi_snapshot.py`, `G/baselines/openapi.json`.

Test-first: yes — `test_openapi_matches_baseline` fails (baseline missing) until the baseline is generated on the base tree.

- [ ] The test's docstring lists the compared surface: paths, operationIds, tags including `Assistant Versioning`, schemas, components.
- [ ] `regenerate_baselines.py`: `python -m` entry writing `canonical_json` to `baselines/openapi.json`; its docstring documents manual regeneration (only for an intended, reviewed contract change, such as the step 1 `PaginationInfo` renames). No `test_` name; pytest never runs it. Generate only while `git diff --quiet e55bea3005 -- src` holds.
- [ ] Environment check: copy the baseline outside the repo, regenerate with `ANALYTICS_DEFAULT_PAGE_SIZE=7 PASSWORD_MIN_LENGTH=9 APP_VERSION=x` exported, `cmp`; a difference is an unpinned input for `PROFILE_ENV`.
- [ ] Mutation demo, local, reverted, not committed: rename a router handler (operationId changes), separately change a tag; the test fails with a diff naming it and `git status` shows the baseline unmodified; then `git checkout -- src` and confirm `git diff --quiet -- src`.

### Task 5: Route precedence guard

**Files:** Modify `G/regenerate_baselines.py` (also write `baselines/route_precedence.json`, a list of `{method, earlier, later}` in live order, and print the pair total). Create `G/test_route_precedence.py`, `G/baselines/route_precedence.json`.

Test-first: yes — `test_baseline_order_is_preserved` and `test_baseline_contains_hand_reproduced_pairs` fail (baseline missing) until it is generated on the base tree.

- [ ] Tests. (a) Every baseline pair: both routes exist live (else fail "baseline route missing: METHOD path") and `earlier` still precedes `later`. (b) Live pairs absent from the baseline go to `warnings.warn`, never fail. (c) These 16 pairs, paths under `/v1`, earlier then later, are in the baseline:
  - POST `/assistants/virtual/model` then `/assistants/{assistant_id}/model`
  - GET `/assistants/id/{assistant_id}`, `/assistants/prebuilt/{slug}`, `/assistants/metrics/{conversation_id}`, each then `/assistants/{assistant_id}/versions` (3)
  - GET `/assistants/slug/{assistant_slug:path}` then `/assistants/{assistant_id}/versions`, `.../versions/{version_number}`, `.../versions/{version1}/compare/{version2}` (3)
  - GET `/index/users`, `/index/find_id`, each then `/index/{index_id}` (2)
  - GET `/conversations/search`, `/conversations/new`, each then `/conversations/{conversation_id}` (2)
  - PUT and DELETE `/conversations/{conversation_id}/history` and `.../history/{history_index}`, each then same-method `/conversations/folder/{folder:path}` (4, generic first, recorded as is)
  - PUT `/conversations/folder/{folder:path}` then `/conversations/{conversation_id}/messages/{history_index}/output`
- [ ] A missing pair means the matcher or `technical-analysis.md` is wrong: resolve before committing. Do not assert the ticket's "40"; note the printed total for the MR.
- [ ] Mutation demo, local, reverted, not committed: move `GET /index/{index_id}` (`index.py` L256) above `GET /index/users` (L229); the test fails naming that pair; then `git checkout -- src` and confirm `git diff --quiet -- src`.

**Negative-constraint pass:** no `src/`, `main.py` or existing-test edit (probe patches at runtime); snapshot never rewritten (Task 1, Task 4 demo); generic-first pairs recorded, not fixed (Tasks 2, 5); new pairs warn, not fail (5b); no network, DB or Elasticsearch (Task 3); profile independent of the developer environment (Tasks 3, 4); mutation demos never committed; no runtime or auth tests; "40" not asserted; second-priority files untouched.
