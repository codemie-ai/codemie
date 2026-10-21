# Plan: Filter Built-in CLI Command Names from Slash Commands Analytics

**Task:** EPMCDME-15546  
**Specification:** spec.md (approved)  
**Strategy:** Two complementary filtering layers (rollup-time + read-time) driven by a single vocabulary constant.

---

## Implementation Overview

This plan executes changes across **six files** (3 production + 3 existing test files), using TDD with RED/GREEN checkpoints and proportional regression coverage. No UI, ports, migration, or backfill changes. The rollup fix prevents new built-in names from entering `invocations_hourly`; the reader fixes ensure correctness for historical rows and summary data.

**Key Constraint:** reader.py uses parameter binding (`$builtin_commands::text[]`) not the `_text_array` helper; rollups.py continues using its existing `_text_array` helper.

---

## Change List (ordered)

1. **vocabulary.py** — Add `BUILTIN_COMMAND_NAMES` tuple constant
2. **rollups.py** — Import + add NOT IN exclusion to kind-4 INSERT WHERE clause using `_text_array`
3. **reader.py** — Add parameter binding for built-in commands; add NOT IN exclusions to branches 2 & 3
4. **test_vocabulary.py** — Extend with new test for `BUILTIN_COMMAND_NAMES` constant
5. **test_rollups.py** — Add SQL-structure test for rollup exclusion predicate
6. **test_reader.py** — Extend two existing exact-SQL tests + add one new parameter binding test

---

## Stage 1: Test-Driven vocabulary.py Addition

### RED: vocabulary.py test (extend test_vocabulary.py)

```bash
poetry run pytest tests/codemie/repository/cli_analytics/test_vocabulary.py::test_builtin_command_names_constant -xvs
```

**Test assertion:**
- `BUILTIN_COMMAND_NAMES` is a tuple
- Contains exactly `("model", "exit", "quit")`
- No leading slashes (convention 9: OTel stores without slash)
- `compact` is absent (it is a real slash command, must be counted)
- Tuple is immutable (no accidental mutation)

**Expected:** FAIL (constant does not exist yet)

### GREEN: Implement vocabulary.py change

**File:** `src/codemie/repository/cli_analytics/vocabulary.py`  
**Location:** After line 139 (after `SENTINEL_EXACT`)  
**Action:** Add new module-level constant:

```python
BUILTIN_COMMAND_NAMES: tuple[str, ...] = ("model", "exit", "quit")
```

**File touch:** `tests/codemie/repository/cli_analytics/test_vocabulary.py` — add new test function (extend existing file, do not create).

---

## Stage 2: Test-Driven rollups.py Addition

### RED: rollups.py SQL-structure tests (add to test_rollups.py)

```bash
poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_rollups.py::test_kind_4_rollup_excludes_builtin_commands -xvs
```

**Test assertion:**

- The kind-4 INSERT statement in `rollups.RECOMPUTE` includes a WHERE clause condition referencing `BUILTIN_COMMAND_NAMES` via the `_text_array` helper. Assertion: parse the SQL string from the module and confirm the static expansion `ARRAY['model', 'exit', 'quit']` is present in the WHERE clause via the call `_text_array(BUILTIN_COMMAND_NAMES)`, with the predicate `<> ALL(...)` applied to `l.command_name`. The `_text_array` helper expands at import time (not parameter binding), so the exact SQL literal is visible in the source.

**Expected:** FAIL (no exclusion predicate in kind-4 INSERT yet)

### GREEN: Implement rollups.py change

**File:** `src/codemie/repository/cli_analytics/postgres/rollups.py`  
**Import:** Add `BUILTIN_COMMAND_NAMES` to the import from `vocabulary` (line 56–68):

```python
from codemie.repository.cli_analytics.vocabulary import (
    ...
    BUILTIN_COMMAND_NAMES,  # ← ADD THIS
    ...
)
```

**Location:** Line 1592 (the kind-4 INSERT WHERE clause)  
**Current WHERE:**
```sql
WHERE k.kinds & {_LOG} <> 0 AND l.event_kind = {int(EventKind.USER_PROMPT)} AND l.command_name <> ''
```

**Change to:**
```sql
WHERE k.kinds & {_LOG} <> 0 AND l.event_kind = {int(EventKind.USER_PROMPT)} AND l.command_name <> '' AND l.command_name <> ALL({_text_array(BUILTIN_COMMAND_NAMES)})
```

**Pattern:** Matches the existing `_PROMPT_OK` predicate pattern already in this file (line 159):
```python
lower(btrim(h.prompt_body)) <> ALL({_text_array(SENTINEL_EXACT)})
```

---

## Stage 3: Test-Driven reader.py Branch 2 (no-summary) Exclusion

### RED: reader.py branch 2 & 3 tests + parameter binding (add to test_reader.py)

```bash
# Branch 2 predicate test
poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py::test_otel_slash_commands_count_only_for_sessions_without_a_summary -xvs
# Branch 3 predicate test
poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py::test_summary_sessions_count_every_string_command_once_by_the_shared_window -xvs
# Parameter binding test
poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py::test_invocations_bind_builtin_command_names -xvs
```

**Test assertions:**

1. **Existing test update — branch 2 predicate:** Extend `test_otel_slash_commands_count_only_for_sessions_without_a_summary` (already exists) to include the new `$builtin_commands::text[]` placeholder in its exact-SQL assertion for the `invocations_hourly` branch. Parse the SQL and confirm the new `AND name <> ALL($builtin_commands::text[])` predicate is present.

2. **Existing test update — branch 3 predicate:** Extend `test_summary_sessions_count_every_string_command_once_by_the_shared_window` (already exists) to include the new `$builtin_commands::text[]` placeholder in its exact-SQL assertion for the `session_dims.commands` branch. Parse the SQL and confirm the new `AND (e.cmd #>> '{{}}') <> ALL($builtin_commands::text[])` predicate is present.

3. **New test — parameter binding:** Add exactly one new test function named `test_invocations_bind_builtin_command_names` using an `_ArgRecorder` on the real reader.py `get_invocations` execution path. Assert that the parameter `$builtin_commands` bound during execution equals `list(BUILTIN_COMMAND_NAMES)`.

**Expected:** FAIL (no exclusion conditions or parameter binding yet)

### GREEN: Implement reader.py branch 2 change

**File:** `src/codemie/repository/cli_analytics/postgres/reader.py`  
**Imports & Parameters:** 

1. Add `BUILTIN_COMMAND_NAMES` to the import from `vocabulary` at the top
2. In `get_invocations`, add parameter binding to `window_params(f)` call:
   ```python
   params = window_params(f)
   params["builtin_commands"] = list(BUILTIN_COMMAND_NAMES)
   ```

**Location:** Lines 432–433 (the kind=4 `invocations_hourly` branch for no-summary sessions)  
**Current:**
```sql
SELECT kind, name, calls AS c FROM invocations_hourly
WHERE kind = 4 AND {_no_summary("invocations_hourly.session_id")}{self._scope(f)}
```

**Change to:**
```sql
SELECT kind, name, calls AS c FROM invocations_hourly
WHERE kind = 4 AND {_no_summary("invocations_hourly.session_id")} AND name <> ALL($builtin_commands::text[]){self._scope(f)}
```

**Rationale:** reader.py does not import or use `_text_array` helper (that is rollups.py-internal); instead, parameter binding is used with the `:text[]` cast. The parameter value is set once in the method using `list(BUILTIN_COMMAND_NAMES)`.

---

## GREEN: Implement reader.py Branch 2 & 3 Changes

**File:** `src/codemie/repository/cli_analytics/postgres/reader.py`

**Import + Parameter Setup:** 
1. Add `BUILTIN_COMMAND_NAMES` to the import from `vocabulary` at the top
2. In `get_invocations`, add parameter binding to `window_params(f)` call:
   ```python
   params = window_params(f)
   params["builtin_commands"] = list(BUILTIN_COMMAND_NAMES)
   ```

**Branch 2 (no-summary, lines ~432–433):**
```sql
SELECT kind, name, calls AS c FROM invocations_hourly
WHERE kind = 4 AND {_no_summary("invocations_hourly.session_id")} AND name <> ALL($builtin_commands::text[]){self._scope(f)}
```

**Branch 3 (summary, lines ~435–437):**
```sql
SELECT 4, e.cmd #>> '{{}}', 1 FROM session_dims, jsonb_array_elements(session_dims.commands) AS e(cmd)
WHERE session_dims.summary_ts IS NOT NULL AND jsonb_typeof(e.cmd) = 'string'
  AND e.cmd #>> '{{}}' <> '' AND (e.cmd #>> '{{}}') <> ALL($builtin_commands::text[]){self._scope(f)}
```

**Rationale:** reader.py uses parameter binding (not the `_text_array` helper, which is rollups.py-internal). The parameter `$builtin_commands` is set once in the method as `list(BUILTIN_COMMAND_NAMES)` and used in both branch WHERE clauses.

---

## Stage 4: Regression and Extended Test Coverage

### Update existing exact-SQL test expectations

**Files:** `test_reader.py` and `test_rollups.py`

The two existing reader tests (`test_otel_slash_commands_count_only_for_sessions_without_a_summary` and `test_summary_sessions_count_every_string_command_once_by_the_shared_window`) that inspect exact SQL must be minimally updated to include the new `<> ALL($builtin_commands::text[])` placeholders in their WHERE clause assertions. Update TDD command counts for these two tests consistently (they are the only exact-SQL reader tests touched); other tests pass without modification.

### Run updated tests

```bash
poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_rollups.py -xvs
poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -xvs
```

**Expectation:** Two updated reader exact-SQL tests + one new parameter binding test pass; all unrelated tests pass without modification.

---

## Production & Test Files in Scope

| File | Lines | Change | Status |
|---|---|---|---|
| `src/codemie/repository/cli_analytics/vocabulary.py` | 140–141 | Add `BUILTIN_COMMAND_NAMES` tuple | GREEN after Stage 1 test |
| `src/codemie/repository/cli_analytics/postgres/rollups.py` | 56–68 (import), 1592 (WHERE) | Import + add NOT IN exclusion to kind-4 INSERT using `_text_array` | GREEN after Stage 2 test |
| `src/codemie/repository/cli_analytics/postgres/reader.py` | ~418–441 (method), lines ~433 & ~437 (WHERE) | Add parameter binding + NOT IN exclusions to branches 2–3 using `$builtin_commands::text[]` | GREEN after Stage 3 tests |
| `tests/codemie/repository/cli_analytics/test_vocabulary.py` | (extend) | Add new test for `BUILTIN_COMMAND_NAMES` constant (verifies exact tuple, no compact) | Extend existing file, Stage 1 RED |
| `tests/codemie/repository/cli_analytics/postgres/test_rollups.py` | (extend) | Add SQL-structure test for rollup exclusion predicate presence | Extend existing file, Stage 2 RED |
| `tests/codemie/repository/cli_analytics/postgres/test_reader.py` | (extend + update) | Extend two existing exact-SQL tests + add one new parameter binding test | Extend + update existing, Stage 3 RED + Stage 4 update |

---

## Predicates & Constants Usage

**Constant:** `BUILTIN_COMMAND_NAMES: tuple[str, ...] = ("model", "exit", "quit")`  
- **No leading slashes** (convention 9: OTel and SQL storage use bare names)
- **Immutable tuple** (defined once at module level)
- **Never modified** (runtime configuration not in scope)

**Exclusion Predicate — rollups.py (uses `_text_array` helper):**
```sql
AND l.command_name <> ALL({_text_array(BUILTIN_COMMAND_NAMES)})
```
- Location: rollups.py line 1592 — prevents new kind-4 rows from entering `invocations_hourly`
- Helper: `_text_array(BUILTIN_COMMAND_NAMES)` converts tuple to SQL `ARRAY['model', 'exit', 'quit']` literal at import time

**Exclusion Predicate — reader.py (uses parameter binding):**
```sql
AND column <> ALL($builtin_commands::text[])
```
- Used in two SQL locations within `get_invocations`:
  1. Line ~433 — filters historical kind-4 rows read from `invocations_hourly` (no-summary branch)
  2. Line ~437 — filters summary slash-command data from `session_dims.commands` (summary branch)
- Parameter: `$builtin_commands` bound once in `get_invocations` as `list(BUILTIN_COMMAND_NAMES)`
- Rationale: reader.py does not use `_text_array` helper (internal to rollups); parameter binding is the standard pattern for dynamic values in `get_*` methods

---

## Confirmations (Non-Goals Explicitly Excluded)

✓ **No UI changes** — `get_invocations` signature and return type are unchanged; UI renders what the backend returns.  
✓ **No ports.py changes** — `CliAnalyticsReader` Protocol is unchanged.  
✓ **No migration** — Data model, schema, or table changes not needed.  
✓ **No backfill code** — The read-layer fix handles historical rows immediately; backfill.py remains available for storage cleanup.  
✓ **No `SENTINEL_EXACT` modification** — Remains used for first-prompt filtering with leading slashes (`/exit`, etc.); not modified.  
✓ **No `clear` or `resume` exclusion** — No evidence these should be excluded; not added to `BUILTIN_COMMAND_NAMES`.  
✓ **No `compact` exclusion** — `compact` is a real user-invoked slash command; deliberately absent from tuple.  
✓ **`model`, `exit`, `quit` in rollup** — Exactly these three names are excluded; verified in AC2/AC3.  
✓ **`model`, `exit`, `quit` in both reader branches** — AC4 (branch 2) and AC5 (branch 3) confirm the same exclusion.  
✓ **`compact` passes through** — AC3 (not in exclusion set for rollup) and AC6 (not filtered in reader) explicitly confirm `compact` is counted.

---

## Command Reference

### Run Tests in Order

```bash
# Stage 1: vocabulary constant (RED)
poetry run pytest tests/codemie/repository/cli_analytics/test_vocabulary.py::test_builtin_command_names_constant -xvs
# → GREEN after vocabulary.py implementation

# Stage 2: rollup SQL exclusion (RED)
poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_rollups.py::test_kind_4_rollup_excludes_builtin_commands -xvs
# → GREEN after rollups.py implementation

# Stage 3: reader branch 2 & 3 + parameter binding (RED)
poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py::test_otel_slash_commands_count_only_for_sessions_without_a_summary -xvs
poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py::test_summary_sessions_count_every_string_command_once_by_the_shared_window -xvs
poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py::test_invocations_bind_builtin_command_names -xvs
# → GREEN after reader.py implementation (all three assertions)

# Stage 4a: Focused regression tests (3 touched test files)
poetry run pytest tests/codemie/repository/cli_analytics/test_vocabulary.py tests/codemie/repository/cli_analytics/postgres/test_rollups.py tests/codemie/repository/cli_analytics/postgres/test_reader.py -xvs

# Stage 4b: Quality gates (changed Python files only)
make ruff                                    # Lint, format, import sort (vocabulary.py, rollups.py, reader.py + tests)
make license-check                           # Repository license-header check
make build                                   # Build checks
make test                                    # Full test suite or record environment blocker

# Pre-MR repository requirement
make test-harness                            # Repository harness validation
```

---

## Implementation Summary

**Six files, two decision points:**

### Production Files (3)
1. **vocabulary.py** — 1 line added: `BUILTIN_COMMAND_NAMES` tuple
2. **rollups.py** — 2 edits: import + 1 WHERE clause extension (using `_text_array`)
3. **reader.py** — 3 edits: import + parameter setup + 2 WHERE clause extensions (using `$builtin_commands::text[]`)

### Test Files (3, extend existing + update assertions)
4. **test_vocabulary.py** — 1 new test function added (extend existing file)
5. **test_rollups.py** — 1 new test function added (extend existing file)
6. **test_reader.py** — 1 new test function + minimal updates to existing SQL assertions (extend + update existing file)

**Non-goals explicitly excluded:**
- No UI, ports, migrations, or backfill changes
- No `SENTINEL_EXACT` modification
- No runtime configuration or environment variables
- No `clear`, `resume`, or `compact` added to exclusion set

---

## Revision Notes (Plan-Only Corrections Round 2)

**Corrections applied (no implementation):**
1. ✓ All bare `pytest` commands replaced with `poetry run pytest`
2. ✓ reader.py branch 2 & 3 tests clarified: assert `$builtin_commands::text[]` placeholder + add minimal parameter binding test via `_ArgRecorder` to prove bound value equals `list(BUILTIN_COMMAND_NAMES)`
3. ✓ vocabulary.py test is the single source proving exact tuple and compact absence; existing reader `_q` path catches missing parameters
4. ✓ test_reader.py scope: two existing test extensions + one minimal parameter binding assertion = three test assertions total (not five)
5. ✓ Pre-MR command corrected: `make test-harness` (not nonexistent `test_harness.py`)
6. ✓ make license-check remains as documented; no "changed-files-only" wording added (no FILE arguments)
7. ✓ Removed claim that plan.approved is already approved; plan remains pending human approval

## Revision Notes (Final Mechanical Corrections Round 3)

**Corrections applied (no implementation):**
1. ✓ Stage 2 rollups.py test section: assert the static SQL expansion `ARRAY['model', 'exit', 'quit']` via `_text_array(BUILTIN_COMMAND_NAMES)` helper; removed reference to `$builtin_commands` placeholder (parameter binding is reader.py-only)
2. ✓ Consolidated reader.py test structure: two existing exact-SQL tests (`test_otel_slash_commands_count_only_for_sessions_without_a_summary` and `test_summary_sessions_count_every_string_command_once_by_the_shared_window`) are extended to include placeholder predicates; one new test named `test_invocations_bind_builtin_command_names` added for parameter binding using `_ArgRecorder`
3. ✓ Removed redundant Stage 4 section; stages renumbered (Stage 5 → Stage 4)
4. ✓ Updated make license-check command description to "Repository license-header check" (exact scope from current implementation)
5. ✓ Plan remains at `plan.approved` pending human approval; no implementation, commits, or MR created

**Status:** Plan-only revision complete. Ready for human approval.
