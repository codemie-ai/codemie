# Slash-Started Skills Classified As Skills — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A skill started with a slash command is a `skill` everywhere CLI Analytics reports it: Session Detail shows kind `skill`, and Tools & Models lists it under `skills_invoked` only.

**Architecture:** Two read-side fixes, no migration, no rollup change. (1) Session Detail: `_dispatch_label_and_kind` stops special-casing `is_slash_command`; every dispatch row that carries a `skill_name` is a skill. (2) Tools & Models: in `get_invocations` and its mirror `get_class_lists("invocation")`, a kind-4 (slash command) name is dropped for a session when that same session has a kind-2 (skill) row of that name, so the skill keeps its kind-2 count and the command row disappears.

**Tech Stack:** Python 3.12, asyncpg + PostgreSQL (hand-written SQL in `PostgresCliAnalyticsReader`), pytest / pytest-asyncio, Poetry.

**Spec:** none (`sdlc-light`). Requirements are ticket EPMCDME-17201 as resolved in `technical-analysis.md` (same directory); decision taken in Stage 2: Tools & Models lists a slash-started skill under Skills only.

## Global Constraints

- No Alembic migration, no `rollups.py` change, no backfill: `invocations_hourly` rows are untouched.
- `LocalAnalyticsDispatchRow.kind` stays a free string and its description in `src/codemie/rest_api/models/cli_analytics.py:360` is not edited, so `tests/codemie/rest_api/router_guards/baselines/openapi.json` does not change.
- Cost, token and duration columns of the dispatch query (`get_session_detail_dispatches`, `reader.py:1826-2148`) are not edited, and its `ORDER BY dispatch_start, is_slash_command, subagent_type COLLATE "C", skill_name COLLATE "C"` tie-break stays. `is_slash_command` stays in the SQL; only the handler stops reading it for the label and kind.
- Skill counts in `skills_invoked` stay the kind-2 value; a command row is only ever removed, never added to a skill's count.
- Code comments state the reason in one line and carry no ticket IDs. Test docstrings may name a ticket (existing convention).
- Commit messages: `EPMCDME-17201: <Capitalised description>`, then the attribution lines from the session reminder (`Generated with AI` and the `Co-Authored-By` line).
- Python is run through Poetry (`poetry run ...`), per `.ai-run/guides/quality-gates.md`.
- Live-DB tests run with `TEST_LOCAL_PG_URL` and `-n0` (local container `cli-live-pg17` is published on `localhost:35417`; the URL form used elsewhere is `postgresql://postgres:password@localhost:<port>/postgres`; confirm it connects before relying on it).

## Review Focus

- A plain command that shares its name with a skill used in a *different* session stays under `slash_commands` for the session that only typed it (per-session exclusion, not global). Pinned in Task 2.
- A summary-based session (`summary_ts` set, name in `session_dims.commands`) that started a skill by slash: the name leaves `slash_commands`, the skill keeps its kind-2 count. Pinned in Task 2.
- A command with no kind-2 row in its session (a hook never arrived) stays visible under `slash_commands` rather than vanishing from both lists. Pinned in Task 2.
- `get_class_lists("invocation")` and `get_invocations` agree: the same name never has a `command` class-list row without a matching `command` count row. Pinned in Task 2.
- A skill started with the Skill tool and one started by slash in the same session both become `skill` dispatch rows, and their number equals the number of kind-2 names. Pinned in Task 1.

---

### Task 1: Session Detail classifies every skill-backed dispatch as a skill

**Files:**
- Modify: `src/codemie/service/analytics/handlers/cli_analytics_handler.py:463-468` (`_dispatch_label_and_kind`)
- Modify (comments and guide wording only): `src/codemie/repository/cli_analytics/postgres/reader.py` comments at `:1829-1836`, `:2095-2099`, `:2119-2121`; `src/codemie/repository/cli_analytics/vocabulary.py:96`; `.ai-run/guides/integration/cli-analytics-events.md` (the sentence "its fork is its own `command` row when it carries `skill_name`", near lines 405-431)
- Test: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` (rows at `:1357-1392` pin kind `command`; helper `_dispatch_row` at `:1287`)

**Interfaces:**
- Consumes: `_dispatch_label_and_kind(row: dict, type_totals: dict[str, int], counters: dict[str, int]) -> tuple[str, str] | None` (existing signature, unchanged); `LocalAnalyticsHandler._build_dispatches(rows, session_start, session_duration_ms, session_cost)` as the tests already call it.
- Produces: for a row with a non-empty `skill_name` and no `subagent_type`, the result is always `(f"skill · {skill}", "skill")`, whatever `is_slash_command` is. Agent rows and rows with neither field are unchanged.

Test-first: yes — `test_a_slash_started_skill_row_is_a_skill_dispatch` fails today because the function returns `("command · code-review", "command")`.

- [ ] **Step 1: Write the failing tests** in `test_cli_analytics_handler.py`, next to the dispatch tests (import `_dispatch_label_and_kind` from the handler module like the neighbouring tests import the private helpers they use):
  - `test_a_slash_started_skill_row_is_a_skill_dispatch`: `_dispatch_label_and_kind({"skill_name": "code-review", "is_slash_command": 1}, {}, {}) == ("skill · code-review", "skill")`.
  - `test_a_skill_tool_row_and_a_slash_started_row_get_the_same_kind`: both rows (`is_slash_command` 0 and 1) return kind `"skill"`.
  - `test_an_agent_row_stays_an_agent_even_when_it_carries_the_slash_flag`: `{"subagent_type": "Explore", "skill_name": "x", "is_slash_command": 1}` with `type_totals={"Explore": 1}` returns `("agent · Explore", "agent")`.
  - `test_a_row_with_neither_skill_nor_agent_is_not_a_dispatch`: `{"skill_name": "", "subagent_type": "", "is_slash_command": 1}` returns `None` (a plain command never becomes a bar here).
  - `test_slash_started_and_skill_tool_skills_are_both_skill_bars`: `_build_dispatches` over `_dispatch_row(skill_name="forkme") | {"is_slash_command": True}` and `_dispatch_row(skill_name="viatool", offset_s=20)` yields two dispatches of kind `skill`, none of kind `command`.

- [ ] **Step 2: Run them and confirm they fail**
  Run: `poetry run pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -k "slash_started or skill_tool_row or agent_row_stays or neither_skill_nor_agent" -q`
  Expected: the first, second and fifth fail on kind `command`; the third and fourth already pass (they pin unchanged behaviour).

- [ ] **Step 3: Implement** in `_dispatch_label_and_kind`: drop the `is_slash_command` branch so a non-empty `skill` always returns `(f"skill · {skill}", "skill")`. Add one comment line saying why: a skill is a skill however it was started; the reader marks slash-started rows only to order them.

- [ ] **Step 4: Update the three existing tests that pin `command`** (`test_forked_skill_command_dispatch_uses_real_duration_ms`, `test_main_thread_slash_command_dispatch_stays_instant`, `test_a_slash_command_at_the_session_end_keeps_its_duration`, `:1357-1392`): select the row by `d["kind"] == "skill"` instead of `"command"` and reword their docstrings from "command-kind row" to "slash-started skill row". Their duration assertions stay byte-identical (this is the cost/duration guard for AC5).

- [ ] **Step 5: Refresh the stale wording.** In the files listed above, change every comment/doc sentence that calls a slash-started skill's row a "command" row to "skill row (started by slash)". In `cli-analytics-events.md` also add one sentence under the `agent.skill.dispatch` section: the timeline kind of a skill does not depend on how it was started. Do not touch any SQL.

- [ ] **Step 6: Run the handler tests and the live pricing test's label pins**
  Run: `poetry run pytest tests/codemie/service/analytics/handlers -q`
  Expected: all PASS.
  Then update `tests/codemie/repository/cli_analytics/postgres/test_live_route_pricing.py:838-844` to expect `skill · forkme`, `skill · groupme`, `skill · inlineme` and `d["kind"] == "skill"` (the cost numbers and `sum(...) == detail["cost_usd"]` stay unchanged, they guard AC5), and run it with the live DB: `TEST_LOCAL_PG_URL=<url> poetry run pytest -n0 tests/codemie/repository/cli_analytics/postgres/test_live_route_pricing.py -q`. Expected: PASS, none skipped. If no database is reachable, record that the live file was not run.

- [ ] **Step 7: Commit**
```bash
git add src/codemie/service/analytics/handlers/cli_analytics_handler.py tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py tests/codemie/repository/cli_analytics/postgres/test_live_route_pricing.py src/codemie/repository/cli_analytics/postgres/reader.py src/codemie/repository/cli_analytics/vocabulary.py .ai-run/guides/integration/cli-analytics-events.md
git commit -m "EPMCDME-17201: Classify slash-started skills as skills in Session Detail"
```

---

### Task 2: Tools & Models lists a slash-started skill under Skills only

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py` — new helper next to `_no_summary` (`:345`); `get_invocations` (`:1122-1150`); `get_class_lists`, `"invocation"` source (`:787-800`)
- Modify (wording only): `.ai-run/guides/integration/cli-analytics-storage.md` "Slash commands (`get_invocations`, kind 4)" section (lines ~191-200); comments in `rollups.py:1978-1992`, `:2038`, `session_derived.py:32-36`, `reader.py:333-334` that describe a slash-started skill as a command
- Test: `tests/codemie/repository/cli_analytics/postgres/test_reader.py` (`:1395-1477`, exact-SQL pins); `tests/codemie/repository/cli_analytics/postgres/test_live_route_pricing.py` (new live test, reuses its `_engine`, `_cleanup`, `_START`, `_LOCAL_PG_URL` helpers; add it at the end of the file)

**Interfaces:**
- Consumes: `_no_summary(column: str) -> str` (existing), `self._scope(f)` and `window_params(f)` (existing).
- Produces: `_not_a_skill_of_session(session_column: str, name_column: str) -> str` in `reader.py`, returning the SQL predicate `NOT EXISTS (SELECT 1 FROM invocations_hourly sk WHERE sk.kind = 2 AND sk.session_id = <session_column> AND sk.name = <name_column> AND sk.session_id IN (SELECT session_id FROM sel))`. The trailing `sel` scope was added during implementation: the reader's scope audit (`_unscoped_fact_branches`) requires every fact read to carry it. `get_invocations` and `get_class_lists("invocation")` AND it onto both kind-4 branches: the rollup branch with `("invocations_hourly.session_id", "invocations_hourly.name")`, the summary-commands branch with `("session_dims.session_id", "(e.cmd #>> '{}')")`. The kind-2/3 branch is untouched. Row shape (`kind`, `name`, `count` / `key`, `kind`, class lists) is unchanged.

Skill-name source decision: the exclusion set is exactly the kind-2 rows of the same session, i.e. the names that session already contributes under Skills. A name only present in summary `skills` keys or `session_skills` is not excluded, because `get_invocations` never lists it under Skills, so excluding it would drop it from both lists.

Test-first: yes — the updated SQL-shape pins in `test_reader.py` fail until the predicate is in place, and `test_live_a_slash_started_skill_is_listed_under_skills_only` fails today with the skill present in both lists.

- [ ] **Step 1: Write the failing SQL-shape tests** in `test_reader.py`:
  - Update `_invocation_branches`-based pins (`test_otel_slash_commands_count_only_for_sessions_without_a_summary`, `test_summary_sessions_count_every_string_command_once_by_the_shared_window`, `test_an_empty_summary_command_counts_nothing_and_names_are_taken_as_stored`) so the kind-4 branches contain the predicate before the `_SCOPED` suffix. Add a module constant `_NOT_A_SKILL = lambda s, n: ...` mirroring the helper's text (do not import the private helper into the test; the test spells the SQL out as the neighbouring `_no_summary` does).
  - Keep `test_invocations_have_three_branches_each_scoped_to_sel` and `test_skill_and_agent_invocations_come_from_the_hourly_rollup_alone` unchanged: the skill/agent branch must not gain the predicate.
  - Add `test_class_lists_by_invocation_exclude_the_same_session_skill_names_from_both_command_branches`: capture the SQL of `get_class_lists(_flt(), "invocation")` the way `_invocation_branches` does and assert both kind-4 branches contain the predicate with the column pairs above and the kind-2/3 branch does not.

- [ ] **Step 2: Write the failing live test** `test_live_a_slash_started_skill_is_listed_under_skills_only` in `test_live_route_pricing.py` (skipped without `TEST_LOCAL_PG_URL`). Seed with direct INSERTs, no `_refresh` (a refresh would recompute the rollup rows): four sessions with unique ids `f"test-skill-or-command-{uuid.uuid4().hex[:8]}"`, each with a `session_dims` row (`started_at` inside the window; `summary_ts` NULL for the OTel-path sessions, set with a `commands` jsonb array for the summary-path ones; check the table's other NOT NULL columns before inserting) and `invocations_hourly` rows at an hour inside the window:
  - `otel`: kind 2 `inlineme` calls 1, kind 4 `inlineme` calls 1, kind 4 `compact` calls 2.
  - `summary`: kind 2 `forkme` calls 2, `commands = ["forkme", "compact"]`.
  - `typed_only`: kind 4 `inlineme` calls 1, no kind-2 row (the same name as a skill in another session).
  - `no_hook`: summary session with `commands = ["orphan"]` and no kind-2 row.
  Call `PostgresCliAnalyticsReader(engine).get_invocations(...)` and `LocalAnalyticsHandler(reader).get_tools(...)` with a `LocalAnalyticsFilter` covering the window and only these sessions (follow how other tests in the file build one with `LocalAnalyticsFilter(`; keep it scoped so other data in the database cannot leak in). Assert: `skills_invoked` has `inlineme` count 1 and `forkme` count 2; `slash_commands` has `inlineme` count 1 (from `typed_only` only), `compact` count 3 and `orphan` count 1, and contains no `forkme`; `get_class_lists(f, "invocation")` has a `command` row for `inlineme`, `compact` and `orphan` and none for `forkme`. Clean up with `_cleanup` in `finally`.

- [ ] **Step 3: Run the new and updated tests and confirm they fail**
  Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -k "invocation or slash or class_lists" -q`
  Expected: the updated pins and the new class-lists test FAIL (predicate missing).
  Run: `TEST_LOCAL_PG_URL=<url> poetry run pytest -n0 tests/codemie/repository/cli_analytics/postgres/test_live_route_pricing.py -k skills_only -q`
  Expected: FAIL with `forkme` and the matching `inlineme` still in `slash_commands`.

- [ ] **Step 4: Implement** `_not_a_skill_of_session` and apply it to the two kind-4 branches of `get_invocations` and of the `"invocation"` source in `get_class_lists`, placed before `{self._scope(f)}` in `get_invocations` so the scope suffix stays last. Update the `get_invocations` docstring with one sentence: a slash command that is a skill of the same session is counted once, under Skills. Reword the storage-guide section and the comments listed above to the same rule.

- [ ] **Step 5: Run the tests and confirm they pass**
  Run: `poetry run pytest tests/codemie/repository/cli_analytics tests/codemie/service/analytics -q`
  Expected: all PASS (live files report skipped without the URL).
  Run: `TEST_LOCAL_PG_URL=<url> poetry run pytest -n0 tests/codemie/repository/cli_analytics/postgres/test_live_route_pricing.py -q`
  Expected: PASS, none skipped. Also run once on the PostgreSQL 14 container (`cli-live-pg14`, port 35414) since the repo supports 14 and 17.

- [ ] **Step 6: Commit**
```bash
git add src/codemie/repository/cli_analytics/postgres/reader.py tests/codemie/repository/cli_analytics/postgres/test_reader.py tests/codemie/repository/cli_analytics/postgres/test_live_route_pricing.py .ai-run/guides/integration/cli-analytics-storage.md src/codemie/repository/cli_analytics/postgres/rollups.py src/codemie/repository/cli_analytics/postgres/session_derived.py
git commit -m "EPMCDME-17201: Drop slash-started skills from slash commands in Tools and Models"
```

---

## Out of this repo (hand-off note for the MR description)

The UI colours or labels timeline bars by `dispatches[].kind` / the `command · ` prefix; after this change slash-started skills arrive as `skill` / `skill · <name>`. The frontend needs no new field, but its owners should know the `command` kind no longer appears in Session Detail dispatches.
