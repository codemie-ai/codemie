# Code review — EPMCDME-16573 (backend)

reviewed_head: 10bc4a23c182c5959b56bf8b90245398363f7330
base: dcdee987632e0c45099ca99066661ced2194b92e
diff: dcdee9876..10bc4a23c — 2 files, +309/-28 (diff sha256 4ca32f75d0cb81b8871cac2a2902b5cf953983ab82dc4760c4339961146c5b84)
profile: balanced (complexity unknown at review time) · lenses run: edge-case, standards · blind, verification-gap not part of the balanced profile · acceptance n/a (no spec)
verdict: approve · confidence: low (no spec, by design) · 0 blocking · 1 advisory · 1 dismissed
machine verdict: `code-review-final.json`

## Finding

CR-001 (minor, advisory) — `reader.py` `hook_usage` sums `source_cost_usd`, so an agent with both priced and unpriced `session_usage` rows would get a partial total.
Disposition: no change. The row's `cost_usd` is not served: `CliAnalyticsHandler.get_session_detail` replaces it with `dispatch_money(pricer, row)`, computed from the row's per-model tokens (`cli_analytics_handler.py`, `dispatch_rows = [{**row, "cost_usd": dispatch_money(...)}]`), so the partial sum never reaches the Session Detail projection. Optional hardening only.

## Dismissed

A skill start with an empty `tool_use_id` is dropped when the session has any skill TOOL span; this mirrors the skill_count rule by design.

## Evidence at the reviewed revision (synthetic: live Postgres 17, real ingestor, rollup, reader and handler)

- `test_live_hooks_only_agent_dispatches.py` + skill/tool attribution + foreground run + reader + `4652` shapes: 602 passed.
- `test_live_route_pricing.py` + `test_live_foreground_agent_attribution.py`: 61 passed, 2 failed; both failures reproduce on the unmodified `reader.py` on a fresh database (baseline, not caused by this change).
- ruff check and format clean on the changed files. `make test-harness` and live API calls not run; no real Copilot or Cowork session was used.
- Dependency: per-agent usage on Copilot bars needs the `codemie-code` producer fix to send `agent_id` on `agent.usage.request`.
