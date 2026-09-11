# Remove billing_user Injection for Teams Billing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold the Teams-header + service-account "billing_user" swap into `authenticate()` itself, so a single resolved `User` drives both access control and billing/usage everywhere downstream — removing the separate `billing_user` concept end to end.

**Architecture:** Add an isolated `TeamsSenderResolver`-style helper (own module, mirrors the `UserProvider` separation-of-concerns pattern) that `authenticate()` calls into as a third branch, after the existing bind-key/external resolution, when the already-authenticated caller is the allow-listed Teams service account and the `X-Teams-Sender-Email` header is present and the feature flag is on. It reuses `AuthenticationService._build_security_user`/`_finalize_authentication` (the existing user-loading path) instead of duplicating a DB-to-User rebuild, and raises an `ExtendedHTTPException` (401) on an unresolvable sender email — which `authenticate()`'s existing except-clause already re-raises verbatim. `billing_user_resolver.py` is deleted; `billing_user` parameters are stripped from the router and handler chain, leaving one `user` used uniformly.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy async session, pytest + pytest.mark.asyncio/anyio, unittest.mock.

**Spec:** No spec.md for this task — requirements arrived as inline text from the caller (reproduced in Acceptance criteria below). Stage 1 research: `docs/superpowers/tasks/2026-09-10-remove-the-billing-user-injection/technical-analysis.md`.

**Commit per task using the repository's existing convention.**

## Global Constraints

- Teams sender-email resolution failure must raise `ExtendedHTTPException` with a 401 (not today's 404) from inside `authenticate()`. This is an intentional, documented behavior break for the `codemie-teams-bot` consumer — no backwards-compat shim.
- Do not add HMAC/signature verification for the Teams header — that pre-existing gap (header trusted verbatim once the caller matches the allow-listed service account) is explicitly out of scope; note it, don't fix it.
- The `teamsBotIntegration` feature flag (`TEAMS_BOT_INTEGRATION_FEATURE`) and `config.TEAMS_SERVICE_ACCOUNT_ID` allow-list check must keep gating the swap exactly as today (flag off, no header, or non-allow-listed caller → passthrough unchanged).
- No task may run the whole test/lint/build suite, browser verification, or commit as a separate step — commits happen per task per the repo's own convention.

---

## Acceptance criteria

- [ ] When a request carries the `X-Teams-Sender-Email` header, is made by the allow-listed Teams service account, and the `teamsBotIntegration` flag is on, `authenticate()` returns a single `User` resolved from the header's sender email — used for both access control and billing/usage in every downstream call.
- [ ] When the header is absent, the flag is off, or the caller is not the allow-listed service account, `authenticate()` returns the originally authenticated caller unchanged (today's passthrough behavior is preserved).
- [ ] When the sender email cannot be resolved to an active, non-deleted, non-service-account user, `authenticate()` raises an `ExtendedHTTPException` with a 401 code — not the old 404 from `get_billing_user`.
- [ ] `billing_user_resolver.py` and the `get_billing_user` dependency no longer exist; no router or handler code references `billing_user`.
- [ ] `_ask_assistant`, `_ask_virtual_assistant`, `get_request_handler`, `AssistantRequestHandler.__init__` (and its stream/background/sync/save_chat_history methods), `A2AAssistantHandler`, `HedgedAssistantHandler`, `AssistantService.build_agent`, and `set_llm_context` all use the single `user` from `authenticate()`; none take or thread a `billing_user` parameter.
- [ ] `resume_tool_call`/`_ask_assistant_resume` are confirmed unaffected by the `authenticate()` change (no `billing_user` there before or after).
- [ ] `TestAskAssistantBillingUserSwap` and `TestAskVirtualAssistantBillingUserSwap` are rewritten to assert the single-user invariant (or removed with equivalent coverage moved to the `authenticate()` layer); `test_billing_user_resolver.py` is removed and replaced by tests covering the new `authenticate()` Teams branch (swap success, three passthrough cases, resolution-failure 401).
- [ ] The pre-existing header-trust/no-signature-verification gap is noted as a known, unresolved, out-of-scope issue somewhere in the change (comment/docstring), and no signature verification is added.

---

### Task 1: Add `AuthenticationService.authenticate_teams_sender` — reuse the existing user-loading path

**Files:**
- Modify: `src/codemie/service/user/authentication_service.py` (add new static method near `authenticate_persistent_user`, ~line 515)
- Test: `tests/codemie/service/user/test_authentication_service.py` (add new test class; create the file if it doesn't already exist as this exact path — check first)

**Interfaces:**
- Consumes: `AuthenticationService._build_security_user(db_user, auth_token=None)` (existing, line 344), `AuthenticationService._finalize_authentication(security_user_ins, auth_source)` (existing, async, line 376), `user_repository.aget_by_email(session, email)` (existing, `repository/user_repository.py:578`), `codemie.clients.postgres.get_async_session`.
- Produces: `AuthenticationService.authenticate_teams_sender(sender_email: str) -> User` (async staticmethod) — raises `ExtendedHTTPException(code=401, ...)` when `sender_email` doesn't match an active, non-deleted, non-service-account user. Later tasks call this exact name/signature.

Reject the same three cases `BillingUserResolver._get_billable_db_user` rejected (no match; inactive/deleted; service-account match), but with a 401 `ExtendedHTTPException` instead of 404.

- [ ] **Step 1: Write the failing test**

```python
# tests/codemie/service/user/test_authentication_service.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.user.authentication_service import AuthenticationService


@pytest.mark.asyncio
@patch("codemie.service.user.authentication_service.get_async_session")
@patch("codemie.service.user.authentication_service.user_repository")
async def test_authenticate_teams_sender_raises_401_when_email_unmatched(mock_user_repo, mock_get_session):
    mock_get_session.return_value.__aenter__.return_value = MagicMock()
    mock_user_repo.aget_by_email = AsyncMock(return_value=None)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        await AuthenticationService.authenticate_teams_sender("unknown@example.com")

    assert exc_info.value.code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/service/user/test_authentication_service.py::test_authenticate_teams_sender_raises_401_when_email_unmatched -v`
Expected: FAIL — `AttributeError: type object 'AuthenticationService' has no attribute 'authenticate_teams_sender'`

- [ ] **Step 3: Implement `authenticate_teams_sender`**

```python
@staticmethod
async def authenticate_teams_sender(sender_email: str) -> security_user.User:
    """Load the Teams end-user identified by sender_email as a full security.User.

    Reuses the same build/finalize path as authenticate_persistent_user instead of
    duplicating a DB-to-User rebuild. Raises 401 (not 404) on failure so the error
    surfaces uniformly through authenticate()'s own exception handling.
    """
    from codemie.clients.postgres import get_async_session

    async with get_async_session() as session:
        db_user = await user_repository.aget_by_email(session, sender_email.lower())

        if (
            db_user is None
            or not db_user.is_active
            or db_user.deleted_at is not None
            or db_user.user_type == "service_account"
        ):
            raise ExtendedHTTPException(
                code=401,
                message="Authentication failed",
                details=f"sender_email={sender_email!r} does not resolve to a billable CodeMie user.",
            )

        security_user_ins = AuthenticationService._build_security_user(db_user)

    return await AuthenticationService._finalize_authentication(security_user_ins, "teams_sender")
```

- [ ] **Step 4: Run test to verify it passes, then add the remaining cases (match/inactive/deleted/service-account/success) to the same test class**

Add four more tests mirroring `test_billing_user_resolver.py`'s existing coverage (inactive, deleted, service-account-match, successful resolution returning a `User` with `project_names`/`admin_project_names` populated via a mocked `_finalize_authentication`), each patching `user_repository.aget_by_email` and, for the success case, `AuthenticationService._finalize_authentication`.

Run: `poetry run pytest tests/codemie/service/user/test_authentication_service.py -v`
Expected: PASS (all new tests)

- [ ] **Step 5: Commit**

---

### Task 2: Add the isolated Teams-sender-resolution helper

**Files:**
- Create: `src/codemie/rest_api/security/teams_sender_resolver.py`
- Test: `tests/codemie/rest_api/security/test_teams_sender_resolver.py`

**Interfaces:**
- Consumes: `AuthenticationService.authenticate_teams_sender` (Task 1), `codemie.configs.config.TEAMS_SERVICE_ACCOUNT_ID`, `codemie.configs.customer_config.customer_config.is_feature_enabled`, `codemie.service.settings.settings_request_validator.TEAMS_BOT_INTEGRATION_FEATURE`, `codemie.rest_api.security.user.User`.
- Produces: `TEAMS_SENDER_EMAIL_HEADER = "X-Teams-Sender-Email"` constant; `async def resolve_teams_sender(caller: User, raw_request: Request) -> User` — returns `caller` unchanged unless the flag is on, the header is present, AND `caller.user_type == "service_account"` and `caller.id == config.TEAMS_SERVICE_ACCOUNT_ID`; otherwise delegates to `AuthenticationService.authenticate_teams_sender(sender_email)`. `authenticate()` (Task 3) calls this exact name/signature.

This is the "own separate/isolated component" the requirements call for — a plain async function is enough (mirrors `UserProvider`'s one-method-protocol simplicity); no class is required since there is exactly one caller and no state.

- [ ] **Step 1: Write the failing test**

```python
# tests/codemie/rest_api/security/test_teams_sender_resolver.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from codemie.rest_api.security.teams_sender_resolver import resolve_teams_sender
from codemie.rest_api.security.user import User


def _caller(user_type="service_account", user_id="codemie-teams-bot"):
    return User(id=user_id, email="bot@svc.example.com", user_type=user_type, project_names=[])


@pytest.mark.asyncio
@patch("codemie.rest_api.security.teams_sender_resolver.customer_config")
async def test_no_header_returns_caller_unchanged(mock_customer_config):
    mock_customer_config.is_feature_enabled.return_value = True
    caller = _caller()
    raw_request = MagicMock()
    raw_request.headers = {}
    assert await resolve_teams_sender(caller, raw_request) is caller
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/security/test_teams_sender_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codemie.rest_api.security.teams_sender_resolver'`

- [ ] **Step 3: Implement the helper**

```python
# src/codemie/rest_api/security/teams_sender_resolver.py
from fastapi import Request

from codemie.configs import config
from codemie.configs.customer_config import customer_config
from codemie.rest_api.security.user import User
from codemie.service.settings.settings_request_validator import TEAMS_BOT_INTEGRATION_FEATURE
from codemie.service.user.authentication_service import authentication_service

TEAMS_SERVICE_ACCOUNT_USER_TYPE = "service_account"
TEAMS_SENDER_EMAIL_HEADER = "X-Teams-Sender-Email"


def _is_allowlisted(caller: User) -> bool:
    return (
        bool(config.TEAMS_SERVICE_ACCOUNT_ID)
        and caller.user_type == TEAMS_SERVICE_ACCOUNT_USER_TYPE
        and caller.id == config.TEAMS_SERVICE_ACCOUNT_ID
    )


async def resolve_teams_sender(caller: User, raw_request: Request) -> User:
    """Third authenticate() branch: swap the Teams service account for the real
    end-user identified by the X-Teams-Sender-Email header.

    Known, pre-existing, out-of-scope gap: the header is trusted verbatim once the
    caller matches the allow-listed service account, with no HMAC/signature binding
    it to the real Teams message (see docs/superpowers/tasks/2026-08-31-.../lens-blind.md).
    """
    if not customer_config.is_feature_enabled(TEAMS_BOT_INTEGRATION_FEATURE):
        return caller

    sender_email = raw_request.headers.get(TEAMS_SENDER_EMAIL_HEADER)
    if not sender_email:
        return caller

    if not _is_allowlisted(caller):
        return caller

    return await authentication_service.authenticate_teams_sender(sender_email)
```

Note: `authentication_service` is the existing module-level singleton (mirrors `billing_user_resolver`'s own pattern) — confirm its name in `authentication_service.py` before wiring the import; use the singleton, not the class, for parity with how `authenticate_persistent_user` is already called elsewhere.

- [ ] **Step 4: Run the new test, then add the remaining cases mirroring `test_billing_user_resolver.py`'s old coverage** — feature flag off passthrough, non-allow-listed caller passthrough (both user_type mismatch and id mismatch), and a successful-swap case patching `authentication_service.authenticate_teams_sender` to return a distinct `User` and asserting `resolve_teams_sender` returns that exact object.

Run: `poetry run pytest tests/codemie/rest_api/security/test_teams_sender_resolver.py -v`
Expected: PASS (all cases)

- [ ] **Step 5: Commit**

---

### Task 3: Wire the Teams branch into `authenticate()`

**Files:**
- Modify: `src/codemie/rest_api/security/authentication.py:100-159` (inside `authenticate`, after line 140 where `user` is first resolved, before line 142's context-storage block)
- Test: `tests/codemie/rest_api/security/test_authentication.py` (add new test class)

**Interfaces:**
- Consumes: `resolve_teams_sender(caller, raw_request) -> User` (Task 2).
- Produces: `authenticate()`'s public contract is unchanged (`Request, internal_user_id, bind_key -> User`); the returned `User` may now be the Teams-resolved identity when the swap conditions hold. Everything downstream (Task 4+) relies only on this single `user`.

Insert `user = await resolve_teams_sender(user, request)` immediately after both the `bind_key` branch (line 128) and the external branch (line 140) converge — i.e. right after line 140, before line 142 — so the swap applies uniformly regardless of which branch authenticated the caller. Any `ExtendedHTTPException` `resolve_teams_sender` raises propagates through the existing `except` clause (lines 161-167) unchanged, since it already special-cases `ExtendedHTTPException` for re-raise.

- [ ] **Step 1: Write the failing test**

```python
# in tests/codemie/rest_api/security/test_authentication.py, new class
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User


class TestAuthenticateTeamsSenderBranch:
    async def _authenticate_as(self, mocker, caller: User, headers: dict):
        request = mocker.MagicMock()
        request.headers = headers
        provider = MagicMock()
        provider.authenticate_and_load_user = AsyncMock(return_value=caller)
        with (
            patch("codemie.rest_api.security.authentication.get_user_provider", return_value=provider),
            patch("codemie.rest_api.security.authentication.IdpFactory"),
        ):
            return await authenticate(request, internal_user_id=None, bind_key=None)

    @pytest.mark.anyio
    @patch("codemie.rest_api.security.authentication.resolve_teams_sender")
    async def test_teams_header_swaps_resolved_user(self, mock_resolve, mocker):
        caller = User(id="codemie-teams-bot", username="bot", user_type="service_account", project_names=[])
        resolved = User(id="end-user-1", username="enduser", project_names=[])
        mock_resolve.return_value = resolved

        result = await self._authenticate_as(mocker, caller, {"X-Teams-Sender-Email": "enduser@example.com"})

        assert result is resolved
        mock_resolve.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/security/test_authentication.py::TestAuthenticateTeamsSenderBranch -v`
Expected: FAIL — patch target `codemie.rest_api.security.authentication.resolve_teams_sender` doesn't exist (`AttributeError`), since `authenticate()` doesn't call it yet.

- [ ] **Step 3: Wire it in**

Add `from codemie.rest_api.security.teams_sender_resolver import resolve_teams_sender` to the imports (near line 33), and insert immediately after line 140 (`user = await provider.authenticate_and_load_user(request, idp)`), before line 142:

```python
        user = await resolve_teams_sender(user, request)
```

- [ ] **Step 4: Run test to verify it passes, then add a second case asserting passthrough when `resolve_teams_sender` returns the same caller (no header) and a third case asserting a `resolve_teams_sender`-raised `ExtendedHTTPException(code=401)` propagates unchanged out of `authenticate()`**

Run: `poetry run pytest tests/codemie/rest_api/security/test_authentication.py -v`
Expected: PASS (all authentication.py tests, including the new class and all pre-existing ones)

- [ ] **Step 5: Commit**

---

### Task 4: Remove `billing_user_resolver.py` and its test file

**Files:**
- Delete: `src/codemie/service/user/billing_user_resolver.py`
- Delete: `tests/codemie/service/user/test_billing_user_resolver.py`

**Interfaces:**
- Consumes: nothing (deletion only).
- Produces: nothing exported anymore; Task 5 removes the router's import of it, which must happen for the suite to import cleanly again.

Test-first: no — this is a pure deletion; Task 1–3 already added equivalent, superseding coverage, and Task 5's own test-first step is what proves the router no longer needs this module.

- [ ] **Step 1: Delete both files**
- [ ] **Step 2: Confirm nothing else imports the deleted module**

Run: `grep -rn "billing_user_resolver" src/ tests/`
Expected: no matches (Task 5 will make this true if any router references remain at this point — if this task runs before Task 5, expect matches in `assistant.py` and note them, then proceed; the grep is the acceptance check for Task 5, not a blocker here).

- [ ] **Step 3: Commit**

---

### Task 5: Strip `billing_user` from the router layer

**Files:**
- Modify: `src/codemie/rest_api/routers/assistant.py:113` (drop the `billing_user_resolver` import), `:962,1030` (`ask_virtual_assistant`), `:1050,1088` (`ask_assistant_by_id`), `:1183,1222` (`ask_assistant_by_slug`), `:2277-2411` (`_ask_virtual_assistant`/`_ask_assistant` signatures and bodies)
- Test: `tests/codemie/rest_api/routers/test_assistant.py` (rewrite `TestAskAssistantBillingUserSwap` and `TestAskVirtualAssistantBillingUserSwap`)

**Interfaces:**
- Consumes: `user: User = Depends(authenticate)` (already present on all three endpoints; `authenticate` now performs the Teams swap per Task 3).
- Produces: `_ask_assistant(assistant, raw_request, request, user, background_tasks, include_tool_errors=False, error_detail_level=ErrorDetailLevel.STANDARD)` — no `billing_user` parameter. `_ask_virtual_assistant` sibling loses its `billing_user` parameter identically. Task 6 relies on `get_request_handler(assistant, user, request_uuid)` having no `billing_user` kwarg.

Remove: the `get_billing_user` import (line 113); the three `billing_user: User = Depends(get_billing_user)` parameters (lines 962, 1050, 1183) and their positional pass-throughs (lines 1030, 1088, 1222); the `billing_user: User | None = None` parameter (line 2359) and `billing_user = billing_user or user` (line 2372); replace `record_usage(assistant=assistant, user=billing_user)` (2403), `request_summary_manager.create_request_summary(..., user=billing_user.as_user_model())` (2408), and `get_request_handler(assistant, user, request_uuid, billing_user=billing_user)` (2411) with `user` in place of `billing_user`.

- [ ] **Step 1: Rewrite the two swap tests to assert the single-user invariant (failing first)**

```python
class TestAskAssistantSingleUserInvariant:
    @patch("codemie.rest_api.routers.assistant.assistant_user_interaction_service.record_usage")
    @patch("codemie.rest_api.routers.assistant.request_summary_manager.create_request_summary")
    @patch("codemie.rest_api.routers.assistant.Ability")
    @patch("codemie.rest_api.routers.assistant.get_request_handler")
    def test_single_user_drives_both_access_control_and_record_usage(
        self, mock_get_handler, mock_ability, mock_request_summary, mock_record_usage, mock_user, mock_assistant,
    ):
        from codemie.rest_api.routers.assistant import _ask_assistant
        from codemie.core.models import AssistantChatRequest
        from unittest.mock import MagicMock

        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance
        mock_handler = MagicMock()
        mock_handler.process_request.return_value = {"response": "ok"}
        mock_get_handler.return_value = mock_handler
        mock_user.as_user_model.return_value = MagicMock()

        request = AssistantChatRequest(text="hi")
        _ask_assistant(mock_assistant, MagicMock(state=MagicMock(uuid="req-1")), request, mock_user, MagicMock())

        mock_ability.assert_called_with(mock_user)
        mock_record_usage.assert_called_with(assistant=mock_assistant, user=mock_user)
        mock_get_handler.assert_called_with(mock_assistant, mock_user, "req-1")
```

Replace `TestAskVirtualAssistantBillingUserSwap` similarly: drop the `billing_user` argument from the `ask_virtual_assistant(...)` call and assert `passed_user is mock_user` instead of `is billing_user`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_assistant.py -k "SingleUserInvariant or VirtualAssistant" -v`
Expected: FAIL — `_ask_assistant()`/`ask_virtual_assistant()` still require/accept `billing_user`, and `get_request_handler` is still called with a `billing_user=` kwarg, so the new assertions on call args mismatch.

- [ ] **Step 3: Make the router edits described above**

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_assistant.py -v`
Expected: PASS (full file, including unrelated pre-existing tests untouched by this change)

- [ ] **Step 5: Commit**

---

### Task 6: Strip `billing_user` from the handler layer

**Files:**
- Modify: `src/codemie/rest_api/handlers/assistant_handlers.py:100-105` (`AssistantRequestHandler.__init__`), `:432` (`save_chat_history`), `:615,930,991` (stream/background/sync `build_agent` calls), `:1121-1122` (`A2AAssistantHandler.__init__`), `:1292-1301` (`get_request_handler`)
- Test: `tests/codemie/rest_api/handlers/test_assistant_handlers.py` (check exact filename first via `Glob`; add/rewrite tests asserting `self.user` is what flows to `build_agent`/`set_llm_context`)

**Interfaces:**
- Consumes: `get_request_handler(assistant, user, request_uuid)` call sites from Task 5 (no `billing_user` kwarg).
- Produces: `AssistantRequestHandler.__init__(self, assistant, user, request_uuid)` — `self.billing_user` attribute removed entirely; every former `self.billing_user` use site now reads `self.user`. `A2AAssistantHandler.__init__(self, assistant, user, request_uuid)` matches. `get_request_handler(assistant, user, request_uuid)` matches. `HedgedAssistantHandler` needs no direct edit — it inherits the base `__init__` unchanged; confirm this after the base class changes (no new call site references `billing_user` there).

- [ ] **Step 1: Write/adjust the failing test**

```python
def test_init_has_no_billing_user_attribute_and_user_drives_build_agent(mock_assistant):
    from codemie.rest_api.handlers.assistant_handlers import StandardAssistantHandler
    from unittest.mock import MagicMock

    user = MagicMock()
    handler = StandardAssistantHandler(mock_assistant, user, "req-1")

    assert not hasattr(handler, "billing_user")
    assert handler.user is user
```

(If `StandardAssistantHandler` isn't directly constructible in existing tests, use whichever concrete subclass the existing test file already instantiates — check the file first.)

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/handlers/test_assistant_handlers.py -k billing_user -v`
Expected: FAIL — `hasattr(handler, "billing_user")` is `True` today.

- [ ] **Step 3: Make the edits**

At line 100: drop the `billing_user` parameter; at line 105: delete `self.billing_user = billing_user or user` entirely (no replacement needed — `self.user` already exists). At line 432: `set_llm_context(self.assistant, None, self.user)`. At lines 615, 930, 991: `user=self.user`. At lines 1121-1122: drop `billing_user` from `A2AAssistantHandler.__init__`'s signature and the `super().__init__(...)` call. At lines 1292-1301: drop `billing_user` from `get_request_handler`'s signature and from each of the three constructor calls it makes.

- [ ] **Step 4: Run the full handler test file**

Run: `poetry run pytest tests/codemie/rest_api/handlers/test_assistant_handlers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 7: Confirm `resume_tool_call`/`_ask_assistant_resume` is unaffected

**Files:**
- Test: `tests/codemie/rest_api/routers/test_assistant.py` (add one regression assertion near existing resume-path tests; `Grep` the file first for the existing resume test class name)

**Interfaces:**
- Consumes: `authenticate()`'s post-Task-3 behavior (single `user`, possibly Teams-swapped).
- Produces: nothing new — this task is a regression guard, not a behavior change.

Test-first: yes — add a test asserting `_ask_assistant_resume`/`resume_tool_call` calls `get_request_handler`/`Ability` with the single `user` argument it receives, with no `billing_user`-shaped kwarg anywhere in its call signature, and that it still passes after Tasks 5–6.

- [ ] **Step 1: Write the failing-then-passing regression test**

```python
def test_resume_tool_call_never_had_and_still_has_no_billing_user_param():
    import inspect
    from codemie.rest_api.routers.assistant import resume_tool_call

    params = inspect.signature(resume_tool_call).parameters
    assert "billing_user" not in params
```

- [ ] **Step 2: Run it**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_assistant.py -k resume_tool_call -v`
Expected: PASS immediately (confirms no accidental regression was introduced by Tasks 3–6; if it fails, a prior task leaked `billing_user` into this path and must be fixed before proceeding)

- [ ] **Step 3: Commit**

---

## Post-implementation note (not a task)

The Teams sender-email header remains trusted verbatim once the caller matches the allow-listed service account, with no HMAC/signature binding it to the real Teams message. This is a pre-existing gap (documented in `docs/superpowers/tasks/2026-08-31-implement-option-4b-for-routing-teams-bot-group-ch/lens-blind.md`) that this plan deliberately does not fix — noted in Task 2's `resolve_teams_sender` docstring for future reference.
