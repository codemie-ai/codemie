# Technical Research

**Task**: customer-config dynamic-config feature-flags lifespan (EPMCDME-14705)
**Generated**: 2026-09-03
**Research path**: filesystem (codegraph MCP not configured in this session)

---

## 1. Original Context

Ticket EPMCDME-14705 — "Dynamic configuration: single component resolver shared by the backend and /v1/config (pilot on features:webSearch)".

### Context
Dynamic customer configuration (EPMCDME-13983 chat disclaimer, EPMCDME-14649 banner) overrides only what the frontend is served. Two independent read paths exist over the same YAML-backed singleton:

- The `customer_config` singleton (`src/codemie/configs/customer_config.py`) parses customer-config YAML once at import time. `is_component_enabled()` / `is_feature_enabled()` / `get_feature_setting()` scan `self.components` and never consult the `dynamic_config` table.
- `customer_config_service.resolve_components()` merges override rows from the `dynamic_config` table on the way out to `GET /v1/config`, i.e. for the frontend only.

Consequence: for any component the backend also reads, an admin flipping the switch changes the UI while the backend keeps running on YAML. That split brain is worse than not exposing the switch at all, and it blocks 13 operationally valuable components (webSearch, dynamicCodeInterpreter, requestHedging, tool_permissions, mcpCustomServersDisabled, interactiveElements, subWorkflow, costCenters, projectChargeback, userEnrichmentEnabled, teamsBotIntegration, personalLiteLLMIntegrations, skills) from becoming dynamic.

The resolver ended up at the API layer because EPMCDME-13983 had exactly one consumer — the frontend. This story moves it to where both consumers can share it.

The whole point is to remove restarts, not to add them: today a flag the backend reads can only be changed by redeploying, because the YAML is parsed once at import. Nothing in this story restarts, reloads or recycles anything.

### An override is a layer over YAML, not a replacement config
A write is a snapshot of the whole declared set. The admin form is prefilled with the resolved value — `list_settings` returns the override laid over YAML (`customer_config_service.py:411`) — and `validate_and_sanitize` rejects a payload missing any declared field (`customer_config_service.py:221`), so saving stores every declared field, including the ones the admin did not touch. From that moment those fields are frozen at the values they had when saved: they no longer follow YAML across deployments. `reset_setting`, which deletes the row, is the only way to put a component back under YAML control.

What a stored row still does not cover:
- Fields the declaration does not expose — a component's `name`, `description`, anything absent from the admin form. They never reach the database and always resolve from YAML. `apply_override` merges over the YAML settings rather than replacing them (`customer_config_service.py:143`, `model_dump() | override`).
- Declared fields added after the row was written. Grow a declaration and every earlier row is missing the new field; `_parse_override` guards this on the read side with `if name in value` (`customer_config_service.py:129`) and the field resolves from YAML — until the next save freezes it along with the rest.

So resolution is per field, and the presence of a row says nothing about the presence of a given field in it.

### Scope
**Part 1 — one resolver under both read paths**
- Introduce a single owner of override state and of the precedence rule, sitting below both `configs/customer_config.py` and `service/customer_config_service.py`. The precedence rule must exist in exactly one implementation, covered by one set of tests.
- Rationale for placing it below both, rather than having the service push a snapshot into the config: `configs/customer_config.py` imports nothing from `service/`, the service imports from configs. A shared lower-level module avoids the import cycle without duplicating the merge rule.
- `customer_config` reads through it synchronously; `customer_config_service` populates it and resolves `/v1/config` through it.
- Precedence applies per field, not per component: runtime-computed component, then the override if it carries that field, then YAML. `is_component_enabled` resolves the `enabled` field this way; `get_feature_setting` resolves the requested setting the same way.
- `apply_override` already implements exactly this merge for `/v1/config`; the synchronous path must go through the same code rather than reimplement it.
- No changes at the call sites. There are ~21 of them, 19 inside synchronous functions, so an async signature is not an option; synchronous resolution is a hard constraint.

**Part 2 — something that actually loads the overrides**
An admin write already invalidates the cache: `save_setting` and `reset_setting` call `override_cache.invalidate()` today. `invalidate()` only marks the snapshot stale (`_overrides = None`); it loads nothing. The load happens inside `await get()`, whose sole caller is `resolve_components()`, i.e. the `GET /v1/config` path. Synchronous backend code cannot await anything, so the snapshot would stay empty forever in a process that serves no `GET /v1/config`.

Two additions, both inside the existing application lifespan:
- A periodic background refresh, alongside the existing `asyncio.create_task` calls in `rest_api/main.py`, driven by the existing `CUSTOMER_CONFIG_CACHE_TTL_SECONDS` (currently 60s). The loop must refresh first and sleep afterwards.
- A one-off warm-up of the snapshot before the application starts serving traffic, i.e. `await`ed before the lifespan `yield`, mirroring the existing `await jwks_warmup()` at `rest_api/main.py:722`.
- Warm-up must not fail start-up. `_load_overrides()` already catches its errors and falls back to YAML; that behaviour is preserved.
- Per-pod invalidation on write stays as it is. Other pods converge within the TTL.

**Part 3 — pilot migration**
- Declare `features:webSearch` in the dynamic-configuration registry (a single `enabled` switch field) and migrate it to the runtime mechanism.
- Chosen because it has exactly one backend call site (`service/tools/toolkit_service.py:260`, `enable_web_search`), it is not read during application start-up, and its effect is directly observable in a chat.

### Behaviour that must be preserved
- Opposite defaults for a missing key must survive verbatim: `is_feature_enabled` returns `True` for an unknown key, `is_component_enabled` returns `False`.
- Runtime-computed components stay excluded from overriding. `get_runtime_components()` is never overridable; `resolve_components()` filters those ids out before applying overrides.
- Fields outside a declaration, and declared fields absent from a row written before the declaration grew, keep coming from YAML.
- The backend passes the key WITHOUT the `features:` prefix — `is_feature_enabled("webSearch")` maps to component id `features:webSearch`. Grepping for the full component id misses these call sites.

### Acceptance criteria
1. The precedence rule — runtime-computed, then dynamic override, then YAML — is implemented once and used by both backend feature resolution and the `GET /v1/config` path.
2. Resolution is per field: fields a declaration does not expose, and declared fields absent from a stored row, resolve from YAML.
3. When a stored override carries a field, backend resolution returns the overridden value for that field instead of the YAML value.
4. When no override exists, backend feature resolution returns the YAML value, with the existing defaults for an unknown key unchanged.
5. Resetting a component removes its row and returns backend resolution to YAML.
6. Runtime-computed components are never overridable.
7. Feature resolution stays synchronous and every existing call site keeps working without modification.
8. Saving a setting takes effect on a running service: no restart, reload or redeploy is required, and the change reaches backend behaviour within the configured cache staleness window — including in a process that has served no `GET /v1/config` request.
9. A pod never serves a request before its override snapshot has been loaded at least once; a snapshot that cannot be loaded at start-up leaves the service running on YAML rather than failing to start.
10. When the override source is unreachable after start-up, the last known good snapshot is served; if there is none, resolution falls back to YAML rather than failing.
11. The pilot feature is declared in dynamic configuration and, when switched off at runtime, both the frontend surface and the backend capability are disabled consistently.
12. Unit tests cover override precedence, a stored row missing a declared field, both missing-key defaults, runtime-computed precedence, reset returning to YAML, start-up warm-up failure, and the unreachable-source fallback, against the shared resolver.

---

## 2. Codebase Findings

### Existing Implementations

**The YAML read path (synchronous, import-time singleton)**
- `src/codemie/configs/customer_config.py` — the whole sync surface. `CustomerConfig(BaseModel)` at `:75`; `model_post_init` → `_load_config()` parses the YAML; module-level singleton `customer_config = CustomerConfig()` at `:297`, i.e. at import time. No reload path exists today.
  - `:23` `CONFIG_IDS` — the 6 runtime-computed, never-overridable component ids.
  - `:33` `ComponentSetting` — `enabled: bool` (required), `availableForExternal=True`, `name/url/created_by/icon_url`, `model_config = ConfigDict(extra="allow")`. The `extra="allow"` is load-bearing: structured settings (`text`, `value`, `recentReleaseCount`, `catalog`, `min_tool_call_policy`) ride along as extras.
  - `:63` `Component` — `{id, settings}`.
  - `:131` `_get_runtime_config()` / `:190` `get_runtime_components()`.
  - `:194` `get_enabled_components()` — the pure-YAML twin of `resolve_components()`.
  - `:249` `is_component_enabled()`, `:267` `is_feature_enabled()`, `:284` `get_feature_setting()`.
  - `:79` `config_path = Path(f"{config.CUSTOMER_CONFIG_DIR}/customer-config.yaml")`.

**The DB-merged read path (async, service layer)**
- `src/codemie/service/customer_config_service.py`
  - `:61` `class OverrideCache` — `_overrides: Overrides | None`, `_expires_at` on `time.monotonic()`; `:74 invalidate()` (drop), `:78 expire_now()` ("mark stale but keep the snapshot as degradation fallback" — **currently zero callers**, evidently intended for exactly this refresher), `:82 async get()`.
  - `:100` `override_cache = OverrideCache(ttl_seconds=config.CUSTOMER_CONFIG_CACHE_TTL_SECONDS)` — module-level singleton; the TTL is snapshotted at import.
  - `:103` `async _load_overrides()` — one `DynamicConfigService.alist_by_key_prefix(KEY_PREFIX)` query, drops undeclared keys.
  - `:120` `_parse_override`, `:134` `apply_override`, `:147` `resolve_components`, `:161` `_declared_components`, `:176` `_switchless_defaults`, `:194` `validate_and_sanitize`, `:289` `save_setting`, `:310` `reset_setting`, `:373` `list_settings` (deliberately bypasses the cache), `:404` `_resolved_value`.
- `src/codemie/service/customer_config_declarations.py` — the declaration registry. `SettingDeclaration` / `FieldDeclaration`, `KEY_PREFIX`, `:50 build_key` (`features:webSearch` → `CUSTOMER_CONFIG__FEATURES__WEB_SEARCH`), `:103 empty_value()`, `:145 DECLARATIONS = (CHAT_DISCLAIMER, RELEASE_NOTES_RECENT_COUNT)`. **Only two components are declared overridable today.**
- `src/codemie/service/dynamic_config_service.py` — DB access, sync and async classmethod twins.
- `src/codemie/rest_api/models/dynamic_config.py:35` — `DynamicConfig` SQLModel table (`key` unique/indexed ≤255, `value` ≤10000, `value_type`, `description`, `updated_by`).
- `src/external/alembic/versions/93e2d3c3b1c0_add_dynamic_config_table_for_runtime_.py` — the migration (down_revision `a4d9b6c2e7f1`).
- `src/codemie/rest_api/routers/customer_config.py` — `GET /v1/config`, `/v1/config/declarations`, PUT/DELETE.
- `config/customer/customer-config.yaml` — the YAML. `features:webSearch` at `:125`:
  ```yaml
    - id: "features:webSearch"
      settings:
        enabled: true
        name: "Web Search"
        description: "Enable web search capabilities including Google Search, Tavily Search, and Web Scraper"
  ```
  All 13 components named in the ticket exist. Note two are **not** under the `features:` prefix: `mcpCustomServersDisabled` (`:228`) and `skills` (`:45`) — they are read via `is_component_enabled`, not `is_feature_enabled`.

**The pilot call site**
- `src/codemie/service/tools/toolkit_service.py:260` — inside sync `_augment_toolkits_with_feature_flags`, combines `request.enable_web_search is True` with `customer_config.is_feature_enabled("webSearch")`. Sibling flags read in the same function: `dynamicCodeInterpreter` (`:283`), `interactiveElements` (`:620`).

### Architecture and Layers Affected

- **configs/** — `customer_config.py` gains a read-through to the shared resolver. This is the layer with the hard "no imports from `service/`" constraint. Confirmed: it imports only stdlib, pydantic and `codemie.configs.config`.
- **service/** — `customer_config_service.py` becomes a populator of the shared resolver rather than the owner of the merge rule; `customer_config_declarations.py` is the registry that must become importable from `configs/`.
- **rest_api/** — `main.py` lifespan gains the warm-up and the refresh loop; `routers/customer_config.py` unchanged; `models/dynamic_config.py` is the table.
- **New shared module** — the ticket asks for a module below both. Candidate homes: `src/codemie/configs/` or `src/codemie/core/`.
- **Consumers (unchanged by design)** — `service/`, `rest_api/{routers,handlers}`, `workflows/`, `codemie_tools/`, `external/deployment_scripts/`.

### Integration Points

**Call sites — the ticket's "~21, 19 sync" claim is verified exactly.** 21 flag/setting reads: 19 synchronous, 2 async.

`is_feature_enabled` (18 — 16 sync, 2 async):
| Site | Sync/async | Function | Key |
|---|---|---|---|
| `rest_api/handlers/assistant_handlers.py:1266` | sync | `get_request_handler` | requestHedging |
| `rest_api/main.py:394` | sync | `_initialize_optional_features` | subWorkflow |
| `rest_api/routers/analytics.py:2125` | **async** | `_run_enriched_user_insight` | userEnrichmentEnabled |
| `rest_api/routers/user_settings.py:62` | sync | `_validate_litellm_user_setting_access` | personalLiteLLMIntegrations |
| `rest_api/routers/workflow.py:148` | sync | `get_sub_workflow_candidates` | subWorkflow |
| `service/assistant_service.py:458` | sync | `_prepare_system_prompt` | interactiveElements |
| `service/cost_center_service.py:63` | sync | `ensure_feature_enabled` | costCenters |
| `service/project/project_service.py:200` | sync | `update_project` | projectChargeback |
| `service/project/project_service.py:316` | sync | `_validate_chargeback_attribution` | costCenters |
| `service/settings/settings_request_validator.py:318` | sync | `validate_ms_teams_request` | teamsBotIntegration |
| `service/tool_permissions_service.py:51` | sync | `get_effective_permissions` | tool_permissions |
| `service/tools/toolkit_service.py:260` | sync | `_augment_toolkits_with_feature_flags` | **webSearch (pilot)** |
| `service/tools/toolkit_service.py:283` | sync | same | dynamicCodeInterpreter |
| `service/tools/toolkit_service.py:620` | sync | `_append_request_user_input_tool_if_enabled` | interactiveElements |
| `service/user/billing_user_resolver.py:138` | **async** | `get_billing_user` | teamsBotIntegration |
| `workflows/nodes/sub_workflow_node.py:65` | sync | `execute` | subWorkflow |
| `workflows/validation/resources.py:701` | sync | `_validate_sub_workflow_availability` | subWorkflow |
| `workflows/workflow.py:616` | sync | `initialize_node` | subWorkflow |

`is_component_enabled` (2, sync): `service/mcp/access_control.py:102` (`validate_on_save`), `:139` (`filter_for_runtime`) — both `mcpCustomServersDisabled`.
`get_feature_setting` (1, sync): `service/tool_permissions_service.py:67`.

Non-flag couplings that still matter:
- `src/codemie_tools/base/models.py:22` — `get_tool_default = customer_config.get_tool_default` binds a **bound method at import time**. A resolver design that swaps the singleton would not be picked up here.
- `src/external/deployment_scripts/preconfigured_assistants.py:41,44,81,82,84`.
- `src/codemie/configs/__init__.py:16,21` re-exports the singleton; `rest_api/main.py:30` aliases it as `_customer_config`.
- `src/codemie/rest_api/models/customer_config.py:23` imports `FieldType`/`Markup` **from `service/customer_config_declarations`** — relocating that module must update this import.

**Import direction (confirmed)**: `configs/` → nothing in `service/`, `repository/` or `rest_api/`. One tolerated precedent for a downward exception: `src/codemie/configs/authorized_apps_config.py:24` imports `codemie.rest_api.models.permission` — relevant because `DynamicConfig`/`ConfigValueType` live under `rest_api/models/`.

**DB access shape**: `DynamicConfigService` has sync (`get_by_key:250`, `get:266`, `list_all:518`, `get_typed_value_safe:570`, `get_bool_value_safe:594`) and async (`aget_by_key:338`, `aset:378`, `alist_by_key_prefix:467`, `adelete:488`) twins. **There is no sync `list_by_key_prefix`.** A sync resolver therefore either needs one added (mirroring the async one with `Session(...)`, following the existing `_`-prefixed shared-helper convention) or — the design the ticket clearly intends — must be fed exclusively by the async warm-up/refresh and read purely from memory with zero I/O.

### Patterns and Conventions

- Module-level singletons with `invalidate()` / `cache_clear()`; monotonic-clock TTL; a degradation ladder that never raises: DB error → last known good snapshot → empty override map → YAML (`OverrideCache.get`, `:82-97`).
- Writer invalidates its own pod immediately; other pods converge within TTL (docstring `customer_config_service.py:61-66`).
- Only declared field names cross the DB boundary (`_parse_override` filter + `_reject_undeclared_fields`).
- Merge is over `model_dump(exclude_none=True)`, so undeclared YAML fields keep following deployments.
- Overrides are applied **before** the `enabled` filter, so an override can both enable and disable.
- Lifespan house style (`rest_api/main.py:705-821`): private `_setup_*_scheduler(app)` / `_schedule_*(app, tasks)` helpers, `tasks: list[asyncio.Task]` appended and cancelled in `_shutdown_services` (`:691-693`). Awaited warm-up precedent: `await jwks_warmup()` at `:723-726`. Background-task precedents: `_schedule_startup_recovery:323-337`, `_schedule_budget_reconciliation:339-358`. TTL-derived interval precedent: `_setup_litellm_cache_cleanup_scheduler:164-205`.
- Sync→async bridging exists (`core/event_loop.py` + `asyncio.run_coroutine_threadsafe`) but is explicitly documented as deadlock-prone on the main loop (`enterprise/litellm/project_member_runtime_sync.py:390`) — reinforcing the pre-warmed in-memory snapshot as the right approach.
- Code style (`.ai-run/guides/standards/code-quality.md`): `X | None` not `Optional`, `list[str]`/`dict[str,str]`, `from __future__ import annotations` in new modules, Apache header on every new file (`make license-check`). The 13983 review failed its first round on exactly the `Optional` rule.
- Logging: f-strings with `{var=}`; `logger.warning` on degradation, never an exception to the caller.

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/` exists and is substantive. Most relevant:
- `architecture/layered-architecture.md` — router → service → repository; "keep feature startup side effects explicit", "gate optional routers where the app is assembled". The refresh loop and warm-up belong in the `main.py` lifespan, not hidden behind a module import.
- `development/configuration-patterns.md` — never read env directly, go through `src/codemie/configs/`; documents the precedent for a runtime-togglable flag (`CHAT_CONTEXTUAL_NAMING_ENABLED` via `DynamicConfigService.get_bool_value_safe`).
- `development/performance-patterns.md` — "keep I/O paths async where the surrounding layer is async", "avoid blocking calls in request paths", prefer existing background-task abstractions over ad-hoc loops. This is the guide that justifies a cached sync snapshot plus an async refresher instead of sync DB reads at call sites.
- `testing/testing-patterns.md` — mirror `src/` under `tests/codemie/...`; the **"Seam Tests for Policy Helpers"** rule applies almost verbatim: a shared resolver helper needs a test at each call-site branch, not only an isolated unit test. "Red flag: deleting a call-site guard leaves tests green."
- `standards/code-quality.md`, `standards/git-workflow.md` (branch `EPMCDME-14705_...`, commit `EPMCDME-14705: Description`), `quality-gates.md` (`make ruff` → `build` → `license-check` → `gitleaks` → `test` → `test-harness`).

Repo skills (`.claude/skills/`): `codemie-jira-assistant`, `taf-regression-advisor`, `sonarqube-mcp-analyzer`, `codemie-onboarding`. No `.claude/agents/`.

### Architectural Decisions

No ADR directory. Decisions live in prior run artifacts, chiefly `docs/superpowers/runs/20260820-1318-EPMCDME-13983-dynamic-customer-config/design.md` and `requirements.md`:

- **Layering (the tension in this ticket)**: "`DynamicConfigService` gains only generic helpers… It never learns what a customer-config key means." The declaration registry, resolver, cache and degradation were deliberately placed in the `customer-config` **service** layer. EPMCDME-14705 now asks for that resolver to sit *below* `configs/`. This is a conscious revision of 13983's layering, not an oversight, but it must be executed without dragging DB imports into `configs/`.
- **D4 — per-field merge of declared fields**, rationale stated specifically for the 19 `features:*` components: whole-object storage "would pin `name` and `description` to whatever the YAML held at write time".
- **D1** — no schema change; the override is a JSON-serialised settings object stored as `value_type=STRING`.
- **Cache** — process-local `{component_id: settings}` + expiry stamp, TTL from `CUSTOMER_CONFIG_CACHE_TTL_SECONDS` (default 60); the writing pod calls `invalidate()` after a successful write.
- **Degradation ladder** — "DB error → last known good snapshot → empty override map. The public endpoint never 500s because of `dynamic_config`."
- **CR-003** — the admin read path deliberately bypasses the cache so an admin sees their own write.
- **CR-004** — a declared component absent from YAML still resolves via a synthesised placeholder (`_declared_components` / `_switchless_defaults`). A shared resolver must preserve this.
- **FR-2** — "No seeding on startup: a row exists only when an admin explicitly overrides a value." The warm-up must read, never write.
- **FR-5** — "The cache lives in the customer-config layer; consumers do not cache."
- **FR-1** — "Making a new key dynamic means adding a declaration only: no API, schema or frontend change." This is what makes the webSearch pilot cheap.

**Explicitly deferred by 13983 to a follow-up — i.e. this ticket:**
- "Cross-pod pub/sub invalidation (TTL is the first-iteration mechanism)." The background refresh is the second-iteration answer to the same staleness problem.
- "Bulk migration of the remaining config keys." Piloting `features:webSearch` is step one.
- FR-5b was accepted as `partial` in both review rounds; the backend-side equivalent of that gap is precisely the never-refreshed sync `customer_config` singleton.

Two decision documents referenced by `design.md` are **outside the repo** and were not read: `~/Projects/codemie/customer-config-dynamic-decisions.md` and `customer-config-dynamic-open-questions.md`.

### Derived Conventions

- `expire_now()` at `customer_config_service.py:78` exists with zero callers and a docstring describing exactly the refresher semantics — treat it as the intended hook rather than adding a new one.
- New modules in this feature already use `from __future__ import annotations`, `X | None` and the Apache header.
- No `TODO`/`FIXME`/`HACK` markers anywhere in the dynamic-config source files; constraints are carried in prose docstrings (`customer_config_service.py:16-20`, `:61-66`, `:162-166`, `:184`).

---

## 4. Testing Landscape

### Existing Coverage

Test tree: `tests/codemie/**` mirrors `src/codemie/**` (main tree), plus legacy `tests/unit/**`, `tests/codemie_tools/**`, `tests/enterprise/**`. 1058 test files.

- `tests/codemie/configs/test_customer_config.py` (566 lines) — the sync `CustomerConfig`. `unittest.TestCase` style. Model validation, YAML load success/invalid, `get_enabled_components`, `is_feature_enabled` (webSearch enabled / dynamicCodeInterpreter disabled / unknown), runtime features incl. runtime-overrides-YAML precedence, `get_feature_setting` for tool_permissions.
- `tests/codemie/service/test_customer_config_service.py` (309 lines) — the async `resolve_components`. Already close to AC12: empty DB, override merge before the enabled filter, override disabling a YAML-enabled component, undeclared YAML fields following deployments, override for an undeclared component ignored, unparseable override → YAML, DB failure → last known good, DB failure without snapshot → YAML, cache hit/invalidate/TTL expiry, runtime components appended unchanged, declared component missing from YAML.
- `tests/codemie/service/test_customer_config_declarations.py` (110) — `build_key`, `by_key`/`by_component_id`, key-pattern compliance.
- `tests/codemie/service/test_customer_config_validation.py` (231) — `validate_and_sanitize`.
- `tests/codemie/service/test_customer_config_audit.py` (152) — `save_setting`/`reset_setting`, cache invalidation on save, reset deletes + audits.
- `tests/codemie/rest_api/routers/test_customer_config_router.py` (362), `.../security/test_customer_config_write_guard.py`.
- `tests/codemie/service/test_dynamic_config_service.py` (1062) — sync CRUD only; the async methods are always mocked, never exercised.
- `tests/codemie/rest_api/test_startup_integration.py` (452) — the lifespan tests.
- `tests/codemie/service/tools/test_toolkit_service.py` (~1800) — `enable_web_search` is only ever set to `None`; the feature-flag branch is never exercised.

### Testing Framework and Patterns

- pytest `^8.3.1`, pytest-asyncio `^0.23.7`, pytest-mock, pytest-cov, pytest-env, pytest-httpx. No anyio.
- `asyncio_mode` is **not set anywhere** → pytest-asyncio **strict**; every async test needs an explicit `@pytest.mark.asyncio`.
- `pytest.ini`: `testpaths=tests`, `pythonpath=src`, `--import-mode=importlib`, env `ENV=local`, `PG_URL` pointed at a deliberately unroutable port. `.coveragerc` has an `omit` list and **no `fail_under`**.
- `tests/conftest.py`: stubs missing native packages into `sys.modules` before imports; `load_dotenv(tests/.env.test, override=True)` at module level so `Config()` sees test env at import; session-scoped autouse patch of `PostgresClient.get_engine`. **No real DB anywhere.**
- YAML stubbing — two idioms coexist; prefer the newer `patch.object(CustomerConfig, "_read_config_file", ...)` helper `_make_config()` at `test_customer_config.py:516` over `patch("...Path.read_text", ...)`.
- Import-time singleton patching — nobody reloads modules; they patch the re-exported reference in the consuming module: `patch.object(customer_config_service, "customer_config")`, `patch("codemie.rest_api.main._customer_config")`. For the pilot you would patch `codemie.service.tools.toolkit_service.customer_config`.
- Cache hygiene — autouse `reset_cache` fixture calling `override_cache.invalidate()` before and after each test (`test_customer_config_service.py:36`), essential because `override_cache` is a module-level singleton. Tests patch `customer_config_service.override_cache` **directly** (`test_customer_config_audit.py:31,33,107`) — renaming or moving it breaks them.
- Lifespan testing — `test_startup_integration.py` uses a ~15-deep nested `with patch(...)` pyramid over every `main.*` startup helper, then `async with lifespan(mock_app)`. But the house norm is the **extracted-helper pattern**: `_initialize_optional_features()`, `_schedule_startup_recovery(tasks)` etc. are tested as standalone functions. That is the model for a warm-up helper.
- Background tasks — `_schedule_startup_recovery` is tested by patching `main.asyncio.create_task` and asserting on captured args; the coroutine never runs. `grep "while True" tests` → **zero hits**. There is no precedent anywhere for testing a periodic loop; you will need to establish one (extract the loop body into a `_refresh_once()` coroutine, test it directly, and separately assert the loop is scheduled via a patched `create_task`).
- Warm-up failure precedent — `tests/codemie/rest_api/security/jwks/test_no_enterprise.py:58` `test_warmup_logs_error_and_does_not_crash`.

### Coverage Gaps

Against AC12:
- **Stored row missing a declared field** — uncovered on the read path. `_parse_override`'s `if name in value` guard has no test.
- **`is_component_enabled` unknown → False** — no direct test; only implied through `is_feature_enabled`.
- **`is_feature_enabled` unknown → True** — not merely uncovered but **actively contradicted** (see Risk 1).
- **Runtime-computed precedence over a stored override** — nothing asserts that an override on e.g. `features:enterpriseEdition` is ignored.
- **Reset returning to YAML** — uncovered end-to-end. `test_reset_deletes_the_row_and_audits_it` asserts the delete and audit only, never a subsequent resolution.
- **Start-up warm-up failure** — uncovered (no warm-up exists).
- **Unreachable-source fallback** — covered for the async path only; no sync-resolver equivalent.
- **Periodic background refresh** — entirely uncovered, no repo precedent.
- **`enable_web_search`** — neither the True×enabled nor the True×disabled branch is tested.

Two existing assertions will need changing:
- `tests/codemie/configs/test_customer_config.py:186` — asserts `is_feature_enabled("unknownFeature")` is False.
- `tests/codemie/service/test_customer_config_declarations.py:71` `test_undeclared_component_is_not_resolvable` asserts `by_component_id("features:webSearch") is None` — this breaks the moment webSearch is declared. (The parametrized `build_key` tests at lines 20/33 already use `features:webSearch` as a sample, so only the negative test needs replacing.)

---

## 5. Configuration and Environment

### Environment Variables

- `CUSTOMER_CONFIG_CACHE_TTL_SECONDS` — `src/codemie/configs/config.py:793`, `int = 60`. Sole consumer is `customer_config_service.py:100`, where it is snapshotted into `OverrideCache.__init__` **at import**. A refresh loop should read `override_cache.ttl_seconds` or `config.*` rather than assume 60. Oddly filed inside the "Chat Contextual Naming Configuration" block. Not present in `.env.example`, `deploy-templates/values.yaml` or any Helm template.
- `CUSTOMER_CONFIG_DIR` — `config.py:112`, default `<repo>/config/customer`. Read by `customer_config.py:79` and `managed_mcp_config.py:128`.
- No `DYNAMIC_CONFIG*` env var exists — dynamic config is DB-only, keyed by the `CUSTOMER_CONFIG__` prefix.

### Configuration Files

- `config/customer/customer-config.yaml` — the in-repo default, baked into the image at `/app/config/customer` (`Dockerfile:41`) and **overridden in k8s** by the `codemie-customer-config` ConfigMap mounted over the same path (`deploy-templates/values.yaml:596-611`, rendered at `templates/deployment.yaml:69-90`). The per-customer YAML lives in that ConfigMap, not in this repo. A ConfigMap edit needs a pod restart today.
- Settings pattern: a single `class Config(BaseSettings)` at `src/codemie/configs/config.py:44` (~1050 flat typed fields, `env_file=(.env, .env.local)`, `extra="ignore"` at `:839-845`), exported as the `config` singleton from `configs/__init__.py`. **Canonical home for any new env var: a new field on `Config`, next to line 793.**

### Feature Flags and Deployment Concerns

Three coexisting flag mechanisms, which is itself part of the problem this ticket addresses:
1. YAML `components` via the import-time sync singleton (the 21 call sites above).
2. DB-backed dynamic config (`dynamic_config` table, `CUSTOMER_CONFIG__` prefix) — only `chatDisclaimer` and `releaseNotesRecentCount` are declared.
3. Plain env-var booleans on `Config` (`SUBWORKFLOW_ENABLED`, `WORKFLOW_GENERATION_ENABLED:787`, `LEADERBOARD_ENABLED:796`, …), six of which are surfaced as pseudo-components via `CONFIG_IDS` / `_get_runtime_config()` and are explicitly never overridable.

Deployment:
- `deploy-templates/values.yaml:6` `replicaCount: 1`; HPA at `:548-556` disabled by default, `minReplicas: 1`, `maxReplicas: 2`. Default is single-pod but multi-pod is a supported live configuration, so per-pod TTL convergence is a real concern. Two further worker pools (`:307` ds-pool, `:399` proxy-pool) also import `customer_config`.
- Workloads: `templates/rollout.yaml:10` (Argo Rollout, disabled by default) and `templates/deployment.yaml:10`.
- Probes (`values.yaml:504-542`), all `GET /v1/healthcheck:8080`. `startupProbe` allows ~600s (sized for Alembic at startup), so a DB round-trip before `yield` is safe budget-wise — but it must never raise.
- Secrets: none in this domain; the customer-config ConfigMap holds no credentials.

---

## 6. Risk Indicators

1. **Requirements conflict on the missing-key default — highest-priority blocker.** The ticket's "Behaviour that must be preserved" says `is_feature_enabled` returns `True` for an unknown key. The implementation returns **`False`**: `is_feature_enabled` (`customer_config.py:267`) simply prefixes `features:` and delegates to `is_component_enabled`, whose default is `False` (`:249-265`). Only the *docstring* at `:269` claims `True`. `tests/codemie/configs/test_customer_config.py:186` actively asserts `False`. AC4 says defaults must be "unchanged", which contradicts the Behaviour section. Implementing the ticket literally would flip the default for 18 call sites — including four `subWorkflow` guards and a lifespan-time pool decision — turning unknown keys into enabled features. **This needs a product decision before implementation, not a judgement call during it.**
2. **A flag is already read inside the lifespan, before any warm-up could help.** `rest_api/main.py:394` (`_initialize_optional_features`, invoked at `:786`) calls `is_feature_enabled("subWorkflow")`. The webSearch pilot is unaffected, but AC9's "never serves a request before the snapshot is loaded" interacts with startup ordering, and the moment `subWorkflow` is migrated the warm-up must precede `:786`. Ordering must be chosen deliberately now.
3. **Layering revision versus a recorded 13983 decision.** 13983's `design.md` deliberately kept the merge rule in the service layer; 14705 moves it below `configs/`. The declaration registry (`service/customer_config_declarations.py`) must become importable from `configs/` — it is safely relocatable (stdlib + pydantic only), but `rest_api/models/customer_config.py:23` imports `FieldType`/`Markup` from it and would need updating. `codemie_tools/base/models.py:20` also imports from `codemie.configs.customer_config`, so the shared resolver must not pull DB imports in at module scope.
4. **No sync `list_by_key_prefix` on `DynamicConfigService`.** The sync path can only be fed by the async warm-up/refresh. Any fallback that tries a synchronous DB read from a call site would either need a new sync twin or would risk the documented "Future attached to a different loop" failure (`enterprise/mcp_auth/_trust_policy.py:44-53`) and the deadlock warned about at `project_member_runtime_sync.py:390`.
5. **Import-time binding defeats singleton swapping.** `codemie_tools/base/models.py:22` does `get_tool_default = customer_config.get_tool_default` at import. Any design that replaces the `customer_config` object rather than mutating shared state behind it will silently not apply there.
6. **No repo precedent for testing a periodic `while True` loop** — zero hits across 1058 test files. AC12's refresh-loop coverage requires establishing a new pattern (extract `_refresh_once()`, test it directly; assert scheduling via a patched `create_task`).
7. **Two existing tests will fail by design** and must be consciously rewritten, not silently deleted: `test_customer_config.py:186` (unknown-key default) and `test_customer_config_declarations.py:71` (`features:webSearch` asserted undeclared).
8. **TTL is frozen at import** (`override_cache = OverrideCache(ttl_seconds=config.CUSTOMER_CONFIG_CACHE_TTL_SECONDS)` at `customer_config_service.py:100`), and `CUSTOMER_CONFIG_CACHE_TTL_SECONDS` is absent from `.env.example` and all Helm templates — so the knob AC8 depends on is currently not settable in a deployed environment without adding it to `extraEnv`.
9. **`override_cache` is patched by name in existing tests** (`test_customer_config_audit.py:31,33,107`; `test_customer_config_service.py:36`). Renaming or relocating it breaks tests that are not obviously part of this feature.
10. **EPMCDME-14649 (banner) left no spec/plan/design artifact** in the repo — only `.state.json` and `gate-plan.json`. Its decisions are recoverable only from the branch diff or Jira. Notably `DECLARATIONS` contains no banner entry while the YAML still has flat `bannerMessage`/`bannerLinkLabel`/`bannerLinkRoute` components (`customer-config.yaml:261-274`), so the registry may not be the only override path. Worth verifying before assuming it is.
11. **Two async call sites** (`analytics.py:2125`, `billing_user_resolver.py:138`) currently call the sync API. Harmless, but they are the places most likely to tempt a well-meaning "just await it" refactor that AC7 forbids.
12. **`extra="allow"` on `ComponentSetting`** means non-boolean structured extras (`catalog` on `interactiveElements`, `min_tool_call_policy` on `tool_permissions`) live alongside declared fields. A per-field resolver must not flatten or drop them — this is exactly what D4 protects.
13. **Two async `DynamicConfigService` methods the warm-up depends on (`alist_by_key_prefix`) have no direct tests** — they are always mocked.
14. **Coverage has no `fail_under` threshold**, so a regression in this area would not be caught by the coverage gate alone.

---

## 7. Summary for Complexity Assessment

**Layers and change surface.** This task touches four layers in a repository where the layering rule is the whole point of the ticket: `configs/` (the import-time sync singleton, `customer_config.py`), `service/` (`customer_config_service.py` plus the relocatable `customer_config_declarations.py`), `rest_api/` (the lifespan in `main.py`, and an incidental import fix in `models/customer_config.py`), and a brand-new shared module that must sit below `configs/` without importing anything from `service/` or the database at module scope. Expected production change surface is modest in file count but high in blast radius: roughly 5–7 source files changed plus one new module, against 21 verified call sites (19 synchronous — the ticket's count is exact) spread over `service/`, `rest_api/`, `workflows/` and `codemie_tools/`, none of which may change. Test surface is larger than the source surface: at least 4 existing test files touched, 2 with assertions that will deliberately fail, plus new coverage for eight AC12 scenarios.

**Technical novelty.** Most of the mechanism already exists and is well-shaped for reuse — `apply_override`, `_parse_override`, `OverrideCache` with its degradation ladder, and an unused `expire_now()` whose docstring describes precisely the refresher this ticket adds. The lifespan has clear precedents for both additions (`await jwks_warmup()` for the warm-up; `_schedule_budget_reconciliation` for the background task; `_setup_litellm_cache_cleanup_scheduler` for deriving an interval from a TTL). The genuinely novel parts are three: making a shared resolver readable synchronously with zero I/O while it is populated asynchronously (the codebase's existing sync→async bridge is documented as deadlock-prone and should be avoided); relocating the declaration registry below `configs/` without an import cycle; and consciously revising a layering decision that EPMCDME-13983's `design.md` recorded on purpose. Each is tractable, but none is a copy-paste of an existing pattern.

**Test posture and risk drivers.** Coverage is mixed and asymmetric: the async `/v1/config` path is genuinely well tested (309 lines covering merge order, cache TTL, and both degradation rungs), while the sync path has no override awareness at all, the pilot call site's flag branch is never exercised, and there is no precedent anywhere in 1058 test files for testing a periodic loop. Three factors should dominate the complexity score. First, an unresolved requirements contradiction: the ticket asserts `is_feature_enabled` defaults to `True` for unknown keys, but the code and an existing test both say `False` — only a docstring supports the ticket, and implementing it literally would flip behaviour at 18 call sites. This should be settled with the product owner before planning, not decided in code. Second, one flag is read *inside the lifespan itself* (`main.py:394`), which constrains warm-up ordering and complicates AC9 as soon as migration moves past the pilot. Third, the pilot is deliberately well chosen — a single sync call site, not read at startup, observable in a chat — which keeps Part 3 genuinely small and contains the risk of Parts 1 and 2. Overall this reads as a medium-to-high complexity refactor whose difficulty lies in layering discipline and behavioural fidelity rather than in volume of code.
