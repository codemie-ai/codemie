# Spec: Provisioning API — Project Budget Writes (EPMCDME-16905)

Requirements: [story.md](story.md) (12 ACs, D1–D12, Out of Scope). Research: [technical-analysis.md](technical-analysis.md). Size L. Builds on EPMCDME-16904. `svc` = `src/codemie/service/servicenow_integration_service.py`; `pbs` = `src/codemie/service/budget/project_budget_service.py`.

## Why

The budget logic sits unrouted in `svc` under ServiceNow names. Two checks run after its first commit (`pbs:1928-1944`, re-plan `svc:360`), so a rejected request can leave a project and admins with no budget.

## Approach

Keep the two-commit write (`svc:228-243`). Commit 1 is sync and covers the project, cost center and admins. Commit 2 is async and covers the budget group and LiteLLM. All-or-nothing holds by contract: a read-only check phase raises every deterministic 4xx, and any failure after commit 1 is a 5xx. Every write is find-or-create, so a resend converges. Ruled out: one transaction (story Context), and compensating deletes, which add writes that can fail; D9 chose resend.

## Design

1. **Routes.** Sub-router `routers/provisioning/budgets.py` is included in the parent (`routers/provisioning/__init__.py:39`), so the lock and guard cover it (`provisioning_access.py:46-76`). It serves `PUT|PATCH|DELETE /v1/provisioning/projects/{project_name}/budget`, each declaring only `RequirePermission(MANAGE_BUDGETS)`. Requests carry a required `X-Change-Reference` header of 1–64 chars. The path name, the cost center and the admin emails are used exactly as sent: no trimming or case folding (CR-002, `security-patterns.md`). The name and cost center are 3–100 chars, else 422; admin emails are plain `EmailStr`. OpenAPI documents the 422 in the platform shape (`main.py:1806-1814`, D7).
2. **Body** (PUT and PATCH; DELETE has none). Unknown fields → 422.
   ```json
   {"amounts": {"platform": 500, "cli": 100, "premium_models": 50},
    "project": {"cost_center": "eng-123", "admin_emails": ["requester@example.com"]}}
   ```
   `amounts` keeps today's rules (`models/servicenow_integration.py:70-77,97-100`); PUT needs `platform` > 0. `project` is optional.
3. **Responses.** PUT and PATCH return today's result shape (`docs/superpowers/tasks/2026-10-19-servicenow-budget-integration/spec.md:80-102`) plus `created_budget`, with `admins_pending` renamed `admins_not_assigned`. PUT answers 201 when it created the budget and 200 otherwise (`routers/conversation/history.py:261`). PATCH answers 200. DELETE answers 204, also when there is no budget. A missing project is 404, a deleted one 409, a personal one 403 (`svc:284-311`).
4. **Move.** `svc` is moved with `git mv` to `service/provisioning_budget_service.py` (`ProvisioningBudgetService`), which keeps amounts, group writes and cancel. The project steps (name and cost-center checks, D10 link, find-or-create, admin promotion; `svc:264-282,383-523`) become public in-session functions of `service/provisioning_service.py`, whose docstring becomes "project operations of the provisioning API". The models go to `rest_api/models/provisioning.py`, `models/servicenow_integration.py` is deleted, and the tests move with them. Business rules are unchanged (`svc:76-145`).
5. **Check phase** (read-only, in this order):
   1. Project lookup: deleted 409, personal 403.
   2. D12. If the project is missing, or the block names a cost center or an admin, the client also needs `provisioning:manage-projects`. A new public `require_permission(client, permission)` in `provisioning_access.py` checks it, and `RequirePermission.__call__` (`:85-94`) delegates to it, so the 403 body and log line are the same. The routes' 403 description names this condition. Cancel never needs it.
   3. New project: valid name. A named cost center must have the feature on, a valid name, and not be deleted (`svc:264-282`).
   4. Existing unlinked project with a cost center named: the same checks (D10). Existing link to an active cost center: the named one is ignored. A link to a deleted cost center counts as no link (CR-011).
   5. The exact group create or update request is built here (a failure is 400) and passed to one new public, read-only `pbs` check: duration, categories, per-budget constraints, and each existing category's active member fixed limits against the planned max and the derived soft limit (`pbs:1202-1218`). `pbs`'s group create and update run the same check before their first write.
6. **Write phase.** Commit 1 (`svc:383-523`) creates the cost center and project, or links an unlinked one through a new in-session `ProjectService` method. That method sets `cost_center_id` only while it is empty or points to a deleted cost center, records `PROJECT_UPDATED` and returns the link actually stored, which the response reports. Commit 1 then promotes admins; the action label names the provisioning route. Commit 2 locks the existing group row and re-plans against its current amounts (as `svc:356-360` does), then creates or updates the group. A new group is named `f"{client.name} {reference}"[:100]` (D2). A 4xx raised after commit 1 becomes 500 "partly applied; send the same request again". A 500's `details` is replaced (`main.py:1565-1572`), so the cause is logged with `client`, `change_reference` and the 500's request id. LiteLLM's 502 is kept (`pbs:657-661`). After commit 2, any category whose stored sync status is not ok (`svc:339`) answers 502 with the same message. Two changes let a resend repair that category:
   - the adapter's update rebuilds a missing provider reference from project and category (`budget_provider_adapter.py:890-897`);
   - `pbs` syncs a category only when its effective max, soft limit or duration changed, its last sync did not end synced (status outside `SYNCED_STATUSES` = `ok`/`noop`, the same set the provisioning 502 check uses, so a `pending` budget is retried too), or a member push of it failed (allocation marked `failed`) (`pbs:704-714`).
7. **Logs**: `domain=provisioning`, with `client=` and `change_reference=` on every line. The `servicenow_*` events are renamed `provisioning_*` (`svc:174-200,402-436`).
8. **No vendor names** in routes, models, descriptions or help texts (`svc:66`, `cost_center_service.py:102`).
9. **Docs.**
   - A new `.ai-run/guides/api/provisioning-api.md` covers the story's "pattern is documented" list. It links `endpoint-conventions.md` and states only where provisioning departs from it.
   - Registry rule: systems calling `/v1/provisioning` go in the provisioning client registry. Signed-request apps stay in authorized applications.
   - The guide and the package docstring (`routers/provisioning/__init__.py:15-20`) state: one declared permission per route, and a permission the target state triggers is checked with `require_permission` in the check phase.
   - AGENTS.md gets a Guide Imports row and a Task Classifier row. `endpoint-conventions.md:27-29` gets a pointer.
   - The OpenAPI baseline is regenerated.

## Acceptance criteria

- AC1–AC12: the story's twelve criteria, in order, as written.
- AC13: PUT gives 201 when it created the budget, also on an existing project with no budget, and 200 when it replaced one. PATCH gives 200 even when it created the project. Both report `created_budget`.
- AC14: A test forces a 4xx after commit 1, through concurrent group creation or a group deleted between commits. The answer is 500, commit 1's rows remain, and a resend reaches the uninterrupted state.
- AC15: A LiteLLM failure on a new budget's second category gives 502. A resend creates the budget with every category.
- AC16: An unknown field, missing or empty `amounts`, a negative or non-finite amount, or a PUT with `platform` ≤ 0 gives 422 in the platform shape, as documented in OpenAPI.
- AC17: Each budget route declares exactly `provisioning:manage-budgets`, and the 16904 one-permission test passes. A budget-only client whose block has no cost center and no admins succeeds on an existing project.
- AC18: No `servicenow_integration` module, `ServiceNowBudget*` or `ServiceNowIntegration*` name, or `servicenow_*` log event remains in `src/codemie` or `tests/codemie`. ITSM ServiceNow names stay unchanged: `src/codemie_tools/itsm/servicenow/`, its settings mapping and tester, and their test fixtures.
- AC19: An adjust that takes a category below its members' fixed limits answers 400, and no row and no LiteLLM key changes.
- AC20: A forced key-generation failure on an existing budget answers 502. The resend leaves the key present and the provider reference stored. Resending an already-applied request makes no LiteLLM call.

## Non-goals

- The story's Out of Scope list.
- Changing the provider sync beyond Design 6's two changes, or cleaning up LiteLLM budgets left by a failed attempt.
- Compensating rollback of commit 1.
- Changing the routes, bodies or status codes of `/v1/admin/project-budget-groups`, or existing public `pbs` signatures. The admin API's group writes do gain the 5.5 check and the 6 sync rule.
- Re-linking a link to an active cost center, renaming a project, removing admins.
- A project limit or creator membership for provisioned projects (D6).

## Decisions

- Check first, then two commits, with 5xx after the first write, over one transaction or compensating deletes — how D1/D9 are met.
- New `service/provisioning_budget_service.py` for amounts, group writes and cancel, over merging into `provisioning_service.py` — where the budget logic lives.
- Project steps as public in-session functions of `provisioning_service.py`, over private budget-service methods — the planned project endpoint reuses them (AR-5).
- New sub-router `budgets.py` over extending `projects.py` — where the routes live.
- Body names `amounts`, `project.cost_center`, `project.admin_emails` over new names — public field naming.
- Honour a named cost center even when it equals the project name, over the old equal-means-none rule (`svc:269,464`) — story AC1; the old rule came from the default project name.
- A block counts for D12 only when it names a cost center or an admin, over mere presence — what "carries a project block" means.
- Check `manage-projects` right after the project lookup, over after the other checks — which error wins.
- A public `require_permission` that `RequirePermission` delegates to, over a second 403 path in the service — one home for provisioning 403s (AR-4).
- One public read-only plan check in `pbs`, shared by the check phase and `pbs`'s group writes, over copying its rules — how D5 covers every budget rule (AR-1).
- Reject unknown body fields over ignoring them — a misspelt `admin_emails` would skip admins silently.
- PUT 201 means the budget was created, over the project was created — the URL names the budget.
- Add `created_budget` to the response over the status code alone — PATCH is always 200 (D3).
- Answer a 4xx after commit 1 with 500 over passing it through — D9.
- 502 on a failed category sync that a resend repairs, over documenting that a 200 may be false — how D9 holds on an existing budget (AR-2).
- Keep today's response fields, renaming `admins_pending` to `admins_not_assigned`, over a new shape — D11 wording.
- Change reference of 1–64 chars over another limit — same as today's `request_id`.
- Run cost-center checks only when a cost center would be created or linked, over always — an ignored name needs no check.
- Link an unlinked existing project on PUT and PATCH, over PUT only — the block means the same on both.
- Link through a conditional in-session `ProjectService` method that records `PROJECT_UPDATED`, over a direct field write — D10 under a concurrent link (AR-3).
- Commit 2 re-plans against the locked group's current amounts, over the check-time plan — story AC3 under a concurrent edit (AR-3).
- New guide file plus two AGENTS.md rows over extending `endpoint-conventions.md` — where the pattern is documented.

## Risks

- A mid-category failure leaves unreferenced LiteLLM budgets, because `budget_id` has a random suffix (`pbs:1969,2027`); this already happens today. A resend still converges CodeMie's state.
- A resend after a partial write reports `created_project: false` and may give 200 where the first try would have given 201.
- Check-to-write races surface as 500 and are completed by a resend.
- Commit 2 holds the group row lock through the LiteLLM calls, so a concurrent write to the same project waits.
- A failed spend carry on a changed category restarts its period spend at 0 and still answers 2xx (best-effort carry, adapter :290-328).
