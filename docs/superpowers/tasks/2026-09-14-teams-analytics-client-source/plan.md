# Teams Analytics Client-Source Test Coverage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the missing unit and seam test coverage for the already-implemented, already-committed (`cb20fe61d`) client-source attribution feature on `conversation_assistant_usage`. No production code changes.

**Architecture:** Four independent test files/additions, one per layer the feature already touches: the `client_context.py` normalization/ContextVar module, `ConversationMonitoringService.send_conversation_metric`, the `AssistantRequestHandler` construction-time snapshot seam in `save_chat_history`, and a ContextVar-reset autouse fixture so none of the above leak state across the suite.

**Tech Stack:** pytest, `unittest.mock.patch`/`patch.object`, existing repo test conventions (see `.ai-run/guides/testing/testing-patterns.md`).

**Spec:** No `spec.md` for this task — requirements arrived inline from the caller; see the Acceptance criteria below and `docs/superpowers/tasks/2026-09-14-teams-analytics-client-source/technical-analysis.md` for the Stage 1 research this plan is built from.

## Global Constraints

- No production code changes anywhere in `src/` — the feature (`client_context.py`, `main.py`, `assistant_handlers.py`, `conversation_service.py`, `conversation_monitoring_service.py`, `metrics_constants.py`) is already implemented and committed at HEAD. Every task below is test-only.
- No changes to any file under `src/codemie/service/analytics/` — wiring `client_source` into analytics widgets is explicitly out of scope and deferred.
- Detection stays header-only: no new test may reference `is_teams_bot_request` or `teams_authentication_resolver.py`.
- `MetricsAttributes.CLIENT_SOURCE` and `MetricsAttributes.CODEMIE_CLIENT` are deliberately distinct fields — no new test may assert on `CODEMIE_CLIENT` as a stand-in for `client_source`, or treat them as aliases.
- Commit per task using the repository's existing convention.

---

## Acceptance criteria

- [ ] `normalize_client_source` has unit coverage for: `None`, empty string, whitespace-only, every recognized Teams value (`teams-bot`, `teams`, `msteams`, case-insensitive and whitespace-padded), every recognized platform value (`web`, `webapp`, `desktop`, `ui`), and unrecognized values including `codemie-cli`/`codemie-code` — with exact expected `ClientSource` values asserted.
- [ ] `set_client_source` / `get_client_source` / `clear_client_source` have a round-trip test, including that `clear_client_source()` resets to `ClientSource.PLATFORM` (not `None`).
- [ ] `ConversationMonitoringService.send_conversation_metric` has a test asserting `MetricsAttributes.CLIENT_SOURCE` is present with the exact value for `client_source=ClientSource.TEAMS`, `client_source=ClientSource.OTHER`, and `client_source=None` (defaults to `ClientSource.PLATFORM.value`).
- [ ] A seam test proves the value set on the `client_context` ContextVar before `AssistantRequestHandler.__init__` survives, unchanged, into the `client_source` kwarg passed to `ConversationService.upsert_chat_history` inside `save_chat_history` — for both `ClientSource.TEAMS` and `ClientSource.OTHER`.
- [ ] The `_current_client_source` ContextVar is reset around every test in the suite (new tests and pre-existing ones), so no test leaks a non-default value to any other test in the same process.
- [ ] No production file under `src/` is modified by this work.

---

## Task 1: Unit tests for `client_context.py`

**Files:**
- Create: `tests/codemie/rest_api/security/test_client_context.py`
- Create: `tests/codemie/rest_api/security/conftest.py`

**Interfaces:**
- Consumes: `codemie.rest_api.security.client_context.{ClientSource, normalize_client_source, set_client_source, get_client_source, clear_client_source}` (existing, unchanged).
- Produces: an autouse `reset_client_source` fixture in the new `conftest.py`, reused implicitly by every test file under `tests/codemie/rest_api/security/` (including Task 3's additions, which live in a different directory and get their own reset — see Task 3).

Test-first: yes — no test file exists for `client_context.py` today (`grep -rln client_context tests/` returns zero matches per the technical analysis); every assertion below fails with `ModuleNotFoundError`/collection error until the file is created, then passes against the existing implementation.

- [ ] **Step 1: Write the conftest reset fixture**

```python
# tests/codemie/rest_api/security/conftest.py
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

import pytest

from codemie.rest_api.security.client_context import _current_client_source, ClientSource


@pytest.fixture(autouse=True)
def reset_client_source():
    """Reset the client-source ContextVar before each test.

    Mirrors reset_litellm_context in tests/codemie/service/monitoring/conftest.py:
    prevents a value set by one test (or by production code under test) from
    leaking into the next test in the same process.
    """
    token = _current_client_source.set(ClientSource.PLATFORM)
    yield
    _current_client_source.reset(token)
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/codemie/rest_api/security/test_client_context.py
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

import pytest

from codemie.rest_api.security.client_context import (
    ClientSource,
    clear_client_source,
    get_client_source,
    normalize_client_source,
    set_client_source,
)


class TestNormalizeClientSource:
    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_absent_or_blank_defaults_to_platform(self, raw):
        assert normalize_client_source(raw) is ClientSource.PLATFORM

    @pytest.mark.parametrize("raw", ["teams-bot", "TEAMS-BOT", "  teams-bot  ", "teams", "Teams", "msteams", "MSTeams"])
    def test_recognized_teams_values_map_to_teams(self, raw):
        assert normalize_client_source(raw) is ClientSource.TEAMS

    @pytest.mark.parametrize("raw", ["web", "WEB", "  web  ", "webapp", "desktop", "Desktop", "ui", "UI"])
    def test_recognized_platform_values_map_to_platform(self, raw):
        assert normalize_client_source(raw) is ClientSource.PLATFORM

    @pytest.mark.parametrize("raw", ["codemie-cli", "codemie-code", "some-unknown-client", "TEAMSBOT"])
    def test_unrecognized_values_map_to_other(self, raw):
        assert normalize_client_source(raw) is ClientSource.OTHER


class TestClientSourceContextVar:
    def test_default_is_platform(self):
        assert get_client_source() is ClientSource.PLATFORM

    def test_set_and_get(self):
        set_client_source(ClientSource.TEAMS)
        assert get_client_source() is ClientSource.TEAMS

    def test_clear_resets_to_platform_not_none(self):
        set_client_source(ClientSource.OTHER)
        clear_client_source()
        assert get_client_source() is ClientSource.PLATFORM
```

- [ ] **Step 3: Run the tests**

Run: `poetry run pytest tests/codemie/rest_api/security/test_client_context.py -v`
Expected: all PASS against the existing `client_context.py` implementation (no production code is changed by this task).

- [ ] **Step 4: Commit**

```bash
git add tests/codemie/rest_api/security/test_client_context.py tests/codemie/rest_api/security/conftest.py
git commit -m "test: add coverage for client_context normalization and ContextVar trio"
```

---

## Task 2: Tests for `ConversationMonitoringService.send_conversation_metric` client-source attribute

**Files:**
- Create: `tests/codemie/service/monitoring/test_conversation_monitoring_service.py`

**Interfaces:**
- Consumes: `codemie.service.monitoring.conversation_monitoring_service.ConversationMonitoringService.send_conversation_metric(user, assistant, tokens_usage, time_elapsed, conversation_id, llm_model, status, request_id=None, client_source=None)` (existing signature, `src/codemie/service/monitoring/conversation_monitoring_service.py:48-81`); `codemie.rest_api.security.client_context.ClientSource`; `codemie.service.monitoring.metrics_constants.MetricsAttributes.CLIENT_SOURCE`.
- Produces: nothing consumed by later tasks — this is a leaf test file.

Test-first: yes — `grep -rln conversation_monitoring tests/` returns zero matches per the technical analysis; these tests fail with `ModuleNotFoundError` until written, then pass against the existing `send_conversation_metric` implementation.

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/service/monitoring/test_conversation_monitoring_service.py
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from unittest.mock import patch

import pytest

from codemie.core.models import TokensUsage
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.base import ConversationStatus
from codemie.rest_api.security.client_context import ClientSource
from codemie.rest_api.security.user import User
from codemie.service.monitoring.conversation_monitoring_service import ConversationMonitoringService
from codemie.service.monitoring.metrics_constants import MetricsAttributes


@pytest.fixture
def mock_assistant():
    return Assistant(
        name="test_assistant",
        description="Test Assistant",
        project="test",
        toolkits=[],
        system_prompt="",
        llm_model_type="test_model",
        slug="test",
        mcp_servers=[],
        assistant_ids=[],
    )


@pytest.fixture
def mock_user():
    return User(name="test_user", username="test@example.com", id="test_id")


@pytest.fixture
def tokens_usage():
    return TokensUsage(input_tokens=10, output_tokens=20, money_spent=0.01)


@patch.object(ConversationMonitoringService, "send_count_metric")
@pytest.mark.parametrize(
    "client_source,expected_value",
    [
        (ClientSource.TEAMS, ClientSource.TEAMS.value),
        (ClientSource.OTHER, ClientSource.OTHER.value),
        (None, ClientSource.PLATFORM.value),
    ],
)
def test_send_conversation_metric_emits_client_source(
    mock_send_count_metric, mock_assistant, mock_user, tokens_usage, client_source, expected_value
):
    ConversationMonitoringService.send_conversation_metric(
        user=mock_user,
        assistant=mock_assistant,
        tokens_usage=tokens_usage,
        time_elapsed=1.5,
        conversation_id="conv-1",
        llm_model="test-model",
        status=ConversationStatus.SUCCESS,
        client_source=client_source,
    )

    _, call_kwargs = mock_send_count_metric.call_args
    assert call_kwargs["attributes"][MetricsAttributes.CLIENT_SOURCE] == expected_value
```

- [ ] **Step 2: Run the tests**

Run: `poetry run pytest tests/codemie/service/monitoring/test_conversation_monitoring_service.py -v`
Expected: all PASS against the existing implementation (which already defaults `None` to `ClientSource.PLATFORM.value` at `conversation_monitoring_service.py:77`).

- [ ] **Step 3: Commit**

```bash
git add tests/codemie/service/monitoring/test_conversation_monitoring_service.py
git commit -m "test: cover client_source attribute on conversation_assistant_usage metric"
```

---

## Task 3: Seam test — construction-time client-source snapshot survives into `save_chat_history`

**Files:**
- Modify: `tests/codemie/rest_api/handlers/test_assistant_handlers.py` (add tests to the existing `TestSaveChatHistory` class, around line 507-560)

**Interfaces:**
- Consumes: `codemie.rest_api.security.client_context.{set_client_source, clear_client_source, ClientSource}`; `AssistantRequestHandler.__init__` (`self.client_source = get_client_source()`, `src/codemie/rest_api/handlers/assistant_handlers.py:100-108`); `save_chat_history` (`client_source=self.client_source` passed into `ConversationService.upsert_chat_history`, `:414-453`); the existing `StandardAssistantHandler`, `chat_history_data_save_true` fixtures already defined in this file.
- Produces: nothing consumed by later tasks.

Test-first: yes — no existing test in this file sets the client-context ContextVar or asserts on the `client_source` kwarg reaching `upsert_chat_history`; these are new assertions that fail until added, then pass against the existing snapshot-at-`__init__` implementation.

- [ ] **Step 1: Write the failing seam tests**

Add inside `class TestSaveChatHistory` (same file, same class as the existing `test_save_chat_history_passes_background_tasks_through`), after the last existing test method:

```python
    @pytest.mark.parametrize("source", [ClientSource.TEAMS, ClientSource.OTHER])
    def test_save_chat_history_forwards_client_source_snapshotted_at_construction(
        self, chat_history_data_save_true, source
    ):
        """client_source is snapshotted at __init__ time and survives unchanged
        into upsert_chat_history, even though save_chat_history runs later and
        may run in a copy_context() that has lost the ContextVar value
        (see AssistantRequestHandler.__init__ inline comment)."""
        set_client_source(source)
        try:
            user = Mock(spec=User, id="user-123")
            assistant = Mock(id="assistant-123", project="test-project")
            fresh_handler = StandardAssistantHandler(assistant, user, "request-uuid")
        finally:
            clear_client_source()  # simulate the ContextVar reverting after this "request"

        with (
            patch("codemie.service.llm_service.utils.set_llm_context"),
            patch("codemie.rest_api.handlers.assistant_handlers.ConversationService") as mock_service,
            patch("codemie.rest_api.handlers.assistant_handlers.request_summary_manager") as mock_manager,
        ):
            mock_manager.get_summary.return_value = Mock(tokens_usage=Mock())

            fresh_handler.save_chat_history(chat_history_data_save_true)

            _, call_kwargs = mock_service.upsert_chat_history.call_args
            assert call_kwargs["client_source"] is source
```

Add the two required imports at the top of the file alongside the existing imports:

```python
from codemie.rest_api.security.client_context import ClientSource, clear_client_source, set_client_source
```

- [ ] **Step 2: Add the ContextVar reset for this file**

This test module lives under `tests/codemie/rest_api/handlers/`, not `tests/codemie/rest_api/security/`, so it is not covered by Task 1's `conftest.py`. Add a local autouse fixture inside `class TestSaveChatHistory` so the `set_client_source` call above cannot leak into any other test in this file or elsewhere in the suite:

```python
    @pytest.fixture(autouse=True)
    def reset_client_source_after_test(self):
        yield
        clear_client_source()
```

- [ ] **Step 3: Run the tests**

Run: `poetry run pytest tests/codemie/rest_api/handlers/test_assistant_handlers.py -v -k client_source`
Expected: both parametrized cases PASS against the existing snapshot-at-`__init__` implementation.

- [ ] **Step 4: Run the whole file to confirm no leakage into pre-existing tests**

Run: `poetry run pytest tests/codemie/rest_api/handlers/test_assistant_handlers.py -v`
Expected: all tests PASS, including the pre-existing ones in `TestSaveChatHistory` that ran before the new parametrized tests in file/collection order.

- [ ] **Step 5: Commit**

```bash
git add tests/codemie/rest_api/handlers/test_assistant_handlers.py
git commit -m "test: seam test proving client_source snapshot survives save_chat_history"
```

---

## Task 4: Narrow-scope verification run

**Files:**
- None (verification only; no new files).

**Interfaces:**
- Consumes: the test files created/modified in Tasks 1-3.
- Produces: nothing.

Test-first: no — this task runs the tests already written in Tasks 1-3 together to confirm the `conftest.py` reset fixture and the local `reset_client_source_after_test` fixture jointly prevent cross-test ContextVar leakage across both directories in a single pytest process, which no single-file run in Tasks 1-3 exercises.

- [ ] **Step 1: Run the narrow scope together**

Run: `poetry run pytest tests/codemie/service/monitoring tests/codemie/rest_api/security tests/codemie/rest_api/handlers/test_assistant_handlers.py -q`
Expected: all PASS, in any collection order, confirming no ContextVar state from Task 3's `set_client_source(ClientSource.TEAMS/OTHER)` calls leaks into Task 1's or Task 2's tests (or vice versa) when run in the same process.

- [ ] **Step 2: Commit**

No new files to add; if Step 1 required a fix to a fixture written in an earlier task, amend that task's commit instead of creating a new one. Otherwise this step is a no-op — proceed to handoff.

---

## Negative-constraint check

- **No production code changes:** every task above creates or modifies only files under `tests/`. No task touches any file under `src/`.
- **No analytics-widget wiring:** no task references any file under `src/codemie/service/analytics/`; Task 2 asserts only on the write-side attribute dict passed to `send_count_metric`.
- **Header-only detection preserved:** no task references `is_teams_bot_request` or `teams_authentication_resolver.py`; Task 1's tests exercise only `normalize_client_source`'s string-matching logic.
- **`CLIENT_SOURCE` vs `CODEMIE_CLIENT` kept distinct:** Task 2 asserts exclusively on `MetricsAttributes.CLIENT_SOURCE`; no task reads or asserts on `MetricsAttributes.CODEMIE_CLIENT`.
- **No whole-suite quality gate, browser verification, code-review, or commit-of-artifacts task:** Task 4 runs only the narrow scope named in the caller's own verification commands, not `make test`; the full-suite and lint/license gates belong to the calling flow's own stages, not this plan.
