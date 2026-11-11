# Technical Research

**Task**: user-projects project-deletion data-migration alembic
**Generated**: 2026-10-16
**Research path**: filesystem

---

## 1. Original Context

Summary: Deleted projects remain visible in assistant settings due to legacy user_projects records

Previously deleted projects remain visible in CodeMie assistant settings for the user who created them. The reported records are legacy entries in the user_projects table from projects deleted before 2026-09-18.

A user reported seeing a deleted project in assistant settings on Production. The project can appear on assistant-related pages, including workflow and skills settings, when the user was its creator.

The reported fix was delivered in release 2.51 on 2026-09-18 under EPMCDME-14501. According to the report, no new records have been added to the database since deployment, but 657 records for projects created and deleted before that date remain in user_projects. These historical records continue to make the deleted projects visible.

The issue is to determine and carry out the appropriate handling of those legacy records, while preserving valid project associations.

Expected Result: Deleted projects are not shown or selectable in assistant settings. Legacy records are handled without affecting valid project associations.

Acceptance Criteria:
1. Deleted projects represented by the reported legacy records are no longer shown or selectable in assistant settings.
2. The 657 reported Production records are assessed and handled without removing valid project associations.
3. The cleanup or remediation is safe to run and does not affect active projects or their users.
4. No new stale user_projects records are introduced by project deletion after the 2.51 fix.
5. The result is verified in assistant settings, including workflow and skills pages, for affected users.

---

## 2. Codebase Findings

### Existing Implementations
- Deletion path: `DELETE /v1/projects/{projectName}` in `src/codemie/rest_api/routers/projects/management.py:136` -> `ProjectService.delete_project` (`src/codemie/service/project/project_service.py:570`). Personal projects 403; any non-creator `user_projects` row -> 409; resources -> 409; then `application_repository.delete_by_name` (physical `session.delete`, `application_repository.py:276`) at L627, and since EPMCDME-14501 (commit 555aa6aef, 2026-09-15) L628-631: `remove_project(creator_id)`, `_sync_project_budget_member_removed`, `invalidate_user_from_cache`. Only guarded by `if creator_id is not None` (legacy apps with NULL `created_by` would still leave rows, but then all rows count as "assigned" and block deletion).
- History: `delete_project` introduced 2026-03-26 (98ef4e22f, EPMCDME-10807); the non-creator 409 guard added 2026-04-01 (87da7fbd4, EPMCDME-11518). Deletions in that 6-day window could have orphaned non-creator rows too. It is the only caller of `delete_by_name`.
- `UserProject` model (`src/codemie/rest_api/models/user_management.py:62`): `project_name` plain indexed str, `UniqueConstraint(user_id, project_name, 'uix_user_project')`, `is_default` with partial unique index `uix_user_projects_one_default`. DDL `299a10eb05a0_add_user_management_tables.py:62-75`: `user_id` FK users.id ON DELETE CASCADE; no FK on `project_name`.
- `Application` (`src/codemie/core/models.py:381`): `id` PK, `name`, `project_type`, `created_by`, `deleted_at` (soft delete). `application_repository.create` sets `id=name` (L188+); `update_project` sets `application.id = name` on rename (L738).
- `UserProjectRepository._filter_projects_by_visibility` (`user_project_repository.py` ~L440-485) already skips rows with no applications row ("Project doesn't exist in applications table - skip") but matches by exact name via `get_project_types_bulk` (`application_repository.py:514`, exact `IN`, no `deleted_at` filter).

### Architecture and Layers Affected
- **Router**: `src/codemie/rest_api/routers/user.py` `_get_user_response` (L57-~100) -> `GET /v1/user`; `user_profile_router.py:76-100` (same pattern, profile update response); `user_management_router.py:289` -> `user_access_service.get_user_projects_list`.
- **Auth/security context** (`User.project_names`, used for RBAC and the legacy IDP list): `authentication_service.py` `load_user_for_auth` L155, `authenticate_persistent_user` L400, local login response L735-745; `rest_api/security/idp/local.py:98`. All copy `user_projects` names verbatim.
- **Repository**: `user_project_repository.py` (`get_by_user_id`, `has_access`, `is_admin`, `get_project_names_for_user`, `can_project_admin_view_user`, `get_admin_visible_projects_for_user` - none check applications).
- **Migration**: `src/external/alembic/versions/`.

Reader classification (orphan surfacing):
- Surfaces orphan directly to user (no existence check): `user.py` `_get_user_response` (UI project selector source), `user_profile_router.py`, local login `projects_info` (L735), `user_access_service.get_user_projects_list` (admin view, L73), `user_management_service` L187 project-admin branch (`get_admin_visible_projects_for_user`), `member_spend_service.get_user_project_spend` L202 (lists memberships minus personal), `routers/analytics/spending.py:167`, `access_filter.py:144` (from `user.project_names`).
- Already guarded: `get_visible_projects_for_user` / `filter_visible_projects_from_map` (super-admin user views), `application_repository._build_visibility_condition` (L39, joins applications, excludes `deleted_at`), project list endpoints.
- RBAC (correct only once data is clean, since keyed by name): `has_access`, `is_admin`, `can_project_admin_view_user`, `security/authentication.py:314`, `llm_service/utils.py:51`.

### Integration Points
- **Rename**: `_resolve_updated_name` (`project_service.py:361-377`) returns 409 whenever *any* `user_projects` row exists for the project; `rename_project_cascade` (`user_project_repository.py` ~L314) has **no production callers** (tests only).
- **Create**: `create_shared_project` (L132) checks applications case-insensitively, then `add_project` (L178). An orphan row for (same creator, same name) hits `uix_user_project` -> caught `IntegrityError` -> 409. Orphans with other users silently re-attach (14501 RBAC issue) and then block deletion/rename of the new project with 409.
- **IDP / Keycloak bootstrap**: `create_user_from_idp` adds `user_projects` rows from claims (L229-232) *before* `_ensure_projects_exist` (L251) calls `aget_or_create`; enterprise `codemie_enterprise/migration/coordinator.py` (v2.3.44 in `.venv`) does the same (`add_user_project` L306, `get_or_create_application` L238; core deps in `src/codemie/enterprise/migration/coordinator.py:90-101`). `aget_or_create` is case-insensitive, so a claim name differing only in case yields a `user_projects` row with no exact-name applications row. Failures there are only logged.
- **Personal projects**: name = user email; `ensure_personal_project_async` runs on every persistent auth (`authentication_service.py:398`) and recreates the app + mapping if incomplete (`personal_project_service.py:152-240`). Email change soft-deletes old personal app (`deleted_at`) and removes its row (L111-140).
- **Budgets**: `ProjectMemberBudgetAssignment.project_name` FK -> `applications.id` (`budget_models.py:31`, migration `a1b2c3d4e5f7` L86/131 `ondelete="CASCADE"`). So member allocations cannot exist for a physically deleted project; `_sync_project_budget_member_removed` (`project_assignment_service.py:268`) also calls the LiteLLM provider (not SQL-only).
- **Cache**: `invalidate_user_from_cache` (`authentication_service.py:73`) clears an in-process token cache; migrations run at startup (`rest_api/main.py:450` `alembic_upgrade_postgres()`), so a restart already empties it.

### Patterns and Conventions
- Repositories are singletons, sync + async twins (`aget_by_user_id`, ...), flush-not-commit.
- `applications.name` has **no unique constraint**: only GIN trigram `ix_applications_name`; `ix_applications_name_lower` unique index is commented out in `98451a34b9d2...:86-91`. Uniqueness is effectively via PK `id == name`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/data/*.md` and `testing/*.md` exist but are ~1 KB stubs with no migration guidance.
- `src/external/alembic/README.MD`: migrations auto-apply at app startup; custom data migrations allowed; commands run from `src/external/alembic`.
- `docs/superpowers/tasks/2026-09-14-epmcdme-14501-project-admin-deletion/spec.md`: Non-Goals explicitly defer the FK (`ON DELETE CASCADE`) and "Backfilling orphaned UserProject rows from past deletions".

### Architectural Decisions
- Physical project deletion is intentional (14501); `deleted_at` used only for email-change personal projects.
- Data-repair migrations are irreversible by policy: `e5f9a1c3b7d2` downgrade only logs "intentionally not reverted" (mirrors `c3d4e5f6a7b8`).

### Derived Conventions
- Current alembic head: `e5f9a1c3b7d2_repair_ms_teams_migrated_settings.py` (down_revision `y1z2a3b4c5d6`). Revision ids mix random hex and hand-written pseudo-sequential ids; typed header (`revision: str`, `Union[...]`), license header.
- Data migrations use `op.execute`/`op.get_bind()` with `sa.text`, module `logger = logging.getLogger(__name__)`, a summary `logger.info("...: scanned=%d ...")`; schema-qualified `codemie.<table>` in `e5f9a1c3b7d2`, unqualified in `u1v2w3x4y5z6` (dedupe via window functions + `DELETE ... USING`).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/rest_api/routers/test_user_router.py`: `/v1/user` and `_get_user_response` (L84-250; management flag, `is_default`, legacy path). No orphan case.
- `tests/codemie/service/project/test_project_service_delete_update.py` (delete guards, creator cleanup from 14501), `test_project_service.py`, `test_personal_project_service.py`.
- `tests/codemie/repository/test_user_project_repository_{basic,default,extended,visibility,bulk_filter,story18,rename_cascade}.py`; `test_application_repository_visibility.py`.
- `tests/codemie/migrations/`: per-migration unit tests, e.g. `test_e5f9a1c3b7d2_repair_ms_teams_migrated_settings.py` imports `external.alembic.versions.<rev>` and tests pure helpers plus mocked bind; uses `alembic.script.ScriptDirectory` (head/chain checks).

### Testing Framework and Patterns
- pytest (+ pytest-asyncio), `unittest.mock` patching repository singletons; no live-DB migration tests found.

### Coverage Gaps
- No test asserting `/v1/user`, profile, login or security-context project lists exclude names with no applications row.
- No test of `user_projects` vs `applications` consistency; no migration touching `user_projects` data.

---

## 5. Configuration and Environment

### Environment Variables
- `ENABLE_USER_MANAGEMENT` (`configs/config.py:246`, default False): `_get_user_response` reads DB only when True; legacy path uses IDP claims. `IDP_PROVIDER` (keycloak/local) governs bootstrap paths.

### Configuration Files
- `src/external/alembic/alembic.ini`, `env.py`; `config.ALEMBIC_MIGRATIONS_DIR/INI_PATH` (`config.py:137-138`).

### Feature Flags and Deployment Concerns
- Migrations run in-process at every pod start (`main.py:448-452`, then enterprise migrations and `create_default_applications`); a long or failing data migration blocks startup.

---

## 6. Risk Indicators

- Orphan definition is name-based and case-sensitive in readers but creation is case-insensitive; a plain `LEFT JOIN applications ON name = project_name` may classify IDP-bootstrapped mixed-case rows as orphans. Speculative: compare `LOWER(name)` (and/or `id`) and report counts per class before deleting.
- Orphans re-attached to a recreated same-name project are invisible to a LEFT JOIN. The `user_projects.date < applications.date` signal is unreliable: IDP bootstrap and the Keycloak coordinator insert `user_projects` *before* `aget_or_create`, so legitimate rows also predate the app; `applications.date` uses naive `datetime.now()` while `user_projects.date` uses `datetime.now(UTC)`; legacy apps may have NULL `date`. Speculative: treat as report-only or require a large margin plus `created_by` mismatch.
- Rows pointing at soft-deleted applications (`deleted_at IS NOT NULL`) are a second "deleted project" class; `get_project_types_bulk` does not filter `deleted_at`.
- Personal-project rows (name = email) are self-healing on next login; deleting a personal row with a missing app is low risk, but excluding them (`project_name` matching a `users.email`) is the conservative choice.
- `is_default=true` on an orphan (possible via `set_default`, which checks only membership) would make `resolve_membership_default` return a deleted project.
- FK `user_projects.project_name -> applications.name` is not directly feasible: `applications.name` has no unique constraint (lower-unique index commented out). FK to `applications.id` is possible since `id == name`, and rename is already blocked when members exist; but IDP bootstrap/coordinator insert rows before the app exists and log-and-continue on failure, so an FK could break first login. Speculative: would need `DEFERRABLE` or reordered bootstrap, plus `ON UPDATE CASCADE` for the `update_project` id change.
- Readers without existence guard (Section 2) keep surfacing future inconsistencies if data drifts again.
- Startup-run migration on Production: must be idempotent and fast; 657 rows is small.

---

## 7. Summary for Complexity Assessment

The defect is data, not current code: `ProjectService.delete_project` (`project_service.py:570`) has removed the creator's `user_projects` row since 14501, and the non-creator 409 guard has existed since 2026-04-01. The 657 Production rows are `user_projects` entries with no matching `applications` row (no FK on `project_name`). They surface because `_get_user_response` (`routers/user.py:57`) and ~8 other readers (profile router, local login, security-context builders in `authentication_service.py` and `idp/local.py`, admin user-projects list, analytics spend) build project lists straight from `user_projects`. The super-admin visibility helpers already skip missing applications, which gives a precedent for an existence guard.

The likely change touches the migration layer (a new irreversible alembic data migration after head `e5f9a1c3b7d2`, following the `e5f9a1c3b7d2` logging/downgrade pattern and its unit-test style in `tests/codemie/migrations/`) and possibly the router/auth layer (existence filter on the project lists). It needs no new dependencies. The novel part is classifying rows safely: case-insensitive app matching from IDP bootstrap, rows re-attached to recreated projects (date heuristic unreliable), soft-deleted apps, and personal projects. Budget allocations cannot dangle (FK to `applications.id` with CASCADE) and the auth cache resets on the startup that runs the migration.

Tests cover `/v1/user`, delete guards and the repository broadly, but none exercise orphan rows or `user_projects`/`applications` consistency. Adding an FK is high-risk (no unique `applications.name`, IDP bootstrap inserts memberships before applications) and was explicitly deferred by 14501.

---

## 8. External References

None named by the task. (The 14501 spec in this repo, `docs/superpowers/tasks/2026-09-14-epmcdme-14501-project-admin-deletion/spec.md`, was read as related context; see Section 3.)
