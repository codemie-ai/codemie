# Technical Research

**Task**: trigger engine cron scheduling stagger
**Generated**: 2026-10-27T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Add staggered start times for datasource scheduled reindexing to prevent queue-timeout failures when many datasources fire at the same cron time.

Reuse the EXISTING scheduler options exactly as they are today ('Daily at midnight' and 'Custom cron expression' / arbitrary cron) — do not add a new schedule-type/preset field, do not change the Settings schema, do not change what the user configures. Instead, apply a deterministic per-datasource stagger offset purely at the point where the backend turns a schedule's cron expression into the actual APScheduler CronTrigger, so that multiple datasources sharing the same configured cron (e.g. all set to 'Daily at midnight') do not all fire at the exact same instant.

Confirmed from prior codebase research: the trigger engine (src/codemie/triggers/bindings/cron.py) polls Settings rows every 10s (__watch_settings) and registers an APScheduler CronTrigger per schedule (__actualize_cron_job -> __create_cron_trigger), parsed directly from the raw 5-field cron string + timezone stored on the Settings row. APScheduler fires jobs at the exact computed time using a shared thread-pool executor sized by CRON_SCHEDULER_MAX_WORKERS. When many datasources share the same cron, they fire simultaneously, competing for that pool and for datasource_concurrency_manager's capacity, producing 'Datasource indexing queue timeout exceeded' failures. The code deliberately avoids APScheduler's own jitter option (comment in __create_cron_trigger explains CronTrigger's jitter can silently skip runs) — so stagger must be a deterministic, pre-computed offset applied once per datasource, not scheduler-level jitter.

There is an existing precedent: src/codemie/triggers/job_lock.py's datasource_lock_key() uses BLAKE2b on the datasource id to derive a stable Postgres advisory-lock key (two-part, each half of an 8-byte BLAKE2b digest). The new staggering logic should follow the same spirit (deterministic hash of datasource id + a random seed, so the resulting distribution isn't trivially predictable, but is stable across restarts and re-evaluations for the same datasource) and must be isolated in ONE new module.

Acceptance criteria:
- No new user-facing scheduling option, no Settings schema change. Stored cron expression and timezone are not rewritten in the database.
- A single new module encapsulates all staggering logic. No staggering logic duplicated or spread into cron.py's job-building code beyond calling into this module.
- Stagger offset deterministic/stable per datasource across repeated 10s polls and process restarts.
- Multiple datasources sharing the same base cron must not all fire at the same real-world instant.
- Each datasource still fires exactly once per its configured cron cadence — no skipped or duplicate fires.
- Next/effective fire time stays inspectable (ScheduleInfo/describe mechanisms or logs).
- Does not attempt to fix datasource_concurrency_manager's own capacity — root-cause confirmation out of scope.
- Existing cron validation / scheduler CRUD / trigger dispatch tests must not break; new stagger module needs its own tests.

This supersedes and must NOT reuse an earlier abandoned 'daily_overnight_random' schedule_type field approach (it added a new field threaded through 8 router files) — that was reverted; do not reference it as precedent except to note it's rejected.

Please research specifically: (1) the exact mechanics of __create_cron_trigger and how a per-datasource offset could be applied to the resulting CronTrigger's minute/hour fields or fire-time computation without breaking APScheduler's cadence semantics (e.g. does shifting minute/hour risk crossing into the next day boundary for 'Daily at midnight' schedules, and how should that be handled); (2) where __actualize_cron_job currently passes resource_id to __create_cron_trigger (it currently does not take resource_id — identify exactly what would need to change in the call chain from __actualize_jobs -> __actualize_cron_job -> __create_cron_trigger to thread resource_id through); (3) how ScheduleInfo/the describe mechanism currently computes next-run-time, to confirm it would stay accurate for a staggered trigger; (4) existing tests for cron.py / job_lock.py to use as a pattern for the new module's tests.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/triggers/bindings/cron.py` — `Cron` class, the trigger engine core.
  - `__watch_settings` (async, runs every 10s via `self.scheduler.add_job(self.__watch_settings, "interval", seconds=10, ...)`, line ~271/345) fetches all `SCHEDULER` `Settings` rows and calls `remove_jobs_for_deleted_settings` then `__actualize_jobs`.
  - `__actualize_jobs(settings)` (line 427) loops over settings, calls `__valid_setting(setting)` to get a dict of resolved fields, then calls `__actualize_cron_job(cron_expression=..., resource_id=valid_setting.get("resource_id"), ..., job_id=setting.id, ...)`. **`resource_id` is already available here** as `valid_setting.get("resource_id")` and is already passed into `__actualize_cron_job`.
  - `__actualize_cron_job(self, cron_expression, resource_id, resource_type, job_id, is_enabled, user_id, ...)` (line 568) — receives `resource_id` as a parameter already, but does **not** forward it to `__create_cron_trigger`: `cron_trigger = self.__create_cron_trigger(cron_expression, timezone=timezone)` (line 588) only passes `cron_expression` and `timezone`. `resource_id` is used later only inside `__schedule_job_by_type` / `__schedule_datasource_job` (job payload construction), not in trigger construction.
  - `__create_cron_trigger(cron_expression, timezone=None)` is a `@staticmethod` (line 695-713): splits the raw 5-field cron string, normalizes `day_of_week` via `__normalize_day_of_week`, and constructs `apscheduler.triggers.cron.CronTrigger(minute=minute, hour=hour, day=day_of_month, month=month, day_of_week=day_of_week, timezone=timezone or config.TIMEZONE)`. Its docstring explicitly states jobs must fire "at exactly the time the user asked for" and explains why `CronTrigger`'s built-in `jitter` is rejected (it "picks the next slot after the previous jittered run," so "a window wider than the schedule period silently skips runs").
  - Job registration for datasources happens through `__schedule_job_by_type` → `__schedule_datasource_job` / `__schedule_knowledge_base_job`, all of which call `self.scheduler.add_job(..., trigger=cron_trigger, id=job_id, replace_existing=True, ...)` — the `cron_trigger` object built by `__create_cron_trigger` is passed through unchanged to every `add_job` call across all resource types (assistant, workflow, datasource/svn/code/jira/confluence/git_faq/google/azure_devops_wiki/azure_devops_work_item/xwiki/xray/sharepoint).
  - `job_defaults` on the `AsyncIOScheduler` (line 253-263): `misfire_grace_time=None`, `coalesce=True`, `max_instances=1` — relevant to "fires exactly once" guarantees; a stagger implementation must not interact badly with coalescing.
  - The thread pool is `APSThreadPoolExecutor(max_workers=config.CRON_SCHEDULER_MAX_WORKERS)` (line 250) — the shared resource that staggering is meant to relieve contention on.

- `src/codemie/triggers/job_lock.py` — `datasource_lock_key(datasource_id)` (line 63-82): the explicitly-cited precedent. Uses `hashlib.blake2b(str(datasource_id).encode("utf-8"), digest_size=8).digest()`, split into two 4-byte big-endian signed ints for PostgreSQL's two-argument advisory lock form. Docstring explains why Python's built-in `hash()` is rejected (`hash()` is salted per process — "two pods would derive different keys for the same datasource and never contend"), which is the same reasoning the task gives for why the new stagger hash must be stable across restarts.

- `src/codemie/service/settings/scheduler_settings_service.py` — `SchedulerSettingsService`, the service layer for scheduler CRUD (`handle_schedule`, `create_or_update_schedule`, `delete_schedule`, `patch_is_enabled`, `list_schedulers`). Also defines:
  - `validate_cron_expression(cron_expr)` (line 723) — validates with `croniter` then re-validates the same 5-field split through `CronTrigger(minute=..., hour=..., day=..., month=..., day_of_week=...)` directly (no timezone, no stagger) and enforces a minimum-hourly-frequency rule via `_validate_minimum_hourly_frequency`.
  - `_build_scheduler_item(setting, last_run=None)` (line 385-441) — this is the "describe" mechanism referenced in the task. It computes `next_run_at` independently of `cron.py`: it reads the raw `schedule` and `timezone` credential values directly off the `Settings` row and does `croniter(cron_expr, datetime.now(tz)).get_next(datetime)` — this is a **separate code path** from `__create_cron_trigger`, using `croniter` rather than `CronTrigger`. It also builds a human-readable `description` via `get_pretty_cron.prettify_cron(cron_expr)`. **This means any stagger applied only inside `cron.py`'s `__create_cron_trigger` will NOT be reflected in `ScheduleInfo.nextRunAt`/`description` unless this function is also updated to call into the same stagger module** — today these two computations (actual trigger vs. displayed next-run) are already independent, so this is a pre-existing seam, not something the task's design introduces.
  - No `resource_id`/stagger awareness anywhere in this service today.

- `src/codemie/rest_api/models/settings.py` — `Settings` model (table, line 434) and `SettingsBase` (line 253) with `credential_values: List[CredentialValues]` (JSONB-backed list of key/value pairs) and a `credential(key)` helper. No `schedule_type` or preset field exists; `schedule` (raw cron string) and `timezone` are plain string-valued credential entries. Confirms the task's "do not change the Settings schema" constraint is already satisfied by current structure — stored data is just `schedule` + optional `timezone` + `resource_id` + `resource_type` + `is_enabled`.

### Architecture and Layers Affected

- **Trigger/scheduler binding layer**: `codemie/triggers/bindings/cron.py` — the `Cron` class is the sole place that turns a `Settings` row into a live APScheduler job. This is where the task says the stagger must be applied (`__create_cron_trigger`, and the call chain `__actualize_jobs` → `__actualize_cron_job` → `__create_cron_trigger`).
- **Service layer**: `codemie/service/settings/scheduler_settings_service.py` — owns CRUD and the describe/list mechanism (`_build_scheduler_item`, `list_schedulers`, `validate_cron_expression`). Not required to change for core staggering, but its `nextRunAt` computation is the other place "inspectable next/effective fire time" (an acceptance criterion) would need to agree with the actual staggered trigger.
- **New module layer** (to be created): task requires a single new module for stagger logic, analogous in spirit to `job_lock.py`'s `datasource_lock_key`.
- **Locking layer**: `codemie/triggers/job_lock.py` / `codemie/utils/leader_lock.py` — unaffected by this task but is the direct pattern precedent for deterministic per-datasource hashing.

### Integration Points

- `cron.py` imports from: `codemie.configs` (`logger`, `config` — specifically `config.CRON_SCHEDULER_MAX_WORKERS`, `config.TIMEZONE`, `config.SCHEDULER_PROMPT_SIZE_LIMIT`), `codemie.core.models` (`GitRepo`, `SVNRepo`), `codemie.rest_api.models.index.IndexInfo`, `codemie.rest_api.models.settings.Settings`, `codemie.service.constants.FullDatasourceTypes`, `codemie.triggers.actors.*` (all the `reindex_*` functions, `invoke_assistant`, `invoke_workflow`, `resume_stale_datasource`), `codemie.triggers.bindings.cache_manager.CacheManager`, `codemie.triggers.bindings.utils` (`resolve_trigger_user`, `validate_assistant`, `validate_datasource`).
- `apscheduler.triggers.cron.CronTrigger` is the direct third-party object being constructed; a stagger module would need to either (a) adjust the cron fields before constructing `CronTrigger`, or (b) wrap/subclass `CronTrigger` to shift its computed `get_next_fire_time`.
- `croniter` is used both in `cron.py` (`__valid_schedule`) and independently in `scheduler_settings_service.py` (`validate_cron_expression`, `_build_scheduler_item`) — two separate libraries (`CronTrigger` vs `croniter`) are used for parsing/validating the same cron string in different files today.
- No code currently imports `job_lock.py`'s hashing helper from `cron.py` or vice versa; they are sibling modules under `codemie/triggers/`.

### Patterns and Conventions

- **Deterministic hashing pattern** (`job_lock.datasource_lock_key`): `hashlib.blake2b(str(id).encode("utf-8"), digest_size=N).digest()`, then slice/convert digest bytes into integer(s) via `int.from_bytes`. Explicitly avoids Python's salted `hash()`. This is the directly-named precedent for the new stagger module.
- **Static-method helpers on `Cron`**: `__create_cron_trigger`, `__normalize_day_of_week`, `__weekday_number`, `__expand_weekday_term`, `__valid_schedule`, `__get_settings` are all `@staticmethod` on the `Cron` class, name-mangled private (`__`) methods — the established convention for cron-field-manipulation helpers in this file.
- **Call-chain threading of `resource_id`**: `resource_id` is already available as a parameter at every level of the chain the task asks about (`__actualize_jobs` has it via `valid_setting.get("resource_id")`, passes it into `__actualize_cron_job`, which receives it as a parameter) — it is simply not yet passed into `__create_cron_trigger`'s call. Threading it through is a matter of adding `resource_id` as a parameter to `__create_cron_trigger`'s signature and passing `resource_id` at the one call site (line 588).
- **Explicit rejection of APScheduler's own `jitter`**: documented in the `__create_cron_trigger` docstring; any new design must not reintroduce `jitter=`.
- **Day-of-week / weekday normalization**: `__normalize_day_of_week` shows the established way this file reasons about crontab field semantics it must not silently get wrong (0=Sunday vs 0=Monday mismatch between crontab and APScheduler) — relevant precedent for reasoning carefully about minute/hour field shifts crossing day boundaries.

---

## 3. Documentation Findings

### Guides and Architecture Docs

No `.ai-run/guides/` file is dedicated to the trigger/scheduler/cron subsystem. Checked `.ai-run/guides/workflows/langgraph-workflows.md` (LangGraph workflow execution, not APScheduler cron) and `.ai-run/guides/integration/*` (external service integrations — Jira, Confluence, X-ray, etc., not scheduling) — neither covers cron/trigger scheduling. `.ai-run/guides/architecture/*` and `.ai-run/guides/data/*` cover layered architecture and SQLModel/repository patterns generally but do not mention the trigger engine specifically. No guide found that covers `cron.py`, `job_lock.py`, or `scheduler_settings_service.py` directly.

### Architectural Decisions

- Inline docstring in `__create_cron_trigger` (cron.py lines 696-703) is itself a recorded decision: "Jobs fire at exactly the time the user asked for. Bursts of schedules sharing one expression are absorbed by the executor queue, not by spreading the fire times: CronTrigger's jitter picks the next slot after the previous jittered run, so a window wider than the schedule period silently skips runs." — this decision predates the current task and is exactly what the task is now revisiting (moving from "absorbed by the executor queue" to "spread via deterministic offset").
- `job_lock.py` module docstring records the advisory-lock design decision (session-level PG lock vs. a lease/heartbeat scheme) and why `hash()` was rejected in favor of BLAKE2b — cited directly by the task as the pattern to follow.
- `job_defaults` comments in `cron.py` (`misfire_grace_time=None`, `coalesce=True`, `max_instances=1`) record decisions about late-running vs. skipped jobs that any stagger design must stay compatible with.
- No ADR documents or changelog entries found specifically about staggering or about the earlier `daily_overnight_random` attempt; `git log --all` found no commits mentioning "overnight" or "stagger", and no occurrences of `daily_overnight_random` or `schedule_type` exist anywhere in the current tree — consistent with the task's statement that it was fully reverted.

### Derived Conventions

- New small, single-purpose modules under `codemie/triggers/` (e.g. `job_lock.py`) are written as a handful of free functions/decorators plus a module-level docstring explaining the design rationale, imported directly by `cron.py` and by the reindex actors, each with its own `tests/codemie/triggers/test_<module>.py`.
- Private helper methods on `Cron` are double-underscore-prefixed static/instance methods; cron-field manipulation logic is kept in small composable static methods rather than inline in `__create_cron_trigger`.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/triggers/bindings/test_cron.py` — exercises job-adding behavior for assistant/workflow/datasource jobs by mocking `CronTrigger` and `scheduler.add_job`; largely re-implements the cron-splitting logic inline in the test rather than calling `Cron.__create_cron_trigger` (private/name-mangled method), so it tests behavior at the `scheduler.add_job` boundary, not the trigger-construction internals directly.
- `tests/codemie/triggers/bindings/test_cron_prompt_extension.py` — not read in full; name suggests prompt-handling coverage for cron jobs, not trigger timing.
- `tests/codemie/rest_api/routers/test_settings_cron_validation.py` — covers cron validation at the router/API boundary (likely exercises `validate_cron_expression`/`validate_timezone_string` from `scheduler_settings_service.py`).
- `tests/codemie/triggers/test_job_lock.py` — the directly-relevant pattern precedent: covers key-derivation properties (`test_lock_key_halves_fit_in_int4`, `test_lock_key_is_stable_across_processes` — run via a real subprocess to prove stability independent of `PYTHONHASHSEED`/process salting, `test_different_datasources_get_different_keys`), decorator behavior (lock acquired/held-elsewhere/raises), and a retrieval-availability guard. This is an explicit, usable template for the new stagger module's test file: property-style tests on the deterministic function (bounds, stability across process boundary, distinctness across ids) plus behavioral tests on however the stagger value is consumed.

### Testing Framework and Patterns

- `pytest` (see `pytest.ini` at repo root). Patterns observed: `unittest.mock.patch`/`MagicMock` for scheduler/engine mocking, `@contextmanager`-based test helpers (`_patched_lock`, `_retrieval`, `_mocked_session` in `test_job_lock.py`) to swap real dependencies, a real `subprocess.run` invocation to prove per-process stability (no `PYTHONHASHSEED` dependency), and docstrings on nearly every test explaining the specific regression or acceptance criterion ("AC: ...", "CR-00N: ...") it guards — a strong convention of traceability back to a decision or ticket.

### Coverage Gaps

- No existing test exercises `Cron.__create_cron_trigger` directly (it is private/static and tests currently reconstruct `CronTrigger` inline rather than calling it) — a stagger change inside this method has no existing unit test to regress against; new tests will need to either make the method more directly testable or test it via its effect on `scheduler.add_job`'s `trigger` argument.
- No existing test covers `__actualize_cron_job`'s exact parameter-passing to `__create_cron_trigger` — the chain the task asks about threading `resource_id` through is untested today.
- No existing test covers `scheduler_settings_service._build_scheduler_item`'s `next_run_at`/`croniter` computation staying consistent with whatever `cron.py` actually schedules — this is a pre-existing gap the task's "next/effective fire time stays inspectable" criterion will need to confront.
- No test exists yet for distribution/spread properties of multiple datasources sharing one cron expression (the actual bug this task fixes) — entirely new territory.

---

## 5. Configuration and Environment

### Environment Variables

- `CRON_SCHEDULER_MAX_WORKERS` (int, default `20`, in `src/codemie/configs/config.py` line 386) — sizes the `APSThreadPoolExecutor` used by the `AsyncIOScheduler`'s `"default"` executor; also sizes the dedicated PostgreSQL engine pool in `job_lock.py`'s `_get_lock_engine` (`pool_size=config.CRON_SCHEDULER_MAX_WORKERS`). This is the capacity constraint the queue-timeout symptom is about; the task explicitly scopes fixing its capacity out of scope.
- `STALE_INDEXING_WATCHDOG_ENABLED` (bool, default `False`) — toggles the separate stale-indexing watchdog interval job; unrelated to stagger but lives in the same `Cron.start_async`.
- `SCHEDULER_PROMPT_SIZE_LIMIT` (int, default `4000`) — unrelated to timing, used by `__sanitize_prompt`.
- `TIMEZONE` (referenced as `config.TIMEZONE`, used as the fallback when a `Settings` row has no `timezone` credential) — relevant because any stagger computation that reasons about day boundaries must do so in the same per-schedule timezone `__create_cron_trigger` already uses, not a fixed timezone.

### Configuration Files

- No dedicated scheduler/cron config file — all relevant settings are environment-driven `config.*` attributes in `src/codemie/configs/config.py`, and per-schedule configuration (`schedule`, `timezone`, `resource_id`, `is_enabled`) is stored as `credential_values` rows on the `Settings` SQLModel table, not in any static config file.

### Feature Flags and Deployment Concerns

No feature flag currently gates cron scheduling behavior (`STALE_INDEXING_WATCHDOG_ENABLED` is the only boolean toggle in this area and is unrelated). No Dockerfile/CI reference to cron stagger was found (none exists yet, as expected for a not-yet-built feature).

---

## 6. Risk Indicators

- Speculative: Shifting `minute`/`hour` fields on a per-datasource basis for a "Daily at midnight" cron (`0 0 * * *`) risks the stagger offset crossing into the previous/next calendar day (e.g. offset of +90 minutes on `0 0 * * *` becomes `30 1 * * *`, fine; but an offset applied as a *negative* minute shift, or a large offset near a day-of-month/day-of-week boundary, could change which day the job logically falls on if day/month/day_of_week fields are not simultaneously recomputed). This needs explicit handling in the new module's design — e.g. operate on an absolute time-of-day computed via `croniter`/datetime arithmetic rather than naively adding to the raw `hour`/`minute` string fields, or clamp offsets within a bounded window that cannot cross a day boundary.
- The "next run / describe" mechanism (`SchedulerSettingsService._build_scheduler_item`) computes `nextRunAt` via `croniter` independently of `cron.py`'s `CronTrigger` construction. These two code paths are already decoupled today; a stagger applied only in `cron.py` will silently desynchronize the UI's displayed "next run" from the actual fire time unless the new stagger module is also consulted from `_build_scheduler_item` (or equivalent describe/logging path). This is a discovered architectural seam, not a proposed requirement.
- `__create_cron_trigger` is a `@staticmethod` with no access to `self` state; threading `resource_id` through to it is mechanically simple (one new parameter, one new call-site argument at `__actualize_cron_job` line 588) but the exact fire-time semantics (whether `CronTrigger` is constructed with already-shifted field strings, or wrapped so `get_next_fire_time` is shifted post-construction) is an open design question with real correctness consequences for "fires exactly once" and "no skipped fires" under `coalesce=True, max_instances=1, misfire_grace_time=None`.
- No existing test harness directly unit-tests `__create_cron_trigger` or `__actualize_cron_job`'s parameter wiring; the private/name-mangled method naming makes direct testing from outside the class awkward (tests today reconstruct `CronTrigger` inline instead), which the new work will need to either accommodate or refactor around.
- `apscheduler` (`^3.10.4`) and `croniter` (`^3.0.3`) are both pinned dependencies already in use; no version constraint issue found, but two different cron-parsing libraries already coexist in this codebase for related purposes (`CronTrigger` field validation vs. `croniter` next-run computation), which is a pre-existing inconsistency the new module should be careful not to compound with a third interpretation.
- No guide document exists for this subsystem, so conventions must be derived entirely from source reading; a reviewer unfamiliar with `cron.py` will have no guide to check design choices against.

---

## 7. Summary for Complexity Assessment

The task touches a single, well-isolated layer: the trigger-engine binding (`codemie/triggers/bindings/cron.py`), specifically the `__actualize_jobs` → `__actualize_cron_job` → `__create_cron_trigger` chain, plus a brand-new module for the deterministic stagger computation that this chain will call into. `resource_id` is already threaded as far as `__actualize_cron_job`; the only new plumbing is passing it one level deeper into `__create_cron_trigger` and deciding how the offset perturbs the `CronTrigger`'s fields or fire-time computation. A secondary, smaller touch point is `SchedulerSettingsService._build_scheduler_item` in the service layer, whose independent `croniter`-based `nextRunAt` computation will go stale relative to the actual staggered trigger unless it also calls into the new module — this is a real architectural seam discovered during research, not a given requirement.

Technical novelty is moderate: the task gives a close, explicit precedent (`job_lock.datasource_lock_key`'s BLAKE2b-based deterministic hashing) to adapt, and the constraint of using a single new module with no schema change bounds the design space considerably. The main genuinely novel work is correctly composing an offset with crontab's minute/hour/day/month/day_of_week semantics without breaking cadence (day-boundary crossing is the sharpest edge) and deciding whether the offset is applied to the `CronTrigger` construction inputs or wraps its fire-time output — both plausible, with different risk profiles for the "fires exactly once, never skipped" guarantee under `coalesce=True`/`max_instances=1`.

Test coverage posture is mixed: there is a strong, directly reusable pattern in `tests/codemie/triggers/test_job_lock.py` (property tests on a deterministic hash: bounds, cross-process stability, distinctness) that transfers almost directly to testing the new stagger function. However, no existing test exercises `__create_cron_trigger` or the `__actualize_cron_job` parameter chain directly (current `test_cron.py` reconstructs `CronTrigger` inline rather than calling the private method), and nothing tests the distribution/non-collision property across datasources sharing a cron, or the consistency between `cron.py`'s actual schedule and `scheduler_settings_service.py`'s displayed `nextRunAt` — both are net-new test surface this task must build from scratch.

---

## 8. External References

None named by the task. The task's cited files (`src/codemie/triggers/bindings/cron.py`, `src/codemie/triggers/job_lock.py`) are internal to this repository and are covered in Section 2 above, not external sources outside the codebase.
