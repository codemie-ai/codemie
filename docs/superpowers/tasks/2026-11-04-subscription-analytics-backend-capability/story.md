---
schema_version: 1
id: subscription-analytics-backend-capability
parent: subscription-analytics-private-sessions
repository: codemie
depends_on: []
---
# Backend: explicit-connect routing, funding-route dimension, aggregates and missing-price flag — Story

**Date**: 2026-11-04
**Status**: Approved
**Ticket**: EPMCDME-16849 (umbrella)
**Parent**: ../../story.md
**Feature**: subscription-analytics

---

## Complexity

**Size**: L (25/36) · **Recommended flow**: sdlc-standard

One repository, high technical risk in routing/privacy and new aggregate dimension; one point under XL.

---

## Story

**As a** project manager, **I want** the backend to accept an explicit-connect signal and a funding-route field, route connected personal-funded work sessions to the project, and expose API-based and subscription consumption separately, **so that** project analytics include subscription-funded work without exposing private sessions.

---

## Background

Backend (origin/main 25a34585) always routes personal-funded sessions of personal-storage users to the personal project, and has no funding-route dimension or aggregate-level incomplete-price indicator. The capability must land without changing behaviour for existing clients. It deploys first; the CLI child activates it later.

---

## Acceptance Criteria

- [ ] AC1 Given a new-contract session with a valid explicit-connect signal for a project where the user has access and analytics enabled, when ingested, then it is stored under that project and follows project permissions including conversation content, whatever its funding class.
- [ ] AC2/AC3 Given a new-contract personal session without a valid connect signal, when ingested, then it goes to owner-only personal storage where supported; where unsupported it is suspended retryably and never placed in a shared or default project; no personal project is provisioned for unsupported users; it never appears in work-project lists, details or aggregates (all user types).
- [ ] AC5 Given sessions with funding route api_based, subscription or unknown, when consumption is queried through the real analytics reader, then the buckets are returned separately by project, user, repository and model, with corporate/personal/unknown classification still distinguishable; unproven or contradictory input lands in unknown.
- [ ] AC6 Given personally funded consumption in a project, when the project budget spend source is read, then it is excluded and its estimate is available separately. The actual budget consumer is inspected first; existing exclusion is reused and evidenced, not re-implemented.
- [ ] AC7 Given a model with no catalog price, when estimates are returned, then the API marks the aggregate incomplete and does not present cost 0 as free; existing pricing metadata is reused.
- [ ] AC9/AC11 Given an old client, old headers without the new signal, historical sessions or existing bindings, when the capability is deployed, then routing, visibility and binding are unchanged; the session's chosen publication contract is persisted and checked at binding.
- [ ] AC-neg Given a contradictory, malformed or unvalidated client assertion, when ingested, then it never grants visibility and the funding route stays unknown.

---

## Out of Scope

- CLI changes; Control Plane, UI, ticket attribution, Cost per Point; relocation of existing history; a personal toggle.

---

## Decisions

- Inherits parent D1 (origin/main baselines) and D5–D8.
- Contract (draft): new explicit-connect signal and funding-route field (api_based | subscription | unknown); capability advertisement so the CLI can negotiate; exact names and compatibility defined here.
- The contract is not frozen until the CLI child's spike (session_id, cwd and project across /clear, resume, folder change) has reported; the spike is research inside the CLI child, not a new stage. Deployment order stays backend support first.
- Preserve dirty `cli_analytics_project_gate.py` and its tests; deliver from an isolated worktree on origin/main.
- Budget evidence: `budget_usage_service`/`member_spend_service` (analytics handlers) are the apparent consumers and sit outside the CLI-analytics rollups; confirm before claiming AC6.

---

## Open Questions

- None
