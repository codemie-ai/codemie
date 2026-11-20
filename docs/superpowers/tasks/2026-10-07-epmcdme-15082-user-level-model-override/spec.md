# User-Level Model Override Enforcement — Specification

**Task**: Implement user-level model override enforcement for project admins to configure and enforce model access overrides for individual or bulk-selected users, with precedence over project settings and fallback logic. A model disabled at platform level is skipped to the next fallback tier (see 3.2). A separate disablement store, global (marketplace) assets and the user notice before a fallback are out of scope (see Section 8).

**Size**: L (4–5 days)

**Generated**: 2026-09-21

---

> **Scope change (project-whitelist subset check dropped):** `_validate_override_models` no longer requires `allowed_models ⊆ project.allowed_models`. An admin may grant a user access to any platform model regardless of the project's own whitelist; only emptiness and project-existence are still validated at set-time. The invocation-time chain (`ModelAvailabilityService.resolve_user_override_model`) was already unaffected — tier 1 checks only `override.allowed_models` and never consults the project whitelist. Wherever the sections below still describe override models as constrained to be a subset of the project whitelist, that content is superseded by this note.

> **Scope change (Jira ACs revised):** The ticket's acceptance criteria were updated; the list in section 2.1 is the current one. Two changes against the previous version: (a) fallback now uses the default of the *user's scope* — the user-level override's `default_model` when the user has an override, otherwise the project default (project-scoped asset) or the platform default (all other cases) — instead of ignoring the user-level override; (b) the acceptance criterion that a platform-disabled model stays blocked even if the user override includes it was removed. The ticket's Context still covers disabled models: they are a hard stop that moves resolution to the next tier (User Override ➔ Project Default ➔ Platform Default) instead of raising an error (see 3.2). The implementation-level criteria in section 2.2 and the rest of this spec are traced to the Jira list in section 2.1.
>
> **Scope change (fallback candidates):** the override fallback no longer picks the first model of `allowed_models`. When the override has no `default_model`, the fallback is the project default, then the platform default.
>
> **Disabled models skip a tier:** the ticket's Context makes a model disabled at platform level a hard stop that moves resolution to the next tier (User Override ➔ Project Default ➔ Platform Default) instead of raising. Every tier of the user-override chain checks liveness with `is_known_model` (the enabled-model catalog or an enabled router): a disabled requested model goes to the override's `default_model`, a disabled override default to the project default, a disabled project default to the platform default. The model list does not mark a disabled override default as the default.

> **Deprecated models (ticket Context, 2026-11-17):** the ticket's Context now carries the platform model management rule (EPMCDME-15077) for deprecated models: they stay usable where already selected but cannot be selected anew. For overrides this means: saving an override rejects a deprecated model it does not already list, as an allowed model or as its default; a bulk grant accepts a deprecated model only when every selected user's override already lists it; at invocation a deprecated model already in an override keeps working, and resolution is not changed. See 3.2.1. The rule is in the Context, not in the acceptance criteria list.

## 1. Overview

Project admins need granular control over LLM model access at the user level. The feature allows admins to override project-level model whitelists for individual users or bulk-selected users, with a secure 3-tier precedence chain that respects project constraints. User-level overrides take precedence over project settings and are no longer required to be a subset of the project whitelist. A model disabled at platform level is skipped to the next fallback tier (see 3.2).

---

## 2. Scope & Acceptance Criteria

### 2.1 Jira Acceptance Criteria (EPMCDME-15082, current)

| # | Criterion | Covered by |
|---|---|---|
| J1 | Given a project admin is viewing a single user within their project, when they set a model access override for that user, then the override is saved and associated with that user within that project. | Section 4.1 (POST single-user endpoint), `UserModelOverrideService.set_override` |
| J2 | Given a project admin has selected multiple users (bulk selection), when they apply a model access override to the selection, then the same override is saved for every selected user. | Section 4.2 (POST /batch, grant), `batch_set_overrides` |
| J3 | Given a user has a model-access override granting a model that is outside their project's model set, when that user invokes a project-scoped asset whose selected model is in the user's override, then the invocation resolution check passes and that model is used — the user is not blocked by the project-level restriction. | Section 3.5 tier 1, `resolve_user_override_model`; section 3.3 (no project-whitelist subset check); asset-creation check in `validate_model_for_asset_creation` |
| J4 | Given a user has both a project-level model set and a user-level override, when the system resolves whether the caller can invoke the asset's selected model, then the user-level override takes precedence over the project's model set for that determination — whether the override expands or narrows the user's available models. | Section 3.5 (tier 1 before tier 2, same call sites as the project-level resolution); section 5.2 |
| J5 | Given a user has a model-access override that excludes a model otherwise available at the project level, when that user invokes an asset whose selected model is excluded by their override, then the invocation resolution check fails for that user, even though other users in the same project can access that model. | Section 3.5 tier 1: once an override row exists the project whitelist is never consulted, so the excluded model is replaced by the user's fallback (J6) and never reaches the project whitelist; other users are unaffected |
| J6 | Given a user's access check fails and fallback is required, when the system selects the fallback model, then it uses the default associated with the user's scope (user-level override if it exists and has a default, otherwise falling back to the project default for a project-scoped asset, or the platform default for all other cases) | Section 3.5 tier 1 fallback order, tiers 2-3, workflow system nodes; `_pick_from_override`, `resolve_user_override_model` |

The Jira list has no platform-disablement criterion any more (see section 8). The deprecated-model rule is in the ticket's Context, not in this list (see 3.2.1).

### 2.2 Implementation Acceptance Criteria

1. **UserModelOverride database table** — SQLModel table defined in `src/codemie/rest_api/models/user_model_override.py` with fields: id (UUID PK), user_id (FK to UserDB, deferrable), project_name (together with user_id a composite FK to user_projects, see 3.1), allowed_models (ARRAY), default_model (optional), date, update_date (the platform's BaseModelWithSQLSupport audit columns). user_id is a string matching users.id. Unique constraint on (user_id, project_name). Check constraint: allowed_models must not be empty. There is no enabled/disabled flag: a missing row means the user has no override.

2. **UserModelOverrideService** — New service in `src/codemie/service/` orchestrating override CRUD and validation. Methods: set_override, get_override, delete_override, get_overrides_by_project, batch_set_overrides (model resolution is `ModelAvailabilityService.resolve_user_override_model`, see 3.5). Set-time validation checks that allowed_models is non-empty, every id names a known model that is not disabled at platform level, no model the override does not already list is deprecated (3.2.1), and the project exists; override models are no longer required to be a subset of project allowed_models.

3. **UserModelOverride repository** — New repository in `src/codemie/repository/` (synchronous, like the project override) supporting queries by (user_id, project_name) and project_name for admin bulk operations.

4. **API endpoints** (all need WRITE access to the project) — PUT/GET/DELETE for single-user overrides; POST /batch for bulk operations. Batch endpoint supports grant/revoke operations with atomic transaction scoping.

5. **3-tier precedence chain** — At invocation time, model resolution follows: (1) user override if a row exists for the user and project, (2) project whitelist, (3) default model. A model disabled at platform level is skipped to the next tier at each step of the user-override chain (see 3.2); there is no separate hard-stop tier, ApplicationEnrichment table or PLATFORM_DISABLED_MODELS setting.

6. **ModelAvailabilityService integration** — The user override is applied by `resolve_user_override_model` ahead of the project-level resolution at each call site; `resolve_model_for_execution` and `ProjectModelPolicy` are unchanged. Within the user-override chain a model disabled at platform level is skipped to the next tier (see 3.2).

7. **Validation at set-time** — Override creation fails if allowed_models is empty/blank, names an unknown or platform-disabled model, or adds a deprecated model the override does not already list (ValueError, 400), or if the project does not exist (NotFoundException); default_model must be one of allowed_models (ModelNotWhitelistedException). Override models are no longer validated against the project whitelist.

8. **Override replaces the project set** — Once a row exists it is the user's whole model set, so a later change to the project's whitelist does not affect that user and nothing re-validates the override against it. A requested model that is not in the override falls back to the override's default, then the project default, then the platform default; the project whitelist itself is never consulted for this user.

9. **Batch atomicity** — POST /batch rolls back entire transaction if any user fails validation. Returns 400 with failed user details (not 207 partial success).

10. **Admin gating** — All override endpoints are authorized with the shared `_authorize_project_access` dependency, like the other project write routes: WRITE access to the project (its owner, a project admin or a platform admin/maintainer). A caller without it, or a missing or deleted project, gets 404, so the existence of a project is not revealed.

11. **Backward compatibility** — Legacy projects (no user overrides) continue using project-level whitelists only.

12. **Test coverage** — Unit tests for all service methods (set, get, delete, batch, resolve), API endpoint tests (admin gating, validation, batch atomicity), and tests for the precedence chain (an override that allows or omits the model, with and without its own default; no override; a deny-all project; the feature flag off).

---

## 3. Architecture & Data Model

### 3.1 Database — UserModelOverride Table

**Location**: `src/codemie/rest_api/models/user_model_override.py`, next to `UserProject` (`user_management.py`), whose lifecycle the row follows. The alembic env imports it so the table stays in the metadata.

**Definition**:
```python
class UserModelOverride(BaseModelWithSQLSupport, table=True):
    """
    User-level override for LLM model access within a project.

    Once a row exists it is the user's whole model set; the project whitelist is not consulted.
    One override per (user_id, project_name) pair; a missing row means the user has no override.
    """
    __tablename__ = "user_model_overrides"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: str = Field(nullable=False, index=True)
    project_name: str = Field(max_length=100, nullable=False, index=True)
    allowed_models: list[str] = Field(sa_column=Column(ARRAY(String), nullable=False))
    default_model: str | None = Field(default=None, max_length=255)  # first fallback; must be in allowed_models
    # date / update_date come from BaseModelWithSQLSupport

    __table_args__ = (
        # the user, deferrable so the user id remap (migration c6f1a2b3d4e5) can move users.id and its children
        ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_model_overrides_user_id",
                             ondelete="CASCADE", deferrable=True, initially="DEFERRED"),
        # the membership: the override goes when the membership goes and follows a project rename
        ForeignKeyConstraint(["user_id", "project_name"], ["user_projects.user_id", "user_projects.project_name"],
                             name="fk_user_model_overrides_membership",
                             ondelete="CASCADE", onupdate="CASCADE", deferrable=True, initially="DEFERRED"),
        UniqueConstraint("user_id", "project_name", name="uq_user_model_override_user_project"),
        CheckConstraint("array_length(allowed_models, 1) IS NOT NULL", name="ck_user_model_override_has_models"),
    )
```

There is deliberately no foreign key to `applications`: a project rename moves `user_projects.project_name` but not `applications.id`, so such a key would block the rename cascade. The membership key already ties the row to the project, and hard-deleting a project requires every member but its creator to be removed first and then removes the creator's membership itself, which cascades to the overrides. The behaviour of both keys (remap in either order, rename, membership removal, upgrade and downgrade) was run against PostgreSQL 17 when they were introduced.

### 3.2 Platform-Disabled Models

The ticket's Context makes a model disabled at platform level a hard stop at every scope that moves resolution to the next fallback tier rather than raising an error: User Override ➔ Project Default ➔ Platform Default.

- **What counts as disabled:** a model that is not in the enabled-model catalog (`enabled: false` in the YAML configuration, or absent from the LiteLLM catalog) and is not an enabled router. The check is `is_known_model` in `model_availability_service.py`, the same rule the override's set-time validation uses.
- **Where it applies:** every tier of `resolve_user_override_model`. A disabled requested model goes to the override's `default_model`; a disabled override default goes to the project default; a disabled project default goes to the platform default. `default_llm_model` already prefers an enabled platform default, and there is no tier after it. Each skip is logged (`user_model_override=model_not_live tier=...`).
- **The model list:** `GET /v1/llm_models` does not mark a disabled override default as the default; the project default is marked instead.
- **Asset creation is unchanged:** `validate_model_for_asset_creation` decides by override membership only, so a model the override lists passes even after it was disabled. At run time resolution skips it to the next tier.
- **Not part of this ticket:** a separate disablement store or setting (an `ApplicationEnrichment` table, a `PLATFORM_DISABLED_MODELS` setting), and set-time validation against such a store. The skip relies on the existing enabled-model catalog only.

### 3.2.1 Deprecated Models

The ticket's Context applies the platform model management rule (EPMCDME-15077): a deprecated model stays usable where it is already selected but cannot be selected anew. An override follows the same rule as every other place a model is saved (project default, admin global default, assistant, workflow, conversation).

- **What counts as deprecated:** a model whose status in platform model management is `deprecated`, or a Switchyard router with a deprecated tier. The check is `llm_service.assert_model_selectable`, the rule the other save paths use; there is no override-specific rule.
- **Saving one override (PUT):** a deprecated model the request adds is rejected with 400, as an allowed model or as the default (the default is always one of allowed_models). A deprecated model the stored override already lists, by its base_name or deployment_name, is kept, so an admin can still edit the override after one of its models was deprecated. `UserModelOverrideService._reject_new_deprecated_models` runs after the existing checks; the stored override is read once and reused for the write.
- **Bulk grant (POST /batch):** a deprecated model counts as kept only when every selected user's override already lists it. Otherwise the whole batch is rejected and no user is changed (5.3).
- **Invocation:** unchanged. A deprecated model already in an override keeps working: `is_known_model` treats only disabled models as not live, so neither the requested model nor the override default is skipped for being deprecated.
- **Not part of this ticket:** how a deprecated project default or platform default is handled during fallback. That follows the platform and project rules of EPMCDME-15077 and is unchanged here; on `main` assistant chat can substitute a deprecated project default, while workflow execution does not.

### 3.3 Service Layer — UserModelOverrideService

**Location**: `src/codemie/service/user/user_model_override_service.py` (new file)

**Key Methods** (synchronous; each takes the caller's session and does not commit, the route commits; every write takes `actor_id`):
- `set_override(session, user_id: str, project_name: str, allowed_models: list[str], default_model: str | None = None, *, actor_id: str) -> UserModelOverride` — Create or replace the override; validate the models; raise ModelNotWhitelistedException when `default_model` is not in `allowed_models`; write a `user.model_override.set` activity event when the stored override changes.
- `get_override(session, user_id: str, project_name: str) -> UserModelOverride | None` — Fetch override for user-project pair.
- `delete_override(session, user_id: str, project_name: str, *, actor_id: str) -> UserModelOverride | None` (the deleted record) — Remove override; writes `user.model_override.removed`.
- `get_overrides_by_project(session, project_name: str, skip: int = 0, limit: int = 100) -> list[UserModelOverride]` — List all overrides for admin view.
- `batch_set_overrides(session, project_name: str, user_ids: list[str], operation: 'grant' | 'revoke', allowed_models: list[str] | None = None, default_model: str | None = None, *, actor_id: str) -> list[UserModelOverride]` — Apply the same override to, or remove it from, multiple users; atomic transaction (all or nothing). Users, memberships and existing overrides are read with one query each and written with one flush, so the cost does not grow with the number of users.
- Model resolution is exposed by `ModelAvailabilityService.resolve_user_override_model` (section 3.5); the service has no separate `resolve_model_for_user`.

**Validation Helper**:
- `_validate_override_models(session, project_name: str, allowed_models: list[str]) -> None` — Check: allowed_models is non-blank/non-empty, and the project exists. Does **not** check allowed_models against the project whitelist — override models may be any platform model, independent of the project's own allowed_models.

### 3.4 Repository Layer — UserModelOverrideRepository

**Location**: `src/codemie/repository/user_model_override_repository.py` (new file; all methods synchronous, `user_id` is a `str`)

**Key Methods**:
- `get_by_user_and_project(session, user_id: str, project_name: str) -> UserModelOverride | None`
- `get_by_users_and_project(session, user_ids: list[str], project_name: str) -> dict[str, UserModelOverride]` (one query for many users)
- `get_by_project(session, project_name: str, skip: int, limit: int) -> list[UserModelOverride]`
- `create(...)`, `update(...)`, `delete(session, user_id, project_name) -> UserModelOverride | None` (the deleted record)
- `upsert_many(...)` and `delete_many(...)` for the batch, each with a single flush

### 3.5 Model Resolution — 3-Tier Precedence Chain

**Integration point**: `src/codemie/service/llm/model_availability_service.py` (extend existing)

**Precedence (highest to lowest)**:
1. **User Override** — If a UserModelOverride row exists, check if requested_model in allowed_models. If it is in - proceed to invocation. If not in list (the model is excluded by the override), fallback in this order: the override's `default_model` (if set and in allowed_models), otherwise the project default (`ProjectModelPolicy`, the same default tier 2 uses); if the project has no default either, the platform default model. A project that denies all models rejects the request with `ModelNotWhitelistedException`. The override's other `allowed_models` are not used as fallback candidates. At each tier a model disabled at platform level (not in the enabled-model catalog and not an enabled router) is skipped to the next tier. The project whitelist is not consulted for this user once an override row exists. If a UserModelOverride row doesn't exist - proceed to tier 2. If feature flag `userModelOverride` is not enabled for UserModelOverride - proceed to tier 2. Global assets skip the override (tier 2 and 3 apply).
2. **Project Whitelist** — Check if requested_model in Application.allowed_models. If yes, proceed. If no - fallback to default project model. If Project whitelist doesn't exist for a project or Feature flag `projectModelOverride` is disabled, move to tier 3.
3. **Default Model** — if there is no User Override and Project Whitelist or both features are disabled - keep the previous logic, existing default model will be used (the platform default for all other cases).

**Fallback default by scope** (Jira AC J6): the fallback model is always the default of the user's scope — the user-level override's default when the user has an override, else the project default for a project-scoped asset, else the platform default for all other cases.

**Model listing (chat window)**: the same override also drives the list of selectable models, not only invocation. In `LLMService.get_allowed_chat_models` (used by `GET /v1/llm_models`, `GET /v1/default_models/{category_id}` and other callers), when `userModelOverride` is enabled and a project is given:
- If the user has an override for that project, the visible chat models are limited to `override.allowed_models` (matched by `base_name` or `deployment_name`). This **replaces** the project-whitelist filter — it is applied first and the project filter is skipped, mirroring tier 1 of the invocation chain.
- If there is no override (or the flag is off, or no project is given), the project-whitelist filter applies as before.
- The lookup is `LLMService.get_user_model_override(user, project)`; it is independent of the `projectModelOverride` flag.
- With `userModelOverride` on, `project_id` must reference a project the caller can access (`User.has_access_to_application`); otherwise `GET /v1/llm_models` and `GET /v1/default_models/{category_id}` return 403 (404 when an authorized caller names a project that does not exist). With the flag off the check is skipped and a missing project is a 404, as before the feature. The chat UI passes the chat's project as `project_id`, but only when `projectModelOverride` or `userModelOverride` is enabled.
- If none of the override's models is in the catalog (a stale override), the list falls back to the project set so the user is not left with nothing to pick, while resolution goes to the project default.
- Router options (`LLMService.get_allowed_router_options`, called by `GET /v1/llm_models` with the caller) are narrowed the same way: with an override only the routers it lists are offered, matched by `base_name`, and an override that lists no router offers none (no fallback to the project set); without an override the project whitelist applies.
- In `GET /v1/llm_models` the `default` flag is set from the override's `default_model` when present, otherwise from the project's `default_model`.

**Workflow nodes** (conversation summarization, result finalizer, state processor, supervisor): these nodes have no model of their own, so the requested model is the node's configured model, else the workflow's `default_model`. They resolve it in this order:
1. If the executing user has an override for the workflow's project (`userModelOverride` enabled), resolve through it with `ModelAvailabilityService.resolve_user_override_model` (requested model if the override allows it, else the override's `default_model`, else the project default, else the platform default). Global workflows skip the override.
2. Otherwise use the project's default model, subject to the project whitelist when `projectModelOverride` is enabled.
3. Otherwise keep the previous behavior (the workflow's default model, then the fixed fallback model).

The nodes call `BaseNode.resolve_user_override_model(requested_model)`, so an override that excludes the requested model is enforced even when it has no `default_model`. The agent node resolves the model of its assistant the same way with `fail_open=True`, like its project resolution, so a model-availability failure there does not abort the whole run.

**Other places that apply the override**: the OCR model that tools request by name (`ToolkitService._build_ocr_model`) is checked with `ModelAvailabilityService.user_override_allows_model` and rejected, not substituted; `enforce_allowed_model` tries the override first, with the request user taken from the logging context; asset creation is decided by the override alone (see section 10).

**New method**: `resolve_user_override_model(requested_model, user_id, project_name, is_global=False) -> str | None` — applies tier 1 (the user override) and returns `None` when no override applies, so the caller continues with the existing project-level logic (`ProjectModelPolicy`, `resolve_model_for_execution`), which is unchanged. It is called next to the project model resolution in assistant execution and in the workflow nodes. `RouterChatModel` is not changed: like the project whitelist, the override is not applied inside router candidate selection.

---

## 4. API Design

### 4.1 Single-User Override Endpoints

**PUT /projects/{projectName}/user-model-overrides/{userId}** — Set (replace) the override of one user; creates it when there is none. An idempotent full replace, as the endpoint guide describes for PUT. The routes live in `routers/projects/user_model_overrides.py` with the other project routes and, like them, answer 400 when user management is not enabled.
- **Auth**: `_authorize_project_access` (WRITE access to the project); 404 without it
- **Request body**: `{ allowed_models: list[str] (1 to 100 ids, repeated ids dropped), default_model?: str }`
- **Response**: 200 OK, UserModelOverride JSON
- **Errors**: 400 Bad Request (user not in project, empty allowed_models, default_model not in allowed_models, user management not enabled), 404 Not Found (no access to the project, project not found, user not found), 501 (feature off)

**GET /projects/{project_name}/user-model-overrides** — List all overrides for a project.
- **Auth**: `_authorize_project_access` (WRITE access to the project); 404 without it
- **Query params**: skip=0, limit=100
- **Response**: 200 OK, list[UserModelOverride] JSON
- **Errors**: 404 Not Found (no access to the project, or project not found)

**GET /projects/{project_name}/user-model-overrides/{user_id}** — Fetch override for a specific user.
- **Auth**: `_authorize_project_access` (WRITE access to the project); 404 without it
- **Response**: 200 OK, UserModelOverride JSON
- **Errors**: 404 Not Found (no access to the project, or project or override not found)

**DELETE /projects/{project_name}/user-model-overrides/{user_id}** — Physically delete the override row (no soft-disable, no `override_enabled` column). Once deleted the user has no override and resolution falls through to the project whitelist and default. Returns 204, or 404 when the user has no override.
- **Auth**: `_authorize_project_access` (WRITE access to the project); 404 without it
- **Response**: 204 No Content
- **Errors**: 404 Not Found (no access to the project, or no override)

### 4.2 Batch Override Endpoint

**POST /projects/{project_name}/user-model-overrides/batch** — Apply/revoke override for multiple users.
- **Auth**: `_authorize_project_access` (WRITE access to the project); 404 without it
- **Request body**:
  ```json
  {
    "operation": "grant" | "revoke",
    "user_ids": ["<uuid>", "<uuid>", ...],
    "allowed_models": ["model-a", "model-b"]  // required for grant, ignored for revoke
  }
  ```
- **Response**: 200 OK, list[UserModelOverride] JSON (created/updated/deleted records)
- **Errors**: 
  - 400 Bad Request: empty user_ids, empty allowed_models for grant, first user fails validation → entire batch rolled back
  - 404 Not Found: no access to the project
  - 404 Not Found: project not found
- **Atomicity**: All users succeed or entire batch fails (no partial success). Transaction scope includes validation + all CRUD operations.

### 4.3 Response Models

**UserModelOverrideResponse** (JSON):
```json
{
  "id": "<uuid>",
  "user_id": "<user id>",
  "project_name": "project-123",
  "allowed_models": ["gpt-4", "claude-opus"],
  "default_model": null,
  "date": "2026-09-21T10:00:00Z",
  "update_date": "2026-09-21T10:00:00Z"
}
```

**Error Response** (following existing ExtendedHTTPException pattern — now only raised for the default_model check, since the project-whitelist subset check was dropped):
```json
{
  "code": "MODEL_NOT_WHITELISTED",
  "message": "default_model must be one of the override's allowed_models",
  "details": {
    "default_model": "gpt-4-turbo",
    "allowed_models": ["gpt-4", "claude-opus"]
  }
}
```

---

## 5. Validation & Error Handling

### 5.1 Set-Time Validation (Override Creation)

**Checks** (in order, fail-fast):
1. User exists and is member of project (via UserProject)
2. Project exists
3. allowed_models is non-empty and non-blank
4. default_model, if provided, is one of allowed_models
5. No model the stored override does not already list is deprecated in platform model management (`assert_model_selectable`, the rule every other save path uses). A deprecated model the override already lists is kept, so an admin can still edit the override after it was deprecated; for a batch grant a model counts as kept only when every target user's override lists it. Resolution is unchanged: a model already in an override keeps working after it is deprecated (see 3.2.1).

**Note**: allowed_models is **not** checked against project.allowed_models. The former subset check (allowed_models ⊆ project.allowed_models) was dropped — admins may grant users access to any platform model regardless of the project's own whitelist.

**Exceptions**:
- `NotFoundException("User not found" | "Project not found")` — 404
- `ModelNotWhitelistedException("default_model must be one of the override's allowed_models", details=...)` — 400
- `ValueError("allowed_models must be a non-empty list of non-blank model ids")` — 400
- `ValueError("Deprecated model ids cannot be added to an override: [...]")` — 400

### 5.2 Invocation-Time Validation

**At model resolution** (assistant execution and workflow nodes, next to the project model resolution):
1. Check user override if exists → use if model in override list; otherwise use the override's default model, else the project default, else the platform default. Stop here: the project whitelist is not consulted for a user with an override.
2. No override: check project whitelist → use if model in project list, else the project default
3. Use default model (platform default for all other cases) → no exception (safe fallback)

**No re-validation of the override against the project whitelist.** The override is the user's whole model set, so narrowing or widening the project whitelist afterwards does not change what this user may use; nothing is re-checked per call. A model that was valid when the override was saved can later leave the catalog; resolution does not re-check that, and the fallback order (the override's default, then the project default, then the platform default) applies when the requested model is not in the override.

### 5.3 Batch Operation Error Handling

**Transaction scope**: Entire batch wrapped in SQLAlchemy session transaction.

**On validation failure of any user**:
- Catch exception during first failed validation
- Roll back entire transaction (no partial updates)
- Return 400 with failed user details and reason (`detail` carries the body below; `failed_user_id` is null when the failure is not user-specific, e.g. invalid models)

**Example response**:
```json
{
  "code": "BATCH_VALIDATION_FAILED",
  "message": "Batch operation failed; no users updated",
  "details": {
    "failed_user_id": "<uuid>",
    "reason": "User not a member of this project",
    "operation": "grant",
    "user_ids": ["<uuid>", "<uuid>", "<uuid>"]
  }
}
```

---

## 6. Testing

### 6.1 Unit Tests (test_user_model_override_service.py)

- **set_override**: success, user not found (NotFoundException, 404), user not in project (ValueError, 400), default_model not in allowed_models (ModelNotWhitelistedException), empty allowed_models (400), models outside project whitelist succeed (no longer rejected), a newly added deprecated model or default rejected (ValueError, 400), a deprecated model the stored override already lists kept (also under its other catalog name)
- **get_override**: override exists, override not found (None)
- **delete_override**: success (writes an activity event), not found (returns None; the route answers 404)
- **get_overrides_by_project**: list with pagination, empty project
- **batch_set_overrides (grant)**: success, first user fails validation (rollback all), partial conflict (rollback all), a deprecated model every selected user already lists kept, a deprecated model new to any selected user rejects the whole batch
- **batch_set_overrides (revoke)**: success, user override not found (no-op), rollback on first failure
- **resolve_user_override_model**: the precedence chain:
  - Override exists, model in the override → the requested model
  - Override exists, model not in the override → the override's default, else the project default, else the platform default
  - No override row, feature off, global asset or missing ids → `None` (the caller continues with the project logic)
  - Deny-all project → `ModelNotWhitelistedException`
  - A substitution is logged and counted; an allowed or unset model is not

### 6.2 API Endpoint Tests (test_user_model_override_endpoints.py)

- **PUT single override**: success, admin-only gating (404 without WRITE access to the project), validation errors (400), project not found (404)
- **GET list**: admin-only, pagination works, empty list for new project
- **GET single**: admin-only, found, not found (404)
- **DELETE**: admin-only, success (204), not found (404)
- **POST batch**: success, rollback on first failure, empty user_ids (400), empty allowed_models for grant (400), admin-only (404 without WRITE access)

### 6.3 Integration Tests (extend test_model_availability_service.py)

- End-to-end: create override, resolve the model for an assistant and a workflow node → verify the precedence chain applies
- A change to the project whitelist after the override was created does not affect that user (the override is their whole set)

### 6.4 Model Listing Tests

- **test_model_narrowing_integration.py** (`TestUserModelOverrideNarrowing`): override replaces the project whitelist; matching by `deployment_name`; override applies even when `projectModelOverride` is off; no override → project whitelist; `userModelOverride` off → lookup skipped and project whitelist applies; no project → lookup skipped. Existing project-narrowing tests enable only `projectModelOverride` so they never reach the override lookup.
- **test_llm_models.py** (`TestDefaultModelFromOverrides`): `default` flag follows the override's `default_model`, falls back to the project's `default_model` when there is no override or the override has no default.

---

## 7. Backward Compatibility & Deployment

### 7.1 Backward Compatibility

- **Existing projects** (no user overrides): Continue using project-level whitelist only; tier 1 (user override) skipped.
- **Legacy invocation code** (calls `resolve_model_for_execution` without user_id): Existing method unchanged; callers apply `resolve_user_override_model` ahead of the project-level logic when user context is available.

### 7.2 Feature Flags

- `features:userModelOverride` (customer-config component in `config/customer/customer-config.yaml`, **disabled by default**, checked via `customer_config.is_feature_enabled("userModelOverride")`, same mechanism as `projectModelOverride`) — gates new endpoints and service logic. If disabled, the endpoints that create or change overrides (`POST` single and `POST /batch`) return 501 Not Implemented, while reading and deleting stay available so stored rows can be seen and cleared; user overrides are ignored at resolve time (tier 1 skipped) and when listing available models (the project whitelist alone applies), and the stored rows take effect again when the flag is switched back on. The project access check on `GET /v1/llm_models` and `GET /v1/default_models/{category_id}` also applies only when the flag is on. The flag is independent of `projectModelOverride`.
- Allows rapid disable if production issues discovered.

### 7.3 Alembic Migration

**New migration file**: `src/external/alembic/versions/<timestamp>_add_user_model_overrides_table.py`

**Changes**:
- Create `user_model_overrides` table (DDL from UserModelOverride)
- Add unique constraint on (user_id, project_name)
- Add check constraint that allowed_models is not empty

---

## 8. Non-Goals

- **A separate platform disablement store** — no `ApplicationEnrichment` table or migration, no `PLATFORM_DISABLED_MODELS` setting, no set-time validation against such a store. Disabled models are still skipped to the next tier at resolution, using the existing enabled-model catalog (see 3.2).
- **Deprecated defaults at fallback** — How a deprecated project default or platform default is handled during fallback follows the platform and project rules of EPMCDME-15077 and is not changed by this ticket. Overrides only stop new deprecated models from being saved (see 3.2.1); a deprecated model already in an override keeps working at invocation.
- **Router model selection** — `RouterChatModel` and router candidate choice are not changed by this ticket. The project whitelist is not applied there either, so the user override is not applied there.
- **Global (marketplace) assets** — Removed from the ticket EPMCDME-15082 (the former global-asset acceptance criteria, including merging a user's overrides across projects). Overrides apply only to project-scoped assets; for a global asset the override is skipped and resolution matches the behaviour without overrides.
- **User notification before a fallback model is used** — Moved out of this ticket to the follow-up story EPMCDME-16924. Fallback to another model stays silent here.
- **Role/group-based overrides** — This iteration applies overrides to explicit user_ids only. Role/group-based inheritance is a future feature.
- **Cached model resolution** — Each resolution reads the user override from the database; any future caching layer is out of scope.
- **Audit trail with immutable event log** — Override changes logged via activity events (existing pattern), but no separate immutable audit table is created. Regulatory audit requirements are out of scope.
- **UI for override management** — This spec covers API and backend only. Admin UI endpoints are out of scope.
- **Migration of existing user groups to overrides** — No bulk import tool or migration script for legacy user group data.
- **Per-invocation override metadata tracking** — Invocations do not record which tier of the precedence chain resolved the model; only the resolved model is persisted.

---

## 9. Open Risks

- **Overrides and memberships** — Resolved: a composite foreign key to `user_projects` with ON DELETE/UPDATE CASCADE removes an override with its membership and carries it through a project rename (see 3.1). No hand-written cleanup remains.
- **Invocation performance** — Each model resolution read the override through a new synchronous session. Mitigation implemented: the override is read once per request (a middleware opens a request scope; threads that inherit the request's context share it; the admin write paths clear it). A plain thread started inside a request does not inherit the scope and reads the database each time. The real cost under load has not been measured; check override SELECTs per request with the flag on.
- **Concurrent override updates** — Batch operations are atomic per transaction, but concurrent individual updates can race on unique constraint. Mitigation: application-layer retry logic or accept constraint violation (rare in practice for admin operations).
- **Override models are independent of the project whitelist** — Since the subset check was dropped, an override can grant a user models the project itself does not allow (and project whitelist narrowing after override creation is likewise never re-validated). Accepted: tier 1 of the invocation chain checks only `override.allowed_models`, and once an override row exists the project whitelist is not consulted at all; it only matters when the user has no override. Admin must clean up overrides manually if this is unintended.

---

## 10. Decisions Made

- **UserModelOverride placement**: its own module `src/codemie/rest_api/models/user_model_override.py`, beside `UserProject` (not `core/models.py`, which holds project and core entities).
- **Platform disablement**: The blocking criterion was removed from the ticket's acceptance criteria; the Context's skip-a-tier rule is implemented with the enabled-model catalog (see 3.2). No separate disablement store.
- **Fallback default**: follows the user's scope — override `default_model`, else project default (project-scoped asset), else platform default (all other cases); no longer "regardless of user-level override".
- **Global assets and fallback notice**: Descoped by the updated ticket. Global (marketplace) assets are not covered by overrides; the notice before a fallback moves to EPMCDME-16924.
- **Bulk override operations**: POST /batch endpoint with explicit user_ids and atomic transaction. Simpler than role/group-based; future iteration for group support.
- **Override validation timing**: Set-time only (early fail-fast), and no longer checks allowed_models against the project whitelist — only non-emptiness, known and not disabled models, no newly added deprecated model, project existence, and default_model ⊆ allowed_models. Invocation-time re-check not implemented; graceful degradation via the 3-tier chain if project whitelist changes.
- **Model listing honours user overrides**: the chat model list is filtered by the user override first (replacing the project whitelist), gated by `userModelOverride`; otherwise users with an override would see every model the project allows, even though invocation is restricted by the override.
- **Batch error semantics**: All-or-nothing (400 + rollback) rather than 207 partial success. Clearer semantics for admin UX.
- **An override replaces the project set (not a delta)**: once a row exists it is the user's whole model set. Consequences accepted: a user with a row stops following later changes to the project's whitelist; to restrict a user the admin lists every model the user keeps (the list cannot be empty); to expand, the admin lists the project's models plus the extra ones. A delta model (models added and removed relative to the project) was considered and rejected as a much larger change; if it is wanted later, the single place to add it is the function that computes the effective model set.
- **No new deprecated models in an override**: per the ticket's Context, saving an override rejects a deprecated model it does not already list, matching the project default, assistant, workflow and conversation saves (EPMCDME-15077). Models already listed are kept, and a bulk grant keeps one only when every selected user already lists it. Invocation and the project default's handling of deprecated models are unchanged by this ticket (see 3.2.1).
- **Asset creation honours the override alone**: when the user has an override for the project, `validate_model_for_asset_creation` (called from assistant creation only; updates and workflows are not validated) accepts a model the override lists and rejects one it omits, even if the project whitelist allows it. Without an override the project whitelist applies as before.
- **Fully synchronous**: repository, service and handlers use the synchronous session like the project override, so there is one data-access path next to the resolver and no extra async session dependency in `clients/postgres.py`.
- **Single write is PUT**: `PUT /projects/{projectName}/user-model-overrides/{userId}` replaces the POST upsert, matching the endpoint guide (POST creates new, PUT is an idempotent replace). The UI MR must call PUT; `POST /batch` is unchanged.
- **Membership foreign key instead of cleanup code**: see 3.1 and section 9.
- **One rule for 'does the override allow this model'**: `model_in_override` in `model_availability_service.py`, used by resolution, asset creation, the OCR model and the model and router lists. The model list still falls back to the project set when none of the override's models is in the catalog (so a stale override never leaves the user with nothing to pick), while resolution goes to the project default.
- **Substitutions are logged and counted**: a user override that replaces the requested model logs `model_availability_event=fallback_applied ... context='user_override'` and increments `codemie_model_availability_fallbacks_total` with `fallback_source=user_override`. The user-facing notice stays with EPMCDME-16924.
- **Project default needs the project flag**: the fallback is the override's default, then the project default, then the platform default. The project default (`Application.default_model`) belongs to `projectModelOverride`; with that flag off the platform ignores it everywhere, so for such a project the fallback after the override's default is the platform default. Reading the stored field only for users with an override would make the two flags inconsistent.
- **Separate user-tier resolver**: the user tier stays a resolver of its own (`ModelAvailabilityService.resolve_user_override_model`) and `ProjectModelPolicy` is not changed, so the project tier stays as on `main` and each tier keeps its own flag. Folding both into one policy with a required `user` would touch about 15 files and is not part of this MR.
- **Flag off keeps reads and deletes**: with `userModelOverride` off only the writes answer 501; see 7.2.
- **Project access check is gated by the flag**: the 403 (not a member) and 404 (no such project) on the model list endpoints apply only with `userModelOverride` on, so flag-off behaviour matches the platform before the feature. Hardening these endpoints for everyone is a separate change.
- **User-project binding**: a composite foreign key to `user_projects` with cascade (see 3.1) ties the row to the membership; the service also checks membership at set time, for a clear 400 or 404 instead of a constraint error.

---

## Summary

This spec defines a secure, layered user-level model override system. The 3-tier precedence chain (user override → project whitelist → default) is validated at set-time only for non-emptiness, known and not disabled models, no newly added deprecated model, project existence, and default_model membership — override models are not required to be a subset of the project whitelist — and, once an override row exists, it is the user's whole model set, so later changes to the project configuration do not affect that user. The implementation spans database models (UserModelOverride), service orchestration (UserModelOverrideService), repository persistence, and API endpoints (single and batch CRUD). Backward compatibility is maintained for legacy projects and invocation patterns. Key risks (per-invocation query latency, overrides that grant models outside the project set) are documented with mitigations.
