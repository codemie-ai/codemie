# Provisioning API

`/v1/provisioning` serves third-party systems (provisioning clients) that apply an approved business request to CodeMie in one call. It follows `.ai-run/guides/api/endpoint-conventions.md`; this guide states only where it departs from it and the rules every provisioning endpoint adds.

## Namespace And Access

| Rule | Where |
|---|---|
| Every route lives under `/v1/provisioning`, in a resource sub-router of `routers/provisioning/` included by the parent router | `routers/provisioning/__init__.py` (`router.include_router(...)`) |
| The parent router applies `authenticate` then `provisioning_clients_only`; its routes are also the clients' allowlist, so a route added under it is reachable by clients and a client gets 403 anywhere else | `security/provisioning_access.py` (`provisioning_clients_only`, `set_provisioning_routes`, `enforce_provisioning_scope`) |
| The router is mounted only when `PROVISIONING_API_ENABLED` and `ENABLE_USER_MANAGEMENT` are both true | `mount_provisioning_api` |

## Client Registry

| The external system | Registry |
|---|---|
| Calls `/v1/provisioning` with a token of its own CodeMie user | Provisioning client registry: `config/provisioning_clients/provisioning-clients-config.yaml` (`provisioning_clients_config`, keyed by CodeMie user id) |
| Signs its requests with a public key and acts on `allowed_resources` | Authorized applications: `config/authorized_applications/authorized-applications-config.yaml` (`authorized_applications_config`) |

A system calling `/v1/provisioning` always goes in the provisioning client registry, never in authorized applications. A listed client is confined to this API.

| Permission | Lets the client | Grant |
|---|---|---|
| `provisioning:read-projects` | List the shared projects of given people | Any client that needs the lookup |
| `provisioning:manage-projects` | Create shared projects and make any active user an admin of any existing shared project | Only to a client trusted with that reach |
| `provisioning:manage-budgets` | Replace or cancel the budget of every shared project, including one a maintainer set | Only to a client trusted with that reach |

A client that creates projects with budgets needs both management permissions. A new permission is a `ProvisioningPermission` member and a line in the registry file's header comment.

## Permission Checks

| Rule | Where |
|---|---|
| A route declares exactly one permission: `Depends(RequirePermission(...))` | `routers/provisioning/budgets.py`; pinned by `test_every_provisioning_route_declares_one_permission_after_guards` in `tests/codemie/rest_api/routers/test_provisioning_router.py` |
| A permission the target state triggers is checked with `require_permission(client, permission, actor_user_id)` in the service's check phase, before anything is written. Never add a second dependency or a second 403 path | `ProvisioningBudgetService._check_project` requires `manage-projects` when the project is missing or the `project` block names a cost center or admins |
| The route's 403 description names every condition that can produce it | `_WRITE_RESPONSES` in `budgets.py` |

## Paths, Bodies And Methods

| Rule | Notes |
|---|---|
| Natural keys in paths, never internal ids | `/projects/{project_name}/budget`; the name is used exactly as sent, 3-100 characters, else 422 (`ProvisioningProjectName`). Names are never trimmed or case-folded before a permission or lookup decision (`security-patterns.md`). Emails are `EmailStr`, which returns email-validator's normalized address: surrounding spaces and a `Name <...>` display name are dropped and the domain is lower-cased, while the local part keeps its case (except RFC 2142 role names such as `postmaster`). Case variants of one email count once, as first sent (`ProvisioningProjectBlock`) |
| CodeMie terms in bodies, descriptions and help texts | No vendor names, no caller-specific fields; the caller maps its own fields |
| Body = target state plus an optional add-only prerequisite block | `amounts` is the target; `project` (`cost_center`, `admin_emails`) only adds what the target needs |
| Unknown body fields are rejected with 422 | `ConfigDict(extra="forbid")` on `ProvisioningBudgetRequest` and `ProvisioningProjectBlock`; a misspelt `admin_emails` would otherwise skip admins silently |
| 422 is documented in the platform's validation-error shape | `ProvisioningValidationErrorResponse` |

| Method | Meaning | Status |
|---|---|---|
| PUT | Set the target state. Creates the parent (the project) when missing | 201 when the call created the target, 200 when it replaced one |
| PATCH | Adjust: merge into the target, creating what is missing | 200 |
| DELETE | Cancel the target. Never creates; no target is not an error | 204; 404 when the parent is missing |
| POST | Not used for writes | |

Departures from `endpoint-conventions.md` (Update Semantics (PUT)): PUT creates the parent, the prerequisite block is add-only, PUT answers 201 or 200, and unknown fields are rejected (generic request models ignore them).

Admins in a prerequisite block are only ever added, never removed or replaced. People without an active, non-service-account user are reported as `admins_not_assigned` and are added when the request is sent again after they sign in. A future endpoint whose target is the project itself must say in its description whether its admin list replaces or adds.

A cost center in a prerequisite block is linked only while the project has no active one: a link to a deleted cost center counts as no link, so the named cost center is linked, while a link to an active cost center never changes.

## Change Reference

Every write carries the required `X-Change-Reference` header (1-64 characters; missing or empty is 422). It is recorded only in log lines (`change_reference=` next to `client=`, `domain=provisioning`) and in the name of a newly created budget (`<client name> <reference>`); a client name has at most 35 characters, so the 100-character name always holds the whole reference. It is not in the activity history.

## All-Or-Nothing Contract

A write cannot be one transaction because it also syncs an external provider, so the contract is made of ordering and resending.

| Phase | Does | Failure answers |
|---|---|---|
| Check (read-only) | Raises every deterministic 4xx: lookup, permissions, names, cost center, budget rules | 4xx; nothing changed |
| Commit 1 | Project, cost center, admins; find-or-create | 5xx |
| Commit 2 | The target (budget group) and the provider sync | A 4xx or an unexpected error here becomes 500 `partly applied; send the same request again`; a provider failure during the write passes the provider's own 502 through; a category left unsynced after the commit is 502 `partly applied; send the same request again` |

| Rule | Where |
|---|---|
| Every 4xx means nothing changed. 5xx or no answer means send the same request again | Every write route's description says so |
| A resend converges because every write is find-or-create. The resend may answer 200 instead of 201 and `created_project: false` | `ProvisioningService.resolve_project`, `ProvisioningBudgetService._commit_budget` |
| A check that depends on commit 1 cannot answer 4xx. Put each deterministic check in the check phase; anything after commit 1 answers 5xx | `ProvisioningBudgetService._check`, `_commit_budget_after_project` |
| The cause of a 5xx is logged with `client`, `change_reference` and the project; the 500 body carries only the generic message | `_log_partly_applied` |

## Versioning

Within `/v1` a contract change is additive only: a new route, a new optional request field, a new response field, a new permission. Never remove, rename or retype a field, tighten validation, or change a status code; that needs a new version. The OpenAPI baseline changes with any of it; see Router Contract Guards in `.ai-run/guides/testing/testing-api-patterns.md`.

Evidence: `src/codemie/rest_api/routers/provisioning/budgets.py` (routes, descriptions, responses), `src/codemie/rest_api/models/provisioning.py` (models), `src/codemie/service/provisioning_budget_service.py` (check phase, commits, partly-applied 5xx), `src/codemie/service/provisioning_service.py` (project steps), `src/codemie/configs/provisioning_clients_config.py` (registry, permissions).
