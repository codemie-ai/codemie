# Technical Research

**Task**: katas customer-config feature-flag
**Generated**: 2026-11-04
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-16850 "Turn Katas Off by Default (Backend)".
Context: Katas have no on/off setting in the backend: both kata API routers are always registered and check authentication only. Shipped default customer configuration has no katas entry; an absent feature reads as off. Other customer-configuration features already refuse API calls with 404 "feature not available" when off. Preconfigured katas are loaded at startup independently of the API.
Story: As a platform administrator installing CodeMie, I want the katas feature setting shipped off and enforced by the API so a default installation does not serve katas I have not chosen to offer.
Acceptance criteria:
1. Shipped default configuration carries a katas entry set to disabled, so katas can be turned on via its environment override without copying the file.
2. Feature configuration served to the frontend shows katas as off by default.
3. With default config, every kata API endpoint (kata content, leaderboard, my progress, start, complete, progress) is refused with the same response as other disabled features (404, feature not available), returning/changing no kata data.
4. At startup with default config, preconfigured katas still load (seed unchanged).
5. When katas are turned on via env override or a customer config entry, the kata API works as today and earlier progress is intact.
6. A customer config with no katas entry reads as off, no error.
7. Release notes state katas turn off on upgrade and how to turn them on (env override on the shipped config, or katas entry in a customer config).
Out of scope: frontend, removing seed/data/content, leaderboard scoring/usage attribution, admin-settings switch.
Hint from PO analysis: follow the existing routing/CLI-analytics per-router 404 gate pattern; files likely customer-config.yaml, ai_kata router(s), possibly main.py, tests, release note.

---

## 2. Codebase Findings

### Existing Implementations

Purpose A: refuse API calls of a feature that is off.
- CLI analytics gate: `src/codemie/rest_api/routers/cli_analytics.py:154` `_ensure_enabled()` calls `customer_config.is_feature_enabled("cliAnalytics")`, raises `ExtendedHTTPException` 404 "Local Analytics feature is not available" (details/help fields). Called as first line of each handler (e.g. line 166), not as a router dependency. Tests patch it via `patch.object(router_module, "_ensure_enabled", ...)` (`tests/codemie/rest_api/routers/test_cli_analytics_storage_wiring.py:108`).
- Routing analytics gate: `src/codemie/rest_api/routers/analytics/routing.py:39` `_ensure_routing_analytics_enabled()`, 404 "Routing Analytics feature is not available"; called inside handlers (line 63).
- Other 404/403 gates: `usage_attribution.py:81`, `cli_enriched_users.py:63`, `workflow.py:272` (subWorkflow). Cost centers (`cost_center_service.py:63`) and teams (`settings_request_validator.py:401`) use message "Feature not available" with code 403, not 404. Message text is per-feature, not a shared constant; no shared helper or router-level dependency gate found.
- Rule: `customer_config.is_feature_enabled(key)` (`src/codemie/configs/customer_config.py:401`) reads component `features:<key>`; missing component = False (`:393-413`).

Purpose B: the kata API surface (what a gate must cover).
- `src/codemie/rest_api/routers/ai_kata.py` (router prefix `/v1`, only `Depends(authenticate)`, line 71-75): GET `/katas/tags`, `/katas/roles`, `/katas`, `/katas/{kata_id}`; POST `/katas`; PUT `/katas/{kata_id}`; POST `.../publish`, `.../unpublish`, `.../archive`; DELETE `/katas/{kata_id}`; POST/DELETE reactions (lines 442, 480; sync `def` handlers).
- `src/codemie/rest_api/routers/user_kata_progress.py` (prefix `/v1/katas`, `Depends(authenticate)`, line 34-38): GET `/leaderboard` (43), GET `/progress/my` (61), POST `/{kata_id}/start` (79), POST `/{kata_id}/complete` (122), GET `/{kata_id}/progress` (165). Note the ticket's list covers these five plus "kata content"; ai_kata admin/CRUD/tags/roles/reaction routes are not enumerated by the ticket.
- Both registered unconditionally at `src/codemie/rest_api/main.py:1485-1486`.

Purpose C: preconfigured katas seeded at startup.
- `main.py:544-553` `_initialize_preconfigured_content()` calls `import_preconfigured_katas()` (`src/external/deployment_scripts/preconfigured_katas.py`, imported main.py:148); no customer_config read there. Content under `config/katas/`. Import tooling: `src/external/utility_scripts/import_katas.py`, `src/codemie/service/kata_import_service.py`.

Purpose D: default config entry and env override.
- `config/customer/customer-config.yaml` (and `standalone/external-config/customer-config.yaml`): no `features:katas` entry (grep). Entry shape example: `features:cliAnalytics` at line 119 (`settings: enabled: false, name, description, availableForExternal: false`).
- Env override at load: `customer_config.py:131` `_apply_feature_env_override`: only for existing YAML `features:*` components; env name `FEATURE_<UPPER_SNAKE of key>` (key `katas` -> `FEATURE_KATAS`), value `== 'true'` enables. An absent YAML entry gets no override (hence AC1).
- Separate `CUSTOMER_CONFIG__...` key prefix exists for admin-setting declarations (`customer_config_declarations.py:29,57`); `SettingDeclaration`s (e.g. `CLI_ANALYTICS` at :226, registry list around :474) back the admin settings UI; no katas declaration (admin switch is out of scope).
- Frontend feature list: served from `customer_config.components` (resolve_all, `customer_config.py:385`); entry added to YAML would appear with enabled false.

Other touched code (not named above): kata services `src/codemie/service/ai_kata_service.py`, `user_kata_progress_service.py`, `kata_user_interaction_service.py`; leaderboard `src/codemie/service/leaderboard/{collector,scorer}.py` and `usage_attribution/settings.py` mention katas (scoring; out of scope; unread beyond grep match).

### Decisions Already in the Code
- Convention: per-handler guard function raising `ExtendedHTTPException(404, message "<X> feature is not available", details, help)`; guard defined in each router module (`cli_analytics.py:154`, `routing.py:39`). No router-level dependency used for gating.
- Absent component = off (`customer_config.py:399`); env override applies only to components present in YAML (`:133-143`).
- Startup seeding is independent of customer_config (main.py:553); no recorded reason.
- Release notes: not in this repo (UI links `#/release-notes` only); precedent task `docs/superpowers/tasks/2026-09-02-epmcdme-14467-retire-aiadoption-flag/` refers to release notes, and `standalone/README.md` documents flags in prose.

### Task Statements Checked Against the Code
- "both kata API routers are always registered and check authentication only": holds (main.py:1485-1486; ai_kata.py:71-75; user_kata_progress.py:34-38).
- "Shipped default configuration has no katas entry; absent reads as off": holds (grep of both YAML files; customer_config.py:399).
- "Other features refuse with 404 'feature not available'": partly holds: 404 with feature-specific messages (cli_analytics.py:157, routing.py:42); cost centers/teams use 403 "Feature not available". No single shared response.
- "Preconfigured katas loaded at startup independently of the API": holds (main.py:553).
- "env override on the shipped config" (AC1/7): holds only once a `features:katas` entry exists (customer_config.py:133); variable name derived `FEATURE_KATAS`.
- "kata content" endpoints (AC3): AC lists kata content, leaderboard, my progress, start, complete, progress; ai_kata.py has additional tags/roles/CRUD/reaction routes that the list does not name (keeps them served unless covered).
- "earlier progress is intact": no code deletes progress on config; DB tables untouched by config (no kata reference to customer_config found).

### Consumers of Changed Contracts
- Changed contract: `features:katas` component (new) and kata route availability.
- Searched `customer_config|is_feature_enabled|features:` for kata code: none found in kata routers, services, leaderboard collector, import scripts.
- Consumers of the kata endpoints (frontend, repo `codemie-ui`) are outside this repo: unverified.
- Tests that call kata routes directly without a customer_config patch: `tests/codemie/rest_api/routers/test_ai_kata.py`, `test_user_kata_progress.py` (handlers invoked directly as async functions, e.g. `test_get_leaderboard_success` at line 101); a handler-level guard would run in them.
- Tests enumerating shipped YAML components: `tests/codemie/configs/test_customer_config.py:852` lists feature ids (e.g. "features:cliAnalytics").
- Router registration at main.py:1485-1486 and route ordering: `/v1/katas/leaderboard` etc. (user_kata_progress) and `/v1/katas/{kata_id}` (ai_kata) share a prefix.

### Architecture and Layers Affected
REST routers (`ai_kata.py`, `user_kata_progress.py`, `main.py` registration), customer configuration (YAML + `customer_config.py`), deployment/startup seeding (unchanged), docs/release note.

### Integration Points
customer_config singleton (`customer_config.py:430`), `ExtendedHTTPException` (`codemie.core.exceptions`), frontend feature list consumer (outside repo).

### Patterns and Conventions
Guard function per router + `ExtendedHTTPException`; YAML `features:<key>` entries with `name`, `description`, `availableForExternal`; feature env var `FEATURE_<NAME>`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists (api, development/configuration-patterns.md). Not read in depth; no kata-specific guide found. `standalone/README.md:262` documents the cliAnalytics gate in prose.

### Architectural Decisions
None found for katas gating.

### Derived Conventions
See Patterns above.

---

## 4. Testing Landscape

### Existing Coverage
`tests/codemie/rest_api/routers/test_ai_kata.py`, `test_user_kata_progress.py` (router handlers), service/repository tests for katas (`tests/codemie/service/test_ai_kata_service.py`, `test_user_kata_progress_service.py`, `test_kata_user_interaction_service.py`, `test_kata_import_service.py`, `tests/codemie/repository/test_ai_kata_repository.py`, `test_user_kata_progress_repository.py`). `tests/codemie/configs/test_customer_config.py` covers config loading.

### Testing Framework and Patterns
pytest (async handler tests, `patch.object(router_module, "_ensure_enabled", ...)` pattern for gates).

### Coverage Gaps
No test of kata gating, of a `features:katas` entry, or of `FEATURE_KATAS` override. No tests for `preconfigured_katas.py` found by name.

---

## 5. Configuration and Environment

### Environment Variables
`FEATURE_<UPPER_SNAKE>` derived by `customer_config.py:131-147`; for katas `FEATURE_KATAS` (derived, no entry yet).

### Configuration Files
`config/customer/customer-config.yaml`, `standalone/external-config/customer-config.yaml` (both lack katas; standalone has its own copy of cliAnalytics at :119).

### Feature Flags and Deployment Concerns
Seed (`import_preconfigured_katas`) runs regardless. Release notes location not found in repo.

---

## 6. Risk Indicators

- Two shipped YAML copies (config/customer and standalone/external-config): both lack a katas entry.
- Gate text/status is not standardized (404 per-feature messages vs 403 "Feature not available"); AC3 wording "same response as other disabled features" maps to the 404 routers.
- Unlisted kata routes (tags, roles, CRUD, reactions in ai_kata.py) fall outside the ticket's endpoint list.
- Existing router tests call handlers directly; a gate in handlers changes their default behaviour (customer_config unpatched = off). Speculative: tests need a patch.
- Existing mechanism kept in place: startup seeding independent of config (Section 2, Purpose C).
- Release-note location not found in this repo; frontend consumer outside repo, unverified.
- Leaderboard/usage attribution code mentions katas (`service/leaderboard`, `usage_attribution/settings.py`): not read; scope stated out.

---

## 7. Summary for Complexity Assessment

Layers: customer-config YAML (two files), two REST routers (about 17 routes across `ai_kata.py` and `user_kata_progress.py`), and possibly the router registration in `main.py`. Startup seeding is unchanged and independent. The repo has two same-shape precedents (`cli_analytics.py:154`, `routing.py:39`) for a per-router 404 guard on `customer_config.is_feature_enabled`, and the config reader already treats an absent component as off.

Novelty is low. Tests exist for both routers and call handlers directly; no gating tests exist. Env override works only for components present in YAML, so the shipped entry matters. Key risks: the unlisted ai_kata routes, two YAML copies, existing tests affected by an unpatched default-off, release-note location unknown, and the frontend consumer being outside the repo.

---

## 8. External References

None named by the task.
