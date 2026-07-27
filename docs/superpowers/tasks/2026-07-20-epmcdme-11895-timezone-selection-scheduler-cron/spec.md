# Spec: Timezone Selection for Scheduler Cron Expression

**Ticket**: EPMCDME-11895  
**Branch**: `EPMCDME-11895_timezone-selection-for-scheduler-cron`  
**Scope**: Backend only — both scheduler creation paths (user-settings and index router)

---

## Problem

When a Scheduler integration is created with a cron expression, no timezone can be specified. APScheduler defaults to the server's local timezone, making scheduled execution time unpredictable and DST-unaware. Users have no way to ensure a cron fires at their expected local time.

---

## Solution

Add an optional `timezone` field (IANA name, e.g. `"Europe/Warsaw"`) to both scheduler creation paths. Store it as a new `credential_values` entry alongside the existing `schedule` key. Apply it when constructing the APScheduler `CronTrigger`. Default to `config.TIMEZONE` (`"UTC"`) when absent, preserving full backward compatibility with existing rows.

---

## Timezone Format

**IANA timezone names only** (e.g. `"America/New_York"`, `"Europe/Warsaw"`, `"UTC"`).

UTC offsets (`UTC+2`) are not accepted — they are DST-unaware and would cause cron jobs to fire at the wrong local time for half the year in regions that observe DST. `"UTC"` is a valid IANA name and covers the zero-offset case.

---

## Data Model

No database migration is required. The `credential_values` column is a JSONB key-value list. A new entry is appended alongside the existing `schedule`, `resource_type`, `resource_id`, and `is_enabled` entries:

```python
CredentialValues(key="timezone", value="America/New_York")
```

Existing rows without a `timezone` key are handled at runtime by falling back to `config.TIMEZONE`. No backfill is needed.

`get_scheduler_settings_for_datasources` return type changes from `Dict[str, str]` to `Dict[str, dict]`:

```python
# Before
{"<resource_id>": "0 9 * * *"}

# After
{"<resource_id>": {"cron_expression": "0 9 * * *", "timezone": "America/New_York"}}
```

The sole caller, `IndexStatusService.enrich_index_with_schedule`, is updated to consume the new shape and return `timezone` in its enrichment output.

---

## Validation

A single `validate_timezone_string(timezone: Optional[str]) -> None` function is added to `scheduler_settings_service.py` alongside the existing `validate_cron_expression`.

```python
def validate_timezone_string(timezone: Optional[str]) -> None:
    if timezone is None:
        return
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Invalid timezone",
            details=f"'{timezone}' is not a recognised IANA timezone name.",
            help="Provide an IANA timezone name such as 'Europe/Warsaw', 'America/New_York', or 'UTC'.",
        )
```

**User-settings path** (`settings_request_validator.py`): a `validate_timezone_value(request)` function is added to the `validate_scheduler_request` pipeline, extracting the `timezone` credential value and delegating to `validate_timezone_string`.

**Index router path** (`models/index.py`): `CronExpressionValidatorMixin` gains `timezone: Optional[str] = None` and the existing `validate_cron_expression_field` model validator is extended to also call `validate_timezone_string(self.timezone)`. All 10+ request models that inherit the mixin pick up the field automatically.

---

## Layer-by-Layer Changes

### Layer 1 — Request models (`models/index.py`)

`CronExpressionValidatorMixin` gains:
- `timezone: Optional[str] = None` field
- `validate_timezone_string(self.timezone)` called inside the existing `validate_cron_expression_field` model validator when `timezone` is in `model_fields_set`

### Layer 2 — API routers (`routers/index.py`)

Each call to `_create_or_update_scheduler` (or `_update_datasource_scheduler`) passes `timezone=request.timezone`.

### Layer 3 — Datasource processor (`base_datasource_processor.py`)

`_create_or_update_scheduler` gains `timezone: Optional[str] = None` and forwards it to `SchedulerSettingsService.handle_schedule`.

### Layer 4 — Service (`scheduler_settings_service.py`)

- `handle_schedule` gains `timezone: Optional[str] = None`, passes it to `_create_new_schedule` and `_update_schedule_values`.
- `_create_new_schedule` appends `CredentialValues(key="timezone", value=timezone)` when `timezone` is not `None`.
- `_update_schedule_values` updates the `timezone` credential value when present; adds it when absent.
- `get_scheduler_settings_for_datasources` returns `Dict[str, dict]` with both `cron_expression` and `timezone` keys.

### Layer 5 — Trigger engine (`cron.py`)

- `__valid_setting` reads `timezone = self.__get_cred_value(setting, "timezone")` and includes it in the returned dict.
- `__actualize_jobs` passes `timezone=valid_setting.get("timezone")` to `__actualize_cron_job`.
- `__actualize_cron_job` gains `timezone: Optional[str] = None` and passes it to `__create_cron_trigger`.
- `__create_cron_trigger` gains `timezone: Optional[str] = None` and applies it:

```python
@staticmethod
def __create_cron_trigger(cron_expression, timezone=None):
    from codemie.configs.config import config
    minute, hour, day_of_month, month, day_of_week = cron_expression.split()
    day_of_week = Cron.__normalize_day_of_week(day_of_week)
    return CronTrigger(
        minute=minute, hour=hour, day=day_of_month,
        month=month, day_of_week=day_of_week,
        timezone=timezone or config.TIMEZONE,
    )
```

### Layer 6 — Index enrichment (`index_service.py`)

`enrich_index_with_schedule` reads `timezone` from the updated `schedule_map` dict and includes it in the returned enrichment dict alongside `cron_expression`.

---

## Backward Compatibility

- Existing scheduler rows have no `timezone` credential value. `__get_cred_value(setting, "timezone")` returns `None`. `__create_cron_trigger` falls back to `config.TIMEZONE` (`"UTC"`). Behaviour is identical to current.
- All new parameters are `Optional[str] = None`. No existing call site breaks.
- No Alembic migration required.

---

## Dependency

The Python `tzdata` package is added to `pyproject.toml`. This ships IANA timezone data as a Python package, making `zoneinfo.ZoneInfo` work regardless of OS timezone data availability. No Dockerfile change is required.

---

## Testing

| File | Coverage added |
|---|---|
| `tests/codemie/triggers/bindings/test_cron.py` | `__create_cron_trigger` passes timezone to `CronTrigger`; `__valid_setting` extracts and forwards timezone; missing key falls back to `config.TIMEZONE` |
| `tests/codemie/rest_api/routers/test_settings_cron_validation.py` | Valid IANA name accepted; invalid string rejected with 422; `None` accepted (backward compat) |
| `tests/codemie/service/settings/test_scheduler_settings_service.py` | `validate_timezone_string` parametrized cases; `_create_new_schedule` stores timezone credential; `_update_schedule_values` sets/updates timezone |
| `tests/codemie/service/settings/test_settings_request_validator.py` | `validate_scheduler_request` rejects bad timezone via new pipeline step |
| `tests/codemie/service/index/test_index_service.py` (new) | `enrich_index_with_schedule` returns `timezone` key from schedule map |

Test template: `tests/codemie/triggers/bindings/test_cron_prompt_extension.py` — same pattern (new credential key → read in `__valid_setting` → forwarded through `__actualize_cron_job`).

---

## Out of Scope

- Frontend UI changes (separate frontend ticket)
- UTC offset support (`UTC+2` style strings)
- Backfilling existing scheduler rows with a default timezone value
- Changes to internal system schedulers (leaderboard, stale datasource, spend tracking) — these already hardcode `timezone="UTC"` and are unaffected
