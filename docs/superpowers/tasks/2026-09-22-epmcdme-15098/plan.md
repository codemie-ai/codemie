# EPMCDME-15098 — Enforce MCP Policy on All Persisting Save Paths — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the one confirmed enforcement gap (`use_custom_config` override on catalogue-referenced servers) and add regression coverage proving `MCPAccessControlService` is actually invoked at the 7 call sites that currently have none, so no save path can silently drop the guard.

**Architecture:** One production fix in the shared validator (`MCPAccessControlService._validate_restricted_mode`); everything else is new tests against already-wired call sites in `routers/assistant.py`, `routers/workflow.py`, `skill_service.py`. No new files, no new production symbols besides the one dedicated check.

**Tech Stack:** pytest 8.3.3, `unittest.mock` (`patch`/`MagicMock`), httpx `AsyncClient`/`ASGITransport` for the async workflow router tests, direct function calls for the sync assistant/skill paths.

## Global Constraints

- The only production-code change in this plan is the `use_custom_config` fix in `src/codemie/service/mcp/access_control.py` (Task 1). No other production file changes unless a task explicitly says otherwise.
- Every restricted-mode toggle test MUST patch `codemie.service.mcp.access_control.customer_config` — this is the binding `access_control.py` actually reads. Patching a router-level `customer_config` binding (e.g. `codemie.rest_api.routers.workflow.customer_config`) silently tests the wrong object.
- Mode mocks are `MagicMock()` with `.is_component_enabled.return_value = True/False` (single value is fine here — only one component id, `mcpCustomServersDisabled`, is under test in any of these tasks).
- AC7 tests use a `MagicMock(spec=User)` with `is_admin`/`is_maintainer` set explicitly, never a real `User(...)` construction — under this suite's `pytest.ini` (`ENV=local`), constructing a real `User` forces `is_admin=True` regardless of what's passed, which would make a "non-admin" test silently exercise the admin path instead. A `MagicMock` sidesteps the validator entirely and makes the admin flag explicit and intentional.
- Commit per task using the repository's existing convention (see recent commits on this branch) — one commit per task, no separate quality-gate/verification tasks; the calling flow runs those itself.
- Running tests locally in this session requires `poetry` on PATH: prepend `/c/Users/Mate_Jambricska/AppData/Roaming/Python/Python312/Scripts` before `poetry run pytest ...`.

---

## Acceptance criteria

- [ ] AC1 — Toggle on + hand-written MCP server (assistant/skill/workflow) → rejected, message names the server and states platform policy caused it.
- [ ] AC2 — Toggle on + catalogue-referenced server carrying its own connection config → rejected, comparable message. (Already covered by existing `test_forbidden_field_config_raises` et al. in `test_access_control.py` — no new work required; listed for completeness.)
- [ ] AC3 — Toggle on + catalogue-referenced server declaring custom mode without config → rejected.
- [ ] AC4 — Toggle on + catalogue-referenced server supplying only catalogue-declared user-supplied values → save succeeds.
- [ ] AC5 — Toggle on + same violating payload through every persisting path (assistant create/update/virtual, skill create/update, workflow create/update) → every path rejects it.
- [ ] AC6 — Removing a save path's guard call fails at least one test.
- [ ] AC7 — Admin/maintainer building their own assistant with a violating payload → rejected exactly as any other user.
- [ ] AC8 — Toggle off + hand-written server or config override → save succeeds exactly as today.

negative-constraints:
- "declaring custom mode is itself an override [and must be rejected even without carrying configuration]" (AC3) — Task 1's dedicated check rejects on `use_custom_config` truthiness alone, not on whether `config`/`command` are also present, so a `use_custom_config=True` server with nothing else set still raises. Confirmed no task lets custom-mode-declaration-without-config pass.
- "administrative rank is not a bypass" (AC7) — confirmed by research: no code path anywhere in the nine call sites conditions the `MCPAccessControlService` call on `user.is_admin`/`is_maintainer`. This plan adds no such conditional in Task 1 and Task 2's AC7 test proves none exists. No task introduces an admin bypass.
- "the only production-code change is the `use_custom_config` fix" (ticket's explicit scope boundary) — Tasks 2–5 are test-only; none of their steps touch `routers/assistant.py`, `routers/workflow.py`, or `skill_service.py` production code.
- Out-of-scope items (toggle storage, tool preview, run-time blocking of stored servers, deployment-template startup validation, UI) — no task in this plan touches `preconfigured_assistants.py`, `filter_for_runtime`, `resolve_catalog_config`, or any UI file.

---

### Task 1: Fix the `use_custom_config` override gap in `MCPAccessControlService`

**Files:**
- Modify: `src/codemie/service/mcp/access_control.py:26-39` (frozenset) and `:56-69` (`_validate_restricted_mode`)
- Test: `tests/unit/service/mcp/test_access_control.py`

**Test-first: yes — `test_use_custom_config_true_raises_in_restricted_mode` fails today because `use_custom_config` is still in `_CATALOG_REF_ALLOWED_FIELDS` and no dedicated check exists.**

- [ ] **Step 1: Write the failing test** in `TestValidateOnSaveRestrictedMode`:

```python
def test_use_custom_config_true_raises_in_restricted_mode(self):
    # AC3: declaring custom mode on a catalog-ref server is itself an override.
    servers = [_server("s1", mcp_config_id="cat-1", use_custom_config=True)]
    with _restricted_mode():
        with pytest.raises(ValidationException, match="use_custom_config"):
            MCPAccessControlService.validate_on_save(servers)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/unit/service/mcp/test_access_control.py -k use_custom_config_true -v`
Expected: FAIL (no `ValidationException` raised — `use_custom_config` is allowed today).

- [ ] **Step 3: Implement the fix**

In `_CATALOG_REF_ALLOWED_FIELDS` (line 26-39), remove `"use_custom_config"` from the frozenset. In `_validate_restricted_mode` (line 56-69), add a dedicated truthy check right after the `mcp_config_id`-falsy check and before the generic per-field loop:

```python
if server.use_custom_config:
    raise ValidationException(
        f"Field 'use_custom_config' is not allowed when mcp_config_id is set "
        f"in restricted mode (server '{server.name}')."
    )
```

This must be a dedicated `is True`-style truthy check, not a frozenset-membership removal alone — the generic loop's `getattr(server, field, None) is not None` test would reject every catalog-ref server (default `use_custom_config=False`, and `False is not None`), breaking AC4.

- [ ] **Step 4: Run the new test and the full file to verify pass and no regression**

Run: `pytest tests/unit/service/mcp/test_access_control.py -v`
Expected: PASS, including the pre-existing `test_valid_catalog_ref_passes_in_restricted_mode` (AC4 regression: default `use_custom_config=False` must still pass) and `test_custom_config_true_with_config_returns_server_unchanged`/`test_custom_config_true_with_no_config_returns_none` in `TestResolveCatalogConfig` (unaffected — those exercise `resolve_catalog_config`, not `validate_on_save`).

- [ ] **Step 5: Commit**

---

### Task 2: Regression coverage for `routers/assistant.py` create/update (call sites 1–2)

**Files:**
- Test: `tests/codemie/rest_api/routers/test_assistant.py`

No production change. Reuses the file's existing direct-call pattern (`TestCreateAssistantSlug._patches()`/`_run_create()`, both already in this file) rather than the ASGI transport, and the module's existing `AssistantRequest` construction shape.

**Test-first: yes — each new test currently passes only because the call sites already call `sanitize_for_save`; Step 2 below deliberately breaks that to prove AC6.**

- [ ] **Step 1: Write the failing/regression tests** — add a new class:

```python
class TestAssistantMcpGovernance:
    """Regression guard: create/update must reject a violating payload via
    MCPAccessControlService, and admin status must not bypass it (AC1, AC5, AC6, AC7, AC8)."""

    def _request(self, mcp_servers):
        from codemie.rest_api.models.assistant import AssistantRequest
        return AssistantRequest(
            name="Governed Assistant", description="d", system_prompt="p", project="demo",
            llm_model_type="gpt-4o", skip_integration_validation=True, mcp_servers=mcp_servers,
        )

    def test_create_restricted_mode_rejects_handwritten_server(self):
        from codemie.rest_api.routers.assistant import create_assistant
        from codemie.rest_api.models.assistant import MCPServerDetails
        from codemie.core.exceptions import ValidationException

        request = self._request([MCPServerDetails(name="rogue", command="npx", enabled=True)])
        mode = MagicMock(); mode.is_component_enabled.return_value = True
        with patch("codemie.service.mcp.access_control.customer_config", mode):
            with pytest.raises(ValidationException, match="rogue"):
                create_assistant(request, user=MagicMock(spec=User))

    def test_create_admin_user_violating_payload_still_rejected(self):
        # AC7: admin/maintainer is not a bypass — set the flags explicitly on the mock.
        from codemie.rest_api.routers.assistant import create_assistant
        from codemie.rest_api.models.assistant import MCPServerDetails
        from codemie.core.exceptions import ValidationException

        request = self._request([MCPServerDetails(name="rogue", command="npx", enabled=True)])
        mode = MagicMock(); mode.is_component_enabled.return_value = True
        user = MagicMock(spec=User); user.is_admin = True; user.is_maintainer = True
        with patch("codemie.service.mcp.access_control.customer_config", mode):
            with pytest.raises(ValidationException, match="rogue"):
                create_assistant(request, user=user)

    def test_update_restricted_mode_rejects_handwritten_server(self):
        from codemie.rest_api.routers.assistant import update_assistant
        from codemie.rest_api.models.assistant import MCPServerDetails, Assistant
        from codemie.core.exceptions import ValidationException

        request = self._request([MCPServerDetails(name="rogue", command="npx", enabled=True)])
        mode = MagicMock(); mode.is_component_enabled.return_value = True
        existing = MagicMock(spec=Assistant); existing.project = "demo"
        with (
            patch("codemie.service.mcp.access_control.customer_config", mode),
            patch("codemie.rest_api.routers.assistant.project_access_check"),
            patch("codemie.rest_api.routers.assistant._get_assistant_by_id_or_raise", return_value=existing),
            patch("codemie.rest_api.routers.assistant._check_user_can_access_assistant"),
            patch("codemie.rest_api.routers.assistant._validate_remote_entities_and_raise"),
        ):
            with pytest.raises(ValidationException, match="rogue"):
                update_assistant("assistant-1", request, background_tasks=MagicMock(), user=MagicMock(spec=User))

    def test_create_open_mode_allows_handwritten_server(self):
        # AC8: toggle off, same payload, save succeeds — reuses TestCreateAssistantSlug's
        # _patches()/fake-save shape (Assistant.save patched to a no-op).
        from codemie.rest_api.routers.assistant import create_assistant
        from codemie.rest_api.models.assistant import MCPServerDetails, Assistant

        request = self._request([MCPServerDetails(name="ok", command="npx", enabled=True)])
        mode = MagicMock(); mode.is_component_enabled.return_value = False
        patches = [
            patch("codemie.rest_api.routers.assistant.project_access_check"),
            patch("codemie.rest_api.routers.assistant.ensure_application_exists"),
            patch("codemie.service.assistant.assistant_version_service.AssistantVersionService.create_initial_version"),
            patch("codemie.rest_api.routers.assistant.GuardrailService.sync_guardrail_assignments_for_entity"),
            patch("codemie.rest_api.routers.assistant._track_mcp_usage_on_create"),
            patch("codemie.rest_api.routers.assistant._track_assistant_management_metric"),
        ]
        started = [p.start() for p in patches]
        try:
            with (
                patch("codemie.service.mcp.access_control.customer_config", mode),
                patch.object(Assistant, "save", new=lambda self, *a, **kw: None),
            ):
                response = create_assistant(request, user=MagicMock(spec=User, id="u1", username="u1", name="u1"))
        finally:
            for p in patches:
                p.stop()
        assert response.message == "Specified assistant saved"
```

- [ ] **Step 2: Run to verify current pass, then prove the regression guard (AC6)**

Run: `pytest tests/codemie/rest_api/routers/test_assistant.py -k TestAssistantMcpGovernance -v`
Expected: all 4 PASS immediately (enforcement already exists at both call sites).
Then temporarily comment out line 743 (`request.mcp_servers = MCPAccessControlService.sanitize_for_save(...)`) in `create_assistant`, rerun `test_create_restricted_mode_rejects_handwritten_server` and `test_create_admin_user_violating_payload_still_rejected` — expect FAIL (proves AC6 for call site 1). Restore the line. Repeat for line 862 in `update_assistant` against `test_update_restricted_mode_rejects_handwritten_server` — expect FAIL, then restore.

- [ ] **Step 3: Final run to confirm all pass with guards restored**

Run: `pytest tests/codemie/rest_api/routers/test_assistant.py -k TestAssistantMcpGovernance -v`
Expected: PASS.

- [ ] **Step 4: Commit** (test file only — no production diff in this task)

---

### Task 3: Regression coverage for `ask_virtual_assistant` (call site 3)

**Files:**
- Test: `tests/codemie/rest_api/routers/test_assistant.py`

Not persisted, but explicitly in scope per AC5 ("third assistant save path"). Follows `TestAskVirtualAssistantSingleUserInvariant`'s existing async pattern (`raw_request.state.wait_for_disconnect` mocked to a no-op coroutine).

**Test-first: yes — passes today because the call site already calls `sanitize_for_save`; the guard-removal check in Step 2 is the regression proof.**

- [ ] **Step 1: Write the test**

```python
class TestAskVirtualAssistantMcpGovernance:
    @pytest.mark.asyncio
    async def test_restricted_mode_rejects_handwritten_server(self):
        from codemie.rest_api.routers.assistant import ask_virtual_assistant, VirtualAssistantChatRequest
        from codemie.rest_api.models.assistant import MCPServerDetails
        from codemie.core.exceptions import ValidationException

        async def _noop():
            return None

        raw_request = MagicMock()
        raw_request.state.wait_for_disconnect = MagicMock(return_value=_noop())
        request = VirtualAssistantChatRequest(mcp_servers=[MCPServerDetails(name="rogue", command="npx", enabled=True)])
        mode = MagicMock(); mode.is_component_enabled.return_value = True
        user = MagicMock(spec=User); user.current_project = "demo"

        with patch("codemie.service.mcp.access_control.customer_config", mode):
            with pytest.raises(ValidationException, match="rogue"):
                await ask_virtual_assistant(raw_request, MagicMock(), request, user)

    @pytest.mark.asyncio
    async def test_open_mode_allows_handwritten_server(self):
        from codemie.rest_api.routers.assistant import ask_virtual_assistant, VirtualAssistantChatRequest
        from codemie.rest_api.models.assistant import MCPServerDetails

        async def _noop():
            return None

        raw_request = MagicMock()
        raw_request.state.wait_for_disconnect = MagicMock(return_value=_noop())
        request = VirtualAssistantChatRequest(mcp_servers=[MCPServerDetails(name="ok", command="npx", enabled=True)])
        mode = MagicMock(); mode.is_component_enabled.return_value = False
        user = MagicMock(spec=User); user.current_project = "demo"

        with (
            patch("codemie.service.mcp.access_control.customer_config", mode),
            patch("codemie.rest_api.routers.assistant.asyncio.to_thread", return_value={"response": "ok"}),
        ):
            await ask_virtual_assistant(raw_request, MagicMock(), request, user)
```

- [ ] **Step 2: Run, then prove the regression guard**

Run: `pytest tests/codemie/rest_api/routers/test_assistant.py -k TestAskVirtualAssistantMcpGovernance -v`
Expected: PASS. Then comment out line 977 (`mcp_servers = MCPAccessControlService.sanitize_for_save(request.mcp_servers)`), rerun the restricted-mode test — expect FAIL. Restore the line.

- [ ] **Step 3: Final run to confirm pass**

Run: same command. Expected: PASS.

- [ ] **Step 4: Commit**

---

### Task 4: Regression coverage for `routers/workflow.py` create/update (call sites 4–5)

**Files:**
- Test: `tests/codemie/rest_api/routers/test_workflow.py`

Uses the file's existing `AsyncClient`/`ASGITransport` + `override_auth` autouse fixture pattern (same as `test_create_workflow`/`test_update_workflow`). `create_workflow` reads MCP servers straight from `CreateWorkflowRequest.assistants`; `update_workflow` reads them only after `updated_config.parse_execution_config()` re-parses `yaml_config`, so the update test's violating server must live inside the `yaml_config` YAML string, not on `UpdateWorkflowRequest` (which has no `assistants` field).

**Test-first: yes — passes today because both call sites already call `validate_on_save`; Step 2's guard removal is the regression proof.**

- [ ] **Step 1: Write the tests**

```python
@pytest.mark.asyncio
async def test_create_workflow_restricted_mode_rejects_handwritten_mcp_server(request_header):
    from codemie.core.workflow_models import CreateWorkflowRequest, WorkflowAssistant, WorkflowMode
    from codemie.rest_api.models.assistant import MCPServerDetails

    request = CreateWorkflowRequest(
        name="Governed Workflow", description="d", project="demo", icon_url="i",
        yaml_config=test_yaml_config, mode=WorkflowMode.SEQUENTIAL,
        assistants=[WorkflowAssistant(id="a1", mcp_servers=[MCPServerDetails(name="rogue", command="npx", enabled=True)])],
        states=[],
    )
    mode = MagicMock(); mode.is_component_enabled.return_value = True
    with (
        patch("codemie.service.mcp.access_control.customer_config", mode),
        patch("codemie.rest_api.routers.workflow.project_access_check"),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post("/v1/workflows", json=request.model_dump(), headers=request_header)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "rogue" in response.json()["error"]["details"]


@pytest.mark.asyncio
async def test_update_workflow_restricted_mode_rejects_handwritten_mcp_server(request_header, workflow_config):
    from codemie.core.workflow_models import UpdateWorkflowRequest, WorkflowMode

    # yaml_config carries assistants for update — insert mcp_servers under the first assistant.
    violating_yaml = test_yaml_config.replace(
        "model: 'gpt-4o-2024-11-20'\n  - id: onboarder",
        "model: 'gpt-4o-2024-11-20'\n    mcp_servers:\n      - name: rogue\n        command: npx\n        enabled: true\n  - id: onboarder",
    )
    request = UpdateWorkflowRequest(
        name="Updated", description="d", project="demo", icon_url="i",
        yaml_config=violating_yaml, mode=WorkflowMode.SEQUENTIAL,
    )
    mode = MagicMock(); mode.is_component_enabled.return_value = True
    with (
        patch("codemie.service.workflow_service.WorkflowService.get_workflow", return_value=workflow_config),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch("codemie.service.mcp.access_control.customer_config", mode),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.put(f"/v1/workflows/{workflow_config.id}", json=request.model_dump(), headers=request_header)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "rogue" in response.json()["error"]["details"]


@pytest.mark.asyncio
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_entity_guardrail_assignments")
async def test_create_workflow_open_mode_allows_handwritten_mcp_server(mock_guardrails, request_header):
    from codemie.core.workflow_models import CreateWorkflowRequest, WorkflowAssistant, WorkflowMode
    from codemie.rest_api.models.assistant import MCPServerDetails

    mock_guardrails.return_value = None
    request = CreateWorkflowRequest(
        name="Open Workflow", description="d", project="demo", icon_url="i",
        yaml_config=test_yaml_config, mode=WorkflowMode.SEQUENTIAL,
        assistants=[WorkflowAssistant(id="a1", mcp_servers=[MCPServerDetails(name="ok", command="npx", enabled=True)])],
        states=[],
    )
    mode = MagicMock(); mode.is_component_enabled.return_value = False
    with (
        patch("codemie.service.mcp.access_control.customer_config", mode),
        patch("codemie.rest_api.routers.workflow.project_access_check"),
        patch("codemie.service.workflow_service.WorkflowService.create_workflow", return_value=workflow_config_data),
        patch("codemie.service.workflow_service.WorkflowService.save_workflow_schema"),
        patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw"),
        patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow"),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post("/v1/workflows", json=request.model_dump(), headers=request_header)
    assert response.status_code == status.HTTP_200_OK
```

- [ ] **Step 2: Run, then prove the regression guard**

Run: `pytest tests/codemie/rest_api/routers/test_workflow.py -k "mcp_server" -v`
Expected: PASS. Then comment out line 396 (`MCPAccessControlService.validate_on_save(...)` in `create_workflow`), rerun the create test — expect FAIL; restore. Repeat for line 474 in `update_workflow` against the update test; restore.

- [ ] **Step 3: Final run to confirm pass**

Run: same command. Expected: PASS.

- [ ] **Step 4: Commit**

---

### Task 5: Regression coverage for `skill_service.py` create/update (call sites 6–7)

**Files:**
- Test: `tests/codemie/service/test_skill_service.py`

Uses the file's existing `patch.object(SkillRepository, "...", ...)` pattern (see `TestCreateSkill.test_create_skill_success`) rather than a router/HTTP layer — skills have no router-level indirection for this check.

**Test-first: yes — passes today because both call sites already call `sanitize_for_save`; Step 2's guard removal is the regression proof.**

- [ ] **Step 1: Write the tests**

```python
class TestSkillMcpGovernance:
    def test_create_restricted_mode_rejects_handwritten_server(self, owner_user):
        from codemie.rest_api.models.skill import SkillCreateRequest, SkillVisibility
        from codemie.rest_api.models.assistant import MCPServerDetails
        from codemie.core.exceptions import ValidationException

        request = SkillCreateRequest(
            name="new-skill", description="d", content="Content " * 20, project="project-a",
            visibility=SkillVisibility.PRIVATE, categories=[],
            mcp_servers=[MCPServerDetails(name="rogue", command="npx", enabled=True)],
        )
        mode = MagicMock(); mode.is_component_enabled.return_value = True
        with (
            patch("codemie.service.mcp.access_control.customer_config", mode),
            patch.object(SkillRepository, "get_by_name_author_project", return_value=None),
        ):
            with pytest.raises(ValidationException, match="rogue"):
                SkillService.create_skill(request, owner_user)

    def test_create_open_mode_allows_handwritten_server(self, owner_user, sample_skill):
        from codemie.rest_api.models.skill import SkillCreateRequest, SkillVisibility
        from codemie.rest_api.models.assistant import MCPServerDetails

        request = SkillCreateRequest(
            name="new-skill", description="d", content="Content " * 20, project="project-a",
            visibility=SkillVisibility.PRIVATE, categories=[],
            mcp_servers=[MCPServerDetails(name="ok", command="npx", enabled=True)],
        )
        mode = MagicMock(); mode.is_component_enabled.return_value = False
        with (
            patch("codemie.service.mcp.access_control.customer_config", mode),
            patch.object(SkillRepository, "get_by_name_author_project", return_value=None),
            patch.object(SkillRepository, "create", return_value=sample_skill),
        ):
            result = SkillService.create_skill(request, owner_user)
        assert result is not None

    def test_update_restricted_mode_rejects_handwritten_server(self, owner_user, sample_skill):
        from codemie.rest_api.models.skill import SkillUpdateRequest
        from codemie.rest_api.models.assistant import MCPServerDetails
        from codemie.core.exceptions import ValidationException

        request = SkillUpdateRequest(mcp_servers=[MCPServerDetails(name="rogue", command="npx", enabled=True)])
        mode = MagicMock(); mode.is_component_enabled.return_value = True
        with (
            patch("codemie.service.mcp.access_control.customer_config", mode),
            patch.object(SkillRepository, "get_by_id", return_value=sample_skill),
        ):
            with pytest.raises(ValidationException, match="rogue"):
                SkillService.update_skill(sample_skill.id, request, owner_user)
```

- [ ] **Step 2: Run, then prove the regression guard**

Run: `pytest tests/codemie/service/test_skill_service.py -k TestSkillMcpGovernance -v`
Expected: PASS. Then comment out line 506 (`skill_data["mcp_servers"] = MCPAccessControlService.sanitize_for_save(...)`) in `create_skill`, rerun the create test — expect FAIL; restore. Repeat for line 649 (inside `if "mcp_servers" in updates:`) in `update_skill` against the update test; restore.

- [ ] **Step 3: Final run to confirm pass**

Run: same command. Expected: PASS.

- [ ] **Step 4: Commit**

---

## Self-Review

- **Spec coverage:** AC1/AC5/AC6 → Tasks 2–5 (one call site per group, shared violating payload shape). AC2 → already covered by existing `test_forbidden_field_config_raises` (no new task). AC3/AC4 → Task 1. AC7 → Task 2's admin test. AC8 → the open-mode variant in each of Tasks 2, 4, 5 (Task 3's virtual endpoint also gets one, since AC5 names it explicitly).
- **Placeholder scan:** no task collapses to "similar to Task N"; each test shows the actual payload, patch target, and assertion for its call site.
- **Type/signature consistency:** `MCPServerDetails(name=..., command=..., enabled=...)` and `ValidationException` match `access_control.py`'s actual constructor/exception; `create_assistant`/`update_assistant`/`ask_virtual_assistant`/`create_workflow`/`update_workflow`/`SkillService.create_skill`/`SkillService.update_skill` signatures were confirmed against the current source before drafting these tests.
