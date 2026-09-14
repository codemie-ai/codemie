# A2A Agent-Card Authentication Enforcement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce authentication and authorization on the A2A agent card discovery endpoint `GET /v1/a2a/assistants/{assistant_id}/.well-known/agent.json` to prevent leaking private/project assistant metadata, while explicitly allowing anonymous access to intentionally public (global/marketplace) assistants.

**Architecture:**
- Update the synchronous endpoint `get_assistant_agent_card` in `src/codemie/rest_api/routers/a2a.py` to be asynchronous (`async def`).
- Inside the endpoint, fetch the assistant by `assistant_id`. If not found, raise a standard `404 Not Found` `ExtendedHTTPException` (this behavior is preserved).
- Check if `assistant.is_global` is `True` (meaning it's an intentionally public marketplace assistant). If it is, allow the request to proceed immediately without authentication and return the agent card.
- If the assistant is NOT global (`is_global` is False or None), resolve the authenticated user by calling `user = await authenticate(request)`.
- Check if the resolved user has READ access to the assistant: `Ability(user).can(Action.READ, assistant)`. If they do not, raise an `ExtendedHTTPException` with a `403 Forbidden` code (Access Denied).
- Return the converted agent card.

**Tech Stack:** Python, FastAPI, SQLModel, pytest + pytest.mark.asyncio, unittest.mock.

**Spec:** No spec.md for this task. Requirements are based on the bug report in `EPMCDME-14367.md`. Stage 1 research: `docs/superpowers/tasks/2026-09-14-a2a-agent-card-auth/technical-analysis.md`.

---

## Acceptance criteria

- [ ] Given an anonymous request to `GET /v1/a2a/assistants/{assistant_id}/.well-known/agent.json` for a private/project assistant, when executed, then a `401 Unauthorized` response is returned.
- [ ] Given an authenticated request to `GET /v1/a2a/assistants/{assistant_id}/.well-known/agent.json` for a private/project assistant, when the caller has `READ` permission, then a `200 OK` response with the complete agent card metadata is returned.
- [ ] Given an authenticated request to `GET /v1/a2a/assistants/{assistant_id}/.well-known/agent.json` for a private/project assistant, when the caller does NOT have `READ` permission, then a `403 Forbidden` response is returned.
- [ ] Given an anonymous or authenticated request to `GET /v1/a2a/assistants/{assistant_id}/.well-known/agent.json` for an intentionally public (global/marketplace) assistant (`is_global=True`), when executed, then a `200 OK` response with the agent card is returned.
- [ ] Given a request for a non-existent assistant ID, when executed, then a `404 Not Found` response is returned (regardless of authentication status, matching the standard error prioritization).

---

### Task 1: Create Router Integration Tests in `tests/codemie/rest_api/routers/test_a2a_router.py` (Red State First)

We write a dedicated test file to cover all defined access vectors before applying implementation changes, ensuring we have a strict "test-first" validation cycle.

**Files:**
- Create: `tests/codemie/rest_api/routers/test_a2a_router.py`

**Test Cases:**
- `test_get_agent_card_anonymous_raises_401_on_private_assistant`: Anonymous client gets 401 when fetching a private assistant card.
- `test_get_agent_card_authorized_succeeds_on_private_assistant`: Authenticated client with READ access gets 200 and card metadata.
- `test_get_agent_card_unauthorized_raises_403_on_private_assistant`: Authenticated client without READ access gets 403.
- `test_get_agent_card_anonymous_succeeds_on_global_assistant`: Anonymous client gets 200 when fetching a global assistant card.
- `test_get_agent_card_missing_raises_404`: Client gets 404 for a missing assistant ID.

- [ ] **Step 1: Create the test file with failing test cases (Test-First: yes)**
- [ ] **Step 2: Run pytest to verify all test cases fail**

---

### Task 2: Implement Secure Authentication & Authorization Checks in the A2A Endpoint

Update the `GET /v1/a2a/assistants/{assistant_id}/.well-known/agent.json` endpoint to be async and apply the security policy.

**Files:**
- Modify: `src/codemie/rest_api/routers/a2a.py`

**Implementation details:**
- Change signature of `get_assistant_agent_card` to `async def get_assistant_agent_card(assistant_id: str, request: Request):`
- Fetch `assistant` using `Assistant.find_by_id(assistant_id)`. If missing, raise 404.
- If `assistant.is_global` is True, bypass auth and return `assistant_to_agent_card(assistant, request)`.
- Otherwise, resolve `user = await authenticate(request)`.
- Validate READ permission: `Ability(user).can(Action.READ, assistant)`. If False, raise 403.

- [ ] **Step 1: Apply security checks to `get_assistant_agent_card`**
- [ ] **Step 2: Run pytest on the new tests to verify all pass**
- [ ] **Step 3: Run the full linter & formatter suite (Ruff) to ensure code-quality compliance**
