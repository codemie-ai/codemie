# Spec: Filter Built-in CLI Command Names from Slash Commands Analytics

**Task:** EPMCDME-15546
**Date:** 2026-10-06
**Size:** S (~1 day, broader scope than initial assessment — 5 files, two read branches + rollup)

---

## Problem

The CLI Analytics "Slash commands" chart counts every `user_prompt` OTel event that carries a non-empty `command_name`, regardless of whether the name belongs to a real user-invoked slash command or a Claude Code lifecycle/configuration built-in. The result is that names such as `model`, `exit`, and `quit` appear in the chart alongside legitimate slash commands like `compact`.

There are two data paths that feed the chart. First, the kind-4 INSERT in `rollups.py` (lines 1590–1593) writes rows to `invocations_hourly` by selecting `user_prompt` log events where `command_name <> ''`; it has no guard against built-in names. Second, `get_invocations` in `reader.py` (lines 418–441) has three UNION ALL branches: branch 2 reads kind-4 rows from `invocations_hourly` for sessions without a summary, and branch 3 reads from `session_dims.commands` for sessions with a summary. Neither read branch currently filters built-in names.

---

## Goal

Exclude a fixed, evidence-backed set of Claude Code built-in command names from every code path that contributes to the "Slash commands" chart, so the chart reflects only real user-invoked slash commands regardless of whether a session has a summary. `compact` must remain counted.

---

## Approach

Two complementary layers of filtering, both driven by a single new vocabulary constant:

**Layer 1 — Rollup-time (prevention for new data).** Add a `NOT IN` predicate to the kind-4 rollup INSERT in `rollups.py`, referencing `BUILTIN_COMMAND_NAMES` via the existing `_text_array` helper. This prevents built-in names from entering `invocations_hourly` for any future rollup slice. This is the primary fix for new OTel rows and follows the identical pattern used by `_PROMPT_OK` / `SENTINEL_EXACT` for first-prompt filtering.

**Layer 2 — Read-time (defence-in-depth for existing and summary data).** Add an exclusion condition to both slash-command branches of `get_invocations` in `reader.py`:
- Branch 2 (`invocations_hourly` kind=4, no-summary path, `reader.py:432–433`): add a `NOT IN` guard on the `name` column.
- Branch 3 (`session_dims.commands` summary path, `reader.py:435–437`): add a `NOT IN` guard on the resolved command string.

This ensures correctness for historical `invocations_hourly` rows that pre-date the rollup fix and for any session-summary data that may include built-in names, without waiting for a backfill run.

### New vocabulary constant — `vocabulary.py`

Add a module-level tuple `BUILTIN_COMMAND_NAMES` near `SENTINEL_EXACT` (lines 139–140 of `vocabulary.py`). Names are stored **without** a leading slash, matching convention 9 of `cli-analytics-events.md`, which governs both the `command_name` column and the `session_dims.commands` array:

```python
BUILTIN_COMMAND_NAMES: tuple[str, ...] = ("model", "exit", "quit")
```

`compact` is deliberately absent — it is a real user-invoked slash command and must remain counted. `clear` and `resume` are not included: they appear in `SENTINEL_EXACT` for a different purpose (first-prompt detection) but there is no ticket or repository evidence that they are unwanted in the invocations metric.

Note: `SENTINEL_EXACT` stores names **with** a leading slash (`/clear`, `/resume`, etc.) and is not modified by this change.

### Rollup SQL change — `rollups.py`

In the kind-4 INSERT (lines 1590–1593), add an exclusion clause to the WHERE predicate consuming `BUILTIN_COMMAND_NAMES` through the existing `_text_array` helper. The exact SQL form should match the convention already used by `_PROMPT_OK` in the same file. `_text_array` converts the Python tuple to a SQL `ARRAY[...]` literal at module import time.

### Reader SQL changes — `reader.py`

In `get_invocations` (lines 428–440), add exclusion conditions to both slash-command branches:

- Branch 2: extend the WHERE clause on the `invocations_hourly` kind=4 branch to exclude rows where `name` is in `BUILTIN_COMMAND_NAMES`.
- Branch 3: extend the WHERE clause on the `session_dims.commands` branch to exclude resolved command strings that are in `BUILTIN_COMMAND_NAMES`.

Both conditions reference the same constant, ensuring the exclusion set is defined in one place. The SQL form should be consistent with the rollup-layer exclusion pattern. `ports.py` is not modified; `get_invocations` signature and return type are unchanged.

---

## Acceptance Criteria

1. `vocabulary.py` exports `BUILTIN_COMMAND_NAMES` as a tuple containing exactly `model`, `exit`, and `quit`, all without leading slashes. `compact` is not present in the tuple.
2. The kind-4 rollup INSERT in `rollups.py` includes an exclusion predicate that references `BUILTIN_COMMAND_NAMES` (or its `_text_array` expansion), verified by a new test in `test_rollups.py` that inspects the SQL string.
3. A new test in `test_rollups.py` asserts that `model`, `exit`, and `quit` appear in the exclusion predicate; a complementary assertion confirms `compact` is absent from `BUILTIN_COMMAND_NAMES` (not excluded).
4. A new test in `test_reader.py` uses `_invocation_branches()` to assert that the kind-4 `invocations_hourly` branch (branch 2) carries the built-in exclusion condition.
5. A new test in `test_reader.py` uses `_invocation_branches()` to assert that the `session_dims.commands` summary branch (branch 3) carries the built-in exclusion condition.
6. Both `test_reader.py` AC4 and AC5 tests confirm `compact` is not excluded (the branch SQL does not filter it out).
7. All existing tests in `test_rollups.py` and `test_reader.py` continue to pass without modification.
8. `ports.py` and all UI layers are unchanged; the `get_invocations` interface signature does not change.

---

## Files in Scope

| File | Change |
|---|---|
| `src/codemie/repository/cli_analytics/vocabulary.py` | Add `BUILTIN_COMMAND_NAMES` tuple |
| `src/codemie/repository/cli_analytics/postgres/rollups.py` | Add exclusion predicate to kind-4 INSERT |
| `src/codemie/repository/cli_analytics/postgres/reader.py` | Add exclusion conditions to branches 2 and 3 of `get_invocations` |
| `tests/codemie/repository/cli_analytics/postgres/test_rollups.py` | New SQL-structure tests for rollup exclusion |
| `tests/codemie/repository/cli_analytics/postgres/test_reader.py` | New SQL-structure tests for both reader branches |

---

## Non-Goals

- **No backfill code.** The read-layer fix in `reader.py` handles historical rows immediately. Authoring or scheduling a backfill run is out of scope; the existing `backfill.py` mechanism remains available for operators who want to clean `invocations_hourly` for storage efficiency.
- **No ports.py change.** The `CliAnalyticsReader` Protocol and `get_invocations` signature are not altered.
- **No UI change.** The UI renders only what the backend returns; fixing the rollup and the reader is sufficient. This is a backend-only change.
- **No SENTINEL_EXACT modification.** `SENTINEL_EXACT` is used for first-prompt detection with a different naming convention (leading slashes). It is not extended or altered.
- **No runtime configuration.** The built-in name list is a compile-time vocabulary constant, not an environment variable or database-backed table.
- **No `clear` or `resume` exclusion.** There is no ticket or repository evidence these names are unwanted in the invocations metric. They are not added to `BUILTIN_COMMAND_NAMES`.
- **No `compact` exclusion.** `compact` is a real user-invoked slash command and must remain counted in the chart.

---

## Open Risks

- **Historical `invocations_hourly` rows are cleaned at read time, not at storage.** The read-layer fix means the chart is immediately correct, but pre-fix kind-4 rows in `invocations_hourly` still contain built-in names in storage. This is acceptable — they are filtered on read — but operators may run a backfill for storage hygiene. The reach of any backfill is bounded by `CLI_ANALYTICS_RAW_RETENTION_DAYS` (default 90 days).
- **Future built-ins.** New Claude Code lifecycle commands introduced after this fix will appear in the chart until `BUILTIN_COMMAND_NAMES` is updated. There is no runtime configuration path; a code change is required.
- **Vocabulary constant alignment.** `BUILTIN_COMMAND_NAMES` must use no leading slashes (convention 9), unlike `SENTINEL_EXACT`. Mixing conventions would cause silent over- or under-filtering. The test in AC3 guards against this for the rollup; AC4/AC5 guard it for the reader.
