# Spec: Session Detail Deep-Dive

**Branch:** `feature/EPMCDME-13580-code-agent-analytics`
**Repos:** `codemie` (backend), `codemie-ui` (frontend)
**Component:** `CodingAgentsSessionModal` (`codemie-ui/src/pages/analytics/components/codingAgents/`)

---

## Goal

The Coding Agents session detail modal answers "where exactly did this session's cost/time go,
and how did it perform" — not just the top-line total. It's a single scrolling page (no tabs —
see History below for why that changed), organized into two topic groups, documented separately
so this file stays a short index:

- **[spec-cost.md](spec-cost.md)** — Cost Over Time, Cost by Model, Token Composition, Cost by
  Query Source, Cost by Agent/Skill. Everything answering "where did the money/tokens go."
- **[spec-activity.md](spec-activity.md)** — Dispatches, Tool Usage, Daily Lines Changed.
  Everything answering "what did the session actually do."

## Layout (top to bottom)

1. Metadata row (session ID + copy, agent, project, branch, mode, end reason)
2. Stat tiles: Cost, Total Tokens, Duration, Turns, Tool Calls ✓/✗
3. Cost Over Time *(spec-cost.md)*
4. Cost by Model *(spec-cost.md)*
5. Token Composition + Cost by Query Source, side by side *(spec-cost.md)*
6. Cost by Agent / Skill *(spec-cost.md)*
7. Dispatches *(spec-activity.md)*
8. Tool Usage *(spec-activity.md)*
9. Daily Lines Changed *(spec-activity.md)*

Every widget below the stat tiles is conditionally rendered — a session with no signal for a
given widget (e.g. no subagent dispatches) renders nothing for that widget rather than an empty
shell. Nothing here is a placeholder for "coming soon"; a missing widget on a given session means
that session genuinely has no data for it.

## Shared conventions

- **Never fabricate a zero.** `duration_ms: null` (a dispatch/tool-call with no matching stop
  event) renders as `—` or is passed through as `null` into chart data (Chart.js renders a gap,
  not a bar) — never coerced to `0ms`, which would misleadingly imply an instant call.
- **Aggregate over raw logs.** Nothing in this modal lists literally every underlying event
  (hundreds of tool calls, dozens of API requests). Every table/chart here is either already an
  aggregate at the source (e.g. `agent_cost_totals`) or aggregated client-side from a richer
  per-event field (`tool_calls` → Tool Usage). See spec-activity.md for the one place this needed
  its own aggregation function.
- **Prefer Path B over the trace pipeline.** Wherever hook events (`coding_agent_hook_events`,
  written by the `analytics-hook` plugin) and OTel trace spans (`coding_agent_traces`) could both
  answer the same question, Path B wins — the trace pipeline's `subagent_type`/`skill_name`
  attribution is documented as near-zero coverage (~0.4%) in `ANALYTICS_DISCOVERY/`. Traces are
  used only where Path B has no equivalent at all (per-tool-call `duration_ms`, still used inside
  `tool_calls` pairing — see spec-activity.md).

## History (why this doesn't match the original design)

The modal was originally built as 4 tabs (Overview / Dispatches & Errors / Tool Calls /
Performance & Friction) with a raw per-call Tool Call Log, an LLM Request Performance table, and
a Friction (human-wait-time) widget. Direct user feedback after seeing it live changed this
substantially, in order:

1. Tool Call Log (every individual call) → too much — replaced with the aggregated Tool Usage
   widget, tab deleted.
2. LLM Request Performance and Failed Tool Calls tabs → removed outright (redundant granularity
   / low signal once aggregated views existed elsewhere).
3. Tabs themselves → removed once every widget was already a summary, not a raw log — a single
   page reads fine without navigation.
4. Cost by Model, Token Composition, Cost by Query Source, Daily Lines Changed, and a Total
   Tokens stat tile → added, mirroring widgets that already existed in the user-detail modal
   (`CodingAgentsUserModal`), scoped down to one session.
5. Dispatches → converted from a table to a chart once `kind` was confirmed to always be
   `"subagent"` (Path B doesn't yet distinguish skill dispatches) — not worth a column.
6. LLM Request Performance and Friction → dropped entirely (not just reduced) once the cost/token
   angle was covered by Cost by Model + Token Composition, and friction wasn't valued enough to
   keep as its own widget.

This file (and its two topic specs) documents the **current, shipped state** — not the original
plan. Update these files in place for further changes to this modal; don't create a new
dated spec file for an incremental change to something that already has one.
