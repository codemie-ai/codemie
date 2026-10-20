# EPMCDME-16025 CLI Analytics Project Gate (BE) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Store CLI analytics only for projects with `cli_analytics_enabled=true`; expose/modify that flag only per the role matrix.

**Architecture:** New `applications.cli_analytics_enabled` column. Project API gets a viewer-aware response field (omitted for non-authorized viewers) and a 403 write guard. Ingest: router builds an async `gate` closure (service layer: header + user + DB policy) and passes it to the ingestor. The decoder records, while it decodes, every explicit project value and the wire record count into the `DecodedBatch`; the ingestor calls the gate with that set and, on a discard reason, skips `_store` and answers HTTP 200 with a discard body. One decode per request.

**Tech Stack:** FastAPI, SQLModel, Alembic, pydantic v2, pytest(-asyncio).

**Conventions:** Commit per task using the repository's existing convention (ticket prefix `EPMCDME-16025: `); stage explicit paths only; no push. Narrow tests: `poetry run pytest <path>`; `make ruff` at the end of each task. Do not add whole-suite gate tasks.

## Global Constraints

- Field name `cli_analytics_enabled`; reasons `cli_analytics_disabled`, `project_context_required`, `project_context_conflict`; header `X-CodeMie-Project` (`core/constants.py:54` `HEADER_CODEMIE_CLI_PROJECT`).
- Write = `user.is_admin_or_maintainer` only. Read = `is_admin_or_maintainer` OR `user.is_application_admin(project)`. Never `is_applications_admin`, auditor, or `platform_admin` label.
- Never HTTP 403/404/400 for a disabled/missing-context/conflict batch (CLI retries or suspends); discard = HTTP 200.
- Instance flag `cliAnalytics`, auth, size, format validation unchanged.

## Acceptance criteria

1. New and existing projects default to `cli_analytics_enabled=false` (migration server_default false).
2. Global admin/maintainer can set it via create and PATCH; value persists and is returned.
3. Project Admin sees the value only for projects they admin (list, detail, create/PATCH responses); never gets write.
4. Other viewers (member, auditor, `is_applications_admin`-only, outsider) get no `cli_analytics_enabled` key in any project response, including Pydantic defaults.
5. Any request body containing the field (true/false/null/unchanged) without global write returns 403 before any mutation; other fields in that PATCH are not applied.
6. Project Admin can PATCH permitted fields without the field.
7. All four POST endpoints, in NDJSON, OTLP JSON and OTLP protobuf, store data for an enabled project and discard (HTTP 200, no DB writes) for a disabled one; two projects with different settings are isolated.
8. Discard bodies: OTLP `Export*ServiceResponse.partial_success` with the wire-level rejected count (never 0 for a non-empty discarded batch) + reason in `error_message`; NDJSON `{"accepted":0,"dropped":N,"reason":...}` with N = parseable event lines.
9. Missing/unknown/inaccessible project, or any conflicting explicit project value anywhere in the batch (hook, usage event, log/span/metric attribute, resource attribute), is a 200 discard with `project_context_required` / `project_context_conflict`.
10. Policy lookup failure returns 503 with no writes. Disabling applies to the next request; history untouched.
11. Positive ingest fixtures explicitly enable an isolated test project.

## Assumptions (veto at plan approval)

- A1 Order per POST: instance flag, size, content-type, decode (malformed stays 400), then gate. Malformed is never a discard.
- A2 Context = `X-CodeMie-Project` (case-insensitive name lookup via `application_repository.aget_by_name_case_insensitive`). Missing/blank header, unknown or soft-deleted project, or no `user.has_access_to_application(app.name)` -> 200 discard `project_context_required`.
- A3 Explicit project context is collected from ALL sources, at the point where the decoder would otherwise drop it, into a new `DecodedBatch.explicit_projects: set[str]` (stripped, non-empty, via `as_str`/`clean`), and the whole set goes to the gate; no second decode. The only project key the code knows is `codemie_project_name` (`vocabulary.py:99-101` `HOOK_TEXT_FIELDS`; `HookRow.codemie_project_name` `otlp.py:141`; no other project key exists in `vocabulary.py`, `otlp.py` or the test builders, and `SESSION_SCOPED_KEYS` `vocabulary.py:128` has none), so that key is read at every site: (a) NDJSON events in `decode_hook_events` (`otlp.py:844-854`), read from the raw dict before the `try`, so an event skipped on `RecursionError` still counts; covers hook and `agent.usage.request` events (`_hook_event_row` `:857-877`); (b) OTLP log records in `_log_from_record` (`:650`) before `_hook_log` pops anything, covering hook logs (column), `agent.usage.request` logs (no column, key lands in `UsageRow.attrs`, `:572-626`) and plain logs (`LogRow.attrs`, `:656-687`); (c) span attributes (`:721`); (d) every metric data point's attributes (`:801`), including points of skipped kinds and non-finite points; (e) resource attributes in `_resource` (`:513-518`) for logs, traces and metrics. Gate rule: any value differing (case-insensitive) from the header project, or two different values anywhere in the batch -> nothing written, 200 discard `project_context_conflict`. Known limit: a project carried under some other attribute key is not detected; veto if the CLI sends one.
- A4 Discard count = WIRE records the client sent, not rows the decoder keeps: `DecodedBatch.wire_count` is incremented per OTLP log record, per span, per metric data point of every kind (sum, gauge, histogram, exponential_histogram, summary; a metric with no data oneof adds 0, as it has no data points; non-finite points count), and for NDJSON equals `len(events)`. NDJSON: `_parse_ndjson` (`cli_analytics.py:170-185`) silently skips malformed or non-object lines before the ingestor, so they are not counted and do not change today's behaviour; a body with zero parseable events or an empty body stays HTTP 400 (`:367-371`, A1). Histogram/summary points are counted in `rejected_data_points` even though the decoder never stores them (`otlp.py:754-755,:771-773`). Decoder-skipped records still count (hook events skipped on `RecursionError` `:851-853`, non-finite points `:797-798`).
- A5 Policy lookup exception -> `TelemetryStorageUnavailableError` -> 503, nothing written.
- A6 Personal projects stay false: create/PATCH setting true on a personal project -> 400 `ExtendedHTTPException`; `PersonalProjectService` shared->personal conversion (`personal_project_service.py:223-230`) resets the flag to false.
- A7 Alembic `down_revision` = the single real head (candidate `y1z2a3b4c5d6`, nothing references it as a parent; confirm with `alembic heads`).
- A8 Gate is a callable passed to the ingestor (no `codemie.service` import under `repository/cli_analytics`, per `test_layering.py`); one decode per request.

## Task 1: Persistence, migration, repository

**Files:** Modify `src/codemie/core/models.py:396` (add `cli_analytics_enabled: bool = SQLField(default=False, nullable=False)` next to `chargeback_enabled`); modify `src/codemie/repository/application_repository.py:188-232` (`create`) and `:722-756` (`update_project`) to accept `cli_analytics_enabled: bool | None = None`; modify `service/project/personal_project_service.py:223-230` (set `existing_app.cli_analytics_enabled = False` on conversion); create migration in `src/external/alembic/versions/` modelled on `f1g2h3i4j5k6_add_chargeback_enabled_to_applications.py` (Boolean, `server_default=sa.text("false")`, `nullable=False`, downgrade drops). Tests: `tests/codemie/repository/test_application_repository_extended.py`, personal-service test file.

Test-first: yes — repo `update_project(cli_analytics_enabled=True)` persists and `None` leaves it unchanged; shared->personal conversion resets a true flag to false; a single alembic head exists.

- [ ] Write failing tests, implement, run `poetry run pytest` on those files, commit.

## Task 2: Project API permissions and response omission

**Files:** Create `src/codemie/service/project/cli_analytics_policy.py`; modify `rest_api/models/project.py` (:99-116, :160-180, :183-202, :215-248), `rest_api/routers/projects/crud.py` (:170-200 create, :256-281 list, :316-354 detail), `routers/projects/management.py:46-82`, `service/project/project_visibility_service.py:106-121,:186-203` (add the raw column to the dicts), `service/project/project_service.py:132-206,:209-281`.

**Interfaces (Produces):**
```python
# cli_analytics_policy.py
def can_view(user: User, project_name: str) -> bool   # is_admin_or_maintainer or user.is_application_admin(project_name)
def require_write(user: User, fields_set: set[str]) -> None  # 403 ExtendedHTTPException if "cli_analytics_enabled" in fields_set and not user.is_admin_or_maintainer
def visible_value(user: User, project_name: str, value: bool) -> bool | None  # value if can_view else None
```
DTOs: add `cli_analytics_enabled: Optional[bool] = None` to `ProjectListItem`, `ProjectDetailResponse`, `ProjectCreateResponse` via a small mixin whose `@model_serializer(mode="wrap")` drops the key when the value is `None` (so non-viewers never see a default); add the same optional field to `ProjectCreateRequest` and `ProjectUpdateRequest`. In `ProjectUpdateRequest.validate_non_empty` count `"cli_analytics_enabled" in self.model_fields_set` as a provided field so a field-only PATCH reaches the 403 rather than a 422.

Routers: PATCH calls `require_write(user, payload.model_fields_set)` right after `_ensure_user_management_enabled()` and before `project_service.update_project` (field explicitly null by an authorized user = no change); create does the same before `create_shared_project`. List/detail/create/PATCH responses set the field only through `visible_value(...)`; list/detail strip the dict key before `ProjectListItem(**proj)` / `_build_project_detail_response` when the viewer cannot view. Services: `update_project` and `create_shared_project` accept `cli_analytics_enabled: bool | None`, re-check `user.is_admin_or_maintainer` when non-None (defence in depth), and reject `True` on a personal project (A6) with 400; pass the value to the repository.

Test-first: yes — role matrix (global admin, maintainer, project admin of the project, project admin of another, auditor, member, outsider) on list/detail/create/PATCH JSON key presence; PATCH with `true`/`false`/`null`/unchanged by non-writers returns 403 and `application_repository.update_project` is not called; Project Admin PATCH of `description` alone succeeds; personal project `true` rejected.

- [ ] Extend `tests/codemie/rest_api/routers/test_projects_router.py` (add `cli_analytics_enabled` to its `SimpleNamespace` projects, use `getattr` defaults in builders), `test_projects_router_auditor.py`, `tests/codemie/service/project/test_project_service_delete_update.py`, `test_project_visibility_service.py`; add `test_cli_analytics_policy.py`. Implement, run, commit.

## Task 3: Decoder collects explicit projects and wire count

**Files:** Modify `repository/cli_analytics/postgres/otlp.py`: `DecodedBatch` (:236-250) gains `explicit_projects: set[str]` and `wire_count: int = 0`; add helper `_note_project(batch, value)` (`as_str` + `clean`, strip, skip empty, key `PROJECT_KEY = "codemie_project_name"`). Hook sites exactly as A3 (a)-(e). Counting sites: log records loop (:698-700, +1 each), span loop (:719, +1 each), `_decode_metric` (:770-784): before the `sum`/`gauge` filter, for every kind in (`sum`, `gauge`, `histogram`, `exponential_histogram`, `summary`) add `len(data.data_points)` to `wire_count` and note each point's `attributes` project (skipped kinds are not decoded further); in `_append_metric_point` (:796-798) note the project and count before the non-finite early return (avoid counting sum/gauge points twice); `decode_hook_events` (:844-854) `wire_count = len(events)` semantic (+1 per dict event, before the `try`). Tests: `tests/codemie/repository/cli_analytics/postgres/test_otlp*.py` (use the existing builders in `support/otlp_builders.py`, `hook_event_builders.py`; add attribute/resource parameters if they lack them).

Test-first: yes — `decode_otlp` for JSON and protobuf: project value in a resource attribute, log record attribute (plain, hook-with-`event_type`, `agent.usage.request`), span attribute, sum point, gauge point, histogram point, summary point attribute each lands in `explicit_projects`; NDJSON hook and `agent.usage.request` events land there too (incl. one that raises `RecursionError`); mixed values give a set of 2; `wire_count` equals logs sent / spans sent / all points of every metric kind (histogram + summary + NaN sum point + valid gauge point counted, `batch.metrics` holds only the valid ones) / NDJSON events; no project anywhere gives an empty set.

- [ ] Write failing tests, implement, run `poetry run pytest tests/codemie/repository/cli_analytics`, commit.

## Task 4: Ingestor port gate and discard responses

**Files:** Modify `repository/cli_analytics/ports.py:99-111`, `repository/cli_analytics/postgres/ingestor.py:184-195,:248-265`; tests `tests/codemie/repository/cli_analytics/postgres/test_ingestor.py`, `support/contracts.py` if it lists signatures.

```python
# ports.py
IngestGate = Callable[[frozenset[str]], Awaitable[str | None]]  # all explicit project values in the batch -> discard reason or None
# both protocol methods gain: gate: IngestGate | None = None
```
In `ingest_otlp`/`ingest_hook_events`: after decode, if `gate` is set, `reason = await gate(frozenset(batch.explicit_projects))`; on a reason return before `_store` an `IngestResult(accepted=0)` whose body is, for OTLP, `_otlp_message(signal, batch.wire_count)` with `partial_success.error_message = reason` instead of `_REJECTED_MESSAGE` (JSON via `json_format.MessageToJson`, protobuf otherwise), and for hooks `{"accepted":0,"dropped":batch.wire_count,"reason":...}`. If `wire_count` is 0 (nothing was sent, e.g. an empty OTLP request) the OTLP body is the normal empty success response. Gate exceptions propagate (router maps `TelemetryStorageUnavailableError` to 503). `gate=None` behaviour is unchanged.

Test-first: yes — with a stub gate returning a reason, `RecordingConnection` records zero statements and the body carries wire count and reason for logs, metrics, traces, hooks, JSON and protobuf; a metrics batch with only histogram/summary/NaN points still reports a non-zero `rejected_data_points`; a stub gate returning `None` stores as before; the stub receives the full `explicit_projects` set.

- [ ] Write failing tests, implement, run `poetry run pytest tests/codemie/repository/cli_analytics`, commit.

## Task 5: Gate service and router wiring

**Files:** Create `src/codemie/service/analytics/cli_analytics_project_gate.py`; modify `rest_api/routers/cli_analytics.py:155-167,:358-381`; update `RecordingIngestor` (`tests/codemie/rest_api/routers/test_cli_analytics_ingest.py:51-80`) and `FakeIngestor` (`test_cli_analytics_storage_wiring.py:46-63`) to accept and record `gate`.

**Interfaces:** `def build_ingest_gate(user: User, header_project: str | None) -> IngestGate`. The returned coroutine: blank header -> `project_context_required`; any explicit value that differs (case-insensitive, stripped) from the header project, or more than one distinct value -> `project_context_conflict`; then in `get_async_session()` fetch `aget_by_name_case_insensitive`, missing/`deleted_at`/no `user.has_access_to_application(app.name)` -> `project_context_required`; `not app.cli_analytics_enabled` -> `cli_analytics_disabled`; else `None`. Any lookup exception -> raise `TelemetryStorageUnavailableError`.

Router: both handlers read `request.headers.get(HEADER_CODEMIE_CLI_PROJECT)` and pass `gate=build_ingest_gate(request.state.user, header)` after the existing checks.

Test-first: yes — gate unit tests (each reason, case-insensitive header, soft-deleted, non-member vs global admin, enabled vs disabled, one conflicting value among several matching ones, DB error -> 503 error); router tests assert every path forwards a gate and that a failing gate yields 503 not 200.

- [ ] Write failing tests, implement, run router + service tests, commit.

## Task 6: End-to-end ingest matrix with real ingestor

**Files:** Create `tests/codemie/rest_api/routers/test_cli_analytics_project_gate.py` (reuse `FakeEngine`/`RecordingConnection` from `postgres/test_ingestor.py`, moving them to `tests/codemie/repository/cli_analytics/support/` if import is awkward; builders from `support/otlp_builders.py`, `hook_event_builders.py`). Patch only the DB policy lookup with two projects (one enabled, one disabled) and the isolated enabled test project for positives.

Test-first: yes — parametrized over 4 endpoints x (NDJSON, OTLP JSON, OTLP protobuf where applicable): enabled stores (SQL recorded); disabled / missing header / non-member write nothing and return 200 with the documented body and wire count; conflict tests with the conflicting `codemie_project_name` placed in each source other than a hook row: `agent.usage.request` event (NDJSON and OTLP log), OTLP resource attribute (logs, traces, metrics), OTLP log attribute, span attribute, metric data-point attribute (sum, and a skipped histogram point), each in JSON and protobuf, plus a batch mixing two projects: all write nothing and return 200 `project_context_conflict`; a discarded metrics batch of only histogram/summary/NaN points returns a non-zero rejected count; a partly malformed NDJSON body discards with `dropped` = parseable lines, a wholly malformed one is still 400; lookup failure returns 503 with no SQL; malformed OTLP body still 400; instance flag off still 404; two projects in sequence isolated; flipping the flag applies on the next request.

- [ ] Write tests, fix any gaps found, run, commit.

## Task 7: Docs

**Files:** Modify `.ai-run/guides/integration/cli-analytics-events.md` (routes table :9-15: header, gate order, discard bodies, three reason strings, which attributes are checked for conflict (`codemie_project_name` in hook/usage events, log/span/metric/resource attributes), wire-level counting rule incl. histogram/summary/non-finite points and NDJSON parseable lines, 503 meaning, "2xx deletes the CLI spool so discard is final") and `.ai-run/guides/integration/cli-analytics-storage.md` (project policy lives in the main DB `applications.cli_analytics_enabled`, default false rollout impact, no ingest writes for discards, project-API visibility rules).

Test-first: no — doc-only; verify no stale statements about "no project context" remain via Grep.

- [ ] Edit docs, commit.

## Negative-constraint pass

- No 403/404/400 for disabled/missing/conflict: Tasks 4-6 return 200 (A1-A3). Malformed stays 400 (A1, A4).
- No writes (raw, resources, dedup, rollup) for discards: Task 4 returns before `_store`; Task 6 asserts zero SQL.
- No inference of the user's default project: Task 5 uses header only, blank -> `project_context_required`.
- `is_applications_admin`/auditor/`platform_admin` never grant read/write: Global Constraints, Task 2 role matrix.
- Other PATCH fields not applied on the 403: Task 2 router guard precedes the service call.
- No history deletion, no instance-flag change, no mixed project data stored: Tasks 3-6 (a conflict anywhere discards the whole batch).
- No second decode and no empty success for a discarded non-empty batch: Task 3 collects during the single decode; Task 4/6 assert non-zero counts.
