# Persisted LLM Model Status + Admin API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a CodeMie-owned persisted tri-state model status (enabled/disabled/deprecated), merge it into `LLMService`'s catalog reload path, expose admin-only read/set endpoints, and enforce the `deprecated` write-path guard at the confirmed call sites. Backend-only (`codemie/`); no frontend work.

**Architecture:** New `llm_model_status` table + repository (mirrors `user_preferences`), a new `ModelStatus` enum + `LLMModel.status` field, a merge step inside `LLMService.initialize_default_litellm_models()`, a status-aware narrowing added to the four `get_allowed_*` methods, three new admin endpoints in `admin.py` (mirrors `retire`/`retire/bulk`), a new `assert_model_selectable()` guard + `ModelNotSelectableError`, wired into the five confirmed write-path call sites.

**Tech Stack:** FastAPI, SQLModel, Alembic, pytest.

**Commits:** one commit per task, `git add <path> <path>...` (never `git add -A`) — the working tree has pre-existing unrelated dirty files (`config/customer/customer-config.yaml`, untracked `.codemie-repos/`) that must never be committed.

---

### Task 1: `ModelStatus` enum + `LLMModel.status` field

**Files:** Modify `src/codemie/configs/llm_config.py:55-76` (add enum), `:166-188` (add field). Test: `tests/codemie/configs/test_llm_config.py`.

Test-first: yes — failing test asserts `LLMModel(base_name="x", deployment_name="x", enabled=True).status == ModelStatus.ENABLED` and that `ModelStatus("deprecated") == ModelStatus.DEPRECATED`.

```python
class ModelStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"
```

Add `status: ModelStatus = ModelStatus.ENABLED` to `LLMModel` (after `is_premium`, line 188). Do not touch `enabled` (line 172) — it stays the provider-sourced signal.

---

### Task 2: `llm_model_status` SQLModel table + repository

**Files:** Create `src/codemie/rest_api/models/llm_model_status.py`, `src/codemie/repository/llm_model_status_repository.py`. Test: `tests/codemie/repository/test_llm_model_status_repository.py`.

Test-first: yes — failing test: `upsert()` on a fresh session followed by `get_by_base_name()` returns a row with the given status; a second `upsert()` for the same `base_name` updates in place (no duplicate row); `get_by_base_name()` for an unknown name returns `None`; `get_all()` returns all rows. Mirror `tests/codemie/service/test_user_preferences_service.py`'s `MagicMock(spec=Session)` style, or a real SQLite/PG test session if an existing repository test uses one — check `tests/codemie/repository/` for the nearest precedent before choosing.

```python
class LLMModelStatus(SQLModel, table=True):
    __tablename__ = "llm_model_status"

    id: uuid.UUID = SQLField(default_factory=uuid.uuid4, primary_key=True)
    base_name: str = SQLField(nullable=False, unique=True, index=True, max_length=255)
    status: str = SQLField(nullable=False, default="enabled", max_length=20)
    updated_by_id: Optional[str] = SQLField(default=None, max_length=255)
    updated_by_name: Optional[str] = SQLField(default=None, max_length=255)
    created_at: Optional[datetime] = SQLField(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    )
    updated_at: Optional[datetime] = SQLField(
        sa_column=Column(TIMESTAMP(timezone=True), nullable=True, onupdate=func.now())
    )
```

`LLMModelStatusRepository` (static methods, session-per-call like `UserPreferencesRepository`):
- `get_all(session) -> list[LLMModelStatus]`
- `get_by_base_name(session, base_name: str) -> LLMModelStatus | None`
- `upsert(session, base_name: str, status: str, admin_id: str, admin_name: str) -> LLMModelStatus` — `session.get`-then-update-or-create, matching `UserPreferencesRepository.upsert`.

---

### Task 3: Alembic migration for `llm_model_status`

**Files:** Create `src/external/alembic/versions/<new_hash>_create_llm_model_status_table.py`.

Test-first: no — this is a schema migration, not application logic; the repository test in Task 2 (which needs the table to exist) is the closest thing to a test and already covers correctness of the schema.

Before writing: run `poetry run alembic -c src/external/alembic/alembic.ini heads` (see `.ai-run/guides/development/setup-guide.md`) to get the current head revision id; use it as `down_revision`. Mirror `a9f3c1d2e4b5_create_user_preferences_table.py`'s structure: `op.create_table("llm_model_status", ...)` with the columns from Task 2, a `UniqueConstraint("base_name")`, and `downgrade()` doing `op.drop_table("llm_model_status")`.

---

### Task 4: Merge persisted status into the catalog on `initialize_default_litellm_models`

**Files:** Modify `src/codemie/service/llm_service/llm_service.py:446-454`. Test: `tests/codemie/service/llm_service/test_llm_service.py` (create if absent).

Test-first: yes — failing test: given a `LiteLLMModels` with one chat model `base_name="m1"` and a persisted `LLMModelStatus(base_name="m1", status="deprecated")` (repository mocked/stubbed), calling `initialize_default_litellm_models()` results in `llm_service.default_litellm_models[0].status == ModelStatus.DEPRECATED`; a model with no persisted row defaults to `ModelStatus.ENABLED`.

Add a private helper `_merge_persisted_status(models: list[LLMModel]) -> list[LLMModel]` that opens a session, calls `LLMModelStatusRepository.get_all()`, builds a `{base_name: status}` dict, and sets `model.status = ModelStatus(row.status) if row else ModelStatus.ENABLED` for every model in `models` (both chat and embedding lists) — do not filter or drop any model here (they are already `enabled=True`-filtered upstream per Task 4's grounding). Call this helper for `chat_models` and `embedding_models` at the top of `initialize_default_litellm_models` before the existing assignment on lines 448-449.

---

### Task 5: Status-aware narrowing in the four `get_allowed_*` methods

**Files:** Modify `src/codemie/service/llm_service/llm_service.py:510-534` (`_filter_models_by_visibility`), `:115-135` (`get_allowed_router_options`). Test: `tests/codemie/service/llm_service/test_llm_service.py`.

Test-first: yes — failing tests, one per case of the conflict matrix, on `_filter_models_by_visibility` (used by `get_allowed_chat_models`/`get_allowed_embedding_models`/`get_allowed_image_generation_models`) and one for `get_allowed_router_options`:
- `enabled=True, status=enabled, include_all=False` → listed.
- `enabled=True, status=disabled, include_all=False` → absent.
- `enabled=True, status=deprecated, include_all=False` → listed, `status == "deprecated"` on the returned object.
- `enabled=False, status=deprecated, include_all=False` → absent (provider `enabled` still wins).
- `enabled=True, status=disabled, include_all=True` → listed (include_all bypasses status narrowing).

Rename `_filter_models_by_visibility`'s body to also drop `model.status == ModelStatus.DISABLED` when `include_all` is `False`, alongside the existing `forbidden_for_web` check (same early return for `include_all=True`). For `get_allowed_router_options` (line 115), after building `by_name` (line 118), skip a router's `capable`/`efficient` when either resolved model has `status == ModelStatus.DISABLED` and `not include_all` — add this check next to the existing `router.forbidden_for_web` check at line 125.

---

### Task 6: `ModelNotSelectableError` + `LLMService.assert_model_selectable()`

**Files:** Modify `src/codemie/core/exceptions.py` (add exception near line 254, alongside `WorkflowGenerationError`), `src/codemie/service/llm_service/llm_service.py` (add method after `get_allowed_embedding_models`, line ~606). Test: `tests/codemie/service/llm_service/test_llm_service.py`.

Test-first: yes — failing test: `assert_model_selectable("m1")` raises `ModelNotSelectableError` when the merged model for `base_name="m1"` has `status == ModelStatus.DEPRECATED`; raises the same error when `enabled=False` (defense-in-depth); does not raise for `status == ModelStatus.ENABLED`; does not raise for an unknown `base_name` (nothing to guard — the caller's own validation handles unknown models elsewhere).

```python
class ModelNotSelectableError(Exception):
    """Raised when a model cannot be selected for new work (deprecated or unavailable)."""

    def __init__(self, base_name: str, reason: str):
        self.base_name = base_name
        self.reason = reason
        super().__init__(f"Model '{base_name}' is not selectable: {reason}")
```

```python
def assert_model_selectable(self, base_name: str) -> None:
    model = next((m for m in self.get_all_llm_model_info() if m.base_name == base_name), None)
    if model is None:
        return
    if model.status == ModelStatus.DEPRECATED:
        raise ModelNotSelectableError(base_name, "model is deprecated")
    if not model.enabled or model.status == ModelStatus.DISABLED:
        raise ModelNotSelectableError(base_name, "model is not available")
```

---

### Task 7: Wire the guard into `create_assistant` / `update_assistant`

**Files:** Modify `src/codemie/rest_api/routers/assistant.py:730-827` (`create_assistant`), `:836-926` (`update_assistant`). Test: `tests/codemie/rest_api/routers/test_assistant.py` (extend or create).

Test-first: yes — failing test: `POST /assistants` with `llm_model_type` set to a `base_name` for which `llm_service.assert_model_selectable` raises `ModelNotSelectableError` (mock the service) returns HTTP 400 with a message naming the model; same assertion for `PUT /assistants/{id}`. A request with an unaffected model still returns 200.

In `create_assistant`, after building `assistant` (line 745-753) and before `assistant.save(refresh=True)` (line 782), call `llm_service.assert_model_selectable(assistant.llm_model_type)` inside the existing `try` block; catch `ModelNotSelectableError` and raise `ExtendedHTTPException(code=400, message="Model not selectable", details=str(e))`. Apply the same call in `update_assistant` right after `repository.update(assistant, request, user)` (line 887), guarding on `assistant.llm_model_type` (skip the call if `request.llm_model_type` was not part of `request.model_fields_set`, to avoid blocking unrelated updates that don't touch the model field). Do **not** add this call to `ask_virtual_assistant` (line ~956+) — its `Assistant.model_construct(...)` is never persisted.

---

### Task 8: Wire the guard into `ConversationService.update_conversation`

**Files:** Modify `src/codemie/service/conversation_service.py:976-994`. Test: `tests/codemie/service/test_conversation_service.py` (extend or create).

Test-first: yes — failing test: `update_conversation()` with `request.llm_model` set and `'llm_model' in fields_set` raises `ModelNotSelectableError` when `llm_service.assert_model_selectable` raises (mocked); `conversation.llm_model` is left unchanged (the assignment at line 983 must not run before the check). A request without `llm_model` in `fields_set` does not call the guard at all.

Add, immediately before line 982's `if 'llm_model' in fields_set:` block, a call: inside that same `if` branch, before assigning `conversation.llm_model = request.llm_model`, call `llm_service.assert_model_selectable(request.llm_model)`. Let `ModelNotSelectableError` propagate — the router at `src/codemie/rest_api/routers/conversation.py:582` maps unhandled service exceptions the same way; add a catch there converting it to `ExtendedHTTPException(code=400, ...)` if no such catch-all exists (check the router's existing exception handling for `update_conversation` before adding a new one — do not duplicate).

---

### Task 9: Wire the guard into `create_workflow` / `update_workflow`

**Files:** Modify `src/codemie/rest_api/routers/workflow.py:377-434` (`create_workflow`), `:443+` (`update_workflow`). Test: `tests/codemie/rest_api/routers/test_workflow.py` (extend or create).

Test-first: yes — failing test: `POST /workflows` with a `workflow_config.assistants` list containing one node whose `model` is a `base_name` for which `assert_model_selectable` raises returns HTTP 400 naming that node's model; a workflow whose nodes all reference selectable models still succeeds; `PUT /workflows/{id}` behaves the same for its `updated_config.assistants`.

Add a module-level helper in `workflow.py`:

```python
def _assert_all_assistants_selectable(workflow_config: WorkflowConfig) -> None:
    for wf_assistant in workflow_config.assistants or []:
        if wf_assistant.model:
            try:
                llm_service.assert_model_selectable(wf_assistant.model)
            except ModelNotSelectableError as e:
                raise ExtendedHTTPException(
                    code=status.HTTP_400_BAD_REQUEST,
                    message="Model not selectable",
                    details=str(e),
                ) from e
```

Call `_assert_all_assistants_selectable(workflow_config)` in `create_workflow` right after `workflow_config = WorkflowConfig(**request.model_dump())` (line 385) and before the autonomous-mode check. Call `_assert_all_assistants_selectable(updated_config)` in `update_workflow` right after `updated_config.parse_execution_config()` (line 464), inside the same `try` block that already catches configuration errors.

---

### Task 10: Admin endpoints — `GET /v1/admin/llm/status`, `PUT /v1/admin/llm/{base_name}/status`, `POST /v1/admin/llm/status/bulk`

**Files:** Modify `src/codemie/core/models.py` (add request models near `LLMRetirementPair`, line 352-378), `src/codemie/rest_api/routers/admin.py` (add endpoints after `retire_llm_models_bulk`, line ~400+). Test: `tests/codemie/rest_api/routers/test_admin.py` (extend or create).

Test-first: yes — failing tests per endpoint: `GET /v1/admin/llm/status` (non-admin gets 403; admin gets `data` listing every model currently in the catalog with `status` defaulted to `"enabled"` when no persisted row); `PUT /v1/admin/llm/{base_name}/status` with `{"status": "deprecated"}` persists via `LLMModelStatusRepository.upsert` (mocked) and returns `data: {base_name, status: "deprecated"}`; `PUT` with an invalid status string returns 422 (Pydantic enum validation); `POST /v1/admin/llm/status/bulk` with two updates, one to an unknown status value, returns `data.results` with one success and one `success: False` entry, independent of each other.

Add to `core/models.py`:

```python
class LLMModelStatusUpdateRequest(ConfiguredModel):
    status: ModelStatus


class LLMModelStatusBulkUpdateItem(ConfiguredModel):
    base_name: str
    status: ModelStatus


class LLMModelStatusBulkUpdateRequest(ConfiguredModel):
    updates: list[LLMModelStatusBulkUpdateItem] = Field(..., min_length=1, max_length=100)
```

Add to `admin.py`, mirroring `retire_llm_model`'s `Depends(admin_access_only)` + logging pattern:
- `GET /admin/llm/status`: calls `llm_service.get_all_llm_model_info()` (include_all semantics — every model in the catalog, matching spec's "admin-facing status endpoints always behave as include_all=True"), looks up each `base_name` via `LLMModelStatusRepository.get_all()`, returns `BaseResponseWithData(data=[{base_name, status, updated_by_name, updated_at}, ...])`.
- `PUT /admin/llm/{base_name}/status`: calls `LLMModelStatusRepository.upsert(session, base_name, request.status.value, admin.id, admin.name)`, returns `BaseResponseWithData(data={"base_name": base_name, "status": request.status.value})`.
- `POST /admin/llm/status/bulk`: loops `request.updates`, calling `upsert` per item inside its own try/except (mirroring `retire_models_bulk`'s per-item isolation at `llm_retirement_service.py:235-271`), returns `BaseResponseWithData(data={"total", "succeeded", "failed", "results": [...]})`.

---

### Task 11: Confirm `status` appears on `GET /v1/llm_models` payload

**Files:** No production code change expected — `src/codemie/rest_api/routers/llm_models.py` already serializes `LLMModel` directly. Test: `tests/codemie/rest_api/routers/test_llm_models.py` (extend or create).

Test-first: yes — failing test: `GET /v1/llm_models` response JSON contains `"status": "enabled"` (or the persisted value) for a model whose `LLMModel.status` was set via a mocked `llm_service.get_allowed_chat_models`. If the test passes on first run once Tasks 1-5 land (i.e. no separate implementation step is needed), record that in the test as the assertion itself — do not add router code speculatively.

---

## Negative-constraint pass

- `LLMModel.enabled` untouched — Task 1 adds `status` as a new field; no task modifies `enabled`'s meaning or assignment.
- Merge step does not re-filter on `enabled` — Task 4's `_merge_persisted_status` only sets `.status`, never drops a model from the list.
- `disabled` → fully absent from `get_allowed_*` (not just flagged) — Task 5's test matrix includes an explicit "absent" assertion for `status=disabled, include_all=False`.
- Existing entities referencing a disabled/deprecated model are never touched — Tasks 7-9 only guard NEW writes (create/update endpoints on request-supplied fields); no task adds a migration, backfill, or read-path check against existing rows.
- `include_all=True` bypasses status narrowing — Task 5's test matrix has an explicit `include_all=True` case; Task 10's admin `GET /admin/llm/status` deliberately reads every catalog model rather than calling `get_allowed_chat_models(include_all=False)`.
- `llm_retirement_service.py` / `retire` / `retire/bulk` are not modified — no task touches that file or those two existing endpoints.
- No frontend (`codemie-ui`) work — no task references a `codemie-ui` path.
- No task enumerates write-path call sites beyond the five confirmed in Tasks 7-9 (create/update assistant, update_conversation, create/update workflow); `ask_virtual_assistant` is explicitly named as out of scope in Task 7 because it is never persisted.
