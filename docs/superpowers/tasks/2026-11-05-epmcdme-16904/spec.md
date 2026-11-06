# Spec: Provisioning API — Clients, Access Lock and Member Lookup (EPMCDME-16904)

Requirements: [story.md](story.md) (D1–D8). Research: [technical-analysis.md](technical-analysis.md). Size L. Next: EPMCDME-16905 (budget writes) builds on this.

## Why

Third-party access is hard-wired to one ServiceNow account (`src/codemie/rest_api/security/servicenow_access.py:45-81`). Onboarding a second system would need new code. One config registry of provisioning clients and one access lock replace it, and the member lookup moves to `/v1/provisioning`.

## Design

1. **Client registry.** A YAML file, loaded once at startup like `AuthorizedApplicationsConfig` (`src/codemie/configs/authorized_apps_config.py:95-128`), always loaded and enforced whatever `PROVISIONING_API_ENABLED` says. A client is matched by its `user_id`, which is the CodeMie user id, i.e. the token subject (`servicenow_access.py:17-19`). Permissions come from a closed set of three. Startup fails, and the error names the entry by position and name, on any of these: invalid YAML, a missing file, a missing or empty `name` or `user_id`, a `user_id` that is not a UUID, an unknown permission, or a duplicate `name` or `user_id`. An empty `permissions` list is valid. The repo ships the file with an empty list. A new setting, `PROVISIONING_CLIENTS_CONFIG_DIR`, defaults to `config/provisioning_clients`, following `AUTHORIZED_APPS_CONFIG_DIR` (`config.py:131`).

```yaml
# <PROVISIONING_CLIENTS_CONFIG_DIR>/provisioning-clients-config.yaml
provisioning_clients:
  - name: support-portal          # used in logs (and, in 16905, budget names)
    user_id: "<CodeMie user id (UUID) / token sub>"
    permissions: [provisioning:read-projects]   # also: provisioning:manage-projects, provisioning:manage-budgets
```

**Lifecycle** (also stated in the shipped file's header). The lock confines only listed ids, so deleting an entry releases the account. Onboarding: the entry reaches every workload before the third party receives its credentials. Offboarding: disable the client's IdP client or deactivate its CodeMie user first, then set its `permissions` to `[]`. A change is applied by a rollout restart, right after the edit, of every workload that runs the API image: api, ds-pool, proxy-pool (`deploy-templates/templates/deployment.yaml`, `ds-pool/deployment-ds-pool.yaml`, `proxy-pool/deployment-proxy-pool.yaml`). A bad file then stalls the rollout while the old pods keep serving. A revocation that cannot wait deactivates the CodeMie user (401 at `authentication_service.py:560-561`, within the 30 s auth cache, `config.py:320`) or disables its IdP client.

2. **Access lock (external branch only).** The `enforce_servicenow_scope` call in `authenticate()`'s external branch (`authentication.py:155`) is replaced. The new check returns 403 when a registered client calls any route outside the provisioning router. The allowed set is the (method, route path) pairs of the parent provisioning router, which include every sub-router, so routes added in 16905 are covered without edits. The internal `bind_key` branch (`:123-133`) is unchanged (D7).
3. **Clients only, plus per-route permission.** A guard on the parent provisioning router answers 403 to any caller that is not a registered client, admins included. Each route also declares the one permission it needs, and a client without it gets 403. Both guards run after `authenticate`. The new access module replaces `servicenow_access.py` and `servicenow_integration_access_only` (`authentication.py:246-258`).
4. **Service accounts.** `is_service_account` (`user_type_validator.py:45-48`) is true when `user_type == "service_account"` or the user id is a registered account. That reaches the lookup, admin assignment (`servicenow_integration_service.py:516`) and Teams sender resolution (`authentication_service.py:625`). The personal-project exclusion (`user_type_validator.py:41-42`) reads only the stored type, which for a client-credentials client is `regular` (`servicenow_access.py:17-19`, `authentication_service.py:208`). It also counts registered clients through `is_service_account`, so none gets a personal project on authentication or on an email change (`authentication_service.py:395`, `personal_project_service.py:125`). Registration and local login keep the stored-type check (`registration_service.py:214,251`, `authentication_service.py:787`): accounts created there are not IdP clients.
5. **Lookup.** New router package `routers/provisioning/` with its parent `APIRouter` in `__init__.py` (`routers/projects/__init__.py:22-31`), tagged "Provisioning". It serves `GET /v1/provisioning/projects?member_email=…`, which needs `provisioning:read-projects`. The parameter is repeatable. Each value is parsed by `EmailStr` with no normalization of our own before parsing (security-patterns.md Untrusted Input Handling; decided at code review, CR-003), and matched case-insensitively by `user_repository.get_by_emails`. The logic of `servicenow_integration_service.py:384-395` moves into a provisioning service. Behaviour stays as today (`servicenow_integration.py:138-159`): the response is `[{project_name, display_name}]`, the union sorted by name; unknown and service-account emails are skipped; no match gives `[]`; a missing or invalid email gives 422.
6. **Wiring.** The router is mounted only when `PROVISIONING_API_ENABLED` and `ENABLE_USER_MANAGEMENT` are both true. If the flag is on but user management is off, the router is not mounted and a warning is logged. If the flag is on and the registry is empty, a warning is logged (`main.py:1511-1523` precedent). Logs use `domain=provisioning` and carry the client name.
7. **Removals.** These go: the ServiceNow router and its tests, `servicenow_access.py` and its tests, the router-only models, `SERVICENOW_INTEGRATION_ENABLED` and `SERVICENOW_SERVICE_ACCOUNT_ID` (`config.py:262-263`), and their uses in `main.py`, `guarded_app.py:25,55` (switched to the new flag) and `mock_config`. The OpenAPI baseline is regenerated. The budget logic in `servicenow_integration_service.py`, with what it uses and its service tests, stays in place, unrouted, for 16905 to move.

## Acceptance criteria

- AC1: A client holding `provisioning:read-projects` that calls `GET /v1/provisioning/projects` with repeated `member_email` gets 200 and the union of those people's shared projects, sorted by name. Unknown and service-account emails are skipped; no match gives `[]`; no or an invalid `member_email` gives 422.
- AC2: A registered client without `provisioning:read-projects` gets 403 on the lookup.
- AC3: A registered client calling a route outside the provisioning router on the external branch gets 403. Every route of the parent provisioning router passes the lock. The internal `bind_key` calls behave as before.
- AC4: A caller that is not a registered client, including an admin, gets 403 on every `/v1/provisioning` route.
- AC5: A client added only in the registry file can, after a rollout restart of every workload that runs the API image (api, ds-pool, proxy-pool), call exactly the routes its permissions allow.
- AC6: Startup fails, naming the entry, on invalid YAML, a missing file, a missing or empty `name` or `user_id`, a `user_id` that is not a UUID, an unknown permission, or a duplicate `name` or `user_id`.
- AC7: `is_service_account` is true for a registered account whatever its stored `user_type` and whatever the flag. This holds in the lookup, in admin assignment and in Teams sender resolution (the test at `test_authentication_service.py:1921` is updated). It stays false for an unregistered regular user. No personal project is created for a registered client on its first call or on an email change.
- AC8: With the flag off or user management off, `/v1/provisioning/*` gives 404. `/v1/integrations/servicenow/*` gives 404 in every configuration, and no `SERVICENOW_INTEGRATION_ENABLED` or `SERVICENOW_SERVICE_ACCOUNT_ID` reference remains in `src/` or `tests/`.
- AC9: The regenerated OpenAPI baseline has `/v1/provisioning/projects` and no `/v1/integrations/servicenow` path. The lookup's tag, description, parameters and fields carry no vendor names.
- AC10: `src/codemie_tools/itsm/servicenow/` is unchanged.
- AC11: A test over every route of the parent provisioning router asserts that each declares exactly one permission from the closed set, and that `authenticate` runs before both guards.

## Non-goals

- Budget write endpoints, the change reference, and the provisioning pattern guide (EPMCDME-16905).
- Moving or renaming the budget logic in `servicenow_integration_service.py`.
- Checking `provisioning:manage-projects` or `provisioning:manage-budgets` on any route; the registry only accepts them.
- The Teams bot service account, `teams_authentication_resolver.py`, and the authorized-applications registry.
- The ServiceNow agent tool and its settings.
- An admin UI, or reloading the registry without a restart.
- Locking the internal `bind_key` branch.
- Restating Barracuda's ServiceNow role for provisioning clients (requester follow-up, parent D20).

## Decisions

- Match clients by user id (token subject) over username — how a token maps to a client; same as `servicenow_access.py:45-50`.
- Use their own YAML file under `PROVISIONING_CLIENTS_CONFIG_DIR` over an env-var list — where the registry lives; mirrors authorized applications.
- Enforce the registry whatever `PROVISIONING_API_ENABLED` says over today's flag-gated check — what switching the API off does to a registered account (fail closed; story ACs 3 and 6 are unconditional).
- Fail startup on a duplicate `name` or `user_id` over merging the entries — how ambiguous entries are handled.
- Allow an empty `permissions` list over rejecting it — whether a client can stay registered, and locked in, with no grants.
- Fail startup on a missing registry file over treating it as empty — fail closed, same as authorized applications.
- Start with an empty registry and the flag on, with a warning, over failing — `main.py:1518-1522` precedent.
- Build the lock allowlist from all routes of the parent provisioning router over a path-prefix match — keeps method+route matching (`servicenow_access.py:53-70`) and covers sub-routers.
- Put the clients-only guard on the parent router over a guard per route — future routes cannot omit it.
- Keep the budget service unrouted over deleting it — 16905's Context says that logic is "implemented and tested", and story AC6's admin assignment lives at `servicenow_integration_service.py:516`.
- Keep the lookup response `[{project_name, display_name}]` and its 422 rules over a new shape — D6 "same behaviour as today".
- Log with `domain=provisioning` and the client name over the user id alone.
- Name the registry key `user_id` over `account_id` — what the key holds; `account_id` already names the Atlassian OAuth id and an OTel attribute here (technical-analysis, Facts Added by Review).
- Fail startup on a `user_id` that is not a UUID over accepting any non-empty id — how a pasted IdP client name is caught; the router mounts only with user management on (`main.py:1511-1523`), where every user id is a UUID (`authentication_service.py:545`).
- Offboard by disabling the IdP client or deactivating the CodeMie user, then emptying `permissions`, over deleting the entry — how a client leaves the registry; an unlisted id is not confined.
- Apply a registry change by an immediate rollout restart of api, ds-pool and proxy-pool over waiting for the next restart — when an edit takes effect and where a bad file surfaces.
- Exclude registered clients from personal projects through `is_service_account` over the stored type alone — story D5 "wherever service accounts are excluded"; registration and local login keep the type check.
- Catch a route that omits its permission with a test over every route of the parent router over a runtime deny in the parent guard — how 16905's routes are kept from failing open.

## Assumptions and risks

- Assumption: a client account's CodeMie user id equals its token subject (`servicenow_access.py:17-19`).
- The budget logic cannot be reached between this change and 16905.
- Barracuda's planned design still names the removed ServiceNow account, settings and guard (parent AR-2). The requester owns the follow-up.
- Teams sender resolution now treats registered accounts as service accounts.
- Admin user lists filtered by type still show registered clients by their stored type, `regular` (low).
