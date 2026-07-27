# EPMCDME-13666 — Move/Copy Integrations Between Projects

**Ticket**: https://jiraeu.epam.com/browse/EPMCDME-13666
**Branch**: `EPMCDME-13666_move-copy-project-integrations`
**Grounding**: [technical-analysis.md](./technical-analysis.md), [complexity-assessment.json](./complexity-assessment.json) (L, 25/36)

---

## 1. Context

An "integration" in the product UI is a row in the `settings` table (`Settings`, `src/codemie/rest_api/models/settings.py:243-476`). It is attached to a project by a plain `project_name: str` column — no foreign key, no DB constraint. Both scopes carry that column:

| `setting_type` | Meaning | Managed via |
|---|---|---|
| `PROJECT` | shared across the project | `POST /v1/settings/project` |
| `USER` | one user's personal credential, still attached to a project | `POST /v1/settings/user` |

Today the project cannot be changed after creation — the edit UI disables the field. This spec adds a backend endpoint that moves or copies **all** of a source project's integrations to a target project.

**Both scopes are in scope.** A user-scoped integration belongs to a project just as a project-scoped one does, and transferring only half of a project's integrations would leave the target incomplete. A transferred USER row keeps its `user_id`, so it remains that user's personal credential — only its project attachment changes.

---

## 2. Endpoint

### Placement

New module `src/codemie/rest_api/routers/settings.py`, registered in `src/codemie/rest_api/main.py` alongside `user_settings` and `project_settings`.

The two existing router modules are organised by scope. This operation spans both, so it belongs in neither. The repository currently has no home for scope-spanning settings endpoints — the two that exist (`GET /v1/settings/user/available` at `user_settings.py:92-118` and `POST /v1/settings/test/` at `user_settings.py:242-269`) are both parked in `user_settings.py`. A neutral `settings.py` module is a deliberate improvement on that, and gives later scope-neutral endpoints somewhere to go.

Service: new `SettingsTransferService` in `src/codemie/service/settings/settings_transfer_service.py`. Kept out of `settings.py` (~1600 lines already); the operation is self-contained.

### Contract

```
POST /v1/settings/transfer
```

**Authorization**: `dependencies=[Depends(admin_or_maintainer_access_only)]` (`src/codemie/rest_api/security/authentication.py:173-183`), plus `user: User = Depends(authenticate)` for the actor identity used in logging. Project admins are deliberately *not* sufficient: the operation relocates credentials — including other users' personal ones — across a project boundary.

**Request**

```python
class TransferMode(str, Enum):
    MOVE = "move"
    COPY = "copy"


class TransferSettingsRequest(BaseModel):
    source_project_name: str = Field(min_length=1)
    target_project_name: str = Field(min_length=1)
    mode: TransferMode
```

A missing or unrecognised `mode` fails Pydantic validation and is rendered by the existing `RequestValidationError` handler (`main.py:1064`) as **422** with the offending field path. No hand-rolled mode check is needed.

**Response** — `200 OK`

```python
class TransferredIntegration(BaseModel):
    id: str
    alias: str
    credential_type: CredentialTypes


class SkippedIntegration(BaseModel):
    alias: str
    credential_type: CredentialTypes


class TransferSettingsResponse(BaseModel):
    message: str
    source_project_name: str
    target_project_name: str
    mode: TransferMode
    transferred_count: int
    transferred: list[TransferredIntegration]
    skipped_count: int
    skipped: list[SkippedIntegration]
```

`transferred[].id` is the unchanged row id for `move`, and the newly created row id for `copy`.

`skipped[]` reports **only** `WEBHOOK` and `SCHEDULER` rows held back under `copy` (§4.2). It is therefore always empty with `skipped_count: 0` when `mode == move`. Machine-managed rows (§4.1) never appear in it.

---

## 3. Behaviour

### 3.1 Move

An in-place UPDATE of `project_name` (plus `update_date`). The row keeps its `id`.

This is not an implementation preference — it is required. Implementing move as delete-then-create would fire `BedrockOrchestratorService.delete_all_entities(setting_id)` (`aws_bedrock/bedrock_orchestration_service.py:29-34`), destroying Bedrock knowledge bases, guardrails, flows and agents attached to an `AWS` integration.

Because the `id` is preserved, every id-based consumer keeps resolving (`BySettingIDSettingsHandler` checks `credential_type` only, not `project_name` — `settings_handler.py:78-87`).

### 3.2 Copy

Insert a new row per source row: fresh `id`, `project_name = target`, fresh `date` / `update_date`. **Every other field is duplicated verbatim** — `alias`, `credential_type`, `credential_values`, `user_id`, `created_by`, `setting_type`, `is_global`, `default`, `setting_hash`.

Two constraints on how the copy is produced:

- **Read the raw row** (`Settings.get_by_id` / the session query), never `get_settings` (masks secrets to `**********`, `base_settings.py:88-97`) and never `retrieve_setting` (returns *decrypted* values, `settings.py:1584-1613` — re-creating from those would double-encrypt).
- **Duplicate `credential_values` ciphertext byte-for-byte.** Encryption is not keyed by project, user, or setting id (`base_settings.py:43-77`, `encryption/encryption_factory.py:27-69`), so verbatim ciphertext is valid in the target. It is also required for correctness: the Google OAuth shared-token guard compares *encrypted* values (`google_oauth/token_manager.py:357-358`), and re-encryption under KMS or Vault would produce different ciphertext and defeat it.

`user_id` must be preserved for the same reason — that guard searches via `Settings.get_by_user_id(user_id)` (`token_manager.py:346-350`). If a copy were re-owned, the two rows would land under different users, the guard would miss, and deleting either would revoke a token the other still depends on.

`created_by` is preserved because it records authorship of the credential, not custody of the transfer.

---

## 4. Exclusions and skips

Two distinct notions, deliberately handled differently.

### 4.1 Silently excluded — machine-managed rows (both modes)

Never transferred, never reported. These are not integrations any user created or can see in the product; they are written and owned by other subsystems.

| Alias pattern | Owner | `setting_type` | Note |
|---|---|---|---|
| prefix `__internal__` | IDE plugin settings (`settings.py:118-120`) | USER | Lives in a fake project `__internal__IDE_virtual`, so `project_name == source` never selects it. Filter retained as defence for the namespace. |
| prefix `codemie:project:` | LiteLLM budget provider (`budget_provider_adapter.py:57,86-87`) | PROJECT | Alias embeds the project name and is the join key to budget entities. |
| prefix `Schedule_` | index router datasource schedules (`scheduler_settings_service.py:38,252-257`) | USER | Deduped by `(user_id, project_name, resource_id)`; a transferred row would be missed and a duplicate schedule created, causing double indexing. |
| exact `project_member_budget_tracking_enabled` | member-spend flag (`settings.py:125`, `886-918`) | PROJECT | Owned by `set_enforce_member_spend_limits`. |

Documented in the OpenAPI endpoint description, not in the response body.

### 4.2 Reported skips — trigger types on copy

`WEBHOOK` and `SCHEDULER` are **not copied**. They are returned in `skipped[]`. They **are** moved normally, and under `move` the `skipped[]` array is always empty — these two credential types under `copy` are the only thing that ever populates it.

Both are trigger bindings rather than passive credentials: copying one duplicates its firing.

- `WEBHOOK` — `check_webhook_id_unique` (`models/settings.py:317-327`) enforces a **globally** unique `webhook_id` with no project filter, because inbound delivery resolves by that id alone. A verbatim copy creates exactly the ambiguity that check exists to prevent.
- `SCHEDULER` — a copy yields two rows with the same `resource_id`. Cron discovery filters on credential type only, with no project filter (`triggers/bindings/cron.py:701-713`), so **both fire**: double indexing runs, silently.

Skipping rather than rejecting: the caller operates on a whole project and cannot exclude individual rows, so rejecting would leave no path forward.

---

## 5. Validation

All checks complete before any write.

1. `source_project_name == target_project_name` → **422**.
2. Source project exists — `application_repository.exists_by_name` → **404** if not. Deliberately not `ensure_application_exists`, which auto-creates the project (`settings.py:405-410`, `utils/default_applications.py:22`) and would silently turn a typo into a new project.
3. Target project exists → **404** if not.
4. Load candidates: all `Settings` rows with `project_name == source`, both scopes. (`Settings.get_by_project_names` cannot be used — it hard-filters `setting_type == PROJECT`, `models/settings.py:391-399`.)
5. Drop machine-managed rows (§4.1).
6. Nothing left → **200** with `transferred_count: 0`, `skipped_count: 0`, and a message stating the source has no transferable integrations. Not an error.
7. `mode == copy` → move `WEBHOOK` / `SCHEDULER` rows into `skipped[]` and out of the transfer set.
8. Alias collision against the target → **409** listing every colliding alias, nothing written.

### 5.1 Collision rules — reuse `check_alias_unique`

**Do not reimplement the rule. Call `Settings.check_alias_unique`** (`models/settings.py:296-315`) once per candidate row, against the *target* project:

```python
Settings.check_alias_unique(
    project_name=target_project_name,
    alias=row.alias,
    setting_id=None,
    user_id=row.user_id,
    setting_type=row.setting_type,
)
```

It raises `ValueError` on collision. Catch per row, accumulate the offending aliases, and raise a single **409** naming all of them; nothing is written if the list is non-empty.

**`setting_id` is `None` in both modes.** It exists so an *update* does not collide with itself, but the query always targets the destination project while the candidate row still lives in the source — and `source == target` is already rejected at validation step 1. The row can therefore never appear in its own collision query, making the self-exclusion branch unreachable. Passing the row id would be harmless but misleading.

**Why reuse rather than reimplement.** The rule is not obvious — type-blind on the PROJECT branch, owner-scoped on the USER branch, and dependent on PROJECT rows carrying `user_id`. Encoding it in a second place invites drift, and drift here fails silently: the transfer would produce states the create path forbids, surfacing only later when `get_by_fields(...).first()` resolves a duplicate to the wrong row.

**Accepted costs.** One `get_by_fields` per candidate row, each opening its own `Session` (`models/base.py:464`); a project with a few dozen integrations means a few dozen round trips on a rare admin operation. The alternative — loading the target's rows once and matching in memory — is a single query, but pays for it by duplicating the logic above, which is the wrong trade here. The check also runs outside the write transaction, so it inherits the create path's existing read-then-write race; with no DB unique constraint (§5.1, verified) concurrent writers can still collide. Neither is new exposure.

**Empty aliases.** `check_alias_unique` opens with `if not alias: raise ValueError("Alias is required")`, and `Settings.alias` is `Optional[str]` (`models/settings.py:247`), so a NULL-alias row is representable even though `SettingRequest.alias` is not. Guard before calling: a candidate row with a falsy alias is rejected with **422** naming its id and credential type, rather than being reported as a collision.

#### How the rule actually behaves

Verified by reading the implementation, not inferred:

```python
# models/settings.py:296-315
if setting_type == SettingType.PROJECT:
    settings = cls.get_by_fields({PROJECT_NAME_TERM: project_name, ALIAS_TERM: alias})
else:
    settings = cls.get_by_fields({PROJECT_NAME_TERM: project_name, USER_ID_TERM: user_id, ALIAS_TERM: alias})

if settings and not setting_id or settings and setting_id and setting_id != settings.id:
    raise ValueError(f"There are more than one settings with the alias named {alias}")
```

- **Neither branch filters on `setting_type`.** The PROJECT branch queries `(project_name, alias)` only; the USER branch queries `(project_name, user_id, alias)`.
- **The raise condition** parses as `(settings and not setting_id) or (settings and setting_id and setting_id != settings.id)` — `and` binds tighter than `or`. No match passes; a match with no `setting_id` raises; a match with a different id raises; a match with the same id passes.
- **The queries are plain equality.** `_get_list_condition` (`models/base.py:440-453`) returns `None` for non-list fields, and `project_name` / `user_id` / `alias` are all scalars (`models/settings.py:244-247`), so `get_by_fields` falls through to `get_field_expression(key) == value` and returns `.first()` (`models/base.py:463-475`).
- **PROJECT rows carry `user_id`.** `create_setting` sets `user_id=user_id` unconditionally in the `Settings(...)` constructor (`settings.py:428`), and `project_settings.py:113` passes `user_id=user.id` together with `settings_type=SettingType.PROJECT`. This is why the USER-branch query can match a PROJECT row.
- **No database-level uniqueness exists.** The model's `__table_args__` (`models/settings.py:258-267`) declares three `Index` entries and no constraint; migration `074d06e75b25` creates every settings index with `unique=False` (lines 107-124); no `UniqueConstraint` naming `settings` appears in any migration. The rule is enforced solely in application code.
- **Caller wiring**, since the branch depends on `setting_type`: `create_setting` calls the check at line 378 with the raw `settings_type`, *before* it defaults to `USER` at lines 405-406 — so `None` takes the USER branch, matching the eventual default. `update_settings` (451-457) passes `setting_id=credential_id`; `project_settings.py:163` updates with `settings_type=PROJECT` and no `user_id` (unused on that branch), while `user_settings.py:195` updates with `user_id` and no `settings_type` (falls to the USER branch).

The resulting matrix, for one project and one alias `A`:

| Existing row | New attempt | Query issued | Result |
|---|---|---|---|
| `USER(U1, A)` | `PROJECT(A)` | `{project, alias}` | blocked — type-blind, matches the USER row |
| `USER(U1, A)` | `USER(U1, A)` | `{project, user_id=U1, alias}` | blocked |
| `USER(U1, A)` | `USER(U2, A)` | `{project, user_id=U2, alias}` | allowed |
| `PROJECT(U1, A)` | `USER(U1, A)` | `{project, user_id=U1, alias}` | blocked — PROJECT row carries `user_id=U1` |
| `PROJECT(U1, A)` | `USER(U2, A)` | `{project, user_id=U2, alias}` | allowed |
| `PROJECT(U1, A)` | `PROJECT(A)` | `{project, alias}` | blocked |

In short: **PROJECT creation is blocked by any row holding that alias; USER creation is blocked only by rows holding that alias and owned by the same user.**

So a single project may legitimately contain several USER rows with alias `A` owned by different users, and may contain a PROJECT row with alias `A` alongside USER rows with alias `A` owned by anyone other than the PROJECT row's `user_id`. It may never contain two PROJECT rows with the same alias, nor a PROJECT row and a same-owner USER row with the same alias.

Every multi-row state a transfer can land in the target is therefore reachable through ordinary creation (create the PROJECT row first, then the other owners' USER rows), so no additional intra-batch rule is required: candidates are validated against the target's existing rows only, and a source project's internally-legal set stays legal once transferred.

---

## 6. Atomicity

All-or-nothing. Either every selected row transfers or none does; skips per §4 are a deterministic rule, not a partial failure.

`Settings.save()` and `Settings.update()` each open their own session and commit immediately (`models/base.py:512-514`, `525-529`), so looping over them yields per-row commits. The write loop must therefore run inside a single `with get_session() as session:` with one `commit()`, mutating or adding model instances bound to that session — the pattern already used by the bulk project endpoints (`routers/projects.py:1145-1175`).

---

## 7. Cache invalidation

If any transferred row has `credential_type == LITE_LLM`, call `clear_litellm_user_credentials_cache(None)` once after commit.

The TTL cache key includes `project_name` (`enterprise/litellm/credentials.py:36-55`), so a project change silently invalidates nothing. Targeted invalidation is not possible without enumerating every user who cached a project-scoped resolution. The existing helper `_clear_litellm_user_credentials_cache_if_needed` (`settings.py:551-563`) fires only for `LITE_LLM` + `SettingType.USER` and refuses a full clear, so it cannot be reused here.

Not addressed: `_get_integration_api_key` (`enterprise/litellm/proxy_router.py:809-843`) is an `@lru_cache` that is never invalidated anywhere in the repository. It is keyed by setting id, which a transfer does not change, so behaviour is unaffected either way. Out of scope.

---

## 8. Documented behaviour (OpenAPI description)

The endpoint description states:

- which alias patterns are skipped silently, and that they are subsystem-managed;
- that `WEBHOOK` and `SCHEDULER` are move-only, and appear in `skipped[]` under `copy`;
- that user-scoped integrations are transferred too, retaining their owner;
- that under `move`, dependent entities referencing an integration **by id** (datasources via `IndexInfo.setting_id`, assistants, Bedrock, A2A) continue to work, while references **by alias** resolve against the entity's own project and will fail — workflows raise `ValueError` (`service/tools/tool_service.py:196-205`, `workflows/nodes/bedrock_flow_node.py:98-104`), per-user MCP integration overrides are skipped with a log line (`service/mcp/toolkit_service.py:1963-1969`), and re-saving an assistant-user mapping to a moved integration returns 403 (`settings_util.py:54-61`, `routers/assistant_mapping.py:49-61`).

This is the AC's "behave according to the current implementation", surfaced rather than changed.

---

## 9. Error catalogue

| Condition | Status | Shape |
|---|---|---|
| Missing / unsupported `mode`; blank project name | 422 | `RequestValidationError` handler, field path included |
| `source == target` | 422 | `ExtendedHTTPException` |
| Source or target project not found | 404 | `ExtendedHTTPException`, names which one |
| Caller not admin/maintainer | 403 | raised by `admin_or_maintainer_access_only` |
| Alias collision in target | 409 | `ExtendedHTTPException`, `details` lists colliding aliases |
| Candidate row has an empty/NULL alias | 422 | `ExtendedHTTPException`, `details` names the row id and credential type |
| Source has no transferable integrations | 200 | `transferred_count: 0`, explanatory `message` |

All bodies follow the existing `{"error": {"message", "details", "help"}}` shape emitted by the `ExtendedHTTPException` handler (`main.py:969`).

---

## 10. Testing

`tests/codemie/rest_api/routers/test_settings_transfer.py` and `tests/codemie/service/settings/test_settings_transfer_service.py`.

House style (see technical-analysis §4): module-level `app = FastAPI(); app.include_router(router)` with a local `anyio_backend` fixture; `LocalIdp.authenticate` patched as the innermost decorator; `# Arrange` / `# Act` / `# Assert` blocks; `status.HTTP_*` constants; `pytest.raises(ExtendedHTTPException)` for denials plus `assert_not_called()` on the guarded service; fake secrets prefixed `test-fake-`; Apache-2.0 header; line length ≤ 120.

Cases:

**Modes** — move relocates and preserves `id`; copy creates a new `id` and leaves the source row intact; copy preserves `user_id`, `created_by`, `is_global`, `default`, `alias` and ciphertext verbatim.

**Validation** — missing `mode` → 422; unsupported `mode` → 422; blank project name → 422; `source == target` → 422; unknown source → 404; unknown target → 404; empty source → 200 with zero counts.

**Permissions** — non-admin, non-maintainer → 403 and no write attempted.

**Skips and exclusions** — `WEBHOOK` skipped on copy, transferred on move; `SCHEDULER` likewise; `skipped[]` empty with `skipped_count: 0` on every move; each of the four machine-managed alias patterns excluded from both modes and absent from `skipped[]`.

**Collisions** — asserted against the matrix in §5.1, with `Settings.check_alias_unique` left unmocked so the real rule is exercised: PROJECT row vs. any target row holding the alias (PROJECT-owned or USER-owned, same or different owner) → 409; USER row vs. a same-owner target row → 409; USER row whose alias exists in the target under a *different* owner → transfers successfully; PROJECT row and a different owner's USER row transferring together into an empty target → both land. Also assert a 409 names every colliding alias rather than only the first, and that a candidate row with a NULL alias yields 422 rather than a collision error or a 500.

**Atomicity** — a collision detected mid-set leaves nothing written.

**Cache** — `clear_litellm_user_credentials_cache` called once with `None` when a `LITE_LLM` row transfers, and not called otherwise.

---

## 11. Out of scope

- Rewriting dependent references after a move (workflow `integration_alias`, assistants' embedded `SettingsBase` snapshots). No reverse-lookup query exists today — there is no `IndexInfo.get_by_setting_id` and no assistant-by-embedded-settings-id query. The AC explicitly permits current behaviour.
- Transferring subsystem-managed rows (LiteLLM budget keys, index-router schedules). Those are provisioned and deleted by their owning services; re-provisioning them in another project is a different operation.
- Selecting a subset of integrations; per-item partial success; a dry-run/preview endpoint.
- The pre-existing bug whereby renaming a project does not update `settings.project_name` (`service/project/project_service.py:172-253`).
- Adding a DB unique constraint on `(project_name, alias)`. Uniqueness stays application-level and read-then-write, so it remains race-prone under concurrent writes — unchanged by this work.
