# GitLab MR Webhook Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add granular filtering for GitLab merge request webhooks, allowing users to specify which MR actions (Created/open, Updated, Closed, Reopened, Merged, Approved/Unapproved) trigger their workflows.

**Architecture:** Mirror the existing GitHub webhook security pattern in `webhook.py`. Create a new `gitlab_webhook_security.py` module and add a `_verify_gitlab_token` helper to `WebhookService`, inserted as a new priority branch inside the existing `verify_security_header()` method (which already delegates to `_verify_github_signature`, `_verify_legacy_header`, `_handle_no_security`). Add two unconditional UI fields for GitLab settings, matching how the GitHub fields are already rendered.

**Tech Stack:** Python 3.12 + Poetry (FastAPI backend), TypeScript/React UI, JSONB `credential_values` storage (no schema migration).

## Clarification assumptions

- **GitLab token is a plaintext shared secret, not a signature.** GitLab sends the secret token configured on the webhook *verbatim* in the `X-Gitlab-Token` header. Verification is a constant-time string comparison against the stored token — it is **not** an HMAC of the request body (that is GitHub's model, via `X-Hub-Signature-256`). This corrects the original technical-analysis note that labeled `X-Gitlab-Token` a "signature".
- **No provider selector exists in the webhook config**, so the GitLab fields are shown unconditionally, exactly like the existing GitHub fields (`github_webhook_secret`, `github_event_filter`). No `shouldShow`/`isGitLabWebhook` conditional is introduced — there is no reliable signal to key it on, and it would be inconsistent with the GitHub fields.

## Global Constraints

- GitLab verification is inserted as a new priority branch in `verify_security_header()`, after GitHub signature verification and before legacy-header auth. It only engages when a `gitlab_webhook_token` is configured **and** the request looks like a GitLab webhook (`gitlab_token and is_gitlab_webhook`). Otherwise the request falls through to the existing legacy-header / no-security handling — preserving backward compatibility.
- Event filtering applies at the security/dispatch layer (`webhook.py`), NOT at the workflow execution layer.
- No schema migrations — settings stored as JSONB key-value pairs via `setting.credential(key)`.
- New settings keys: `gitlab_webhook_token`, `gitlab_event_filter`.
- GitLab MR event actions (the closed set the filter validates against): `open`, `close`, `merge`, `update`, `reopen`, `approved`, `unapproved`.
- Empty/unset `gitlab_event_filter` = allow all MR actions (no regression / default-on behavior).
- HTTP status codes mirror the GitHub path: invalid token → `401 UNAUTHORIZED` (matches `ERROR_INVALID_SIGNATURE`); disallowed MR action → `400 BAD_REQUEST` (matches GitHub's `ERROR_EVENT_NOT_ALLOWED` at `webhook.py:236`).
- Existing GitHub and legacy-header webhooks must continue to work unchanged.
- Backend commands use Poetry from the `codemie/` directory: tests via `poetry run pytest tests/...`, lint/format via `make ruff`. UI type-checks via `npm run type-check`.

---

## File Structure

### Backend Files
- **Create:** `codemie/src/codemie/triggers/bindings/gitlab_webhook_security.py` — GitLab detection, plaintext token verification, MR action extraction/validation, metadata extraction.
- **Modify:** `codemie/src/codemie/triggers/bindings/webhook.py` — add `import json`, GitLab constants + messages, `_verify_gitlab_token` helper, and the GitLab priority branch in `verify_security_header()`.
- **Create:** `codemie/tests/triggers/bindings/conftest.py` — shared GitLab MR payload fixtures (all actions) for reuse across test modules.

### Frontend Files
- **Modify:** `codemie-ui/src/utils/settingsUIConfig.ts` — add `gitlab_webhook_token` and `gitlab_event_filter` fields to the `webhook` credential config.

### Test Files
- **Create:** `codemie/tests/triggers/bindings/test_gitlab_webhook_security.py` — unit tests for the GitLab security module. (Already scaffolded on disk with the corrected plaintext-token contract; Task 1 finalizes it against the real module.)
- **Modify:** `codemie/tests/triggers/bindings/test_webhook.py` — add `verify_security_header`-level tests for GitLab token + event filtering and GitHub/legacy regression.

---

## Task 1: Create GitLab Webhook Security Module

**Files:**
- Create: `codemie/src/codemie/triggers/bindings/gitlab_webhook_security.py`
- Test: `codemie/tests/triggers/bindings/test_gitlab_webhook_security.py`

**Interfaces:**
- Consumes: FastAPI `Request` (headers) and the raw webhook body (dict, str, or bytes).
- Produces (all `@classmethod` on `GitLabWebhookSecurity`, mirroring `GitHubWebhookSecurity`):
  - `is_gitlab_webhook(request) -> bool` — detect GitLab via `X-Gitlab-Event` / `X-Gitlab-Token` headers or `GitLab/` user agent.
  - `verify_token(request, expected_token: str) -> bool` — constant-time compare of `X-Gitlab-Token` header against `expected_token` (plaintext secret).
  - `extract_mr_action(body) -> Optional[str]` — return the MR `action` if `object_kind == "merge_request"` and the action is a known MR action, else `None`.
  - `validate_mr_event_type(body, allowed_actions: list) -> bool` — `True` if `allowed_actions` is empty (allow all) or the extracted action is in it; else `False`.
  - `extract_gitlab_metadata(request) -> dict` — `{event_type, delivery_id, user_agent}`.

**Test-first: yes — failing tests for GitLab detection, plaintext token verification, and MR action extraction/validation.**

- [ ] **Step 1: Write the failing test**

Create/overwrite `codemie/tests/triggers/bindings/test_gitlab_webhook_security.py` with real behavior tests (no placeholder `__doc__` assertions):

```python
import pytest
from unittest.mock import Mock
from codemie.triggers.bindings.gitlab_webhook_security import GitLabWebhookSecurity


@pytest.fixture
def gitlab_mr_open_payload():
    """Sample GitLab MR 'open' event payload."""
    return {
        "object_kind": "merge_request",
        "action": "open",
        "project": {"id": 12345, "name": "test-project"},
        "object_attributes": {"id": 1, "iid": 100, "title": "Test MR", "state": "opened"},
    }


@pytest.fixture
def gitlab_mr_merge_payload():
    """Sample GitLab MR 'merge' event payload."""
    return {
        "object_kind": "merge_request",
        "action": "merge",
        "project": {"id": 12345},
        "object_attributes": {"id": 1, "iid": 100, "state": "merged"},
    }


@pytest.fixture
def mock_gitlab_request():
    """Mock FastAPI Request with GitLab headers."""
    request = Mock()
    request.headers = {
        "X-Gitlab-Event": "Merge Request Hook",
        "X-Gitlab-Delivery": "12345-67890-abcdef",
        "X-Gitlab-Token": "test_token_signature",
        "User-Agent": "GitLab/16.0",
    }
    return request


def test_is_gitlab_webhook_with_gitlab_headers(mock_gitlab_request):
    assert GitLabWebhookSecurity.is_gitlab_webhook(mock_gitlab_request) is True


def test_is_gitlab_webhook_with_github_headers():
    request = Mock()
    request.headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": "sha256=abcd1234",
        "User-Agent": "GitHub-Hookshot/123abc",
    }
    assert GitLabWebhookSecurity.is_gitlab_webhook(request) is False


def test_extract_mr_action_open(gitlab_mr_open_payload):
    assert GitLabWebhookSecurity.extract_mr_action(gitlab_mr_open_payload) == "open"


def test_extract_mr_action_merge(gitlab_mr_merge_payload):
    assert GitLabWebhookSecurity.extract_mr_action(gitlab_mr_merge_payload) == "merge"


def test_extract_mr_action_non_mr_event():
    assert GitLabWebhookSecurity.extract_mr_action({"object_kind": "push", "action": None}) is None


def test_extract_mr_action_from_bytes(gitlab_mr_open_payload):
    import json
    raw = json.dumps(gitlab_mr_open_payload).encode("utf-8")
    assert GitLabWebhookSecurity.extract_mr_action(raw) == "open"


def test_validate_mr_event_type_allowed(gitlab_mr_open_payload):
    assert GitLabWebhookSecurity.validate_mr_event_type(gitlab_mr_open_payload, ["open", "merge"]) is True


def test_validate_mr_event_type_not_allowed(gitlab_mr_merge_payload):
    assert GitLabWebhookSecurity.validate_mr_event_type(gitlab_mr_merge_payload, ["open"]) is False


def test_validate_mr_event_type_empty_filter(gitlab_mr_open_payload):
    assert GitLabWebhookSecurity.validate_mr_event_type(gitlab_mr_open_payload, []) is True


def test_verify_token_valid(mock_gitlab_request):
    """GitLab sends the secret verbatim in X-Gitlab-Token (plaintext, NOT an HMAC)."""
    token = "test_token"
    mock_gitlab_request.headers["X-Gitlab-Token"] = token
    assert GitLabWebhookSecurity.verify_token(mock_gitlab_request, token) is True


def test_verify_token_invalid(mock_gitlab_request):
    assert GitLabWebhookSecurity.verify_token(mock_gitlab_request, "correct_token") is False


def test_verify_token_empty_expected(mock_gitlab_request):
    assert GitLabWebhookSecurity.verify_token(mock_gitlab_request, "") is False


def test_verify_token_missing_header():
    request = Mock()
    request.headers = {"X-Gitlab-Event": "Merge Request Hook"}
    assert GitLabWebhookSecurity.verify_token(request, "some_token") is False


def test_extract_gitlab_metadata(mock_gitlab_request):
    metadata = GitLabWebhookSecurity.extract_gitlab_metadata(mock_gitlab_request)
    assert metadata["event_type"] == "Merge Request Hook"
    assert metadata["delivery_id"] == "12345-67890-abcdef"
    assert "GitLab" in metadata["user_agent"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/oleg_sotnichenko/codemie-dev/codemie
poetry run pytest tests/triggers/bindings/test_gitlab_webhook_security.py -v
```

Expected: FAILED — `ModuleNotFoundError: No module named 'codemie.triggers.bindings.gitlab_webhook_security'`.

- [ ] **Step 3: Write the GitLab security module**

Create `codemie/src/codemie/triggers/bindings/gitlab_webhook_security.py` (prepend the Apache 2.0 license header — run `make license-fix FILE=src/codemie/triggers/bindings/gitlab_webhook_security.py` if unsure of the exact banner):

```python
# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import hmac
import json
from typing import Optional

from fastapi import Request

from codemie.configs import logger


class GitLabWebhookSecurity:
    """GitLab webhook detection, token verification, and MR event validation.

    Unlike GitHub (which signs the payload with HMAC-SHA256 and sends the digest
    in X-Hub-Signature-256), GitLab sends the configured *secret token verbatim*
    in the X-Gitlab-Token header. Verification is therefore a constant-time
    comparison of the header value against the stored token — there is no HMAC
    and the request body is not involved in authentication.
    """

    HEADER_EVENT = "X-Gitlab-Event"
    HEADER_DELIVERY = "X-Gitlab-Delivery"
    HEADER_TOKEN = "X-Gitlab-Token"
    USER_AGENT_PREFIX = "GitLab/"

    # The closed set of merge-request actions the event filter recognizes.
    MR_ACTIONS = {"open", "close", "merge", "update", "reopen", "approved", "unapproved"}

    @classmethod
    def is_gitlab_webhook(cls, request: Request) -> bool:
        """Detect a GitLab webhook from its headers / user agent."""
        headers = request.headers
        has_event = cls.HEADER_EVENT in headers
        has_token = cls.HEADER_TOKEN in headers
        has_gitlab_agent = headers.get("User-Agent", "").startswith(cls.USER_AGENT_PREFIX)
        return has_event or has_token or has_gitlab_agent

    @classmethod
    def verify_token(cls, request: Request, expected_token: str) -> bool:
        """Verify the plaintext GitLab secret token (constant-time compare)."""
        if not expected_token:
            return False
        received = request.headers.get(cls.HEADER_TOKEN, "")
        if not received:
            return False
        return hmac.compare_digest(received, expected_token)

    @classmethod
    def extract_mr_action(cls, body) -> Optional[str]:
        """Return the MR action for merge_request events, else None.

        Accepts a dict, a JSON str, or raw bytes.
        """
        if isinstance(body, (bytes, bytearray)):
            body = body.decode("utf-8", errors="ignore")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                return None
        if not isinstance(body, dict):
            return None
        if body.get("object_kind") != "merge_request":
            return None
        action = body.get("action")
        return action if action in cls.MR_ACTIONS else None

    @classmethod
    def validate_mr_event_type(cls, body, allowed_actions: list) -> bool:
        """True if no filter is set (allow all) or the MR action is allowed."""
        if not allowed_actions:
            return True
        action = cls.extract_mr_action(body)
        if action is None:
            return False
        return action in allowed_actions

    @classmethod
    def extract_gitlab_metadata(cls, request: Request) -> dict:
        """Extract GitLab metadata from webhook request headers."""
        headers = request.headers
        return {
            "event_type": headers.get(cls.HEADER_EVENT, ""),
            "delivery_id": headers.get(cls.HEADER_DELIVERY, ""),
            "user_agent": headers.get("User-Agent", ""),
        }
```

> Note: `logger` is imported for parity with the GitHub module and use by later log lines; if ruff flags it as unused at this stage, either add a debug log in `is_gitlab_webhook` or drop the import until Task 2. Prefer keeping the module import-clean — remove it here and re-add in Task 2 only if needed.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/oleg_sotnichenko/codemie-dev/codemie
poetry run pytest tests/triggers/bindings/test_gitlab_webhook_security.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Lint/format**

```bash
cd /Users/oleg_sotnichenko/codemie-dev/codemie
make ruff
```

Expected: no errors (formatting auto-applied; no lint violations).

- [ ] **Step 6: Commit**

```bash
cd /Users/oleg_sotnichenko/codemie-dev
git add codemie/src/codemie/triggers/bindings/gitlab_webhook_security.py \
        codemie/tests/triggers/bindings/test_gitlab_webhook_security.py
git commit -m "EPMCDME-8384: Add GitLab webhook security module with MR event validation"
```

---

## Task 2: Wire GitLab verification + event filtering into WebhookService

**Files:**
- Modify: `codemie/src/codemie/triggers/bindings/webhook.py`
- Test: `codemie/tests/triggers/bindings/test_webhook.py`

**Interfaces:**
- Consumes: `GitLabWebhookSecurity` (Task 1); the existing `WebhookService.verify_security_header(cls, request, setting, raw_payload)` and its `_send_verification_metric` helper.
- Produces: a `_verify_gitlab_token` classmethod and a new GitLab priority branch inside `verify_security_header()`. No signature changes to public methods.

**Reality check (verified against the current `webhook.py`):**
- `invoke_webhook_logic(cls, request, webhook_id, background_tasks, raw_payload)` is a **synchronous** `@classmethod`. It fetches the setting itself via `SettingsService.retrieve_setting(...)`; it does **not** take a settings dict, and it is **not** `async`.
- Security is verified by `verify_security_header(cls, request, setting, raw_payload)` (`webhook.py:211`), which delegates to `_verify_github_signature` / `_verify_legacy_header` / `_handle_no_security`. The GitHub branch guard is `if github_secret and is_github_webhook:` (`webhook.py:233`).
- The unit under test for this feature is therefore `verify_security_header`, called with a fake `setting` object exposing `.credential(key)`, `.project_name`, `.user_id`, `.alias`. Tests target it directly — no `await`, no `SettingsService` round-trip.

**Test-first: yes — failing tests asserting `verify_security_header` enforces the GitLab token and event filter.**

- [ ] **Step 1: Write the failing tests**

Add to `codemie/tests/triggers/bindings/test_webhook.py` (top-of-file imports as needed):

```python
import pytest
from unittest.mock import Mock, patch
from fastapi import HTTPException

from codemie.triggers.bindings.webhook import WebhookService
from codemie.service.monitoring.webhook_monitoring_service import WebhookMonitoringService


class _FakeSetting:
    """Minimal stand-in for a Settings object in verify_security_header."""

    def __init__(self, creds: dict):
        self._creds = creds
        self.project_name = "test-project"
        self.user_id = "user-1"
        self.alias = "gitlab-hook"

    def credential(self, key):
        return self._creds.get(key)


def _gitlab_request(token_header: str):
    request = Mock()
    request.headers = {
        "X-Gitlab-Event": "Merge Request Hook",
        "X-Gitlab-Delivery": "abc-123",
        "X-Gitlab-Token": token_header,
        "User-Agent": "GitLab/16.0",
    }
    return request


@pytest.fixture(autouse=True)
def _silence_metrics():
    with patch.object(WebhookMonitoringService, "send_webhook_invocation_metric", return_value=None):
        yield


def test_gitlab_allowed_action_passes():
    setting = _FakeSetting({
        "webhook_id": "w1",
        "gitlab_webhook_token": "tok",
        "gitlab_event_filter": "open,merge",
    })
    request = _gitlab_request("tok")
    raw = b'{"object_kind":"merge_request","action":"open"}'
    # Should NOT raise.
    WebhookService.verify_security_header(request, setting, raw)


def test_gitlab_filtered_action_raises_400():
    setting = _FakeSetting({
        "webhook_id": "w1",
        "gitlab_webhook_token": "tok",
        "gitlab_event_filter": "open,merge",
    })
    request = _gitlab_request("tok")
    raw = b'{"object_kind":"merge_request","action":"update"}'
    with pytest.raises(HTTPException) as exc:
        WebhookService.verify_security_header(request, setting, raw)
    assert exc.value.status_code == 400


def test_gitlab_invalid_token_raises_401():
    setting = _FakeSetting({"webhook_id": "w1", "gitlab_webhook_token": "tok"})
    request = _gitlab_request("WRONG")
    with pytest.raises(HTTPException) as exc:
        WebhookService.verify_security_header(request, setting, b"{}")
    assert exc.value.status_code == 401


def test_gitlab_empty_filter_allows_all():
    setting = _FakeSetting({"webhook_id": "w1", "gitlab_webhook_token": "tok"})
    request = _gitlab_request("tok")
    raw = b'{"object_kind":"merge_request","action":"update"}'
    # No filter configured -> allow all -> must NOT raise.
    WebhookService.verify_security_header(request, setting, raw)


def test_gitlab_without_configured_token_falls_through_to_legacy():
    """Backward compat: GitLab-looking request but no gitlab_webhook_token
    configured must fall through to legacy-header auth, not force a 401."""
    setting = _FakeSetting({
        "webhook_id": "w1",
        "secure_header_name": "X-Secure",
        "secure_header_value": "expected",
    })
    request = _gitlab_request("irrelevant")
    request.headers["X-Secure"] = "expected"
    # Legacy header matches -> must NOT raise.
    WebhookService.verify_security_header(request, setting, b"{}")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/oleg_sotnichenko/codemie-dev/codemie
poetry run pytest tests/triggers/bindings/test_webhook.py -k gitlab -v
```

Expected: FAILs — GitLab branch not implemented, so `test_gitlab_filtered_action_raises_400` / `test_gitlab_invalid_token_raises_401` do not raise (or the request is mis-routed). `test_gitlab_without_configured_token_falls_through_to_legacy` should already pass (documents existing behavior we must preserve).

- [ ] **Step 3: Add `import json` and GitLab constants/messages to `webhook.py`**

Add `import json` to the top-level imports (after the `enum` import). Then, inside `class WebhookService`, add the GitLab constants next to the GitHub ones (after `GITHUB_REQUIRE_SHA256`, ~`webhook.py:56`):

```python
    # GitLab webhook security (plaintext token verification)
    GITLAB_WEBHOOK_TOKEN = "gitlab_webhook_token"
    GITLAB_EVENT_FILTER = "gitlab_event_filter"
```

And add response messages next to the other message constants (~`webhook.py:69`):

```python
    INVALID_GITLAB_TOKEN = "Invalid GitLab token"
    GITLAB_EVENT_NOT_ALLOWED = "GitLab MR event action '{}' is not allowed for this webhook"
```

Add the import for the new module next to the GitHub import (`webhook.py:32`):

```python
from codemie.triggers.bindings.gitlab_webhook_security import GitLabWebhookSecurity
```

- [ ] **Step 4: Insert the GitLab priority branch in `verify_security_header()`**

In `verify_security_header` (`webhook.py:211`), the current body computes `webhook_id`, `github_secret`, `is_github_webhook`, then:

```python
        # Priority 1: GitHub signature verification
        if github_secret and is_github_webhook:
            return cls._verify_github_signature(request, setting, raw_payload, webhook_id, github_secret)
```

Immediately **after** that `if` block (and before "Priority 2: Legacy header authentication"), add:

```python
        # Priority 1.5: GitLab token verification
        gitlab_token = setting.credential(cls.GITLAB_WEBHOOK_TOKEN)
        is_gitlab_webhook = GitLabWebhookSecurity.is_gitlab_webhook(request)
        if gitlab_token and is_gitlab_webhook:
            return cls._verify_gitlab_token(request, setting, raw_payload, webhook_id, gitlab_token)
```

- [ ] **Step 5: Add the `_verify_gitlab_token` helper**

Add this classmethod after `_verify_github_signature` (mirrors its structure, including metrics and error handling):

```python
    @classmethod
    def _verify_gitlab_token(cls, request: Request, setting, raw_payload: bytes, webhook_id: str, gitlab_token: str):
        """Verify GitLab webhook token and MR event filter.

        GitLab authenticates via a plaintext secret token in the X-Gitlab-Token
        header (constant-time compared against the stored token). When a
        gitlab_event_filter is configured, only the listed MR actions are
        allowed; an empty/unset filter allows all actions.

        Raises:
            HTTPException: 401 on token mismatch, 400 on a filtered-out MR action.
        """
        try:
            if not GitLabWebhookSecurity.verify_token(request, gitlab_token):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail=cls.INVALID_GITLAB_TOKEN
                )

            event_filter = setting.credential(cls.GITLAB_EVENT_FILTER)
            if event_filter:
                allowed_actions = [a.strip() for a in event_filter.split(',') if a.strip()]
                try:
                    body = json.loads(raw_payload) if raw_payload else {}
                except (json.JSONDecodeError, TypeError):
                    body = {}
                if not GitLabWebhookSecurity.validate_mr_event_type(body, allowed_actions):
                    action = GitLabWebhookSecurity.extract_mr_action(body)
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=cls.GITLAB_EVENT_NOT_ALLOWED.format(action),
                    )

            metadata = GitLabWebhookSecurity.extract_gitlab_metadata(request)
            logger.info(
                f"GitLab webhook token verified successfully. "
                f"WebhookID: '{webhook_id}', Event: {metadata.get('event_type')}, "
                f"Delivery ID: {metadata.get('delivery_id')}, "
                f"Project: '{setting.project_name}', UserID: '{setting.user_id}'"
            )
            cls._send_verification_metric(
                webhook_id,
                setting,
                success=True,
                verification_method="gitlab_token",
                additional_attributes={
                    "event_type": metadata.get("event_type"),
                    "delivery_id": metadata.get("delivery_id"),
                },
            )
        except HTTPException as e:
            logger.error(
                f"GitLab webhook verification failed. "
                f"WebhookID: '{webhook_id}', Project: '{setting.project_name}', "
                f"UserID: '{setting.user_id}', Error: {e.detail}"
            )
            cls._send_verification_metric(
                webhook_id,
                setting,
                success=False,
                verification_method="gitlab_token",
                additional_attributes={
                    "error_cause": "gitlab_verification_failed",
                    "status_code": e.status_code,
                },
            )
            raise
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /Users/oleg_sotnichenko/codemie-dev/codemie
poetry run pytest tests/triggers/bindings/test_webhook.py -k gitlab -v
# Regression: existing GitHub + legacy webhook tests must still pass.
poetry run pytest tests/triggers/bindings/test_webhook.py -v
```

Expected: all GitLab tests PASS; all pre-existing tests still PASS.

- [ ] **Step 7: Lint/format**

```bash
cd /Users/oleg_sotnichenko/codemie-dev/codemie
make ruff
```

- [ ] **Step 8: Commit**

```bash
cd /Users/oleg_sotnichenko/codemie-dev
git add codemie/src/codemie/triggers/bindings/webhook.py \
        codemie/tests/triggers/bindings/test_webhook.py
git commit -m "EPMCDME-8384: Add GitLab token verification and MR event filtering to WebhookService"
```

---

## Task 3: Shared GitLab MR payload fixtures

**Files:**
- Create: `codemie/tests/triggers/bindings/conftest.py`
- Modify: `codemie/tests/triggers/bindings/test_gitlab_webhook_security.py` (drop the now-duplicated inline payload fixtures; rely on conftest)

**Interfaces:**
- Consumes: nothing.
- Produces: pytest fixtures for every MR action (`gitlab_mr_open_payload`, `..._close_`, `..._merge_`, `..._update_`, `..._reopen_`, `..._approved_`, `..._unapproved_payload`) plus `gitlab_push_payload` (non-MR negative case). Auto-discovered by pytest for all modules in `tests/triggers/bindings/`.

**Why conftest.py (not a `fixtures/` module):** pytest auto-loads `conftest.py` fixtures for sibling test files — no `sys.path` hacks or `import *`. It also avoids the original plan's bug of calling a `@pytest.fixture` (`gitlab_mr_open_payload()`) directly as a function, which pytest forbids. Fixtures here delegate to a plain module-level factory instead.

**Test-first: n/a — fixtures are shared test data; they are exercised by Task 1/Task 5 tests.**

- [ ] **Step 1: Create `codemie/tests/triggers/bindings/conftest.py`**

```python
import pytest


def _mr_payload(action: str, state: str = "opened", **attr_overrides) -> dict:
    """Build a GitLab merge_request webhook payload for the given action."""
    object_attributes = {
        "id": 100,
        "iid": 1,
        "title": "Add test feature",
        "description": "Test merge request",
        "source_branch": "feature/test",
        "target_branch": "main",
        "state": state,
        "action": action,
        "url": "https://gitlab.com/group/test-project/-/merge_requests/1",
    }
    object_attributes.update(attr_overrides)
    return {
        "object_kind": "merge_request",
        "event_type": "merge_request",
        "user": {"id": 1, "name": "John Doe", "username": "johndoe"},
        "project": {
            "id": 12345,
            "name": "test-project",
            "path_with_namespace": "group/test-project",
        },
        "object_attributes": object_attributes,
    }


@pytest.fixture
def gitlab_mr_open_payload():
    return _mr_payload("open", "opened")


@pytest.fixture
def gitlab_mr_close_payload():
    return _mr_payload("close", "closed")


@pytest.fixture
def gitlab_mr_merge_payload():
    return _mr_payload("merge", "merged", merge_commit_sha="xyz789", merge_user_id=1)


@pytest.fixture
def gitlab_mr_update_payload():
    return _mr_payload("update", "opened", description="Updated description")


@pytest.fixture
def gitlab_mr_reopen_payload():
    return _mr_payload("reopen", "opened")


@pytest.fixture
def gitlab_mr_approved_payload():
    return _mr_payload("approved", "opened")


@pytest.fixture
def gitlab_mr_unapproved_payload():
    return _mr_payload("unapproved", "opened")


@pytest.fixture
def gitlab_push_payload():
    """Non-MR event, for negative testing."""
    return {
        "object_kind": "push",
        "event_type": "push",
        "ref": "refs/heads/main",
        "project": {"id": 12345, "name": "test-project"},
    }
```

- [ ] **Step 2: Remove the duplicated inline fixtures from the Task 1 test file**

In `test_gitlab_webhook_security.py`, delete the local `gitlab_mr_open_payload` and `gitlab_mr_merge_payload` fixture definitions (they now come from `conftest.py`). Keep the `mock_gitlab_request` fixture (it is header-only and specific to this module). The test functions are unchanged — they resolve the fixtures from conftest by name.

- [ ] **Step 3: Run tests**

```bash
cd /Users/oleg_sotnichenko/codemie-dev/codemie
poetry run pytest tests/triggers/bindings/test_gitlab_webhook_security.py -v
```

Expected: all tests PASS (fixtures resolved from conftest).

- [ ] **Step 4: Commit**

```bash
cd /Users/oleg_sotnichenko/codemie-dev
git add codemie/tests/triggers/bindings/conftest.py \
        codemie/tests/triggers/bindings/test_gitlab_webhook_security.py
git commit -m "EPMCDME-8384: Add shared GitLab MR payload fixtures via conftest"
```

---

## Task 4: Add UI configuration fields for GitLab webhooks

**Files:**
- Modify: `codemie-ui/src/types/settingsUI.ts` — new `CredentialComponentType.multiselect` variant.
- Modify: `codemie-ui/src/pages/integrations/components/SettingsForm/CredentialFields.tsx` — render branch for `multiselect`.
- Modify: `codemie-ui/src/utils/settingsUIConfig.ts` (the `webhook` credential config) — `gitlab_webhook_token` (sensitive input) and `gitlab_event_filter` (checkbox group).
- Create: `codemie-ui/src/pages/integrations/components/SettingsForm/__tests__/CredentialFields.test.tsx`

**Revision note (superseding the original Step 1 below):** the original plan mirrored GitHub's free-text `github_event_filter` field, which does not satisfy the story's AC1 ("Users can define specific merge request event actions ... Created, Updated, Closed, Reopened, Merged, Approved/Unapproved") or AC3 ("UI validation prevents saving a webhook with none of the event types selected") — a raw comma-separated string has no discrete controls and no save-time validation; a typo silently produces a filter that never matches. A code review of the original implementation (`docs/superpowers/reviews/2026-07-13-gitlab-mr-webhook-filters/`) flagged this. Fixed by adding a real checkbox-group component type instead of reusing the existing `input`/`select`/`textarea`/`record` types, none of which support multi-selection with per-item checkboxes.

**Interfaces:**
- Consumes: existing `webhook.fields` structure; `CredentialFieldConfig.options` (already supported by the type, previously only used by `select`).
- Produces: `gitlab_webhook_token` unchanged (sensitive input); `gitlab_event_filter` rendered as one checkbox per named MR action. The "Approved / Unapproved" checkbox bundles two raw backend tokens (`approved`,`unapproved`) into a single `option.value` of `"approved,unapproved"` — `CredentialFields` normalizes option values into individual tokens for checked-state and serialization, never comparing `option.value` as one atomic string.
- Stored format is unchanged: a comma-separated string matching `GitLabWebhookSecurity.MR_ACTIONS` tokens (`open,close,merge,update,reopen,approved,unapproved`), so no backend or migration changes are needed.
- Empty/unset stored value renders with **every checkbox checked** (visually explicit "all events", matching the backend's already-existing empty-filter-allows-all behavior — AC5, no regression for existing webhooks). Unchecking the last remaining checked box is blocked client-side with an inline message ("At least one merge request action must stay selected") instead of ever producing an empty selection — this satisfies AC3 without breaking AC5, since the stored value is never written as an explicit empty string by user interaction (only truly untouched/legacy webhooks can have that value, and it continues to mean "all").

**Test-first: yes — `CredentialFields.test.tsx` covers: renders one checkbox per option; defaults to all-checked when unset; reflects a stored filter as partial selection; correctly checks/serializes the combined Approved/Unapproved option across its two raw tokens; blocks emptying the last selection.**

- [x] **Step 1: Add `CredentialComponentType.multiselect`**

`codemie-ui/src/types/settingsUI.ts` — add `multiselect = 'multiselect'` to the `CredentialComponentType` enum.

- [x] **Step 2: Render branch in `CredentialFields.tsx`**

Renders one `Checkbox` (from `@/components/form/Checkbox`) per `option`, computing checked-state and toggling by normalizing each option's `value` into individual comma-split tokens (so a bundled option like `"approved,unapproved"` is compared/stored token-by-token). Blocks reducing the token set to zero.

- [x] **Step 3: Wire `gitlab_event_filter` to the new type**

In `codemie-ui/src/utils/settingsUIConfig.ts`, inside `webhook.fields`, immediately after `github_event_filter`:

```typescript
      gitlab_webhook_token: {
        label: 'GitLab Webhook Secret Token',
        placeholder: 'GitLab Webhook Secret Token: Optional field',
        sensitive: true,
      },
      gitlab_event_filter: {
        label: 'Trigger on merge request actions',
        type: CredentialComponentType.multiselect,
        options: [
          { value: 'open', label: 'Created (open)' },
          { value: 'update', label: 'Updated' },
          { value: 'close', label: 'Closed' },
          { value: 'reopen', label: 'Reopened' },
          { value: 'merge', label: 'Merged' },
          { value: 'approved,unapproved', label: 'Approved / Unapproved' },
        ],
      },
```

- [x] **Step 4: Type-check, lint, test**

```bash
cd /Users/oleg_sotnichenko/codemie-dev/codemie-ui
npx tsc --noEmit -p .
npx eslint src/pages/integrations/components/SettingsForm src/utils/settingsUIConfig.ts src/types/settingsUI.ts
npx vitest run --project unit src/pages/integrations/components/SettingsForm/__tests__/CredentialFields.test.tsx
```

Expected: no errors, no lint findings, 6/6 tests passing.

- [ ] **Step 5: Commit**

```bash
cd /Users/oleg_sotnichenko/codemie-dev/codemie-ui
git add src/types/settingsUI.ts src/pages/integrations/components/SettingsForm/CredentialFields.tsx \
  src/pages/integrations/components/SettingsForm/__tests__/CredentialFields.test.tsx src/utils/settingsUIConfig.ts
git commit -m "EPMCDME-8384: Add GitLab MR event filter checkbox UI"
```

Branch `EPMCDME-8384_gitlab-mr-webhook-filters` in `codemie-ui` is a **separate repo from `codemie`**, where MR !3758 already lives — this change needs its own MR in `codemie-ui`, linked from !3758's description, not a commit appended to the existing backend MR.

---

## Task 5: End-to-end filter coverage + documentation

**Files:**
- Modify: `codemie/tests/triggers/bindings/test_webhook.py`
- Create: `codemie/docs/webhook-configuration.md`

**Interfaces:**
- Consumes: `GitLabWebhookSecurity`, the fixtures from Task 3, and `verify_security_header` behavior from Task 2.
- Produces: parametrized coverage across all MR actions and user-facing documentation.

**Test-first: yes — a parametrized test asserting every MR action is extracted and filtered correctly.**

- [ ] **Step 1: Add a parametrized action-coverage test**

Add to `codemie/tests/triggers/bindings/test_webhook.py`:

```python
from codemie.triggers.bindings.gitlab_webhook_security import GitLabWebhookSecurity


@pytest.mark.parametrize(
    "fixture_name, expected_action",
    [
        ("gitlab_mr_open_payload", "open"),
        ("gitlab_mr_close_payload", "close"),
        ("gitlab_mr_merge_payload", "merge"),
        ("gitlab_mr_update_payload", "update"),
        ("gitlab_mr_reopen_payload", "reopen"),
        ("gitlab_mr_approved_payload", "approved"),
        ("gitlab_mr_unapproved_payload", "unapproved"),
    ],
)
def test_all_mr_actions_extracted_and_self_filter(request, fixture_name, expected_action):
    payload = request.getfixturevalue(fixture_name)
    assert GitLabWebhookSecurity.extract_mr_action(payload) == expected_action
    # A filter containing exactly this action allows it...
    assert GitLabWebhookSecurity.validate_mr_event_type(payload, [expected_action]) is True
    # ...and a filter without it rejects it.
    others = [a for a in GitLabWebhookSecurity.MR_ACTIONS if a != expected_action]
    assert GitLabWebhookSecurity.validate_mr_event_type(payload, others) is False


def test_push_event_is_not_an_mr_action(gitlab_push_payload):
    assert GitLabWebhookSecurity.extract_mr_action(gitlab_push_payload) is None
    # With an MR filter set, a push event is filtered out (not an MR action).
    assert GitLabWebhookSecurity.validate_mr_event_type(gitlab_push_payload, ["open"]) is False
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd /Users/oleg_sotnichenko/codemie-dev/codemie
poetry run pytest tests/triggers/bindings/test_webhook.py -v
poetry run pytest tests/triggers/bindings/test_gitlab_webhook_security.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Create `codemie/docs/webhook-configuration.md`**

```markdown
# Webhook Configuration Guide

## Supported Providers

- GitHub
- GitLab

## GitHub Webhooks

1. GitHub repository → Settings → Webhooks.
2. Payload URL: `https://<your-codemie-host>/v1/webhooks/{webhook_id}`.
3. Secret: generate a secret in CodeMie settings and paste it into GitHub.
4. Optional **GitHub Event Filter**: comma-separated event types, e.g. `pull_request,push`.

Security: GitHub signs each delivery with HMAC-SHA256 (SHA-1 fallback for legacy),
sent in `X-Hub-Signature-256`. CodeMie verifies the signature against the stored secret.

## GitLab Webhooks

1. GitLab project → Settings → Webhooks.
2. URL: `https://<your-codemie-host>/v1/webhooks/{webhook_id}`.
3. **Secret token**: generate a token in CodeMie (`GitLab Webhook Secret Token`) and paste
   the same value into GitLab's "Secret token" field.
4. Trigger: enable **Merge request events**.

### Security model (important)

GitLab does **not** sign the payload. It sends the configured secret token *verbatim*
in the `X-Gitlab-Token` header. CodeMie verifies it with a constant-time comparison
against the stored `GitLab Webhook Secret Token`. (This differs from GitHub, which sends
an HMAC signature.) Always use HTTPS so the plaintext token is not exposed in transit.

### MR event filtering

Set **GitLab MR Event Filter** to a comma-separated list of merge-request actions. Only
those actions trigger the workflow; every other MR action returns `400` and does not run.
Leaving the filter empty triggers on **all** MR actions (default behavior).

Supported actions:

- `open` — merge request created
- `close` — merge request closed
- `merge` — merge request merged
- `update` — merge request updated (commits, description, etc.)
- `reopen` — merge request reopened
- `approved` — approval added
- `unapproved` — approval removed

Examples:

- `open` — only on creation.
- `merge` — only when merged.
- `open,merge,reopen` — on creation, merge, or reopen.

## Troubleshooting

- **401 Invalid GitLab token** — the `X-Gitlab-Token` sent by GitLab does not match the
  stored `GitLab Webhook Secret Token`. Re-copy the token into both places (no extra spaces).
- **400 GitLab MR event action '…' is not allowed** — the MR action is not in your filter.
  Add it to **GitLab MR Event Filter**, or clear the filter to allow all actions.
- **Workflow not triggering** — confirm the webhook is enabled, the token matches, and the
  MR action is within the filter. Check CodeMie logs for the delivery and verification result.
```

- [ ] **Step 4: License header + lint on new/changed backend files**

```bash
cd /Users/oleg_sotnichenko/codemie-dev/codemie
make license-check
make ruff
```

Expected: no missing headers; no lint/format issues. (Docs/markdown are excluded from the Python license check.)

- [ ] **Step 5: Sanity check the full suite for regressions**

```bash
cd /Users/oleg_sotnichenko/codemie-dev/codemie
make test-harness
```

Expected: PASS (this is the sdlc-light Stage 6 gate; see local test-harness setup notes if it needs `ENV=local`/superadmin config).

- [ ] **Step 6: Commit**

```bash
cd /Users/oleg_sotnichenko/codemie-dev
git add codemie/tests/triggers/bindings/test_webhook.py \
        codemie/docs/webhook-configuration.md
git commit -m "EPMCDME-8384: Add GitLab MR action coverage tests and webhook configuration docs"
```

---

## Summary

Implements GitLab MR webhook event filtering by mirroring the existing GitHub security pattern in `webhook.py`:

1. **GitLab security module** — detection, **plaintext** `X-Gitlab-Token` verification (constant-time compare — *not* HMAC), and MR action extraction/validation.
2. **WebhookService** — `_verify_gitlab_token` helper + a Priority 1.5 branch in `verify_security_header()`, guarded by `gitlab_token and is_gitlab_webhook` so non-GitLab and unconfigured webhooks fall through unchanged.
3. **Shared fixtures** — all seven MR actions + a push negative case, via `conftest.py`.
4. **UI** — two unconditional fields (`gitlab_webhook_token` sensitive, `gitlab_event_filter`), matching the GitHub fields' rendering.
5. **Coverage + docs** — parametrized per-action tests and a provider setup/troubleshooting guide.

**Key properties:**
- Correct GitLab auth model (plaintext token), so it actually works against real GitLab.
- Empty filter = all MR actions (no regression); invalid token → 401, filtered action → 400 (mirrors GitHub).
- Backward compatible with GitHub and legacy-header webhooks.
- No DB migration (JSONB storage).
