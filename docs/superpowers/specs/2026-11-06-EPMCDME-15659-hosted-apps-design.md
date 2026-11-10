# Hosted applications: solution specification

**Ticket**: EPMCDME-15659 · **Merge requests**: codemie !4432 (backend; remote branch `EPMCDME-15659`, local branch
`EPMCDME-15659_hosted-apps-share-links`) and codemie-ui !2044 (frontend; branch `EPMCDME-15659_hosted-apps-ui`)
**Status**: describes the code as built, as of 2026-11-07. Backend head: the commit that carries this revision of the
document (parent `be91b761c`). UI head: `6055206f3`.

This document describes the final solution only. Section 14 lists the decisions in effect, section 15 the
alternatives that were rejected. Decision IDs are stable labels; a gap in a numbered series is a decision that no
longer applies.

Related documents kept in the repository and not repeated here:

- `docs/hosted-applications/architecture.md`: isolation design (content gateway, per-version origins, launch
  tickets, viewing sessions, validator and scanner, storage, lock order, lifecycle, background processing,
  configuration).
- `hosted-applications/README.md`: operations runbook (topology, settings, ingress, identity, storage, validator and
  scanner, secrets, sign-out, rollout, monitoring, residual risks).

---

## 1. Context and goals

### 1.1 What the feature is

Hosted applications let a CodeMie user upload a single HTML file or a ZIP of a prebuilt static web app (React,
Angular, Vue, plain HTML) into one of their projects and open it inside CodeMie at `/apps/{id}`. Uploaded code runs
only in the viewer's browser, on a separate registrable domain, one origin per version, and never with CodeMie
credentials. There is no server-side rendering, no build step and no container per application.

The feature is off by default (customer-config component `features:hostedApplications`, overridden by
`FEATURE_HOSTED_APPLICATIONS`) and requires `ENABLE_USER_MANAGEMENT=true`.

### 1.2 Goals

- One sharing and publishing model, the same as for assistants: a project with a "Shared with project" switch, an
  independent Marketplace publication, named view-only grants, and the platform permission check (`Ability`).
- One access rule used by opening by id, every list, the response abilities and the content gateway, so they cannot
  disagree.
- Project admins and platform admins manage apps as they manage other project assets.
- Grants stored in the platform `permissions` table, where RBAC can later extend them to write access without
  changing existing rows.
- A revocable share link whose recipients go through the same "shared with" check as named grants.
- Named sharing of assistants and hosted apps served by one shared module.
- Isolation of untrusted content: content gateway, per-version origins, launch tickets, viewing sessions, a
  validator with a virus scan, and a managers' working version separate from the viewers' active version.

---

## 2. Scope and non-goals

### 2.1 In scope

- Backend: hosted tables and migrations, the access model, Marketplace publication, suspend and reinstate, named
  grants, share links, list scopes and filters, creators list, versions and validation, object storage, content
  gateway, launch and viewing sessions, delete and purge, retention, audit of grant and link changes as platform
  activity events.
- Shared platform pieces the feature needs: the action-aware grant role in `Ability`, the generic named-grant and
  share-link services and their registries, the common `share_links` table, the generic permissions API routing,
  and the move of assistant named sharing onto the shared module.
- UI: the Applications area under `/apps` (five tabs), filters, create and edit form, details, viewer, version list,
  upload and validation dialogs, generic sharing components (also used by assistants), share-link section and share
  page, Suspend and Reinstate dialogs, admin share-link revoke, not-available page.
- Deployment: validator and ClamAV workloads (Helm templates and plain manifests), configuration settings, the
  feature flag entry.

### 2.2 Non-goals

| Non-goal | Decision |
|---|---|
| Folders and trash for catalog entities (a platform-wide ticket for assistants, skills, workflows and hosted apps) | D5 |
| Edit (write) access for named people; the grant row already stores a level | D9, S-33 |
| Hosted apps for external users (by grant, link or Marketplace) | CORE-27 |
| Charging a manager's upload to the uploader instead of the creator | D12 |
| Moving the user search into the user directory service; one Share popup for every entity type | — |
| Marketplace review and categories | D3 |
| Gateway database-pool exhaustion under one viewer's burst; Azure storage | D3 (16437) |
| Changes to uploaded card covers | — |
| Keeping hosted data written by earlier revisions of the feature | D-C1 |
| Several links per app, link expiry, access counters, rate limiting of the link open | D16 |
| Revocable conversation links; moving conversation links into `share_links` | SL-22 |
| Link sharing for assistants | SL-18 |
| Favorites for apps | — |
| A manager-facing "delete a version" operation (only reinstate can retire a version) | — |
| Choosing a version or deleting the previous one in the Reinstate dialog (the API supports both) | UI-4 |
| A named-sharing marker on cards | UI-7 |
| Changing assistant REST routes or response shapes | NG-40 |
| Grant entries in `hosted_application_operations` | NG-5 |
| Rate or scope limits on the recipient search (decided once for hosted apps and assistants) | P-C3 |
| A list or tab of apps to moderate | S-28 |

---

## 3. Platform concepts reused, and new concepts with reasons

### 3.1 Reused

| Platform concept | How hosted apps use it |
|---|---|
| Project (`project`) and creator (`created_by: CreatedByUser`) | `hosted_applications.project` and `created_by` jsonb; create and move require a project the caller has access to, as for datasources |
| `Owned` interface and `Ability` | `Ability.PERMISSIONS["HostedApplications"]`: READ = shared_with, owner, managed_by, admin, granted; WRITE and DELETE = owner, managed_by, admin. Responses carry `user_abilities` from `Ability.list` |
| Assistant flags `shared` and `is_global` | "Shared with project" and Marketplace publication, two independent flags |
| Marketplace publication without review or categories, as for skills | `POST /{id}/marketplace/publish` and `/unpublish`; labels "Publish to Marketplace" / "Remove from Marketplace" |
| Platform `permissions` table | Named and link grants: `principal_type=USER`, `principal_id=<users.id>`, `resource_type=HOSTED_APPLICATION`, `permission_level=READ` |
| Assistant sharing (EPMCDME-14786, codemie !4256, codemie-ui !1901) | Same request and response shapes, owner-only granting, recipient search limits (2 characters, 20 results, internal users only), audit payload, "People with access" popup and "Shared with" sidebar. Assistants and hosted apps run on the same named-grant module |
| Activity audit (`ActivityDomain`, strict activity events) | `hosted_application.grant.added|removed`, `hosted_application.share_link.created|revoked` |
| Generic permissions API | `DELETE /v1/permissions/{id}` removes one named grant, decided by the type's descriptor and audited; `POST /v1/permissions` refuses named grants |
| Conversation share links | "Copy share link" behaviour (first click creates, later clicks return the same link), path-form URL `/apps/share/<token>`, default referrer policy |
| Platform user (`authenticate`) | Every management route authenticates the platform `User`; the hosted identity is used only for viewing sessions |
| Skills catalog filters | Project multiselect and "Created by" (creator display name, options from a users endpoint) |
| UI entity helpers | `canEdit`, `canDelete`, `isOwner` from `src/utils/entity.ts`; `ProjectSelector` and `Switch` as in the assistant form |
| Platform advisory leader lock (`async_leader_lock`) | The storage sweep runs on one replica per period |

### 3.2 New concepts, with the reason each is needed

| New concept | Why it is needed |
|---|---|
| Content gateway, per-version origins, launch tickets, viewing sessions, validator with virus scan | Isolation of untrusted uploaded code (`architecture.md` decisions D1-D6) |
| Working version (`selected_version_id`) beside the active version (`active_version_id`), and "Activate version" | Managers prepare and preview a new upload while viewers keep the active one (D6) |
| Suspend and reinstate (`security_state`) | Platform admins take content down at once without changing sharing (D10, D21, D22) |
| One access module with SQL conditions (`readable`, `manageable`) | The gateway authorizes every content request in SQL, at most two queries per request; `Ability` alone cannot be used there |
| `NAMED_GRANT_TYPES` descriptor registry | Generic named-grant code needs per-type facts (project, creator, remover rule, owner rule, pre-check, id type, audit extras, recipient rule) that `ResourceType._lookup` cannot give for async hosted models (NG-17). Serves `ASSISTANT` and `HOSTED_APPLICATION` |
| `PreloadedGrants` and the lazy per-class grant load in `Ability` | The Python grant term must not look up per object, and no caller should have to remember a preload (S-29) |
| `grant_source` and `source_link_id` on `permissions` | Telling a by-name grant from a link grant, so revoking a link removes only its recipients (D8, NG-32) |
| Common `share_links` table and `SHARE_LINK_TYPES` registry | Revocable links with one live link per resource, keyed like `permissions`, so link grants point into one table for every type that shares by link (SL-22). It serves named-grant types whose openers become named recipients; every link type must be in `NAMED_GRANT_TYPES` (S-30). A bearer-token link such as a conversation link would need a token-only mode first (section 16.2). Hosted apps are its only user |
| Hosted job queue (`hosted_application_jobs`: leased jobs with fencing and retry) | Validation is long and must survive restarts and replicas; the platform has no shared leased queue, and the gateway must never wait for it |
| Operations and idempotency ledger (`hosted_application_operations`) | Every hosted mutation takes an `Idempotency-Key` and a replay must return the stored answer; the platform has no idempotency store. The ledger is also the hosted audit trail |
| Runtime loops (`HostedApplicationsRuntime`: validation, maintenance, storage sweep) | Expiry, cleanup, purge, retention, retry and GC must run without a request; they coordinate through leases and `SKIP LOCKED`, and the sweep runs under the platform leader lock (S-31). The platform schedulers run whole-job crons, not resumable per-row steps |
| `HostedObjectStorage` (S3 and GCS adapter) | `FileRepository` has no multipart upload, no fenced commit against a writer lease and no paged listing for the sweep; the adapter reuses only the client setup of the file repositories (D1 of EPMCDME-16437, OS-1) |
| Quota per creator (`hosted_application_user_state`) and `quota_principal_id` on storage attempts | Storage accounting of uploads and purge refunds, charged to the creator (D12, CORE-24) |

---

## 4. Data model and migrations

### 4.1 Migrations

All hosted migrations are revisions on main's chain, after main's assistant resource-type revision. No data from
earlier revisions of the feature is migrated (D-C1).

| Revision | Content |
|---|---|
| `0ba28b31fa53` (main, !4256) | Adds `ASSISTANT` to `codemie.resourcetype` (parent of the hosted chain) |
| `e3b7c1a95d42_add_hosted_applications` | Creates the eight hosted tables with constraints and indexes, the `pg_trgm` extension and the GIN trigram index `hosted_applications_search` on `search_text`; `hosted_application_storage_attempts.provider` accepts `filesystem`, `aws`, `gcp` (EPMCDME-16437) |
| `f8d24a6c0b17_platform_sharing_grants_and_links` (head) | Changes the tables other entities share. `ALTER TYPE codemie.resourcetype ADD VALUE IF NOT EXISTS 'HOSTED_APPLICATION'` (autocommit block); creates `share_links` and the partial unique index of live links; when `permissions` resolves on the search path, adds `permissions.grant_source` (varchar 16, nullable), `permissions.source_link_id` (uuid, nullable), `permissions_grant_source_check` and `permissions_source_link_id_fkey` (`source_link_id` → `share_links.id`, `ON DELETE RESTRICT`), and sets `grant_source='name'` on assistant USER grants that have NULL. Downgrade deletes link and hosted grants, drops the constraints and columns (assistant grants stay), then the table |

Two revisions on purpose (decision M-1): the hosted tables, and the shared platform changes apart so they can be reviewed and rolled back on their own. They replace five unmerged revisions of earlier branch versions under new ids, so a database that ran the earlier branch fails on an unknown revision instead of half-applying; drop its hosted tables, `share_links` and the `permissions` grant columns, set `alembic_version` to `0ba28b31fa53`, then upgrade.

### 4.2 Tables

| Table | Key columns and constraints |
|---|---|
| `hosted_applications` | `id` uuid PK; `project` text NOT NULL; `created_by` jsonb NOT NULL; `shared` bool default false; `is_global` bool default false; `name` varchar(120); `description` varchar(2000); `search_text`; `artifact_kind` (`html`, `zip`); `selected_version_id`; `active_version_id`; `security_state` (`clear`, `suspended`); `lifecycle_state` (`active`, `purging`, `purged`); `revision`; `access_revision`; `created_at`, `updated_at`, `deleted_at`, `purged_at`. CHECKs: name and description present unless purged; `lifecycle_state = 'active'` iff `deleted_at IS NULL`; a purged row has `purged_at`, no name, description, version pointers or search text, and empty `created_by.username` and `created_by.name` (only the creator id is kept). Indexes: `hosted_applications_project`, `hosted_applications_created_by_id` on `(created_by->>'id', updated_at DESC, id DESC)`, `hosted_applications_purged`, `hosted_applications_name` on `(name, id)`, `hosted_applications_search` (GIN trigram) |
| `hosted_application_user_state` | Quota row per principal: `principal_id`, `byte_limit` (default 2 GiB), `stored_bytes`, `reserved_bytes`, `revision`, and `auth_revoked_before` (the sign-out cutoff) |
| `hosted_application_versions` | Immutable versions: upload state, validation state (`uploading`, `queued`, `validating`, `awaiting_configuration`, `accepted`, `rejected`, `failed`, `canceled`), version-level `security_state` (`clear`, `blocked`), digests, routing mode (`static`, `spa_history`), expanded size (≤ 500 MiB), file count (≤ 5000) |
| `hosted_application_storage_attempts` | One ledger row per storage prefix: `quota_principal_id` NOT NULL (indexed with `app_id`, `version_id`), `kind` (`upload`, `validation`), `provider` (`filesystem`, `aws`, `gcp`), byte reservations, writer fencing, deletion proof |
| `hosted_application_jobs` | Durable job queue: `kind` (`validate`, `purge`, `retention`, `cleanup_upload`), `state` (`ready`, `running`, `passed`, `completed`, `rejected`, `failed`, `canceled`), leases, one active validation job per version and one active purge job per app |
| `hosted_application_operations` | Idempotency ledger and audit trail of hosted mutations and launches: `actor_kind` (`user`, `administrator`, `system`), action, key digest, request digest, replay body |
| `hosted_application_view_sessions` | Auth contexts, launch tickets and viewing sessions (`kind`), bound to the principal and its sign-in binding |
| `hosted_application_covers` | Uploaded card cover: PNG, JPEG or WebP, ≤ 1 MiB, ≤ 2048 px per side |
| `share_links` (platform, shared) | `id` uuid PK; `resource_type` `codemie.resourcetype`; `resource_id` uuid; `token` text; `token_sha256` text UNIQUE; `created_by` jsonb; `created_at`; `revoked_at`; `revoked_by` jsonb. Partial unique index `uq_share_links_live (resource_type, resource_id) WHERE revoked_at IS NULL`. No FK to the resource, as on `permissions`; each type deletes its rows explicitly (SL-22) |
| `codemie.permissions` (platform, shared) | Hosted grants: `resource_type='HOSTED_APPLICATION'`, `principal_type=USER`, `principal_id=users.id`, `resource_id` = app id (uuid), `permission_level=READ`, `created_by` = the owner who granted (for a link grant the link's owner, never the opener, SL-24), `grant_source` in (`name`, `link`), `source_link_id` set exactly when the source is `link` (CHECK `permissions_grant_source_check`, which also admits NULL/NULL for APPLICATION rows). Assistant grants are the same rows with `resource_type='ASSISTANT'` and `grant_source='name'`. Unique constraint `unique_resource_principal` gives one row per person per resource. `source_link_id` references `share_links.id` `ON DELETE RESTRICT` |

Foreign keys to the app id are `ON DELETE RESTRICT`, except `hosted_application_covers.app_id`, which is
`ON DELETE CASCADE`. Retention deletes dependants explicitly before the app row, including the app's
`share_links` rows (`resource_type='HOSTED_APPLICATION'`), which have no FK to it. View sessions reference auth
contexts by `(principal_id, parent_id, parent_kind)`. No hosted table carries an organization or sign-in-issuer key
(D15).

### 4.3 Enum values and constants

- `codemie.resourcetype`: `DATASOURCE`, `ASSISTANT`, `HOSTED_APPLICATION` (added with `ADD VALUE IF NOT EXISTS`, so
  the order of enum migrations does not matter, NG-34).
- `ResourceType` (Python): `datasource`, `assistant`, `hosted_application`; `ResourceType._lookup` (types with
  APPLICATION-principal permissions) holds only `DATASOURCE`.
- `ActivityDomain` and `ActivityEntityType`: `HOSTED_APPLICATION`.
- Suspend-gated actions: `activate`, `publish`, `share_on`, `move` (`access.SUSPEND_GATED_ACTIONS`).
- Limits: `MAX_APPLICATIONS_PER_OWNER = 500` active apps per creator (`APPLICATION_LIMIT`, 409); quota
  `HOSTED_APPS_QUOTA_BYTES` (default 2 GiB, minimum 1 MiB); upload limits html 20 MiB, zip 100 MiB, expanded
  500 MiB, file 50 MiB, 5000 files.

---

## 5. Access model

### 5.1 One rule, one module

`src/codemie/core/hosted_applications/access.py` is the only definition of who may see or manage an app. The hosted
model may import it; it never imports the model (CORE-25). It imports the `users` and `user_projects` tables and
`eligible_internal_user` (`rest_api/models/user_management.py`) and `grant_exists` / `ResourceType`
(`rest_api/models/permission.py`) at module level, with no imports inside functions; a test pins that set (S-24).
It exposes:

- Python predicates for one loaded app: `is_owned_by(created_by_id, user)`, `is_managed_by(project, user)` (project
  admin), `is_shared_with(project, shared, is_global, security_state, user)` (project and Marketplace terms only),
  `is_granted(security_state, user, app_id, grants, action)` (the named-grant term behind `Role.GRANTED`),
  `is_eligible`, `is_grant_remover(created_by_id, user)`, `is_moderator(user)`, `is_suspended(app)`.
- Suspension: `refuses(app, action)`, `suspend_refuses(app, action)`, `refuses_sql(apps, action)`,
  `refused_actions(app)` (section 5.5).
- SQL conditions taking a user-id SQL expression: `readable(apps, user_id_expr)`,
  `manageable(apps, user_id_expr, action)` with `action` WRITE (change the app, the default) or DELETE (the delete
  path), `grant_remover(apps | None, user_id_expr)`, and `grant_recipient(users)` (the hosted recipient policy). They
  join `users` and `user_projects` themselves. A grant term joins the manager condition only for the actions in
  `MANAGER_GRANT_ACTIONS`, which is empty while grants are view only (D9) and never applies to DELETE (S-16).
- `platform_user(session, user)`: a copy of the request user with roles read from `users` and `user_projects`, the
  rows the SQL conditions read (CR-001 of access-core). `is_admin` on the copy is `is_admin OR is_maintainer`. An
  account that is missing, inactive, deleted or external comes back with no roles and fails every predicate.

Consumers: the hosted model (`Owned` delegation), `Ability`, opening by id, every list scope, the creators list, the
response projection, `tx.managed_app`, the share-link open, the grant and link descriptors, the admin service, and
the gateway's per-request check (`readable` for the active version, `manageable` for a manager's preview of any
version).

### 5.2 Roles

| Role | Who (from `users` / `user_projects`) | May |
|---|---|---|
| Eligible user (term of every condition) | `is_active`, `deleted_at IS NULL`, `user_type <> config.EXTERNAL_USER_TYPE` (service accounts count as internal, CORE-27) | Nothing by itself |
| Owner (creator) | `created_by.id = user.id` | Everything a manager may, plus share by name and by link (`can_share`) |
| Manager | Owner; project admin of the app's project (`user_projects.is_project_admin`); platform admin or maintainer (`users.is_admin OR users.is_maintainer`) | Open and preview any version, edit, change `project` and `shared`, upload, configure, select and activate versions, publish and unpublish, covers, delete |
| Viewer | Not suspended, and one of: `is_global`; `shared` and member of the app's project; a live grant (by name or by link) | Open the active version; READ only |
| Platform admin (moderator) | `users.is_admin OR users.is_maintainer` | Suspend and reinstate (`can_moderate`); revoke the share link; delete a single grant through the generic permissions API |
| Nobody | External, inactive or deleted users; anyone matching no term | 404 everywhere, absent from every list |

The creator's own eligibility is not a term: a deactivated creator does not hide the app; other managers keep it
(CORE-28, D7).

### 5.3 Live grant rule and the grant role in `Ability`

`core/ability.py` and `rest_api/models/permission.py`:

- `levels_covering(action)` (`core/ability.py`) is the only level order: READ < WRITE < DELETE; a READ grant never
  passes WRITE or DELETE (NG-2).
- `live_grants(resource_type, principal_id_expr, recipient)` is the only statement of a live grant: USER rows of
  that type, counted only for a grantee whose `users` row meets the type's recipient policy (the descriptor's
  `recipient`; internal only for both types) and only while `ENABLE_USER_MANAGEMENT` is on; otherwise empty (NG-4,
  NG-12, S-18). Admitting external users for one type is a change to that type's descriptor.
- `grant_exists(resource_type, resource_id_expr, principal_id_expr, action, *, recipient, resource_key)` is an EXISTS
  over `live_grants` at a covering level. `resource_key` turns `permissions.resource_id` (uuid) into the resource
  table's id type: identity for hosted apps (UUID to UUID, so `unique_resource_principal` serves the probe, NG-13),
  a cast to text for assistants. Services call `named_grant_service.grant_exists_term`, which takes `recipient` and
  `resource_key` from the descriptor; the access module passes `grant_recipient` itself (it cannot import the
  service layer). Python ids are converted to UUID by the service; an id that is not a UUID holds no grant row.
- `Permission.get_for` reads APPLICATION principals only, at a level covering the action; USER rows are never read
  through it (NG-3).
- The hosted SQL viewer term, `list_scope('shared')`, the `shared` list filter, the assistant per-user grant list
  condition (`granted_ids_statement`) and the Python grant term are all built on `live_grants`.
- `PreloadedGrants(class_name, levels)` maps `str(resource_id)` to the highest level; it is built only by
  `named_grant_service.preload` / `apreload` over `live_grants` and tested by `preloaded_grant_covers` against the
  action being checked. No grants means no grant (fail closed).
- Where the grants come from (S-29): `Ability` keeps a map of class name to grants. The first check that reaches
  `Role.GRANTED` for an instance class with no entry calls `core.ability.load_class_grants(user, class_name)`, which
  resolves the dotted path `GRANT_LOADER` (`named_grant_service.load_class_grants`, so `core` imports no service
  module), finds the named-grant type that owns the class (`named_grant_types.resource_type_for_class`) and reads all
  of the user's live grants of that type in one statement through a short-lived sync session on the platform engine.
  The result lives on the `Ability` object (one request; nothing is cached across requests); a class with no named
  grants gets `None`, and with `ENABLE_USER_MANAGEMENT` off no query runs. The load is synchronous because `Ability`
  is. `Ability(user, grants=...)` (keyword only) counts as loaded for `grants.class_name`; hosted code passes the
  grants it read once per transaction on its own async pool (`apreload`), so no hosted path runs the sync load.
  `GRANTED` is the last role of every action that has it, so owners, managers, admins and project viewers never
  cause a grant read. A list that mixes classes makes one query per class. List code creates one `Ability` per list
  (assistant repository, guardrail entity details, skill assistant lists, `get_by_ids`), so a page costs one grant
  read.
- `Role.GRANTED` is the only action-aware role: `Ability.can` passes the action, and `granted(instance, action)` asks
  the instance (`Owned.is_granted_to(user, action, grants)`, default "no grant") whether the loaded level covers it.
  `GRANTED` is registered under READ for `HostedApplications` and `Assistant`; `is_shared_with` carries no grant term
  (S-16).
- Edit (WRITE) grants do not exist; only the seam does (S-33). `named_grant_service.grant(..., level=)` writes a
  level, the by-name upsert raises a stored level that does not cover the requested one (a lower level is set by
  revoke then grant), and `manageable(DELETE)` never takes a grant term, so a WRITE grant could never imply delete.
  The API accepts READ only (D9). The recipe for enabling edit access is a follow-up (section 16.2).

### 5.4 Access levels and actions

| Action | Rule |
|---|---|
| View card and details, launch the active version | `readable` (manager, or viewer while not suspended) |
| Preview the working or any version | `manageable` |
| Edit metadata, change `shared` | Manager (`tx.managed_app`); `shared: true` refused while suspended |
| Change `project` | Manager with access to the target project (`PROJECT_ACCESS_DENIED`, 403); refused while suspended |
| Upload, configure, select the working version, covers | Manager; allowed while suspended |
| Activate a version | Manager, version eligible (`can_activate`); refused while suspended |
| Publish to the Marketplace | Manager; refused while suspended; allowed without an active version (CORE-15) |
| Remove from the Marketplace | Manager; allowed while suspended |
| Delete | Manager, asked as `manageable(DELETE)` (`managed_app(action=DELETE)`), never through a grant term; allowed while suspended (S-21) |
| Grant by name, read or create the share link, list who has access, search recipients | Owner only; granting and link creation refused while suspended (`share_on`) |
| Revoke by name | Owner only; allowed while suspended |
| Revoke the share link | Owner, or a platform admin or maintainer (link descriptor `may_revoke` = owner or `is_grant_remover`, S-17); allowed while suspended; one revoke step, audited with the revoker as actor |
| Suspend, reinstate | Platform admin (maintainers included), reason required; reinstate refused to the app's creator |
| Delete one grant through `DELETE /v1/permissions/{id}` | `grant_remover`: platform admin or maintainer, or the app's creator, from the `users` row; for an orphan grant only an admin |
| Create a grant through `POST /v1/permissions` | Refused for every named grant |

### 5.5 Suspension

- Suspend sets only `security_state='suspended'`; it changes no sharing field, no grant and no link, so reinstate
  restores exactly the sharing that was in place (D22). Suspending an already suspended app changes nothing.
- For non-managers a suspended app does not exist: absent from lists, 404 by id and by link, and their open viewing
  sessions end on the next request (D21).
- Managers may open, preview (non-active versions included), edit, upload (validated and scanned into a ready
  version), configure, unpublish, turn `shared` off, revoke grants, revoke the link and delete (CORE-2..CORE-4,
  S-21).
- The refusal list has one home, `access.SUSPEND_GATED_ACTIONS`, applied one way (S-19): `refuses(app, action)` is
  the only predicate (an action outside the list raises `ValueError`); `suspend_refuses` raises 409 `APP_SUSPENDED`
  from it; `refuses_sql(apps, action)` is its SQL twin; `refused_actions(app)` projects it to clients.
  `suspend_refuses` is evaluated on the row returned by `tx.managed_app(..., lock=True)` inside the writing
  transaction, never in a route pre-check (CORE-6):

| Action key | Where it is checked | Answer |
|---|---|---|
| `activate` | `publication.py` (`activate_version`) | 409 `APP_SUSPENDED` |
| `publish` | `publication.py` (`/marketplace/publish`) | 409 `APP_SUSPENDED` |
| `share_on` | `PATCH` with `shared: true`; grant descriptor `before_grant` (by-name grant and link create); link open's lock query (`refuses_sql`) | 409 `APP_SUSPENDED` (open: 404 `SHARE_LINK_NOT_AVAILABLE`) |
| `move` | `PATCH` with a changed `project` | 409 `APP_SUSPENDED` |

- Every app projection carries `suspension_refuses`: the sorted list (`["activate", "move", "publish",
  "share_on"]`) while suspended, else `[]`. Clients read it instead of keeping a copy (S-19).
- `can_activate` is defined once in the version projection: eligible and not `refuses(app, "activate")`.
- Outside the access module and the admin service, no service, the worker or the gateway reads the app-level
  `security_state` (the projection exposes it as `suspended`); the version-level `security_state` is separate.

### 5.6 Moderation

- `POST /v1/admin/hosted-applications/{id}/security/{suspend|reinstate}`, body `SecurityReason{reason (≤ 1000
  characters), version_id?, delete_previous?}`, `Idempotency-Key` required, `If-Match` optional (checked only when
  sent, because a takedown may target an app the admin never opened). The admin check reads `users`
  (`platform_user`), not request roles; anyone else gets 403 `ACCESS_DENIED`.
- Suspend refuses `version_id` and `delete_previous` (422 `INVALID_ACTION`); an empty reason is refused (422).
- Reinstate is refused to the app's creator (403 `INDEPENDENT_REVIEW_REQUIRED`), so another admin must review
  (CORE-29), and answers 409 `NOT_SUSPENDED` when the app is not suspended.
- Reinstate may name a version to make active (default: the current active one) and is refused only when that
  version is not eligible (409 `REVALIDATION_REQUIRED`); it may retire the previously active version
  (`delete_previous`, only with another version chosen, else 409 `NO_OTHER_VERSION`) through `retire_version`. It
  calls the shared `activate` and `retire_version` steps (CORE-7..CORE-10).
- The idempotency digest covers `{reason, if_match, version_id, delete_previous}`; the operation is recorded with
  `actor_kind='administrator'`, the reason, the admin id and, for reinstate, the version made active and the version
  retired.
- The UI Reinstate dialog sends a reason only (UI-4).
- There is no list or tab of apps to moderate (S-28). Suspend, Reinstate and the admin "Revoke share link" are in
  the app's own actions (`can_moderate`). A platform admin finds every app, of any project and private ones
  included, in the Project tab (`scope=project`, CORE-18), and can also open one by its URL or id (`GET /{id}` and
  `managed_app` admit every platform admin).
- A leaked link is stopped without suspending the app: a platform admin or maintainer opens the app by id and calls
  `DELETE /v1/hosted-applications/{id}/share-link`, the same single revoke step the owner uses (tombstone plus
  removal of the link's grants, each audited with the admin as actor). Reinstate restores only `security_state`, so
  it never brings a revoked link back. A single grant can be deleted through the generic permissions API (S-17).

### 5.7 Viewing sessions and revocation

- The gateway re-evaluates `readable` / `manageable` on every content request (at most two queries per request),
  resolving roles, membership and external type from `users` and `user_projects` on the credential's principal id,
  never from token claims (CORE-17).
- Losing membership, `shared` off, unpublish, suspend, a revoked grant or link, and delete end access on the next
  request. Widening access (turning `shared` on, publishing, granting) ends no session.
- `access_revision` is bumped only on delete and purge (CORE-30). Activation and reinstate onto another version are
  caught by the session's `version_id`.
- Sign-out writes a durable cutoff that ends all viewing sessions of the user. The platform logout routes
  (`POST /v1/local-auth/logout`, `GET /v1/user/log_out`) call `revoke_hosted_sessions_best_effort` (bounded by 2 s,
  never raises, never blocks logout); `POST /v1/hosted-applications/sessions/revoke` does the same for a client that
  calls it. Management writes also apply the cutoff to the hosted identity recorded on the request and are refused
  when none is recorded (CORE-22, CORE-23).

---

## 6. Sharing: named grants and share links

### 6.1 Named grants

- Owner only (D14, D17). Routes: `GET /{id}/shared-with`, `POST /{id}/share`, `DELETE /{id}/share`,
  `GET /{id}/sharable-users?q=`. Anyone else who can read the app gets 403 `ACCESS_DENIED` "Only the owner can share
  or revoke access"; anyone who cannot read it gets 404. All four require user management (`user_management`
  dependency: 400 `USER_MANAGEMENT_DISABLED` otherwise).
- Request `ShareUsersRequest{user_ids}`: 1 to 100 ids, extra fields forbidden (so no access level can be asked for),
  422 otherwise (NG-24, NG-25). Ids are de-duplicated keeping order (NG-23).
- Grant and revoke run in the hosted management transaction on the locked app row (`_owned_app`, which calls
  `tx.managed_app(lock=True)`), so the sign-out cutoff, the owner check and the suspend refusal apply to the locked
  row (NG-29).
- Shared code, generic over the resource type and in !4256's shapes, lives in
  `src/codemie/service/permission/named_grant_service.py` (sync functions and `a`-prefixed async twins; each step
  is one statement; nothing commits, the caller owns the transaction, NG-10):
  - `grant`: owner check through `descriptor.is_owner` (403 with the descriptor's `not_owner_message`), then
    `descriptor.before_grant`, then de-duplication, then one validation select (every id is a user the type's
    `recipient` rule admits: 404 for an unknown id, 400 for a found but not sharable one) with a LEFT JOIN on any
    existing grant, then one `INSERT ... ON CONFLICT ON CONSTRAINT unique_resource_principal DO UPDATE SET
    grant_source='name', source_link_id=NULL, permission_level=excluded WHERE grant_source='link' OR the stored
    level does not cover the requested one RETURNING ..., (xmax = 0) AS inserted`. A by-name grant replaces a link
    grant (NG-22).
  - `revoke`: owner check, de-duplication, one `DELETE ... RETURNING`; revoking a missing grant is a no-op. It
    removes a person's row whatever its source.
  - `list_shared_with`: `{id, username, email, name, picture, grant_source}`, sorted; orphan grants (user row gone)
    are listed with their stored `grant_source` so the owner can revoke them.
  - `search_sharable_users(session, resource_type, q)`: active, not-deleted users the type's `recipient` rule admits
    (applied in SQL), at most `SEARCH_RESULT_LIMIT = 20`, so the people offered are the people a grant would accept
    (S-30); the route requires `q` of 2 to 200 characters.
  - `revoke_permission`, `remove_all_grants`, `link_grant`, `remove_link_grants`: one `DELETE ... RETURNING` or
    `INSERT` in the caller's transaction, one audit event per row.
  - `preload` / `apreload`, `load_class_grants`, `granted_ids_statement`, `grant_exists_term` (section 5.3).
- `NAMED_GRANT_TYPES` (`service/permission/named_grant_types.py`) maps `HOSTED_APPLICATION` to
  `codemie.service.hosted_applications.grant_descriptor:DESCRIPTOR` and `ASSISTANT` to
  `codemie.service.assistant.grant_descriptor:DESCRIPTOR`, imported lazily; nothing registers at import time, and
  neither `core` nor `rest_api/models` imports it (NG-18). `NamedGrantDescriptor` carries: `owned_class_name`
  (`Ability`'s class name), activity domain, entity type and events, sync and async `resolve` (project, creator id,
  and whether the actor may remove one grant), `before_grant`, `is_owner`, `not_owner_message`, `resource_key`,
  `audit_attributes` and `recipient` (the condition a grantee's `users` row must meet both to be granted and for a
  stored grant to count).
- Hosted descriptor: `resolve` is one select of scalar subqueries (project, creator id, `grant_remover`, with only
  the admin branch for an orphan grant); `before_grant` is `suspend_refuses(app, "share_on")`; `is_owner` is
  `access.is_owned_by` on the platform copy of the user; `recipient` is `access.grant_recipient`;
  `audit_attributes` adds `app_id`.
- Assistant descriptor: `before_grant` is the personal-integration refusal (400, "Assistant uses a personal
  integration and cannot be shared with users. ..."); `is_owner` is `Assistant.is_owned_by`; `not_owner_message` is
  "Only the assistant owner can share or revoke access"; `resolve` reads the assistant row and the actor's `users`
  row (eligible, and admin or maintainer, or the assistant's creator); `resource_key` casts to text; events keep
  `assistant.grant.added|removed` and `audit_attributes` adds `assistant_id`; `recipient` is
  `eligible_internal_user`. `AssistantShareService` keeps only the assistant rules (personal-integration guards, the
  owner pre-check, `has_named_grants`) and delegates grant, revoke, list, search and remove-all to the shared
  module; grant and revoke run on the assistant row locked `FOR NO KEY UPDATE`. Assistant REST routes
  (`/v1/assistants/{id}/shared-with`, `/share`, `/sharable-users`) and response shapes are unchanged; items carry
  the additive `grant_source`.
- Every `ResourceType` member is in exactly one of `_lookup` and `NAMED_GRANT_TYPES`. `type_for_instance` serves
  datasources only, so `Ability.can(..., check_resource_permissions=True)` admits nothing extra for a grantee (NG-20).
- Generic permissions API: routing decides by (resource type, principal type) through
  `named_grant_types.is_named_grant` (a USER row of a `NAMED_GRANT_TYPES` type, S-18). `POST /v1/permissions`
  refuses named grants and any type outside `_lookup`. `DELETE /v1/permissions/{id}` branches before its `Ability`
  check: a named grant is decided by the descriptor's `resolve` (`may_remove`) and removed through
  `revoke_permission` with audit in one transaction; any other row of a `_lookup` type keeps the `Ability` path; any
  other row is refused (`PermissionAccessDenied`) (NG-19, NG-21, NG-33). An unknown permission id answers 422. A type
  with both APPLICATION (project) and USER grants can therefore join `NAMED_GRANT_TYPES` and stay in `_lookup`.
- Delete of an app: `_begin_purge` calls `share_link_service.arevoke` (the link and its grants) and
  `aremove_all_grants` with the deleting user as actor, in the delete transaction (NG-31, SL-23). Delete of an
  assistant calls `remove_all_grants` in its delete transaction.

### 6.2 Audit of grant and link changes

- Each changed row is one strict activity event, flushed once in the same transaction
  (`insert_many_strict`, `async_insert_many_strict`), as in !4256 (NG-5, NG-26). Hosted grant changes are not
  written to `hosted_application_operations`.
- Grant events: `hosted_application.grant.added`, `hosted_application.grant.removed`. Attributes: `actor_id`,
  `actor_username` (`system` without an actor), `principal_type`, `principal_id`, `resource_type`, `resource_id`,
  `project`, `grant_source`, `before`, `after`, `occurred_at`, plus `app_id` (hosted) or `assistant_id` (assistant
  events `assistant.grant.added|removed`) (NG-7, S-30).
- `before` / `after` are levels: added `None`→`READ`, removed `READ`→`None`; a link-to-name upgrade is `READ`→`READ`
  with `previous_grant_source='link'` and `previous_source_link_id`, read under the app-row lock (NG-8, NG-9). A
  removed link grant carries `previous_source_link_id`; a link grant carries `source_link_id`.
- Link events: `hosted_application.share_link.created`, `hosted_application.share_link.revoked`, with `link_id`,
  `resource_type`, `resource_id`, `project`, the actor and `app_id`, never the token.
- With `ACTIVITY_EVENTS_ENABLED` off (the default) no grant or link change is recorded (NG-6, S-25); the rollout
  checklist turns it on.

### 6.3 Share links

- One live link per app (D16). The link is a row of the common `share_links` table (SL-22), served by the generic
  `service/permission/share_link_service.py` and the `SHARE_LINK_TYPES` registry (`share_link_types.py`, hosted
  only). A link type must be a named-grant type (`link_descriptor_for` raises `KeyError` otherwise, and a test checks
  the registries). The link service reads the type's grant descriptor for the activity domain and entity type, the
  owner rule (`is_owner`) and its message, the pre-check (`before_grant`), the audit attributes and the recipient
  rule (S-30). The hosted link descriptor (`service/hosted_applications/share_link_descriptor.py`) holds only what
  is specific to links: the link events, `areadable` (`readable` on an active app), `alock` (`tx.lock_app` on an
  active app that does not refuse `share_on`), `project_of`, the link answers (`not_available`: 404
  `SHARE_LINK_NOT_AVAILABLE`; `opener_refused`: 403 `ACCESS_DENIED` "Only internal users can open a share link";
  `access_denied(message)`: 403) and `may_revoke`. Recipients are `permissions` rows with `grant_source='link'` and
  `source_link_id=<link id>`, so link access is the grant viewer term and there is no second access store (D8).
- Token: `secrets.token_urlsafe(32)`, stored twice: `token_sha256` (unique, the only lookup value, so only the hash
  is ever a bound parameter on open) and the plaintext `token`, kept so the owner can read the same link again
  (SL-2). Plaintext at rest is an accepted risk (CR-001 of share-links).
- Link routes (behind `user_management`, on the locked row from `_owned_app`):
  - `GET /{id}/share-link` (owner) → `{"link": {id, token, created_at} | null}` (SL-9);
  - `POST /{id}/share-link` (owner) calls `share_link_service.acreate`: the grant descriptor's owner check on the
    locked row and `before_grant` (refused while suspended), then get or create; writes
    `hosted_application.share_link.created` on create;
  - `DELETE /{id}/share-link` (owner, or a platform admin or maintainer; anyone else who can read the app gets 403
    "Only the owner or a platform administrator can revoke the share link") calls `arevoke_checked` (the link
    descriptor's `may_revoke`, defaulting to the grant owner rule), which calls `arevoke`, the one revoke step
    (SL-23): set `revoked_at` / `revoked_by`, write `hosted_application.share_link.revoked`, remove every grant whose
    `source_link_id` is that link, one event each. A no-op without a live link; always answers `{"link": null}`;
    allowed while suspended.
- A revoked link stays as a tombstone, so its token is never live again; the next "Copy share link" creates a new
  id and token (SL-3).
- Open: `POST /v1/hosted-applications/share-links/open`, body `{"token"}` (1 to 512 characters, extra fields
  forbidden), guarded by `mutation` (CSRF origin and content-type checks), `hosted_actor` and `user_management`;
  returns `{"app_id"}`, never the token. In one hosted write transaction (`share_link_service.aopen`):
  1. `SET LOCAL lock_timeout = '400ms'` (`SHARE_LINK_OPEN_LOCK_TIMEOUT_MS`), the first statement of the
     transaction, through `repository.transaction(..., lock_timeout_ms=...)`.
  2. The opener must meet the type's recipient rule (hosted: an eligible internal user), else 403 (S-30);
     signed-out callers get 401 from authentication.
  3. Look up the live link by `token_sha256` and `resource_type`, so a hosted route opens only hosted links. Unknown
     or revoked: 404 `SHARE_LINK_NOT_AVAILABLE`, the same answer in every unusable case (SL-11).
  4. If the opener can already read the active app (creator, manager, project member, Marketplace, any grant),
     return `app_id` and write nothing.
  5. Otherwise lock the app row through `alock` (`tx.lock_app(query)` with open's own select: app id, `active`,
     not refusing `share_on`; `lock_app` adds only `FOR NO KEY UPDATE` and `populate_existing`, SL-12, SL-13). Open
     takes no quota lock. No row (app gone, purging, suspended): 404. Re-read the link; revoked meanwhile: 404 and
     nothing written.
  6. `link_grant`: `INSERT ... ON CONFLICT ON CONSTRAINT unique_resource_principal DO NOTHING RETURNING`, so an
     existing row of either source stays (by name keeps winning, SL-6), plus one `grant.added` event with
     `grant_source='link'` and `source_link_id`. The row's `created_by` is the link's owner (`share_links.created_by`);
     the opener is its principal and the event's actor (SL-24).
  - An expired lock wait (`lock_not_available`, 55P03) answers the retryable 503 `SERVICE_BUSY` and writes nothing
    (S-23). The timeout stays well below the hosted pool checkout timeout (`runtime.DB_POOL_TIMEOUT_SECONDS`, 2.0 s):
    waiting opens hold pool connections, and a wait as long as a checkout would let them take the whole
    10-connection pool, so viewers of every app in that process would see `SERVICE_BUSY`. A test keeps it under half
    the checkout timeout (S-32).
  - Revoke, owner removal of a person and suspend take the same app-row lock, so open and revoke serialize.
- Revoke semantics:
  - revoking the link removes every grant whose `source_link_id` is that link, with one event each; those people
    lose access on their next request and the app leaves their "Shared with me"; named recipients keep access;
  - a link recipient who is later granted by name gets `grant_source='name'`, `source_link_id=NULL` and survives a
    link revoke ("by name wins", D8, NG-22);
  - the owner removing a link recipient in "People with access" ends their access on the next request, but they can
    come back by opening the live link again; only revoking the link stops that (D19);
  - someone who can already read the app when they open a live link (project member, Marketplace) gets no recipient
    grant; their access ends when that other path ends (unpublish, `shared` off, leaving the project) although the
    link is still live, and opening the link again then records the grant (accepted behaviour);
  - deleting the app revokes the live link (with its grants) and removes all other grants in the delete
    transaction; retention later deletes the app's `share_links` rows explicitly before the app row. The
    `permissions.source_link_id` FK is `RESTRICT`, so a link row can never be deleted while a grant points at it;
    every link-grant removal stays an explicit, audited step.
- Logging: the hosted engine is created with `hide_parameters=True`, so a failed statement cannot print the token
  from a bound parameter (SL-16). Hosted 422s never echo submitted values; hosted error logs carry the route
  template, not the path.

### 6.4 "Shared with me" and who has access

- `GET /v1/hosted-applications?scope=shared` lists active, readable apps the caller holds a live grant on (by name or
  by link) and did not create, whatever their project or `is_global` (NG-27, NG-28). Assistants list items shared by
  name through the personal-project filter of !4256; giving them the same tab and scope is a follow-up.
- Who has access (`GET /{id}/shared-with`) is returned to the owner only. Project members see only `shared`; no other
  response carries the grantee list (D14).
- The UI shows "via link" for rows with `grant_source === 'link'` and tells the owner that such people can come back
  through the link while it is live.
- The card shows "Shared with project" from `shared` and "Marketplace" from `is_global`; named sharing appears only
  in the owner's details sidebar under "Shared with" (UI-7).

---

## 7. Marketplace and project sharing

- `shared` ("Shared with project") and `is_global` (Marketplace) are independent (D2):
  - `shared` is set on create (`AppCreate.shared`, default false; create already requires access to the project) and
    changes afterwards only through `PATCH /{id}` (any manager; turning it on and changing `project` are refused while
    suspended);
  - `is_global` changes only through `POST /{id}/marketplace/publish` and `/unpublish` (any manager; publish refused
    while suspended);
  - unpublish leaves `shared` as it was; toggling `shared` leaves `is_global` as it was;
  - delete writes neither flag; `lifecycle_state` and `access_revision` end access (CR-005 of access-core).
- Publication has no review and no categories (D3) and needs no active version (CORE-15); the card then shows "No
  active version yet" and launching is refused (`NO_ACTIVE_VERSION`, 409).
- Marketplace apps are visible to every eligible internal user; never to external users (CORE-27).
- Project sharing: members of the app's project see it in the Project tab when `shared` is on; project admins also
  see the project's unshared apps there; a platform admin's (or maintainer's) Project tab shows every project's
  apps, private ones included, as skills show every project and private skill to a global admin (CORE-18).
- Create and move require a project the caller has access to (`PROJECT_ACCESS_DENIED`, 403), as for datasources.
- UI labels: "Publish to Marketplace" / "Remove from Marketplace" (D-U1); no category dialog.

---

## 8. Versions, validation, storage, gateway, launch, sessions, delete and identity

Full design in `docs/hosted-applications/architecture.md`; operations in `hosted-applications/README.md`.

- **Versions** (D6, D13): versions are immutable. A manager's upload becomes a new version; managers see the
  working version (`selected_version_id`), everyone else the active one (`active_version_id`) until "Activate
  version". `POST /{id}/activate` sets the active and working versions and `artifact_kind`; `POST /{id}/select-version`
  sets only the working version. An app shared before any activation shows its card and details with "No active
  version yet", and launching is refused.
- **Version steps with one home**: `activate(tx, app, version)` (`publication.py`), used by `POST /activate` and
  reinstate; `retire_version(tx, app, version, actor)` (`retention.py`) takes no locks, refuses a version that is
  active, selected or has a ready or running job (409 `VERSION_IN_USE`), blocks it and queues the deletion of its
  bytes; used by retention (system actor) and reinstate (admin).
- **Quota** (D12): uploads are charged to the app creator's quota whoever uploads; each storage attempt stores the
  holder in `quota_principal_id`, and version accounting, purge refunds and the worker's fair share read that column.
  `GET /usage` reports the caller's own quota.
- **Lock order** (one order across hosted code, admin reinstate included): creator's quota row (`FOR UPDATE`), app
  row (`FOR NO KEY UPDATE`), version, job, attempt. `tx.managed_app(app_id, user, lock=, states=, action=)` reads
  through `manageable` without a lock, locks the creator's quota row as `tx.quota`, then locks the app row through
  `tx.lock_app` and re-checks. The caller's own quota row is never locked when the caller is not the creator. Create
  locks the creator's quota row before counting apps and before the idempotency lookup (CORE-21).
- **Validation**: a separate validator pod (sandboxed parser plus ClamAV clamd with freshclam); a configuration step
  for several site roots or entry points; uploads of a suspended app are validated too (CORE-3). The clamd liveness
  probe also fails when freshclam is not running, and `FRESHCLAM_CHECKS=4` (FC-1..FC-4). Accepted versions are served
  only while their policy version and definition digest are in the code constant `PERMITTED_POLICIES`.
- **Storage** (EPMCDME-16437): `FILES_STORAGE_TYPE` selects the backend (`aws` S3, `gcp` GCS, `filesystem` only where
  `ENV` is `local` or `test`, refused elsewhere, `azure` keeps the feature unavailable); a dedicated `HOSTED_APPS_STORAGE_BUCKET` per
  deployment (OS decisions). The orphan sweep runs every `HOSTED_APPS_STORAGE_SWEEP_SECONDS` on one replica, under
  `async_leader_lock(HOSTED_STORAGE_SWEEP_LOCK_ID = 987654340)`, bounded by `HOSTED_APPS_STORAGE_SWEEP_BUDGET_SECONDS`
  (default 20 s); the resume token is per process, so a new leader starts from the beginning of the bucket (S-31).
- **Database pool**: hosted traffic uses its own engine per process on the core database and schema: 10
  connections, no overflow, 2.0 s checkout timeout, `hide_parameters=True`; a saturated pool answers 503
  `SERVICE_BUSY`.
- **Content gateway and launch**: content is served from `v-<version-id-hex>.<content-domain>`. `POST /{id}/launch`
  (`purpose` `published_view` or `owner_preview`, optional `version_id`) issues a ticket and a viewing session for the
  active version (`/apps/:id`, for everyone) or, for a manager, a given version (`/apps/:id/preview/:versionId`).
  Every launch is a deliberate Open or manager Preview and is audited. The card cover never launches: it shows the
  image from `GET /{id}/thumbnail` (an uploaded cover, or a validated raster bundled in the upload) or the app's
  initials (S-13).
- **Sessions**: the hosted identity is used only by the content gateway and the routes that create or end viewing
  sessions (`/launch`, `/sessions/revoke`) (CORE-16). Viewing sessions are bound to `[principal_id, sid or
  credential digest]` (CL-8); sign-out ends them (section 5.7). Only a verified token (`local_jwt`, `jwks`) supplies
  `sid`, `iat`, `auth_time` and `exp`; a `platform_jwt` token binds by its credential digest alone, from the time the
  platform accepted the request (CR-004).
- **Delete**: `DELETE /v1/hosted-applications/{id}` (202, `Idempotency-Key` required, `If-Match`, no confirmation
  body) moves an active app straight to `purging` through `_begin_purge` (CL-2..CL-4): sets `deleted_at`, clears the
  version pointers, bumps `revision` and `access_revision`, revokes the share link, removes all grants, cancels ready
  and running jobs, fences storage attempts and queues a purge job. The answer is `{state: "purging", job_id,
  application}`. The app is at once absent from lists and 404 by id. A repeated DELETE with the same key returns the
  stored 202; a new key answers 404 (CL-10). The background purge removes files and versions, refunds bytes to the
  creator and reaches `purged`; `GET /{id}/deletion-status` (manager, DELETE) reports `{state, app_id,
  remaining_prefixes, charged_bytes, etag}` (CL-7). A purged row keeps its id, project, creator id, flags and
  timestamps for 180 days, with the operation records.
- **Identity** (ID decisions): the feature requires `ENABLE_USER_MANAGEMENT=true` and works with any `IDP_PROVIDER`;
  people come only from the `users` table; the principal is `user.id`; `record_platform_identity` records the token
  the platform provider authenticated (called by the persistent user provider, and by `JwksValidatingIdp` through the
  verified-token request state). `IdentityAuthority` keeps only `leeway_seconds = max(5, JWKS_LEEWAY_SECONDS)`
  (CL-6). `bind_identity` binds the claims of a verified token (the local JWT, the JWKS wrapper). For `platform_jwt`
  (`keycloak`, `oidc`, `entraid-oidc` without JWKS, whose forwarded token the API decodes without verifying it),
  `bind_forwarded_identity` takes no claim: no `sid` (so no SID-based revocation), `issued_at` is the time the platform
  accepted the request, and the sign-in lasts at most 24 hours after the last accepted request. Sign-out ends those
  sessions through the per-principal cutoff (section 5.7), which needs no `sid`.
- **Startup**: the runtime starts in the API lifespan and never blocks platform startup: a failure is logged and
  leaves the feature closed. With `HOSTED_APPS_CONTENT_DOMAIN` set, host dispatch sends the content namespace only to
  the closed content tree and every other host to the management application.

---

## 9. REST API

All routes are under `/v1`. Management routes authenticate the platform user (`hosted_actor`: platform `User` plus
the recorded hosted sign-in; 403 `ACCOUNT_NOT_SUPPORTED` for accounts that cannot use the feature). Every route except
`/capabilities` needs the feature on (403 `FEATURE_DISABLED` otherwise). Mutations pass `mutation` (`guard_mutation`: duplicate critical
headers, exact content type, origin; 403 `CSRF_REJECTED`, 415 `UNSUPPORTED_MEDIA_TYPE`), take an
`Idempotency-Key` (16 to 128 characters of `[A-Za-z0-9._:-]`, 422 `INVALID_IDEMPOTENCY_KEY`) where a replay matters,
and accept `If-Match` (etag). Share and link routes also require user management (`user_management`). An app the
caller cannot read answers 404. Responses are `JSONResponse` with `Cache-Control: no-store` and an `ETag` header when
the body carries one; CORS exposes `ETag` and `Retry-After`. JSON mutation bodies are bounded by
`HostedManagementBodyBoundary` (408, 413).

### 9.1 Catalog and app

| Method and path | Who | Request | Response / notes |
|---|---|---|---|
| `GET /hosted-applications/capabilities` | any signed-in user | — | `create`, `view`, `disabled_reason`, `launch_policy`, `limits` (`html_bytes` 20 MiB, `zip_bytes` 100 MiB, `expanded_bytes` 500 MiB, `file_bytes` 50 MiB, `files` 5000); accounts that cannot use the feature get `create=false, view=false` with a reason |
| `GET /hosted-applications/usage` | caller | — | `byte_limit`, `available_bytes`, `stored_bytes`, `reserved_bytes` of the caller's quota |
| `GET /hosted-applications` | caller | `scope` = `mine` (default) \| `project` \| `marketplace` \| `shared`; `q` (≤ 200); `project` (repeatable, ≤ 50 names of ≤ 200); `created_by` (creator display name, ≤ 200); `shared` = `private` \| `people` \| `project` (mine only); `status` = `draft` \| `published` \| `unpublished` (mine only); `artifact_kind` = `html` \| `zip`; `version_state` (one of the validation states; mine only); `sort` = `updated_desc` \| `created_desc` \| `name_asc`; `cursor` (≤ 4096, signed, bound to the caller and filters); `limit` 1..100 (24) | `{items, next_cursor}`; any invalid value or combination answers 422 `INVALID_FILTER` (`INVALID_CURSOR` for a bad cursor) |
| `GET /hosted-applications/users` | caller | `scope` (optional; one of the four scopes, 422 `INVALID_FILTER` otherwise) | List of distinct creators `[{id, username, name}]` of the apps the caller may list in that scope (all four scopes when omitted); feeds "Created by" |
| `POST /hosted-applications` | eligible internal user with access to `project` | `AppCreate{name (≤ 120), description (1..2000 printable), project, shared? (strict bool, default false)}`, extra fields forbidden; `Idempotency-Key` | 201, app projection; `shared` as requested ("Shared with project"), `is_global` false; 409 `APPLICATION_LIMIT` at 500 active apps |
| `GET /hosted-applications/{id}` | readable | — | App projection (details even without an active version) |
| `PATCH /hosted-applications/{id}` | manager | `AppPatch{name?, description?, project?, shared?}`, at least one non-null field; `If-Match`, `Idempotency-Key` | `shared: true` refused while suspended; a changed `project` needs project access and is refused while suspended |
| `DELETE /hosted-applications/{id}` | manager (DELETE) | `If-Match`, `Idempotency-Key` | 202 `{state, job_id, application}`; irreversible |
| `GET /hosted-applications/{id}/deletion-status` | manager (DELETE) of a purging or purged app | — | `{state, app_id, remaining_prefixes, charged_bytes, etag}` |
| `GET /hosted-applications/{id}/thumbnail` | readable | — | Cover image, 204 without one, 304 on a matching validator |
| `PUT /hosted-applications/{id}/cover`, `DELETE /{id}/cover` | manager | raw PNG, JPEG or WebP body (signature checked) | Card cover |
| `POST /hosted-applications/{id}/marketplace/publish` | manager | empty body, `If-Match`, `Idempotency-Key` | Sets `is_global`; 409 `APP_SUSPENDED` while suspended |
| `POST /hosted-applications/{id}/marketplace/unpublish` | manager | same | Clears `is_global`; allowed while suspended |

List filters (`list_filters`, applied on top of the scope; each only narrows):

- `project`: the app's project is one of the names; `created_by`: `created_by.name` equals the value.
- `shared`: `private` = not `shared`, not `is_global` and no live grant of anyone; `people` = any live grant (by name
  or by link); `project` = `shared`.
- `status`: `draft` = no active version and not in the Marketplace; `published` = in the Marketplace;
  `unpublished` = has an active version and not in the Marketplace. The three never overlap (S-22).
- `artifact_kind`: on `mine` the working version's kind, elsewhere the active version's; `version_state`: the latest
  version's validation state.

App projection fields: `id`, `name`, `description`, `artifact_kind` (the working kind for managers, the active kind
otherwise), `updated_at`, `publisher.display_name`, `project`, `shared`, `is_global`, `created_by{id, username,
name}`, `user_abilities` (`read`/`write`/`delete` from `Ability.list`), `can_share` (owner), `can_moderate` (platform
admin or maintainer), `suspended`, `suspension_refuses`, `has_active_version`, `has_cover`; managers also get
`selected_version_id`, `active_version_id`, `lifecycle_state`, `revision`, `etag`, `created_at`.

### 9.2 Versions and viewing

| Method and path | Who | Notes |
|---|---|---|
| `POST /hosted-applications/{id}/versions` | manager | `VersionCreate{filename, artifact_kind, byte_size, sha256?, routing_mode, site_root_hint?, entrypoint_hint?}`; 201; charged to the creator's quota; allowed while suspended |
| `GET /{id}/versions`, `GET /{id}/versions/{vid}` | manager | Version list (paged) and one version, with `can_activate` |
| `PUT /{id}/versions/{vid}/artifact`, `POST .../reset-upload`, `POST .../complete` | manager | Upload (raw `application/octet-stream` body; `UploadReset{generation}`, `UploadComplete{generation, upload_id, sha256}`) |
| `GET /{id}/versions/{vid}/validation`, `PUT .../configuration`, `POST .../cancel`, `POST .../retry` | manager | Validation report and configuration (`VersionConfiguration{site_root, entrypoint_path, routing_mode, candidate_manifest_digest, configuration_revision}`) |
| `POST /hosted-applications/{id}/select-version` | manager | `VersionSelection{version_id}`; working version only |
| `POST /hosted-applications/{id}/activate` | manager | `VersionSelection{version_id}`; active and working version; 409 `APP_SUSPENDED` while suspended; 422 `VERSION_NOT_ACCEPTED` for an ineligible version |
| `POST /hosted-applications/{id}/launch` | readable (active) / manageable (preview) | `LaunchRequest{purpose, version_id?}`; hosted identity required; launch ticket and viewing session; 409 `NO_ACTIVE_VERSION` |
| `POST /hosted-applications/sessions/revoke` | hosted identity | Ends the caller's viewing sessions; answers `{state: "revoked"}` |

### 9.3 Sharing

| Method and path | Who | Request | Response |
|---|---|---|---|
| `GET /hosted-applications/{id}/shared-with` | owner | — | `{shared_with: [{id, username, email, name, picture, grant_source}]}` |
| `GET /hosted-applications/{id}/sharable-users?q=` | owner | `q` 2..200 characters | `{users: [{id, username, email, name, picture}]}`, ≤ 20 users the hosted recipient rule admits |
| `POST /hosted-applications/{id}/share` | owner | `{user_ids: [1..100]}` | `{shared_with: [...]}`; 409 `APP_SUSPENDED` while suspended; 404 unknown, 400 not sharable (external, inactive, deleted) |
| `DELETE /hosted-applications/{id}/share` | owner | `{user_ids: [1..100]}` | `{shared_with: [...]}`; allowed while suspended |
| `GET /hosted-applications/{id}/share-link` | owner | — | `{link: {id, token, created_at} \| null}` |
| `POST /hosted-applications/{id}/share-link` | owner | — | `{link: {...}}`, the same link on every call while live; 409 `APP_SUSPENDED` while suspended |
| `DELETE /hosted-applications/{id}/share-link` | owner, platform admin or maintainer | — | `{link: null}`; revokes the link and its grants, audited with the caller as actor; no-op without one; allowed while suspended; 403 for other readers |
| `POST /hosted-applications/share-links/open` | eligible internal user | `{token}` | `{app_id}`; 404 `SHARE_LINK_NOT_AVAILABLE`; 403 `ACCESS_DENIED` for callers outside the recipient rule; 503 `SERVICE_BUSY` on lock timeout; 401 signed out |
| `DELETE /permissions/{id}` (generic) | descriptor's `may_remove` (`grant_remover`) | — | Removes one named grant with audit |
| `POST /permissions` (generic) | — | — | Refused for named grants |

### 9.4 Admin

| Method and path | Who | Request | Notes |
|---|---|---|---|
| `POST /admin/hosted-applications/{id}/security/suspend` | platform admin or maintainer | `{reason}`; `Idempotency-Key`; optional `If-Match` | `version_id` / `delete_previous` refused (422 `INVALID_ACTION`); suspending twice is a no-op |
| `POST /admin/hosted-applications/{id}/security/reinstate` | platform admin or maintainer, not the creator | `{reason, version_id?, delete_previous?}`; `Idempotency-Key`; `If-Match` required (428 `PRECONDITION_REQUIRED` without it), because reinstate may activate another version and retire the previous one | 409 `NOT_SUSPENDED`, `REVALIDATION_REQUIRED`, `NO_OTHER_VERSION`, `VERSION_IN_USE`; 403 `INDEPENDENT_REVIEW_REQUIRED` for the creator |

The OpenAPI baseline `tests/codemie/rest_api/router_guards/baselines/openapi.json` matches these routes. Hosted
responses are untyped JSON, so response fields are not in the schema.

---

## 10. UI (codemie-ui)

### 10.1 Routes and tabs

| Route | Component | Purpose |
|---|---|---|
| `/apps` | `ApplicationsPage` → `HostedApplicationsPage` | My apps (scope `mine`) |
| `/apps/project`, `/apps/marketplace`, `/apps/shared` | `ApplicationsPage` → `HostedApplicationsPage` | Project, Marketplace, Shared with me |
| `/apps/platform` | `ApplicationsPage` → `PlatformApplicationsPage` | Platform apps: the applications configured for the deployment (D20) |
| `/apps/new` | `NewHostedApplicationPage` | Create page (`HostedApplicationForm`) |
| `/apps/:id/edit` | `EditHostedApplicationPage` | Edit page (`HostedApplicationForm`); non-managers are sent to the details page |
| `/apps/share/:token` | `HostedSharePage` | Share-link redemption |
| `/apps/:id/manage` | redirect | To `/apps/:id`, so older links keep working |
| `/apps/:id/preview/:versionId` | `HostedApplicationViewer` | Manager preview of one version |
| `/apps/:id/view` | `HostedApplicationViewer` | Viewer of the active version, for everyone |
| `/apps/:id` | `HostedApplicationDetails` | Details (as for assistants): Open, Edit, versions, sharing, Marketplace, lifecycle, admin actions; the cover read-only |
| `/applications` | redirect | To `/apps`; platform applications keep their `/applications/...` URLs |

- The tabs come from one registry, `APPLICATION_COLLECTIONS` (`applicationCollections.tsx`, ids in
  `APPLICATION_COLLECTION_IDS`: `mine`, `project`, `marketplace`, `shared`, `platform`), each with label, icon,
  subtitle and `provider` (`hosted` or `platform`). `ApplicationsPage` picks the page from the provider of the
  current tab; the Platform apps path is found from the registry, not a literal.
- Without hosted applications (`capabilities.view` false) every `/apps` catalog route shows Platform apps and
  redirects to the Platform apps tab; the viewer and details routes send people there too
  (`useHostedFeatureRedirect`). With `features:hostedApplications` off the store never calls the hosted API.
- Any other `/apps/<segment>` falls through to `/apps/:id` and shows the not-available page.
- Paths come from `applicationsRoutes.ts` and `hostedRoutes.ts` (`share.path`, `share.url(token)`,
  `application(id)`, `manage(id)`, `preview(id, versionId)`); route ids from `constants/routes.ts`. Page titles exist
  for exactly the hosted route ids (`pageTitles.ts`), and a test keeps them in step.
- The main navigation shows "Applications" linking to `/apps` when hosted apps can be viewed or platform
  applications exist, and highlights it on `/apps/*` and `/applications/*` (`activeRoutes`).

### 10.2 Access predicates and actions

`src/pages/applications/hosted/hostedAccess.ts` is the only module that reads the access fields (UI-12):

| Predicate | Source |
|---|---|
| `isManager` | `canEdit(app)` (`write` in `user_abilities`) |
| `canRemove` | `canDelete(app)` |
| `canShare` | `can_share` |
| `canModerate` | `can_moderate` |
| `canOpen` | `has_active_version` |
| `isSuspended` | `suspended` |
| `suspensionRefuses(app, action)` | `action in app.suspension_refuses` (the server's list, S-19) |
| `isCreator(app, userId)` | `isOwner` on `created_by` ("Created by you" byline only for the creator) |

`hostedActions(app)` is the one ordered menu used by the card, the details header and the viewer
(`useHostedCardActions`): Open (disabled without an active version); for managers Edit, Upload new version, and
"Publish to Marketplace" (disabled by `suspensionRefuses(app, 'publish')`) or "Remove from Marketplace"; Share when
`canShare`; Delete when `canRemove`; for `canModerate`, Suspend or Reinstate, and "Revoke share link" when the
moderator is not the owner (the owner revokes from the Share popup). The admin revoke asks for confirmation and then
says only "No live share link remains", because the API answers `{link: null}` whether or not a link was live.
Activate in the version list also requires the version's own `can_activate` (UI-23). Wording: "Activate version",
version tag "Active".

### 10.3 Create and edit form

Create and edit are pages, as for skills and assistants: `/apps/new` and `/apps/:id/edit` render one
`HostedApplicationForm` with the Cancel and Save actions in the page header. The catalog's "Create application" opens
the create page; the details page has an Edit button for managers, and the card and viewer menus link to the edit
page. The form has a project selector and a "Shared with project" switch bound to `shared`, as in the assistant
form. Edit sends a PATCH with only the changed fields, so an unchanged `shared` or `project` is never sent
and a suspended app already shared with its project can still be renamed (UI-16). All fields and Save are disabled without
`write`; the switch follows `write` (any manager), while Share follows `can_share` (UI-26). Changing the project is
disabled when `suspensionRefuses(app, 'move')`; turning the switch on is disabled when `suspensionRefuses(app,
'share_on')`; turning it off stays enabled. Create can upload the first artifact on the same page; a created draft
stays when the upload is interrupted and its details page opens. The cover is part of the same form
(`HostedCoverField`, as the assistant form's logo): upload, replace or remove a PNG, JPEG or WebP up to 1 MiB, sent
on Save after the metadata (on create, after the app is created and before the artifact); a failed cover upload
keeps what was already saved. On create, the form adds `shared: true` to the POST body when the switch is
on, and the app is created shared with its project (section 9.1).

### 10.4 Generic sharing components

`src/components/sharing/`:

- `SharePopup`: no entity and no store imports; callbacks for search, share, unshare and an optional shared-with
  loader, all texts and toasts, `canGrant`, an optional `notice` above the picker, `entityKey` / `refreshKey` to reload
  the people with access, and an `extra` slot (UI-5, UI-20). A failed recipient search shows "User search failed.
  Please try again." in the popup.
- `SharedWithList`: "People With Access", "via link" badge and the via-link note, read-only or removable; the
  assistant `SidebarSharedWith` re-exports it.
- `SharedWithLoadError`: "Could not load the people with access." with "Retry loading people".
- `ShareLinkSection` (`getLink`, `createLink`, `revokeLink`, `buildUrl(token)`, `canCreate`): on open it calls GET
  `/share-link`; "Copy share link" copies at once when a link is loaded, otherwise POSTs and copies the new link; a
  failed copy asks for a second click; a failed load shows "Could not load the share link." with Retry, and copy waits
  for a successful load. "Revoke link" is shown only while a live link exists. There is no plain "Copy link" for the
  app address (D-U2).
- `sharingClient.ts`: `createSharingClient(basePath, {guard})` builds one sharing client per entity over
  `GET {id}/shared-with`, `POST|DELETE {id}/share`, `GET {id}/sharable-users?q=`; it is exposed once on each store
  (`assistantsStore.sharing`, `hostedApplicationsStore.sharing`, the hosted one guarded by the authentication epoch).
  `bindSharing(client, id, currentUserId)` binds it to one item for the popup and leaves the current user out of the
  search results and the people with access.
- `useSharedWith(client, id, {enabled, currentUserId, reloadKey})` loads "Shared with" for both details pages with the
  same rules: only when enabled (the owner), the current user left out, the list kept on screen while it reloads, and
  a retry after a failure.
- `ShareAssistantPopup` stays at its path as a thin wrapper over `assistantsStore.sharing`; it passes the shared-with
  loader only when `isOwner` (UI-6, UI-21). The assistant details sidebar uses `useSharedWith` and
  `SharedWithLoadError`.
- `HostedSharePopup` renders only when `canShare`, with `ShareLinkSection` in the slot. While suspended, search,
  grant and "Copy share link" are disabled and the popup says "This application is suspended. You can remove access,
  but not share it until a moderator reinstates it."; removing people and "Revoke link" stay (UI-14). After revoking
  the link the popup reloads "People with access" and the details page reloads "Shared with".

### 10.5 Share page

`HostedSharePage` (`/apps/share/:token`):

- On load it calls `POST share-links/open {token}` once per token and attempt (guarded against React StrictMode
  re-runs), then navigates to `/apps/:id/view` with history replace, so the token leaves the address bar and
  history (UI-1, UI-2).
- 401, 403 and 404 show the not-available page. A local-auth 401 that `api` turns into a sign-in redirect, and an SSO
  opaque redirect (status 0), keep the neutral "Opening application..." state so the normal sign-in or
  session-expired handling runs and the token is redeemed on return (UI-17, UI-18). A 503 shows "Applications are
  busy right now" with "Try again". Other errors show the not-available page with a message (UI-3). No error text or
  log line carries the token.
- No referrer policy is set; the browser default applies, as for `/share/conversations/:token` (UI-11).

### 10.6 Admin dialogs

`HostedSecurityDialog`: Suspend and Reinstate, reason required, sends `{reason}` only (UI-4). Actions use the details
etag, not a cached card etag; a new idempotency key is used whenever the reason changes.

### 10.7 Catalog filters (`HostedCatalogFilters`)

| Filter | Tabs | API parameter |
|---|---|---|
| Search | all hosted tabs | `q` |
| Project (multiselect, server-side search) | all hosted tabs | `project`, repeated |
| Created by | all hosted tabs | `created_by` = creator display name; options from `GET /hosted-applications/users?scope=<tab>` (`userStore.loadHostedApplicationsUsers`) |
| Shared: All / Not shared / With people / With project | My apps | `shared` = `private` / `people` / `project` |
| Status: All / Draft / Published / Unpublished | My apps | `status` = `draft` / `published` / `unpublished` |

`HOSTED_COLLECTIONS` (`hostedCollections.ts`) is the per-tab registry: the API scope, the URL filter keys the tab
accepts (`project`, `created_by` on every tab; `shared` and `status` on My apps), the empty state and whether the tab
offers Create. The URL reader (`readHostedCatalogSearch`), the filter bar and the one loader
(`loadHostedCollection` → `hostedCatalogParams`) read only those keys, so a tab never sends a parameter its scope
rejects; unknown values are ignored. The UI always sorts by `updated_desc` and sends neither `artifact_kind` nor
`version_state`. Switching tabs clears the filters.

### 10.8 Other UI rules

- As for assistants, a card click opens the details page (`/apps/:id`), which carries Open, Edit, Share and
  New version; the app itself launches only from the card's or the details page's Open.
- With `has_active_version` false the card, details and viewer show "No active version yet"; `/apps/:id` launches
  nothing, for managers too; managers keep the per-version Preview (UI-8, UI-13). On the card a manager gets the
  next step instead of Open, leading to the details page: "Upload version" without a selected version, "Activate
  version" when a selected version may be activated, else "Manage"; anyone else sees Open disabled with the tooltip
  "No active version yet". The cover hides the artifact-kind badge until a version is active.
- Cards never show a live or static preview of the app; the cover is the thumbnail image or the app's initials,
  and an unavailable cover leaves the initials in place (S-13).
- A suspended app shows "Suspended" to managers on the card, details and viewer.
- The viewer subtitle tells managers whether they preview a version or see their app, and tells viewers who
  published it ("content provided by its publisher").
- Types (`src/types/entity/hostedApplication.ts`): `HostedApplication` extends `Entity` (`user_abilities`) with the
  projection fields of section 9.1; `HostedScope`, `SuspensionRefused`, `HOSTED_SHARED_FILTERS`,
  `HOSTED_STATUS_FILTERS`; feature-level `HostedCapabilities` (`create`, `view`, `disabled_reason`, `limits`).
  `SharedWithUser` has an optional `grant_source`.
- The hosted store (`src/store/hostedApplications.ts`, Valtio) keeps state per signed-in person (`followUser`),
  fences responses with an authentication epoch, and validates ids before building paths. Uploads go through
  `api.putBinary` (no redirects followed) and resume after interruptions (`store/utils/hostedApplicationUpload.ts`);
  launches are validated against the expected origin (`utils/hostedApplicationLaunch.ts`).

---

## 11. Security and privacy decisions

| Topic | Decision |
|---|---|
| External users | No access to any hosted app (Marketplace included), cannot create apps, cannot be granted, cannot open links (403); `hosted_actor` refuses them with `ACCOUNT_NOT_SUPPORTED` |
| Role source | Admin, maintainer, project-admin and membership flags are read from `users` / `user_projects` (`platform_user`), never from IdP roles or `ENV=local`, in Python and SQL (CR-001 of access-core); local tests must set `users.is_admin` |
| External type | SQL eligibility uses `config.EXTERNAL_USER_TYPE`, as the Python predicates do (CR-002 of named-grants) |
| Not available vs forbidden | An app the caller cannot read answers 404 everywhere; readers who are not the owner get 403 on owner routes |
| Share-link token in transit | Open is a POST with the token in the JSON body, never in an API path; the open response, launch tickets and gateway requests never contain it; it never reaches the content origin |
| Share-link token in logs | Lookup by `token_sha256`; `hide_parameters=True` on the hosted engine; no log, error body or event carries it |
| Share-link token at rest | Plaintext beside the hash so the owner can re-read the same link; accepted risk (CR-001 of share-links), as for conversation tokens |
| Share-link token in the UI | Path form `/apps/share/<token>` (as conversation links); the document request writes it to the UI nginx and ingress access logs, and requests made before the redirect may carry it in Referer to the same origin; both accepted (UI-24, UI-25). History replace removes it from the address bar and history |
| Referrer policy | Browser default (UI-11) |
| Redeem on load | A link preview, prefetch or accidental visit by a signed-in internal user grants view access; accepted (UI-1) |
| Link probing | One 404 for unknown, revoked, garbage, suspended or purging-app tokens (SL-11); no rate limit on open |
| Recipient search exposure | Any owner can search internal users' names and emails (2-character minimum, 20 results), as in assistant sharing (P-C3) |
| Grant scope | View only; no level field accepted; edit access waits for RBAC (D9) |
| Audit | Grant and link changes are strict activity events in the same transaction; nothing is recorded with `ACTIVITY_EVENTS_ENABLED` off (the default) |
| Independent review | Reinstate refused to the app's creator |
| Sign-out cutoff | Applies to management writes as well as viewing sessions; a management write with no recorded hosted identity is refused |
| Unverified forwarded token | A `platform_jwt` token (decoded without signature verification) feeds no hosted identity claim: no `sid`, `iat` or `auth_time`, so a caller who controls the header cannot choose its session id or escape revocation; it binds by its digest and is revoked through the per-principal cutoff (CR-004) |
| Personal data after purge | A purged row keeps only the creator id; username and display name are cleared (CHECK constraint) |
| Tenancy | The access rule is no tenant boundary beyond the project: the Marketplace means every eligible internal user of the deployment |
| CSRF | `share-links/open` and every hosted mutation pass `guard_mutation` (duplicate critical headers, exact content type, origin) |
| API client headers | The UI `api` client refuses caller-set `authorization`, `cookie`, `host` and `user-id` headers |

---

## 12. Operations and rollout

- **Feature switch**: customer-config component `features:hostedApplications` (`enabled: false` by default, name
  "Custom Applications"); `FEATURE_HOSTED_APPLICATIONS=true|false` overrides it. One switch turns the feature on for
  the UI and the API.
- **Prerequisites**: `ENABLE_USER_MANAGEMENT=true` (otherwise the feature stays off with the startup reason "Hosted
  applications need user management (ENABLE_USER_MANAGEMENT=true)"); `ACTIVITY_EVENTS_ENABLED=true` for the grant
  and link audit (S-25).
- **Settings** (`config.py`): `HOSTED_APPS_CONTENT_DOMAIN`, `HOSTED_APPS_CREDENTIAL_SECRET` (≥ 32 characters),
  `HOSTED_APPS_STORAGE_DIR` (filesystem only), `HOSTED_APPS_STORAGE_BUCKET`, `HOSTED_APPS_STORAGE_S3_GRACE_SECONDS`
  (30), `HOSTED_APPS_STORAGE_SWEEP_SECONDS` (3600), `HOSTED_APPS_STORAGE_SWEEP_BUDGET_SECONDS` (20),
  `HOSTED_APPS_VALIDATOR_URL`, `HOSTED_APPS_VALIDATOR_KEY` (≥ 32 characters), `HOSTED_APPS_QUOTA_BYTES` (2 GiB).
  The runbook describes each.
- **Deployment**: Helm templates under `deploy-templates/templates/hosted-apps/` (namespace, validator Deployment,
  Service and NetworkPolicy, clamd Deployment, ConfigMap, Service and NetworkPolicy, default-deny NetworkPolicy,
  content Ingress) with values in `deploy-templates/values.yaml`; plain manifests in
  `hosted-applications/kubernetes.yaml`; the validator image from `hosted-applications/Dockerfile.validator` with
  `requirements-validator.txt` and `clamd.conf`. API and validator images are built from the same commit (policy and
  protocol must match).
- **Migrations**: the five hosted revisions run in the normal migration step, with traffic gated (runbook §10).
- **Monitoring**: fixed-label OpenTelemetry instruments on the `codemie-business-metrics` meter (content requests
  and denials, directory, validation, sessions, sign-out revocation; `observability.py`); labels never carry users,
  apps, versions or paths. Runbook §11 lists them.
- **Rollout gate** (ID-10): decode one real `oidc` / `entraid-oidc` staging token (JWT, `iat`, `exp`, `sid`,
  lifetime) before release.
- **Merge**: both MRs land together (P-C1). The codemie MR touches dependencies, so its description asks a reviewer to
  post `/sanity` (P-C4).

---

## 13. Changes outside the feature

### 13.1 Backend (codemie)

- `core/ability.py`: `levels_covering`, `PreloadedGrants`, `preloaded_grant_covers`, `GRANT_LOADER` /
  `load_class_grants`, `Role.GRANTED` (last role), `Owned.is_granted_to` (default no grant), `Ability(user, *,
  grants=None)` with the lazy per-class `grants_for`, the `HostedApplications` permission entry, and `GRANTED` under
  READ for `Assistant`.
- `rest_api/models/permission.py`: `ResourceType.HOSTED_APPLICATION`; `_lookup` holds only `DATASOURCE`;
  `grant_source`, `source_link_id` (FK to `share_links`) and `permissions_grant_source_check`; `get_for` reads
  APPLICATION rows at a covering level; the `ShareLink` model; `live_grants` and `grant_exists`.
- `rest_api/models/user_management.py`: `eligible_internal_user(users)`.
- `rest_api/models/assistant.py`: `is_shared_with` has project and Marketplace terms only; `is_granted_to` reads the
  loaded grants; `get_by_ids` uses one `Ability` per list.
- Assistant sharing: `service/assistant/grant_descriptor.py` (new), `AssistantShareService` delegating to the shared
  module, `assistant_repository` (one `Ability` per page; the per-user grant condition from
  `granted_ids_statement`), data step of `f8d24a6c0b17`.
- `service/permission/`: `named_grant_service.py`, `named_grant_types.py`, `share_link_service.py`,
  `share_link_types.py` (new); `permission_creation_service.py` and `permission_deletion_service.py` route by
  `is_named_grant`.
- `service/activity/`: `ActivityDomain.HOSTED_APPLICATION`, `ActivityEntityType.HOSTED_APPLICATION`, and the
  repository methods `async_insert_strict`, `insert_many_strict`, `async_insert_many_strict`.
- `repository/user_repository.py`: `aquery_active_users` takes `limit` and an extra `condition`.
- Sign-out: `routers/local_auth_router.py` (`POST /v1/local-auth/logout`) and `routers/user.py`
  (`GET /v1/user/log_out`) revoke hosted sessions (best effort, 2 s).
- Identity: `security/user_providers/persistent.py` calls `record_platform_identity` after authentication;
  `security/idp/jwks_validating.py` stores the verified claims and token on the request state.
- `rest_api/main.py`: hosted runtime start and close in the lifespan, the hosted router, `HostedManagementBodyBoundary`
  middleware, CORS `expose_headers` adds `ETag` and `Retry-After`, the `HostedApplicationError` handler, and host
  dispatch when a content domain is set.
- One `Ability` per list in `guardrail_service.py` and the skill assistant list; comments in `routers/utils.py` and
  `skill_service.py`.
- `configs/config.py`: the `HOSTED_APPS_*` settings; `config/customer/customer-config.yaml`: the
  `features:hostedApplications` entry; `src/external/alembic/env.py`: hosted model imports.
- `.ai-run/guides/integration/cloud-integrations.md`: the hosted storage exception (client setup reused, dedicated
  bucket).
- Dependencies (`pyproject.toml`, `poetry.lock`): adds `google-cloud-storage ~3.13.1`; removes `python-jose`; bumps
  `langchain` 1.3.9, `langchain-core` 1.4.6, `langchain-anthropic` 1.4.6, `langgraph-checkpoint ^4.1.1`,
  `aiosmtplib ^5.1.2`, `pyjwt ^2.15.0`, `setuptools ^83.0.0`. Repository files: `.dockerignore` (local data, QA
  artifacts, docs and tests kept out of the build context), `.gitignore` (`output/playwright`), `.gitleaks.toml`
  (allowlist for generated caches, a gitignored QA manifest and downloaded ClamAV signatures). An unrelated test fix
  in `tests/codemie_tools/file_analysis/email`. These inherited changes were reviewed and kept (S-11).

### 13.2 UI (codemie-ui)

- Shared components: `Card` (optional `preview` slot with a 2:1 cover area), `Navigation` (Applications item, see
  10.1), `NavigationLink` (`activeRoutes`), `NavigationProfile` (awaits sign-out and reports a failure:
  "Sign-out is pending..."), `MobileTopBar` (Applications section), `NavigationMore` (the menu always flips at
  viewport edges; the trigger tooltip hides while the menu is open), `Select` (`ariaLabel` for selects without a
  visible label), `MultiSelect` and `ChipWrapper` (chip text for options outside the current list; Escape consumed
  before a containing modal), `FilesDropzone` / `FileDropArea` / `DropzoneArea` (`accept`, `disabled`, `label`,
  `hint`, `maxFileSizeBytes`; a fieldset disables every control), `Filters`, `Switch`, `RadioButton` (accessible
  names), `PageLayout` (header does not shrink), `DetailsCopyField`, `ApplicationGrid` (layout), `UserAvatar` moved
  to `src/components/Avatar/`.
- Assistant sharing UI moved onto `src/components/sharing/` (10.4); `assistantsStore.sharing` replaces the per-store
  sharing methods.
- `utils/api.ts`: `putBinary` (raw uploads, no redirects followed) and `buildHeaders` (caller headers merged,
  identity headers reserved); `store/user.ts`: `loadHostedApplicationsUsers(scope)`; `hooks/useInitialDataFetch.tsx`:
  hosted store follows the signed-in user and loads capabilities; `hooks/useVueRouter.tsx`: route parameters are
  stringified before `generatePath`.
- `constants/routes.ts`, `constants/pageTitles.ts`, `constants/featureFlags.ts`, `utils/featureFlags.ts`
  (`isHostedApplicationsEnabled`), `router.tsx`.
- `tailwind.config.ts` (card height variable, `card-cover` aspect ratio); `vite.config.ts` (`bundledDev: false`,
  because module federation needs unbundled development transforms).
- Dependencies (`package.json`, `package-lock.json`): bumps `axios` 1.20.0, `diff` 8.0.4, `dompurify` 3.4.16,
  `js-yaml` 4.3.2, `lodash` 4.18.1, `react-router` 7.18.4, `postcss` 8.5.29; removes `uuid` and `vee-validate`; adds
  overrides for transitive packages (`adm-zip`, `body-parser`, `brace-expansion`, `browserslist`, `compression`,
  `form-data`, `immutable`, `markdown-it`, `minimatch`, `morgan`, `path-to-regexp`, `postcss-selector-parser`,
  `proxy-addr`, `qs`, `source-map-js`, `baseline-browser-mapping`, and `axios` / `dompurify` pinned to the direct
  versions). `.gitignore` adds local QA output and tool caches.

---

## 14. Decision log

Columns: ID, question, answer, decided by. "user" means the requester decided; "spec" means the spec writer decided
and the user approved the spec; "PO" means proposed by the product-owner role and approved with the story; "ticket"
and "design" are decisions of the original author's tickets.

### 14.1 Product and access

| ID | Question | Answer | Decided by |
|---|---|---|---|
| D1 | Sharing with named people, "Shared with me" and link sharing? | Yes, all through the same "shared with" check as "Shared with project" and Marketplace publication | user |
| D2 | Project and Marketplace settings? | Two independent settings, as for assistants: "Shared with project" and Publish / Unpublish; unpublish leaves project sharing as it was | user |
| D3 | How is an app published to everyone? | Publish / Unpublish to the Marketplace, without review and without categories | user |
| D4 | UI for project sharing? | The project selector and "Shared with project" switch of the assistant and skill forms | user |
| D5 | Folders and trash? | None for hosted apps; a platform-wide ticket for all catalog entities | user |
| D6 | Name of switching the version viewers see? | "Activate version"; "Publish" means only the Marketplace | user |
| D7 | Can platform admins open and manage any hosted app? | Yes, as for other entities | user |
| D8 | How does a link work? | Opening a link once makes the signed-in internal user a view-only recipient, shown like a grant and listed in "Shared with me"; revoking the link removes everyone who got access through it | user |
| D9 | Access level and store of a named share? | View only, stored in the platform `permissions` table as in assistant sharing (EPMCDME-14786), so RBAC reworks one place | user |
| D10 | Can platform admins take an app down from the UI? | Yes: Suspend / Reinstate with a reason in the app's actions | user |
| D11 | How are recipients picked? | User search and Share popup aligned with assistant sharing (!4256, !1901) | user |
| D12 | Whose quota does a manager's upload use? | The app creator's | user |
| D13 | What does a recipient see before any version is activated? | Card and details with "No active version yet", without content | user |
| D14 | Who may share with named people and by link, and who sees the list? | Only the owner, as for assistants; who it is shared with appears only in the owner's details sidebar | user |
| D15 | Organization and sign-in-issuer keys on hosted data? | None: they hold one constant per installation and an issuer change would orphan data. Viewing sessions are bound to the user and the sign-in session | user |
| D16 | How is link sharing turned on? | As for conversations: the first "Copy share link" creates a link, later clicks return it; "Revoke link" kills it and removes its recipients; the next copy creates a new link. Unlike conversations, revocable | user |
| D17 | Who grants and revokes by name and creates the link? | Only the owner, as in !4256. A platform admin can delete a single grant through the generic permissions API, with audit, and revoke the link (S-17) | user |
| D18 | Who builds the parts shared with assistant sharing? | This feature, generic over the resource type and shaped like !4256: user grants in `permissions`, level check, owner-only granting, recipient search, grant audit, who-has-access listing, removal on delete, audited admin delete | user |
| D19 | May a person the owner removed get back in through the same live link? | Yes: everyone who opens a live link is added; to cut access for good the owner revokes the link | user |
| D20 | Where do the platform's configured applications appear? | In their own tab, "Platform apps" | user |
| D21 | What does a non-manager see for a suspended app? | Nothing: absent from lists, "not available" when opened; only managers see it marked "Suspended" | user |
| D22 | What may managers do while an app is suspended? | Open, edit and upload versions; activating, publishing and sharing wait for Reinstate. Suspend changes no sharing setting; Reinstate brings back exactly the sharing that was there | user |
| P-C1 | How do the two repositories land? | Together: the UI depends on the backend contract | user |
| P-C2 | How do the hosted migrations relate to assistant sharing's? | Both add a value to the same enum and use the same `permissions` table; the hosted chain starts after `0ba28b31fa53` | user |
| P-C3 | Limits of the user search? | The same exposure as assistant sharing; any limit is decided once for both | user |
| P-C4 | `/sanity` | The codemie MR touches dependencies, so its description asks a reviewer to post `/sanity` | repository rule |
| D-C1 | Keep hosted data written by earlier revisions of the feature? | No; no data migration and no downgrade path for them | user |
| M-2 | `If-Match` on administrator actions? | Optional on suspend, so an emergency takedown works without opening the app; required on reinstate, which may activate and retire versions | user |
| M-1 | How many migrations? | Two under new ids: the hosted tables (`e3b7c1a95d42`) and the shared platform changes (`f8d24a6c0b17`: resource type, `share_links`, permissions grant columns, assistant grant source), so the shared part is reviewed and rolled back on its own | user |
| CORE-2 | What may managers do with sharing during a suspend? | Narrow access (unpublish, `shared` off, revoke); widening is refused | user |
| CORE-3 | Does new content of a suspended app go through validation? | Yes: uploads are validated and scanned, configured if needed, and become ready; activation waits for reinstate | user |
| CORE-4 | Does a suspend block manager preview? | No: managers may preview, non-active versions included; viewers may not | user |
| CORE-5 | Where is activation guarded? | On the activate step (`publication.py`), not on configuration | spec |
| CORE-6 | How is the suspend rule kept from drifting? | One home, evaluated on the locked row in the writing transaction; `can_activate` defined once in the version projection | spec |
| CORE-7 | How does a fixed version go live without re-exposing the taken-down one? | Reinstate takes an optional ready version (default: current active); an ineligible current version does not block when another is chosen | user |
| CORE-8 | What happens to the taken-down version? | Reinstate may retire the previously active version in the same action, only with another version chosen | user |
| CORE-9 | How does reinstate activate and remove versions? | It calls the shared `activate` and `retire_version` steps | spec, user |
| CORE-10 | May a working selection block reinstate? | No; reinstate checks only the version it makes active | spec |
| CORE-11 | Is moving to another project allowed while suspended? | No; it could widen access after reinstate | spec |
| CORE-12 | Is `project` required at create? | Yes, as for assistants | spec |
| CORE-13 | Field names? | Assistant names `project`, `shared`, `is_global` | spec |
| CORE-14 | Routes per tab or a parameter? | One list route with `scope` (`mine`, `project`, `marketplace`, `shared`) | spec |
| CORE-15 | May an app be published without an active version? | Yes; the card shows no active version (D13) | spec |
| CORE-16 | Which routes use the hosted identity? | `/launch` and `/sessions/revoke`, beside the platform `User`, because they create and end viewing sessions | spec |
| CORE-17 | Where does the gateway read roles? | From `users` and `user_projects`, not token claims | spec |
| CORE-18 | Which projects does a platform admin's Project tab show? | Every project's apps, private ones included, as for skills; they also reach any app by id | user |
| CORE-19 | How is a non-owner manager admitted and charged? | The management transaction is keyed by the platform actor id and locks the creator's quota row | spec |
| CORE-20 | Lock order of `managed_app`? | Creator's quota row before the app row, then re-check; one order quota → app → version → job → attempt | spec |
| CORE-21 | How are the app limit and create replay serialized? | Create locks the creator's quota row before counting and before the idempotency lookup | spec |
| CORE-22 | Does management on the platform `User` keep the hosted sign-out cutoff? | Yes; management writes apply it whenever a hosted identity is recorded | spec |
| CORE-23 | A management write with no recorded hosted identity? | Refused | spec |
| CORE-24 | How does accounting find the charged holder? | Each storage attempt stores `quota_principal_id` | spec |
| CORE-25 | Where does the access module live? | `core/hosted_applications/access.py`, so the model can delegate `Owned` without an import cycle | spec |
| CORE-26 | One visibility condition or two? | Separate `readable` and `manageable`, each taking a user-id expression (gateway view vs preview) | spec |
| CORE-27 | Who is "internal"? | Not external (`user_type <> EXTERNAL_USER_TYPE`), so service accounts count; external users get no access at all | spec |
| CORE-28 | Does a deactivated creator hide the app? | No (D7) | spec |
| CORE-29 | Independent-review guard? | Reinstate refused to the app's creator (`created_by`) | spec |
| CORE-30 | What ends viewer sessions? | The per-request rule; `access_revision` bumped only on delete and purge | spec |
| CORE-31 | How many writers per flag? | One: `PATCH` for `shared`, `/marketplace/*` for `is_global` | spec |
| CORE-32 | What does `can_share` gate? | Named and link sharing only (owner); project sharing is changed by any manager through `PATCH` | spec |
| CORE-33 | Flags in the response? | `user_abilities`, `can_share`, `can_moderate`, `suspended`, `suspension_refuses`, `has_active_version`; no hosted capability flags; values per persona tested | spec |
| CORE-34 | Where must the gateway's added cost be shown? | Two queries per content request; p95 authorize latency at a stated concurrency recorded in the MR | story constraint |
| CR-001 (core) | Source of admin and project roles? | The database (`users`, `user_projects`) through `platform_user`, for projection abilities, the admin gate and `is_managed_by`; a divergence test guards it | user |
| CR-005 (core) | Does DELETE write `shared` / `is_global`? | No; only `PATCH` and `/marketplace/*` write the flags | user |
| CR-006 (core) | Personal data of purged rows? | Purge keeps only `created_by.id`; a CHECK requires empty username and name | user |
| S-16 | Grant term action-aware before edit access exists? | Yes: `Role.GRANTED` checks whether the loaded level covers the action, registered under READ for hosted apps and assistants; `is_shared_with` has no grant term; SQL `manageable` takes WRITE or DELETE and the delete path asks DELETE; the by-name upsert raises a level the stored grant does not cover; only READ is requestable | user |
| S-17 | May a platform admin or maintainer revoke a live share link without suspending the app? | Yes: `DELETE /{id}/share-link` admits the owner or `grant_remover` through the link descriptor's `may_revoke`; the single revoke step, audited with the admin as actor; no new route. Granting and link creation stay owner only | user |
| S-19 | One source of the suspension refusal list? | `SUSPEND_GATED_ACTIONS`, applied through `refuses` / `suspend_refuses` / `refuses_sql`; every app projection carries `suspension_refuses`, which clients read | user |
| S-21 | Refuse delete of a suspended app? | No: deletion takes the content down as suspension does, blocking it would not stop a re-upload, and retaining evidence is out of scope | user |
| S-22 | Status filter overlap? | `draft` is "no active version and not in the Marketplace", so Draft, Published and Unpublished never overlap | user |
| S-24 | Imports of the access module into `rest_api` models? | Module-level imports of the `users`, `user_projects` and permission terms, pinned by a test; the direction `core` → `rest_api/models` stays until those shared tables move to a data layer | user |
| S-26 | Naming? | Tables `hosted_application_*`, classes `HostedApplication*`, "version" throughout (columns, constraints, API paths `/versions` and `select-version`, fields `version_id` and `version_state`, error codes `VERSION_*`, audit actions `version.*`, capabilities `version_host_prefix` and `version_id_encoding`); content hosts `v-<id>` / `w-<id>` | user |
| S-28 | Where do platform admins moderate? | From the app's own actions (Suspend, Reinstate, Revoke share link); there is no moderation list, scope or tab; `access.is_moderator` serves the actions | user |
| S-33 | Which manager actions would an edit (WRITE) grant cover? | Not decided; no WRITE grant exists, only the seam. The action set is decided before edit access is enabled (section 16.2) | user |

### 14.2 Named grants

| ID | Question | Answer | Decided by |
|---|---|---|---|
| NG-1 | How does a grantee pass the one access rule? | Through the grant term of `readable` (SQL) and `Role.GRANTED` (Python), not a `check_resource_permissions` side path | spec |
| NG-2 | How do datasource rows of any level keep passing READ? | Levels ordered READ < WRITE < DELETE (`levels_covering`), not exact match | spec |
| NG-3 | How do datasource lookups stay as they are? | `get_for` reads APPLICATION rows only | spec |
| NG-4 | Where do the user-management gate and the recipient policy live? | Inside `live_grants`; the recipient policy is the descriptor's `recipient` | user |
| NG-5 | Where are grant changes recorded? | Strict activity events as in !4256, not `hosted_application_operations` or a new store | user |
| NG-6 | What does a default deployment (events off) record? | Nothing, as in !4256; documented limitation | user |
| NG-7 | How does one audit payload serve every type? | `resource_type` / `resource_id`, plus the descriptor's `audit_attributes` | spec |
| NG-8 | How is a link-to-name upgrade audited? | `before`/`after` stay levels (`READ`→`READ`); the previous source in `previous_grant_source` / `previous_source_link_id` | user |
| NG-9 | How do the validation join and the upsert see the same row? | Both run under the resource's row lock; link grants are created under the same app-row lock | user |
| NG-10 | Sync only? | The shared parts run on sync and async sessions | spec |
| NG-11 | What if no grants are loaded? | No grant (fail closed), never a per-object lookup; `Ability` loads a class's grants itself (S-29) | spec |
| NG-12 | Where is a live grant defined? | `live_grants` is the one statement under every grant consumer of every `NAMED_GRANT_TYPES` type | user |
| NG-13 | What id bridge does hosted need? | None: UUID to UUID (`resource_key` is the identity) | user |
| NG-14 | How will RBAC read the level? | Loaded grants are a map of id to level, not an id set | spec |
| NG-15 | How does hosted pass grants it already read? | `PreloadedGrants` in `core`; `Ability(user, grants=...)` | user |
| NG-16 | Where does hosted read its grants? | Once per transaction on its own async pool (`apreload`) | spec |
| NG-17 | Where do each type's facts live? | A descriptor per named-grant type, not `get_by_id` through `_lookup` | user |
| NG-18 | How to avoid import-order preconditions? | `NAMED_GRANT_TYPES` is a static map resolved lazily; nothing registers at import | user |
| NG-19 | What does an unregistered type get on generic delete? | Refused (`PermissionAccessDenied`) | user |
| NG-20 | May `check_resource_permissions` admit a grantee? | No; `type_for_instance` serves datasources only | user |
| NG-21 | How does the remover rule stay the only rule on generic delete? | For named grants, decide through the descriptor's `resolve` before `Ability` | spec |
| NG-22 | One row per person with two sources? | A by-name grant replaces a link grant (upsert) | spec |
| NG-23 | Repeated id in one request? | De-duplicated before validation and upsert | user |
| NG-24 | How is a request for edit access refused? | `ShareUsersRequest` forbids extra fields | spec |
| NG-25 | How is one request bounded under the owner's locks? | `user_ids` capped at 100 | spec |
| NG-26 | Cost of large requests? | Grant, revoke and remove-all are one statement each, with one audit flush | spec |
| NG-27 | How does hosted list "Shared with me"? | `scope=shared` | spec |
| NG-28 | What does a self-grant show? | "Shared with me" excludes apps the caller created | spec |
| NG-29 | Do the sign-out cutoff and suspend refusal apply to grants? | Yes: grant and revoke run in the hosted management transaction | spec |
| NG-30 | Sharing during a suspend? | Grant refused, revoke allowed | spec |
| NG-31 | When does a deleted app lose its grants? | At `_begin_purge`, in the delete transaction | spec |
| NG-32 | How is a link grant recorded? | Two nullable columns `grant_source`, `source_link_id` with a CHECK | spec |
| NG-33 | Which role source decides generic delete of a named grant? | The `users` row (`grant_remover`), not request roles | spec |
| NG-34 | How do two enum migrations land in either order? | Additive `ADD VALUE IF NOT EXISTS` | spec |
| NG-37 | May the generic permissions API create grants that skip the type's rules? | No: generic create is refused for every named grant | spec |
| NG-38 | Grant principal key? | CodeMie user id (`principal_id = user.id`), as in !4256 | spec |
| NG-39 | Recipient search? | The shared search with !4256's limits, filtered by the type's recipient rule | spec |
| NG-40 | Assistants on the shared named-grant module? | Yes. `ASSISTANT` is a `NAMED_GRANT_TYPES` entry with its own descriptor (personal-integration refusal as `before_grant`, assistant event names with `assistant_id`, text ids through `resource_key`) and is not in `_lookup`; `Ability` has one grant path; assistant REST routes and response shapes are unchanged (items carry the additive `grant_source`). The shared rules apply to assistants: a deactivated, deleted or external grantee stops counting and cannot be granted; generic `DELETE /v1/permissions/{id}` of an assistant grant is decided by `grant_remover` from the `users` row (admin or maintainer, or the assistant's creator; orphan grants admin only); generic create stays refused. Stored rows get `grant_source='name'` (`f8d24a6c0b17`) | user |
| S-18 | Generic routing and recipient policy per type? | Routing by (resource type, principal type) through `is_named_grant`; `grant_exists` takes the descriptor's `resource_key`; the recipient policy is the descriptor's `recipient`, read by `live_grants`, validation, search and link open | user |
| S-29 | How are named grants counted without each caller remembering a preload? | Lazy per-class loading inside `Ability`: the first check that reaches `Role.GRANTED` for a class loads all the user's live grants of that class in one sync query (`GRANT_LOADER`) and keeps them for the life of the object; passed `grants=` count as loaded; `GRANTED` is the last role | user |
| CR-002 (NG) | External type in SQL eligibility? | `eligible_internal_user` uses `config.EXTERNAL_USER_TYPE` | user |
| CR-005 (NG) | Where is the owner check of grant and revoke? | Inside the shared functions through `descriptor.is_owner` on the locked row; callers keep their own 403/404 split | user |

### 14.3 Share links

| ID | Question | Answer | Decided by |
|---|---|---|---|
| SL-1 | How does the token stay out of access logs? | In the open request's JSON body, not a path segment | spec |
| SL-2 | How does a DB error on open avoid printing the token while the owner can re-read the link? | Lookup by `token_sha256`; plaintext kept for the owner's re-read | spec |
| SL-3 | How does an old token stay refused with one live link per resource? | A revoked link stays as a tombstone (`revoked_at`) | spec |
| SL-5 | Where are link-grant statements? | In the shared `named_grant_service`; the link record and its service are generic | spec |
| SL-6 | How does by-name keep winning? | The link grant uses `ON CONFLICT DO NOTHING` | spec |
| SL-7 | Cost of repeated opens under the lock? | Check `readable` unlocked first; lock the app row only to write a grant | spec |
| SL-8 | Link sharing during a suspend? | Create and open refused; revoke allowed | spec |
| SL-9 | How does the UI learn about a live link without creating one? | Owner-only `GET /share-link` | spec |
| SL-10 | How does "who has access" show "via link"? | `list_shared_with` items carry `grant_source` (additive) | spec |
| SL-11 | What does an opener learn about a token? | One 404 `SHARE_LINK_NOT_AVAILABLE` for unknown, revoked, app gone and suspended | spec |
| SL-12 | How does open's lock keep the hosted lock mode and refresh what it re-reads? | `tx.lock_app`, shared with `managed_app` | spec |
| SL-13 | How does each caller keep its own state filter on the locked re-check? | `tx.lock_app(query)` takes the caller's whole select and adds only the lock mode and `populate_existing` | user |
| SL-14 | How do link rows leave with a purged app? | Retention deletes the app's link rows explicitly before the app row | spec |
| SL-16 | How does a failed statement keep the token out of the error log? | Hosted engine `hide_parameters=True` | spec |
| SL-17 | Audit with events off? | Not audited, as for grants | user |
| SL-18 | Link sharing for assistants? | No | user |
| SL-19 | Audit of the link itself? | `share_link.created` / `share_link.revoked`; link grants removed on revoke or delete are audited | spec |
| SL-20 | How is the link opened and its URL built? | A state-changing call returns the app id; the backend returns the token and the UI builds the share-page URL, as for conversations | user |
| SL-21 | Link grant lock? | Creating a link grant locks the app row before the grant statement | user |
| SL-22 | Hosted-only link table, or one common table? | One common table `share_links(id, resource_type, resource_id, token, token_sha256, created_by, created_at, revoked_at, revoked_by)` keyed like `permissions`, with the partial unique index of live links per resource, unique `token_sha256` and no FK to the resource; `permissions.source_link_id` references `share_links.id` `ON DELETE RESTRICT`; a generic `share_link_service` with a `SHARE_LINK_TYPES` registry; hosted REST API unchanged; no link sharing for assistants or conversations | user |
| SL-23 | How many steps is a link revoke? | One: `share_link_service.arevoke` tombstones the link and removes every grant it made, each audited; the revoke route (`arevoke_checked`) and `_begin_purge` call it | user |
| SL-24 | Who is the `created_by` of a link grant row? | The link's owner (`share_links.created_by`); the opener stays the principal and the audit actor | user |
| S-23 | Can a long revoke stall share-link opens? | Bounded: open sets a transaction `lock_timeout` and answers 503 `SERVICE_BUSY` when the app row stays locked longer | user |
| S-30 | One set of type facts for grants and links? | The link service reads the type's named-grant descriptor (domain, entity type, owner rule and message, `before_grant`, audit attributes, recipient rule); `ShareLinkDescriptor` holds only link events, `areadable`, `alock`, `project_of`, the link answers and `may_revoke`; every link type must be a named-grant type; link open admits openers by the type's `recipient` rule; hosted grant events carry `app_id` | user |
| S-32 | Link-open lock timeout? | 400 ms, well below the hosted pool checkout timeout (2.0 s); recorded in the code comment and tested | user |
| CR-001 (SL) | Plaintext token at rest? | Kept; accepted risk (owner re-read) | user |
| CR-003 (SL) | `grant_source` of orphan grants? | Orphan rows return the stored `grant_source` | user |
| CR-005 (SL) | Link grants on downgrade of `f8d24a6c0b17`? | Downgrade deletes `grant_source='link'` rows before dropping the table (guarded for schemas without `permissions`) | user |

### 14.4 Lifecycle, identity and catalog

| ID | Question | Answer | Decided by |
|---|---|---|---|
| CL-2 | Delete route? | `DELETE /hosted-applications/{app_id}` (platform delete-on-resource convention) | spec |
| CL-3 | Intermediate state on delete? | Straight `active` → `purging` | spec |
| CL-4 | How does delete start the purge? | Through `_begin_purge(tx, app)`, precondition `active`, sets `deleted_at`; asynchronous storage deletion (`purging` → `purged`) guarantees byte deletion | spec |
| CL-5 | Which states do lookups by id admit? | Only `active` by default (`managed_app`, `get_app`, admin target) | spec |
| CL-6 | `IdentityAuthority` configuration? | `IdentityAuthority(leeway_seconds)`; no `JWT_ISSUER` dependency | spec |
| CL-7 | `GET /{id}/deletion-status`? | Kept: it reports on the purge | spec |
| CL-8 | Viewing-session binding digest? | `[principal_id, sid or credential_digest]` | spec |
| CL-10 | Answer to a repeated DELETE? | DELETE loads the app in `active`, `purging` or `purged`; a matching `Idempotency-Key` returns the stored 202; otherwise a non-active app answers 404 | user |
| ID-1 | Dependency on Keycloak? | None; CodeMie user management is the only source of people and identity; `KEYCLOAK_ADMIN_*` and `HOSTED_APPS_KEYCLOAK_AUDIENCE` are not read | user |
| ID-2 | Feature without user management? | Off, with the startup reason "Hosted applications need user management (ENABLE_USER_MANAGEMENT=true)" | user |
| ID-3 | Supported providers? | Any `IDP_PROVIDER` (`local`, `keycloak`, `oidc`, `entraid-oidc`), JWKS on or off | user |
| ID-4 | Principal? | `user.id` of the platform-authenticated user, not a token claim | design |
| ID-5 | Which token is recorded, and when? | The token the platform provider authenticated, recorded after authentication by `record_platform_identity` | design |
| ID-6 | Identity failures? | Record nothing and fail closed; warn per cause (`missing`, `not_jwt`, `unbound`) at most once per 10 minutes per process; never log token content | review |
| ID-7 | Tokens without `sid`? | A viewing session lasts one token lifetime; documented | user |
| ID-8 | User disabled only in the IdP? | Accepted gap: stays active in `users` until a CodeMie admin deactivates them | user |
| ID-9 | Filter people by `auth_source`? | No | design |
| ID-10 | Rollout gate | Decode one real `oidc` / `entraid-oidc` staging token (JWT, `iat`, `exp`, `sid`, lifetime) before release | design |
| S-1 | Catalog filters on every hosted tab? | Search, Project (multiselect, repeatable `project`) and Created by (`created_by` = creator display name, options from `GET /hosted-applications/users?scope=`), as for skills | user |
| S-2 | Filters on My apps only? | Shared (`private`, `people`, `project`) and Status (`draft`, `published`, `unpublished`) | user |
| S-3 | Artifact type and version state filters? | Not in the UI; the API accepts `artifact_kind` and `version_state` | user |
| S-5 | Filters on tab switch? | Cleared | user |
| S-11 | Changes inherited from the original MRs outside the feature (dependency bumps, `.dockerignore`, vite `bundledDev`, unrelated test fixes)? | Reviewed and kept | user |
| S-13 | Previews on catalog cards? | None. The card shows an uploaded cover, a validated raster bundled in the upload, or the app's initials; every launch is a deliberate Open or manager Preview and is audited. Reason: a card preview costs a launch, a ticket redemption and live authorization of every file it loads on the 10-connection hosted pool, for every card on screen | user |
| S-25 | Grant and link audit needs activity events on? | Accepted: `ACTIVITY_EVENTS_ENABLED=true` is on the rollout checklist | user |
| S-31 | Storage sweep placement? | One replica per period under the platform's `async_leader_lock` (`HOSTED_STORAGE_SWEEP_LOCK_ID = 987654340`); the leader holds one core sync-pool connection for at most the sweep budget; the resume token is per process; validation, cleanup and retention loops use leases and `SKIP LOCKED` | user |

### 14.5 UI

| ID | Question | Answer | Decided by |
|---|---|---|---|
| D-U1 | Publish labels? | "Publish to Marketplace" / "Remove from Marketplace", as for skills | PO |
| D-U2 | A plain "Copy link" for the app address? | No; only "Copy share link", so nobody sends a link that grants nothing | PO |
| UI-1 | Redeem on load or on an "Open app" click? | On load | user |
| UI-2 | Leave the token URL in history? | No; history replace after redeeming | spec |
| UI-3 | Distinct messages per error code on the share page? | No; the not-available page (503 offers a retry) | user |
| UI-4 | Version picker and delete-previous in the Reinstate dialog? | No; reason only | user |
| UI-5 | Hosted copy of the Share popup or shared components? | Shared components in `src/components/sharing/`, driven by callbacks | user |
| UI-6 | Move the assistant popup? | No; it stays at its path as a thin wrapper | spec |
| UI-7 | Card shared label? | From `shared` and `is_global` only; no named-sharing marker | user |
| UI-8 | Separate "open working version" action? | No; Open depends on `has_active_version` for everyone; managers keep per-version Preview | spec |
| UI-10 | Path or fragment for the share link? | Path form `/apps/share/<token>`, like `/share/conversations/:token` | user |
| UI-11 | Referrer control on the share page? | None: browser default, as for conversation links | user |
| UI-12 | How do screens decide what an app allows? | One `hostedAccess` module and one `hostedActions` builder | user |
| UI-13 | Which version does `/apps/:id` launch? | The active version, for everyone; the cover never launches | user |
| UI-14 | Share popup while suspended? | Opens for the owner to remove people and revoke the link; grant and copy disabled, with a note | user |
| UI-15 | One "not suspended" gate or a predicate? | A per-action predicate, `suspensionRefuses`, over the server's `suspension_refuses`; upload, "Remove from Marketplace" and `shared` off stay enabled | user |
| UI-16 | What does the edit form send? | A PATCH with only the changed fields | user |
| UI-17 | 401 on the share page? | Through sign-in and back, then redeem | user |
| UI-18 | SSO opaque redirect on the share page? | Session-expired handling, not the not-available page | spec |
| UI-19 | Capability types? | Feature-level `HostedCapabilities` (`create`, `view`, `disabled_reason`, `limits`); per-app access comes from the app projection | user |
| UI-20 | Owner check in the generic popup? | No; the popup takes no entity, wrappers decide ownership | spec |
| UI-21 | What does the assistant wrapper's owner check gate? | The shared-with load, not whether the popup renders | spec |
| UI-23 | `can_activate` source? | Activate reads the per-version `can_activate` | spec |
| UI-24 | Token in the UI nginx / ingress access logs? | Accepted risk | user |
| UI-25 | Token in Referer of same-origin requests before the redirect? | Accepted risk, as for conversation links | user |
| UI-26 | Who may toggle "Shared with project"? | Any manager (`write`); Share follows `can_share` | spec |
| AR-2 (UI r2) | How is sharing wired per entity? | One sharing client per entity on its store, passed to `bindSharing`; one `useSharedWith` hook for both details pages | user |
| AR-3 (UI r2) | Where do a tab's filters live? | In the collections registry (`HOSTED_COLLECTIONS`); the URL reader, filter bar and loader read it | user |
| AR-11 (UI r2) | Assistant popup's search error? | "User search failed" in the popup, instead of the global error handling; intended | user |
| AR-7 (UI r2) | What does the admin link revoke report? | Only "No live share link remains", because the API answers `{link: null}` either way | user |
| AR-8 (UI r2) | How is the Platform apps tab found? | From the provider in the collections registry, not a literal | user |

### 14.6 Object storage (EPMCDME-16437) and signature updater (EPMCDME-16173)

| ID | Question | Answer | Decided by |
|---|---|---|---|
| D1 (16437) | Storage adapter? | A dedicated `HostedObjectStorage` with the `HostedFileStorage` protocol; reuses only the client setup of the file repositories | ticket |
| D2 (16437) | S3 writes? | Multipart with presigned Create and Complete expiring at `min(lease_until, write_deadline)` | ticket |
| D3 (16437) | Providers? | S3 and GCS; Azure keeps hosted apps unavailable | ticket |
| D4 (16437) | Filesystem? | Only where `ENV` is `local` or `test`; refused elsewhere at startup (review comment on !4432) | user |
| D5 (16437) | Small-asset byte cache? | None | ticket |
| D6 (16437) | S3 cleanup grace? | `HOSTED_APPS_STORAGE_S3_GRACE_SECONDS`, default 30 | ticket |
| D7 (16437) | GCS writes? | Fence object plus `compose`; no signed URLs, no `signBlob` | ticket |
| OS-1 | A separate hosted storage type setting? | No; `FILES_STORAGE_TYPE` selects the backend | user |
| OS-2 | Share the files bucket? | No; a dedicated `HOSTED_APPS_STORAGE_BUCKET` | spec |
| OS-3 | Credentials and region? | Reused from the files-storage settings | user |
| OS-5 | GCS fence and staging keys? | Scoped by prefix and writer token, not attempt id | spec |
| OS-6 | Buckets per deployment? | One; restoring an older database orphans newer objects, which the sweep removes after a day | spec |
| OS-7 | S3 presign expiry | `floor((until − database_now) − (monotonic() − received_at))`; under 1 s fails as a lost lease | spec |
| OS-8 | GCS fence recheck | The database recheck after `open()` is part of the fence (documented and tested) | user |
| FC-1 | How is a stopped freshclam made visible? | The clamd container's liveness probe also fails when freshclam is absent or a zombie | user |
| FC-2 | Check frequency? | `FRESHCLAM_CHECKS=4` (every 6 hours) | user |
| FC-3 | Startup probe? | Ping only, so the signature load never trips on freshclam timing | spec |
| FC-4 | Probe robustness | `/init` assumption comment, 60 s initial delay, atomic per-pid `/proc` read, non-zombie any-match loop | user |

---

## 15. Rejected alternatives

| Alternative | Rejected because | Decision |
|---|---|---|
| A skill-style single `visibility` (`private` / `project` / `public`) | Unpublishing must not change project sharing | D2, CORE-13 |
| A publish dialog with categories | Not needed; no category list settled | D3 |
| An organization-wide "Explore" listing or audience beside the Marketplace | A second marketplace; links cover "everyone with the link" | D3, D8 |
| A hosted-only grant table | A second per-person access list; RBAC would need a data migration | D9 |
| A link record modelled on `SharedConversation` (token as the access check, access counter) | Link access must go through the "shared with" check and be revocable; counters are a non-goal | D8 |
| A hosted-only link table with an FK to the app | `source_link_id` in the shared `permissions` table would name a hosted table | SL-22 |
| Moving conversation links into `share_links` now | It would change conversation links; conversations may adopt the table later | SL-22 |
| Keeping removed people out of the link | The link is meant for a chat; exclusions add state; the owner revokes the link instead | D19 |
| Grants or link revoke by project admins; grants by platform admins | Owner only, as for assistants; platform admins have generic delete of one grant and link revoke | D14, D17, S-17 |
| Suspend revoking the share link | Reinstate must restore sharing exactly; an admin revokes the link directly | D22, S-17 |
| Charging a manager's upload to the manager | The creator holds the quota | D12 |
| Suspend as a freeze checked at many consumers, or resetting sharing | Managers must keep working; reinstate must restore sharing exactly | CORE-6, D22 |
| Holding a suspended app's uploads unvalidated | Managers must be able to fix the app before reinstate | CORE-3 |
| Activation while suspended | Re-exposes content before review; reinstate chooses the version | CORE-7 |
| Refusing delete of a suspended app | Deletion takes content down too; blocking it does not stop a re-upload | S-21 |
| One SQL visibility condition in `service/` | The gateway needs a manage condition and a user-id expression; import cycle with the model | CORE-25, CORE-26 |
| Token claims for gateway roles | Claims carry no project data | CORE-17 |
| `access_revision` bump on every sharing change | Widening would end sessions; the per-request rule already ends access | CORE-30 |
| A second writer of `shared` or `is_global` | One writer per flag | CORE-31 |
| Request-user roles (IdP, `ENV=local`) for admin and project-admin checks | They diverge from the SQL rule | CR-001 (core), NG-33 |
| Grants through `check_resource_permissions` | A second grant evaluator that ignores the level | NG-1, NG-20 |
| `get_for` reading USER rows | A second, ungated grant path | NG-3 |
| Grant source in `before`/`after` | Changes !4256's audit payload | NG-8 |
| `DO NOTHING` for by-name grants | By name must replace a link grant | NG-22 |
| Auditing regardless of `ACTIVITY_EVENTS_ENABLED`, or requiring it on | Would change assistants too | NG-6 |
| Registering named-grant types at import time | Import-order precondition; fails open | NG-18 |
| A separate assistant sharing service beside the shared module | Two rules for one concept (eligibility, grant reads, remover rule, audit payload) | NG-40 |
| A per-object grant lookup when no grants are loaded | A second grant path, N+1 in lists, not gated by the live rule | NG-11 |
| A preload at every read site, or an `ability_for(user, type, items)` factory | A convention every new screen must remember; a forgotten preload silently denies a grantee | S-29 |
| A link descriptor repeating the grant descriptor's owner rule, pre-check, audit facts and recipient rule | Two descriptors that must agree; drift would make link sharers or openers differ from named grantees | S-30 |
| A moderation list scope or tab | A second home for moderation beside Settings > Administration; the actions already sit on the app | S-28 |
| Plaintext token lookup | Database error logs could print the token | SL-2 |
| A hash-only token (shown once) or an encrypted token column | The owner must re-read the same link | SL-2, CR-001 (SL) |
| Deleting revoked links | An old token could become live; the one-live-link index | SL-3 |
| No FK from `permissions.source_link_id` | With one common link table the FK costs nothing and keeps link grants pointing at real links | SL-22 |
| `ON DELETE CASCADE` or `SET NULL` from `share_links` to `permissions` | Link-grant removal must stay an explicit, audited step | SL-22, SL-23 |
| A revoke that only tombstones the link, leaving grant removal to each caller | A new caller could leave recipients with access | SL-23 |
| A separate lock statement in open; `lock_app(app_id, *where)` | Lock mode and refresh would drift; callers would lose their state filter | SL-12, SL-13 |
| Distinct error codes per token state | Lets an opener probe tokens | SL-11 |
| A link-open lock timeout equal to the pool checkout timeout | Waiting opens could take the whole hosted pool | S-32 |
| Storage sweep on every replica | Every replica would walk the whole bucket | S-31 |
| Token in a URL fragment | Fragment survival across Keycloak and proxy sign-in uncertain; the path form matches conversation links | UI-10 |
| `no-referrer` on the share page | The same-origin POST would carry `Origin: null`, which the CSRF check refuses | UI-11 |
| `strict-origin` through an nginx location | Sub-path deployments never match that location | UI-11 |
| An "Open app" button before redeeming | An extra click; redeem on load with history replace | UI-1 |
| Version picker and delete-previous in the Reinstate dialog | Kept API-only | UI-4 |
| A hosted-only copy of the Share popup | Two popups for one concept | UI-5 |
| A single "not suspended" gate in the UI | Blocks allowed narrowing (upload, unpublish, `shared` off) | UI-15 |
| Sending the whole form on PATCH | `shared: true` on a suspended app shared with its project would refuse a rename | UI-16 |
| A named-sharing marker on cards | The grantee list is owner-only | UI-7 |
| Automatic live or static card previews | Load on the hosted pool for every card on screen | S-13 |
| A separate `HOSTED_APPS_STORAGE_TYPE`; sharing the files bucket | One setting selects the backend; the sweep would delete generic files | OS-1, OS-2 |
| Freshclam in its own container | A larger change; the minimal probe was proved in the lab | FC-1 |
| Organization and issuer keys | One constant per installation; an issuer change orphans data | D15 |

---

## 16. Limitations and follow-ups

### 16.1 Known limitations

- Grant and link changes are not audited while `ACTIVITY_EVENTS_ENABLED` is off, which is the default.
- Share-link tokens are stored in plaintext beside their hash.
- The share-link token reaches UI nginx / ingress access logs and same-origin Referer before the redirect; redeem on
  load means a prefetch or accidental visit by a signed-in internal user grants view access.
- The recipient search exposes internal users' names and emails to any owner; there is no rate limit on the search or
  on `share-links/open`.
- Assistant named grants follow the shared rules: a deactivated, deleted or external grantee loses the assistant at
  once (the rows stay and count again if the account is reactivated), and the first assistant READ check that no other
  role answers costs one query per `Ability` object.
- `Ability`'s grant load is a synchronous query on the core pool (once per class per `Ability` object), also when the
  check runs in an async route; hosted code avoids it by passing the grants it read on its own pool.
- Retention depends on purge having removed link grants: a leftover link grant makes the retention delete of that
  link fail (`RESTRICT`) rather than drop the grant unaudited.
- A user disabled only in the identity provider stays active in `users` until a CodeMie admin deactivates them;
  viewing sessions without `sid` last one token lifetime.
- Delete is irreversible; there is no trash. A manager, the creator included, may delete a suspended app.
- Delete removes every grant and revokes the live link in the delete transaction; a future trash and restore would
  need that step moved into the purge job, or restore would have nothing to restore.
- One live link per app: `GET /share-link` answers one `{link}` and a partial unique index allows one live row.
  Several links or link expiry need the index, the response contract and a sweep that revokes through the single
  revoke step.
- Marketplace publication has no review: `/marketplace/publish` sets `is_global` directly. Review would need a pending
  state and an `EntityAdapter` for hosted apps in marketplace management.
- The creator id is owner, sharer, quota holder and the person excluded from reinstating at once. Transferring an app
  whose owner has left would mean changing `created_by`, moving stored and held bytes between quota rows, updating
  `quota_principal_id` on in-flight attempts and taking both quota locks in the hosted lock order; no such operation
  exists.
- There is no list of suspended apps: a platform admin finds a suspended app of a project they are not a member of
  only by its URL or id.
- `ENV=local` makes the request `User` an admin while the `users` row may not be; hosted decisions read the row, so
  local tests must set `users.is_admin`.
- `hide_parameters=True` hides bound parameters in every hosted SQL error log, which makes hosted SQL failures harder
  to debug.
- The storage sweep's resume token is per process: a new leader restarts from the beginning of the bucket.

### 16.2 Follow-ups (no ticket keys yet)

| Follow-up | Owner |
|---|---|
| Platform-wide folders and trash for assistants, skills, workflows and hosted apps (D5) | platform |
| Edit access for named people (S-33). Before enabling it, the requester and the RBAC team decide which manager actions edit covers (metadata, upload, configure, select and activate versions, publish to the Marketplace, turn on project sharing, move; uploads are charged to the creator's quota). Every manager route asks one `manageable(WRITE)`, so a WRITE grant term would pass all of them. If edit covers fewer, first add a grant-free manager term `manageable(apps, user, MANAGE)` (owner, project admin, platform admin; never a grant), ask it on publish, project sharing, move and activation, and split the UI's `isManager` callers the same way; then register `GRANTED` under WRITE, add WRITE to `access.MANAGER_GRANT_ACTIONS`, and accept a level in the share request | requester / RBAC team |
| Assistants get the "Shared with me" tab and `scope=shared`, so the platform has one way to find items shared by name | platform / assistant owners |
| Conversations may adopt `share_links`. A conversation link today serves a snapshot to anyone holding the token and counts accesses, while the generic open writes a named recipient grant, so the conversations owner chooses: (a) openers become named recipients (needs a conversation grant role, a `NAMED_GRANT_TYPES` descriptor and session support), or (b) a token-only link mode that serves the resource without writing a grant. Either way `share_links.resource_id` is a uuid, so conversation ids must be UUID strings (or the column must change) first. The generic `ShareLinkSection` UI may be adopted together | conversations owner |
| Limits on the recipient search (rate, scope), decided once for hosted apps and assistants | requester / security |
| Gateway database-pool exhaustion load test | original author |
| Storage provider guard (refuse `filesystem` outside local and test) and Azure support | original author / DevOps |
| Validator: read the parser's stderr concurrently and escape the JSON log message (commit `8dd1c48aa`, not on this branch) | original author |
| Gateway authorize p95 and p99 latency and pool wait measurement (CORE-34) | original author |

---

## 17. Verification status

What is recorded for the final code, and what is not.

### 17.1 Backend (codemie)

- Last recorded run, on the final backend code (`83364e7d1`): `make ruff` (pass) and
  `env -u HOSTED_TEST_DATABASE_URL poetry run pytest tests/codemie/service/permission tests/codemie/service/assistant
  tests/codemie/rest_api tests/codemie/core tests/codemie/service/hosted_applications tests/unit -q -n auto`:
  8287 passed, 238 skipped, 5 xfailed, 13 failed. All 13 failures are outside the diff: 9 in `test_validator_server`
  (subprocess mocks lack `stderr`), 3 marketplace-management smoke tests (routes not mounted locally), and the
  OpenAPI snapshot test (only the LLM-proxy routes, which do not load locally; the hosted entries match the baseline).
  Create with `shared` is covered by `test_requests.py` (default false, strict bool), the router test (`shared`
  true, false and omitted reach the catalog, no 422) and `test_create_shared.py` (the stored flag); the DB-backed
  create tests in `test_catalog.py` were skipped with the rest of the PostgreSQL suite.
- Covered by unit tests: the access module and its import set, the persona × access-path matrix, the named-grant and
  share-link services (including the registries' consistency, the link-grant row creator and the lock-timeout bound),
  the generic permissions routing, the assistant descriptor and lazy grant loading, the hosted catalog, admin,
  publication, retention, runtime and gateway.
- Not run: PostgreSQL-backed tests (`HOSTED_TEST_DATABASE_URL`: schema parity, purge, migration upgrade and
  downgrade), so the migrations, the `permissions` CHECK and FK, the partial unique index, the lock timeout and the
  sweep leader lock have not been exercised against a real PostgreSQL by these runs; the permissions migration is not
  covered by any default-run test (no default migration gate exists).

### 17.2 UI (codemie-ui)

- Last full gate, at `cea0a5d10` (28 commits before the final head `6055206f3`): lint, typecheck and secrets pass;
  unit 8891 passed / 1 skipped; integration 815 passed / 1 skipped. Later runs were scoped to the hosted area and the
  stores (unit and integration, all passing); the full gate was not repeated on `6055206f3`.
- UI test harness at hand-off: 88 passed / 3 failed; the 3 failures reproduce on main.
- Assistant Share popup tests pass with the shared components.

### 17.3 Manual

- A manual end-to-end walkthrough on a stand (create in a project; share with the project, with one named colleague
  and by link; publish and unpublish; revoke; delete; check each persona's access after each step) was done during
  development; it was not repeated on the final heads, and its record is not kept in the repository. It did not
  cover creating an app with "Shared with project" switched on.

### 17.4 Unverified

- PostgreSQL-backed hosted tests, schema parity and migration upgrade and downgrade against a real database.
- Gateway p95 and p99 authorize latency and pool wait at 64 concurrent requests, with query plans (CORE-34).
- How long a link revoke and an app delete hold the app and quota locks at 1,000 and 10,000 link recipients (one
  `DELETE ... RETURNING` plus one audit event per row). Link open is bounded by the 400 ms lock timeout.
- The synchronous grant load under real async load.
- Same-origin `POST share-links/open` carrying the real `Origin` in Chrome, Firefox and Safari beyond the stand
  walkthrough.
- A real `oidc` / `entraid-oidc` token decode (ID-10).
