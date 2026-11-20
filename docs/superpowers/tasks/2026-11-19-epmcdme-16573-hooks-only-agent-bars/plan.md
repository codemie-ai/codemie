# EPMCDME-16573: Hooks-Only Agent Bars Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Session Detail Timeline render an agent bar for every subagent run reported only by
hook events (GitHub Copilot CLI, and any Claude Code session ingested without OTel), by adding one new
`UNION ALL` branch to `get_session_detail_dispatches`.

**Architecture:** `get_session_detail_dispatches` (`src/codemie/repository/cli_analytics/postgres/reader.py`)
already has an OTel tool-span branch and a hook-only fallback branch for skill forks. This plan adds a
third branch, structurally mirroring the skill fallback, that reads `hook_events` for plain agent forks
(`agent_type <> ''`, `skill_name = ''`) and reuses the already-computed `stopped`/`agent_usage` CTEs and
the `first_subagent_start` helper. A `NOT EXISTS` guard against `t`/`launched` stops it from duplicating a
row already produced by the OTel tool-span branch. No other file changes.

**Tech Stack:** PostgreSQL (asyncpg), pytest + `pytest.mark.asyncio`, the repo's live-Postgres integration
test harness (`TEST_LOCAL_PG_URL`).

## Global Constraints

- One new `UNION ALL` branch plus its guard in `get_session_detail_dispatches` — no refactor of the
  surrounding query, no change to `agent_call_attribution.py`, no change to the scalars `agent_count`
  hook-fallback (`reader.py:3193-3218`) or the rollup's kind-3 hook branch (`rollups.py:2098-2101`).
- The new branch must **not** gate on `is_claude_code()`. Verified this run: `is_claude_code(alias)` is
  `coalesce(attrs->>'platform', 'claude-code') = 'claude-code'`, and GitHub Copilot CLI hook events carry
  `platform = 'copilot-cli'` (confirmed against `test_reader_live_copilot_detail.py`'s `_hook` helper), so
  copying the skill branch's `AND is_claude_code('h')` verbatim would silently exclude every Copilot
  session — the ticket's primary named symptom. This is a deliberate, documented deviation from "mirror
  the skill branch," not an oversight.
- No handler/API/frontend change. Confirmed this run by reading `_dispatch_label_and_kind`,
  `_count_dispatch_subtypes`, and `_build_dispatches` in `cli_analytics_handler.py`: none inspects a row's
  source, all three already render the existing hook-only skill branch's rows through the identical path.
  The new branch's rows use the same 13-column shape with `subagent_type` populated and `skill_name = ''`,
  so they reach the same code unchanged.
- No touching the "Active time is empty for hooks-only sessions" defect or EPMCDME-16571 (Copilot skills).
- No per-task commits. Each task below leaves its changes uncommitted; the user commits later on request.
- This is a pure-SQL change: no Python helper is added or modified. Requirement coverage for "unit tests
  for any touched Python helper" is satisfied by this fact, not by inventing a unit test — stated once
  here rather than repeated per task.

## Acceptance criteria

- [ ] A hooks-only session (no TOOL spans) with `agent.subagent.start`/`.stop` pairs for five subagents —
  two named agents and three overlapping ("fleet") agents — renders exactly one Timeline agent row per
  subagent, each labelled by its own `agent_type`, each spanning its own start hook to its own matching
  stop hook (by `agent_id`), with overlapping runs preserved as overlapping.
- [ ] Of those five rows, only the one subagent given agent-scope usage carries `cost_usd`/token values;
  the other four are `NULL`, never the main thread's.
- [ ] The synthetic `"session"` Timeline row is unaffected by the new branch.
- [ ] This holds for a session whose hook events are **not** Claude-Code-shaped (e.g. `platform =
  'copilot-cli'`) — the fix does not gate on `is_claude_code()`.
- [ ] A Claude Code session that has both an OTel Agent tool span and the matching subagent hooks for the
  same `agent_id` still renders exactly one row for that subagent — no duplicate from the new branch.
- [ ] No file outside `src/codemie/repository/cli_analytics/postgres/reader.py` changes.

## File Structure

- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py` — one new `UNION ALL` branch (plus
  guard) inside `get_session_detail_dispatches`, inserted between the existing skill hook-fallback branch
  and the slash-command dispatch branch (current lines 3733/3734).
- Create: `tests/codemie/repository/cli_analytics/postgres/test_live_hooks_only_agent_dispatches.py` — the
  two required live-Postgres integration tests for EPMCDME-16573, following the direct-insert harness
  pattern of `test_live_skill_tool_attribution.py` and `test_live_route_pricing.py`.

---

### Task 1: Write the two failing/guarding integration tests

**Files:**
- Create: `tests/codemie/repository/cli_analytics/postgres/test_live_hooks_only_agent_dispatches.py`

**Interfaces:**
- Consumes: `PostgresCliAnalyticsReader.get_session_detail_dispatches(session_id, *, scope)` (existing,
  unchanged signature) from `codemie.repository.cli_analytics.postgres.reader`; `DETAIL_SCOPE` from
  `tests.codemie.repository.cli_analytics.support.scopes`; the already-proven test helpers `_TODAY`,
  `_cleanup`, `_detail`, `_engine`, `_entry`, `_hook`, `_id`, `_ingest`, `_refresh`, `_SONNET`,
  `_SONNET_RATES` from `test_live_route_pricing.py`, and `_agent`, `_interaction`, `_request`, `_span` from
  `test_live_skill_tool_attribution.py` (all unchanged — this task only imports them).
- Produces: nothing other tasks consume; Task 2 makes these tests pass.

Test-first: yes — both tests in this task are the failing tests. Test 1 fails against current `HEAD`
because no hook-only agent branch exists yet (0 of the expected 5 agent rows appear). Test 2 is a
regression guard: before Task 2's change it trivially passes (no new branch exists yet to duplicate a
row), and it is included in this same task so it exists, unmodified, before and after Task 2 — its
acceptance condition is that it keeps passing once the new branch is added.

- [ ] **Step 1: Write the test file**

```python
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Live-Postgres checks for EPMCDME-16573: Session Detail Timeline agent bars for hooks-only sessions
(GitHub Copilot CLI, and any Claude Code session ingested without OTel). Needs `TEST_LOCAL_PG_URL`
(a disposable PostgreSQL); skipped without.

    TEST_LOCAL_PG_URL=postgresql://<user>:<password>@<host>:<port>/<db> poetry run pytest -n0 <this file>
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from tests.codemie.repository.cli_analytics.postgres.test_live_route_pricing import (
    _SONNET,
    _SONNET_RATES,
    _TODAY,
    _cleanup,
    _detail,
    _engine,
    _entry,
    _hook,
    _id,
    _ingest,
    _refresh,
)
from tests.codemie.repository.cli_analytics.postgres.test_live_skill_tool_attribution import (
    _agent,
    _interaction,
    _request,
    _span,
)
from tests.codemie.repository.cli_analytics.support.scopes import DETAIL_SCOPE

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("TEST_LOCAL_PG_URL"), reason="TEST_LOCAL_PG_URL is not set: no database"
)

_USAGE = (0.5, 9, 9, 9, 9)  # cost, input, output, cache_read, cache_write


async def _plain_subagent(engine, sid, start_ts, stop_ts, agent_id, agent_type, platform):
    """A hooks-only subagent fork: SubagentStart/Stop with no Agent tool call and no span at all."""
    await _ingest(
        engine,
        [
            _hook("agent.subagent.start", "SubagentStart", sid, start_ts,
                  agent_id=agent_id, agent_type=agent_type, platform=platform),
            _hook("agent.subagent.stop", "SubagentStop", sid, stop_ts,
                  agent_id=agent_id, agent_type=agent_type, platform=platform),
        ],
    )  # fmt: skip


async def _plain_subagent_with_usage(engine, sid, start_ts, stop_ts, agent_id, agent_type, platform,
                                      prompt_id, usage):
    """The same hooks-only fork, plus agent-scope usage: an `llm_request` span (never a TOOL span) and its
    API request, both carrying `agent_id` — the `span_agent_usage` path `agent_usage` already joins
    through, exactly as the existing OTel-sourced agent rows do."""
    await _plain_subagent(engine, sid, start_ts, stop_ts, agent_id, agent_type, platform)
    group = _id(f"group-{sid}-{agent_id}")
    await _span(engine, sid, "claude_code.llm_request", start_ts + timedelta(seconds=5), 1,
                _id(f"llm-{sid}-{agent_id}"), group, agent_id=agent_id)  # fmt: skip
    await _request(engine, sid, start_ts + timedelta(seconds=5), prompt_id, f"req-{agent_id}", None, usage,
                   agent_id=agent_id, span=group)  # fmt: skip


async def _seed_hooks_only_session(engine, sid):
    at = lambda m, s=0: _TODAY + timedelta(minutes=m, seconds=s)  # noqa: E731
    # two named agents, sequential, Copilot-shaped (platform != 'claude-code')
    await _plain_subagent(engine, sid, at(1), at(3), "ag-explore", "explore", "copilot-cli")
    await _plain_subagent_with_usage(
        engine, sid, at(4), at(6), "ag-file-auditor", "file-auditor", "copilot-cli", "p-fa", _USAGE
    )
    # a /fleet-shaped parallel run: three overlapping subagents of the same agent_type
    await _plain_subagent(engine, sid, at(7, 0), at(7, 30), "ag-fleet-1", "general-purpose", "copilot-cli")
    await _plain_subagent(engine, sid, at(7, 5), at(7, 35), "ag-fleet-2", "general-purpose", "copilot-cli")
    await _plain_subagent(engine, sid, at(7, 10), at(7, 20), "ag-fleet-3", "general-purpose", "copilot-cli")
    await _refresh(engine, _TODAY.date())


@pytest.mark.asyncio
async def test_live_hooks_only_session_gets_one_agent_row_per_subagent() -> None:
    """A Copilot-shaped, hooks-only session (no TOOL spans at all) with five subagent forks renders one
    Timeline agent bar per fork, each from its own start hook to its own stop hook, overlaps preserved,
    only the one with usage carrying cost/tokens, the synthetic 'session' row unaffected."""
    sid = f"test-16573-hooks-only-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    try:
        await _seed_hooks_only_session(engine, sid)
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
        detail = await _detail(engine, sid, [_entry(_SONNET, _SONNET_RATES)], {})
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    agent_rows = [r for r in rows if r["subagent_type"] != ""]
    assert len(agent_rows) == 5, rows  # one row per fork: none dropped, none doubled
    by_type: dict[str, list[dict]] = {}
    for r in agent_rows:
        by_type.setdefault(r["subagent_type"], []).append(r)
    assert set(by_type) == {"explore", "file-auditor", "general-purpose"}
    assert len(by_type["general-purpose"]) == 3  # the three fleet rows share a type, not a row

    at = lambda m, s=0: _TODAY + timedelta(minutes=m, seconds=s)  # noqa: E731
    explore = by_type["explore"][0]
    assert explore["dispatch_start"] == at(1)
    assert explore["subagent_duration_ms"] == 120_000  # 1:00 to 3:00
    assert explore["real_duration_ms"] == 120_000
    assert explore["cost_usd"] is None and explore["input_tokens"] is None  # never the main thread's

    auditor = by_type["file-auditor"][0]
    assert auditor["dispatch_start"] == at(4)
    assert auditor["subagent_duration_ms"] == 120_000  # 4:00 to 6:00
    assert auditor["cost_usd"] == pytest.approx(0.5)
    assert auditor["input_tokens"] == 9

    fleet = sorted(by_type["general-purpose"], key=lambda r: r["dispatch_start"])
    fleet_ends = [r["dispatch_start"] + timedelta(milliseconds=r["subagent_duration_ms"]) for r in fleet]
    assert fleet[0]["dispatch_start"] < fleet[1]["dispatch_start"] < fleet_ends[0]  # fleet-1/-2 overlap
    assert all(r["cost_usd"] is None for r in fleet)  # none of the fleet rows takes the auditor's usage

    kinds = [d["kind"] for d in detail["dispatches"]]
    assert kinds.count("session") == 1
    assert kinds.count("agent") == 5


@pytest.mark.asyncio
async def test_live_claude_code_agent_with_hooks_gets_no_duplicate_row() -> None:
    """A normal Claude Code session (an OTel Agent tool call span) whose subagent also has
    SubagentStart/Stop hooks for the same agent_id must still get exactly one Timeline row for it: the
    new hooks-only branch's NOT EXISTS guard against `t`/`launched` must suppress its own row here."""
    sid = f"test-16573-no-duplicate-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    try:
        await _interaction(engine, sid)
        await _agent(
            engine, sid, _TODAY + timedelta(minutes=1), "p-a", "tu-a1", "general-purpose", "ag-16573",
            None, (0.3, 4, 4, 4, 4),
        )  # fmt: skip
        await _refresh(engine, _TODAY.date())
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(sid, scope=DETAIL_SCOPE)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    agent_rows = [r for r in rows if r["subagent_type"] == "general-purpose"]
    assert len(agent_rows) == 1, rows  # the hook-only branch must not add a second row for ag-16573
    assert agent_rows[0]["cost_usd"] == pytest.approx(0.3)
```

- [ ] **Step 2: Run both tests to verify the expected pre-fix outcome**

Run: `TEST_LOCAL_PG_URL=<dsn> poetry run pytest -n0 tests/codemie/repository/cli_analytics/postgres/test_live_hooks_only_agent_dispatches.py -v`

Expected:
- `test_live_hooks_only_session_gets_one_agent_row_per_subagent` **FAILS** — `len(agent_rows) == 5` fails
  because the current query returns 0 hook-only agent rows.
- `test_live_claude_code_agent_with_hooks_gets_no_duplicate_row` **PASSES** already — there is no new
  branch yet to produce a duplicate. This is expected and correct for this step; Task 2 must keep it
  passing.

### Task 2: Add the hooks-only agent branch to `get_session_detail_dispatches`

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py:3733-3734` (insert a new `UNION ALL`
  branch between the existing skill hook-fallback branch's closing guard and the slash-command dispatch
  branch's `UNION ALL`).

**Interfaces:**
- Consumes: the already-computed CTEs `t`, `launched`, `stopped`, `agent_usage` and the helper
  `first_subagent_start(alias)` (all defined earlier in the same query, all unchanged).
- Produces: no new interface — the branch emits the same 13-column row shape
  (`subagent_type, skill_name, is_slash_command, dispatch_start, duration_ms, real_duration_ms,
  subagent_duration_ms, cost_usd, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
  models`) the other three branches already produce.

Test-first: yes — makes `test_live_hooks_only_session_gets_one_agent_row_per_subagent` pass (currently
failing per Task 1 Step 2) while keeping `test_live_claude_code_agent_with_hooks_gets_no_duplicate_row`
passing.

- [ ] **Step 1: Insert the new branch**

In `src/codemie/repository/cli_analytics/postgres/reader.py`, immediately after the existing skill
hook-fallback branch's closing guard line (currently ending `... sf.agent_id = h.agent_id))`, right before
the `UNION ALL` that starts the slash-command dispatch branch (currently `-- A slash-command skill
dispatch...`), insert:

```sql
        UNION ALL
        -- A plain agent fork reported only by hooks (agent.subagent.start with no skill name): GitHub
        -- Copilot CLI, and any Claude Code session ingested without OTel. Its run time and cost come by
        -- its typed agent_id, exactly as the skill fallback above's do. Guarded against `t`/`launched` so
        -- a session that also has an OTel Agent tool call for the same agent_id (the first SELECT) never
        -- gets a second row for it. Deliberately not gated on is_claude_code(), unlike the skill fallback
        -- above: Copilot CLI's hook events report a platform other than 'claude-code', and excluding them
        -- would silently drop the exact sessions this branch exists for.
        SELECT h.agent_type, '', 0, h.ts, 0,
               CASE WHEN st.ended_at >= h.ts
                    THEN (extract(epoch FROM st.ended_at - h.ts) * 1000)::bigint END,
               CASE WHEN st.ended_at >= h.ts
                    THEN (extract(epoch FROM st.ended_at - h.ts) * 1000)::bigint END AS subagent_duration_ms,
               au.cost_usd::float8 AS cost_usd, au.input_tokens::bigint AS input_tokens,
               au.output_tokens::bigint AS output_tokens, au.cache_read_tokens::bigint AS cache_read_tokens,
               au.cache_creation_tokens::bigint AS cache_creation_tokens, au.models
        FROM hook_events h
        LEFT JOIN stopped st ON st.agent_id = h.agent_id AND h.agent_id <> ''
        LEFT JOIN agent_usage au ON au.agent_id = h.agent_id AND h.agent_id <> ''
        WHERE h.session_id = $sid::text AND h.event_type = 'agent.subagent.start'
          AND coalesce(h.agent_type, '') <> '' AND coalesce(h.skill_name, '') = ''
          AND {first_subagent_start("h")}
          AND NOT EXISTS (SELECT 1 FROM t JOIN launched la ON la.tool_use_id = t.tool_use_id
                          WHERE t.subagent_type <> '' AND la.agent_id = h.agent_id)
```

Notes for the implementer:
- `{first_subagent_start("h")}` is an f-string expression (this query is built with an f-string), exactly
  like its use in the skill branch — not literal text.
- Do **not** add a `dispatch_fork` guard here: `dispatch_fork` only matches slash-command-dispatched skill
  forks, which cannot exist for a plain agent fork (`skill_name = ''` is already required above), so a
  second guard would be dead code.
- Verify the final column list of this new `SELECT` lines up positionally with the other three branches'
  `SELECT` lists before running the tests — a column shifted by one silently corrupts every row's
  `cost_usd`/token fields instead of raising an error.

- [ ] **Step 2: Run the full test file and verify both tests pass**

Run: `TEST_LOCAL_PG_URL=<dsn> poetry run pytest -n0 tests/codemie/repository/cli_analytics/postgres/test_live_hooks_only_agent_dispatches.py -v`

Expected: both `test_live_hooks_only_session_gets_one_agent_row_per_subagent` and
`test_live_claude_code_agent_with_hooks_gets_no_duplicate_row` **PASS**.

- [ ] **Step 3: Run the two existing sibling live-Postgres test files to confirm no regression**

Run: `TEST_LOCAL_PG_URL=<dsn> poetry run pytest -n0 tests/codemie/repository/cli_analytics/postgres/test_live_skill_tool_attribution.py tests/codemie/repository/cli_analytics/postgres/test_reader_live_copilot_detail.py -v`

Expected: all tests in both files **PASS** unchanged — the new branch is additive and gated to rows no
other branch already produces, so neither file's existing assertions about skill rows, dispatch-fork rows,
or Copilot scalar/tool coverage should move.

---

## Negative-constraint pass

- **Must not exclude Copilot sessions via `is_claude_code()` or any other platform gate** — honored by
  Task 2: the new branch's `WHERE` clause has no `is_claude_code()` call, by design (see the Global
  Constraints note and the branch's own comment); Task 1's `test_live_hooks_only_session_gets_one_agent_row_per_subagent`
  seeds every hook with `platform="copilot-cli"` and asserts all five rows still appear, so a reviewer
  re-adding the gate would be caught by that test going back to failing.
- **Must not duplicate a row for an `agent_id` that already has a `t`/`launched`-sourced OTel row** —
  honored by Task 2's `NOT EXISTS (SELECT 1 FROM t JOIN launched la ...)` guard; verified by Task 1's
  `test_live_claude_code_agent_with_hooks_gets_no_duplicate_row`.
- **Must not touch `agent_call_attribution.py`, the scalars `agent_count` hook-fallback, or the rollup's
  kind-3 hook branch** — none of these files or query regions appear in either task; Task 2 touches only
  `reader.py:3733-3734`'s insertion point inside `get_session_detail_dispatches`.
- **Must not touch the "Active time empty" defect or EPMCDME-16571 (Copilot skills)** — neither `active_ms`
  derivation nor any skill-bar/Copilot-skills code path is touched by either task.
- **Must not add a handler/API/frontend change** — no task modifies `cli_analytics_handler.py` or any file
  outside `reader.py` and the new test file; this is also why `test_live_hooks_only_session_gets_one_agent_row_per_subagent`
  calls `_detail()` (the full handler path) in addition to the raw reader call, to prove the existing
  handler code renders the new rows with no change.
- **Must not include a commit-per-task or whole-suite-gate task** — neither task has a commit step or a
  lint/build/full-test-suite step; both end at "run the directly relevant tests and confirm the result."
- `negative-constraints: none stated` does not apply — the requirements source has the above six, all
  recorded.

## Self-review

- **Coverage:** every item in "Acceptance criteria" maps to an assertion in Task 1's tests, made to pass by
  Task 2 — no requirement lacks a task.
- **Placeholders:** none; both tasks show complete code (full test file content, full SQL block) rather
  than references to "similar" code elsewhere.
- **Type/name consistency:** `PostgresCliAnalyticsReader.get_session_detail_dispatches(session_id, *,
  scope)` is called identically in both tests and matches the existing production signature (unchanged by
  this plan); the new branch's column list matches the 13-column shape named in both tasks' Interfaces
  sections and in the Global Constraints note.
