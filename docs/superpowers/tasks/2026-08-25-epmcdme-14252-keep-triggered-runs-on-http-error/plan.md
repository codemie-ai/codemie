# EPMCDME-14252 — Keep triggered assistant runs visible on all HTTP errors

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop deleting triggered assistant conversations on HTTP errors and persist a sanitized error turn so failed runs are always visible in the UI.

**Architecture:** Three targeted changes across two source files — the trigger actor splits its single `except httpx.HTTPError` into `RequestError`/`HTTPStatusError`, a new `save_error_history_turn()` helper in `conversation.py` writes an error turn via the existing `PUT /v1/conversations/{id}/history` endpoint, and `_ask_assistant` in the router adds `_save_error()` before re-raising `MCPAuthenticationRequiredException` and `BrokerAuthRequiredException`. No schema changes, no new endpoints, no new dependencies.

**Tech Stack:** Python 3.11+, httpx, pytest, pytest-asyncio, pytest-httpx, pytest-mock

## Global Constraints

- Never persist `str(e)` for httpx exceptions — use `type(e).__name__` or `e.response.status_code`.
- Never persist or log `Authorization`, `Cookie`, Bearer tokens, API keys, or query strings.
- `delete_conversation` must never be called on any error path after this change.
- The HTTP 401 + payload shape returned by `/model` must be unchanged for live-chat Sign-in flows.
- `_ask_virtual_assistant` is not modified.
- All tests use `pytest-asyncio` + `pytest-httpx` (`httpx_mock`) + `pytest-mock` (`mocker`).
- Commit prefix: `EPMCDME-14252:`.

---

### Task 1: Add `save_error_history_turn()` to conversation.py

**Test-first: yes — test that the function calls PUT /v1/conversations/{id}/history with correct payload and auth headers**

**Files:**
- Modify: `src/codemie/triggers/actors/conversation.py`
- Test: `tests/codemie/triggers/actors/test_actor_conversation.py` (create if not present)

**Interfaces:**
- Produces: `save_error_history_turn(conversation_id: str, assistant_id: str, user_id: str, job_id: str, error_message: str, url: str = BASE_API_URL) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/triggers/actors/test_actor_conversation.py` (or add to existing file if it exists):

```python
import pytest
from unittest.mock import AsyncMock

from codemie.triggers.actors.conversation import save_error_history_turn

_URL = 'http://mockserver:8080'
_CONV_ID = 'conv-id'
_ASST_ID = 'asst-id'
_USER_ID = 'user-id'
_JOB_ID = 'job-id'

_MOCK_SIGN_HEADERS = {
    'X-Bind-Key': 'mock-sig',
    'X-Bind-Nonce': 'mock-nonce',
    'X-Bind-Timestamp': '1000000000',
    'user-id': _USER_ID,
}


@pytest.mark.asyncio
async def test_save_error_history_turn_calls_put(httpx_mock, mocker):
    mocker.patch(
        'codemie.triggers.actors.conversation.sign_internal_request',
        return_value=_MOCK_SIGN_HEADERS,
    )
    httpx_mock.add_response(
        method='PUT',
        url=f'{_URL}/v1/conversations/{_CONV_ID}/history',
        status_code=200,
    )

    await save_error_history_turn(
        conversation_id=_CONV_ID,
        assistant_id=_ASST_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        error_message='Failed to invoke assistant: ConnectError.',
        url=_URL,
    )

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].method == 'PUT'
    assert str(requests[0].url) == f'{_URL}/v1/conversations/{_CONV_ID}/history'
    import json
    body = json.loads(requests[0].content)
    assert body['assistant_id'] == _ASST_ID
    assert len(body['history']) == 1
    assert body['history'][0]['role'] == 'assistant'
    assert 'ConnectError' in body['history'][0]['message']


@pytest.mark.asyncio
async def test_save_error_history_turn_swallows_http_error(httpx_mock, mocker):
    mocker.patch(
        'codemie.triggers.actors.conversation.sign_internal_request',
        return_value=_MOCK_SIGN_HEADERS,
    )
    httpx_mock.add_response(
        method='PUT',
        url=f'{_URL}/v1/conversations/{_CONV_ID}/history',
        status_code=500,
    )

    # Must not raise — same swallow pattern as other conversation helpers
    await save_error_history_turn(
        conversation_id=_CONV_ID,
        assistant_id=_ASST_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        error_message='some error',
        url=_URL,
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/triggers/actors/test_actor_conversation.py -v
```

Expected: `ImportError: cannot import name 'save_error_history_turn'`

- [ ] **Step 3: Implement `save_error_history_turn` in `conversation.py`**

Add after the `delete_conversation` function at line ~112 in `src/codemie/triggers/actors/conversation.py`:

```python
async def save_error_history_turn(
    conversation_id: str,
    assistant_id: str,
    user_id: str,
    job_id: str,
    error_message: str,
    url: str = BASE_API_URL,
) -> None:
    """Persist a single assistant-role error message onto an existing conversation."""
    headers = {
        'Content-Type': CONTENT_TYPE_JSON,
        **sign_internal_request(user_id),
    }
    data = {
        'assistant_id': assistant_id,
        'history': [{'role': 'assistant', 'message': error_message}],
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(
                url=f'{url.rstrip("/")}/v1/conversations/{conversation_id}/history',
                headers=headers,
                json=data,
                timeout=60,
            )
            response.raise_for_status()
            logger.info(
                'Saved error history turn for conversation %s, job_id %s',
                conversation_id, job_id,
            )
    except httpx.HTTPError as e:
        logger.warning(
            'Failed to save error history for conversation %s: %s',
            conversation_id, type(e).__name__,
        )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/codemie/triggers/actors/test_actor_conversation.py -v
```

Expected: `2 passed`

- [ ] **Step 5: Run ruff**

```bash
make ruff
```

Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/codemie/triggers/actors/conversation.py tests/codemie/triggers/actors/test_actor_conversation.py
git commit -m "EPMCDME-14252: Add save_error_history_turn actor utility"
```

---

### Task 2: Update actor tests (TDD red phase for Task 3)

**Test-first: yes — write failing tests that assert the new behavior before changing assistant.py**

**Files:**
- Modify: `tests/codemie/triggers/actors/test_actor_assistant.py`

**Interfaces:**
- Consumes: `save_error_history_turn(conversation_id, assistant_id, user_id, job_id, error_message, url)` from Task 1
- Consumes: `invoke_assistant(assistant_id, user_id, job_id, task, url, trigger_source)` — existing

- [ ] **Step 1: Invert `test_invoke_assistant_post_request_failure`**

Replace lines 97–116 (the existing test that currently passes by asserting `delete_conversation` is called on 500):

```python
@pytest.mark.asyncio
async def test_invoke_assistant_post_request_failure(httpx_mock, mocker):
    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=_CONVERSATION_ID,
    )
    mock_delete = mocker.patch('codemie.triggers.actors.assistant.delete_conversation', new_callable=AsyncMock)
    httpx_mock.add_response(method='POST', url=_POST_URL, status_code=500)

    await invoke_assistant(
        assistant_id=_ASSISTANT_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        task='Do a task',
        url=_ASSISTANT_URL,
    )

    mock_delete.assert_not_called()
```

- [ ] **Step 2: Add new actor tests after the existing ones**

Append these tests to the file:

```python
@pytest.mark.asyncio
async def test_invoke_assistant_http_status_error_no_delete(httpx_mock, mocker):
    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=_CONVERSATION_ID,
    )
    mock_delete = mocker.patch('codemie.triggers.actors.assistant.delete_conversation', new_callable=AsyncMock)
    mock_save_turn = mocker.patch(
        'codemie.triggers.actors.assistant.save_error_history_turn', new_callable=AsyncMock
    )
    httpx_mock.add_response(method='POST', url=_POST_URL, status_code=401)

    await invoke_assistant(
        assistant_id=_ASSISTANT_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        task='Do a task',
        url=_ASSISTANT_URL,
    )

    mock_delete.assert_not_called()
    mock_save_turn.assert_not_called()


@pytest.mark.asyncio
async def test_invoke_assistant_connect_error_persists_error_turn(httpx_mock, mocker):
    import httpx as httpx_lib

    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=_CONVERSATION_ID,
    )
    mock_delete = mocker.patch('codemie.triggers.actors.assistant.delete_conversation', new_callable=AsyncMock)
    mock_save_turn = mocker.patch(
        'codemie.triggers.actors.assistant.save_error_history_turn', new_callable=AsyncMock
    )
    httpx_mock.add_exception(httpx_lib.ConnectError('connection refused'), url=_POST_URL, method='POST')

    await invoke_assistant(
        assistant_id=_ASSISTANT_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        task='Do a task',
        url=_ASSISTANT_URL,
    )

    mock_delete.assert_not_called()
    mock_save_turn.assert_called_once()
    call_kwargs = mock_save_turn.call_args.kwargs
    assert call_kwargs['conversation_id'] == _CONVERSATION_ID
    assert call_kwargs['assistant_id'] == _ASSISTANT_ID
    assert 'ConnectError' in call_kwargs['error_message']
    assert 'secret' not in call_kwargs['error_message'].lower()
    assert 'token=' not in call_kwargs['error_message']


@pytest.mark.asyncio
async def test_invoke_assistant_timeout_persists_error_turn(httpx_mock, mocker):
    import httpx as httpx_lib

    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=_CONVERSATION_ID,
    )
    mock_delete = mocker.patch('codemie.triggers.actors.assistant.delete_conversation', new_callable=AsyncMock)
    mock_save_turn = mocker.patch(
        'codemie.triggers.actors.assistant.save_error_history_turn', new_callable=AsyncMock
    )
    httpx_mock.add_exception(httpx_lib.TimeoutException('timed out'), url=_POST_URL, method='POST')

    await invoke_assistant(
        assistant_id=_ASSISTANT_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        task='Do a task',
        url=_ASSISTANT_URL,
    )

    mock_delete.assert_not_called()
    mock_save_turn.assert_called_once()
    call_kwargs = mock_save_turn.call_args.kwargs
    assert 'TimeoutException' in call_kwargs['error_message']


@pytest.mark.asyncio
async def test_invoke_assistant_no_persist_when_no_conversation_id(httpx_mock, mocker):
    import httpx as httpx_lib

    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=None,
    )
    mock_delete = mocker.patch('codemie.triggers.actors.assistant.delete_conversation', new_callable=AsyncMock)
    mock_save_turn = mocker.patch(
        'codemie.triggers.actors.assistant.save_error_history_turn', new_callable=AsyncMock
    )
    httpx_mock.add_exception(httpx_lib.ConnectError('connection refused'), url=_POST_URL, method='POST')

    await invoke_assistant(
        assistant_id=_ASSISTANT_ID,
        user_id=_USER_ID,
        job_id=_JOB_ID,
        task='Do a task',
        url=_ASSISTANT_URL,
    )

    mock_delete.assert_not_called()
    mock_save_turn.assert_not_called()


@pytest.mark.asyncio
async def test_invoke_assistant_log_does_not_leak_token(httpx_mock, mocker, caplog):
    import httpx as httpx_lib
    import logging

    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=_CONVERSATION_ID,
    )
    mocker.patch('codemie.triggers.actors.assistant.save_error_history_turn', new_callable=AsyncMock)
    httpx_mock.add_exception(httpx_lib.ConnectError('connection refused'), url=_POST_URL, method='POST')

    with caplog.at_level(logging.ERROR, logger='codemie'):
        await invoke_assistant(
            assistant_id=_ASSISTANT_ID,
            user_id=_USER_ID,
            job_id=_JOB_ID,
            task='Do a task',
            url=_ASSISTANT_URL,
        )

    log_text = ' '.join(caplog.messages)
    assert 'ConnectError' in log_text
    assert 'secret-token' not in log_text
    assert 'token=' not in log_text


@pytest.mark.asyncio
async def test_invoke_assistant_log_does_not_leak_url_on_status_error(httpx_mock, mocker, caplog):
    import logging

    mocker.patch('codemie.triggers.actors.assistant.sign_internal_request', return_value=_MOCK_SIGN_HEADERS)
    mocker.patch(
        'codemie.triggers.actors.assistant.create_conversation',
        new_callable=AsyncMock,
        return_value=_CONVERSATION_ID,
    )
    mocker.patch('codemie.triggers.actors.assistant.save_error_history_turn', new_callable=AsyncMock)
    httpx_mock.add_response(method='POST', url=_POST_URL, status_code=500)

    with caplog.at_level(logging.ERROR, logger='codemie'):
        await invoke_assistant(
            assistant_id=_ASSISTANT_ID,
            user_id=_USER_ID,
            job_id=_JOB_ID,
            task='Do a task',
            url=_ASSISTANT_URL,
        )

    log_text = ' '.join(caplog.messages)
    assert '500' in log_text
    # str(httpx.HTTPStatusError) embeds the full URL; our fix should not log it
    assert 'http://mockserver' not in log_text
```

- [ ] **Step 3: Run tests to verify they fail (red phase)**

```bash
poetry run pytest tests/codemie/triggers/actors/test_actor_assistant.py -v
```

Expected failures:
- `test_invoke_assistant_post_request_failure` — now inverted, still PASSES (behavior unchanged, assertion inverted; it will PASS because `delete_conversation` is still being called)

Wait — actually this test will FAIL now because:
- The inverted test asserts `mock_delete.assert_not_called()` but the existing code DOES call `delete_conversation` on 500.
- The new tests that mock `save_error_history_turn` will fail with `NameError`/import error until `assistant.py` imports it.

Expected outcome: `test_invoke_assistant_post_request_failure` FAILS (asserts not_called but code calls it); new tests for `connect_error` etc fail with `AttributeError` on mock.

- [ ] **Step 4: Commit (red phase only — tests intentionally failing)**

```bash
git add tests/codemie/triggers/actors/test_actor_assistant.py
git commit -m "EPMCDME-14252: Update actor tests (TDD red phase)"
```

---

### Task 3: Fix actor — split error handling, remove delete, add error turn

**Test-first: yes (tests written in Task 2)**

**Files:**
- Modify: `src/codemie/triggers/actors/assistant.py`

**Interfaces:**
- Consumes: `save_error_history_turn` from Task 1 (imported from `codemie.triggers.actors.conversation`)

- [ ] **Step 1: Update the import in `assistant.py`**

Replace line 23:
```python
from codemie.triggers.actors.conversation import create_conversation, delete_conversation
```
with:
```python
from codemie.triggers.actors.conversation import create_conversation, save_error_history_turn
```

- [ ] **Step 2: Replace the `except httpx.HTTPError` block**

Replace lines 66–69 in `src/codemie/triggers/actors/assistant.py`:

```python
    except httpx.HTTPError as e:
        logger.error('Failed to invoke assistant %s for job_id %s: %s', assistant_id, job_id, str(e))
        if created_conversation_id:
            await delete_conversation(created_conversation_id, user_id, job_id, url)
```

with:

```python
    except httpx.RequestError as e:
        logger.error(
            'Failed to invoke assistant %s for job_id %s: %s',
            assistant_id, job_id, type(e).__name__,
        )
        if created_conversation_id:
            await save_error_history_turn(
                conversation_id=created_conversation_id,
                assistant_id=assistant_id,
                user_id=user_id,
                job_id=job_id,
                error_message=(
                    f"Failed to invoke assistant: {type(e).__name__}. "
                    "Refresh or fix the MCP token/configuration, then re-run or "
                    "wait for the next webhook/scheduler execution."
                ),
                url=url,
            )
    except httpx.HTTPStatusError as e:
        logger.error(
            'Failed to invoke assistant %s for job_id %s: HTTP %s %s',
            assistant_id, job_id, e.response.status_code, type(e).__name__,
        )
```

The `delete_conversation` import is now unused — it was removed in Step 1. The `uuid` import at line 15 is still used for the fallback `conversation_id`.

- [ ] **Step 3: Run actor tests to verify they pass (green phase)**

```bash
poetry run pytest tests/codemie/triggers/actors/test_actor_assistant.py -v
```

Expected: all tests pass including the newly added ones.

- [ ] **Step 4: Run ruff**

```bash
make ruff
```

Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add src/codemie/triggers/actors/assistant.py
git commit -m "EPMCDME-14252: Stop deleting triggered conversations on HTTP error; persist sanitized error turn for RequestError"
```

---

### Task 4: Add `_create_mcp_auth_error` and fix `_ask_assistant` catches in router

**Test-first: yes — update router tests first, then implement**

**Files:**
- Modify: `tests/codemie/rest_api/routers/test_assistant.py`
- Modify: `src/codemie/rest_api/routers/assistant.py`

**Interfaces:**
- Produces: `_create_mcp_auth_error(payload: dict) -> ExtendedHTTPException` — new private helper in `routers/assistant.py`

- [ ] **Step 1: Update the existing router test `test_reraises_mcp_authentication_required_without_wrapping`**

The existing test (line ~266) has `mock_save_error.assert_not_called()`. Invert it:

```python
    @patch("codemie.rest_api.routers.assistant._save_error")
    @patch("codemie.rest_api.routers.assistant.request_summary_manager.create_request_summary")
    @patch("codemie.rest_api.routers.assistant.assistant_user_interaction_service.record_usage")
    @patch("codemie.rest_api.routers.assistant.get_request_handler")
    def test_reraises_mcp_authentication_required_without_wrapping(
        self,
        mock_get_handler,
        mock_record_usage,
        mock_request_summary,
        mock_save_error,
        mock_user,
        mock_assistant,
    ):
        auth_payload = {
            "error": "authentication_required",
            "servers": [
                {
                    "mcp_config_id": "mcp-1",
                    "mcp_config_name": "GitHub",
                    "mcp_server_name": "GitHub",
                    "auth_config_id": "auth-1",
                    "auth_type": "oauth2",
                    "as_hostname": "login.example.com",
                    "status": "authentication_required",
                    "error_context": None,
                    "initiate_url": "/v1/mcp-auth/oauth2/initiate",
                }
            ],
        }
        auth_error = MCPAuthenticationRequiredException(auth_payload)
        mock_handler = MagicMock()
        mock_handler.process_request.side_effect = auth_error
        mock_get_handler.return_value = mock_handler

        request = AssistantChatRequest(text=None)
        raw_request = MagicMock()
        raw_request.state.uuid = "test-uuid"
        background_tasks = MagicMock()

        with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
            _ask_assistant(mock_assistant, raw_request, request, mock_user, background_tasks)

        assert exc_info.value.payload == auth_payload
        mock_save_error.assert_called_once()
        saved_exception = mock_save_error.call_args[0][2]
        assert "401" in saved_exception.details or "authentication" in saved_exception.details.lower()
        assert "mcp_config_id" not in saved_exception.details
        assert "auth_config_id" not in saved_exception.details
        assert "initiate_url" not in saved_exception.details
        assert "GitHub" in saved_exception.details  # mcp_server_name is allowlisted
```

- [ ] **Step 2: Add test for BrokerAuthRequiredException**

In the same test class, add after the MCP test:

```python
    @patch("codemie.rest_api.routers.assistant._save_error")
    @patch("codemie.rest_api.routers.assistant.request_summary_manager.create_request_summary")
    @patch("codemie.rest_api.routers.assistant.assistant_user_interaction_service.record_usage")
    @patch("codemie.rest_api.routers.assistant.get_request_handler")
    def test_ask_assistant_broker_auth_saves_error_and_reraises(
        self,
        mock_get_handler,
        mock_record_usage,
        mock_request_summary,
        mock_save_error,
        mock_user,
        mock_assistant,
    ):
        from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException

        broker_error = BrokerAuthRequiredException(
            message="Broker 401",
            auth_location="https://broker.example.com/authorize",
            details="token expired",
        )
        mock_handler = MagicMock()
        mock_handler.process_request.side_effect = broker_error
        mock_get_handler.return_value = mock_handler

        request = AssistantChatRequest(text=None)
        raw_request = MagicMock()
        raw_request.state.uuid = "test-uuid"
        background_tasks = MagicMock()

        with pytest.raises(BrokerAuthRequiredException):
            _ask_assistant(mock_assistant, raw_request, request, mock_user, background_tasks)

        mock_save_error.assert_called_once()
        saved_exception = mock_save_error.call_args[0][2]
        assert "broker" in saved_exception.message.lower() or "authentication" in saved_exception.message.lower()
        assert "https://broker.example.com" not in saved_exception.details
        assert "authorize" not in saved_exception.details
```

- [ ] **Step 3: Run the router tests to verify they fail (red phase)**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_assistant.py::TestAskAssistant::test_reraises_mcp_authentication_required_without_wrapping tests/codemie/rest_api/routers/test_assistant.py::TestAskAssistant::test_ask_assistant_broker_auth_saves_error_and_reraises -v
```

Expected:
- `test_reraises_mcp_authentication_required_without_wrapping` — FAILS: `AssertionError: Expected '_save_error' to have been called once. Called 0 times.`
- `test_ask_assistant_broker_auth_saves_error_and_reraises` — FAILS: `AssertionError`

- [ ] **Step 4: Add `_create_mcp_auth_error` helper to `routers/assistant.py`**

Add after `_create_assistant_error` (after line 2153):

```python
def _create_mcp_auth_error(payload: dict) -> ExtendedHTTPException:
    servers = payload.get("servers") or []
    server_names = ", ".join(
        s.get("mcp_server_name") or s.get("mcp_config_name") or ""
        for s in servers
        if s.get("mcp_server_name") or s.get("mcp_config_name")
    ) or "unknown"
    error_contexts = ", ".join(
        s["error_context"] for s in servers if s.get("error_context")
    )
    details = (
        f"MCP request failed due to 401 Unauthorized or expired/invalid authentication. "
        f"Server(s): {server_names}"
    )
    if error_contexts:
        details += f". Context: {error_contexts}"
    return ExtendedHTTPException(
        code=status.HTTP_401_UNAUTHORIZED,
        message="MCP authentication required",
        details=details,
        help=(
            "Refresh or fix the MCP token/configuration, then re-run or "
            "wait for the next webhook/scheduler execution."
        ),
    )
```

- [ ] **Step 5: Update `_ask_assistant` catch blocks**

Replace lines 2326–2329 in `src/codemie/rest_api/routers/assistant.py`:

```python
    except MCPAuthenticationRequiredException:
        raise
    except BrokerAuthRequiredException:
        raise
```

with:

```python
    except MCPAuthenticationRequiredException as exc:
        _save_error(request_uuid, request, _create_mcp_auth_error(exc.payload), user, assistant)
        raise
    except BrokerAuthRequiredException:
        _save_error(
            request_uuid, request,
            _create_assistant_error(
                "Broker authentication required",
                "Authentication with the configured broker failed (401 Unauthorized).",
                "Refresh or fix the broker token/configuration, then re-run or "
                "wait for the next trigger.",
            ),
            user, assistant,
        )
        raise
```

- [ ] **Step 6: Run router tests to verify they pass (green phase)**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_assistant.py -v
```

Expected: all tests pass including:
- `test_reraises_mcp_authentication_required_without_wrapping` — PASSES
- `test_virtual_assistant_reraises_mcp_authentication_required_without_wrapping` — still PASSES (virtual unchanged)
- `test_ask_assistant_broker_auth_saves_error_and_reraises` — PASSES

- [ ] **Step 7: Run full test suite**

```bash
poetry run pytest tests/ -v
```

Expected: all tests pass; no regressions.

- [ ] **Step 8: Run ruff**

```bash
make ruff
```

Expected: no errors

- [ ] **Step 9: Commit**

```bash
git add src/codemie/rest_api/routers/assistant.py tests/codemie/rest_api/routers/test_assistant.py
git commit -m "EPMCDME-14252: Persist error turn for MCP/broker 401 before re-raise in _ask_assistant"
```
