---
schema_version: 1
id: backend-analytics-read
parent: cli-subscription-classification
repository: codemie
depends_on: [backend-ingest-routing]
---
# Backend: expose subscription type and project origin in analytics — Story

**Date**: 2026-10-27
**Status**: Draft
**Parent**: ../../story.md
**Ticket**: —

---

## Context

- The CLI-analytics reader filters on project only; `LocalAnalyticsFilter` has no subscription or origin filter.
- Non-admins see only their admin projects; unattributed sessions are super-admin only; personal projects have no project admin, so "visible only to the owner" is not modeled.
- Personal projects are already excluded from member spend (`member_spend_service`); CLI-analytics cost comes from priced usage rollups separate from that spend.
- Legacy rows will have empty new fields.

---

## Complexity

**Size**: L (22/36) · **Recommended flow**: sdlc-standard

Privacy boundary (owner-only visibility) and two cost pipelines on the read path; brainstorm first.

---

## Story

**As a** project administrator or analytics viewer, **I want** to see and filter sessions by subscription type and project origin with personal sessions visible only to their owner, **so that** shared-project analytics and spend reflect only appropriate usage.

---

## Background

Stored classification is only useful if the existing analytics scopes show it, filter on it and protect personal usage.

---

## Acceptance Criteria

- [ ] Given stored sessions, when the analytics read endpoints respond, then each session/aggregate exposes subscription type and project origin.
- [ ] Given filters for subscription type and project origin, when applied in both analytics views the endpoints already serve, then results match; legacy rows match "Unknown" and no origin.
- [ ] Given a Personal session, when anyone other than its owner (including a project admin) queries, then it is not returned; when the owner queries, then it is.
- [ ] Given shared-project spend/cost aggregates, when personal sessions exist, then they are excluded from project spend.
- [ ] Given a non-owner requests personal data by filter, when they are an administrator without owner rights, then access follows the author answer for super-admin visibility (negative case).
- [ ] Given Unknown/Corporate/CodeMie sessions, when queried, then project attribution is unchanged.

---

## Out of Scope

- Ingest, gate and storage changes (child `backend-ingest-routing`).
- UI controls (parent D5).

---

## Decisions

- D1: Depends on stored columns and values from `backend-ingest-routing`.

---

## Open Questions

- None.

## Design Work

- Whether super-admins may see personal-project sessions (today unattributed rows are super-admin only) — AC3 says "visible only to that user"; working default owner-only, not posted to the author.
