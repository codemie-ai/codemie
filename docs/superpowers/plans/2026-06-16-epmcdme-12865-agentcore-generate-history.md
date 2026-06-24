# EPMCDME-12865: Fix missing chat_history in agentcore generate() path

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Forward `self.request.history` to `BedrockOrchestratorService.invoke_bedrock_assistant()` inside `AIToolsAgent.generate()`, matching the streaming and react paths.

**Architecture:** Single one-line change in `generate()`. History is already populated in `self.request.history` by `_populate_conversation_history()` before `generate()` is called — it is simply not forwarded. No changes needed to the orchestrator, runtime service, or request builder.

**Tech Stack:** Python, pytest, `unittest.mock`

---

## File Map

| File | Change |
|---|---|
| `src/codemie/agents/assistant_agent.py` | Add `chat_history=self.request.history` at line ~534 |
| `tests/codemie/agents/test_assistant_agent.py` | Add one new test function |

---

### Task 1: Write the failing test

**Files:**
- Modify: `tests/codemie/agents/test_assistant_agent.py`

- [ ] **Step 1: Add imports at the top of the test file**

Append after the existing imports (after `from codemie.chains.base import GenerationResult`):

```python
from unittest.mock import MagicMock, patch

from codemie.agents.assistant_agent import AIToolsAgent
from codemie.core.constants import ChatRole
from codemie.core.models import AssistantChatRequest, ChatMessage
from codemie.rest_api.models.assistant import AssistantType
```

- [ ] **Step 2: Add the helper and test function**

Append at the end of the file:

```python
_AGENT_MOD = "codemie.agents.assistant_agent"


def _make_agentcore_agent(history=None):
    """Construct a minimal AIToolsAgent without calling __init__."""
    request = AssistantChatRequest(text="hello", conversation_id="conv-1")
    if history is not None:
        request.history = history

    assistant = MagicMock()
    assistant.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    assistant.bedrock_agentcore_runtime = MagicMock()
    assistant.bedrock_agentcore_runtime.runtime_arn = "arn:aws:bedrock:us-east-1::agent/test"
    assistant.bedrock = None

    agent = AIToolsAgent.__new__(AIToolsAgent)
    agent.request = request
    agent.assistant = assistant
    agent.conversation_id = request.conversation_id
    agent.agent_name = "test-agent"
    return agent


@patch(f"{_AGENT_MOD}.AIToolsAgent._get_tool_errors", return_value=[])
@patch(f"{_AGENT_MOD}.AIToolsAgent._persist_generated_workspace_files")
@patch(f"{_AGENT_MOD}.BedrockOrchestratorService.invoke_bedrock_assistant")
def test_generate_forwards_chat_history_to_orchestrator(mock_invoke, _mock_persist, _mock_errors):
    mock_invoke.return_value = {"output": "response text"}
    history = [ChatMessage(role=ChatRole.USER, message="prior turn")]

    agent = _make_agentcore_agent(history=history)
    agent.generate()

    mock_invoke.assert_called_once()
    _, kwargs = mock_invoke.call_args
    assert kwargs["chat_history"] == history
```

- [ ] **Step 3: Run the test and confirm it fails**

```bash
poetry run pytest tests/codemie/agents/test_assistant_agent.py::test_generate_forwards_chat_history_to_orchestrator -v
```

Expected output: **FAILED** — `AssertionError` because `chat_history` is not in the call kwargs (the current code omits it).

---

### Task 2: Apply the fix

**Files:**
- Modify: `src/codemie/agents/assistant_agent.py:531-535`

- [ ] **Step 1: Add the missing parameter**

In `generate()`, change:

```python
                response = BedrockOrchestratorService.invoke_bedrock_assistant(
                    assistant=self.assistant,
                    input_text=self.request.text or "",
                    conversation_id=self.conversation_id,
                )
```

to:

```python
                response = BedrockOrchestratorService.invoke_bedrock_assistant(
                    assistant=self.assistant,
                    input_text=self.request.text or "",
                    conversation_id=self.conversation_id,
                    chat_history=self.request.history,
                )
```

- [ ] **Step 2: Run the test and confirm it passes**

```bash
poetry run pytest tests/codemie/agents/test_assistant_agent.py::test_generate_forwards_chat_history_to_orchestrator -v
```

Expected output: **PASSED**

- [ ] **Step 3: Run the full test module to check for regressions**

```bash
poetry run pytest tests/codemie/agents/test_assistant_agent.py -v
```

Expected output: all tests **PASSED**

---

### Task 3: Commit

- [ ] **Step 1: Stage the two changed files**

```bash
git add src/codemie/agents/assistant_agent.py \
        tests/codemie/agents/test_assistant_agent.py
```

- [ ] **Step 2: Commit**

```bash
git commit -m "EPMCDME-12865: Forward chat_history in agentcore generate() path"
```
