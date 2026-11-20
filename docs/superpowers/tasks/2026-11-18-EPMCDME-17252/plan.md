# Hosted-app categories (EPMCDME-17252) Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** Hosted apps carry 0–3 shared-platform categories, need 1–3 to publish, filter by category in every catalog scope, and block category deletion while live. Spec: `spec.md` in this folder is the acceptance record.

**Architecture:** JSONB `categories` column + GIN (workflow shape). One shared annotated type in `rest_api/models/category.py` owns shape rules. One async hosted helper resolves ids → names for existence checks, the published-needs-one check, filter validation and the reply. `delete_category` gains a hosted entry with a named liveness term.

**Tech Stack:** FastAPI, pydantic v2, SQLModel/SQLAlchemy async, Alembic (raw SQL), pytest-asyncio. Hosted service tests need `HOSTED_TEST_DATABASE_URL`.

Commit per task using the repository's existing convention. Paths below are relative to `src/codemie/` unless they start with `src/external`, `tests/` or `docs/`.

Negative constraints honored (spec Non-goals): no UI/SDK work; `validate_category_ids` and its callers untouched (Task 3/4 use the new helper); no row locks added to `delete_category` (Task 7 only corrects the docstring); `get_category_stats` untouched (Task 7); workflow/assistant request models untouched (Task 1); stale ids never stripped from stored rows (Task 4); no bulk backfill (Task 2 defaults `[]`).

---

### Task 1: Shared category-selection type

**Files:** Modify `rest_api/models/category.py` (next to `Category`, :39). Test: `tests/codemie/rest_api/models/test_category_ids.py` (new).

Test-first: yes — a pydantic model with `CategoryIds` refuses 4 ids with "Maximum 3 categories allowed" and `["a","a"]` with "category IDs must be unique"; `RequiredCategoryIds` with default `[]` and `validate_default=True` refuses `{}` and `[]` with "At least one category is required"; an id of 257 chars or a non-string is refused.

- [ ] Write the failing tests; run them; confirm they fail on import.
- [ ] Add the new symbols:

```python
CATEGORY_ID_MAX_LENGTH = 256
MAX_CATEGORIES = 3
TOO_MANY_CATEGORIES = "Maximum 3 categories allowed"
DUPLICATE_CATEGORIES = "category IDs must be unique"
CATEGORY_REQUIRED = "At least one category is required"
CategoryId = Annotated[StrictStr, Field(min_length=1, max_length=CATEGORY_ID_MAX_LENGTH)]

def _selection(ids: list[str]) -> list[str]:
    if len(ids) > MAX_CATEGORIES: raise ValueError(TOO_MANY_CATEGORIES)
    if len(set(ids)) != len(ids): raise ValueError(DUPLICATE_CATEGORIES)
    return ids

def _required(ids: list[str]) -> list[str]:
    if not ids: raise ValueError(CATEGORY_REQUIRED)
    return _selection(ids)

CategoryIds = Annotated[list[CategoryId], AfterValidator(_selection)]
RequiredCategoryIds = Annotated[list[CategoryId], AfterValidator(_required)]
```

- [ ] Run the tests; confirm pass. Do not touch workflow/assistant request models.

### Task 2: Column, migration and parity tests

**Files:** Create `src/external/alembic/versions/<rev>_add_hosted_application_categories.py`. Modify `rest_api/models/hosted_application.py:66-148`, `tests/codemie/service/hosted_applications/conftest.py:102-151`, `tests/codemie/service/hosted_applications/test_schema_parity.py:112-184`.

Test-first: yes — `test_hosted_migrations_follow_the_platform_head` asserts the new revision's `down_revision` equals the current platform head, and the parity test fails until the model has the column.

- [ ] Rewrite both positional tests (:113, :150) to load migrations by revision name (dict `revision -> module` built from `HOSTED_MIGRATION`), keep their existing assertions, pin the new revision, and add a categories downgrade/upgrade round trip (column and `ix_hosted_applications_categories` gone, then back). Run; confirm failure.
- [ ] Migration: `down_revision` = output of `alembic heads` at implementation time (a chain head search today ends at `c6dee3b8b5f2`; confirm and pin it in the head test). `upgrade()` starts with `SET LOCAL lock_timeout` as `b4c8e1f2a9d3_add_conversation_pause_tables.py:147`, then `ALTER TABLE hosted_applications ADD COLUMN categories JSONB NOT NULL DEFAULT '[]'::jsonb` and `CREATE INDEX ix_hosted_applications_categories ON hosted_applications USING gin (categories)`; `downgrade()` drops both. Raw SQL like `e3b7c1a95d42`.
- [ ] Model: `categories: list[str]` JSONB, `server_default '[]'`, not nullable, plus the GIN `Index` in `__table_args__`, matching the migration exactly.
- [ ] Append the file to `HOSTED_MIGRATION`; in the `database` fixture also create `Category.__table__` beside `users`/`user_projects` (:150) and seed two categories for tests.
- [ ] Run parity, head and round-trip tests; confirm pass.

### Task 3: Category lookup helper, error code and reply

**Files:** Create `service/hosted_applications/categories.py`. Modify `service/hosted_applications/projections.py:33-74`, `service/hosted_applications/catalog.py:360-378,550-560`. Test: `tests/codemie/service/hosted_applications/test_categories.py` (new), `test_projections.py`.

Test-first: yes — `categories_by_id(tx.session, ["b","missing","a"])` on the hosted AsyncSession returns `{"b": name, "a": name}` (proves `select(Category)` maps there); `app_projection(..., category_names=...)` includes `categories: [{"id","name"}]` in stored order, skipping missing ids, for a non-manager viewer.

- [ ] Write failing tests; run.
- [ ] New module:

```python
INVALID_CATEGORIES = "INVALID_CATEGORIES"
PUBLISHED_NEEDS_ONE = "A published application needs at least one category"

async def categories_by_id(session, ids) -> dict[str, str]:
    """Like enrich_categories: one IN query, missing ids skipped; callers keep stored order."""

async def require_existing(session, ids) -> dict[str, str]:
    """Raise HostedApplicationError(INVALID_CATEGORIES, f"Invalid category IDs: {missing}", 422)."""
```

- [ ] `app_projection` gains keyword `category_names: dict[str, str]` and emits `[{"id": i, "name": names[i]} for i in app.categories if i in names]` for every viewer. `_project` resolves names for its app; `_project_page` batches one lookup for the page into `tx.category_names`, the way `tx.covered` is set (:559), and `_project` reads it when present.
- [ ] Run tests (incl. existing `test_projections.py`, `test_catalog.py`); confirm pass.

### Task 4: Create and patch

**Files:** Modify `rest_api/models/hosted_application_requests.py:57-104`, `service/hosted_applications/catalog.py:380-464`. Test: `tests/codemie/core/hosted_applications/test_requests.py`, `tests/codemie/service/hosted_applications/test_catalog.py`.

Test-first: yes — create/patch with 0–3 existing ids stores them and replies with names; an unknown sent id → `INVALID_CATEGORIES` and the app unchanged; PATCH of a published app to `[]`, or a PATCH not touching categories on a published app whose stored ids no longer exist (legacy `[]` included) → `INVALID_CATEGORIES` "A published application needs at least one category"; the same PATCH succeeds when one stored id still exists and never fails with "Invalid category IDs" for unsent ids; a create body without categories produces the same digest as before (assert the operation payload has no `categories` key).

- [ ] Write failing tests; run.
- [ ] `AppCreate.categories: CategoryIds = []`; `AppPatch.categories: CategoryIds | None` (optional) and add `categories` to `has_changes` (:100).
- [ ] `create_app`: payload at :389 becomes the full dump with `categories` popped when empty; after the replay check, `require_existing` on sent ids; set `categories=request.categories` on the new `App`.
- [ ] `patch_app`: after `check_etag`, if `categories` in `changes`, `require_existing` on sent ids; if `app.is_global`, compute the post-change list and refuse with `PUBLISHED_NEEDS_ONE` when `categories_by_id` of it is empty, before any `setattr`.
- [ ] Run tests; confirm pass.

### Task 5: Publish body

**Files:** Modify `rest_api/models/hosted_application_requests.py`, `service/hosted_applications/publication.py:104-111`, `rest_api/routers/hosted_applications.py:516-525`. Tests: `test_publication.py`, plus the `.publish(` call sites in `test_issuer_change.py`, `test_attestation.py`, `test_thumbnail.py`, `test_serving_policy_sharing.py`, `test_access_matrix.py`, `test_activation.py`; router test in `tests/codemie/rest_api/routers/test_hosted_applications_router.py`.

Test-first: yes — publish with 1–3 ids sets `is_global` and exactly those categories (re-publish replaces); unknown id → `INVALID_CATEGORIES`, app unchanged; same key + different categories (or order) → 409 `IDEMPOTENCY_CONFLICT`; unpublish keeps categories; router: `{}` and four ids → 422 `INVALID_REQUEST` with the Task 1 messages in `fields[]`.

- [ ] Write failing tests; rename `test_publish_sets_only_is_global_and_leaves_shared_and_the_access_revision` (:70) to reflect that publish also sets categories; run.
- [ ] Add `PublishRequest(RequestModel)` with `categories: RequiredCategoryIds = []` and `model_config` keeping `extra="forbid"` plus `validate_default=True`.
- [ ] `publish(identity, app_id, request, if_match, key)`: payload `{"categories": request.categories}`; in `change`, `suspend_refuses`, `require_existing`, then `is_global = True` and `categories = request.categories`. Route passes `body: PublishRequest`. Update every listed call site to pass a `PublishRequest` with a seeded category.
- [ ] Run tests; confirm pass.

### Task 6: Catalog category filter

**Files:** Modify `service/hosted_applications/catalog.py:104-147,179-196,466-539`, `rest_api/routers/hosted_applications.py:183-214`. Tests: `test_catalog.py`, `test_query_plans.py`, router test.

Test-first: yes — in each of mine/project/marketplace/shared, `category=[a,b]` returns only apps carrying a or b, combined with `project`; uncategorized apps excluded; unknown id → `INVALID_FILTER` even with a cursor present; a cursor issued under `[a]` is refused (`INVALID_CURSOR`) under `[a,b]`; paging under one selection neither repeats nor skips; a cursor issued with no category still works (no `category` key in `cursor_scope`); unfiltered list still includes published uncategorized apps; EXPLAIN case for a 50-id filter in marketplace and project scopes.

- [ ] Write failing tests; run.
- [ ] Generalize `normalize_projects` to `normalize_values(value, max_items, max_text)`; callers use `(MAX_PROJECT_FILTERS, MAX_FILTER_TEXT)` for project and `(50, CATEGORY_ID_MAX_LENGTH)` for category.
- [ ] `list_filters(..., categories=())` adds `or_(*(apps.c.categories.contains([bindparam(...)]) ...))` with each id a bound parameter (as `compose_json_array_filter` does). `_valid_list_filters` refuses `categories is None`; the filter is allowed on all scopes.
- [ ] `list_apps(..., category=None)`: normalize; add `"category"` to `cursor_scope` only when non-empty; inside the transaction, before building the cursor clause, refuse with `INVALID_FILTER` when `categories_by_id` misses any id. Router adds `category: list[str] | None = Query(None)` like `project`.
- [ ] Run tests; confirm pass.

### Task 7: Category deletion guard

**Files:** Modify `service/assistant/category_service.py:238-287`. Test: `tests/codemie/service/assistant/test_category_service.py:306-396`.

Test-first: yes — deleting a category used only by a live hosted app → 409 "Cannot delete category" with "assigned hosted applications (<name>)"; a hosted app with `deleted_at` set is not counted; existing assistant/workflow cases pass with the extra `exec` results inserted into their ordered `side_effect` lists.

- [ ] Write/adjust tests; run.
- [ ] Turn the loop into `(label, model, live)` entries: `None` for assistants and workflows, `HostedApplications.deleted_at.is_(None)` for `("hosted applications", HostedApplications)` (lazy import beside the others). Apply `live` to both the count and the names query. Fix the docstring (no row locks; covers assistants, workflows and hosted apps). Leave `get_category_stats` untouched.
- [ ] Run tests; confirm pass.

### Task 8: Docs and OpenAPI baseline

**Files:** `docs/hosted-applications/architecture.md` (:551-569), `tests/codemie/rest_api/router_guards/baselines/openapi.json`.

Test-first: no — update publication (publish takes 1–3 categories, replaces them, unpublish keeps them, digest over categories as sent), lists (`category` any-of filter on all scopes, in cursor scope when non-empty), the deletion guard, and the new column; then regenerate the baseline with `regenerate_baselines.py` and confirm `test_openapi_snapshot.py` passes.
