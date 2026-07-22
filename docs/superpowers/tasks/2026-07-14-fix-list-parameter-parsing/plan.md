# Fix Tool Call List Parameter Parsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix intermittent failures when tool calls contain list parameters by replacing Python repr serialization with JSON serialization

**Architecture:** Three bug sites in the LangGraph event processing layer incorrectly serialize tool call arguments using `str()` (Python repr format with single quotes) instead of `json.dumps()` (JSON format with double quotes). This causes downstream parsing failures in `parse_to_dict()` when payloads contain complex structures or nested quotes. Fix requires updating serialization at all three sites plus updating test assertions that encode the buggy behavior.

**Tech Stack:** Python, LangGraph, pytest

---

## File Structure

**Files to Modify:**
- `src/codemie/agents/langgraph_event_adapter.py` - Add `import json`, fix two serialization sites (lines 160, 227)
- `src/codemie/agents/langgraph_agent.py` - Fix one serialization site (line 1406)
- `tests/codemie/service/assistant/test_langgraph_assistant.py` - Update assertion at line 171
- `tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py` - Update assertions at lines 647, 669

**Files to Create:**
- `tests/codemie/agents/test_langgraph_event_adapter.py` - New test coverage for list-typed parameters

---

### Task 1: Fix langgraph_event_adapter.py serialization in parse_update_type

**Files:**
- Modify: `src/codemie/agents/langgraph_event_adapter.py:15` (add import)
- Modify: `src/codemie/agents/langgraph_event_adapter.py:160` (fix serialization)
- Test: Run existing tests to verify no regressions

**Test-first: no** — This is a pure refactor; existing tests will fail until all sites are fixed

- [ ] **Step 1: Add json import**

Open `src/codemie/agents/langgraph_event_adapter.py` and add `json` to the imports section after line 15:

```python
import uuid
import json
from typing import Any
```

- [ ] **Step 2: Fix serialization at line 160**

Replace line 160 in `parse_update_type` method:

```python
# Before:
tool_args = str(unpack_json_strings(tool_call["args"]))

# After:
tool_args = json.dumps(unpack_json_strings(tool_call["args"]))
```

- [ ] **Step 3: Run existing tests to verify compile**

Run: `pytest tests/codemie/service/assistant/test_langgraph_assistant.py::TestLangGraphAgent::test_process_chunk_agent_tool_calls_updates_type -v`

Expected: FAIL with assertion error (test expects old repr format)

- [ ] **Step 4: Commit**

```bash
git add src/codemie/agents/langgraph_event_adapter.py
git commit -m "EPMCDME-13152: Add json import and fix serialization in parse_update_type"
```

---

### Task 2: Fix langgraph_event_adapter.py serialization in handle_supervisor_tool_calls

**Files:**
- Modify: `src/codemie/agents/langgraph_event_adapter.py:227` (fix serialization)
- Test: Run existing tests to verify no regressions

**Test-first: no** — This is a pure refactor; existing tests will fail until all sites are fixed

- [ ] **Step 1: Fix serialization at line 227**

Replace line 227 in `handle_supervisor_tool_calls` method:

```python
# Before:
tool_args = str(unpack_json_strings(tool_call["args"]))

# After:
tool_args = json.dumps(unpack_json_strings(tool_call["args"]))
```

- [ ] **Step 2: Run existing tests to verify compile**

Run: `pytest tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py::TestLangGraphMultiAssistantSupervisor::test_parse_supervisor_update_type_regular_tool_call -v`

Expected: FAIL with assertion error (test expects old repr format)

- [ ] **Step 3: Commit**

```bash
git add src/codemie/agents/langgraph_event_adapter.py
git commit -m "EPMCDME-13152: Fix serialization in handle_supervisor_tool_calls"
```

---

### Task 3: Fix langgraph_agent.py serialization in _get_tool_call_args

**Files:**
- Modify: `src/codemie/agents/langgraph_agent.py:1406` (fix serialization)
- Test: Verify import exists

**Test-first: no** — This is a pure refactor; no direct test for this method

- [ ] **Step 1: Verify json import exists**

Check that `src/codemie/agents/langgraph_agent.py` already imports json (it should, given the file uses JSON elsewhere). If not, add it.

- [ ] **Step 2: Fix serialization at line 1406**

Replace line 1406 in `_get_tool_call_args` static method:

```python
# Before:
return tool_name, str(unpacked_args)

# After:
return tool_name, json.dumps(unpacked_args)
```

- [ ] **Step 3: Commit**

```bash
git add src/codemie/agents/langgraph_agent.py
git commit -m "EPMCDME-13152: Fix serialization in _get_tool_call_args"
```

---

### Task 4: Update test assertion in test_langgraph_assistant.py

**Files:**
- Modify: `tests/codemie/service/assistant/test_langgraph_assistant.py:171` (update assertion)

**Test-first: no** — Updating existing test to match fixed behavior

- [ ] **Step 1: Update assertion from repr to JSON format**

Replace line 171 in `test_process_chunk_agent_tool_calls_updates_type`:

```python
# Before:
agent_for_parse_update._on_tool_start.assert_called_once_with("test_tool", "{'arg': 1}", run_id=ANY)

# After:
agent_for_parse_update._on_tool_start.assert_called_once_with("test_tool", '{"arg": 1}', run_id=ANY)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/codemie/service/assistant/test_langgraph_assistant.py::TestLangGraphAgent::test_process_chunk_agent_tool_calls_updates_type -v`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/codemie/service/assistant/test_langgraph_assistant.py
git commit -m "EPMCDME-13152: Update test assertion to expect JSON format"
```

---

### Task 5: Update test assertions in test_langgraph_multi_assistant_supervisor.py

**Files:**
- Modify: `tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py:647` (update first assertion)
- Modify: `tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py:669` (update second assertion)

**Test-first: no** — Updating existing tests to match fixed behavior

- [ ] **Step 1: Update first assertion at line 647**

Replace line 647 in `test_parse_supervisor_update_type_regular_tool_call`:

```python
# Before:
assert call_args.args == ("search_tool", "{'query': 'test'}")

# After:
assert call_args.args == ("search_tool", '{"query": "test"}')
```

- [ ] **Step 2: Update second assertion at line 669**

Replace line 669 in `test_process_chunk_supervisor_uses_leaf_namespace_for_nested_subagent_tools`:

```python
# Before:
assert call_args.args == ("search_tool", "{'query': 'test'}")

# After:
assert call_args.args == ("search_tool", '{"query": "test"}')
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py::TestLangGraphMultiAssistantSupervisor::test_parse_supervisor_update_type_regular_tool_call tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py::TestLangGraphMultiAssistantSupervisor::test_process_chunk_supervisor_uses_leaf_namespace_for_nested_subagent_tools -v`

Expected: PASS (both tests)

- [ ] **Step 4: Commit**

```bash
git add tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py
git commit -m "EPMCDME-13152: Update supervisor test assertions to expect JSON format"
```

---

### Task 6: Add regression test for list-typed parameters

**Files:**
- Create: `tests/codemie/agents/test_langgraph_event_adapter.py`

**Test-first: yes** — New test coverage to prevent regression

- [ ] **Step 1: Write failing test for list-typed parameters**

Create `tests/codemie/agents/test_langgraph_event_adapter.py`:

```python
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

import json
from unittest.mock import MagicMock, ANY
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from codemie.agents.langgraph_event_adapter import LangGraphEventAdapter


class TestLangGraphEventAdapter:
    @pytest.fixture
    def mock_agent(self):
        agent = MagicMock()
        agent.is_finish_reason_tool_calls.return_value = True
        agent._safe_check_for_truncation = MagicMock()
        agent._on_llm_end = MagicMock()
        agent._on_tool_start = MagicMock()
        agent._tool_call_id_to_uuid = MagicMock(return_value=uuid4())
        return agent

    def test_parse_update_type_serializes_list_args_as_json(self, mock_agent):
        """Test that tool args containing lists are serialized as JSON, not Python repr."""
        adapter = LangGraphEventAdapter(mock_agent)
        
        ai_message = AIMessage(content="")
        ai_message.tool_calls = [
            {
                "name": "excel_tool",
                "args": {"sheet_names": ["Sheet1", "Sheet2"], "query": "analyze data"},
                "id": "call-123",
            }
        ]
        
        value = {"agent": {"messages": [ai_message]}}
        adapter.parse_update_type(value)
        
        mock_agent._on_tool_start.assert_called_once()
        call_args = mock_agent._on_tool_start.call_args
        
        # Verify args are JSON (double quotes), not Python repr (single quotes)
        tool_args_str = call_args.args[1]
        parsed = json.loads(tool_args_str)  # Should not raise JSONDecodeError
        assert parsed == {"sheet_names": ["Sheet1", "Sheet2"], "query": "analyze data"}

    def test_handle_supervisor_tool_calls_serializes_list_args_as_json(self, mock_agent):
        """Test that supervisor tool args containing lists are serialized as JSON."""
        adapter = LangGraphEventAdapter(mock_agent)
        mock_agent._check_is_handoff_tool = MagicMock(return_value=False)
        
        ai_message = AIMessage(content="")
        ai_message.tool_calls = [
            {
                "name": "email_tool",
                "args": {
                    "recipient_emails": ["user1@example.com", "user2@example.com"],
                    "subject": "Test",
                    "body": "Message",
                },
                "id": "call-456",
            }
        ]
        
        adapter.handle_supervisor_tool_calls(ai_message, author="supervisor")
        
        mock_agent._on_tool_start.assert_called_once()
        call_args = mock_agent._on_tool_start.call_args
        
        # Verify args are JSON (double quotes), not Python repr (single quotes)
        tool_args_str = call_args.args[1]
        parsed = json.loads(tool_args_str)  # Should not raise JSONDecodeError
        assert parsed["recipient_emails"] == ["user1@example.com", "user2@example.com"]

    def test_parse_update_type_handles_complex_nested_payloads(self, mock_agent):
        """Test that complex payloads with nested quotes serialize correctly."""
        adapter = LangGraphEventAdapter(mock_agent)
        
        ai_message = AIMessage(content="")
        ai_message.tool_calls = [
            {
                "name": "cypher_query",
                "args": {
                    "query": 'MATCH (n) WHERE n.name = "test" RETURN n',
                    "repository_ids": ["6a565360-fd33-4bd6-9510-aa333670bf72"],
                },
                "id": "call-789",
            }
        ]
        
        value = {"agent": {"messages": [ai_message]}}
        adapter.parse_update_type(value)
        
        mock_agent._on_tool_start.assert_called_once()
        call_args = mock_agent._on_tool_start.call_args
        
        # Verify args parse as valid JSON without mangling
        tool_args_str = call_args.args[1]
        parsed = json.loads(tool_args_str)  # Should not raise JSONDecodeError
        assert parsed["query"] == 'MATCH (n) WHERE n.name = "test" RETURN n'
        assert parsed["repository_ids"] == ["6a565360-fd33-4bd6-9510-aa333670bf72"]
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/codemie/agents/test_langgraph_event_adapter.py -v`

Expected: PASS (all three tests) - tests should pass because the fixes are already in place from Tasks 1-3

- [ ] **Step 3: Commit**

```bash
git add tests/codemie/agents/test_langgraph_event_adapter.py
git commit -m "EPMCDME-13152: Add regression tests for list-typed tool parameters"
```

---

### Task 7: Run full test suite to verify no regressions

**Files:**
- Test: All affected test files

**Test-first: no** — Verification step

- [ ] **Step 1: Run all agent tests**

Run: `pytest tests/codemie/agents/ tests/codemie/service/assistant/ -v`

Expected: PASS (all tests)

- [ ] **Step 2: Run linting**

Run: `make ruff`

Expected: No errors

- [ ] **Step 3: Verify fix with reproduction scenario**

Test the exact scenario from the ticket: tool calls with list parameters in `repository_ids` field should now parse correctly without `TypeError: Input should be a valid list` errors.

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Fix three bug sites: langgraph_event_adapter.py lines 160, 227 (Tasks 1, 2)
- ✅ Fix third site: langgraph_agent.py line 1406 (Task 3)
- ✅ Add import json (Task 1)
- ✅ Update three test assertions (Tasks 4, 5)
- ✅ Add test coverage for list-typed parameters (Task 6)

**Placeholder scan:**
- ✅ No TBD/TODO placeholders
- ✅ All code blocks contain actual implementation
- ✅ Exact file paths and line numbers provided
- ✅ Commit messages follow project convention (EPMCDME-####: Description)

**Type consistency:**
- ✅ `json.dumps()` used consistently at all three sites
- ✅ Test assertions use proper JSON string format with double quotes
- ✅ Method signatures unchanged (only implementation changed)
