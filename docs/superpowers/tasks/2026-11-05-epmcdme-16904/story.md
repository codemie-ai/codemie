# Provisioning API: Clients, Access Lock and Member Lookup — Story

**Date**: 2026-11-05
**Status**: Approved
**Ticket**: EPMCDME-16904
**Parent**: ../../story.md
**Feature**: provisioning-api

---

## Context

- **Current endpoints:** CodeMie's only inbound API for third-party systems is `/v1/integrations/servicenow`. It has a lookup of the shared projects of given people and one budget endpoint.
- **Access today:** one ServiceNow service account id from configuration. A dedicated check keeps that account inside its two endpoints, and a special case counts it as a service account everywhere, including Teams sender resolution.
- **Authorized applications** are a separate registry. It identifies apps by username and exists so those apps can receive datasource credentials encrypted to their own key.
- **No callers yet:** nothing calls the ServiceNow endpoints, so they can be removed outright. Until the budget-writes story ships, CodeMie has no budget endpoint for third-party systems.

---

## Complexity

**Size**: L (23/36) · **Recommended flow**: sdlc-standard

It changes authentication and authorization: the `authenticate()` scope hook, the route guard, and `is_service_account`, which Teams also uses. It adds a permission-code registry that no existing pattern covers. About 17–21 files are touched.

---

## Story

**As a** CodeMie platform admin onboarding a third-party system, **I want** to register the system with exactly the provisioning permissions it needs, through configuration alone, **so that** it can look up projects (and, after the next story, manage budgets) without any vendor-specific code, and cannot reach anything else in CodeMie.

---

## Background

Access for third-party systems is hard-wired to one ServiceNow account. Adding a second system would mean new settings, new access checks and another special case in how CodeMie recognises service accounts. This story replaces all of that with one registry of provisioning clients and one access lock, and moves the project lookup to `/v1/provisioning`. The budget writes in the next story rely on it.

---

## Acceptance Criteria

- [ ] **Project lookup.** Given the provisioning API is enabled and a registered client holds `provisioning:read-projects`, when it looks up projects for one or more member emails (repeatable `member_email`), then it receives the union of those people's shared (non-personal) projects, sorted by name. Unknown emails are skipped; no match returns an empty list.
- [ ] **Lookup needs its permission.** Given a registered client without `provisioning:read-projects`, when it looks up projects, then it gets 403.
- [ ] **Clients stay inside the provisioning API, and only clients get in.** Given a registered client, when it calls any CodeMie route outside `/v1/provisioning` from outside CodeMie, then it gets 403. Given any caller that is not a registered client, including CodeMie admins, when it calls a `/v1/provisioning` route, then it gets 403.
- [ ] **New clients need only configuration.** Given a platform admin adds a client (name, account, permissions) to the client registry, when CodeMie restarts, then that client can call exactly the routes its permissions allow, with no code change.
- [ ] **A broken registry fails loudly.** Given a registry entry with an unknown permission name or a missing account, when CodeMie starts, then it does not start and the error names the entry.
- [ ] **Clients count as service accounts.** Given a registered client, whatever user type is stored for its account, when any part of CodeMie that excludes service accounts checks it (project lookup, admin assignment, Teams sender resolution), then it is treated as a service account.
- [ ] **Switched off means absent.** Given the provisioning API is switched off, or user management is off, when anyone calls a `/v1/provisioning` route, then it does not exist (404). `/v1/integrations/servicenow/*` and the ServiceNow-specific access settings no longer exist in any configuration.
- [ ] **No vendor names.** Given the published API description, then the lookup route contains no vendor-specific names or fields.

---

## Out of Scope

- The budget write endpoints, the change reference, and the provisioning pattern guide (the budget-writes story).
- The Teams bot service account and the authorized-applications registry.
- The ServiceNow agent tool (Table API tool) and its integration settings.
- An admin UI for managing provisioning clients.
- Restating Barracuda's planned ServiceNow role for provisioning clients. This is a follow-up owned by the requester (parent D20).

---

## Decisions

- D1: Namespace → `/v1/provisioning` — user (parent D1)
- D2: Old ServiceNow endpoints → removed outright, with no compatibility routes — user (parent D3)
- D3: Permission names → `provisioning:read-projects`, `provisioning:manage-projects`, `provisioning:manage-budgets`. This story checks only `provisioning:read-projects`; the registry accepts all three — user (parent D20)
- D4: Where clients are registered → their own list in configuration, separate from authorized applications. Authorized applications are for apps that receive datasource credentials encrypted to their key — proposed by PO (parent D8)
- D5: Registered clients count as service accounts wherever service accounts are excluded — proposed by PO (parent D9)
- D6: Lookup parameter → `member_email`, repeatable, union of results — proposed by PO (parent D12)
- D7: Access lock → applies to externally authenticated calls; CodeMie's internal signed calls are unchanged — proposed by PO (parent D13)
- D8: Invalid registry → CodeMie refuses to start and names the bad entry, the same way the authorized-applications registry fails on invalid configuration — proposed by PO: fail closed for an access list

---

## Open Questions

- None
