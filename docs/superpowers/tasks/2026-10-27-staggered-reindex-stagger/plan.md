# Staggered Reindex Schedule Offset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent "Datasource indexing queue timeout exceeded" by giving each datasource a deterministic per-datasource minute/hour offset on its cron trigger, without changing what the user configured.

**Architecture:** One new pure module (`src/codemie/triggers/cron_stagger.py`) computes a deterministic offset (BLAKE2b of datasource id + fixed seed, following `job_lock.datasource_lock_key`'s precedent) and applies it to `(minute, hour)` via minutes-of-day mod 1440 arithmetic, only when day/month/day_of_week are all wildcards. `cron.py`'s `__create_cron_trigger` and `scheduler_settings_service.py`'s `_build_scheduler_item` both call into it so the actual trigger and the displayed next-run time agree. Commit per task using the repository's existing convention.

**Tech Stack:** Python, `hashlib.blake2b`, `apscheduler.triggers.cron.CronTrigger`, `croniter`, pytest.

**Spec:** Requirements supplied inline (no spec.md for this task); see Acceptance criteria below and `docs/superpowers/tasks/2026-10-27-staggered-reindex-stagger/technical-analysis.md`.

## Global Constraints

- No new user-facing scheduling option; no Settings schema change; stored `schedule`/`timezone` credential values are never rewritten.
- All staggering logic lives in exactly one new module (`src/codemie/triggers/cron_stagger.py`); `cron.py` and `scheduler_settings_service.py` only call into it.
- Offset must be a pure function of `datasource_id` + a fixed seed constant — no runtime randomness, no persisted state, no Python `hash()`.
- Offset is applied only when `day_of_month`, `month`, `day_of_week` are all `"*"`; otherwise return `(minute, hour)` unchanged.
- Do not touch `datasource_concurrency_manager` or `CronTrigger(jitter=...)`.

---

## Acceptance criteria

- [ ] No new user-facing scheduling option and no Settings schema change; stored cron/timezone credential values are never rewritten.
- [ ] All staggering logic lives in exactly one new module; `cron.py` and `scheduler_settings_service.py` only call into it, never reimplement it.
- [ ] Stagger offset is deterministic and stable for a given datasource id across repeated calls and process restarts.
- [ ] Datasources sharing the same base cron (e.g. "Daily at midnight") resolve to different effective fire minutes/hours (for a sample of distinct ids).
- [ ] Offset arithmetic never crosses a calendar day (mod-1440 wrap) and is only applied when day-of-month, month, day-of-week are all wildcards; non-wildcard crons pass through unchanged.
- [ ] `_build_scheduler_item`'s `nextRunAt` reflects the same staggered minute/hour that `cron.py` actually schedules.
- [ ] New module has its own tests: deterministic-per-id, distinct-across-ids, stable-across-repeated-calls, day-wrap correctness at boundary offsets, wildcard-safety-rule passthrough.
- [ ] Existing `cron.py`, `job_lock.py`, `scheduler_settings_service.py` tests still pass.

**Negative-constraints pass:**
- "No new user-facing scheduling option" / "no Settings schema change" / "stored cron/timezone never rewritten" — honored by Task 1-3: the new module only computes values in memory; no task writes to `Settings`, `credential_values`, or any router/request model. No task touches `src/codemie/rest_api/models/settings.py`.
- "must NOT reuse the abandoned `daily_overnight_random` schedule_type approach" — honored by design: no new field is added anywhere; the offset is derived purely from `datasource_id`, never from a stored preset. Task 1 adds no schema/model symbol at all.
- "must not reintroduce `CronTrigger(jitter=...)`" — honored by Task 2: the stagger is pre-computed before `CronTrigger(...)` is constructed; no `jitter` kwarg is added.
- "Do not touch `datasource_concurrency_manager`" — no task modifies that module.
- "staggering logic must not be duplicated/spread into cron.py or scheduler_settings_service.py beyond calling in" — Task 2 and 3 each add only a single call to `cron_stagger.staggered_minute_hour(...)`; no offset math is re-implemented at either call site.

---

### Task 1: New stagger module with deterministic offset and day-wrap/wildcard logic

**Files:**
- Create: `src/codemie/triggers/cron_stagger.py`
- Test: `tests/codemie/triggers/test_cron_stagger.py`

**Interfaces:**
- Produces: `staggered_minute_hour(datasource_id: str, minute: str, hour: str, day_of_month: str, month: str, day_of_week: str) -> tuple[str, str]` — returns the effective `(minute, hour)` as strings (matching `CronTrigger`'s field types), unchanged when any of `day_of_month`/`month`/`day_of_week` is not `"*"`, or when `minute`/`hour` are not plain integers (e.g. already a range/step expression — leave untouched rather than guess).

- [ ] **Step 1: Write failing tests**

```python
from codemie.triggers.cron_stagger import staggered_minute_hour

def test_deterministic_per_id():
    a = staggered_minute_hour("ds-1", "0", "0", "*", "*", "*")
    b = staggered_minute_hour("ds-1", "0", "0", "*", "*", "*")
    assert a == b

def test_distinct_across_ids():
    results = {staggered_minute_hour(f"ds-{i}", "0", "0", "*", "*", "*") for i in range(50)}
    assert len(results) > 1

def test_day_wrap_stays_within_day():
    minute, hour = staggered_minute_hour("ds-x", "0", "0", "*", "*", "*")
    assert 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59

def test_wildcard_safety_rule_passes_through_non_wildcard():
    assert staggered_minute_hour("ds-1", "0", "0", "15", "*", "*") == ("0", "0")
    assert staggered_minute_hour("ds-1", "0", "0", "*", "*", "1-5") == ("0", "0")

def test_non_integer_fields_pass_through():
    assert staggered_minute_hour("ds-1", "*/5", "0", "*", "*", "*") == ("*/5", "0")
```

Test-first: yes — above tests fail with `ModuleNotFoundError: No module named 'codemie.triggers.cron_stagger'`.

- [ ] **Step 2: Run tests, confirm failure**

Run: `poetry run pytest tests/codemie/triggers/test_cron_stagger.py -v`
Expected: FAIL (import error).

- [ ] **Step 3: Implement the module**

```python
import hashlib

_STAGGER_SEED = b"codemie-reindex-stagger-v1"
_STAGGER_WINDOW_MINUTES = 60


def _offset_minutes(datasource_id: str) -> int:
    digest = hashlib.blake2b(str(datasource_id).encode("utf-8") + _STAGGER_SEED, digest_size=8).digest()
    return int.from_bytes(digest, "big") % _STAGGER_WINDOW_MINUTES


def staggered_minute_hour(datasource_id, minute, hour, day_of_month, month, day_of_week):
    if (day_of_month, month, day_of_week) != ("*", "*", "*"):
        return minute, hour
    if not minute.isdigit() or not hour.isdigit():
        return minute, hour
    total = (int(hour) * 60 + int(minute) + _offset_minutes(datasource_id)) % 1440
    return str(total % 60), str(total // 60)
```

Follow `job_lock.datasource_lock_key`'s precedent (BLAKE2b, not `hash()`) and document the mod-1440 day-wrap and wildcard-only guards in the module docstring.

Test-first: yes — see Step 1.

- [ ] **Step 4: Run tests, confirm pass**

Run: `poetry run pytest tests/codemie/triggers/test_cron_stagger.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/triggers/cron_stagger.py tests/codemie/triggers/test_cron_stagger.py
git commit -m "EPMCDME-16236: add deterministic per-datasource cron stagger module"
```

---

### Task 2: Wire stagger into `cron.py`'s trigger construction

**Files:**
- Modify: `src/codemie/triggers/bindings/cron.py:436-441` (`__actualize_jobs`'s call into `__actualize_cron_job` — already passes `resource_id`, no change needed here beyond confirming it's present)
- Modify: `src/codemie/triggers/bindings/cron.py:568-588` (`__actualize_cron_job` — pass `resource_id` through to `self.__create_cron_trigger(cron_expression, resource_id, timezone=timezone)`)
- Modify: `src/codemie/triggers/bindings/cron.py:695-713` (`__create_cron_trigger` — add `resource_id` parameter; after splitting `cron_expression` and before constructing `CronTrigger`, call `cron_stagger.staggered_minute_hour(resource_id, minute, hour, day_of_month, month, day_of_week)` and use the returned `minute, hour`)
- Add import: `from codemie.triggers.cron_stagger import staggered_minute_hour` near the top of `cron.py`.
- Test: `tests/codemie/triggers/bindings/test_cron.py` (new test added, existing tests unmodified)

**Interfaces:**
- Consumes: `staggered_minute_hour(datasource_id, minute, hour, day_of_month, month, day_of_week) -> tuple[str, str]` from Task 1.

- [ ] **Step 1: Write a failing test**

Add to `tests/codemie/triggers/bindings/test_cron.py` a test that calls `Cron._Cron__create_cron_trigger("0 0 * * *", resource_id="ds-stagger-1", timezone="UTC")` (name-mangled private static call, matching how other tests in this file reach name-mangled members) and asserts the resulting `CronTrigger`'s computed minute/hour differ from `"0","0"` for at least one of a handful of sample ids, while asserting that `"0 0 15 * *"` (non-wildcard day-of-month) is untouched for the same id.

Test-first: yes — fails because `__create_cron_trigger` does not yet accept `resource_id`.

- [ ] **Step 2: Run test, confirm failure**

Run: `poetry run pytest tests/codemie/triggers/bindings/test_cron.py -v`
Expected: FAIL with `TypeError: __create_cron_trigger() got an unexpected keyword argument 'resource_id'`.

- [ ] **Step 3: Implement the wiring**

At `cron.py:588`, change the call to `self.__create_cron_trigger(cron_expression, resource_id, timezone=timezone)`. At `cron.py:696`, change the signature to `__create_cron_trigger(cron_expression, resource_id, timezone: Optional[str] = None)` and, after the `cron_expression.split()` and `__normalize_day_of_week` call, insert `minute, hour = staggered_minute_hour(resource_id, minute, hour, day_of_month, month, day_of_week)` before building `CronTrigger`.

Test-first: yes — see Step 1.

- [ ] **Step 4: Run tests, confirm pass**

Run: `poetry run pytest tests/codemie/triggers/bindings/test_cron.py -v`
Expected: PASS, including pre-existing tests in the file (they don't pass `resource_id` as a kwarg mismatch since the call site inside `__actualize_cron_job` is the only internal caller; confirm no other internal caller of `__create_cron_trigger` exists).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/triggers/bindings/cron.py tests/codemie/triggers/bindings/test_cron.py
git commit -m "EPMCDME-16236: apply per-datasource stagger to cron trigger construction"
```

---

### Task 3: Wire stagger into `scheduler_settings_service.py`'s `nextRunAt`

**Files:**
- Modify: `src/codemie/service/settings/scheduler_settings_service.py:385-441` (`_build_scheduler_item` — after reading `cron_expr` and `resource_id` at lines 396/399, before the `croniter` block at lines 411-422, compute the staggered cron string and feed that to `croniter` instead of the raw `cron_expr`)
- Add import: `from codemie.triggers.cron_stagger import staggered_minute_hour`
- Test: `tests/codemie/rest_api/routers/test_settings_cron_validation.py` or a new focused unit test file `tests/codemie/service/settings/test_scheduler_settings_service_stagger.py` (create if no existing unit test file targets `_build_scheduler_item` directly)

**Interfaces:**
- Consumes: `staggered_minute_hour(...)` from Task 1, same signature as Task 2 uses.

- [ ] **Step 1: Write a failing test**

```python
from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

def test_next_run_at_uses_staggered_time(make_setting):
    setting = make_setting(schedule="0 0 * * *", timezone="UTC", resource_id="ds-stagger-1")
    item = SchedulerSettingsService._build_scheduler_item(setting)
    from codemie.triggers.cron_stagger import staggered_minute_hour
    minute, hour = staggered_minute_hour("ds-stagger-1", "0", "0", "*", "*", "*")
    assert item.schedule.nextRunAt is not None
    # the computed next run's hour/minute match the staggered values, not the raw "0 0"
    assert f"T{int(hour):02d}:{int(minute):02d}" in item.schedule.nextRunAt
```

Use whatever existing `Settings`-row test fixture/builder `test_settings_cron_validation.py` already uses in place of `make_setting`.

Test-first: yes — fails today because `_build_scheduler_item` computes `next_run_at` from the raw, unstaggered `cron_expr`.

- [ ] **Step 2: Run test, confirm failure**

Run: `poetry run pytest tests/codemie/service/settings/test_scheduler_settings_service_stagger.py -v`
Expected: FAIL (assertion mismatch, raw `0 0` next-run time).

- [ ] **Step 3: Implement the wiring**

In `_build_scheduler_item`, after line 399 (`resource_id = ...`), split `cron_expr` into its 5 fields, call `staggered_minute_hour(resource_id, minute, hour, day_of_month, month, day_of_week)`, and rebuild a staggered cron string used only for the `croniter(...)` call at line 419 — keep `description` (line 403-409) built from the original unstaggered `cron_expr` so the human-readable text still matches what the user configured, per the design's "stored cron is never rewritten" rule (only the computed display value changes, not the stored string or the prettified description of the user's setting).

Test-first: yes — see Step 1.

- [ ] **Step 4: Run tests, confirm pass**

Run: `poetry run pytest tests/codemie/service/settings/test_scheduler_settings_service_stagger.py tests/codemie/rest_api/routers/test_settings_cron_validation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/settings/scheduler_settings_service.py tests/codemie/service/settings/test_scheduler_settings_service_stagger.py
git commit -m "EPMCDME-16236: align scheduler nextRunAt with staggered cron trigger"
```
