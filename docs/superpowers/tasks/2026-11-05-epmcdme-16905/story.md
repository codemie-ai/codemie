# Provisioning API: Project Budget Writes — Story

**Date**: 2026-11-05
**Status**: Approved
**Ticket**: EPMCDME-16905
**Parent**: ../../story.md
**Feature**: provisioning-api
**Depends on**: ../clients-and-lookup/story.md

---

## Context

- **Prerequisite:** the clients-and-lookup story provides the provisioning client registry, the permissions `provisioning:manage-projects` and `provisioning:manage-budgets`, the access lock, and the `/v1/provisioning` namespace.
- **Existing logic:** the budget logic from the removed ServiceNow endpoint is implemented and tested. It covers replace, merge, zero-removes-a-category and platform-above-zero, along with find-or-create of the project and cost center, admin promotion, and creating or updating the budget group. All of it uses ServiceNow names.
- **Atomicity gap:** budget validation and a concurrent-creation conflict are checked after the project and admins are saved, so a rejected request can leave a project with new admins but no budget. Budget writes also update LiteLLM, so the call cannot be one transaction; resending completes it.
- **Where the reference is kept:** only in log lines and in the name of a new budget (`ServiceNow <request id>`).
- **Not on the generic API:** the platform guide says PUT is a full replace. Provisioning PUT also creates the parent, and its prerequisite block is add-only.

---

## Complexity

**Size**: L (21/36) · **Recommended flow**: sdlc-standard

The business logic can be reused, but the all-or-nothing guarantee needs work. Checks that run after the first save must move before it. A concurrent-creation conflict after that save must answer 5xx, and a resend must still complete across the LiteLLM sync. About 15 files are touched.

---

## Story

**As a** third-party system integrator (first: EPAM Support Portal on ServiceNow), **I want** to apply a whole approved budget request (set, adjust or cancel a project's budget, creating the project, cost center and admins when needed) in one all-or-nothing call, **so that** I never have to chain calls or clean up half-applied changes.

---

## Background

An approved request must land as one unit: project, cost center, admins and budget. The removed endpoint did this with an `action` field and ServiceNow-specific fields. This story publishes the same behaviour as PUT, PATCH and DELETE on the project's budget under `/v1/provisioning`, in caller-neutral terms. It closes the gap where some checks ran after the first save. It also writes down the provisioning pattern, which the planned user and project endpoints will follow.

---

## Acceptance Criteria

- [ ] **Set a budget for a new project.** Given a registered client holds `provisioning:manage-budgets` and `provisioning:manage-projects`. When it sets a budget for a project that does not exist, passing admins and a cost center, then all of this happens in that one call:
  - the project is created and linked to the cost center, which is created if missing;
  - the listed active users become project admins;
  - the budget is created;
  - the response is 201 and reports the resulting budget, what was created, and which listed admins were not assigned.
- [ ] **Set replaces the budget.** Given a project with a budget, when the client sets a budget with only some categories, then the budget is replaced, categories not sent are removed, and the response is 200.
- [ ] **Adjust merges into the budget.** Given a project with a budget, when the client adjusts one category, then only that category changes, a category sent as 0 is removed, and the others stay. If the result would leave platform at 0, the call fails with 400 and nothing changes.
- [ ] **Cancel removes the budget.** Given a project with a budget, when the client cancels it, then the budget is removed and the response is 204.
  - If the project has no budget: 204, and nothing changes.
  - If the project does not exist: 404, and nothing is created.
- [ ] **Change reference.** Given a set, adjust or cancel request without a change reference, then the call fails with 422 and nothing changes. Given a change reference, then it appears in the provisioning log lines together with the client's name, and a newly created budget is named `<client name> <reference>`.
- [ ] **A rejected request changes nothing.** Given a set or adjust request that fails any business check, when it is sent, then nothing is created or changed, including the project, the cost center and admin assignments. Business checks include:
  - an invalid name for a new project or cost center;
  - a project or cost center that was deleted;
  - a personal project;
  - cost centers being disabled;
  - a broken budget rule.
- [ ] **Resending completes a request.** Given a set or adjust request that stopped after partially applying (including a conflict caused by concurrent creation), when CodeMie answers, then the answer is a 5xx. Every 4xx answer means nothing changed. When the caller sends the same request again after a 5xx or no answer, the final state equals that of an uninterrupted request.
- [ ] **A cost center link is set once.** Given an existing project linked to cost center A, when a set request names cost center B, then the project stays linked to A, is not renamed, and the response shows A. Given an existing project with no cost center, when a set request names cost center B, then the project is linked to B and the response shows B.
- [ ] **Admins are only added.** Given admins that include an unknown email or a service account, when set or adjust runs, then the other people become admins and those two are reported as not assigned. No existing admin is removed. People reported as not assigned become admins only when the request is sent again after they have signed in.
- [ ] **Permissions are checked.** Given a client with `provisioning:manage-budgets` but without `provisioning:manage-projects`:
  - when it sets or adjusts the budget of an existing project and sends no project block, the call succeeds;
  - when the call would create the project or carries a project block (cost center or admins), it fails with 403 and nothing changes;
  - cancel succeeds.

  A client without `provisioning:manage-budgets` gets 403 on set, adjust and cancel.
- [ ] **No vendor names.** Given the published API description, then the budget routes contain no vendor-specific names or fields: no ServiceNow, no EPAM project code, no action field.
- [ ] **The pattern is documented.** Given a developer adds a future provisioning endpoint (for users or projects), then the project guides state the provisioning rules and AGENTS.md points to them. The rules cover:
  - the namespace;
  - the client registry, its permissions, and which registry a new external system belongs in;
  - natural keys in paths, never internal ids;
  - CodeMie terms in bodies;
  - methods and status codes;
  - the change reference;
  - the all-or-nothing contract: every 4xx means nothing changed, 5xx or no answer means send again;
  - the body shape: target state plus add-only prerequisites;
  - contract changes within v1 are additive only.

  The guide links the platform's endpoint conventions and states only where provisioning departs from them. It also states that admins in a prerequisite block are only ever added, and that any future endpoint whose target is the project itself must say whether its admin list replaces or adds.

---

## Out of Scope

- The client registry, access lock and project lookup (the clients-and-lookup story).
- Endpoints that manage users or projects directly.
- Recording the change reference in CodeMie's activity history.
- Assigning listed admins automatically at their first sign-in.
- The ServiceNow-side catalog item and workflow.

---

## Decisions

- D1: One business request → one composite, all-or-nothing call — user (parent D2)
- D2: Change reference → recorded in log lines and a new budget's name only — user (parent D5)
- D3: Methods → set = PUT (201/200), adjust = PATCH (200), cancel = DELETE (204; 404 when the project is missing; never creates). No POST for writes — proposed by PO (parent D6)
- D4: Request shape → the project name goes in the path, amounts are the target state, a nested add-only project block carries cost center and admins, and the change reference is a header. The caller maps its own fields — proposed by PO (parent D7)
- D5: Checks that today run after the first save → move them before it — proposed by PO (parent D10)
- D6: Provisioned projects → no project limit, and the client does not become a member — proposed by PO (parent D11)
- D7: 422 body → the platform's existing validation-error shape, documented — proposed by PO (parent D14)
- D8: What the guide covers → the full rule set and its departures from the platform's PUT rule — proposed by PO (parent D15)
- D9: Partly applied versus nothing changed → 4xx means nothing changed; 5xx (including a concurrent-creation conflict) or no answer means resend — proposed by PO (parent D16)
- D10: Existing project with no cost center → link the named one; an existing link never changes — proposed by PO (parent D17)
- D11: Unassignable admins → reported as "not assigned"; resending after they sign in assigns them — proposed by PO (parent D18)
- D12: Budget-only clients → `provisioning:manage-projects` is required only when the call would create the project or carries a project block — user (parent D19)

---

## Open Questions

- None
