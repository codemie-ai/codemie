# Spec: Close the Dead/Wasted-cost gap for rejected/failed Write/Edit (EPMCDME-15583)

## Context

CLI Analytics counts a Write or Edit tool call as a successful file write/edit even when the user rejects the operation in the permission dialog (or the call fails during execution) and no file is actually created or modified. This inflates `Files Written` / `Files Edited` / `Files Changed`, and wrongly excludes the affected session from Dead Session / Wasted Cost classification, since `_dead_sessions()` (`src/codemie/service/analytics/handlers/cli_analytics_handler.py`) classifies a session as dead purely on `files_changed == 0`.

Jira: https://jiraeu.epam.com/browse/EPMCDME-15583

## Root cause (two parts)

1. **`session_files_daily` rollup** (`src/codemie/repository/cli_analytics/postgres/rollups.py`, inside the `RECOMPUTE` list) derives `is_written`/`is_edited` purely from the tool-call span (`span_kind = TOOL`) or the `agent.tool.start` hook event matching a Write/Edit tool name — i.e. the call being *made*, not it *succeeding*. A rejected or failed Write/Edit still set these flags true.
2. **`get_file_facts_by_session()`** (`src/codemie/repository/cli_analytics/postgres/reader.py`) computes `files_changed` as an unconditional `count(*)` over every distinct `file_path` row in `session_files_daily`, completely independent of `is_written`/`is_edited`. Even with (1) fixed, a rejected/failed Write/Edit's row still inflates `files_changed`, and `_dead_sessions()` — which keys off `files_changed`, not `files_written`/`files_edited` — stays wrong.

Both parts must be fixed together to satisfy the ticket's acceptance criteria.

## Design

### 1. `session_files_daily` rollup — already drafted, verified, kept as-is

An uncommitted candidate fix already exists on this branch. It adds, for the span branch, a `LEFT JOIN LATERAL` to the matching execution span (`span_kind = _EXEC`, same `tool_use_id`, timestamp within `[-1h, +1d]`) requiring `e.success = 'true'` in the `bool_or` condition that sets `is_written`/`is_edited`. For the hook-event fallback branch (sessions with no span data), it adds a `LEFT JOIN LATERAL` to the `agent.tool.end` hook event (same `tool_use_id`, same window) requiring that match, since `agent.tool.end` — unlike `agent.tool.denied`/`agent.tool.error` — only fires on a call that actually completed.

Technical research independently verified this diff is correct and safe: it faithfully mirrors the existing, production `invocations_hourly` success-join pattern in the same file (identical join keys, identical time window, identical `coalesce`/NULL handling), all referenced helper symbols (`_EXEC`, `_TOOL`, `_SPANS`, `_TOOL_END`, `_HOOK_FILE_PATH`, `_NO_SPAN_OF_KIND`, `_IS_TOOL_START`, `_hook_key`) are correctly defined and used, and there is no fan-out/double-counting risk (the span-branch LATERAL is a bare aggregate guaranteed to return one row; the hook-branch LATERAL has an explicit `LIMIT 1`). No changes needed here beyond keeping this diff.

### 2. `get_file_facts_by_session()` — new fix

In the `fl` CTE, change:

```sql
count(*)                       AS files_changed,
```

to:

```sql
count(*) FILTER (WHERE written OR edited) AS files_changed,
```

(`written`/`edited` are the per-file `bool_or(is_written)`/`bool_or(is_edited)` aliases already computed one CTE up, in `per_file`.) `files_written`/`files_edited` in this same CTE already correctly use `count(*) FILTER (WHERE written)` / `count(*) FILTER (WHERE edited)` and need no change.

This exact expression (`count(*) FILTER (WHERE p.written OR p.edited)`) already exists verbatim elsewhere in `rollups.py` (~line 1010, in a different rollup's fallback computation) — this is an established in-repo pattern being applied to the one place that still lacks it, not new design.

With both fixes in place: a rejected/failed Write/Edit → `is_written`/`is_edited` false → excluded from `files_changed` → `_dead_sessions()` correctly classifies the session as dead when no other file was genuinely touched.

## Testing

No live-Postgres test harness exists anywhere in this repo (confirmed by research — all "DB" tests use hand-rolled fake connections that record SQL text, never execute it; row-content verification is explicitly deferred to a never-implemented "Task 17"). SQL-string assertions are therefore the established ceiling, and all new tests follow that convention:

1. **`tests/codemie/repository/cli_analytics/postgres/test_rollups.py`** — extend the existing `session_files_daily` coverage (in the style of `test_span_facts_statements_read_hooks_for_pairs_without_tool_or_interaction_spans`) with assertions that the generated `session_files_daily` INSERT text contains the success-gating conditions: `e.success = 'true'` on the span branch, and the `agent.tool.end` LATERAL join on the hook-fallback branch. This locks in the already-drafted fix.
2. **`tests/codemie/repository/cli_analytics/postgres/test_reader.py`** — one new assertion that `get_file_facts_by_session()`'s generated SQL contains `count(*) FILTER (WHERE written OR edited) AS files_changed`.
3. **`tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py`** — a direct unit test of the static `LocalAnalyticsHandler._dead_sessions()` method: a session with `cost_usd > 0` and `files_changed == 0` (the rejected/failed case) is classified dead with its cost counted as wasted; a session with `cost_usd > 0` and `files_changed == 1` (a genuine successful write) is not. This pins down the acceptance criterion that only successfully-applied modifications affect Dead/Wasted classification.

## Out of scope

- **EPMCDME-15448** (Read-only tool calls also counted into `files_changed`, same `get_file_facts_by_session()` area) — related, same root predicate, but a separate ticket. The `files_changed` fix above incidentally also excludes Read-only rows as a side effect of computing it correctly (since `written`/`edited` are false for Read-only touches too), but this is a byproduct, not scoped work on that ticket.
- The unrelated local-dev-environment changes already present on this branch (`.env.example`, `config/customer/customer-config.yaml` feature flag, `docker-compose.yml` port remap) — these look like deliberate local setup to reproduce the bug and are left untouched.
- Any change to the client-side (CLI) telemetry emission layer — out of scope per the ticket, which targets the backend rollup/classification layer only.
