# EPMCDME-12505 — Assistant survives deleted datasource: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chatting with an assistant whose attached datasource was deleted works with the remaining datasources instead of returning 500.

**Architecture:** One service helper, `AssistantService.drop_missing_context`, replaces the fail-fast `check_context` in `build_agent` and backs the router's `_filter_invalid_datasources`. It filters `assistant.context` in memory (new list, no persistence) before `ToolkitService.get_tools`, so no downstream path ever sees a deleted datasource. The dead `MissingContextException` path is removed.

**Tech Stack:** Python 3.12, FastAPI, SQLModel, pytest + unittest.mock.

**Spec:** `docs/superpowers/tasks/2026-09-28-EPMCDME-12505-assistant-broken-deleted-datasource/spec.md` (read with `technical-analysis.md` in the same dir).

## Global Constraints

- Run every pytest with `LC_ALL=en_US.UTF-8` (uk_UA locale breaks unrelated tests).
- Test command prefix: `LC_ALL=en_US.UTF-8 poetry run pytest`.
- The stored assistant is never persisted from `build_agent` (no `assistant.update()` / `save()`).
- Existence = `(repo_name, context_type)` within `assistant.project` — same rule as today's `_filter_invalid_datasources`.
- Bedrock assistants keep their early return before the context filter.
- No commit, push or MR without the owner's explicit OK — "Commit" steps below are staged only (`git add`), commit happens on OK.

## Review Focus

1. Same `repo_name` exists but with another type (e.g. KB named like a deleted CODE repo) → must be dropped, not kept. Pinned in Task 1.
2. The `assistant` object passed to `build_agent` is later saved by some handler → filtered context would silently persist. Task 3 asserts `update`/`save` are not called inside `build_agent`; reviewer should grep handlers for `assistant.update(` after `build_agent`.
3. `assistant.context` is `None` or `[]` → no DB query, no change. Pinned in Task 1.
4. All contexts deleted → agent builds with empty context, no exception. Pinned in Task 3.
5. Sub-assistant with a deleted datasource under a healthy parent → parent builds (covered because `AssistantFactory.build` calls `build_agent`; reviewer confirms no other `check_context` caller remains via grep in Task 4).

---

### Task 1: `drop_missing_context` helper in AssistantService

Test-first: yes — `test_drop_missing_context_*` fail with `AttributeError: drop_missing_context` before the helper exists.

**Files:**
- Modify: `src/codemie/service/assistant_service.py` (add two classmethods next to `check_context`, ~L941; add imports)
- Create: `tests/codemie/service/test_assistant_service_drop_missing_context.py`

**Interfaces:**
- Produces:
  - `AssistantService._existing_context_keys(project: str, names: set[str]) -> set[tuple[str, ContextType]]` — DB lookup, body moved verbatim from router `_filter_invalid_datasources`.
  - `AssistantService.drop_missing_context(assistant: Assistant) -> list[Context]` — assigns a new filtered list to `assistant.context`, returns dropped entries; `[]` and no query when context is falsy.

- [ ] **Step 1: Write the failing tests**

```python
from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.models.assistant import Assistant, Context, ContextType
from codemie.service.assistant_service import AssistantService

KB = Context(context_type=ContextType.KNOWLEDGE_BASE, name="kb-alive")
CODE_GONE = Context(context_type=ContextType.CODE, name="repo-deleted")
PROVIDER_GONE = Context(context_type=ContextType.PROVIDER, name="prov-deleted")

KEYS = "codemie.service.assistant_service.AssistantService._existing_context_keys"


def _assistant(context):
    assistant = MagicMock(spec=Assistant)
    assistant.project = "proj"
    assistant.name = "asst"
    assistant.id = "asst-1"
    assistant.context = context
    return assistant


@pytest.mark.parametrize("context", [None, []])
def test_drop_missing_context_no_context_skips_query(context):
    assistant = _assistant(context)
    with patch(KEYS) as keys:
        assert AssistantService.drop_missing_context(assistant) == []
    keys.assert_not_called()
    assert assistant.context == context


def test_drop_missing_context_all_present_keeps_everything():
    assistant = _assistant([KB])
    with patch(KEYS, return_value={("kb-alive", ContextType.KNOWLEDGE_BASE)}):
        assert AssistantService.drop_missing_context(assistant) == []
    assert assistant.context == [KB]


def test_drop_missing_context_drops_deleted_code_and_provider():
    assistant = _assistant([KB, CODE_GONE, PROVIDER_GONE])
    with patch(KEYS, return_value={("kb-alive", ContextType.KNOWLEDGE_BASE)}) as keys:
        dropped = AssistantService.drop_missing_context(assistant)
    keys.assert_called_once_with("proj", {"kb-alive", "repo-deleted", "prov-deleted"})
    assert dropped == [CODE_GONE, PROVIDER_GONE]
    assert assistant.context == [KB]


def test_drop_missing_context_same_name_other_type_is_missing():
    assistant = _assistant([CODE_GONE])
    with patch(KEYS, return_value={("repo-deleted", ContextType.KNOWLEDGE_BASE)}):
        dropped = AssistantService.drop_missing_context(assistant)
    assert dropped == [CODE_GONE]
    assert assistant.context == []


def test_drop_missing_context_assigns_new_list_and_never_persists():
    original = [KB, CODE_GONE]
    assistant = _assistant(original)
    with patch(KEYS, return_value={("kb-alive", ContextType.KNOWLEDGE_BASE)}):
        AssistantService.drop_missing_context(assistant)
    assert original == [KB, CODE_GONE]
    assert assistant.context is not original
    assistant.update.assert_not_called()
    assistant.save.assert_not_called()
```

- [ ] **Step 2: Run to verify RED**

Run: `LC_ALL=en_US.UTF-8 poetry run pytest tests/codemie/service/test_assistant_service_drop_missing_context.py -v`
Expected: FAIL — `AttributeError: ... has no attribute '_existing_context_keys'` / `'drop_missing_context'`.

- [ ] **Step 3: Implement**

Add imports to `assistant_service.py`:

```python
from sqlmodel import Session, and_, select

from codemie.rest_api.models.assistant import Context, ContextType  # extend the existing import block
from codemie.rest_api.models.index import IndexInfo
```

(Check first whether `IndexInfo` is already imported in the file; don't duplicate.)

Add next to `check_context`:

```python
    @staticmethod
    def _existing_context_keys(project: str, names: set[str]) -> set[tuple[str, ContextType]]:
        with Session(IndexInfo.get_engine()) as session:
            statement = select(IndexInfo.repo_name, IndexInfo.index_type).where(
                and_(IndexInfo.project_name == project, IndexInfo.repo_name.in_(names))
            )
            existing = session.exec(statement).all()
        return {(repo_name, Context.index_info_type_from_index_type(index_type)) for repo_name, index_type in existing}

    @classmethod
    def drop_missing_context(cls, assistant: Assistant) -> list[Context]:
        """Drop datasources that no longer exist in the assistant's project. In memory only."""
        if not assistant.context:
            return []
        existing = cls._existing_context_keys(assistant.project, {ctx.name for ctx in assistant.context})
        kept, dropped = [], []
        for ctx in assistant.context:
            (kept if (ctx.name, ctx.context_type) in existing else dropped).append(ctx)
        assistant.context = kept
        return dropped
```

- [ ] **Step 4: Run to verify GREEN**

Run: same command. Expected: 6 passed.

- [ ] **Step 5: Stage**

```bash
git add src/codemie/service/assistant_service.py tests/codemie/service/test_assistant_service_drop_missing_context.py
```

---

### Task 2: Router `_filter_invalid_datasources` delegates to the helper

Test-first: yes — `test_filter_invalid_datasources_uses_service_lookup` fails because the current body opens a real DB `Session` instead of `AssistantService._existing_context_keys`.

**Files:**
- Modify: `src/codemie/rest_api/routers/assistant.py:2855-2891`
- Create: `tests/codemie/rest_api/routers/test_assistant_filter_invalid_datasources.py`

**Interfaces:**
- Consumes: `AssistantService.drop_missing_context(assistant) -> list[Context]` (Task 1).
- Produces: `_filter_invalid_datasources(assistant) -> None` — same signature and in-place behaviour as today.

- [ ] **Step 1: Write the failing test**

```python
from unittest.mock import MagicMock, patch

from codemie.rest_api.models.assistant import Assistant, Context, ContextType
from codemie.rest_api.routers.assistant import _filter_invalid_datasources

KB = Context(context_type=ContextType.KNOWLEDGE_BASE, name="kb-alive")
CODE_GONE = Context(context_type=ContextType.CODE, name="repo-deleted")


def test_filter_invalid_datasources_uses_service_lookup():
    assistant = MagicMock(spec=Assistant)
    assistant.project = "proj"
    assistant.name = "asst"
    assistant.context = [KB, CODE_GONE]
    with patch(
        "codemie.service.assistant_service.AssistantService._existing_context_keys",
        return_value={("kb-alive", ContextType.KNOWLEDGE_BASE)},
    ):
        _filter_invalid_datasources(assistant)
    assert assistant.context == [KB]


def test_filter_invalid_datasources_no_context_is_noop():
    assistant = MagicMock(spec=Assistant)
    assistant.context = []
    with patch("codemie.service.assistant_service.AssistantService._existing_context_keys") as keys:
        _filter_invalid_datasources(assistant)
    keys.assert_not_called()
```

- [ ] **Step 2: Run to verify RED**

Run: `LC_ALL=en_US.UTF-8 poetry run pytest tests/codemie/rest_api/routers/test_assistant_filter_invalid_datasources.py -v`
Expected: the first test FAILS (DB/engine error from the real `Session`, or context not filtered).

- [ ] **Step 3: Implement**

Replace the body of `_filter_invalid_datasources`:

```python
def _filter_invalid_datasources(assistant: Assistant):
    """
    Filter out datasources that don't exist in the assistant's target project.
    This prevents errors when cloning or editing assistants across projects.
    Modifies the assistant object in place.
    """
    if dropped := AssistantService.drop_missing_context(assistant):
        logger.info(
            f"Filtered out {len(dropped)} invalid datasource(s) for assistant "
            f"'{assistant.name}' in project '{assistant.project}'"
        )
```

Then remove `Session`, `select`, `and_` from the `sqlmodel` import in this file **only if** `ruff check src/codemie/rest_api/routers/assistant.py` reports them unused.

- [ ] **Step 4: Run to verify GREEN**

Run: same command. Expected: 2 passed.

- [ ] **Step 5: Stage**

```bash
git add src/codemie/rest_api/routers/assistant.py tests/codemie/rest_api/routers/test_assistant_filter_invalid_datasources.py
```

---

### Task 3: `build_agent` drops missing context instead of failing (regression)

Test-first: yes — `test_build_agent_with_deleted_datasource_builds_with_remaining` fails with `MissingContextException` from `check_context`.

**Files:**
- Modify: `src/codemie/service/assistant_service.py:541` (call site) and delete `check_context` (~L941-951)
- Create: `tests/codemie/service/test_assistant_service_build_agent_deleted_datasource.py`
- Modify: `tests/codemie/service/test_assistant_service_headers.py` (L42, L126: patch target `check_context` → `drop_missing_context`)
- Delete: `tests/codemie/service/test_assistant_service_check_context.py`, `test_assistant_service_check_context_no_items.py`, `test_assistant_service_check_context_valid.py` (behaviour now covered by Task 1 tests)

**Interfaces:**
- Consumes: `AssistantService.drop_missing_context`, `AssistantService._existing_context_keys` (Task 1).

- [ ] **Step 1: Write the failing test**

Copy the patch stack and assistant arrangement from `test_build_agent_with_request_headers` in `test_assistant_service_headers.py`, **without** patching `check_context`:

```python
from unittest.mock import Mock, patch

import pytest

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant, Context, ContextType
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService

KB = Context(context_type=ContextType.KNOWLEDGE_BASE, name="kb-alive")
CODE_GONE = Context(context_type=ContextType.CODE, name="repo-deleted")


def _assistant(context):
    assistant = Mock(spec=Assistant)
    assistant.id = "asst-123"
    assistant.name = "Test Assistant"
    assistant.description = "d"
    assistant.system_prompt = "You are a helpful assistant"
    assistant.context = context
    assistant.toolkits = []
    assistant.llm_model_type = "claude-sonnet-4"
    assistant.temperature = 0.7
    assistant.top_p = 0.9
    assistant.project = "test-project"
    assistant.bedrock = None
    assistant.smart_tool_selection_enabled = False
    assistant.prompt_variables = []
    assistant.is_global = False
    assistant.mcp_servers = []
    return assistant


def _user():
    user = Mock(spec=User)
    user.id = "user-123"
    user.name = "Test User"
    user.full_name = "Test User Full Name"
    user.username = "test@email.com"
    return user


@pytest.mark.parametrize(
    "context, existing, expected",
    [
        ([KB, CODE_GONE], {("kb-alive", ContextType.KNOWLEDGE_BASE)}, [KB]),
        ([CODE_GONE], set(), []),
    ],
    ids=["one-deleted", "all-deleted"],
)
@patch("codemie.service.assistant_service.AssistantService._existing_context_keys")
@patch("codemie.service.assistant_service.Conversation.find_by_id", return_value=None)
@patch("codemie.service.assistant_service.AIToolsAgent")
@patch("codemie.service.assistant_service.LangGraphAgent")
@patch("codemie.service.assistant_service.config")
@patch("codemie.service.assistant_service.ToolkitService.get_tools", return_value=[])
@patch("codemie.service.assistant_service.llm_service")
@patch("codemie.service.assistant_service.set_llm_context")
@patch("codemie.service.assistant_service.build_unique_file_objects", return_value={})
@patch("codemie.service.assistant_service.BedrockOrchestratorService.is_bedrock_assistant", return_value=False)
def test_build_agent_with_deleted_datasource_builds_with_remaining(
    _bedrock, _files, _llm_ctx, mock_llm_service, mock_get_tools, mock_config, _lg, mock_aitools, _find, mock_keys,
    context, existing, expected,
):
    mock_keys.return_value = existing
    mock_llm_service.get_react_llms.return_value = []
    mock_llm_service.default_llm_model = "claude-sonnet-4"
    mock_config.ENABLE_LANGGRAPH_AITOOLS_AGENT = False
    mock_aitools.return_value = Mock()
    assistant = _assistant(list(context))

    AssistantService.build_agent(
        assistant=assistant,
        request=AssistantChatRequest(text="Hello", file_names=[]),
        user=_user(),
        request_uuid="req-123",
        thread_generator=None,
        tool_callbacks=None,
    )

    mock_get_tools.assert_called_once()
    assert mock_get_tools.call_args[0][0].context == expected
    assistant.update.assert_not_called()
    assistant.save.assert_not_called()
```

(If `get_tools` receives the assistant as a keyword, read it from `call_args[1]["assistant"]` instead — check the call at assistant_service.py ~L565.)

- [ ] **Step 2: Run to verify RED**

Run: `LC_ALL=en_US.UTF-8 poetry run pytest tests/codemie/service/test_assistant_service_build_agent_deleted_datasource.py -v`
Expected: FAIL inside `check_context` — the Mock's `get_deleted_context()` is truthy, so the fail-fast check blows up (`MissingContextException` or `TypeError` while formatting the Mock). Either way `get_tools` is never reached: that is the bug.

- [ ] **Step 3: Implement**

In `build_agent`, replace `cls.check_context(assistant)` with:

```python
        if dropped := cls.drop_missing_context(assistant):
            logger.warning(
                f"Skipping deleted datasource(s) for assistant. AssistantId={assistant.id}, "
                f"Project={assistant.project}, Datasources={[ctx.name for ctx in dropped]}"
            )
```

Delete the `check_context` classmethod. Update the two `@patch('...AssistantService.check_context')` decorators in `test_assistant_service_headers.py` to `...AssistantService.drop_missing_context` (keep the mock parameter, set `return_value=[]`). Delete the three `test_assistant_service_check_context*.py` files.

- [ ] **Step 4: Run to verify GREEN**

Run: `LC_ALL=en_US.UTF-8 poetry run pytest tests/codemie/service/test_assistant_service_build_agent_deleted_datasource.py tests/codemie/service/test_assistant_service_headers.py tests/codemie/service/test_assistant_service_drop_missing_context.py -v`
Expected: all pass.

- [ ] **Step 5: Stage**

```bash
git add -A src/codemie/service/assistant_service.py tests/codemie/service/
```

---

### Task 4: Remove the dead `MissingContextException` path

Test-first: no — pure dead-code removal after Task 3 (nothing raises it any more); verified by grep, ruff and the affected suites.

**Files:**
- Modify: `src/codemie/rest_api/models/assistant.py` (delete `MissingContextException` L70, delete `get_deleted_context` ~L870)
- Modify: `src/codemie/rest_api/routers/assistant.py` (delete both `except MissingContextException as mce:` blocks ~L2344 and ~L2450; drop it from the import at L55)
- Modify: `src/codemie/service/assistant_service.py` (drop it from the import at L38)

- [ ] **Step 1: Delete the code listed above.**

- [ ] **Step 2: Verify nothing references it**

Run: `git grep -n "MissingContextException\|get_deleted_context\|check_context" -- src tests`
Expected: no output.

- [ ] **Step 3: Lint + affected suites + 12384 regression suites**

Run:
```bash
poetry run ruff check src/codemie/rest_api/models/assistant.py src/codemie/rest_api/routers/assistant.py src/codemie/service/assistant_service.py
LC_ALL=en_US.UTF-8 poetry run pytest tests/codemie/service/ tests/codemie/rest_api/routers/ tests/codemie/agents/tools/kb/test_search_kb.py tests/codemie/agents/tools/code/test_code_tools_health.py tests/codemie/rest_api/models/test_index_health_fields.py tests/codemie/service/tools/test_tool_execution_search.py -q
```
Expected: ruff clean; all pass.

- [ ] **Step 4: Stage**

```bash
git add -A src/codemie/rest_api/models/assistant.py src/codemie/rest_api/routers/assistant.py src/codemie/service/assistant_service.py
```

---

## Out of scope (MR follow-up note)

- `tool_execution_service._get_context_tools` still raises for a deleted CODE datasource on the direct tool-invoke API.
- Cleanup on datasource delete and in stored versions (option B).
