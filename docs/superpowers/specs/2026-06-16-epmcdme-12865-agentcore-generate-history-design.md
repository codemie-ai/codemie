# EPMCDME-12865: Fix missing chat_history in agentcore generate() path

## Problem

`AIToolsAgent.generate()` calls `BedrockOrchestratorService.invoke_bedrock_assistant()` without forwarding `chat_history`. The other two agentcore invocation paths already pass it:

| Path | chat_history forwarded? |
|---|---|
| `_agent_streaming()` | Yes — fixed in EPMCDME-12240 commit `931ee1b3d` |
| `_invoke_agent()` | Yes — fixed in EPMCDME-12240 commit `931ee1b3d` |
| `generate()` | **No — oversight, this ticket** |

By the time `generate()` runs, `self.request.history` is already populated — either from the JSON payload or from DB via `_populate_conversation_history()` in `StandardAssistantHandler.process_request()`. The parameter is simply not forwarded.

## Fix

**File:** `src/codemie/agents/assistant_agent.py`

Add `chat_history=self.request.history` to the `invoke_bedrock_assistant()` call inside `generate()`:

```python
response = BedrockOrchestratorService.invoke_bedrock_assistant(
    assistant=self.assistant,
    input_text=self.request.text or "",
    conversation_id=self.conversation_id,
    chat_history=self.request.history,   # add this line
)
```

No changes needed elsewhere — the orchestrator, runtime service, and request builder already handle `chat_history` correctly.

## Tests

**File:** `tests/codemie/agents/test_assistant_agent.py`

Add a test that:
1. Constructs an `AIToolsAgent` with a mock agentcore assistant and a non-empty `request.history`
2. Calls `agent.generate()`
3. Asserts `BedrockOrchestratorService.invoke_bedrock_assistant` was called with `chat_history=request.history`

## Out of scope

- History precedence logic in `_populate_conversation_history()` (JSON payload vs DB): existing behaviour is unchanged.
- Other bedrock invocation paths: no issues found.
