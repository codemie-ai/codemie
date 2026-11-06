# Technical Research

**Task**: assistant sharing access-control permissions users
**Generated**: 2026-09-08T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-14786 — Share an Assistant with Selected Users

Story: As a team member who owns an assistant, I want to grant specific internal colleagues access to use that assistant, so that I can let particular people benefit from it without exposing it to my whole project.

Key requirements:
- Only the assistant owner can grant or revoke access.
- Users granted access cannot re-share the assistant.
- Share targets are limited to internal CodeMie users already registered on the platform.
- Recipients can open and use the assistant / start or continue conversations.
- Recipients cannot edit assistant configuration: prompt, tools, knowledge base, model settings, etc.
- Per-user sharing must coexist with the existing project-wide `shared` flag.
- The UI must include:
  - Share entry point on the assistant.
  - Grant-access view.
  - 'Add people' chip-input with filtered suggestions showing avatar, name, email.
  - Selected users shown as removable chips before granting access.
  - 'People with access' list with immediate remove/revoke action.

Acceptance Criteria:
1. Given a user owns an assistant, when they open the assistant's share/grant-access view and select one or more internal users, then those users are granted access to use that assistant.
2. Given an assistant has been shared with a user, when that user opens the platform, then the shared assistant appears in their accessible assistants.
3. Given a user has been granted access to a shared assistant, when they open it, then they can start or continue a conversation with it, but cannot see or use assistant-editing controls.
4. Given an owner opens the grant-access view for an assistant already shared with someone, when they remove that person, then access is revoked immediately.
5. Given access has just been revoked, when that user attempts to open or use the assistant afterward, then they no longer see it and cannot interact with it.
6. Given an owner is granting access, when they search for a recipient, then only internal CodeMie users can be found and selected.
7. Given a user without owner permission attempts to share an assistant, then the action is rejected and no access is granted.
8. Given sharing/revocation actions occur, the shared list should be displayed in Assistant details.
9. Given an assistant is shared with the user, it should be shown in Project assistants under the user's personal project.
10. Given an assistant is already project-shared (shared: true), when its owner also grants per-user access, then both mechanisms coexist without conflict or duplicate/contradictory access states.

Out of Scope:
- External/non-org sharing.
- Edit/collaboration access.
- Sub-assistant exposure handling for external links.
- Bulk add/remove and distribution-list sharing.
- Changes to the existing project-wide `shared` flag.
- Chats/folders sharing; planned separately in EPMCDME-11988.

---

## 2. Codebase Findings

### Existing Implementations

**Assistant model and access check chain:**
- `src/codemie/rest_api/models/assistant.py` — `AssistantBase` (L644) carries the `shared: bool` field (L659, default `True`). Its `is_shared_with(user)` method (L858) returns `True` if the assistant is global (marketplace) or if `assistant.project in user.project_names and self.shared`. This is the only sharing predicate for non-global assistants today.
- `src/codemie/core/ability.py` — `Ability` class (L76). `PERMISSIONS["Assistant"]` (L94–99) maps READ to `[Role.SHARED_WITH, Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN]`. `can()` (L165) calls `instance.is_shared_with(user)` for READ. WRITE/DELETE are owner/manager/admin only.
- `src/codemie/service/assistant/assistant_repository.py` — `AssistantRepository._filter_for_regular_user_visibility` (L662) and `_get_non_global_base_condition` (L579) define the SQL-level visibility filter. Both currently gate non-global assistants on `assistant.project in user.project_names AND assistant.shared == True` (plus owner/admin paths). No per-user sharing column is queried.

**Existing per-resource permission infrastructure:**
- `src/codemie/rest_api/models/permission.py` — `Permission` table (L58, `__tablename__ = "permissions"`) stores `resource_type`, `resource_id`, `principal_type`, `principal_id`, `permission_level`. Currently `ResourceType` (L29) only enumerates `DATASOURCE`; `PrincipalType.USER` (L55) exists in the enum but `PermissionCreationService._check_principal` (L84) raises `NotImplementedError` for it.
- `src/codemie/service/permission/permission_creation_service.py` — `PermissionCreationService.run()` creates a `Permission` row after checking `Ability(user).can(Action.WRITE, resource)`. The `_check_principal` method explicitly has a `raise NotImplementedError("User principal type is not supported yet")` stub.
- `src/codemie/rest_api/routers/permission.py` — REST surface: `POST /v1/permissions` and `DELETE /v1/permissions/{permission_id}`.
- `src/codemie/service/permission/permission_exceptions.py` — `PermissionAccessDenied`, `PermissionResourceNotFound`, `PermissionPrincipalNotFound`.
- `src/codemie/service/permission/__init__.py` — re-exports the services above.

**User lookup infrastructure:**
- `src/codemie/rest_api/security/user.py` — `User` model. `is_external_user` (L108) checks `user_type == config.EXTERNAL_USER_TYPE`. Internal users are those where `is_external_user` is `False`.
- `src/codemie/rest_api/models/user_management.py` — `UserProject` (L62), `CodeMieUserDetail` (L252, includes `id`, `username`, `email`, `name`, `picture`, `user_type`, `is_active`).
- `src/codemie/repository/user_project_repository.py` — `UserProjectRepository`: membership and admin queries. No existing "search users by name/email" method exposed.

**Assistant listing (visibility) layer:**
- `src/codemie/service/assistant/assistant_repository.py` — `AssistantScope` enum (L32): `VISIBLE_TO_USER`, `ALL`, `CREATED_BY_USER`, `MARKETPLACE`, `PROJECT_WITH_MARKETPLACE`. The `query()` method (L145) calls `_apply_scope_filters` → `_filter_for_regular_user_visibility` for non-admins.
- `_filter_for_regular_user_visibility` (L662) is the single choke-point for listing: condition uses `Assistant.shared` and `user.project_names` exclusively for non-global, non-owner, non-admin cases.

**Analogous sharing patterns in codebase:**
- `src/codemie/rest_api/models/share/shared_conversation.py` — `SharedConversation` table used for public URL-based conversation sharing (different feature, not per-user).
- `src/codemie/repository/assistants/assistant_user_interaction_repository.py` — `SQLAssistantUserInterationRepository` tracks usage/reactions per `(assistant_id, user_id)`.
- `src/codemie/rest_api/models/usage/assistant_user_mapping.py` — `AssistantUserMappingSQL` table (L89, `__tablename__ = "assistant_user_mapping"`) maps `(assistant_id, user_id, workflow_id)` with tools config. This is the closest structural analog to a per-user assistant relationship table.

### Architecture and Layers Affected

| Layer | Component | What is touched |
|---|---|---|
| **Data / Model** | `src/codemie/rest_api/models/assistant.py` | `AssistantBase.is_shared_with()` must be extended to check per-user grants |
| **Data / Model** | `src/codemie/rest_api/models/permission.py` | `ResourceType` enum needs an `ASSISTANT` value; `PrincipalType.USER` path needs implementation |
| **Data / DB schema** | `src/external/alembic/versions/` | New Alembic migration required for any new table or column |
| **Repository** | `src/codemie/service/assistant/assistant_repository.py` | `_filter_for_regular_user_visibility` / `_get_non_global_base_condition` must include per-user grants in the SQL WHERE clause |
| **Service** | `src/codemie/service/permission/permission_creation_service.py` | `_check_principal` USER branch must be implemented; owner-only guard must be enforced for assistants |
| **API router** | `src/codemie/rest_api/routers/assistant.py` | Share/revoke endpoints or delegation to existing permissions router |
| **Ability/Auth** | `src/codemie/core/ability.py` | `Ability.PERMISSIONS["Assistant"]` READ path already includes `Role.SHARED_WITH`; `AssistantBase.is_shared_with()` drives it — extending the method is the primary hook |

### Integration Points

- `Ability.can(Action.READ, assistant)` → `AssistantBase.is_shared_with(user)` — the single object-level READ gate. Any per-user grant must surface here.
- `AssistantRepository.query()` → `_filter_for_regular_user_visibility()` — SQL list filter; must be extended to return assistants where a per-user grant row exists for the requesting user.
- `Permission` table + `PermissionCreationService` / `PermissionDeletionService` — the existing general-purpose per-resource permission infrastructure; `ResourceType.ASSISTANT` does not yet exist.
- `User.is_external_user` — the "internal users only" constraint maps to `not user.is_external_user`.
- Personal project visibility (AC#9): `AssistantRepository._build_project_with_marketplace_query` / `_filter_for_regular_user_visibility` — the "personal project assistants" view must surface per-user-granted assistants even when `assistant.project not in user.project_names`.

### Patterns and Conventions

- `Owned` abstract base (L48, `src/codemie/core/ability.py`) — every resource subject to `Ability` must implement `is_owned_by`, `is_managed_by`, `is_shared_with`.
- `BaseModelWithSQLSupport` (L370, `src/codemie/rest_api/models/base.py`) — all persistent SQL models inherit from this; provides `get_by_id`, `find_by_id`, `save`, `delete`, `get_all_by_fields`.
- Alembic is the mandatory migration path (`src/external/alembic/versions/`) — no runtime schema creation.
- Router → Service → Repository layering enforced by guide; direct DB calls from routers are not used.
- `PydanticListType` / `PydanticType` for JSONB-stored complex types.
- Existing `Permission` table pattern: `resource_type` + `resource_id` + `principal_type` + `principal_id` + `permission_level` with a unique constraint.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/architecture/layered-architecture.md` — confirms Router → Service → Repository; all HTTP concerns stay in routers.
- `.ai-run/guides/data/database-patterns.md` — mandates Alembic for schema changes; migrations under `src/external/alembic/versions/`.

### Architectural Decisions

No ADR was found specifically about assistant sharing. The `Permission` model comment in `permission.py` (L59) notes it is "for storing permissions for resources, e.x. datasources or assistants", suggesting the table was designed with assistant sharing in mind but not yet activated.

### Derived Conventions

- `ResourceType` enum is the extension point for new resource types; adding `ASSISTANT = "assistant"` follows the existing `DATASOURCE` pattern.
- `PrincipalType.USER` already exists as an enum value with a `NotImplementedError` stub — the pathway is explicitly reserved.
- The `is_shared_with` method on `AssistantBase` is the single READ gate consulted by `Ability`; extending it to check a DB table (or an in-memory set of granted user IDs) is the canonical hook.
- SQL-level list queries in `AssistantRepository` use direct `Assistant` column predicates — a subquery or JOIN on a grant table will be needed there in parallel.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/assistant/test_assistant_repository.py` — unit tests for `AssistantRepository.query()` covering admin, regular user, external user, and scope variants. Tests mock `Session` and build `Assistant` fixtures.
- `tests/codemie/service/assistant/test_assistant_repository_sort.py` — sort-related repository tests.
- `tests/codemie/core/test_ability.py`, `tests/codemie/core/test_ability_conversation.py` — `Ability` permission logic tests.
- `tests/codemie/rest_api/routers/test_permission.py` — permission router tests (`test_create_permission_resource_not_found`, `test_delete_permission_not_found`).
- `tests/codemie/service/permission/test_permission_resource_service.py` — permission service tests.
- `tests/codemie/rest_api/models/test_permission_models.py` — `ResourceType` model tests.

### Testing Framework and Patterns

- pytest with `unittest.mock.MagicMock` and `patch` decorators.
- `Session` is patched at the repository level; SQL queries are exercised via mock exec/all chaining.
- `User` is constructed as a `MagicMock` with `.is_admin`, `.project_names`, `.admin_project_names`, `.id` set explicitly.
- No integration/DB test fixtures found for permission tests — all are unit-level mocks.

### Coverage Gaps

- No tests for `AssistantBase.is_shared_with` covering a per-user grant path (the method currently tests only `is_global` and `project in user.project_names`).
- No tests for `_filter_for_regular_user_visibility` covering a per-user grant subquery.
- No tests for `PermissionCreationService._check_principal` with `PrincipalType.USER` (currently raises `NotImplementedError`).
- No tests for owner-only enforcement on the new share/revoke surface.
- No tests for the "assistant appears in personal project view" path when accessed via per-user grant.

---

## 5. Configuration and Environment

### Environment Variables

No environment variables specifically govern assistant per-user sharing today. Relevant ambient config:
- `config.EXTERNAL_USER_TYPE` — used by `User.is_external_user`; the internal-users-only constraint relies on this.
- `config.ENABLE_USER_MANAGEMENT` — gates the user management router; user lookup endpoints live behind this flag.
- `config.EXTERNAL_USER_ALLOWED_PROJECTS` — controls external-user marketplace access; irrelevant to this feature since sharing is internal-only.

### Configuration Files

- `src/codemie/configs/config.py` — central config object.
- No feature flag currently exists for per-user assistant sharing.

### Feature Flags and Deployment Concerns

No deployment manifests or feature flags govern this surface today. Any new DB table or column requires an Alembic migration to run against all environments before code deployment.

---

## 6. Risk Indicators

- **`Permission` table `ResourceType` only covers `DATASOURCE`** — `type_for_instance()` (L38, `permission.py`) raises `ValueError` for an `Assistant` instance today. Adding `ASSISTANT` to the enum is a required extension that touches the `ResourceType._lookup` property, the `PermissionCreationService._check_ability` path, and any code that calls `type_for_instance`.
- **`PermissionCreationService._check_principal` USER path raises `NotImplementedError`** — user-principal permission creation is explicitly unimplemented. This is the core gap for the share-grant flow.
- **SQL visibility filter is not permission-table-aware** — `_filter_for_regular_user_visibility` and `_get_non_global_base_condition` in `assistant_repository.py` are pure SQL filters on `Assistant` columns. They do not join or subquery the `permissions` table. Listing "assistants shared with me" (AC#2, AC#9) requires a parallel SQL change in addition to the object-level `is_shared_with` fix.
- **`is_shared_with` is called at object-level, not list-level** — `Ability.can(Action.READ, assistant)` is only used for single-object reads (e.g., opening a specific assistant), not for the list query. The list is entirely driven by `_filter_for_regular_user_visibility`. A fix to `is_shared_with` alone would not make shared assistants appear in lists.
- **No user search endpoint for "add people" chip-input** — the task's UI requires filtered user suggestions by name/email. A user search/lookup API endpoint (filtered to internal users) does not appear to exist in the current assistant-facing API surface. This may require a new endpoint or extension to the user management router.
- **`AssistantUserMappingSQL` is tool-config oriented** — while structurally similar to what a per-user grant table would look like, this table records tool integration configuration per user, not access grants. It should not be repurposed.
- **Personal project view (AC#9)** — the "personal project assistants" view is driven by `_build_project_with_marketplace_query` which filters on `assistant.project == personal_project_name`. Assistants from other projects that are per-user-granted would not naturally appear here; a separate OR condition or scope extension is required.
- **No existing tests for the per-user sharing path** — all coverage gaps listed in Section 4 represent new test surface.
- **Alembic migration required** — any new table (e.g., `assistant_user_grants`) or column must go through `src/external/alembic/versions/`; deployment ordering matters.
- **Owner-only revoke enforcement** — AC#7 requires rejecting share/revoke from non-owners. The `Ability` WRITE check (`Role.OWNED_BY | Role.MANAGED_BY | Role.ADMIN`) currently permits project managers and admins to write assistants; the share/revoke surface must be owner-only, which is stricter than the existing WRITE permission. This requires an explicit owner check, not just `Ability(user).can(Action.WRITE, assistant)`.

---

## 7. Summary for Complexity Assessment

This task spans four distinct architectural layers: the data model (`AssistantBase.is_shared_with`, `Permission` table schema and `ResourceType` enum), the SQL list-query layer (`AssistantRepository._filter_for_regular_user_visibility` and `_get_non_global_base_condition`), the service layer (`PermissionCreationService` USER branch and owner-only enforcement), and the API router layer (new share/revoke endpoints and a user-search endpoint for the chip-input). The object-level READ gate and the list-level SQL filter are separate code paths that must both be updated — a change to one without the other produces incorrect behavior in either single-object access or list views.

The existing `Permission` table and `PermissionCreationService` infrastructure is designed to support this feature (the code comments reference assistants and the USER enum value is reserved with a `NotImplementedError`), which reduces novelty risk. However, the USER principal path is fully unimplemented, `ResourceType` does not include `ASSISTANT`, and the SQL repository layer is entirely unaware of the permissions table. Additionally, AC#9 (shared assistants appearing in the personal project view) requires a separate query path change beyond the standard visibility filter. The owner-only sharing constraint is stricter than the existing WRITE permission scope (which includes managers and admins), requiring a dedicated guard.

Test coverage for the affected paths — `is_shared_with`, `_filter_for_regular_user_visibility`, `PermissionCreationService` USER path, and personal-project view — is entirely absent. The repository tests use Session mocks and will need new fixtures covering the per-user grant join/subquery. A new Alembic migration is required before deployment. The combination of multi-layer changes (model + SQL filter + service + router + migration), the unimplemented `PermissionCreationService.USER` path, and the stricter-than-WRITE ownership requirement for share/revoke places this task in medium-to-high complexity.

---

## 8. External References

None named by the task.
