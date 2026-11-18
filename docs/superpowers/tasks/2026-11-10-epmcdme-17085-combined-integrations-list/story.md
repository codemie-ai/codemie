---
schema_version: 1
id: codemie-combined-list
parent: integrations-unified-list
repository: codemie
depends_on: []
---

# Combined Integrations List (backend) — Story

**Date**: 2026-11-10
**Status**: Approved
**Ticket**: EPMCDME-17085
**Parent**: ../../story.md

---

## Context

- Integrations are listed through two paged settings lists, one per scope. Each applies its own visibility rules: the User list returns only the person's own integrations and hides system-created internal settings; the Project list returns only projects the person administers (every project for platform admins) and hides project-budget LiteLLM integrations from anyone but platform admins and maintainers. Both are sorted newest first and return a page plus a total.
- No list returns both scopes together, and neither accepts a scope filter. Filters accepted today are search, project, created by, type and Global; unknown filter keys are silently ignored.
- The "available settings" list used by assistant and data source pickers returns both scopes, but without paging or filters and with member (not admin) project visibility, so it does not fit a page list.
- Each stored integration already records its scope, and permission checks for creating, changing and deleting Project integrations stay per project.

---

## Complexity

**Size**: M (16/36) · **Recommended flow**: sdlc-standard

One paged, newest-first query across both scopes that reuses the existing per-scope visibility branches; API, service and a read-only query in about 4–6 files, no schema change.

---

## Story

**As a** CodeMie user opening the Integrations page, **I want** one list that returns my User and Project integrations together, filtered by scope, sorted and paged **so that** the page can show both scopes in one table with correct page numbers and totals.

---

## Background

The unified Integrations page (parent story) replaces the User/Project tabs with one table and a Scope filter. Paging and newest-first sorting across both scopes cannot be done correctly from two separately paged lists, so the page needs one list that serves both scopes while keeping exactly today's rules about who sees what.

---

## Acceptance Criteria

- [ ] Given a person requests the combined list with Scope All, then integrations of both scopes come back as one list sorted newest first, paged, with a total that counts both scopes.
- [ ] Given the combined list with Scope All, then for each scope it holds exactly the rows today's per-scope lists return to that person, with their exclusions (system-created internal settings; project-budget LiteLLM integrations for anyone but platform admins and maintainers).
- [ ] Given a request with Scope User or Scope Project, then the result has the same rows, order and total as today's per-scope list for that person.
- [ ] Given no scope is sent, then the list behaves as Scope All.
- [ ] Given the filters the page offers today (search, project, type, Global, Created by) are sent together with a scope, then they narrow the result the same way they narrow today's per-scope lists.
- [ ] Given every row in the result, then it states its scope (User or Project) and, for User rows, whether it is Global.
- [ ] Given a member who administers no project requests Scope Project, then the result is empty and contains no other person's or project's integrations.
- [ ] Given a project admin requests Scope All, then project-budget LiteLLM integrations of the projects they administer are not included.
- [ ] Given an unknown scope value, then the request is rejected with a validation error naming the allowed values.
- [ ] Given the admin project page keeps using today's per-scope lists, then their results are unchanged by this story.

---

## Out of Scope

- Any page, filter or form change (child `codemie-ui-unified-list`).
- Changes to creating, updating or deleting integrations, and to their permission checks.
- The Schedulers list (separate follow-up story).

---

## Decisions

- D2: How is "All" scopes served? → A new combined list in `codemie`: both scopes in one date-sorted, paged result with a scope filter — user (parent D2)
- D11: Does the combined list restate who sees what? → No: it applies the visibility rules of the existing settings lists, with scope as an optional filter (All by default); the per-scope lists stay and return the same rows as Scope User and Scope Project — proposed by the architecture review; user approved the parent story with it (parent D11)

---

## Open Questions

- None

---

## Constraints

- Lands first: `codemie-ui-unified-list` reads the Integrations page from this list (producer → consumer).
- Contract produced for `codemie-ui`: an optional scope filter (All, User, Project), today's filters, paging and total, newest first, and per-row scope and Global flag.
- The per-scope visibility rules keep one home; the combined list reuses them rather than restating them (D11).
