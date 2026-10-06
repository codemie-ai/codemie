# EPMCDME-14859 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the supervisor's history compactor from mistaking a sub-agent's intermediate tool-call `AIMessage` for its terminal handoff answer, which currently orphans a `ToolMessage` and triggers an Azure/LiteLTM 400; also stop the streaming path from concatenating a raw backend error onto an already-successful answer.

**Architecture:** Two independent, single-guard-clause fixes in existing pre-model-hook / streaming-error code. No new modules, no new state.

**Tech Stack:** Python 3.12, LangGraph/`langgraph_supervisor`, LangChain message types (`AIMessage`/`ToolMessage`/`HumanMessage`), pytest.

## Global Constraints

- Do not modify `_queue_pending_handoff_message`, `_append_pending_handoffs`, `_is_parallel_supervisor_handoff_message`, `sanitize_rich_history_for_llm`, or any parallel-handoff/`[no response]` logic — spec requires these stay untouched.
- Do not port `EPMCDME-13889` code (`_resolve_pending_handoff_author`, `_stamp_subagent_identity`, `_close_unanswered_tool_calls`) — reference only, out of scope.
- All 8 existing tests in `tests/codemie/agents/test_langgraph_multi_assistant_handoffs.py` must continue to pass unmodified.
- Follow repo commit convention `EPMCDME-14859: <Description>` per `.ai-run/guides/standards/git-workflow.md`.

---

### Task 1: Guard `_consume_pending_handoff_message` against intermediate tool-call messages

**Files:**
- Modify: `src/codemie/agents/supervisor/history.py:102-137` (`_consume_pending_handoff_message`)
- Test: `tests/codemie/agents/test_langgraph_multi_assistant_handoffs.py` (append new test method to `TestLangGraphMultiAssistantHandoffs`)

**Interfaces:**
- Consumes: nothing new — `AIMessage.tool_calls` (existing `langchain_core.messages.AIMessage` attribute, always a list, empty when the message carries no tool call).
- Produces: no signature change to `_consume_pending_handoff_message` — same `(message, filtered_messages, pending_parallel_handoffs, pending_single_handoffs) -> bool` contract. Callers (`_process_handoff_message`) are unaffected.

Test-first: yes — new test reproduces the exact ticket shape (supervisor hands off to `jiratesthelp`, which itself calls `generic_jira_tool` before its terminal answer) and asserts the intermediate tool-call pair survives while only the terminal message becomes the handoff result.

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/agents/test_langgraph_multi_assistant_handoffs.py`, inside `class TestLangGraphMultiAssistantHandoffs` (after `test_strip_handoff_back_messages_pre_model_hook_sanitizes_orphan_tool_calls_when_filter_is_noop`, i.e. at the end of the class):

```python
    def test_strip_handoff_back_messages_pre_model_hook_preserves_subagent_tool_call_before_terminal_answer(self):
        # Reproduces EPMCDME-14859: the sub-agent (jiratesthelp) itself calls a tool
        # (generic_jira_tool) before producing its terminal answer. Before the fix,
        # _consume_pending_handoff_message matched the FIRST AIMessage named "jiratesthelp"
        # (the intermediate tool-call message) as the handoff's terminal result, stripping
        # the assistant tool-call declaration while leaving the ToolMessage in place — an
        # orphaned role='tool' message that Azure/OpenAI rejects with a 400.
        supervisor_handoff_call = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "transfer_to_jiratesthelp",
                    "args": {"task": "Get the Jira issue summary"},
                    "id": "call-123",
                    "type": "tool_call",
                }
            ],
        )
        single_parent_handoff = ToolMessage(
            content="Get the Jira issue summary",
            name="transfer_to_jiratesthelp",
            tool_call_id="call-123",
            additional_kwargs={METADATA_KEY_SUBAGENT_TASK: True},
            response_metadata={METADATA_KEY_HANDOFF_DESTINATION: "jiratesthelp"},
        )
        subagent_tool_call = AIMessage(
            content="",
            name="jiratesthelp",
            tool_calls=[
                {
                    "name": "generic_jira_tool",
                    "args": {"issue": "EPMCDME-14859"},
                    "id": "jira-call-1",
                    "type": "tool_call",
                }
            ],
        )
        subagent_tool_result = ToolMessage(
            content="Summary: manual or guarded",
            name="generic_jira_tool",
            tool_call_id="jira-call-1",
        )
        subagent_terminal_answer = AIMessage(
            content="The Jira issue summary is: manual or guarded",
            name="jiratesthelp",
        )

        result = _strip_handoff_back_messages_pre_model_hook(
            {
                "messages": [
                    HumanMessage(content="What is the Jira issue summary?"),
                    supervisor_handoff_call,
                    single_parent_handoff,
                    subagent_tool_call,
                    subagent_tool_result,
                    subagent_terminal_answer,
                ]
            }
        )

        assert result["llm_input_messages"] == [
            HumanMessage(content="What is the Jira issue summary?"),
            supervisor_handoff_call,
            subagent_tool_call,
            subagent_tool_result,
            ToolMessage(
                content="The Jira issue summary is: manual or guarded",
                name="transfer_to_jiratesthelp",
                tool_call_id="call-123",
            ),
        ]

        # Protocol-level assertion: every ToolMessage in the output has a preceding
        # AIMessage.tool_calls entry with a matching id — the exact invariant Azure/OpenAI
        # enforces and this bug violated.
        llm_input = result["llm_input_messages"]
        seen_tool_call_ids = set()
        for message in llm_input:
            if isinstance(message, AIMessage):
                seen_tool_call_ids.update(tc["id"] for tc in message.tool_calls)
            elif isinstance(message, ToolMessage):
                assert message.tool_call_id in seen_tool_call_ids, (
                    f"orphaned ToolMessage: tool_call_id={message.tool_call_id!r} has no "
                    "preceding AIMessage.tool_calls entry"
                )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_multi_assistant_handoffs.py::TestLangGraphMultiAssistantHandoffs::test_strip_handoff_back_messages_pre_model_hook_preserves_subagent_tool_call_before_terminal_answer -v`

Expected: FAIL. The current code consumes `subagent_tool_call` (the first `AIMessage(name="jiratesthelp")` encountered) as the handoff result instead of `subagent_terminal_answer`, so `result["llm_input_messages"]` will be missing `subagent_tool_call`/`subagent_tool_result` and the `ToolMessage` content will be wrong (or the trailing `sanitize_rich_history_for_llm` sweep will additionally drop the now-orphaned `subagent_tool_result`, making the assertion fail differently). Either way the exact-equality assertion on `llm_input_messages` fails.

- [ ] **Step 3: Write minimal implementation**

In `src/codemie/agents/supervisor/history.py`, modify `_consume_pending_handoff_message` (starts at line 102):

```python
def _consume_pending_handoff_message(
    message: BaseMessage,
    filtered_messages: list[BaseMessage],
    pending_parallel_handoffs: dict[str, deque[AIMessage]],
    pending_single_handoffs: dict[str, deque[tuple[str, str | None]]],
) -> bool:
    if not isinstance(message, AIMessage):
        return False
    if message.tool_calls:
        # An AIMessage that still declares tool_calls is an intermediate step, never the
        # terminal handoff answer. Consuming it here would strip the assistant's tool-call
        # declaration while leaving its matching ToolMessage in place, producing an orphaned
        # role='tool' message that Azure/OpenAI rejects (EPMCDME-14859). Let it fall through
        # to _append_unique_message so it (and its ToolMessage response) stay intact; the
        # pending handoff remains queued until this sub-agent's real terminal AIMessage arrives.
        return False

    author_name = message.name or ""
    if author_name and pending_parallel_handoffs.get(author_name):
        handoff_message = pending_parallel_handoffs[author_name].popleft()
        _append_unique_message(filtered_messages, handoff_message)
        _append_unique_message(
            filtered_messages,
            ToolMessage(
                content=extract_text_from_llm_output(str(message.content or "")),
                name=handoff_message.tool_calls[0]["name"],
                tool_call_id=handoff_message.tool_calls[0]["id"],
            ),
        )
        return True

    if author_name and pending_single_handoffs.get(author_name):
        tool_name, tool_call_id = pending_single_handoffs[author_name].popleft()
        _append_unique_message(
            filtered_messages,
            ToolMessage(
                content=extract_text_from_llm_output(str(message.content or "")),
                name=tool_name,
                tool_call_id=tool_call_id,
            ),
        )
        return True

    return False
```

Only the added `if message.tool_calls: return False` guard (plus its comment) changes; the rest of the function body is unchanged from the current source.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_multi_assistant_handoffs.py::TestLangGraphMultiAssistantHandoffs::test_strip_handoff_back_messages_pre_model_hook_preserves_subagent_tool_call_before_terminal_answer -v`

Expected: PASS.

- [ ] **Step 5: Run the full existing test file to confirm no regressions**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_multi_assistant_handoffs.py -v`

Expected: all tests PASS (8 pre-existing + 1 new = 9 total).

- [ ] **Step 6: Commit**

```bash
git add src/codemie/agents/supervisor/history.py tests/codemie/agents/test_langgraph_multi_assistant_handoffs.py
git commit -m "EPMCDME-14859: Guard handoff consumption against intermediate tool-call messages"
```

---

### Task 2: Stop streaming error path from appending raw error text onto a successful answer

**Files:**
- Modify: `src/codemie/agents/langgraph_agent.py:816-838` (`_send_error_to_thread`)
- Test: `tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py` (append new test class)

**Interfaces:**
- Consumes: `LangGraphAgent._send_error_to_thread(self, e: Exception, execution_start: float, chunks_collector: list[str]) -> None` (existing method, signature unchanged) and `LangGraphAgent._process_chunks(self, chunks_collector: list[str], cfg: Config, llm_error_code: str | None = None) -> tuple[str, str | None]` (existing method, unchanged).
- Produces: no new public interface. Behavior change only: `chunks_collector` is left untouched (not appended to) when it already contains non-empty content when `_send_error_to_thread` is invoked.

Test-first: yes — new test constructs a `LangGraphAgent` with a mocked supervisor (following the existing `agent_config_with_subagents`/`supervisor_agent` fixture pattern in `test_langgraph_multi_assistant_supervisor.py`), pre-populates `chunks_collector` with a successful answer, calls `_send_error_to_thread`, and asserts the `StreamedGenerationResult` sent to `thread_generator` contains only the original successful content with no error text appended.

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py`, as a new top-level test class after `TestLangGraphMultiAssistantSupervisor` (reuse its `mock_user`, `mock_request`, `mock_regular_tool`, `mock_agent_tool`, `agent_config_with_subagents`, `supervisor_agent` fixtures by defining this class to inherit from it):

```python
class TestSendErrorToThreadPreservesSuccessfulAnswer(TestLangGraphMultiAssistantSupervisor):
    def test_send_error_to_thread_does_not_append_error_when_answer_already_succeeded(self, supervisor_agent):
        # Reproduces the secondary presentation defect from EPMCDME-14859: when a sub-agent
        # already streamed a successful answer into chunks_collector and a LATER model call
        # then raises, _send_error_to_thread must not concatenate the raw/friendly error text
        # onto the already-successful content.
        supervisor_agent.thread_generator = MagicMock()
        supervisor_agent.thread_context = None
        chunks_collector = ["The Jira issue summary is: manual or guarded"]

        supervisor_agent._send_error_to_thread(
            RuntimeError("litellm.BadRequestError: AzureException BadRequestError"),
            execution_start=0.0,
            chunks_collector=chunks_collector,
        )

        sent_json = supervisor_agent.thread_generator.send.call_args[0][0]
        sent_payload = json.loads(sent_json)
        assert sent_payload["generated"] == "The Jira issue summary is: manual or guarded"

    def test_send_error_to_thread_appends_error_when_nothing_succeeded_yet(self, supervisor_agent):
        # Preserves existing behavior: if nothing succeeded before the exception, the
        # user-facing error message must still be surfaced.
        supervisor_agent.thread_generator = MagicMock()
        supervisor_agent.thread_context = None
        chunks_collector = []

        supervisor_agent._send_error_to_thread(
            RuntimeError("boom"),
            execution_start=0.0,
            chunks_collector=chunks_collector,
        )

        sent_json = supervisor_agent.thread_generator.send.call_args[0][0]
        sent_payload = json.loads(sent_json)
        assert sent_payload["generated"] != ""
```

Add `import json` to the top of `tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py` if not already present (check the existing import block at the top of the file — the file does not currently import `json`).

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py::TestSendErrorToThreadPreservesSuccessfulAnswer::test_send_error_to_thread_does_not_append_error_when_answer_already_succeeded -v`

Expected: FAIL. Current `_send_error_to_thread` unconditionally appends `user_message` to `chunks_collector`, so `generated` will be `"The Jira issue summary is: manual or guarded" + <error message>` instead of just the original content.

- [ ] **Step 3: Write minimal implementation**

In `src/codemie/agents/langgraph_agent.py`, modify `_send_error_to_thread` (starts at line 816):

```python
    def _send_error_to_thread(self, e: Exception, execution_start: float, chunks_collector: list[str]) -> None:
        """Format *e* as a user error and push the final SSE chunk to thread_generator."""
        record_exception_on_span(e)
        time_elapsed = time() - execution_start
        error_response = handle_agent_exception(e)
        llm_error_code = error_response.get_error().error_code.value
        user_message = (
            error_response.get_error().message
            if config.HIDE_AGENT_STREAMING_EXCEPTIONS
            else self.extended_error(error_response, e)
        )
        if not "".join(chunks_collector).strip():
            # Only surface the error text when nothing succeeded yet. If a sub-agent already
            # streamed a complete answer and a later model call then raised, concatenating the
            # raw/friendly error onto that answer would corrupt an otherwise successful
            # user-facing response (EPMCDME-14859 secondary defect). execution_error below still
            # carries llm_error_code regardless, so the failure is still tracked internally.
            chunks_collector.append(user_message)
        generated, execution_error = self._process_chunks(chunks_collector, config, llm_error_code)
        self.thread_generator.send(
            StreamedGenerationResult(
                generated=generated,
                generated_chunk="",
                last=True,
                time_elapsed=time_elapsed,
                context=self.thread_context,
                execution_error=execution_error,
            ).model_dump_json()
        )
```

Only the added `if not "".join(chunks_collector).strip():` guard (plus its comment) around the existing `chunks_collector.append(user_message)` line changes.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py::TestSendErrorToThreadPreservesSuccessfulAnswer -v`

Expected: both new tests PASS.

- [ ] **Step 5: Run the full supervisor test file to confirm no regressions**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py -v`

Expected: all tests PASS (pre-existing tests + 2 new).

- [ ] **Step 6: Commit**

```bash
git add src/codemie/agents/langgraph_agent.py tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py
git commit -m "EPMCDME-14859: Do not append backend error onto an already-successful streamed answer"
```

---

### Task 3: Full regression pass

**Files:** none modified — verification only.

**Interfaces:** none.

Test-first: no — this task runs existing + new suites together as a final gate, no new test code.

- [ ] **Step 1: Run the full targeted regression suite**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_multi_assistant_handoffs.py tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py tests/codemie/agents/test_langgraph_multi_assistant_runtime.py -v`

Expected: all PASS. This confirms the history guard (Task 1) and the streaming guard (Task 2) don't interact badly with runtime-level supervisor tests.

- [ ] **Step 2: Run linter and formatter on changed files**

Run:
```bash
poetry run ruff format src/codemie/agents/supervisor/history.py src/codemie/agents/langgraph_agent.py tests/codemie/agents/test_langgraph_multi_assistant_handoffs.py tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py
poetry run ruff check src/codemie/agents/supervisor/history.py src/codemie/agents/langgraph_agent.py tests/codemie/agents/test_langgraph_multi_assistant_handoffs.py tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py
```

Expected: no formatting diffs left uncommitted, no lint errors. If `ruff format` changes anything, `git add` and commit those files again before continuing.

- [ ] **Step 3: Commit any formatting fixes (only if Step 2 produced changes)**

```bash
git add -u
git commit -m "EPMCDME-14859: Apply ruff formatting"
```
