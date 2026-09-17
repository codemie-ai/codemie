# EPMCDME-14252 — Keep triggered assistant runs visible on all HTTP errors

**Date**: 2026-08-25  
**Ticket**: EPMCDME-14252  
**Scope**: `codemie` backend only — no `codemie-ui` changes

---

## Problem

When a webhook- or scheduler-triggered assistant run calls `/v1/assistants/{id}/model` and that call fails with any HTTP error (401, 5xx, connect error, timeout), the actor **deletes** the conversation it just created. The run becomes invisible in the UI. Users cannot distinguish "trigger never fired" from "trigger fired but execution failed."

Additionally, MCP 401 errors (`MCPAuthenticationRequiredException`) and broker 401 errors (`BrokerAuthRequiredException`) are re-raised bare in `_ask_assistant` without calling `_save_error`, so no error turn is persisted to chat history even when the conversation is kept.

---

## Goals

- Every triggered assistant run that fails stays visible under **Chats → Folders → job**.
- Failed runs show a clear, sanitized error message — not a blank conversation.
- MCP/broker 401 errors produce an error turn with a stable, user-facing message (no raw payload dump, no tokens).
- No regression for successful runs or manual live-chat Sign-in flows.
- No changes to `codemie-ui`, workflow actors, or the `mcp_auth_required_handler` HTTP contract.

---

## Out of Scope

- `codemie-ui` changes (sidebar rendering, polling, auth prompts on job chats).
- Workflow actor/node/execution-service changes.
- In-place OAuth re-authentication on triggered conversations.
- Changing the HTTP 401 + payload shape returned by `/model` for live-chat clients.

---

## Architecture

Three isolated changes across two source files and one new helper function. No new files, no schema migrations, no new external dependencies.

### Layers touched

| Layer | File | Change |
|---|---|---|
| Trigger actor | `src/codemie/triggers/actors/assistant.py` | Remove delete; split error handling; call `save_error_history_turn` on `RequestError` |
| Actor utilities | `src/codemie/triggers/actors/conversation.py` | Add `save_error_history_turn()` |
| REST API router | `src/codemie/rest_api/routers/assistant.py` | Add `_create_mcp_auth_error()`; insert `_save_error` before re-raise in `_ask_assistant` |

---

## Design

### 1. Actor — `assistant.py`

Replace the single `except httpx.HTTPError` block with two sub-catches. Remove `delete_conversation` from both.

```python
except httpx.RequestError as e:
    # /model never ran — no _save_error was called by the router.
    # Persist a sanitized error turn so the conversation is not blank.
    logger.error(
        'Failed to invoke assistant %s for job_id %s: %s',
        assistant_id, job_id, type(e).__name__,   # never str(e) — avoids URL leak
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
    # /model ran and returned an HTTP response.
    # _ask_assistant already called _save_error for 4xx/5xx.
    # Just log; do not delete the conversation.
    logger.error(
        'Failed to invoke assistant %s for job_id %s: HTTP %s %s',
        assistant_id, job_id, e.response.status_code, type(e).__name__,
    )
```

**Key invariant**: `delete_conversation` is never called on any error path. The conversation created in folder `job` always survives.

### 2. Actor utilities — `conversation.py`

Add one new async function that writes a single AI-role message to an existing conversation via the internal REST API. Follows the same HTTP-call pattern as all other functions in this module.

```python
async def save_error_history_turn(
    conversation_id: str,
    assistant_id: str,
    user_id: str,
    job_id: str,
    error_message: str,
    url: str = BASE_API_URL,
) -> None:
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
        # Log and swallow — same pattern as other conversation helpers.
        logger.warning(
            'Failed to save error history for conversation %s: %s',
            conversation_id, type(e).__name__,  # no str(e)
        )
```

This calls `PUT /v1/conversations/{conversation_id}/history` — the existing `upsert_conversation_history` endpoint — with a single `GeneratedMessage` entry. No new endpoint is needed.

### 3. Router — `routers/assistant.py`

#### New helper: `_create_mcp_auth_error`

Extracts only allowlisted fields from the `MCPAuthenticationRequiredException` payload. Never calls `str(exc)` or serializes the full payload.

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

#### Updated `_ask_assistant` catch blocks

```python
except MCPAuthenticationRequiredException as exc:
    _save_error(request_uuid, request, _create_mcp_auth_error(exc.payload), user, assistant)
    raise  # re-raise original — HTTP 401 + payload shape unchanged for live-chat Sign-in

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

`_ask_virtual_assistant` is **not changed** — it forces `save_history=False` and is not a webhook/scheduler path. Its existing `assert_not_called()` tests are preserved.

---

## Security

Persisted conversation history is user-visible. Treat it like an API response.

**Never persist or log on these paths:**
- Full `httpx` exception strings (`str(e)`) — these embed the full internal URL including query strings
- `str(exc.payload)` or the raw `MCPAuthenticationRequiredException` payload dict
- `Authorization`, `Cookie`, `x-api-key`, Bearer tokens, API keys
- Query strings (`token=`, `access_token=`, `client_secret=`, etc.)
- Stack traces in history text (server logs with `exc_info=True` are fine)

**Allowed in history and logs:**
- `type(e).__name__` — stable error class name (`ConnectError`, `TimeoutException`)
- `e.response.status_code` — HTTP status integer
- Allowlisted payload fields: `mcp_server_name`, `mcp_config_name`, `error_context`, `as_hostname`, `status`, `error`
- Short user-facing help text

---

## Error flow summary

| Trigger | `/model` outcome | History result after fix |
|---|---|---|
| Webhook/scheduler | `RequestError` (connect/timeout) | Actor writes sanitized error turn via `save_error_history_turn` |
| Webhook/scheduler | `HTTPStatusError` 401 (MCP expired) | `_ask_assistant` calls `_save_error` with `_create_mcp_auth_error`; actor keeps conversation |
| Webhook/scheduler | `HTTPStatusError` 5xx/422 | Existing `_save_error` in `_ask_assistant`; actor keeps conversation |
| Manual live-chat | Any 401 | HTTP 401 + original payload unchanged; `_save_error` now also writes turn (visible in history later, does not break Sign-in) |

---

## Testing

### `tests/codemie/triggers/actors/test_actor_assistant.py`

| Test | Asserts |
|---|---|
| `test_invoke_assistant_post_request_failure` (inverted) | HTTP 500 → `delete_conversation` **not** called |
| `test_invoke_assistant_http_status_error_no_delete` | HTTP 401 → `delete_conversation` not called; `save_error_history_turn` not called |
| `test_invoke_assistant_connect_error_persists_error_turn` | `ConnectError` → `delete_conversation` not called; `save_error_history_turn` called once with sanitized message |
| `test_invoke_assistant_timeout_persists_error_turn` | `TimeoutException` → same |
| `test_invoke_assistant_no_persist_when_no_conversation_id` | `ConnectError` + `create_conversation` returns `None` → `save_error_history_turn` not called |
| `test_invoke_assistant_log_does_not_leak_token` | `ConnectError` on URL with `?token=secret-token` → log contains `ConnectError`, does **not** contain `secret-token` or `token=` |
| `test_invoke_assistant_log_does_not_leak_url_on_status_error` | HTTP 500 → log contains status code, does **not** contain full URL |

### `tests/codemie/rest_api/routers/test_assistant.py`

| Test | Asserts |
|---|---|
| `test_reraises_mcp_authentication_required_without_wrapping` (inverted) | Raises same `MCPAuthenticationRequiredException` with same payload; `_save_error` called once; saved `details` contains `"401"` or `"authentication"`; text does **not** contain raw tokens or full payload dump |
| `test_virtual_assistant_reraises_mcp_authentication_required_without_wrapping` | Unchanged — `_save_error` still **not** called for virtual |
| `test_ask_assistant_broker_auth_saves_error_and_reraises` | `BrokerAuthRequiredException` → `_save_error` called once; original exception re-raised unwrapped |
| `test_manual_model_401_returns_401_payload_unchanged` | Live `/model` 401 → HTTP 401 + original payload unchanged (not wrapped to 500) |
