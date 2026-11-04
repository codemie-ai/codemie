---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 757878bc472acb7df63907b576b7185f1d61e141
---

# Code review — 2026-11-03-epmcdme-16609-16741-session-detail

**Decision:** approve the code (final round, confidence low: no spec document existed)
**Base:** `4cdd610d51c42836cf117972c83194d96d8c03ea` (origin/main after the rebase; the original review base was `780a7626837275f3c838de297f96897fae5335ef`) · **Reviewed code head:** `757878bc472acb7df63907b576b7185f1d61e141` (the last code/test commit; this file's own commits change no code)

Head mapping (rebased onto `4cdd610d5`; the earlier reviews below are bound to the pre-rebase heads, which were `f839c1874`, `e6ec203a7`, `28df6cf40`, and are not re-run): `113de9709` = `f839c1874`, `31f2a9cba` = `e6ec203a7`, `8b7e9ac22` = `28df6cf40`, `de4a576c9` adds the test adaptation to main's new authorization scope, `757878bc4` adds the denied-scope boundary assertions (tests only).
**Tickets:** EPMCDME-16609, EPMCDME-16741 · **MR:** !4712

This review approves the code of the final diff, with the enterprise OpenAPI guard accepted as unavailable locally. It does not state that every delivery gate passed: the test harness and the full OpenAPI guard are listed under Not covered.

## What was reviewed, and by what

| Revision | Reviewer | Result |
|---|---|---|
| `f839c1874` (base..f839c1874, 6 files) | `code-review` skill, full profile: blind, edge-case, verification-gap, standards (acceptance n/a, no spec) | approve, 0 blocking, 1 deferred, 7 dismissed. Verdict file is bound to this head only. |
| `f839c1874` | independent contract review (rollup grouping and pricing, aggregate tool source, `SessionBody` consumer) | no blocking defect |
| `e6ec203a7` (`f839c1874..e6ec203a7`, 5 files, +177/-56) | independent review of the remediation diff, the rollup `_totals_are_a_source` / `_latest_totals_day` / `_TOTALS_ROWS` and the UI growth filter | no blocking defect |
| `28df6cf40` (`e6ec203a7..28df6cf40`, 3 files) | independent review of the wording correction (field description, reader docstring, snapshot) | no blocking defect |

The skill lenses were not re-run on `e6ec203a7` or `28df6cf40`: the skill verdict had no blocking ids, so no `code-review.check` round was created for the external finding below. The independent reviews above are the evidence for those two revisions.

## Rebase onto main `4cdd610d5`

Main added 16440 (the per-session `detail_gate(scope)` authorization), 16291 (Bedrock model normalization), 16392 and others. Conflicts were in `reader.py` and `cli_analytics_handler.py` only and were resolved by hand, keeping both sides:

- the tools query reads `invocations_hourly` and takes `scope`, with `AND {gate}`;
- the `usage_requests` and `subagent_totals` arms of the events query carry the same `AND {gate}` as the OTel and span arms, so a session the viewer may not see yields no event from any arm;
- the handler keeps main's `normalize_model` on the event model name inside `_session_event`.

Re-run after the rebase (Postgres 17, exit 0): the changed live file, `test_cli_analytics_handler.py`, `test_live_catalog_money.py`, `test_live_costless_sessions.py` and `tests/codemie/rest_api/models`: 590 passed; `make ruff` exit 0; boundary check at `757878bc4` (the live file plus `test_reader_scope_guard.py`: 40 passed, exit 0): on the populated hooks-only, totals-only and 25-tool fixtures the reader returns rows to `DETAIL_SCOPE` (`api_request` and `subagent_totals` events, 20 tools) and no row from the events or tools query to a denied scope (`None`); the FastAPI-generated `LocalAnalyticsSessionEvent` component equals the snapshot entry. The wider tree was not re-run.

## External finding

`ai-review:f-1f496f5b480d` (MR discussion `47e29b05599e93806a3a4a2e1f656d0d0940b99a`, note 2506559): totals-only `session_usage` subagent rows add to COST but had no event, so the Session Detail growth line ended below COST. **Valid. Fixed** in `e6ec203a7` and `28df6cf40`; reply note 2506678; discussion resolved.

Disposition: such an agent now yields one `subagent_totals` event per agent and model, priced through the existing `CatalogPricer` with the stored 5m / 1h cache-write split, at the time of the agent's latest `agent.subagent.usage` hook (the start of the row's UTC day if that hook was purged). It is not an `api_request`: no request count, duration or invented time, and no residual balancing. The costs of all usage events together (requests plus `subagent_totals`), not the requests alone, add up to the session cost for a session with no OTel `api_request` record. A mixed OTel + transcript session lists only its OTel requests (accepted scope, documented in the `get_session_detail_events` docstring).

## Invariants traced

Raw `session_usage` rows → pricing → public event → existing UI summation:

- **Per entity:** each `(agent, model)` totals row maps to exactly one event with that row's tokens and its own model's price; the live test uses two models with different rates and a 5m / 1h split, so a merged or mis-keyed aggregate changes the sum.
- **Aggregate:** the sum of `cost_usd` over events with a cost or tokens (the `SessionBody` `growthData` filter, which ignores `event_type`) equals the session COST for a request-level + totals-only session, a totals-only-only session, and a session whose subagent has per-request usage.
- **No double count:** an agent with its own requests gets no aggregate (`_totals_are_a_source` already excludes it from `session_usage`); the live test asserts only the totals-only agent is listed.
- **Hooks-only requests:** `usage_requests` are grouped as the rollup groups transcript requests (request id, else message id, and raw model; largest value per field), so each request is priced as `cost_daily` prices it.
- **Tools (EPMCDME-16741):** calls, successes and the top-20 list read `invocations_hourly` kind 1, the source of the aggregates; the totals are summed before the LIMIT.
- **OTel / mixed:** a session with OTel `api_request` records keeps exactly those; unchanged.

## Verification evidence

Postgres 17 throwaway container, real rollup refresher, exit codes captured:

- `test_live_session_detail_consistency.py` at `e6ec203a7`: 5 passed (exit 0); the same file against the `f839c1874` sources: 3 failed, 2 passed (exit 1).
- `tests/codemie/repository/cli_analytics` + handler + models at `e6ec203a7`: 1840 passed, 18 failed (exit 1). The 18 are classified below and fail identically on the exact baseline.
- `make ruff` at `28df6cf40`: exit 0. The `LocalAnalyticsSessionEvent` component generated by FastAPI equals the snapshot entry.

The 18 failures, all reproduced on the baseline `780a762` sources (18 failed, 298 passed in the same three files):

| Group | Count | Cause |
|---|---|---|
| `test_dirty.py` calendar-edge dates (0001-01-01, 9999-12-31) and `test_live_dispatch_gate.py` last-calendar-day | 16 | `OSError: [Errno 22]` in `datetime.fromtimestamp` for out-of-range timestamps on Windows (`ns_to_datetime`, `otlp.py`); platform limit, code untouched by this MR |
| `test_settings.py` socket-directory cases | 2 | `AssertionError`: the parsed socket directory is `/var/run` and `''` where `/var/run/postgresql` and `/tmp` are expected; the same on the baseline; likely POSIX-path handling on Windows (not investigated further) |

## Not covered

- The full enterprise OpenAPI guard (`test_openapi_snapshot.py`, `codemie-enterprise` unavailable): UNVERIFIED; accepted by the user, guard not changed or stubbed.
- `make test-harness`: no completed run is attached to this review.
- Mixed sessions list only OTel requests; hooks-only sessions have no `tool_call` timeline events (spans only, baseline behavior); unknown and failed tool outcomes are indistinguishable; Bedrock aliases belong to EPMCDME-16291.
- Complexity score (M, 15/36) is recorded locally only; no Jira write.
