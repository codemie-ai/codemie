# Technical Analysis — EPMCDME-13666: Move/Copy Project Integrations

**Task**: Backend endpoint to move or copy integrations from Project X to Project Y.
**Feature area**: integrations, project scope, datasources, assistants, permissions.
**Method**: four parallel Explore threads (model/persistence, API/permissions, consumers, tests) over the CodeMie backend at `C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie`. The `tech-analyst` agent could not run (no Read/Glob/Grep tools available to it in this host), so research was dispatched directly.

---

## 0. Terminology — the single most important finding

**There is no `Integration` entity in this codebase.** What the UI and the ticket call an "integration" is a row in the `settings` table, modelled as `Settings` in `src/codemie/rest_api/models/settings.py:243-476`. There is no `integrations.py` router and no `IntegrationRepository`. The domain lives under `settings` / "credentials".

Two scopes exist, discriminated by `Settings.setting_type`:

| `SettingType` | Router | Ownership | Ticket relevance |
|---|---|---|---|
| `PROJECT` | `src/codemie/rest_api/routers/project_settings.py` | project-admin managed | **this is the ticket's target** |
| `USER` | `src/codemie/rest_api/routers/user_settings.py` | owner-only | out of scope (but shares the table and the alias-uniqueness key) |

---

## 1. Codebase Findings

### 1.1 Persistence model

`src/codemie/rest_api/models/settings.py`

- `SettingsBase(CommonBaseModel)` — L243-267, field carrier.
- `Settings(BaseModelWithSQLSupport, SettingsBase, table=True)` — L364-365, `__tablename__ = "settings"`.
- Backend is **SQLModel over PostgreSQL** (an Elasticsearch base class exists in the repo but `Settings` uses the SQL base).

```python
class SettingsBase(CommonBaseModel):                       # L243
    user_id: Optional[str] = SQLField(default=None, index=True)
    created_by: Optional[CreatedByUser] = SQLField(default=None, sa_column=Column(PydanticType(CreatedByUser)))
    project_name: str = SQLField(index=True)               # L246  <-- the "project" field
    alias: Optional[str] = None
    default: Optional[bool] = False
    credential_type: CredentialTypes = SQLField(index=True)
    credential_values: List[CredentialValues] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(CredentialValues)))
    setting_hash: Optional[str] = None
    setting_type: Optional[SettingType] = SettingType.USER
    is_global: Optional[bool] = SQLField(default=False)
```

Key facts:

- **`project_name` is a plain string, not a FK.** Everything joins on the project *name*. There is no referential integrity to `applications`.
- Primary key is a **UUID string `id`** (`str(uuid4())` assigned in `BaseModelWithSQLSupport.save`, `src/codemie/rest_api/models/base.py:501-503`).
- `credential_values` is `List[CredentialValues]` (`{key, value}`) stored as JSONB via `PydanticListType` (`base.py:295-367`).

### 1.2 Uniqueness

- **No DB unique constraint exists.** All indexes on `settings` are non-unique (`src/external/alembic/versions/074d06e75b25_create_settings.py:104-124`): `ix_settings_alias` (GIN trgm), `ix_settings_project_name` (GIN trgm), `ix_settings_credential_type`, `ix_settings_date`, `ix_settings_user_id`.
- Uniqueness is enforced **only in application code**, `Settings.check_alias_unique` (`settings.py:296-315`):

```python
if setting_type == SettingType.PROJECT:
    settings = cls.get_by_fields({PROJECT_NAME_TERM: project_name, ALIAS_TERM: alias})
else:
    settings = cls.get_by_fields({PROJECT_NAME_TERM: project_name, USER_ID_TERM: user_id, ALIAS_TERM: alias})
if settings and not setting_id or settings and setting_id and setting_id != settings.id:
    raise ValueError(f"There are more than one settings with the alias named {alias}")
```

- Logical key: **`(project_name, alias)`** for PROJECT settings; `(project_name, user_id, alias)` for USER settings. **`credential_type` is NOT part of the key.**
- The check is read-then-write and race-prone; it runs only on create/update of the setting itself (`settings.py:378-380`, `451-457`).
- Extra rule: `check_webhook_id_unique` (`settings.py:317-327`) — the `webhook_id` credential value must be **globally** unique across all settings, project-independent.

### 1.3 Secrets / encryption — copy is safe, but only from the raw row

`src/codemie/service/settings/base_settings.py:43-77`

```python
class BaseSettingsService:
    MASKED_VALUE: str = "*" * 10
    encryption_service = EncryptionFactory().get_current_encryption_service()

    @classmethod
    def _encrypt_fields(cls, credential_values, force_all=False):
        for cred in credential_values:
            if force_all or cred.key in cls.LIST_OF_SENSITIVE_FIELDS:
                cred.value = cls.encryption_service.encrypt(cred.value)
```

- Sensitive keys listed at `src/codemie/service/settings/settings.py:127-154` (`token`, `password`, `client_secret`, `private_key`, `access_token`, `refresh_token`, `api_key`, `env_vars`, …). `CredentialTypes.ENVIRONMENT_VARS` (MCP) encrypts **everything** (`force_all=True`).
- Backend selected by global config `ENCRYPTION_TYPE` (`src/codemie/service/encryption/encryption_factory.py:27-69`): `plain`, `base64`, `gcp`, `aws`, `azure`, `vault`.
- **The encryption key is NOT derived from project, user, or setting id.** No per-project AAD/encryption context.

**Consequence**: ciphertext is project-independent, so `credential_values` can be duplicated **verbatim** into a new row. But the copy must read the **raw DB row** (`Settings.get_by_id`), never the service read paths:

- `hide_sensitive_fields` (`base_settings.py:88-97`) masks values to `"**********"` in `get_settings` / `get_all_settings` / `SettingsIndexService.run`.
- `retrieve_setting` (`settings.py:1584-1613`) returns **decrypted** values.
- Round-tripping masked values through `update_settings` is treated as "no change" (`settings.py:566-580`); blank sensitive values are treated as **deletions** (`_filter_empty_sensitive_fields`, `base_settings.py:79-86`).
- Passing an already-encrypted value through `create_setting` would **double-encrypt**.

### 1.4 Query / persistence API (no repository class)

`Settings` classmethods (`models/settings.py`): `get_all` L367, `get_by_user_id` L382, **`get_by_project_names(project_names, credential_type=None)` L391-399** (`where(project_name.in_(...))` + `setting_type == PROJECT`), `find_by_resource_id` L401, `get_all_project_litellm_settings` L420, `delete_setting` L435, **`get_by_alias(alias, project_name, user_id=None)` L442-475**. Inherited from `BaseModelWithSQLSupport` (`models/base.py:370-561`): `get_by_id`, `find_by_id`, `get_by_ids`, `get_by_fields`, `get_all_by_fields`, `save()`, `update()` (`session.merge`, raises `StaleDataError`), `delete()`.

`SettingsService` (`src/codemie/service/settings/settings.py`) write paths:

- `create_setting(user_id, request, settings_type=None, user=None)` L371-441 — `check_alias_unique` → `check_webhook_unique` → GoogleOAuth population → `_prepare_cred_values` → `_filter_empty_sensitive_fields` → PLUGIN `setting_hash` → `_encrypt_fields` → **`ensure_application_exists(project_name)`** → build `created_by` → `Settings(...).save()` → LiteLLM cache invalidation.
- `update_settings(...)` L444-529 — key-diff merge with `attributes.flag_modified`, rewrites `alias`, `is_global`, `setting_type`, `update_date`.
- `delete_setting(...)` L531-549 — Google OAuth revoke, delete, cache clear.
- `upsert_project_setting(...)` L330-368; `create_project_credentials_if_missing(...)` L305-328.

**Trap**: `create_setting` calls `ensure_application_exists` (`src/codemie/rest_api/utils/default_applications.py:22`) which **auto-creates a missing project**. The service therefore does *not* validate project existence — a new cross-project endpoint must validate explicitly, or a typo'd target project name silently creates a project.

### 1.5 API surface today

`project_settings.py` (`prefix="/v1"`, tag `Project Settings`), registered at `src/codemie/rest_api/main.py:824-825`:

| Line | Method | Path | Request | Response | Auth |
|---|---|---|---|---|---|
| 44 | GET | `/v1/settings/project/users` | – | `list[CreatedByUser]` | `authenticate` |
| 59 | GET | `/v1/settings/project` | `filters`, `page`, `per_page` | `{data, pagination}` | `authenticate` |
| 80 | POST | `/v1/settings/project` | `SettingRequest` | `BaseResponse` | `authenticate` + `_check_permission(user, request.project_name)` |
| 125 | PUT | `/v1/settings/project/{setting_id}` | `SettingRequest` | `BaseResponse` | `authenticate` + `Ability(user).can(Action.WRITE, setting_ability)` |
| 177 | DELETE | `/v1/settings/project/{setting_id}` | – | `BaseResponse` | `authenticate` + `_check_permission(user, setting.project_name)` |

Request model `SettingRequest` — `models/settings.py:221-227`.

**Note**: DELETE in `project_settings.py:192` calls `Settings.delete_setting` **directly**, bypassing `SettingsService.delete_setting` — so project-scoped deletes skip the Google-OAuth revoke and the LiteLLM cache clear. A move implemented as delete+create must not inherit that bug.

### 1.6 Permission model

`src/codemie/core/ability.py`: `Action` L30 (`READ`/`WRITE`/`DELETE`), `Role` L38, `Ability` L76 with `PERMISSIONS` matrix L94-160:

```python
"ProjectSetting": {
    Action.READ:   [Role.SHARED_WITH, Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
    Action.WRITE:  [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
    Action.DELETE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
},
```

`ProjectSetting(Owned)` — `models/settings.py:347-358`: `is_managed_by` = `project_name in user.admin_project_names`.

**Canonical "can manage integrations in project X"** — `project_settings.py:205-216`:

```python
def _check_permission(user: User, project_name: str):
    if user.is_admin_or_maintainer:
        return True
    if not user.is_application_admin(project_name):
        raise ExtendedHTTPException(code=status.HTTP_403_FORBIDDEN, message="Access denied", ...)
```

`User` (`src/codemie/rest_api/security/user.py:28-118`): `project_names`, `admin_project_names`, `is_admin`, `is_maintainer`, `is_admin_or_maintainer` L87, `is_application_admin(app_name)` L94, `has_access_to_application` L111.

Auth dependencies (`src/codemie/rest_api/security/authentication.py`): `authenticate` L91, `admin_access_only` L158, `admin_or_maintainer_access_only` L173, `project_access_check(user, project_name)` L299 (membership level, 403).

Newest project-authorization style — `src/codemie/rest_api/routers/projects.py:1014-1038` `_authorize_project_access`: loads the `Application`, 404s (not 403s) when missing or unauthorized to avoid leaking project existence, then `Ability(user).can(Action.WRITE, project)`.

### 1.7 Project model

`Application` — `src/codemie/core/models.py:379-443`, `__tablename__ = "applications"`, **`id = name`**, soft-deleted via `deleted_at`. Repository `src/codemie/repository/application_repository.py`: `get_by_name` L138, `get_by_name_case_insensitive` L151, **`exists_by_name` L156**, `get_project_entity_counts_bulk` L573 (already computes `integrations_count` from `Settings`). Service `ProjectService` (`src/codemie/service/project/project_service.py:45`), errors catalogue L65-104 (`PROJECT_NOT_FOUND`), `_get_project_for_update` L225 (404 then 403).

### 1.8 Existing cross-project / bulk precedents to mirror

There is **no move/clone/copy/transfer endpoint for any entity** in the codebase. Closest analogues:

1. `POST /v1/projects/{projectName}/assignments` — `projects.py:1145-1175`; `BulkAssignmentResponse{message, project_name, total, results[]}`; `Depends(_authorize_project_access)`; `with get_session()` + `session.commit()`; audit `actor` + `action`. Service `project_assignment_service.bulk_assign_users_to_project` L423 — **validate-all-then-apply (atomic)**.
2. `POST /v1/projects/{projectName}/import-users` + `/import-users/validate` — `projects.py:1178-1252`: the **dry-run-validate + apply pair**, a good model for a "preview move" endpoint.
3. `POST /v1/skills/{skill_id}/assistants/bulk-attach` — `skill.py:608-631`; partial-success shape `{message, success_count, total_requested, failures}`.
4. `POST /v1/admin/llm/retire/bulk` — `admin.py:346`; request models with `min_length=1, max_length=100`.
5. Target-project duplicate-name check when moving an entity: `SkillService._check_duplicate_name(skill_id, target_name, target_project, author_id)` — `skill_service.py:556`.
6. Cross-project *read* precedent already in integrations: `GET /v1/settings/user/available?scope=marketplace` (`user_settings.py:92-118`).

### 1.9 Error handling conventions

`src/codemie/core/exceptions.py`: `ValidationException(ValueError)` L20 → 400; `NotFoundException` L24; **`ExtendedHTTPException(code, message, details, help)` L28** — the dominant convention, raised directly by services and routers.

Handlers in `src/codemie/rest_api/main.py`: `ValidationException` L961 → 400; `ExtendedHTTPException` L969 → `exc.code` with body `{"error": {"message", "details", "help"}}`; `RequestValidationError` L1064 → 422.

Settings-router convention (`project_settings.py:115-122, 164-173, 193-200`): wrap service calls in `try/except`, re-raise `ExtendedHTTPException` untouched, convert everything else to **422** with a `help` string, `KeyError` → **404** "Credential not found".

Router helpers (`routers/utils.py`): `raise_access_denied` L53 → **401** (not 403), `raise_forbidden` L63 → 403, `raise_not_found` L82 → 404.

---

## 2. Integration Consumers — what breaks on a move

### 2.1 Reference styles in use

| Consumer | Location | Reference style |
|---|---|---|
| Datasources | `models/index.py:294` `IndexInfo.setting_id`; `core/models.py:137` `BaseRepository.setting_id` | **id** |
| Datasources (Bedrock KB) | `models/index.py:242` `bedrock_aws_settings_id`, reverse lookup L880 | id |
| Assistants — tools/toolkits | `models/assistant.py:143,152` `settings: Optional[SettingsBase]` (**embedded snapshot**), reduced to id at L155-156 | **embedded copy + id** |
| Assistants — MCP servers | `assistant.py:213-215` `settings`, **`integration_alias`**, `mcp_connect_auth_token` | alias first, then id (`toolkit_service.py:1464-1508`) |
| Assistants — Bedrock | `assistant.py:279,290`; reverse lookups L1154, L1165 | id |
| Per-user assistant mappings | `models/usage/assistant_user_mapping.py:34` `integration_id` | id |
| Workflows — assistant tools | `core/workflow_models/workflow_models.py:46-48` `integration_alias` | **alias** |
| Workflows — tool nodes | `workflow_models.py:98-102`; schema `workflows/execution_config_schema.yaml:98,122` | **alias** |
| Workflow alias resolution | `service/tools/tool_service.py:182-207` | **(user_id + project + alias)** |
| Bedrock flow node | `workflows/nodes/bedrock_flow_node.py:94-104` `Settings.get_by_alias(alias, project_name)` | **(project + alias)** |
| LiteLLM project keys | `settings.py:868-949`; alias template `enterprise/litellm/credentials.py:183` `f"codemie:project:{project_name}:category:..."` | **(project + alias); alias embeds the project name** |
| Project budget flags | `settings.py:886-918` `(project + alias ENFORCE_MEMBER_SPEND_LIMITS)` | (project + alias) |
| A2A / Plugins / Toolkits / Bedrock / Google OAuth | `routers/a2a.py:136-153`, `plugin_tools_info_service.py:60`, `toolkit_settings_service.py:140-218`, `aws_bedrock/*`, `google_oauth/token_manager.py` | id (mostly) |
| Scheduler / Webhook (stored *as* integrations) | `scheduler_settings_service.py:157-355`; `triggers/bindings/cron.py:701-713` | `(user_id + project_name + resource_id)` inside `credential_values` |
| Ad-hoc tool execution | `service/tools/tool_execution_service.py:113,248` `tool_creds["integration_alias"]` | alias |

### 2.2 The resolution chain

`SettingsService.retrieve_setting` (`settings.py:1584-1600`) delegates to an 8-stage chain — `build_settings_handlers()`, `settings_handler.py:191-204`:

```
1. AssistantUserMappingSettingsHandler   (per-user override, by integration_id)
2. BySettingIDSettingsHandler            (by id; checks credential_type only, NOT project_name)
3. GlobalAssistantSettingsHandler
4. AssistantSettingsHandler
5. DefaultSettingsHandler                \
6. UserSettingsHandler                    |  all project_name-scoped fallbacks —
7. GlobalUserSettingsHandler              |  these silently change behaviour after a move
8. ProjectSettingsHandler                /
```

Because handler 2 ignores `project_name`, **id-based references keep resolving after a move**. The breakage is concentrated in alias-based references, the explicit access check, and listings.

### 2.3 The hard access check — biggest breakage risk

`src/codemie/service/settings/settings_util.py:25-63`:

```python
if setting.setting_type == SettingType.PROJECT:
    if marketplace:
        return True
    return (bool(assistant_project)
            and setting.project_name == assistant_project
            and user.has_access_to_application(assistant_project))
return bool(setting.user_id) and setting.user_id == user.id
```

Callers:
- `routers/assistant_mapping.py:49-61` `_validate_mapping_access` — save-time; re-saving a mapping to a moved integration → **HTTP 403 "Integration is not accessible"**.
- `service/mcp/toolkit_service.py:1902-1925` `_current_user_can_use_integration` — runtime; failure is **silent**: `toolkit_service.py:1963-1969` logs *"Skipping MCP integration override..."* and falls back to base config.

### 2.4 Hard-failure paths after a move

`service/tools/tool_service.py:182-207`:

```python
setting = SettingsService.retrieve_setting({
    SearchFields.USER_ID: lookup_user_id,
    SearchFields.PROJECT_NAME: project_name,
    SearchFields.ALIAS: integration_alias,
})
if not setting:
    raise ValueError(SETTING_NOT_FOUND_ERROR.format(alias=integration_alias))
```

Every workflow with an `integration_alias` **hard-fails with a raised `ValueError`** once the integration's `project_name` no longer matches the workflow's project. Same shape at `workflows/nodes/bedrock_flow_node.py:98-104`.

### 2.5 Referential integrity on DELETE: essentially none

Delete flows: `project_settings.py:183-202`, `user_settings.py:214-239`, `SettingsService.delete_setting` (`settings.py:532-549`), `Settings.delete_setting` (`models/settings.py:435-440`). **No FK, no cascade, no pre-delete validation, no blocking.** The only cascade is AWS Bedrock — `BedrockOrchestratorService.delete_all_entities(setting_id)` (`aws_bedrock/bedrock_orchestration_service.py:29-34`).

Dangling-reference outcomes today:
- datasources → fall through to project-scoped fallback handlers, may silently pick a **different** integration, or get `Credentials(url="", token="")` (`settings.py:1181-1183`)
- assistants → stale embedded blob, id miss, project/user fallback
- per-user mappings → silently skipped
- workflows → hard `ValueError`

Reverse-lookup helpers that exist (currently Bedrock-only): `Settings.find_by_resource_id` (`settings.py:401-418`), `Assistant.get_by_bedrock_aws_settings_id` (`assistant.py:1154`), `Assistant.get_by_bedrock_runtime_aws_settings_id` (L1165), `IndexInfo.get_by_bedrock_aws_settings_id` (`index.py:880`), `WorkflowConfig.get_by_bedrock_aws_settings_id` (`workflow_config.py:309`).

**There is no `IndexInfo.get_by_setting_id` and no assistant-by-`toolkits[].settings.id` query at all.** An impact report would need new queries.

**Precedent for the same bug class**: project **rename** (`project_service.py:172-253`) updates `applications` and `user_projects` (`user_project_repository.py:227`) but **never touches `settings.project_name`** — dangling project references already exist in production behaviour.

### 2.6 Caches needing invalidation

1. **LiteLLM user credentials TTL cache** — `enterprise/litellm/credentials.py:36-55`; `_build_cache_key(user_id, project_name, llm_model, integration_id)` — **the key contains `project_name`**. Existing invalidation (`settings.py:551-563`) fires only for `LITE_LLM` + `SettingType.USER` and explicitly refuses a full clear. A project-move path needs `clear_litellm_user_credentials_cache(None)` for PROJECT settings.
2. **`@lru_cache(maxsize=128)` `_get_integration_api_key(integration_id)`** — `enterprise/litellm/proxy_router.py:809-843`. **Never invalidated anywhere in the repo**, unbounded in time.
3. `@lru_cache` in `enterprise/litellm/dependencies.py:486,499,507` and `core/utils.py:231`.
4. **Embedded snapshots** in `Assistant.toolkits[].settings`, `.tools[].settings`, `MCPServerDetails.settings` — behave like a per-record cache, never refreshed.
5. **Redis** (`clients/redis.py`) caches MCP auth and webhook rate limits only — **no settings caching**, no invalidation needed.

---

## 3. Per-credential-type special handling (copy hazards)

`CredentialTypes` — `src/codemie_tools/base/models.py:63-106` (a Postgres ENUM `credentialtypes`; adding a value requires an alembic migration).

| Type | Special handling | Copy/move hazard |
|---|---|---|
| `LITE_LLM` | admin-only for project scope (`project_settings.py:92-104`); `require_litellm_enabled()`; `validate_litellm_request`; budget adapter writes/deletes these rows by `(project, alias)` (`enterprise/litellm/budget_provider_adapter.py:260-308`, `settings.py:919-949`); `budget_service.backfill_project_budget_assignments_from_settings` L1336-1390 | **Alias literally embeds the project name** (`codemie:project:{project_name}:category:*`). Moving/copying silently breaks budget lookup and can create a duplicate budget key. Strong candidate for exclusion. |
| `GOOGLE_OAUTH` | tokens from an OAuth flow via `oauth_state`; old tokens revoked on email change (`settings.py:384-388, 463-481`); revoked on delete (L535-542) | Tokens are **user-bound**; copying duplicates a revocable token. Revoke-on-delete means a naive move-as-delete+create would kill the token. |
| `ENVIRONMENT_VARS` (MCP) | `force_all=True` — every value encrypted and masked | Must copy raw row, never a service read. |
| `PLUGIN` | `setting_hash = hash_string(plugin_key)` (`settings.py:393-398, 509-514`); IDE plugin uses internal alias + virtual project | Hash must be preserved verbatim. |
| `WEBHOOK` | `check_webhook_id_unique` — **globally** unique `webhook_id` | **Copy is impossible without regenerating `webhook_id`**; a verbatim copy violates a global invariant. |
| `SCHEDULER` | `validate_scheduler_request`; `find_by_resource_id(project_name, credential_type, resource_id)` links to a datasource/workflow/assistant **in the same project** | Copy/move leaves a **dangling `resource_id`** pointing at an entity in the other project. |
| `AWS` | delete cascades into Bedrock entities via `BedrockOrchestratorService.delete_all_entities(setting_id)` | A move implemented as delete+create would **destroy Bedrock entities**. Move MUST be an in-place `project_name` update. |
| `GIT` / `SVN` | `validate_git_request` (PAT vs GitHub App); URL normalized on save (`_prepare_cred_values`, `settings.py:583-592`) | Safe. |
| `SHAREPOINT` | OAuth refresh writes back into the setting (`settings.py:1496`) | Two rows refreshing the same token can race. |

Aliases starting with `SettingsService.INTERNAL_PREFIX = "__internal__"` (`settings.py:118-120`) are hidden from listings (`get_settings` L280, `settings_index_service.py:71`) and use a virtual project `__internal__IDE_virtual`. These must be excluded from bulk move/copy.

`is_global` is a **boolean column**, not a sentinel project value (`settings.py:255`). Global USER settings resolve with `project_name` dropped from the query (`GlobalUserSettingsHandler`, `settings_handler.py:154-173`).

---

## 4. Testing Patterns

### 4.1 Configuration

`pytest.ini` (entire file):

```ini
[pytest]
testpaths = tests
pythonpath = src
addopts = --import-mode=importlib
env =
    ENV=local
    REPOS_LOCAL_DIR=./codemie-repos
    PG_URL=postgresql://pg:pg123@localhost:111/postgres
filterwarnings =
    ignore::DeprecationWarning
    ignore::RuntimeWarning
```

- **No `asyncio_mode` configured** → pytest-asyncio `^0.23.7` **strict** mode; every async test needs an explicit marker.
- `@pytest.mark.anyio` requires a **module-local `anyio_backend` fixture** returning `'asyncio'` (no global one). Usage counts: `asyncio` ×1162, `anyio` ×149. The existing settings suites use `anyio`.
- Fake `PG_URL` on port 111 makes un-mocked DB access fail fast.
- `tests/conftest.py:34-49` — session-scoped autouse fixture patching `PostgresClient.get_engine` with a `MagicMock`. **There is no test DB.**
- `tests/codemie/rest_api/routers/conftest.py:26` disables the rate limiter; L29-42 injects `request.state.uuid`.

### 4.2 Relevant existing tests

- `tests/codemie/rest_api/routers/test_project_settings.py`
- `tests/codemie/rest_api/routers/test_user_settings.py`, `test_user_settings_crud.py` (the newest, most complete CRUD suite)
- `tests/codemie/service/settings/test_settings_service.py`, `test_settings_handler.py`, `test_settings_index_service.py`, `test_settings_request_validator.py`, `test_delete_google_oauth_integration.py`, …
- `tests/integration/` exists but is **empty**; `*_integration.py` means "integration-feature test", not integration-level test.

### 4.3 House style — API test

```python
app = FastAPI()
app.include_router(router)

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.create_setting')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")   # ALWAYS innermost
async def test_create_user_setting_success(mock_authenticate, mock_create_setting, mock_user):
    # Arrange
    mock_authenticate.return_value = mock_user
    ...
    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post("/v1/settings/user", headers={"user-id": "user123"}, json=request_data)
    # Assert
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"message": "Specified credentials saved"}
    mock_create_setting.assert_called_once()
```

Conventions: literal `# Arrange` / `# Act` / `# Assert` blocks; `LocalIdp.authenticate` patch is always the **bottom** decorator; assert `status.HTTP_*` constants (never bare ints), exact `response.json()`, then `mock.assert_called_once_with(...)`.

### 4.4 House style — permission-denied test

Because the bare `FastAPI()` app has no `ExtendedHTTPException` handler, the exception propagates:

```python
with pytest.raises(ExtendedHTTPException) as excinfo:
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        await ac.post("/v1/settings/user", headers={"user-id": "user123"}, json=request_data)

assert excinfo.value.code == status.HTTP_403_FORBIDDEN
assert "Access denied" in excinfo.value.message
mock_create_setting.assert_not_called()   # always assert the side effect did NOT happen
```

### 4.5 House style — service test

Plain `unittest.mock` (`patch` / `patch.object` / `MagicMock`), class-grouped, `# Arrange / # Act / # Assert`. `Settings` is an active-record model, so tests patch its classmethods directly (`@patch.object(Settings, 'get_by_user_id')`, `@patch("codemie.service.settings.settings.Settings")`). Fixtures return **real Pydantic models** for domain objects. Fake secrets are prefixed `test-fake-` / `TEST_FAKE_` to satisfy gitleaks.

### 4.6 Checklist for a new test file

1. Apache-2.0 EPAM license header (14 lines) — enforced by `make license-check`.
2. Module docstring listing endpoints covered.
3. `app = FastAPI(); app.include_router(router)` + module-local `anyio_backend` fixture.
4. Decorator stack bottom-up, `authenticate` last.
5. `# Arrange` / `# Act` / `# Assert`.
6. Line length ≤ 120 (`ruff` `line-length = 120`).

---

## 5. Quality gates

`Makefile`: `test` L27 (`poetry run pytest tests/`), `ruff` L30-33 (format → check --fix → check), `license` L41-43, `gitleaks` L50-51 (dockerized), **`verify` L54** = ruff + license + gitleaks + test, `coverage` L56-57.

---

## 6. Risk Indicators

| # | Risk | Severity | Evidence |
|---|---|---|---|
| R1 | **Move-as-delete+create destroys AWS Bedrock entities** (`delete_all_entities(setting_id)`) and revokes Google OAuth tokens. Move must be an in-place `project_name` UPDATE preserving `id`. | **Critical** | `bedrock_orchestration_service.py:29-34`; `settings.py:535-542` |
| R2 | **Workflows referencing `integration_alias` hard-fail with `ValueError`** the moment `project_name` changes. No warning, no fallback. | **Critical** | `tool_service.py:196-205`; `bedrock_flow_node.py:98-104` |
| R3 | **`WEBHOOK` copy violates the global `webhook_id` uniqueness invariant** — a verbatim copy is illegal. | **Critical** | `models/settings.py:317-327`; `settings.py:1614-1624` |
| R4 | **`LITE_LLM` alias embeds the project name** (`codemie:project:{project_name}:category:*`) and is the join key to budgets. Move/copy silently breaks budget lookup. | **High** | `credentials.py:183`; `settings.py:868-949`; `budget_provider_adapter.py:260-308` |
| R5 | **Alias collision in the target project.** `(project_name, alias)` uniqueness is app-level only, checked only on create/update of the setting. A bulk move can create duplicates that `get_by_alias`/`get_by_fields` then resolve **non-deterministically** via `.first()`. | **High** | `settings.py:296-315`; `models/settings.py:442-475` |
| R6 | **No DB unique constraint + read-then-write check → race conditions** under concurrent move/create. | **High** | migration `074d06e75b25:104-124` |
| R7 | **`create_setting` auto-creates a missing project** via `ensure_application_exists`. A typo'd target project name silently creates a project instead of 404ing. Explicit existence validation is mandatory. | **High** | `settings.py:405-410`; `default_applications.py:22` |
| R8 | **Copy must read the raw DB row**, never `get_settings` (masked) or `retrieve_setting` (decrypted → would double-encrypt on re-create). | **High** | `base_settings.py:79-97`; `settings.py:566-580, 1584-1613` |
| R9 | **Per-user assistant MCP mappings degrade silently** after a move (logged, not surfaced); mapping *save* returns 403. | **Medium-High** | `settings_util.py:54-61`; `toolkit_service.py:1925, 1963-1969`; `assistant_mapping.py:55` |
| R10 | **Assistants carry a stale embedded `SettingsBase` snapshot** (`project_name`, `alias`, `setting_type`, encrypted values) that no move path refreshes. | **Medium-High** | `assistant.py:143,152,213-215` |
| R11 | **`SCHEDULER` integrations carry a `resource_id`** pointing at an entity in the source project; move/copy leaves it dangling. | **Medium** | `settings.py:401-418`; `scheduler_settings_service.py:340-355` |
| R12 | **LiteLLM caches are not invalidated on project change**: TTL cache key contains `project_name`; `_get_integration_api_key` `lru_cache` is never invalidated at all. | **Medium** | `credentials.py:36-55`; `proxy_router.py:809-843` |
| R13 | **`__internal__`-prefixed settings** (IDE plugin, virtual project) must be excluded from bulk operations. | **Medium** | `settings.py:118-120, 280`; `settings_index_service.py:71` |
| R14 | **No reverse-lookup query exists** for "which datasources/assistants use setting X" (only Bedrock has them). Any impact report or reference-fix-up needs new queries. | **Medium** | `index.py:880`; `assistant.py:1154,1165` |
| R15 | **`project_settings.py` DELETE bypasses `SettingsService.delete_setting`**, skipping OAuth revoke + cache clear. Do not copy that pattern. | **Medium** | `project_settings.py:192` |
| R16 | **No atomicity story across a bulk operation.** Precedent (`bulk_assign_users_to_project`) is validate-all-then-apply; the skill/admin precedents use partial-success reporting. A choice is required. | **Medium** | `project_assignment_service.py:423`; `skill.py:608-631` |
| R17 | **Project rename already fails to update `settings.project_name`** — the same dangling-project bug class exists today and may produce integrations pointing at non-existent projects. | **Low-Medium** | `project_service.py:172-253` |
| R18 | **`SettingType.USER` rows also live in the `settings` table** and share the `project_name` column. The endpoint must scope strictly to `setting_type == PROJECT` or it will silently move users' personal credentials. | **Medium** | `models/settings.py:391-399` |

---

## 7. Open questions for brainstorming

1. **Selection granularity** — all integrations in the source project, or an explicit `integration_ids[]` list? AC says "integrations from one source project to one target project" but scenario 1 says "identifies integrations attached to Project X that need to be assigned". An explicit id list is safer and covers the all-of-them case.
2. **Atomicity** — all-or-nothing (mirroring `bulk_assign_users_to_project`) vs partial success with a per-item result list (mirroring `bulk-attach` / `retire/bulk`)?
3. **Alias collision policy in the target** — reject the whole request, skip the colliding item, or auto-suffix (`alias (2)`)?
4. **Excluded credential types** — hard-block `WEBHOOK` (global uniqueness), `LITE_LLM` (project-embedded alias + budget coupling), `GOOGLE_OAUTH` (user-bound tokens), `SCHEDULER` (dangling `resource_id`) for copy? For move?
5. **Copy semantics for `created_by` / `user_id` / `default` / `is_global`** — preserve or reset to the caller?
6. **Permission bar** — project admin on **both** projects (`_check_permission` twice), or admin/maintainer only? AC requires "permissions to manage integrations for the involved projects".
7. **Dry-run endpoint** — mirror `import-users/validate` to surface the impact report before applying?
8. **Reference fix-up on move** — do nothing (AC allows "behave according to the current implementation"), or at minimum *report* affected datasources/assistants/workflows in the response?
9. **Cache invalidation scope** — call `clear_litellm_user_credentials_cache(None)` after any PROJECT-scoped move?
10. **Route placement** — `/v1/settings/project/transfer` (settings router) vs `/v1/projects/{projectName}/integrations/transfer` (projects router, reusing `_authorize_project_access`)?
