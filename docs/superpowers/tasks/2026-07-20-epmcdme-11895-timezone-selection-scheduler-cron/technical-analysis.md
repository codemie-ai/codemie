# Technical Research

**Task**: scheduler integration cron timezone trigger
**Generated**: 2026-07-20T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Allow timezone selection for cron expression in Scheduler integration creation. Currently, when a user creates a Scheduler integration and configures a cron expression, there is no explicit way to define the time zone. This improvement should allow the user to specify the time zone for the cron expression during Scheduler integration creation. Acceptance criteria: (1) User can specify a time zone when creating a Scheduler integration with a cron expression. (2) The selected time zone is saved together with the cron configuration. (3) Scheduled execution uses the selected time zone when interpreting the cron expression. (4) The UI clearly shows which time zone is applied. (5) If a default time zone is preselected, it is visible and can be changed. (6) Time zone support does not break existing scheduler creation and execution flows. (7) Validation prevents saving invalid or unsupported time zone values.

---

## 2. Codebase Findings

### Existing Implementations

- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/triggers/bindings/cron.py` — Core `Cron` class (APScheduler-based engine). Polls `Settings` every 10 s, creates/removes `CronTrigger` jobs per enabled scheduler settings. The `__create_cron_trigger` static method constructs `apscheduler.triggers.cron.CronTrigger` from the stored 5-field cron expression with **no timezone parameter**. This is the primary execution point where timezone must be injected.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/service/settings/scheduler_settings_service.py` — `SchedulerSettingsService` class plus two standalone validation functions (`validate_cron_expression`, `_validate_minimum_hourly_frequency`). Handles CRUD of the `Settings` row for a scheduler. The `_create_new_schedule` method constructs the `credential_values` list: `schedule`, `resource_type`, `resource_id`, `is_enabled`. **No `timezone` key** exists in the credential values at present.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/service/settings/settings_request_validator.py` — `validate_scheduler_request` / `validate_cron_expression` functions validate the incoming `SettingRequest`. `validate_cron_expression` validates using both `croniter` and `APScheduler.CronTrigger` (without timezone). This is where timezone validation logic must be added.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/rest_api/routers/user_settings.py` — Router for `POST /v1/settings/user` and `PUT /v1/settings/user/{setting_id}`. Calls `validate_scheduler_request(request)` for `CredentialTypes.SCHEDULER` requests. Entry point for user-facing Scheduler integration creation/update.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/rest_api/models/settings.py` — `Scheduler` Pydantic model (lines 213–217): fields `schedule`, `is_enabled`, `resource_type`, `resource_id`. No `timezone` field. `CredentialValues` is a generic `key: str, value: Any` store backed by JSONB in the `settings` table. The `Settings` SQLModel class uses `credential_values: List[CredentialValues]` as a JSONB column — adding a new key does **not** require a DB schema migration.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/rest_api/routers/index.py` — Datasource-specific creation/update endpoints (e.g., `POST /application/{app_name}/index`) that accept `cron_expression: Optional[str]` in `CreateIndexRequest`, `IndexKnowledgeBaseConfluenceRequest`, etc. All routes delegate schedule upsert to `_update_datasource_scheduler` or `BaseDatasourceProcessor._create_or_update_scheduler`. These paths do **not** currently accept a `timezone` field.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/rest_api/models/index.py` — Request models (`CreateIndexRequest`, `IndexKnowledgeBaseConfluenceRequest`, `IndexKnowledgeBaseJIRARequest`, etc.) that include `cron_expression: Optional[str] = None`. The `CronExpressionValidatorMixin` validates this field using `validate_cron_expression` from `scheduler_settings_service`. A corresponding `timezone: Optional[str]` field must be added to these models and the mixin.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/datasource/base_datasource_processor.py` — `BaseDatasourceProcessor._create_or_update_scheduler` method (lines 331–375) — delegates to `SchedulerSettingsService.handle_schedule`. Must accept and forward a `timezone` argument.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/service/index/index_service.py` — `IndexStatusService.enrich_index_with_schedule` (lines 314–339). Returns a dict with `cron_expression` key. Will need to also return `timezone` from the schedule map.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/triggers/node_controller.py` — `NodeController` wires up a single `Cron()` instance and toggles it via an Elasticsearch leader-lock. No scheduler-specific logic beyond lifecycle management.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/service/stale_datasource/scheduler.py` — Shows existing timezone pattern: `CronTrigger.from_crontab(config.STALE_DATASOURCE_SCHEDULE, timezone="UTC")`. APScheduler 3.x `CronTrigger` natively accepts a `timezone` string or `datetime.tzinfo` object.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/datasource/loader/jira_loader.py` — Uses `pytz` (imported directly) with `config.TIMEZONE` for Jira incremental reindex time window calculations. `pytz` is a transitive dependency (not declared in `pyproject.toml` directly) — this is a risk for strict dependency resolution.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/datasource/loader/xray_loader.py` — Uses `zoneinfo.ZoneInfo` (stdlib since Python 3.9) with `config.TIMEZONE` for Xray incremental reindex time window.

### Architecture and Layers Affected

| Layer | Component | Change Required |
|---|---|---|
| API (Request Models) | `src/codemie/rest_api/models/index.py` — all `CronExpression`-bearing request models, `CronExpressionValidatorMixin` | Add `timezone: Optional[str]` field; extend mixin validator to validate timezone value |
| API (Routers) | `src/codemie/rest_api/routers/user_settings.py` — `POST/PUT /v1/settings/user` | No structural change; validation delegates to `validate_scheduler_request` |
| API (Routers) | `src/codemie/rest_api/routers/index.py` — datasource creation/update endpoints | Pass `timezone` field from request down to `_update_datasource_scheduler` and `_create_or_update_scheduler` |
| Service (Validation) | `src/codemie/service/settings/settings_request_validator.py` — `validate_scheduler_request`, `validate_cron_expression` | Add `validate_timezone` function; integrate into scheduler request validation |
| Service (Business Logic) | `src/codemie/service/settings/scheduler_settings_service.py` — `SchedulerSettingsService` | Add `timezone` to `credential_values` in `_create_new_schedule`; update `_update_schedule_values`, `get_scheduler_settings_for_datasources`, and `handle_schedule` signatures |
| Service (Business Logic) | `src/codemie/service/index/index_service.py` — `IndexStatusService.enrich_index_with_schedule` | Return `timezone` alongside `cron_expression` |
| Trigger Engine | `src/codemie/triggers/bindings/cron.py` — `Cron.__create_cron_trigger`, `Cron.__valid_setting`, `Cron.__actualize_cron_job`, `Cron.__actualize_jobs` | Read `timezone` credential value; pass to `CronTrigger(... timezone=...)` |
| Datasource Processors | `src/codemie/datasource/base_datasource_processor.py` — `_create_or_update_scheduler` | Accept `timezone` parameter; forward to `SchedulerSettingsService.handle_schedule` |

### Integration Points

- **APScheduler `CronTrigger`**: Already accepts a `timezone` parameter (string like `"America/New_York"` or `datetime.tzinfo`). All internal schedulers that already pass `timezone="UTC"` explicitly (e.g., `stale_datasource/scheduler.py`, `main.py`) prove the integration path is established. No library upgrade is needed.
- **`croniter`**: `croniter(expr, start_time, hash_use_datetime=True)` accepts a timezone-aware start time; however, for purely format validation (existing usage) timezone is not needed. The `_validate_minimum_hourly_frequency` helper computes next-execution gap and would remain accurate regardless of timezone since the gap (3600 s) is absolute.
- **`config.TIMEZONE`**: A global `str` setting defaulting to `"UTC"` (line 56 of `config.py`). This value is the instance-wide default for Jira and Xray loaders. It would become the natural fallback when no per-scheduler timezone is specified.
- **JSONB `credential_values` column**: Stores key-value pairs as a JSONB array. Adding a new key (`timezone`) is backward-compatible with existing rows (absent key treated as `None`, defaulting to UTC). No Alembic migration is required for the `settings` table.
- **`pytz` / `zoneinfo`**: `pytz` is imported in `jira_loader.py` but is **not** a declared `pyproject.toml` dependency — it exists as a transitive dependency of `apscheduler` 3.x. For the timezone feature, the preferred approach is Python 3.9+ `zoneinfo.ZoneInfo` (already used by `xray_loader.py`) with `zoneinfo.available_timezones()` for validation. This avoids undeclared dependency risk.

### Patterns and Conventions

- **`credential_values` as a key-value bag**: Scheduler settings store all parameters (schedule, resource_type, resource_id, is_enabled, prompt) as `CredentialValues(key=..., value=...)` list entries rather than dedicated DB columns. The `timezone` field follows this pattern exactly — add `CredentialValues(key="timezone", value=timezone)`.
- **`__get_cred_value` helper**: `Cron.__get_cred_value(setting, "key")` is the canonical way to read a credential value inside the trigger engine. The timezone value must be read via this helper in `__valid_setting` and forwarded through `__actualize_cron_job` and `__create_cron_trigger`.
- **Validator mixin pattern**: `CronExpressionValidatorMixin` at `models/index.py:113` uses a Pydantic `@model_validator(mode='after')` to validate the `cron_expression` field. A timezone validator can extend this same mixin or be added as an additional `@model_validator` step.
- **`validate_scheduler_request` pipeline**: Request validation for `POST/PUT /v1/settings/user` is orchestrated by `validate_scheduler_request` in `settings_request_validator.py`. Each check is a discrete function. A `validate_timezone_value(request)` function should be added to this pipeline following the same pattern.
- **`ExtendedHTTPException`** with `code`, `message`, `details`, `help` fields is the project-standard error response for validation failures.
- **Default timezone from config**: `config.TIMEZONE` (default `"UTC"`) is the existing project-wide timezone default and should be used as the pre-selected default value in the API when no timezone is provided.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `/Users/yevhen_slyva/codemie-dev/codemie/.ai-run/guides/architecture/layered-architecture.md` — HTTP concerns in routers, business logic in services, persistence in repositories. The timezone feature must follow this boundary.
- `/Users/yevhen_slyva/codemie-dev/codemie/.ai-run/guides/development/configuration-patterns.md` — Config and environment variable patterns. Relevant for `config.TIMEZONE` default usage.
- `/Users/yevhen_slyva/codemie-dev/codemie/.ai-run/guides/testing/testing-patterns.md` — Pytest policy; tests go under `tests/codemie/` mirroring source structure.
- `/Users/yevhen_slyva/codemie-dev/codemie/.ai-run/guides/development/error-handling.md` — `ExtendedHTTPException` pattern for validation errors.

### Architectural Decisions

- Cron expressions are validated in two places: (1) `settings_request_validator.py` for `POST/PUT /v1/settings/user` API requests; (2) `CronExpressionValidatorMixin` in `models/index.py` for datasource index creation/update requests. The timezone validation must be added symmetrically to both places.
- Internal scheduler (trigger engine) validates the schedule again at job-actualization time via `__valid_schedule` (croniter only). If the timezone is invalid at storage time it may cause silent APScheduler errors at job creation. The trigger engine's `__create_cron_trigger` should handle invalid timezone gracefully (log error and return `None`).
- System-internal schedulers (leaderboard, spend tracking, conversation analysis, stale datasource detection) use hardcoded `timezone="UTC"` and are not affected by this feature.

### Derived Conventions

- `zoneinfo.ZoneInfo` (stdlib) is already used in the codebase (`xray_loader.py`). Validation of timezone string should use `zoneinfo.available_timezones()` (or `ZoneInfo(tz_str)` with try/except `ZoneInfoNotFoundError`). Do not introduce `pytz` for this feature since it is not a declared project dependency.
- `config.TIMEZONE = "UTC"` should be used as the API-level default when the caller omits the timezone field, maintaining full backward compatibility.
- The `Scheduler` Pydantic model in `settings.py` (lines 213–217) is not used directly in the API flow (the raw `SettingRequest` is used instead) but should be updated as a documentation model.

---

## 4. Testing Landscape

### Existing Coverage

- `/Users/yevhen_slyva/codemie-dev/codemie/tests/codemie/triggers/bindings/test_cron.py` — 20+ unit tests for `Cron` class: job scheduling, stale watchdog, setting validation, datasource type dispatch.
- `/Users/yevhen_slyva/codemie-dev/codemie/tests/codemie/triggers/bindings/test_cron_prompt_extension.py` — Tests for prompt credential value handling in `Cron.__valid_setting` and `__actualize_cron_job`. Identical pattern to what timezone tests will require.
- `/Users/yevhen_slyva/codemie-dev/codemie/tests/codemie/rest_api/routers/test_settings_cron_validation.py` — `TestScheduleCredentialValidation` and `TestScheduleCredentialParametrized`: parametrized tests for `validate_cron_expression`. Tests valid/invalid expressions, frequency limits.
- `/Users/yevhen_slyva/codemie-dev/codemie/tests/codemie/service/settings/test_scheduler_settings_service.py` — Tests for `validate_cron_expression` in `scheduler_settings_service.py`.
- `/Users/yevhen_slyva/codemie-dev/codemie/tests/codemie/service/settings/test_scheduler_settings_service_class.py` — Additional tests for `SchedulerSettingsService` class methods.
- `/Users/yevhen_slyva/codemie-dev/codemie/tests/codemie/service/settings/test_settings_request_validator.py` — Tests for `validate_datasource_type_for_scheduler`.
- `/Users/yevhen_slyva/codemie-dev/codemie/tests/codemie/triggers/test_node_controller.py` — Tests for `NodeController` lifecycle.

### Testing Framework and Patterns

- **Framework**: `pytest` with `pytest-asyncio`.
- **Mocking**: `unittest.mock.patch` and `MagicMock`. No `pytest-mock`; standard library mocking only.
- **Fixtures**: Function-scoped via `@pytest.fixture`. `mock_setting` fixture in `test_cron.py` provides the standard `Settings`-like mock with `credential_values` list.
- **Parametrize**: `@pytest.mark.parametrize` is heavily used in validation tests for comprehensive input coverage.
- **No live DB**: All tests that touch `Settings` or `IndexInfo` mock the underlying storage calls.

### Coverage Gaps

- No tests exercise `CronTrigger(..., timezone=...)` parameterization — the trigger engine tests mock `CronTrigger` entirely.
- No tests validate that an invalid timezone string is rejected at the API layer.
- No tests verify backward compatibility (settings without a `timezone` credential value default to UTC execution).
- `IndexStatusService.enrich_index_with_schedule` has no tests for the `timezone` enrichment path.
- Datasource request models (`CreateIndexRequest`, etc.) are tested implicitly through router tests but the `CronExpressionValidatorMixin` has no dedicated unit tests.

---

## 5. Configuration and Environment

### Environment Variables

- `TIMEZONE` (default: `"UTC"`) — `src/codemie/configs/config.py:56`. Project-wide timezone string. Currently used by Jira loader (`pytz.timezone(config.TIMEZONE)`) and Xray loader (`ZoneInfo(config.TIMEZONE)`). Should become the default value for per-scheduler timezone when not specified by the user.
- `TRIGGER_ENGINE_ENABLED` (default: `False`) — Gates the trigger engine startup.
- `CRON_SCHEDULER_MAX_WORKERS` (default: `20`) — APScheduler thread pool size.
- `SCHEDULER_PROMPT_SIZE_LIMIT` (default: `4000`) — Max prompt length for assistant/workflow scheduler jobs.

### Configuration Files

- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/configs/config.py` — Pydantic `BaseSettings` class. `TIMEZONE` field at line 56 is the relevant config entry.
- No per-scheduler timezone configuration exists beyond the global `TIMEZONE` default.

### Feature Flags and Deployment Concerns

- `TRIGGER_ENGINE_ENABLED` must be `true` for the trigger engine to start and pick up the new timezone field. No new flags are required.
- No DB migration is needed — `credential_values` is a JSONB column and adding a new key is fully backward-compatible. Existing scheduler rows without a `timezone` credential value should default to `config.TIMEZONE` (or UTC) at runtime, ensuring AC (6) (no breakage of existing flows).
- The IANA timezone database (`tzdata` package or OS `/usr/share/zoneinfo`) must be available at runtime for `zoneinfo.ZoneInfo` validation. In Alpine-based Docker images, `tzdata` must be installed explicitly. This is a deployment concern that must be verified in the Dockerfile.

---

## 6. Risk Indicators

- **No timezone parameter in `Cron.__create_cron_trigger`**: The static method at `cron.py:424–434` calls `CronTrigger(minute=..., hour=..., day=..., month=..., day_of_week=...)` with no `timezone`. This means all current scheduler executions run in the server's local timezone (or APScheduler's default, which is the local machine timezone). Adding the parameter here is the critical execution path change.
- **`pytz` is an undeclared transitive dependency**: Used in `jira_loader.py` but absent from `pyproject.toml`. New timezone feature should use `zoneinfo` (stdlib) instead. If APScheduler 3.x requires `pytz` internally, it is already present transitively — but relying on it for new code is fragile.
- **Duplicate validation logic**: `validate_cron_expression` exists in two files — `scheduler_settings_service.py` (used by index router path) and `settings_request_validator.py` (used by user_settings router path). The timezone validation function must likewise be added in both places, or the two should be unified.
- **`CronExpressionValidatorMixin` is used in 10+ request models**: Any change to the mixin or the models adding `timezone` affects `CreateIndexRequest`, `CreateSVNIndexRequest`, `IndexKnowledgeBaseConfluenceRequest`, `IndexKnowledgeBaseJIRARequest`, `IndexKnowledgeBaseXrayRequest`, `IndexKnowledgeBaseAzureDevOpsWikiRequest`, `IndexKnowledgeBaseAzureDevOpsWorkItemRequest`, `IndexKnowledgeBaseSharePointRequest`, `IndexKnowledgeBaseGoogleRequest`, `UpdateIndexRequest`, `UpdateKnowledgeBaseGoogleRequest` — a wide change surface requiring careful backward-compatibility handling.
- **`SchedulerSettingsService._create_new_schedule` and `_update_schedule_values` do not include timezone**: Both methods must be updated; a missed update in `_update_schedule_values` would silently drop the timezone on update.
- **`Cron.__valid_setting` does not read or pass `timezone`**: The method returns a `dict` but omits timezone. `__actualize_jobs` and `__actualize_cron_job` do not accept it either. This is a chain of three methods requiring coordinated changes.
- **`tzdata` on Docker/Alpine**: The `zoneinfo` module requires OS-level IANA timezone data. Alpine Linux containers do not ship it by default. The Dockerfile must install `tzdata`. Failure to do so will cause `ZoneInfoNotFoundError` at runtime for any timezone other than UTC.
- **No tests for `enrich_index_with_schedule` timezone path**: The enrichment service method has no unit tests; adding `timezone` to the returned dict would not be validated without new tests.
- **`Scheduler` model in `settings.py` is documentation-only**: Lines 213–217 define a `Scheduler` model that is never imported in the request/response flow — only `SettingRequest` with raw `credential_values` is used. The `Scheduler` model should be updated for correctness but has no runtime impact.
- **`get_scheduler_settings_for_datasources` returns only `cron_expression`**: The method (lines 263–299 in `scheduler_settings_service.py`) returns `Dict[str, str]` (datasource_id → cron_expression). It must be extended to also return timezone so callers (`enrich_index_with_schedule`) can surface it.

---

## 7. Summary for Complexity Assessment

The feature spans six distinct architectural layers: API request models (`models/index.py`), API request validation (`settings_request_validator.py`, `scheduler_settings_service.py`), API routers (`user_settings.py`, `index.py`), service layer (`SchedulerSettingsService`, `IndexStatusService`), datasource processors (`BaseDatasourceProcessor._create_or_update_scheduler`), and the trigger engine (`Cron.__create_cron_trigger`, `__valid_setting`, `__actualize_cron_job`). Conservatively, the change surface is 10–15 files. The critical execution path — where the timezone is actually applied — is a one-line addition in `Cron.__create_cron_trigger` (adding `timezone=timezone` to the `CronTrigger` constructor), but reaching that point requires threading the timezone string through five intermediate layers that currently have no parameter for it.

The feature does not introduce entirely novel architectural patterns. Timezone is simply a new `CredentialValues` key stored in the existing JSONB `credential_values` column, following the same pattern as `schedule`, `resource_type`, and `is_enabled`. APScheduler's `CronTrigger` already natively supports a `timezone` parameter (demonstrated in `stale_datasource/scheduler.py` with `timezone="UTC"`). The `zoneinfo` stdlib module is already used in the repo (`xray_loader.py`), providing a validation path without new dependencies. No database migration is required. The main novelty is the validation function for IANA timezone strings, which must be added symmetrically in two validator modules and integrated into both the user-settings and index-router creation paths.

Test coverage for the scheduler domain is reasonably good: the trigger engine (`test_cron.py`), prompt extension (`test_cron_prompt_extension.py`), and cron validation (`test_settings_cron_validation.py`, `test_scheduler_settings_service.py`) all have dedicated test files with parametrized cases. The `test_cron_prompt_extension.py` file in particular provides a direct template for writing timezone tests in the trigger engine. Key gaps are: backward-compatibility tests (existing rows without timezone defaulting to UTC), invalid-timezone rejection tests at both API validation paths, and `enrich_index_with_schedule` timezone enrichment tests. The Dockerfile must also be verified for `tzdata` package availability to avoid silent runtime failures on Alpine-based deployments.
