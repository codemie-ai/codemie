# EPMCDME-15110: Default Project Foundation — Design Spec

## Context

Parent story: [EPMCDME-15109](https://jiraeu.epam.com/browse/EPMCDME-15109) — "Default Project per User: Assignment in User Management and Budget Attribution."

15109 is split into three sub-tasks:
- **EPMCDME-15110 — Default Project Foundation** (this spec). Ships first, no dependencies.
- **Budget Attribution** (sibling, depends on this). Wires the default project into marketplace-assistant billing, skills/workflow attribution, and CLI/proxy fallback.
- **User Management UI** (sibling, depends on this). Ships the admin-facing screens: per-row default toggle, add-project-dialog checkbox, users-list marker.

This sub-task delivers the contract only: a per-membership default flag, its set/clear API, and role gating. It does not change any billing behavior and does not build any UI.

## Problem

Philips (and any org using CodeMie as a multi-department platform) needs a deterministic answer to "which of this user's projects is their own, for billing purposes." Today nothing stores that. Before the sibling tickets can consume such an answer, it has to exist: a flag, a place to set/clear it, and rules for who may do so.

## Scope

### In scope — the 4 acceptance criteria this ticket owns

1. Given a user already has a default project, when a different project is marked as their default, then the new project becomes the default, the previous one remains an ordinary membership, and the user has exactly one default project at any time.
2. Given a request to set a project as a user's default, when the user is not a member of that project, then the request is rejected and no default is stored — this holds whether the request comes from the UI or directly from the API.
3. Given a user's default project is removed from their projects, when the membership is deleted (individually or in bulk), then the user is left with no default project rather than a default pointing at a project they no longer belong to.
4. Given a user without permission to manage users (for example an auditor or a plain user), when they call the API to set or clear another user's default project, then the API rejects the change.

### Explicitly out of scope (owned by sibling tickets or excluded by 15109)

- Add-project-dialog "mark as default in the same action" UI, users-list Projects-cell marker, per-row set/clear buttons, disabled state for non-admins — **User Management UI** child.
- All billing/attribution behavior: marketplace-assistant membership precedence, CLI/proxy fallback, per-category funding gaps, visible-fallback flagging in logs/analytics, ~1-minute cache-delay behavior — **Budget Attribution** child.
- Letting project admins (`is_project_admin`) set default for members of their own project — platform-level admin/maintainer only, by design.
- Letting users self-set their own default.
- A default project the user isn't a member of.
- Bulk-setting default for many users at once.
- Backfilling a default project for existing multi-project users.
- Building a new "deterministic ordered project list" function. The phrase in 15110's own Story line ("with the user's resolved project list deterministic") is read narrowly here: satisfied by this flag+API being the one authoritative answer to "which project is default," not by reordering or rewriting any existing project-listing query. Fixing the "arbitrary first-entry" call sites (skills, workflow execution) is Budget Attribution's job.

## Design

### Data model

Add `is_default: bool` to the existing `user_projects` table (`UserProject` model, `rest_api/models/user_management.py`), same table and same pattern as the existing `is_project_admin` column — a per-membership flag, not a pointer on the `users` table.

This placement is deliberate: because the flag lives on the membership row itself, deleting that row (single or bulk removal — both already-existing code paths) removes the flag automatically. No new cleanup logic is needed to satisfy AC3, and no new mechanism can leave a default pointing at a membership that no longer exists.

Migration (alembic, additive only):
- `ALTER TABLE user_projects ADD COLUMN is_default boolean NOT NULL DEFAULT false`
- Partial unique index: `CREATE UNIQUE INDEX uix_user_projects_one_default ON user_projects (user_id) WHERE is_default = true` — DB-level backstop for AC1, alongside the app-level "unset old, set new" transaction. Mirrors the existing partial-unique-index pattern already used in this codebase (e.g. `uix_budgets_name_active ... WHERE deleted_at IS NULL`).
- No backfill: all existing rows default to `false`, matching the explicit out-of-scope item.

### API surface

Two new endpoints, service method `ProjectAssignmentService`:

- `PUT /users/{user_id}/projects/{project_name}/default` — set default.
  - Looks up the `(user_id, project_name)` membership row. Missing row → `404` (this is what makes AC2 automatic: there is no code path that can default a non-membership, because the operation targets an existing row).
  - Inside one transaction: clear any other row for this user with `is_default = true`, then set `is_default = true` on the target row.
  - Idempotent: setting the already-current default again returns success with no state change.
- `DELETE /users/{user_id}/projects/{project_name}/default` — clear default.
  - Missing row → `404`.
  - Row exists but not currently default → no-op success.
  - Row exists and is default → set `is_default = false`.

Both endpoints require the same admin-or-maintainer permission dependency already enforced on this router's existing project-assignment endpoints (add member, remove member, role update). This must be the **platform-level** admin/maintainer check, not `is_project_admin` — the two booleans live on the same row and are easy to confuse; the out-of-scope list explicitly excludes project-level admins from this capability, even for their own project.

**Not touched**, by explicit decision:
- `ProjectAccessRequest` (add-member request) — no `is_default` field added. The User Management UI child composes "add member" + "set default" as two sequential calls to satisfy its add-dialog AC; this ticket does not extend the add-member contract. (Known consequence, not a defect to fix here: the two calls aren't atomic — if the second fails, the user ends up an ordinary member. Left to the UI child.)
- `ProjectAccessUpdateRequest` (role-toggle request) — no `is_default` field added. Role change and default assignment are kept as separate operations with separate endpoints rather than conflated into one request body.

### Read side

No new read endpoint. Add `is_default: bool` to the two existing response models that already carry `is_project_admin` per project row:
- `ProjectInfo` (`user_management.py`, used by `CodeMieUserDetail.projects`)
- `AdminUserProject` (`user_management.py`, admin list view)

No new read-side permission restriction — whoever can already see a user's project list sees the new flag too. (AC4's "can see markers but cannot change them" for auditors/plain users is satisfied by: read access is unchanged/unrestricted, and the two write endpoints above reject non-admin/maintainer callers.)

### Membership-removal interaction

No new code. `_sync_project_budget_member_removed` and the budget-allocation cleanup it performs are unrelated to this flag and are not touched. Both existing removal paths — `ProjectAssignmentService.remove_user_from_project` (single) and the bulk-removal loop in the same service — delete the `user_projects` row itself, which carries `is_default`. AC3 falls out of the existing deletion behavior for free.

## Acceptance criteria → design mapping

| AC | Satisfied by |
|---|---|
| 1. Exactly one default at a time | Transaction (unset old, set new) in the `PUT .../default` handler + partial unique index backstop |
| 2. Reject if not a member | `PUT .../default` operates on an existing membership row; missing row → 404, for both UI and direct-API callers (same service method) |
| 3. Removal clears default | Automatic — flag lives on the row deleted by existing single/bulk removal code |
| 4. Permission gating | Admin/maintainer dependency on both new endpoints; read side left at existing (broader) visibility |

## Testing approach

- Service-layer unit tests: set default on a valid membership; set default when not a member (404, no row written); replace an existing default (old row flips to false in the same call); idempotent re-set of current default; clear when not currently default (no-op); clear on missing membership (404).
- DB constraint test: attempt to violate the partial unique index directly (two `is_default=true` rows for one user) fails at the DB layer, independent of the app-layer transaction.
- Permission tests: admin ✓, maintainer ✓, auditor ✗ (403), plain user ✗ (403), project admin (`is_project_admin=true`, not platform admin/maintainer) ✗ (403) — this last case is the one most likely to be implemented wrong, given both flags share a row.
- Regression test on existing removal paths: after adding `is_default`, single removal and bulk removal both still work and leave no orphaned default (asserted via a fresh test, not just relying on the mechanism being "automatic").

## Risks / things a reviewer should double-check

- Two read models (`ProjectInfo`, `AdminUserProject`) both need the new field — easy to update one and miss the other.
- The permission check must reuse the platform-level admin/maintainer dependency, not accidentally gate on `is_project_admin` (same table, adjacent column, easy mix-up).
- The non-atomic add-member + set-default composition is intentionally left to the UI child; flagging here so it isn't rediscovered as a surprise later.

## Post-analysis corrections (2026-09-25)

Superseding decisions — details in `post-analysis-fixes.md`:
- The personal project may be set as default (no personal-project guard on default operations).
- The resolved project list is ordered by `project_name`.
- `is_default` is required on `ProjectInfo`, `ProjectInfoResponse`, `AdminUserProject`; the profile endpoint now returns it.
