---
schema_version: 1
id: backend-ingest-routing
parent: cli-subscription-classification
repository: codemie
depends_on: []
---
# Backend: classify and route CLI analytics sessions — Story

**Date**: 2026-10-27
**Status**: Draft
**Parent**: ../../story.md
**Ticket**: —

---

## Context

- The ingest gate requires a project header, discards absent/unknown/inaccessible projects or disabled projects with a final HTTP 200, and never infers the default project.
- Personal and default projects exist but are not consulted by the gate; personal projects have no analytics-enabled flag set.
- Admin-editable settings exist through dynamic customer config; no corporate-organization list exists.
- Stored sessions have a typed project column only; new typed columns are a contract change.
- The gate files are already modified in the working copy on the EPMCDME-16025 branch.

---

## Complexity

**Size**: L (25/36) · **Recommended flow**: sdlc-standard

Security/policy-sensitive gate reversal, storage migration and a new contract; open policy decisions need brainstorming.

---

## Story

**As an** installation administrator, **I want** the platform to classify each CLI session by subscription type and choose its destination project, **so that** work usage reaches work projects, personal usage stays with the user, and nothing is silently lost.

---

## Background

The CLI can only send evidence; the platform knows the corporate-organization list, the user's personal and default projects and the consent and access policy.

---

## Acceptance Criteria

Classification is evaluated in this order, first match wins, so the cases are mutually exclusive (parent D7):

1. Evidence that the model is routed through CodeMie → **CodeMie** (regardless of organization or list).
2. Otherwise, organization missing/unreadable/unknown, or the stored corporate list absent or empty → **Unknown**, never Personal. (Backend always has its stored policy; "no usable policy" is a CLI-side indeterminate state, not a backend classification.)
3. Otherwise, organization matches the list → **Corporate**; no built-in organizations exist.
4. Otherwise (list non-empty, organization known and not in it) → **Personal**.

- [ ] Given evidence that the model is routed through CodeMie, when a session arrives, then it is stored as CodeMie even if the organization is in or out of the list, or the list is empty.
- [ ] Given no corporate list is configured (absent or empty), when a non-CodeMie session arrives with any known organization, then it is stored as Unknown, never Personal (negative case).
- [ ] Given a non-CodeMie session whose organization is missing or unreadable, when it arrives (list configured or not), then it is stored as Unknown, never Personal.
- [ ] Given an admin saved a non-empty list and the organization matches it, when a non-CodeMie session arrives, then it is stored as Corporate.
- [ ] Given a non-empty list and a known organization not in it, when a non-CodeMie session arrives, then it is stored as Personal and routed to the signed-in user's personal project.
- [ ] Given a Corporate/CodeMie/Unknown session whose folder is linked to a project the user can access, via a local `.codemie` link or a repository registered to a project, when it arrives, then it is stored there with origin "linked folder" (the registration lookup is a design gap, see Open Questions).
- [ ] Given such a session with neither link, when it arrives, then it goes to the user's default project with origin "default project" and the marker "folder not linked to a project".
- [ ] Given the session owner, when stored, then the owner is the authenticated CodeMie user, never the Claude account.
- [ ] Given a launch mode codemie-claude (settled D2), when the subscription is not Personal, then the profile project is kept; when Personal, then it routes to the user's personal project.
- [ ] Given the personal or default destination is missing, provisioning fails, or membership/consent/opt-out/access disallows storage, when a session arrives, then the outcome is explicit and observable using the existing provisioning/retry/error patterns (retryable while provisioning can succeed; a permanent failure is observable), the session is never silently discarded, and a Personal session is never stored in a shared project (negative case, D10).
- [ ] Given a personal project, when analytics is ingested for its owner, then ingestion is allowed without per-person administrator enablement, subject to consent, opt-out and owner-only access, while the `PERSONAL_CLI_ANALYTICS` rule that forbids enabling CLI analytics on personal projects is replaced accordingly; shared-project `cli_analytics_enabled` behavior is unchanged (D11).
- [ ] Given an authenticated CLI user, when it reads the classification policy, then it receives the corporate organization list (empty when none configured) with a version, no secrets, and the backend re-derives the stored classification from evidence plus this policy even if the client sent a different value (D3-rev, for 16396 pre-forwarding suppression).
- [ ] Given an ingest request reporting a client policy version older than the current one and a client classification that differs from the backend-derived one, when received, then the backend stores nothing for it and answers with a retryable stale-policy response carrying the current policy version, so the CLI can refresh, re-evaluate its opt-out and resend; a request is never finally discarded for this reason (negative case).
- [ ] Given matching policy versions but differing classifications, or an older version with the same classification, when received, then the backend stores its own derived value, records the client-reported value and policy version for diagnostics, and does not reject.
- [ ] Given a backend-derived Personal result, when the request is stored, then it goes only to the user's personal destination, never a shared project, so a stale client can never cause Personal data to land in a shared project.
- [ ] Given a CLI that sends none of the new evidence, when a session arrives, then behavior is unchanged and legacy rows read as Unknown with no origin.

---

## Out of Scope

- Analytics API response fields, filters, visibility and spend (child `backend-analytics-read`).
- CLI evidence collection (child `cli-classification-routing`).

---

## Decisions

- D1: Backend resolves classification and destination from CLI evidence — proposed by PO (parent D3).

---

## Open Questions

- None.

## Design Work

- Gate and failure mode for personal/default destinations: settled by parent D10/D11 (the Jira questions were withdrawn); remaining work is engineering design inside this child.
- Profile-origin value for AC8 — design decision for this child (not posted to the author); working default: add a "profile" origin.
- **Design gap — repository-to-project registration (parent AC4):** no server-side mapping from a repository identity to a project was found in source (searched `codemie` and `codemie-code`, 2026-10-27); only the local `.codemie` workspace link exists. This child owns the resolution: sessions carry a repository identity from the CLI, and the backend resolves a registered project for it. Whether to build, reuse or defer the registration store/lookup is unresolved — needs source/design analysis in this child, not posted to the author. Until answered, `.codemie` linkage is covered and registration is explicitly not dropped.
