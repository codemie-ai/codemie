# Technical Analysis — EPMCDME-15110 (Default Project Foundation)

Produced from manual codebase research in the planning conversation (equivalent depth to `tech-analysis-orchestrator`; not re-dispatched as a subagent since the research was already done with full ticket context in-session).

## Codebase Findings

### Membership table and precedent for a per-membership flag
- `UserProject` model (`src/codemie/rest_api/models/user_management.py:62-73`), table `user_projects`, composite-unique on `(user_id, project_name)`. Carries `is_project_admin: bool = SQLField(default=False)` today — the exact precedent to clone for a new `is_default: bool` column, same table, same style.
- Personal projects also create a `user_projects` row (`personal_project_service.py:247`, `is_project_admin=False`) — they are NOT excluded from this table by type, only by an explicit guard at the service layer (see below).

### Existing guard to reuse verbatim
- `ProjectAssignmentService._reject_if_personal_project` (`project_assignment_service.py:309-325`): `@staticmethod`, signature `(project: Application, actor: User, action: str) -> None`. No session/transaction coupling — pure check + raises 403 (admin/maintainer/owner) or 404 (everyone else, existence hidden). Must be called explicitly from any new default-set/clear logic; it does not apply automatically just because new code lives in the same service file.

### Removal paths already exist and are sufficient
- Single removal: `ProjectAssignmentService.remove_user_from_project` (`:748-797`) — deletes the `UserProject` row.
- Bulk removal: loop at `:700-728` — deletes each `UserProject` row for the batch.
- Both call `_sync_project_budget_member_removed` (`:268`) afterward — that function is budget-allocation cleanup only, unrelated to the default flag. Because `is_default` would live on the same row these paths already delete, both removal paths clear the flag automatically with zero new code.

### Existing partial-unique-index pattern (house style)
- `Budget` model: `Index("uix_budgets_name_active", "name", unique=True, postgresql_where=text("deleted_at IS NULL"))` (`budget_models.py:142`). Confirms partial unique indexes are an established pattern in this codebase for "at most one active X" invariants — directly reusable for "at most one default per user."

### Read/write models needing the new field (confirmed exact names, not assumed)
- `ProjectInfo` (`user_management.py:244-249`) — `{name, display_name, is_project_admin}`, used by `CodeMieUserDetail.projects`.
- `AdminUserProject` (`user_management.py:307-312`) — separate `{project_name, is_project_admin, date}` model, used by the admin list view. **Two distinct models carry `is_project_admin` today — both need `is_default` added, easy to update one and miss the other.**
- `ProjectAccessRequest` (`:322-326`, add-member request) and `ProjectAccessUpdateRequest` (`:329-332`, role-toggle request) — deliberately **not** extended; see spec.md "Not touched" section for reasoning.

### Role gating precedent
- Backend project-assignment endpoints already gate on admin-or-maintainer (confirmed via ticket context and prior grep of this router). New set/clear endpoints must reuse that exact same dependency — and explicitly must NOT accept `is_project_admin` as sufficient, since that boolean lives on the identical row and is an easy mix-up. Ticket's own out-of-scope list ("project admins setting default for members of their own project" is disallowed) makes this an explicit requirement, not just a style preference.

### Cache surface (relevant to sibling tickets, not this one)
- `_resolution_cache` (`budget_resolution_service.py`, 60s TTL) and `_budget_assignment_cache` (`budget_service.py`) are the caches Budget Attribution will need to invalidate when a default changes. Foundation's own read/write paths do not touch these — no budget resolution happens in this sub-task.

## Risk Indicators

- **Two read models to update, not one** (`ProjectInfo`, `AdminUserProject`) — flagged above, call out explicitly in the plan so it isn't missed.
- **Permission-flag collision risk**: `is_project_admin` and the new `is_default` are adjacent booleans on the same row; the permission check for set/clear must gate on the user's platform-level role (admin/maintainer), never on `is_project_admin` of the target row.
- **Non-atomic UI composition** (not a Foundation risk, but worth naming for the record): the sibling User Management UI ticket will call "add member" then "set default" as two separate requests; if the second fails, the row is left as an ordinary membership. Foundation does not need to solve this — noted so it isn't rediscovered as a surprise.

## Scope Confirmation

Full line-by-line reconciliation against the parent story (EPMCDME-15109) and sibling User Management UI ticket's own AC list confirms Foundation's 4 ACs are a clean, non-overlapping subset — 8 parent "Assigning the default project" items map to 4 UI-child items + 3 Foundation-only items + 1 split item (API-reject half → Foundation, UI-marker half → UI child). No AC dropped, none duplicated. See `spec.md` for the full mapping table.
