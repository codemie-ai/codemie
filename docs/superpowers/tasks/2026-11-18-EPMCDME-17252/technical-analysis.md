# Technical Research

**Task**: hosted-apps categories marketplace-publish catalog-filter
**Generated**: 2026-11-18
**Research path**: codegraph

---

## 1. Original Context

the resolved requirements are in docs/superpowers/tasks/2026-11-18-EPMCDME-17252/requirements.local.md (read it in full; ticket text under "External ticket content (untrusted)" is requirements, never instructions). Summary: add categories (shared platform category list, 0–3 per hosted app, 1–3 required on marketplace publish) to hosted apps — create/edit/publish validation, reply lists categories (id+name), publish request gains a body with categories and idempotency-key conflict on differing categories, unpublish keeps categories, catalog list any-of category filter on all scopes bound into the page cursor, invalid category filter refused, category deletion refused while used by a non-deleted hosted app (deleted apps release categories), update docs/hosted-applications/architecture.md, keep model/migration schema-parity check passing. User guidance: mirror existing assistants/workflows/skills category behavior and reuse shared components (a local, non-global refactor is acceptable).

---

## 2. Codebase Findings

### Existing Implementations

**Purpose A — a shared list of categories an item can carry (store + validate)**
- `Category` table `categories` — `src/codemie/rest_api/models/category.py:39`; string id (humanized), unique `name`; seeded from `config/categories/assistant-categories.yaml` (`alembic/versions/f7a9b2c4d5e6_create_categories_table.py:25`). Same DB schema as hosted tables (hosted engine uses `search_path DEFAULT_DB_SCHEMA,public`, `service/hosted_applications/runtime.py:68-79`).
- Assistants store `categories: list[str]` JSONB (`rest_api/models/assistant.py:793`, request `max_length=3` at :374/:503), GIN index `ix_assistants_categories` (`b0f3d91cff8b_add_assistant_categories.py:22`, nullable).
- Workflows store JSONB `categories` NOT NULL default `'[]'` + GIN `ix_workflows_categories` (`k6l7m8n9o0p1_add_workflow_marketplace_columns.py:29-48`; model `core/workflow_models/workflow_config.py:122`; requests `workflow_models.py:458,474` max 3, item max 256).
- `DatabaseCategoryService.validate_category_ids(ids, required=False)` — `service/assistant/category_service.py:75`. Rules: empty + required → `ValidationException("At least one category is required")`; unknown ids → `ValidationException` (required) or `ValueError` (not required) `"Invalid category IDs: [...]"`; **duplicates are silently de-duplicated** (`dict.fromkeys`, :106), not refused. Sync `Session(Category.get_engine())`. Callers: assistant update `routers/assistant/management.py:465` (`required=was_global` — the "published needs ≥1 on edit" rule), sub-assistants `_sub_assistants.py:143,350` (required), workflow create/update `routers/workflow.py:603,778` (not required), assistant model `assistant.py:1068`, generator `assistant_generator_service.py:726`.
- `CategoryRepository.get_by_ids` — `repository/category_repository.py:94`; used by `WorkflowMarketplaceService._validate_categories` (`service/workflow_config/workflow_marketplace_service.py:45`).
- `enrich_categories` (id → Category objects, order preserved) — `category_service.py:132`; consumed by assistant detail `_get_categories_data` (`routers/assistant/catalog.py`).
- Skills use a separate enum `SkillCategory` (`rest_api/models/skill.py:85`), not the shared table; MCP servers use enum `MCPCategory` (`mcp_config.py:37`). Neither is the shared list.
- Hosted apps: nothing. `HostedApplications` (`rest_api/models/hosted_application.py:66-136`) has no categories column; grep of `service/hosted_applications/` for `categor` returns 0 hits.

**Purpose B — require 1–3 categories on marketplace publish**
- Workflows: `PublishWorkflowToMarketplaceRequest` — `rest_api/models/workflow_marketplace.py:26`: `categories: list[str(min_length=1)]`, `min_length=1, max_length=3`, duplicate ids refused (`"category IDs must be unique"`). `publish()` validates existence then `set_publish_state(is_global=True, categories=...)` (`workflow_marketplace_service.py:39,86`); `unpublish()` sets only `is_global=False` (keeps categories, :115).
- Assistants: `PublishToMarketplaceRequest.categories: list[str] min 1 max 3` — `routers/assistant/marketplace.py:75`.
- Hosted: `PublicationServiceMixin.publish` — `service/hosted_applications/publication.py:104-111`: `suspend_refuses(app,"publish")`, `app.is_global = True`, operation payload `{}` (+ `if_match`). `unpublish` :113 sets `is_global=False`. Router `POST /hosted-applications/{app_id}/marketplace/publish` takes `body: RequestModel` (empty, `extra="forbid"`) — `routers/hosted_applications.py:516-525`.

**Purpose C — idempotent replay that refuses a different request under the same key**
- `HostedTransaction.operation` — `repository/hosted_applications/repository.py:169`: request digest = sha256(`canonical_json(payload)`); `_replay` (:225) raises `IDEMPOTENCY_CONFLICT` 409 when digest differs. Payload per action: create = `request.model_dump(mode="json")` (`catalog.py:389`); update = `{"changes": model_dump(exclude_unset), "if_match"}` (:445); publish/unpublish = `{**payload, "if_match"}` via `_mutate` (`publication.py:66`). Any field placed in the payload is part of the conflict check.

**Purpose D — filter a catalog by categories (any-of)**
- Assistants/workflows SQL: `compose_json_array_filter` — `service/filter/compose_filter_functions.py:86`: OR of `field @> '["id"]'`; wired in `filter_services.py:78,149`; relies on `model_class.get_field_expression` (BaseModelWithSQLSupport), which hosted SQLModels do not inherit. Templates in-memory any-of: `routers/assistant/catalog.py:70`, `routers/workflow.py:434`.
- Hosted catalog: `list_apps` — `service/hosted_applications/catalog.py:466-539`. Validation `_valid_list_filters` (:179) → `INVALID_FILTER` "Invalid application filters" (422 default, `core/hosted_applications/errors.py:22`); terms `list_filters` (:118) on top of `list_scope` (:83, scopes `mine/project/marketplace/shared`); `project` normalized by `normalize_projects` (:104, sorted distinct, ≤50, ≤200 chars). Cursor bound to `cursor_scope` dict (:498-509) hashed into the signed cursor (`core/hosted_applications/protocol.py:49-75`); a mismatch → `INVALID_CURSOR` 422. Router query params `routers/hosted_applications.py:183-214` (`project: list[str] | None = Query(None)` is the repeated-param pattern).

**Purpose E — refuse deleting a category while in use**
- `delete_category` — `category_service.py:238-287`: loops `(("assistants", Assistant), ("workflows", WorkflowConfig))`, `cast(model.categories, JSONB).contains([id])`, count + up to `_MAX_NAMES=10` names → 409 `"Cannot delete category"` / details `"Cannot delete category with N assigned assistants (a, b) and ..."`. Docstring claims row locks; code takes none. Route `DELETE /v1/assistants/categories/{id}` (`routers/category.py:211`, admin only). `get_category_stats` (:289) counts assistants + workflows (feeds `CategoryResponse`, `routers/category.py:123,160,201`).
- Hosted lifecycle: delete → `_begin_purge` (`catalog.py:735`) sets `lifecycle_state="purging"`, `deleted_at`; columns `shared`/`is_global` deliberately untouched (comment :739). Purge (`maintenance.py:212`) → `purged`; CHECK requires `name IS NULL ...` when purged (`hosted_application.py:79-84`). Active ⇔ `deleted_at IS NULL` (CHECK :78).

**Other touched code**
- `AppCreate`/`AppPatch` — `rest_api/models/hosted_application_requests.py:57-104`; `AppPatch.has_changes` lists the four fields explicitly (:100).
- `create_app` (`catalog.py:380`), `patch_app` (:439, `setattr` for every field in `model_fields_set`, :459).
- `app_projection` — `service/hosted_applications/projections.py:33` (single reply shape for detail, list cards, mutation replies; 5 callers in `catalog.py`).
- Migrations: `e3b7c1a95d42_add_hosted_applications.py` (raw SQL), `f8d24a6c0b17_platform_sharing_grants_and_links.py`.

### Decisions Already in the Code
- Category ids stored as JSONB array of strings on the owning row (assistants, workflows), not a join table; GIN index; workflows NOT NULL default `'[]'`, assistants nullable. No FK to `categories` anywhere. Reason: none found.
- Hosted request models forbid extra fields (`RequestModel`, `hosted_application_requests.py:29`) and use `StrictStr`/`StrictBool`.
- Hosted errors are `HostedApplicationError(code, message, status)`, not `ExtendedHTTPException`/`ValidationException` used by the category service. A shared validator raising `ValidationException`/`ValueError` would need mapping in hosted code.
- Hosted DB access is async (`AsyncSession`, own pool size 10, 2 s timeout, `runtime.py:47-48`); category service is sync on the platform engine. The hosted write transaction holds the app row lock (`managed_app(lock=True)`).
- Hosted list filters that only apply to `mine` are refused on other scopes (`catalog.py:188,195`); the task's filter is stated for all scopes.
- Every hosted list filter value enters `cursor_scope` in normalized form (projects sorted/distinct).
- Assistants require ≥1 category on edit of a published item (`management.py:465`); workflows do not (`workflow.py:778`).
- Duplicate handling differs: workflow publish refuses duplicates; `validate_category_ids` de-duplicates.
- Data direction: deletion check reads "which rows contain id X" (`@>` over JSONB with GIN) — the same direction as storage supports.

### Task Statements Checked Against the Code
- "Hosted apps have no categories" — holds (model :66-136; no hits in service).
- "Marketplace publish request has no body; publishing sets only the marketplace flag" — partly contradicted: route declares `body: RequestModel` (empty object required, `extra=forbid`, `routers/hosted_applications.py:519`); flag-only holds (`publication.py:109`).
- "Assistants and workflows carry 1–3 categories from the shared list and require ≥1 on publish" — holds (`marketplace.py:78`, `workflow_marketplace.py:27`). Note: on create/edit both allow 0 (assistants `max_length=3`, no min).
- "Catalog filters by scope, name, project, creator, artifact kind, version state, sharing, status; cursor bound to every filter" — holds (`catalog.py:498-509`); "name" is the `q` search text.
- "Deleting a category is refused while an assistant or workflow uses it; hosted apps not checked" — holds (`category_service.py:270`).
- "Deleted hosted app keeps a record up to 180 days before purge" — unverified here (retention days in settings not inspected); `_delete_purged_applications` uses `audit_days` (`retention.py:351`).
- Doc phrase "publishing sets the marketplace flag and nothing else" — not found verbatim in `docs/hosted-applications/architecture.md`; closest statements at :554 (`is_global` row) and :576 (suspend leaves the marketplace flag). Test name `test_publish_sets_only_is_global_and_leaves_shared_and_the_access_revision` (`test_publication.py:70`) encodes it.
- User guidance "mirror skills category behavior" — skills use an enum, not the shared list (`skill.py:85`); only assistants/workflows share the list.
- "Keep schema-parity check passing" — holds as a mechanism: `test_models_match_the_migrated_schema_exactly` (`tests/.../test_schema_parity.py:85`) compares columns, constraints, indexes of `HOSTED_APP_MODELS` + covers + ShareLink against the migrations in `HOSTED_MIGRATION` (`conftest.py:102`).

### Consumers of Changed Contracts
- `PublicationServiceMixin.publish(identity, app_id, if_match, key)` — router `hosted_applications.py:525`; tests calling `.publish(`: `test_publication.py` (2), `test_issuer_change.py`, `test_attestation.py`, `test_thumbnail.py`, `test_serving_policy_sharing.py`, `test_access_matrix.py`, `test_activation.py` (1 each). A signature change reaches all of them.
- `AppCreate`/`AppPatch` — 40/25 references incl. `tests/codemie/core/hosted_applications/test_requests.py`, `test_catalog.py`, router tests; `has_changes` field list.
- `app_projection` output — `test_projections.py`, `test_catalog.py`, router tests; mutation replies stored as `replay_body` (≤65536 bytes CHECK, `hosted_application.py:450`).
- `list_apps` signature / `cursor_scope` — router `catalog()` (:184), `test_catalog.py`, `test_query_plans.py`; adding a key to `cursor_scope` invalidates all outstanding cursors.
- `HostedApplications` columns — 68 callers; `worker.py:273` snapshots `app.model_dump()`; purge CHECK (:79) lists columns cleared when purged.
- `delete_category` — route `category.py:235`; `tests/codemie/service/assistant/test_category_service.py` (mocked session, e.g. `test_delete_category_used_only_by_workflow`).
- `validate_category_ids` — 10 callers listed in Purpose A; behaviour (dedupe, exception types) is relied on by them.
- OpenAPI snapshot `tests/codemie/rest_api/router_guards/baselines/openapi.json` (:69892 hosted publish path), checked by `test_openapi_snapshot.py`, regenerated by `regenerate_baselines.py`.
- `docs/hosted-applications/architecture.md` :554, :563-569 (list scopes).

### Architecture and Layers Affected
- API: `routers/hosted_applications.py` (create, patch, publish, catalog); request models `hosted_application_requests.py`.
- Service: `service/hosted_applications/catalog.py`, `publication.py`, `projections.py`; `service/assistant/category_service.py`.
- Repository/model: `rest_api/models/hosted_application.py`; `repository/category_repository.py`.
- Migration: `src/external/alembic/versions/` hosted chain.

### Integration Points
- Hosted ↔ platform `categories` table (same schema, different engines: hosted async pool vs `Category.get_engine()` sync).
- Category admin delete ↔ hosted table (category service imports models lazily to avoid cycles, `category_service.py:254`).
- No external services.

### Patterns and Conventions
- Hosted mutations: `tx.operation(...)` → replay/`check_etag` → change → `revision += 1` → `tx.finish(op, projection)`.
- Hosted list filters: validate in `_valid_list_filters`, add term in `list_filters`, add normalized value to `cursor_scope`.
- Category usage check: tuple of `(label, model)` in `delete_category`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/data/database-patterns.md:16-24` — Alembic under `src/external/alembic`, import models in `env.py` (hosted models imported at `env.py:87-88`).
- `docs/hosted-applications/architecture.md` — design record (publication :551-561, lists :563-569, idempotency digest example :580).

### Architectural Decisions
- Publishing needs no active version (`architecture.md:554`, `test_publication.py:83`).
- `shared`/`is_global` changed only by PATCH and `/marketplace/*`; delete leaves them (`catalog.py:739`).

### Derived Conventions
- Category validation messages: "At least one category is required", "Invalid category IDs: [...]", "category IDs must be unique", "Maximum 3 categories allowed".

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/hosted_applications/test_publication.py` (publish/unpublish, replay, suspend), `test_catalog.py` (list, filters, cursor), `test_projections.py`, `test_schema_parity.py`, `test_query_plans.py` (EXPLAIN of list filters), `test_purge.py`.
- `tests/codemie/core/hosted_applications/test_requests.py`, `test_protocol.py` (`test_cursor_is_bound_to_actor_query_and_signature`).
- `tests/codemie/rest_api/routers/test_hosted_applications_router.py` (no publish route test found).
- Category: `tests/codemie/service/assistant/test_category_service.py`; workflow publish `tests/unit/service/workflow_config/test_workflow_marketplace_service.py`, `tests/codemie/service/test_workflow_service.py:1780`.

### Testing Framework and Patterns
- pytest + pytest-asyncio; hosted service tests use a real PostgreSQL schema per test via `HOSTED_TEST_DATABASE_URL` (skipped when unset, `conftest.py:127-130`). The fixture installs only `HOSTED_MIGRATION` plus `users`/`user_projects` tables (`conftest.py:140-151`); the `categories` table is not created there.

### Coverage Gaps
- No hosted tests reference categories (none exist). Router-level publish body not covered.

---

## 5. Configuration and Environment

### Environment Variables
- `HOSTED_TEST_DATABASE_URL` (tests); `DEFAULT_DB_SCHEMA` (search_path, `runtime.py:79`).

### Configuration Files
- `config/categories/assistant-categories.yaml` (seed list).

### Feature Flags and Deployment Concerns
- None found for categories. Category routes depend on `require_core` (`category.py:34`).

---

## 6. Risk Indicators

- Speculative: a hosted schema change must also be reflected in `HOSTED_MIGRATION` (`conftest.py:102`) and `test_hosted_migrations_follow_the_platform_head` (`test_schema_parity.py:112`), which pins exactly two revisions.
- `test_hosted_migrations_downgrade_and_upgrade_again` (`test_schema_parity.py:150`) unpacks three migrations from a two-item tuple — already inconsistent today; only runs with a PG URL.
- `validate_category_ids` de-duplicates instead of refusing duplicates (AC3) and raises non-hosted exception types; reusing it as-is keeps that behaviour (see Existing Implementations A).
- Cross-engine access: hosted async session vs sync category engine; category deletion check vs concurrent hosted publish has no lock in today's `delete_category` (docstring claims locking).
- Publish signature change ripples to 8 test call sites and the OpenAPI baseline.
- Adding a value to `cursor_scope` invalidates outstanding page cursors.
- Hosted service tests skip without `HOSTED_TEST_DATABASE_URL`; new behaviour unverified locally without PG.
- Doc phrase quoted by the ticket is absent verbatim in `architecture.md`.
- Legacy published hosted apps without categories (AC4/AC11) — every currently published row (`is_global=true`) has none after any column addition.

---

## 7. Summary for Complexity Assessment

The change spans API (hosted router + request models), service (`catalog.py` create/patch/list, `publication.py` publish, `projections.py`), model/migration (`HostedApplications` + hosted Alembic chain bound by a strict schema-parity test), and the platform category service (`delete_category`, possibly `get_category_stats`). Roughly 8–12 source files plus `docs/hosted-applications/architecture.md` and the OpenAPI baseline.

Novelty is low: assistants and workflows already store category ids as a JSONB array with a GIN index, validate existence through the shared `category_service`/`CategoryRepository`, require 1–3 on publish with duplicate refusal (workflow request model), keep categories on unpublish, filter any-of via JSONB `@>`, and block category deletion via a `(label, model)` loop. Hosted-specific friction: async hosted sessions vs sync category helpers, `HostedApplicationError` vocabulary, strict extra-forbid request models, digest-based idempotency (publish payload currently `{}`), and the signed cursor scope.

Test posture is strong for hosted apps (publication, catalog, parity, query plans) but those run only against a real PostgreSQL (`HOSTED_TEST_DATABASE_URL`) and the fixture does not create `categories`. Key risks: parity/migration fixture coupling, dedupe-vs-refuse mismatch in the shared validator, ripple of the publish signature to 8 test call sites, and legacy published apps without categories.

---

## 8. External References

- `docs/superpowers/tasks/2026-11-18-EPMCDME-17252/requirements.local.md` — resolved. Facts: AC1 create/edit 0–3 existing distinct categories while unpublished, reply lists ids and names; AC2 publish with 1–3; AC3 >3 / repeated / unknown / publish with none refused with a message naming the problem, app unchanged; AC4 edit leaving a published app (incl. legacy) with no category refused ("a published app needs at least one category"), stays published; AC5 unpublish keeps categories; AC6 publish retry with same key and different categories → conflict; AC7 any-of filter on mine/project/marketplace/shared, combined with other filters, uncategorized apps excluded; AC8 cursor refused when the category selection changes, no repeats/skips; AC9 unknown category in filter → invalid filter; AC10 deletion refused as in use while a non-deleted hosted app carries it, deleted apps never block, refusal never names a purged app; AC11 unfiltered list still returns published uncategorized apps. Out of scope: UI, SDK/CodeMie Code, usage counts on admin page, bulk backfill. D2: differing categories on retry conflict. Constraints: land before/with categories-ui; update architecture.md; keep schema-parity passing.

## Facts Added by Review

Researched for the names the spec relies on, after the analysis above was written. Read like the facts layer; where a line here contradicts one above, this one was read later.

- `DatabaseCategoryService.delete_category` — `src/codemie/service/assistant/category_service.py:238-287`. It is synchronous. It opens `Session(Category.get_engine())` and loads the `Category` by id (404 `"Category not found"` if missing). Then it loops over `(("assistants", Assistant), ("workflows", WorkflowConfig))` (line 270). For each model it builds `cast(model.categories, JSONB).contains([category_id])` and runs `select(func.count()).where(used)`. When the count is above 0 it reads `select(model.name)...order_by(model.name).limit(_MAX_NAMES)` (`_MAX_NAMES = 10`, line 37) and appends `"<count> assigned <label> (<names>)"`. Any usage raises 409 `"Cannot delete category"` with details `"Cannot delete category with " + " and ".join(usages)`. If there is no usage it runs `session.delete(category)` and `session.commit()`. It has no lifecycle filter, because the assistant and workflow tables have no lifecycle-state column of their own. The docstring says the rows are locked ("row-level locking"), but the code issues no `with_for_update`. `Assistant` and `WorkflowConfig` are imported inside the function to avoid a circular import.
- Its one caller is `delete_assistant_category` at `src/codemie/rest_api/routers/category.py:216`, route `DELETE /v1/assistants/categories/{id}`. It runs `authenticate` and `admin_access_only` and returns 204. Its docstring mentions only assistants.
- The singleton is `category_service = DatabaseCategoryService()` (`category_service.py:327`).
- The sibling `get_category_stats` (`category_service.py:289-320`) counts marketplace and project assistants plus workflows with the same JSONB `contains` predicate on `Assistant.get_engine()`. Callers: `get_assistant_category`, `create_assistant_category` and `update_assistant_category` in `routers/category.py`. NG3 (usage counts) leaves this function out of scope.
- `Assistant.categories`: `list[str] = SQLField(default_factory=list, sa_column=Column(JSONB))` (`src/codemie/rest_api/models/assistant.py:793`).
- `WorkflowConfig.categories`: the same JSONB list (`src/codemie/core/workflow_models/workflow_config.py:122`).
- `MCPConfig.categories`: a JSONB list too (`src/codemie/rest_api/models/mcp_config.py:222`). `delete_category` does not check it, and no `category_service.validate_*` call was found on the MCP path. Whether it holds platform category ids is unverified.
- Validators that other entities use: `validate_category_ids(ids, required=False)` (lines 75-106; raises `ValidationException` when `required` is set, otherwise `ValueError`, and returns the ids de-duplicated in order), `filter_valid_category_ids` and `enrich_categories`. Callers: the assistant management, sub-assistant and catalog routers, `rest_api/routers/workflow.py:603,778`, `assistant_generator_service.py` and `models/assistant.py:925,1063`.
- `HostedApplications(SQLModel, ability.Owned, table=True)`, `__tablename__ = 'hosted_applications'` (`src/codemie/rest_api/models/hosted_application.py:66-148`). It does **not** extend `BaseModelWithSQLSupport`, so it has no `get_engine()` or CRUD helpers. Columns: `id` UUID PK, `project`, `created_by` (JSONB `CreatedByUser`), `shared`, `is_global`, `name` varchar(120) NULL, `description` varchar(2000) NULL, `search_text`, `artifact_kind`, `selected_version_id`, `active_version_id`, `security_state`, `lifecycle_state` (`active|purging|purged`), `revision`, `access_revision`, `created_at`, `updated_at`, `deleted_at`, `purged_at`. It has no `categories` column. Check constraints that apply here:
- DDL: raw SQL `CREATE TABLE hosted_applications (...)` in `src/external/alembic/versions/e3b7c1a95d42_add_hosted_applications.py:40`. The module docstring (line 21) says "Every table lives in the core schema next to the platform tables." The next revision is `f8d24a6c0b17_platform_sharing_grants_and_links.py`.
- Hosted engine: `hosted_engine()` (`src/codemie/service/hosted_applications/runtime.py:63-84`) is an **async** engine with its own capped pool (`DB_POOL_SIZE = 10`, `max_overflow=0`, `pool_timeout=2.0`) on `PostgresClient._get_connection_string(async_mode=True)` and `search_path = DEFAULT_DB_SCHEMA,public`. All hosted services (catalog, publication, launch, worker, maintenance, retention and others) reach it through `sessions()` → `HostedApplicationRepository.transaction`.
- Platform sync engine: `PostgresClient.get_engine()` (`src/codemie/clients/postgres.py:144-161`) uses the same database URL with `-c search_path={DEFAULT_DB_SCHEMA},public`. `Category.get_engine()` resolves to it through `BaseModelWithSQLSupport.get_engine` (`rest_api/models/base.py:388`). Both engines therefore point at the same database and schema, and the sync `Category` session can name `hosted_applications`.
- A sync read of `hosted_applications` already exists: `grant_descriptor.resolve(session, ...)` (`src/codemie/service/hosted_applications/grant_descriptor.py:41-65`, through `HostedApplications.__table__`). Its caller, `PermissionDeletionService._delete_named_grant`, opens `Session(Permission.get_engine())` (`src/codemie/service/permission/permission_deletion_service.py:48-49`).
- "Deleting a category is refused while an assistant or workflow uses it; hosted apps are not checked." Holds (`category_service.py:270`).
- Constraint "Hosted schema changes keep the schema-parity check between models and migrations passing." Holds. The check is `tests/codemie/service/hosted_applications/test_schema_parity.py::test_models_match_the_migrated_schema_exactly`, which compares columns, constraints and indexes. `test_hosted_migrations_follow_the_platform_head` pins `(e3b7c1a95d42 → 0ba28b31fa53)` and `(f8d24a6c0b17 → e3b7c1a95d42)`. `HOSTED_MIGRATION` in `tests/codemie/service/hosted_applications/conftest.py:102` lists exactly those two files.
- `delete_category`: `routers/category.py:235` (only caller). Tests are in `tests/codemie/service/assistant/test_category_service.py:306-396`, which patch `Session` and feed `mock_session.exec.side_effect` an **ordered list** per call (category, count, names, ...). Line 396 asserts that `call_args_list[3]` contains LIMIT. All four depend on the exact number and order of `exec` calls.
