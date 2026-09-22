# Handoff: frontend — `client_source` analytics filter

**Audience:** Frontend agent/team consuming `/v1/analytics/*`
**Backend branch:** analytics-router changes are currently **uncommitted** on the working tree
(implemented, tested, ruff-clean — not yet reviewed/committed/merged). Diff scope: 20 files,
`src/codemie/rest_api/routers/analytics.py` + `src/codemie/service/analytics/**` + tests.

## What's new

A new optional query parameter, **`client_source`**, is available on 15 `/v1/analytics`
GET endpoints. It filters results to usage that originated from a specific client, backed by
the `attributes.client_source` field now written on `conversation_assistant_usage` metrics
(see the companion Teams-bot handoff doc for how that field gets populated).

```
GET /v1/analytics/summaries?client_source=teams&time_period=last_hour&page=0&per_page=10
```

## Contract

| | |
|---|---|
| Param name | `client_source` |
| Type | enum query string |
| Allowed values | `platform`, `teams`, `other` |
| Default | unset — **no filtering applied**, all client sources included (this matches the current, pre-change behavior — nothing breaks if the frontend never sends this param) |
| Invalid value | FastAPI returns `422` automatically (enum-typed, not a free string) |

This follows the exact same shape as the existing `users`/`projects` filters on these
endpoints — it's just another optional query param, no new auth or pagination behavior.

## Endpoints with the filter

All under `/v1/analytics`:

- `/summaries`
- `/engagement/weekly-histogram`
- `/spending/by-users/platform`
- `/spending/by-users/cli`
- `/projects-spending`
- `/users-spending`
- `/llms-usage`
- `/cli-llms`
- `/power-users`
- `/top-agents-usage`
- `/top-workflow-usage`
- `/assistants-chats`
- `/workflows`
- `/published-to-marketplace`
- `/webhooks-invocation`

**Not covered** (out of scope for this change — ask backend if you need one of these too):
`/users`, `/users-unique-daily`, `/projects-unique-daily`, `/cli-summary`, the
`cli-insights-user-*` detail/drilldown group, the `cli-insights-by-enriched-user-*` group, and
all `/ai-adoption-*` POST endpoints.

## Response shape

Where these endpoints already return a `filters_applied` metadata block (reflecting active
`users`/`projects` filters back to the caller), `client_source` is included there too when set,
following the same convention. When unset, it's simply absent from `filters_applied` — no
placeholder/null entry.

## Known issue — not caused by this change

`/assistants-chats` was observed erroring in a screenshot prior to this work. The implementing
agent reviewed `AssistantHandler.get_assistants_chats` and its error-handling wrapper and found
no obvious defect introduced by this change, but could not reproduce the error without the
original screenshot/repro steps. **If `/assistants-chats` is still erroring after this change
ships, treat it as pre-existing and separate from the `client_source` filter** — flag it back to
backend with a screenshot/network trace for a dedicated investigation.

## Status

Implementation is done and tests pass (839 passed, 8 skipped in the narrow analytics scope;
`ruff check`/`ruff format --check` clean). **Nothing has been committed or reviewed yet** — this
doc describes the contract as implemented so frontend work can start in parallel, but the exact
shape could still change during code review before merge. Check back before shipping frontend
code that depends on this.
