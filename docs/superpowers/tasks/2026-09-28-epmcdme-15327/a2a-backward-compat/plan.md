# A2A Backward Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Legacy (pre-v1.0) A2A agent cards, `a2a_tasks` rows and old-shape clients degrade gracefully after the v1.0 upgrade.

**Architecture:** Read-time normalisation in `AgentCard` (before-validator) fixes every load path; a typed `ValueError` subclass replaces the bare 500; the -32600 message hints v1.0; legacy task behaviour is pinned by tests and documented. No migration.

**Tech Stack:** Pydantic v2, FastAPI, pytest.

**Spec:** `docs/superpowers/tasks/2026-09-30-a2a-backward-compat/spec.md`

Commit per task using the repository's existing convention (prefix `EPMCDME-15327:`).
Run tests in WSL: `wsl -d Ubuntu -- bash -lc 'cd /mnt/c/Users/Artur_Kuznetsov1/codemie-dev/codemie && poetry run pytest <paths> -n 0 -q -p no:cacheprovider'`.

## Global Constraints

- No Alembic/data migration; no v0.3 method translation; no `A2A-Version` server handling; no `agent.json` route; no Bedrock v0.3 support; task ownership check unchanged; `Part` shape and client unchanged.
- `negative-constraints`: all honored (Tasks below only touch `types.py`, handler error mapping, router message, tests, guide).

## Review Focus

- Card with `url` AND non-empty `supportedInterfaces`: interfaces win (Task 1).
- Card with `url` and empty `supportedInterfaces: []`: synthesised (Task 1).
- Card with empty/None `url` and no interfaces: no synthesis, typed error at use (Tasks 1, 2).
- Legacy card dict round-tripped through `PydanticType.process_result_value` (Task 1).
- Legacy task with NULL owner: not-found, not 500 (Task 3).

---

### Task 1: AgentCard legacy `url` normalisation

**Files:**
- Modify: `src/codemie/rest_api/a2a/types.py:368-383` (`AgentCard`)
- Test: `tests/codemie/rest_api/a2a/test_types.py`, `tests/codemie/rest_api/a2a/client/` (card resolver test file), `tests/codemie/rest_api/handlers/test_assistant_handlers_streaming.py:~521`

**Interfaces:**
- Produces: `AgentCard.model_validate({... "url": u})` yields `supportedInterfaces=[AgentInterface(url=u, transport="JSONRPC")]`; `url` never dumped.

Test-first: yes — `AgentCard.model_validate` of a `url`-only dict currently raises ValidationError; tests assert one JSONRPC interface (AC1), `url`+interfaces keeps interfaces (AC2), `supportedInterfaces=[]` plus `url` synthesises, no `url` and no interfaces still raises, `PydanticType(AgentCard).process_result_value(legacy_dict, dialect)` loads (AC3, `models/base.py:280`), card resolver accepts a mocked v0.3 remote card with `url` (AC4; mimic the existing resolver test's mocking), and the streaming test at ~l.521 passes unedited (AC8).

- [ ] Write the failing tests above; run scoped paths (test_types.py, resolver test, `test_assistant_handlers_streaming.py -k finalizes_stale`); expect FAIL.
- [ ] Add to `AgentCard` a `@model_validator(mode="before") @classmethod` that, for dict input with a truthy `url` and falsy `supportedInterfaces`, returns a copy with `supportedInterfaces=[{"url": url, "transport": "JSONRPC"}]` and `url` removed; non-dict input and all other cases return unchanged.
- [ ] Run the same paths; expect PASS. If the streaming test still fails, adjust only its card construction.
- [ ] Commit.

### Task 2: Typed error for missing JSON-RPC interface

**Files:**
- Modify: `src/codemie/rest_api/a2a/types.py:386-391`; `src/codemie/rest_api/handlers/assistant_handlers.py:1367` (import at :59)
- Test: `tests/codemie/rest_api/a2a/test_types.py`, `tests/codemie/rest_api/handlers/test_a2a_assistant_handler.py`

**Interfaces:**
- Produces: `class AgentCardInterfaceError(ValueError)` in `types.py`, raised by `get_jsonrpc_url` with the same message. Client `client.py:91` keeps working (still a ValueError).

Test-first: yes — `get_jsonrpc_url` on a card with no JSON-RPC interface raises `AgentCardInterfaceError` (also `pytest.raises(ValueError)`); `A2AAssistantHandler.process_request` with such a card raises `ExtendedHTTPException` with a 400 code instead of a bare ValueError (AC5; the A2A router never calls `get_jsonrpc_url`, the chat handler does).

- [ ] Write failing tests; run; expect FAIL (no such class).
- [ ] Define `AgentCardInterfaceError(ValueError)` above `get_jsonrpc_url` and raise it. In `process_request` wrap the `get_jsonrpc_url` call: catch `AgentCardInterfaceError` and raise `ExtendedHTTPException(code=status.HTTP_400_BAD_REQUEST, message=..., details=str(e), help=...)` per `.ai-run/guides/development/error-handling.md` (mirror the constructor use at `assistant_handlers.py:142`; import `status` only if absent).
- [ ] Run; expect PASS. Commit.

### Task 3: v1.0 hint on -32600 and legacy task behaviour

**Files:**
- Modify: `src/codemie/rest_api/routers/a2a.py:200-201` (`_handle_exception`)
- Test: `tests/codemie/rest_api/routers/test_a2a_router.py`, `tests/codemie/rest_api/a2a/test_types.py`, task manager test file (locate via `Grep CodemieTaskManager tests/`)

Test-first: yes — POST with `method: "message/send"` returns 400 and error code -32600 whose `message` contains "1.0" (AC6); a `Task` built from legacy JSON (`kind`-bearing parts, `sessionId`, no owner) validates (AC7); `on_get_task` and `on_cancel_task` for a task with `user_id=None`/`assistant_id=None` return `TaskNotFoundError` (ownership check at `codemie_task_manager.py:~129`, unchanged). If the last two already pass, they are characterization tests: keep them, no source change.

- [ ] Write failing tests; run; expect the -32600 message test to FAIL.
- [ ] In the `ValidationError` branch pass `message="Request payload validation error. Supported A2A protocol version is 1.0."` to `InvalidRequestError`. Update any existing test that asserts the old message.
- [ ] Run; expect PASS. Commit.

### Task 4: Document compat behaviour

**Files:**
- Modify: `.ai-run/guides/integration/a2a-integration.md`

Test-first: no — add a short "Backward compatibility" section: read-time `url` to `supportedInterfaces` normalisation (rows self-heal on save; interfaces win over `url`), `AgentCardInterfaceError` mapped to 400 in the chat handler, v0.3 method names return -32600 with a 1.0 hint (no translation, no `A2A-Version` handling, no `agent.json`), legacy `a2a_tasks` rows with NULL owner fail closed as not-found (no back-fill), Bedrock v0.3 unsupported (AC9).
