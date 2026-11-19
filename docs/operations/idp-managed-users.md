# IdP-managed users

In IdP-managed mode the identity provider owns users, the platform admin role and project membership. The platform
exposes a management API under `/v1/idp-sync/` that an external job calls to bring the identity provider state into
the platform. This repository ships the API and the switch. It does not ship the job or a scheduler: the job
lives in the customer repository.

This guide is written and verified for Keycloak. Other identity providers are covered by a short note in
section 2; that setup has not been verified.

Besides users, project memberships and the admin flag, the API carries the maintainer and auditor flags
(`is_maintainer`, `is_auditor`). Which Keycloak realm roles map to them is a setting of the job, not of the
platform.

## 1. The `IDP_MANAGED_ENABLED` switch

Set `IDP_MANAGED_ENABLED=true` to turn the mode on.

- The platform does not start with `IDP_PROVIDER=local`: that provider accepts only platform tokens, so the sync job
  cannot authenticate.
- The switch turns user management (`ENABLE_USER_MANAGEMENT`) on, even if you left it off.
- Requests that change users, admin status or project membership through the platform return 403.
- The provisioning API (`/v1/provisioning`) refuses, with the same 403, a request that would create a project or
  assign project admins. Budgets and cost centers of existing projects keep working.
- Reads and user budgets keep working.
- The administrator UI shows the user type and the maintainer and auditor flags as read-only.
- The mode is shown in the public configuration, so the UI hides the editing controls for: creating, deactivating
  and reactivating users; assigning users to a project, removing them and changing their project role, including
  bulk actions and import; creating and deleting projects; changing the project owner. Budget actions stay
  available.
- The setting is read at deploy time. Change it in the environment and restart the API.

## 2. Create the identity provider client for the management API

1. In the Keycloak realm, create a client for the sync job.
2. Set client authentication to on, so the client is confidential.
3. Use the client credentials grant (in Keycloak, enable service accounts).
4. Make the access token of the service account carry these claims. A service account has no `name` by
   default, so add a hardcoded-claim mapper for it:

   | Claim | Value |
   |---|---|
   | `sub` | the Keycloak id of the service account user (the platform looks the user up by it) |
   | `preferred_username` | `service-account-<client-id>`; must equal `IDP_SYNC_SERVICE_ACCOUNT_USERNAME`, the same on every environment |
   | `name` | a display name (hardcoded-claim mapper) |
   | `realm_access.roles` | the realm roles of the service account |

   The platform refuses the API to any token whose user name differs from `IDP_SYNC_SERVICE_ACCOUNT_USERNAME`.
   The other claims are read when the platform builds the user from the token; without them the call can fail
   with 401 or 403.
5. Store the client id and client secret in the secret store of the system that runs the job.

Other identity providers: with the OIDC and Entra ID plugins the platform takes the user name from the `email`
claim, and the token must carry `email` and `groups`. A client-credentials token usually has neither, so
`IDP_SYNC_SERVICE_ACCOUNT_USERNAME` would have to equal the email claim of that token. This setup has not been verified.

## 3. Allow the client in the platform

1. Take the user name of the service account: `service-account-<client-id>`, the `preferred_username` claim
   of its token.
2. Set it as `IDP_SYNC_SERVICE_ACCOUNT_USERNAME`.
3. Restart the API.

Only this account may call `/v1/idp-sync/`, and it may call nothing else. An empty
`IDP_SYNC_SERVICE_ACCOUNT_USERNAME` closes the management API to everyone.

## 4. Rollout order

1. Run the sync job in dry-run mode. It logs the changes it would make and writes nothing.
2. Review the log. Fix the realm data or the job settings until the planned changes are correct.
3. Run the first live sync.
4. Check users, admin role and project membership in the platform.
5. Set `IDP_MANAGED_ENABLED=true` and restart the API.

Do not switch the mode on before the first live sync. Changes through the platform are refused from that
moment on, so the platform must already hold the Keycloak state.

If a step fails: after step 1 or 2, nothing was written, so fix the settings and repeat. After step 3 or 4,
suspend the job, correct the realm data and run the sync again. Do not do step 5 until steps 3 and 4 are
clean. If step 5 was already done, follow section 6.

## 5. Scheduling

Scheduling lives in the customer-side job. This repository ships no scheduler, CronJob or sync image.
Run the job from your own scheduler, with the credentials of the client from section 2, and point it at the
management API address reached through your ingress or auth proxy.

## 6. Disable and rollback

1. Suspend the customer-side job first. Stopping it stops all changes at once.
2. Set `IDP_MANAGED_ENABLED=false` and restart the API. Do this only after the job is suspended, otherwise
   the next run undoes the changes made in the platform.
3. Users the sync created or changed stay as they are.

## 7. Security precondition: how the token is verified

The platform runs in one of two modes. Check which one your deployment uses before the first run.

**`JWKS_VALIDATION_ENABLED=true`.** The platform verifies the signature of the `Authorization: Bearer` token
against the issuers in `JWKS_TRUSTED_ISSUERS`, and only then reads the claims. Make sure the Keycloak realm of
the sync client is listed there with the right audience. A token from any other issuer is rejected with 401.
An empty or invalid `JWKS_TRUSTED_ISSUERS` also gives 401 for every request, so check the config first if
the job gets 401 on a correct token.

**`JWKS_VALIDATION_ENABLED=false` (default).** The platform does not verify the signature. It trusts the
`x-auth-request-access-token` header set by the auth proxy in front of it, so the proxy is the only control.
If the backend can be reached around the proxy, anyone can forge the sync account. Before the first run,
check all of these:

1. A request through the ingress or proxy with a valid token succeeds, for example
   `curl -H "Authorization: Bearer $TOKEN" "$API_URL/v1/idp-sync/users?per_page=1"` returns 200.
2. The same request with a forged `x-auth-request-access-token` header and no valid token is rejected. With
   Keycloak and `JWKS_VALIDATION_ENABLED=false` this header is what the platform trusts. With the other identity
   providers the platform reads `Authorization: Bearer`, so forge that header instead.
3. A direct call to the backend service, bypassing the proxy, returns 401.
4. The backend has no network path other than through the proxy.

If check 2 or 3 fails, do not enable the mode and do not run the job. Enabling `JWKS_VALIDATION_ENABLED`
removes this dependency on the proxy.

## 8. What the job has to handle

- `PUT /v1/idp-sync/users/{id}` takes an optional `user_type` (`regular` or `external`; `service_account` is
  refused with 422). An omitted value leaves the stored type alone, and a new user is `regular`. The responses
  report it.
- `POST /v1/idp-sync/users/{id}/deactivate` refuses the last active administrator and the last active maintainer
  with 409, as a demotion does. Treat it as a refusal for that one user, not for the whole run. The sync
  account can deactivate a maintainer.
- A project name with a space at the start or the end is refused with 422 on assign and unassign alike.
- The personal project of a user is named by the user email. In the mode the platform creates it at login only
  when the IdP lists a project with that name for the user; the project then becomes the personal one, as outside
  the mode. The job assigns it with `PUT /v1/idp-sync/users/{id}/projects/{email}`, which creates the project when
  it is missing or turns a project of that name without other members into the personal one, and removes the user
  from it with `DELETE`; the project itself stays. Another user's personal project and a project with other
  members are refused with 409.
- A new user gets the default budget, and a membership change re-splits the equal member shares of the project.
