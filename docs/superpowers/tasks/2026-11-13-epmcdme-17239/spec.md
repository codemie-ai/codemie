# EPMCDME-17239 — Let every manager share a hosted app: backend

## Problem

A hosted app's managers (creator, project admin of the app's project, platform admin, maintainer) already
edit it, make versions live and share it with the whole project, through one rule: SQL `access.manageable`
(`src/codemie/core/hosted_applications/access.py:189-223`) and `Ability` WRITE (`src/codemie/core/ability.py:208-213`).
Named sharing does not follow that rule. Share, unshare, shared-with, people search and share-link
get/create are creator-only, checked twice: in `HostedCatalog._owned_app` (`src/codemie/service/hosted_applications/catalog.py:564-588`)
and in the generic owner check that the hosted descriptor feeds (`named_grant_service._check_owner`,
`share_link_service.acreate`/`arevoke_checked`, through hosted `is_owner` at `grant_descriptor.py:70-73`).
Link revoke and single-grant delete also allow platform admins and maintainers, but not project admins
(`share_link_descriptor.py:47-50`, `access.grant_remover` at `access.py:237-246`). `can_share` is true for the
creator only (`projections.py:54`).

## Goal

For hosted apps only, one named rule decides every sharing operation: **the caller is a sharing manager** —
an eligible creator, project admin of the app's project, platform admin or maintainer. It is defined once in
`access.py` as a SQL term plus a Python twin, equal to today's manager term without its grant branch
(`access.py:189-199`, `MANAGER_GRANT_ACTIONS` at `access.py:42-45`), so it does not follow the WRITE term when
edit grants join it. Assistants keep the owner rule.

## Behaviour

- **Gate.** Share, unshare, shared-with, people search, share-link get, create and revoke admit every sharing
  manager of the app, whoever made the existing grants or link. The catalog gate locks the app only for a sharing
  manager; the catalog's post-lock Python filter (`catalog.py:582-587`) and its `allow`/`refusal` parameters are
  removed. The hosted descriptor's `is_owner`, read by the generic services, is the Python twin of the same rule.
  The 403/404 split stays as it is (`catalog.py:571-588`): a caller who can read the app but not manage it gets
  403, anyone else 404.
- **Single-grant delete** (`DELETE /v1/permissions/{id}`). The hosted descriptor's `may_remove` is: the app row
  exists and the caller is a sharing manager of it, or no app row exists and the caller is an eligible platform
  admin or maintainer (the existing `_eligible_user` and `_platform_admin` terms, `access.py:166-186`) — orphan
  grants keep today's rule. A refusal is answered as today: `PermissionAccessDenied`, which the generic permissions
  route turns into 401 "Access denied" (`routers/permission.py:74-75` → `routers/utils.py:58-65`).
- **One rule (D9).** Link revoke and single-grant delete use the sharing rule. Removed, as left with no consumer:
  hosted `share_link_descriptor.may_revoke`, `access.grant_remover`/`is_grant_remover` and their tests, the generic
  `ShareLinkDescriptor.may_revoke` field (`share_link_types.py:52-53`) and its fallback in `arevoke_checked`
  (`share_link_service.py:204`, which then reads the descriptor's `is_owner` only), and the docstrings that describe
  the dropped rule (`is_moderator`, `access.py:232`; `share_link_types.py:21`).
- **Python and SQL agree.** The sharing rule's Python twin gives the same answer as its SQL term for every persona
  (creator, project admin of the app's project, admin of another project, platform admin, maintainer, member,
  recipient with any grant level, outsider), on a `platform_user` copy (`access.py:58-86`) so roles come from the
  `users`/`user_projects` rows, never from the request. The catalog gate, the descriptor's `is_owner`, the app branch
  of `may_remove` and `can_share` all read this rule and no other.
- **Creator as recipient.** People search for a hosted app leaves out the app's creator. A grant request from
  a manager who is not the creator that names the creator is refused as a whole with 400 and a message saying
  the creator already has access; nothing is inserted. A creator naming themselves is still dropped silently
  as the acting user (`named_grant_service.py:327-336`). People search leaves everyone else in, including the
  caller, as today.
- **Suspended app.** Share and link create are refused as today; unshare, shared-with, link get and link
  revoke work for every manager.
- **`can_share`** is true exactly when the reader is a sharing manager of the app, suspended or not.
- **Activity log.** Grant and link events record the calling manager as actor (already the caller, see
  `named_grant_service.py:357-391`, `share_link_service.py:77-100`); this is asserted for a non-creator manager.
- **Refusal text.** The hosted refusal for a non-manager names the managers, replacing "Only the owner can share
  or revoke access" and "Only the owner or a platform administrator can revoke the share link" with one message:
  "Only the app's managers (its creator, admins of its project, platform admins and maintainers) can share it or
  manage its access". The seven "Owner only" route descriptions (`rest_api/routers/hosted_applications.py:244-311`)
  and the OpenAPI baseline say the same.
- **Assistants.** The generic default owner rule and message, the assistant descriptor
  (`assistant/grant_descriptor.py`) and `assistant_share_service._require_owner` are untouched.
- **Design records.** `docs/hosted-applications/architecture.md` (roles table, "Named grants and share links")
  and the hosted design spec (`docs/superpowers/specs/2026-11-06-EPMCDME-15659-hosted-apps-design.md`: roles and
  access tables, §6.1 refusal, API table, `can_share`, D14, D17, S-17, rejected alternatives) state the manager
  rule; D14, D17 and S-17 are marked superseded by EPMCDME-17239 instead of being deleted.

## Acceptance criteria

- AC1: Each manager (creator, project admin of the app's project, platform admin, maintainer) succeeds on share,
  unshare, shared-with, people search, share-link get, create and revoke, including on grants and links another
  manager made; the activity log records that manager as actor.
- AC2: Each manager may delete a single grant on the app through `DELETE /v1/permissions/{id}`; anyone else is
  refused as today (401 "Access denied" from the generic permissions route); a grant whose app no longer exists is
  removable by platform admins and maintainers only.
- AC3: `can_share` is true for every manager and false for everyone else.
- AC4: A project member who is not an admin, a recipient, a marketplace viewer and an admin of another project
  are refused on every AC1 operation with 403 when they can read the app and 404 otherwise, and on AC2 as today
  (401 "Access denied" from the generic permissions route).
- AC5: People search for a hosted app never returns its creator; a non-creator manager's grant naming the
  creator is refused with 400, a message saying the creator already has access, and no rows written.
- AC6: On a suspended app, share and link create are refused as today for every manager; unshare, shared-with,
  link get and link revoke succeed.
- AC7: Assistant share, unshare, shared-with and link operations by a non-owner are refused with today's message;
  existing assistant sharing tests pass unchanged.
- AC8: Hosted refusals to non-managers carry the managers message; the seven route descriptions and
  `tests/codemie/rest_api/router_guards/baselines/openapi.json` carry the same wording, with no "Owner only" left.
- AC9: The sharing rule's Python twin agrees with its SQL term across the persona table, including the row "a
  recipient with any grant level is not a sharing manager".
- AC10: The two hosted design records state the manager rule and mark D14, D17 and S-17 superseded.

## Non-goals

- Any UI change (child `manager-sharing-ui`).
- New access levels, notifications or a per-app history view.
- Any change to who counts as a manager.
- Excluding the acting user from people search in the backend.
- Removing grants the creator may already hold, or any data migration.
- Changing `POST /v1/permissions` (still refuses all named grants) or the generic delete refusal shape.

## Decisions

- Removable orphan grants (app row gone) stay platform admin/maintainer only, over admitting nobody or anyone with a stale link — what does the manager rule mean without an app.
- A grant request naming the creator is refused whole with 400, over dropping the creator silently — what "refused" means for a batch.
- The creator naming themselves stays a silent drop as the acting user, over refusing it — whether AC5 changes the creator's own path.
- 400 for the creator refusal, over 409 — matching `_check_users`' 400 for a non-sharable user (`named_grant_service.py:345-354`).
- People search excludes only the creator, over also excluding the caller — AC5 asks for the creator; the actor exclusion the ticket describes is client-side and unverified.
- One managers message for every hosted sharing refusal, over keeping a separate link-revoke message — D9 leaves one rule, so one message.
- `can_share` ignores suspension, over false on suspended apps — today's flag ignores it and unshare/revoke still work there.
- Rule symbols left without a consumer (hosted and generic `may_revoke` with its `arevoke_checked` fallback, `grant_remover`/`is_grant_remover`) are removed, over kept unused beside the sharing rule.
- Superseded design decisions are marked superseded in place, over deleted.
- Sharing stays with the platform-role managers when edit grants exist, over following the WRITE term — the ticket names the managers.
- One named sharing rule (SQL term plus Python twin) read by every sharing check, over each check writing its own manager expression — which manager term sharing reads.
- The catalog's post-lock Python filter is removed, over keeping it as a second check after the lock — whether the catalog re-checks what its gate decided.
- The ticket's 403 for AC2 is read as "as today", over changing the generic delete refusal that assistants share — what status a refused single-grant delete gets.

## Risks

- PG-only tests (`test_share_links_pg.py`, `test_access_matrix.py`, `test_named_grant_service_pg.py`) skip without `HOSTED_TEST_DATABASE_URL`.
- The OpenAPI snapshot test is `enterprise`-marked and regenerated by hand.
