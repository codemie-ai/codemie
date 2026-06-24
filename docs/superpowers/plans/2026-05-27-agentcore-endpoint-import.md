# AgentCore Runtime Endpoint Entity Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the AgentCore runtime vendor integration so the `/endpoints` list and detail endpoints expose import status, `delete_vendor_entity` correctly cleans up guardrail assignments, and all deletion logic moves from the router into per-service `unimport_entity` methods.

**Architecture:** `BedrockAgentCoreRuntimeService` gains enriched list/detail responses that include `aiRunId` and `invocationJson` for already-imported endpoints. A new `unimport_entity(entity_id, user)` abstract method on `BaseBedrockService` is implemented in all five concrete service classes; the router's `delete_vendor_entity` handler is reduced to a three-line delegate.

**Tech Stack:** Python, FastAPI, Pydantic, SQLModel (Elasticsearch backend), `unittest.mock` for tests, `pytest`

---

### Task 1: Enrich `/endpoints` list with `invocationJson` for already-imported endpoints

**Files:**
- Modify: `src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py` (method `list_importable_entities_for_main_entity`)
- Test: `tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py`:

```python
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_list_runtime_endpoints")
def test_list_importable_entities_includes_invocation_json_for_imported_endpoint(
    mock_list_endpoints,
    mock_get_existing,
    mock_get_aws_creds,
    mock_get_setting,
    mock_user,
    mock_setting,
    mock_aws_creds,
    endpoint_data,
):
    """Already-imported endpoint items include invocationJson alongside aiRunId."""
    mock_get_setting.return_value = mock_setting
    mock_get_aws_creds.return_value = mock_aws_creds
    mock_list_endpoints.return_value = (endpoint_data, None)

    existing_assistant = MagicMock()
    existing_assistant.id = "assistant-uuid-1"
    existing_assistant.bedrock_agentcore_runtime = MagicMock()
    existing_assistant.bedrock_agentcore_runtime.runtime_endpoint_id = "endpoint-1"
    existing_assistant.bedrock_agentcore_runtime.invocation_json = '{"message": "__QUERY_PLACEHOLDER__"}'
    mock_get_existing.return_value = [existing_assistant]

    result, _ = BedrockAgentCoreRuntimeService.list_importable_entities_for_main_entity(
        user=mock_user,
        main_entity_id="runtime-1",
        setting_id="setting-1",
        page=0,
        per_page=10,
    )

    imported = next(r for r in result if r["id"] == "endpoint-1")
    assert imported["aiRunId"] == "assistant-uuid-1"
    assert imported["invocationJson"] == '{"message": "__QUERY_PLACEHOLDER__"}'

    not_imported = next(r for r in result if r["id"] == "endpoint-2")
    assert "aiRunId" not in not_imported
    assert "invocationJson" not in not_imported
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/Andriy_Lukashchuk/Dev/code-assistant
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_list_importable_entities_includes_invocation_json_for_imported_endpoint -v
```

Expected: FAIL — `assert "invocationJson" not in not_imported` passes but `assert imported["invocationJson"] == ...` raises `KeyError`.

- [ ] **Step 3: Implement the fix**

In `bedrock_agentcore_runtime_service.py`, find the block inside `list_importable_entities_for_main_entity` that adds `aiRunId` and extend it:

```python
            if endpoint_id in existing_entities_map:
                assistant = existing_entities_map[endpoint_id]
                endpoint_dict["aiRunId"] = str(assistant.id)
                if assistant.bedrock_agentcore_runtime:
                    endpoint_dict["invocationJson"] = assistant.bedrock_agentcore_runtime.invocation_json
```

Replace the existing two-line block:
```python
            if endpoint_id in existing_entities_map:
                endpoint_dict["aiRunId"] = existing_entities_map[endpoint_id].id
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_list_importable_entities_includes_invocation_json_for_imported_endpoint -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py \
        tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py
git commit -m "EPMCDME-12240: Return invocationJson in /endpoints list for already-imported endpoints"
```

---

### Task 2: Enrich `get_importable_entity_detail` with import status

**Files:**
- Modify: `src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py` (method `get_importable_entity_detail`)
- Test: `tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py`

- [ ] **Step 1: Write the failing test**

```python
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_get_runtime_endpoint")
def test_get_importable_entity_detail_includes_ai_run_id_when_imported(
    mock_get_endpoint,
    mock_get_existing,
    mock_get_aws_creds,
    mock_get_setting,
    mock_user,
    mock_setting,
    mock_aws_creds,
    endpoint_detail,
):
    """Detail response includes aiRunId and invocationJson when endpoint is already imported."""
    mock_get_setting.return_value = mock_setting
    mock_get_aws_creds.return_value = mock_aws_creds
    mock_get_endpoint.return_value = endpoint_detail

    existing_assistant = MagicMock()
    existing_assistant.id = "assistant-uuid-1"
    existing_assistant.bedrock_agentcore_runtime = MagicMock()
    existing_assistant.bedrock_agentcore_runtime.runtime_endpoint_id = "endpoint-1"
    existing_assistant.bedrock_agentcore_runtime.invocation_json = '{"prompt": "__QUERY_PLACEHOLDER__"}'
    mock_get_existing.return_value = [existing_assistant]

    result = BedrockAgentCoreRuntimeService.get_importable_entity_detail(
        user=mock_user,
        main_entity_id="runtime-1",
        importable_entity_detail="Endpoint 1",
        setting_id="setting-1",
    )

    assert result["aiRunId"] == "assistant-uuid-1"
    assert result["invocationJson"] == '{"prompt": "__QUERY_PLACEHOLDER__"}'
    assert result["id"] == "endpoint-1"
    assert result["agentRuntimeEndpointArn"] == endpoint_detail["agentRuntimeEndpointArn"]


@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_for_user")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.get_setting_aws_credentials")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.Assistant.get_by_bedrock_runtime_aws_settings_id")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.BedrockAgentCoreRuntimeService._bedrock_get_runtime_endpoint")
def test_get_importable_entity_detail_no_ai_run_id_when_not_imported(
    mock_get_endpoint,
    mock_get_existing,
    mock_get_aws_creds,
    mock_get_setting,
    mock_user,
    mock_setting,
    mock_aws_creds,
    endpoint_detail,
):
    """Detail response omits aiRunId when endpoint has not been imported."""
    mock_get_setting.return_value = mock_setting
    mock_get_aws_creds.return_value = mock_aws_creds
    mock_get_endpoint.return_value = endpoint_detail
    mock_get_existing.return_value = []

    result = BedrockAgentCoreRuntimeService.get_importable_entity_detail(
        user=mock_user,
        main_entity_id="runtime-1",
        importable_entity_detail="Endpoint 1",
        setting_id="setting-1",
    )

    assert "aiRunId" not in result
    assert "invocationJson" not in result
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_get_importable_entity_detail_includes_ai_run_id_when_imported tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_get_importable_entity_detail_no_ai_run_id_when_not_imported -v
```

Expected: FAIL — `KeyError: 'aiRunId'`

- [ ] **Step 3: Implement the fix**

In `bedrock_agentcore_runtime_service.py`, replace the entire `get_importable_entity_detail` method body (after the endpoint fetch + empty-check) with the following. The existing return dict is unchanged; add the import-status lookup after building it:

```python
    @staticmethod
    @aws_service_exception_handler("Bedrock AgentCore runtimes")
    def get_importable_entity_detail(
        user: User,
        main_entity_id: str,
        importable_entity_detail: str,
        setting_id: str,
    ):
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        aws_creds = get_setting_aws_credentials(setting.id)

        endpoint_info = BedrockAgentCoreRuntimeService._bedrock_get_runtime_endpoint(
            runtime_id=main_entity_id,
            endpoint_name=importable_entity_detail,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
        )

        if not endpoint_info:
            logger.warning(
                f"Failed to retrieve endpoint information for runtime {main_entity_id}, "
                f"endpoint: {importable_entity_detail}"
            )
            return {}

        status = "PREPARED" if endpoint_info.get("status") == "READY" else "NOT_PREPARED"

        result = {
            "id": endpoint_info.get("id"),
            "name": endpoint_info.get("name"),
            "status": status,
            "description": endpoint_info.get("description"),
            "liveVersion": endpoint_info.get("liveVersion"),
            "targetVersion": endpoint_info.get("targetVersion"),
            "agentRuntimeEndpointArn": endpoint_info.get("agentRuntimeEndpointArn"),
            "agentRuntimeArn": endpoint_info.get("agentRuntimeArn"),
            "failureReason": endpoint_info.get("failureReason"),
            "createdAt": endpoint_info.get("createdAt"),
            "updatedAt": endpoint_info.get("lastUpdatedAt"),
        }

        endpoint_id = endpoint_info.get("id")
        if endpoint_id:
            existing_entities = Assistant.get_by_bedrock_runtime_aws_settings_id(str(setting.id))
            for assistant in existing_entities:
                if (
                    assistant.bedrock_agentcore_runtime
                    and assistant.bedrock_agentcore_runtime.runtime_endpoint_id == endpoint_id
                ):
                    result["aiRunId"] = str(assistant.id)
                    result["invocationJson"] = assistant.bedrock_agentcore_runtime.invocation_json
                    break

        return result
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_get_importable_entity_detail_includes_ai_run_id_when_imported tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_get_importable_entity_detail_no_ai_run_id_when_not_imported -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py \
        tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py
git commit -m "EPMCDME-12240: Return aiRunId and invocationJson in endpoint detail when already imported"
```

---

### Task 3: Add `unimport_entity` abstract method to `BaseBedrockService`

**Files:**
- Modify: `src/codemie/service/aws_bedrock/base_bedrock_service.py`

No test for an abstract method — enforcement comes from concrete subclass tests in Tasks 4–8.

- [ ] **Step 1: Add the abstract method**

In `base_bedrock_service.py`, add the following import at the top (alongside the existing `User` import):

```python
from codemie.rest_api.security.user import User  # already present
```

Then add the abstract method after `validate_remote_entity_exists_and_cleanup`:

```python
    @staticmethod
    @abstractmethod
    def unimport_entity(entity_id: str, user: User) -> None:
        pass
```

- [ ] **Step 2: Verify the file is importable**

```bash
poetry run python -c "from codemie.service.aws_bedrock.base_bedrock_service import BaseBedrockService; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/codemie/service/aws_bedrock/base_bedrock_service.py
git commit -m "EPMCDME-12240: Add unimport_entity abstract method to BaseBedrockService"
```

---

### Task 4: Implement `unimport_entity` on `BedrockAgentCoreRuntimeService`

**Files:**
- Modify: `src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py`
- Test: `tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py`

- [ ] **Step 1: Write the failing tests**

```python
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.GuardrailService.remove_guardrail_assignments_for_entity")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.Ability")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.Assistant.find_by_id")
def test_unimport_entity_agentcore_deletes_and_removes_guardrails(
    mock_find_by_id,
    mock_ability_cls,
    mock_remove_guardrails,
    mock_user,
):
    """unimport_entity deletes the assistant and removes guardrail assignments."""
    mock_assistant = MagicMock()
    mock_assistant.id = "assistant-uuid-1"
    mock_find_by_id.return_value = mock_assistant
    mock_ability_cls.return_value.can.return_value = True

    BedrockAgentCoreRuntimeService.unimport_entity("assistant-uuid-1", mock_user)

    mock_assistant.delete.assert_called_once()
    mock_remove_guardrails.assert_called_once_with(
        GuardrailEntity.ASSISTANT, "assistant-uuid-1"
    )


@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.Assistant.find_by_id")
def test_unimport_entity_agentcore_raises_404_when_not_found(mock_find_by_id, mock_user):
    """unimport_entity raises HTTP 404 when entity does not exist."""
    mock_find_by_id.return_value = None

    with pytest.raises(ExtendedHTTPException) as exc_info:
        BedrockAgentCoreRuntimeService.unimport_entity("missing-id", mock_user)

    assert exc_info.value.status_code == 404


@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.Ability")
@patch("codemie.service.aws_bedrock.bedrock_agentcore_runtime_service.Assistant.find_by_id")
def test_unimport_entity_agentcore_raises_403_when_no_permission(
    mock_find_by_id, mock_ability_cls, mock_user
):
    """unimport_entity raises HTTP 403 when user lacks DELETE permission."""
    mock_find_by_id.return_value = MagicMock()
    mock_ability_cls.return_value.can.return_value = False

    with pytest.raises(ExtendedHTTPException) as exc_info:
        BedrockAgentCoreRuntimeService.unimport_entity("assistant-uuid-1", mock_user)

    assert exc_info.value.status_code == 403
```

These tests also require updating the imports at the top of the test file:

```python
from codemie.core.ability import Ability, Action
from codemie.rest_api.models.guardrail import GuardrailEntity
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_unimport_entity_agentcore_deletes_and_removes_guardrails tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_unimport_entity_agentcore_raises_404_when_not_found tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_unimport_entity_agentcore_raises_403_when_no_permission -v
```

Expected: FAIL — `AttributeError: type object 'BedrockAgentCoreRuntimeService' has no attribute 'unimport_entity'`

- [ ] **Step 3: Add imports and implement the method**

At the top of `bedrock_agentcore_runtime_service.py`, add two imports alongside the existing ones:

```python
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
```

Then add the following static method to `BedrockAgentCoreRuntimeService` (place it after `delete_entities`):

```python
    @staticmethod
    def unimport_entity(entity_id: str, user: User) -> None:
        entity_model = Assistant.find_by_id(entity_id)
        if not entity_model:
            raise ExtendedHTTPException(
                code=404,
                message="agentcore-runtime not found",
                details=f"No agentcore-runtime found with the id '{entity_id}'.",
                help="Please check the id and ensure it is correct.",
            )
        if not Ability(user).can(Action.DELETE, entity_model):
            raise ExtendedHTTPException(
                code=403,
                message="Access denied",
                details="You do not have permission to delete this entity.",
                help="Contact your administrator if you believe this is an error.",
            )
        try:
            entity_model.delete()
            GuardrailService.remove_guardrail_assignments_for_entity(
                GuardrailEntity.ASSISTANT, str(entity_model.id)
            )
        except Exception as e:
            raise ExtendedHTTPException(
                code=500,
                message="Failed to delete entity",
                details=f"An error occurred while deleting the agentcore-runtime: {str(e)}",
                help="This is likely a temporary issue. Please try again later.",
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_unimport_entity_agentcore_deletes_and_removes_guardrails tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_unimport_entity_agentcore_raises_404_when_not_found tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py::test_unimport_entity_agentcore_raises_403_when_no_permission -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py \
        tests/codemie/service/aws_bedrock/test_bedrock_agentcore_runtime_service.py
git commit -m "EPMCDME-12240: Implement unimport_entity on BedrockAgentCoreRuntimeService"
```

---

### Task 5: Implement `unimport_entity` on `BedrockAgentService`

**Files:**
- Modify: `src/codemie/service/aws_bedrock/bedrock_agent_service.py`
- Test: `tests/codemie/service/aws_bedrock/test_bedrock_agent_service.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/service/aws_bedrock/test_bedrock_agent_service.py`:

```python
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.guardrail import GuardrailEntity


@patch("codemie.service.aws_bedrock.bedrock_agent_service.GuardrailService.remove_guardrail_assignments_for_entity")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.Ability")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.Assistant.find_by_id")
def test_unimport_entity_agent_deletes_and_removes_guardrails(
    mock_find_by_id, mock_ability_cls, mock_remove_guardrails, mock_user
):
    """unimport_entity deletes the assistant and removes guardrail assignments."""
    mock_assistant = MagicMock()
    mock_assistant.id = "assistant-uuid-1"
    mock_find_by_id.return_value = mock_assistant
    mock_ability_cls.return_value.can.return_value = True

    BedrockAgentService.unimport_entity("assistant-uuid-1", mock_user)

    mock_assistant.delete.assert_called_once()
    mock_remove_guardrails.assert_called_once_with(
        GuardrailEntity.ASSISTANT, "assistant-uuid-1"
    )


@patch("codemie.service.aws_bedrock.bedrock_agent_service.Assistant.find_by_id")
def test_unimport_entity_agent_raises_404_when_not_found(mock_find_by_id, mock_user):
    mock_find_by_id.return_value = None
    with pytest.raises(ExtendedHTTPException) as exc_info:
        BedrockAgentService.unimport_entity("missing-id", mock_user)
    assert exc_info.value.status_code == 404


@patch("codemie.service.aws_bedrock.bedrock_agent_service.Ability")
@patch("codemie.service.aws_bedrock.bedrock_agent_service.Assistant.find_by_id")
def test_unimport_entity_agent_raises_403_when_no_permission(
    mock_find_by_id, mock_ability_cls, mock_user
):
    mock_find_by_id.return_value = MagicMock()
    mock_ability_cls.return_value.can.return_value = False
    with pytest.raises(ExtendedHTTPException) as exc_info:
        BedrockAgentService.unimport_entity("assistant-uuid-1", mock_user)
    assert exc_info.value.status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_agent_service.py::test_unimport_entity_agent_deletes_and_removes_guardrails tests/codemie/service/aws_bedrock/test_bedrock_agent_service.py::test_unimport_entity_agent_raises_404_when_not_found tests/codemie/service/aws_bedrock/test_bedrock_agent_service.py::test_unimport_entity_agent_raises_403_when_no_permission -v
```

Expected: FAIL — `AttributeError: 'BedrockAgentService' has no attribute 'unimport_entity'`

- [ ] **Step 3: Add imports and implement the method**

At the top of `bedrock_agent_service.py`, add:

```python
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
```

Add the method to `BedrockAgentService` after `delete_entities`:

```python
    @staticmethod
    def unimport_entity(entity_id: str, user: User) -> None:
        entity_model = Assistant.find_by_id(entity_id)
        if not entity_model:
            raise ExtendedHTTPException(
                code=404,
                message="assistant not found",
                details=f"No assistant found with the id '{entity_id}'.",
                help="Please check the id and ensure it is correct.",
            )
        if not Ability(user).can(Action.DELETE, entity_model):
            raise ExtendedHTTPException(
                code=403,
                message="Access denied",
                details="You do not have permission to delete this entity.",
                help="Contact your administrator if you believe this is an error.",
            )
        try:
            entity_model.delete()
            GuardrailService.remove_guardrail_assignments_for_entity(
                GuardrailEntity.ASSISTANT, str(entity_model.id)
            )
        except Exception as e:
            raise ExtendedHTTPException(
                code=500,
                message="Failed to delete entity",
                details=f"An error occurred while deleting the assistant: {str(e)}",
                help="This is likely a temporary issue. Please try again later.",
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_agent_service.py::test_unimport_entity_agent_deletes_and_removes_guardrails tests/codemie/service/aws_bedrock/test_bedrock_agent_service.py::test_unimport_entity_agent_raises_404_when_not_found tests/codemie/service/aws_bedrock/test_bedrock_agent_service.py::test_unimport_entity_agent_raises_403_when_no_permission -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/aws_bedrock/bedrock_agent_service.py \
        tests/codemie/service/aws_bedrock/test_bedrock_agent_service.py
git commit -m "EPMCDME-12240: Implement unimport_entity on BedrockAgentService"
```

---

### Task 6: Implement `unimport_entity` on `BedrockKnowledgeBaseService`

**Files:**
- Modify: `src/codemie/service/aws_bedrock/bedrock_knowledge_base_service.py`
- Test: `tests/codemie/service/aws_bedrock/test_bedrock_knowledge_base_service.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/service/aws_bedrock/test_bedrock_knowledge_base_service.py`:

```python
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.aws_bedrock.bedrock_knowledge_base_service import BedrockKnowledgeBaseService


@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.Ability")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.IndexInfo.find_by_id")
def test_unimport_entity_kb_deletes_entity(mock_find_by_id, mock_ability_cls, mock_user):
    """unimport_entity deletes the IndexInfo entity."""
    mock_entity = MagicMock()
    mock_find_by_id.return_value = mock_entity
    mock_ability_cls.return_value.can.return_value = True

    BedrockKnowledgeBaseService.unimport_entity("kb-uuid-1", mock_user)

    mock_entity.delete.assert_called_once()


@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.IndexInfo.find_by_id")
def test_unimport_entity_kb_raises_404_when_not_found(mock_find_by_id, mock_user):
    mock_find_by_id.return_value = None
    with pytest.raises(ExtendedHTTPException) as exc_info:
        BedrockKnowledgeBaseService.unimport_entity("missing-id", mock_user)
    assert exc_info.value.status_code == 404


@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.Ability")
@patch("codemie.service.aws_bedrock.bedrock_knowledge_base_service.IndexInfo.find_by_id")
def test_unimport_entity_kb_raises_403_when_no_permission(
    mock_find_by_id, mock_ability_cls, mock_user
):
    mock_find_by_id.return_value = MagicMock()
    mock_ability_cls.return_value.can.return_value = False
    with pytest.raises(ExtendedHTTPException) as exc_info:
        BedrockKnowledgeBaseService.unimport_entity("kb-uuid-1", mock_user)
    assert exc_info.value.status_code == 403
```

Check whether `mock_user` fixture already exists in `test_bedrock_knowledge_base_service.py`. If not, add it:

```python
@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.id = "user-id"
    user.is_admin = False
    return user
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_knowledge_base_service.py::test_unimport_entity_kb_deletes_entity tests/codemie/service/aws_bedrock/test_bedrock_knowledge_base_service.py::test_unimport_entity_kb_raises_404_when_not_found tests/codemie/service/aws_bedrock/test_bedrock_knowledge_base_service.py::test_unimport_entity_kb_raises_403_when_no_permission -v
```

Expected: FAIL

- [ ] **Step 3: Add imports and implement the method**

At the top of `bedrock_knowledge_base_service.py`, add:

```python
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
```

Add the method to `BedrockKnowledgeBaseService` after `delete_entities`:

```python
    @staticmethod
    def unimport_entity(entity_id: str, user: User) -> None:
        entity_model = IndexInfo.find_by_id(entity_id)
        if not entity_model:
            raise ExtendedHTTPException(
                code=404,
                message="knowledgebase not found",
                details=f"No knowledgebase found with the id '{entity_id}'.",
                help="Please check the id and ensure it is correct.",
            )
        if not Ability(user).can(Action.DELETE, entity_model):
            raise ExtendedHTTPException(
                code=403,
                message="Access denied",
                details="You do not have permission to delete this entity.",
                help="Contact your administrator if you believe this is an error.",
            )
        try:
            entity_model.delete()
        except Exception as e:
            raise ExtendedHTTPException(
                code=500,
                message="Failed to delete entity",
                details=f"An error occurred while deleting the knowledgebase: {str(e)}",
                help="This is likely a temporary issue. Please try again later.",
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_knowledge_base_service.py::test_unimport_entity_kb_deletes_entity tests/codemie/service/aws_bedrock/test_bedrock_knowledge_base_service.py::test_unimport_entity_kb_raises_404_when_not_found tests/codemie/service/aws_bedrock/test_bedrock_knowledge_base_service.py::test_unimport_entity_kb_raises_403_when_no_permission -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/aws_bedrock/bedrock_knowledge_base_service.py \
        tests/codemie/service/aws_bedrock/test_bedrock_knowledge_base_service.py
git commit -m "EPMCDME-12240: Implement unimport_entity on BedrockKnowledgeBaseService"
```

---

### Task 7: Implement `unimport_entity` on `BedrockFlowService`

**Files:**
- Modify: `src/codemie/service/aws_bedrock/bedrock_flow_service.py`
- Test: `tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py`

`bedrock_flow_service.py` already has `workflow_service = WorkflowService()` at module level — use that instance.

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py`:

```python
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.aws_bedrock.bedrock_flow_service import BedrockFlowService


@patch("codemie.service.aws_bedrock.bedrock_flow_service.workflow_service")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.Ability")
def test_unimport_entity_flow_deletes_workflow(mock_ability_cls, mock_workflow_svc, mock_user):
    """unimport_entity calls delete_workflow on the found workflow."""
    mock_entity = MagicMock()
    mock_workflow_svc.get_workflow.return_value = mock_entity
    mock_ability_cls.return_value.can.return_value = True

    BedrockFlowService.unimport_entity("workflow-uuid-1", mock_user)

    mock_workflow_svc.delete_workflow.assert_called_once_with(mock_entity, mock_user)


@patch("codemie.service.aws_bedrock.bedrock_flow_service.workflow_service")
def test_unimport_entity_flow_raises_404_when_not_found(mock_workflow_svc, mock_user):
    mock_workflow_svc.get_workflow.side_effect = KeyError("not found")
    with pytest.raises(ExtendedHTTPException) as exc_info:
        BedrockFlowService.unimport_entity("missing-id", mock_user)
    assert exc_info.value.status_code == 404


@patch("codemie.service.aws_bedrock.bedrock_flow_service.workflow_service")
@patch("codemie.service.aws_bedrock.bedrock_flow_service.Ability")
def test_unimport_entity_flow_raises_403_when_no_permission(
    mock_ability_cls, mock_workflow_svc, mock_user
):
    mock_workflow_svc.get_workflow.return_value = MagicMock()
    mock_ability_cls.return_value.can.return_value = False
    with pytest.raises(ExtendedHTTPException) as exc_info:
        BedrockFlowService.unimport_entity("workflow-uuid-1", mock_user)
    assert exc_info.value.status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py::test_unimport_entity_flow_deletes_workflow tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py::test_unimport_entity_flow_raises_404_when_not_found tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py::test_unimport_entity_flow_raises_403_when_no_permission -v
```

Expected: FAIL

- [ ] **Step 3: Add imports and implement the method**

At the top of `bedrock_flow_service.py`, add:

```python
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
```

Add the method to `BedrockFlowService` after `delete_entities`:

```python
    @staticmethod
    def unimport_entity(entity_id: str, user: User) -> None:
        entity_model = None
        try:
            entity_model = workflow_service.get_workflow(workflow_id=entity_id)
        except KeyError:
            pass

        if not entity_model:
            raise ExtendedHTTPException(
                code=404,
                message="workflow not found",
                details=f"No workflow found with the id '{entity_id}'.",
                help="Please check the id and ensure it is correct.",
            )
        if not Ability(user).can(Action.DELETE, entity_model):
            raise ExtendedHTTPException(
                code=403,
                message="Access denied",
                details="You do not have permission to delete this entity.",
                help="Contact your administrator if you believe this is an error.",
            )
        try:
            workflow_service.delete_workflow(entity_model, user)
        except Exception as e:
            raise ExtendedHTTPException(
                code=500,
                message="Failed to delete entity",
                details=f"An error occurred while deleting the workflow: {str(e)}",
                help="This is likely a temporary issue. Please try again later.",
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py::test_unimport_entity_flow_deletes_workflow tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py::test_unimport_entity_flow_raises_404_when_not_found tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py::test_unimport_entity_flow_raises_403_when_no_permission -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/aws_bedrock/bedrock_flow_service.py \
        tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py
git commit -m "EPMCDME-12240: Implement unimport_entity on BedrockFlowService"
```

---

### Task 8: Implement `unimport_entity` on `BedrockGuardrailService`

**Files:**
- Modify: `src/codemie/service/aws_bedrock/bedrock_guardrail_service.py`
- Test: `tests/codemie/service/aws_bedrock/test_bedrock_guardrail_service.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/service/aws_bedrock/test_bedrock_guardrail_service.py`:

```python
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.aws_bedrock.bedrock_guardrail_service import BedrockGuardrailService


@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.GuardrailService.remove_guardrail_assignments_for_guardrail")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.Ability")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.Guardrail.find_by_id")
def test_unimport_entity_guardrail_removes_assignments_then_deletes(
    mock_find_by_id, mock_ability_cls, mock_remove_assignments, mock_user
):
    """unimport_entity removes guardrail assignments then deletes the entity."""
    mock_entity = MagicMock()
    mock_entity.id = "guardrail-uuid-1"
    mock_find_by_id.return_value = mock_entity
    mock_ability_cls.return_value.can.return_value = True

    BedrockGuardrailService.unimport_entity("guardrail-uuid-1", mock_user)

    mock_remove_assignments.assert_called_once_with("guardrail-uuid-1")
    mock_entity.delete.assert_called_once()


@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.Guardrail.find_by_id")
def test_unimport_entity_guardrail_raises_404_when_not_found(mock_find_by_id, mock_user):
    mock_find_by_id.return_value = None
    with pytest.raises(ExtendedHTTPException) as exc_info:
        BedrockGuardrailService.unimport_entity("missing-id", mock_user)
    assert exc_info.value.status_code == 404


@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.Ability")
@patch("codemie.service.aws_bedrock.bedrock_guardrail_service.Guardrail.find_by_id")
def test_unimport_entity_guardrail_raises_403_when_no_permission(
    mock_find_by_id, mock_ability_cls, mock_user
):
    mock_find_by_id.return_value = MagicMock()
    mock_ability_cls.return_value.can.return_value = False
    with pytest.raises(ExtendedHTTPException) as exc_info:
        BedrockGuardrailService.unimport_entity("guardrail-uuid-1", mock_user)
    assert exc_info.value.status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_guardrail_service.py::test_unimport_entity_guardrail_removes_assignments_then_deletes tests/codemie/service/aws_bedrock/test_bedrock_guardrail_service.py::test_unimport_entity_guardrail_raises_404_when_not_found tests/codemie/service/aws_bedrock/test_bedrock_guardrail_service.py::test_unimport_entity_guardrail_raises_403_when_no_permission -v
```

Expected: FAIL

- [ ] **Step 3: Add imports and implement the method**

At the top of `bedrock_guardrail_service.py`, add:

```python
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
```

Add the method to `BedrockGuardrailService` after `delete_entities`:

```python
    @staticmethod
    def unimport_entity(entity_id: str, user: User) -> None:
        entity_model = Guardrail.find_by_id(entity_id)
        if not entity_model:
            raise ExtendedHTTPException(
                code=404,
                message="guardrail not found",
                details=f"No guardrail found with the id '{entity_id}'.",
                help="Please check the id and ensure it is correct.",
            )
        if not Ability(user).can(Action.DELETE, entity_model):
            raise ExtendedHTTPException(
                code=403,
                message="Access denied",
                details="You do not have permission to delete this entity.",
                help="Contact your administrator if you believe this is an error.",
            )
        try:
            GuardrailService.remove_guardrail_assignments_for_guardrail(str(entity_model.id))
            entity_model.delete()
        except Exception as e:
            raise ExtendedHTTPException(
                code=500,
                message="Failed to delete entity",
                details=f"An error occurred while deleting the guardrail: {str(e)}",
                help="This is likely a temporary issue. Please try again later.",
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_guardrail_service.py::test_unimport_entity_guardrail_removes_assignments_then_deletes tests/codemie/service/aws_bedrock/test_bedrock_guardrail_service.py::test_unimport_entity_guardrail_raises_404_when_not_found tests/codemie/service/aws_bedrock/test_bedrock_guardrail_service.py::test_unimport_entity_guardrail_raises_403_when_no_permission -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/aws_bedrock/bedrock_guardrail_service.py \
        tests/codemie/service/aws_bedrock/test_bedrock_guardrail_service.py
git commit -m "EPMCDME-12240: Implement unimport_entity on BedrockGuardrailService"
```

---

### Task 9: Refactor `delete_vendor_entity` router to delegate to `unimport_entity`

**Files:**
- Modify: `src/codemie/rest_api/routers/vendor.py`

No new test file needed — the service-level tests in Tasks 4–8 cover all deletion paths. The router's job is now only routing.

- [ ] **Step 1: Replace the `delete_vendor_entity` handler body**

Open `src/codemie/rest_api/routers/vendor.py`. Replace the entire `delete_vendor_entity` function (lines 414–463) with:

```python
@router.delete(
    "/vendors/{origin}/{entity}/{entity_id}",
    status_code=status.HTTP_200_OK,
)
def delete_vendor_entity(
    origin: Vendor,
    entity: Entities,
    entity_id: str,
    user: User = Depends(authenticate),
):
    service = get_service_or_404(origin, entity)
    service.unimport_entity(entity_id, user)
    return {"success": True}
```

- [ ] **Step 2: Remove now-unused imports from the router**

Remove the following lines from the imports section of `vendor.py`:

```python
import contextlib                                          # remove
from codemie.core.ability import Ability, Action           # remove
from codemie.core.workflow_models.workflow_config import WorkflowConfig  # remove
from codemie.rest_api.models.assistant import Assistant    # remove
from codemie.rest_api.models.guardrail import Guardrail    # remove
from codemie.rest_api.models.index import IndexInfo        # remove
from codemie.rest_api.routers.utils import raise_access_denied  # remove
from codemie.service.guardrail.guardrail_service import GuardrailService  # remove
from codemie.service.workflow_service import WorkflowService  # remove
```

Also remove the module-level instance on line 56:

```python
workflow_service = WorkflowService()   # remove
```

- [ ] **Step 3: Verify no import errors**

```bash
poetry run python -c "from codemie.rest_api.routers.vendor import router; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Run the full bedrock test suite**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/ -v
```

Expected: all previously passing tests still pass, plus all new `unimport_entity` tests.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/routers/vendor.py
git commit -m "EPMCDME-12240: Refactor delete_vendor_entity to delegate to service.unimport_entity"
```
