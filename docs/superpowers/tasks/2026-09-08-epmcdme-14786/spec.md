# EPMCDME-14786 — Share an Assistant with Selected Users

**Status:** Draft  
**Complexity band:** L (4–5 days)  
**Date:** 2026-09-08

---

## Problem Statement

An assistant owner can currently share an assistant with their entire project (via `shared: true`) or keep it private. There is no mechanism to share with specific individuals without exposing it to the full project. This feature adds per-user access grants so owners can invite specific internal colleagues to use an assistant.

---

## Goals

- Allow an assistant owner to grant and revoke USE access to specific internal CodeMie users.
- Shared assistants appear in recipients' accessible assistant lists and personal project view.
- Recipients use (converse) but cannot edit the assistant.
- Per-user grants coexist with the existing project-wide `shared` flag without conflict.
- When a recipient uses a shared assistant, the assistant's datasource/tool permissions are evaluated as the assistant owner's context, not the recipient's. A recipient who has no direct access to the assistant's attached datasources must still be able to converse using it — the assistant runs with its configured resource access regardless of the recipient's own permissions.

## Non-Goals

- External or cross-organisation sharing.
- Edit or collaboration access for recipients.
- Sub-assistant exposure via external links.
- Bulk / distribution-list sharing.
- Modifying the existing project-wide `shared` flag.
- Sharing chats or folders (tracked in EPMCDME-11988).
- Feature flagging or gradual rollout mechanism.
- Granting recipients any new direct datasource permissions — the assistant agent already runs under the owner's configured tools/datasources at runtime; this feature only controls who may invoke the assistant, not who may directly access its underlying resources.

---

## Design

### Storage: Extend the existing `Permission` table

The `Permission` table (`src/codemie/rest_api/models/permission.py`) was explicitly designed to cover assistants (comment at L59 names assistants as an example resource). `ResourceType.ASSISTANT` is the extension point activated here; no new table is added. The `PrincipalType.USER` stub in `PermissionCreationService` is intentionally left unimplemented — the generic `/v1/permissions` endpoint remains blocked for USER principal type; the new assistant sharing endpoints write `Permission` rows directly, enforcing all guards in the assistant service layer.

Changes:
- Add `ResourceType.ASSISTANT = "assistant"` to the enum. Update `type_for_instance()` to handle `Assistant` instances.
- One Alembic migration for the enum value addition (`src/external/alembic/versions/`).

The `permission_level` for all assistant per-user grants is `READ`. No new `permission_level` value is required.

### Owner-only enforcement

The existing `Ability(user).can(Action.WRITE, assistant)` scope is too broad — it includes project managers and admins, but AC#7 restricts share/revoke to the owner only. All share and revoke service calls must check `assistant.is_owned_by(current_user)` explicitly before any other logic. This guard is separate from and stricter than the `Ability` WRITE check.

### Object-level READ gate: `is_shared_with`

`AssistantBase.is_shared_with(user)` (`src/codemie/rest_api/models/assistant.py`) gains a third predicate:

> Return `True` if `user.id` appears in a set of granted user IDs for this assistant.

For single-object access (called via `Ability.can(Action.READ, assistant)`), the method queries the `Permission` table directly when no pre-loaded set is available. For list queries, the repository populates the set eagerly to avoid N+1 queries.

### List-level visibility: SQL filter extension

`AssistantRepository._filter_for_regular_user_visibility` (`src/codemie/service/assistant/assistant_repository.py`, L662) gains an OR condition:

```
assistant.id IN (
  SELECT resource_id FROM permissions
  WHERE resource_type = 'assistant'
    AND principal_type = 'user'
    AND principal_id = :current_user_id
)
```

This single OR clause satisfies both AC#2 (accessible assistants list) and AC#9 (personal project view) because the visibility filter already spans all non-global assistant scopes. No separate query path is added for `_build_project_with_marketplace_query`.

### Service layer

**New `AssistantShareService`**:
- `grant(owner, assistant, user_ids: list[str]) → list[CodeMieUserDetail]` — checks owner, writes `Permission` rows directly for each user (verifying `not is_external_user` — i.e. any registered platform member with `user_type != 'external'`; idempotent: skip if grant already exists), returns updated shared-with list.
- `revoke(owner, assistant, user_ids: list[str]) → list[CodeMieUserDetail]` — checks owner, deletes `Permission` rows for all listed users in one call (ignores users who had no grant; no error), returns updated shared-with list.

**New `AssistantSharedUsersQueryService`**:
- `list_shared_with(assistant_id) → list[CodeMieUserDetail]` — queries `Permission` rows for the assistant, joins user details, returns list sorted by name.
- `search_sharable_users(query: str) → list[CodeMieUserDetail]` — searches active internal users by name or email substring. Used by the "add people" chip-input.

### API surface

All new endpoints live on the assistant router (`src/codemie/rest_api/routers/assistant.py`). All require the caller to be the assistant owner; non-owners receive `403`.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/v1/assistants/{id}/shared-with` | List users currently granted access (for grant-access view, AC#8) |
| `POST` | `/v1/assistants/{id}/share` | Grant access; body: `{user_ids: list[str]}` — idempotent per user |
| `DELETE` | `/v1/assistants/{id}/share` | Revoke access; body: `{user_ids: list[str]}` — ignores users with no grant |
| `GET` | `/v1/assistants/{id}/sharable-users?q=<query>` | Search internal users for chip-input suggestions (AC#6) |

Response for share/revoke: `{shared_with: list[CodeMieUserDetail]}` — the full current list after the operation, enabling the UI to refresh in one round trip.

`CodeMieUserDetail` already carries `id`, `username`, `email`, `name`, `picture` — sufficient for the avatar + name + email chip display.

### Edit controls (AC#3)

Per-user grants store `permission_level = READ`. The frontend's existing check of `Ability(user).can(Action.WRITE, assistant)` returns `False` for a recipient who only holds a per-user READ grant, so existing edit UI gating requires no change.

### Coexistence with `shared: true` (AC#10)

The `AssistantBase.shared` flag and the `Permission` table are independent. `is_shared_with` evaluates them with OR semantics: project-shared OR per-user-granted → readable. A user who satisfies both conditions is not shown duplicates; the access is simply granted by either path.

---

## Acceptance Criteria

1. Owner selects ≥1 internal user in the share view → `POST /v1/assistants/{id}/share` with `{user_ids: [...]}` → those users can read and converse with the assistant.
2. Shared assistant appears in the recipient's accessible assistants list (`GET /v1/assistants` with `VISIBLE_TO_USER` scope).
3. Recipient can start or continue conversations; `Ability.can(WRITE)` returns `False` so editing controls are hidden.
4. Owner calls `DELETE /v1/assistants/{id}/share` with `{user_ids: [...]}` → access revoked for all listed users; subsequent reads by those users return `403`.
5. After revocation the assistant no longer appears in the revoked users' assistant list.
6. `GET /v1/assistants/{id}/sharable-users?q=<query>` returns only users where `is_external_user = False` (i.e. `user_type != 'external'`) and `is_active = True`.
7. `POST /v1/assistants/{id}/share` or `DELETE /v1/assistants/{id}/share` called by a non-owner returns `403`; no `Permission` row is created or deleted.
8. `GET /v1/assistants/{id}/shared-with` returns the current shared-with list, visible in the assistant details view.
9. Shared assistant appears in the recipient's personal project assistants view (same query path as AC#2 via the extended `_filter_for_regular_user_visibility`).
10. An assistant with `shared: true` and per-user grants behaves consistently: both sharing mechanisms independently grant READ access; no duplicate or contradictory access states arise.
11. Given an assistant has datasources attached that the recipient user does not have direct access to, when the recipient uses the shared assistant, then conversations execute successfully and the assistant can query its datasources without error.

---

## Migration

One Alembic migration under `src/external/alembic/versions/`:
- Extend the `resourcetype` enum with the `assistant` value.
- No new tables. No column additions to existing tables.
- Must be applied before code deployment.

---

## Testing

New test modules (unit-level, mock-based, consistent with existing patterns):

- `tests/codemie/rest_api/models/test_permission_models.py` — extend with `ResourceType.ASSISTANT` enum test.
- `tests/codemie/service/assistant/test_assistant_repository.py` — extend with per-user grant OR clause: user with a grant row sees assistant; user without does not; admin path unchanged.
- `tests/codemie/core/test_ability.py` — extend with `is_shared_with` per-user grant path: granted user → `True`; non-granted → `False`; project-shared path unchanged.
- New `tests/codemie/service/assistant/test_assistant_share_service.py` — owner grants list of users, owner revokes list of users, non-owner rejected, external user rejected, idempotent re-grant, revoke of non-existent grant is a no-op.
- New `tests/codemie/rest_api/routers/test_assistant_share.py` — endpoint coverage for all four new routes, owner vs. non-owner, bulk grant/revoke response shape.

---

## Open Risks

- **N+1 on single-object `is_shared_with`**: for list queries the subquery handles visibility in bulk, but single-object `Ability.can(READ)` calls will each issue one `permissions` query. Acceptable at current scale; a batch loader can be added if access-check hot paths become a concern.
- **Enum migration lock**: extending a PostgreSQL enum requires an `ALTER TYPE … ADD VALUE` which cannot run inside a transaction on older Postgres versions. The migration must use `execute_if` or run outside a transaction block.
- **`type_for_instance()` blast radius**: `ResourceType.type_for_instance()` currently raises `ValueError` for non-DATASOURCE resources. Any code path calling it on an `Assistant` instance today will start routing correctly after the change — confirm no callers rely on the raised exception for flow control.
