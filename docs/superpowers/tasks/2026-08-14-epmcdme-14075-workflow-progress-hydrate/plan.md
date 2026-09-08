# Workflow Chat Hydrate After Reload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development (sdlc-light Stage 4 is inline TDD; do not dispatch subagent-driven-development). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a mid-run browser reload, `GET /conversations/{id}` restores the current workflow step (`in_progress: true`) and overall `execution_status`, and SSE client disconnect does not stop the workflow.

**Architecture:** Keep stub-on-write / materialize-on-GET. Include persisted `WorkflowExecutionState` rows (including `IN_PROGRESS` with `output=None`) as thoughts, copy `execution.overall_status` onto the hydrated turn, and make `DualQueue.is_closed()` follow the persistence queue so agents keep running after streaming disconnect. No SSE rejoin, no ThoughtConsumer token persistence, no new endpoints.

**Tech Stack:** Python, FastAPI/Pydantic, SQLModel `WorkflowExecutionState`, pytest, existing DualQueue (`ThreadedGenerator` + `ThoughtQueue`).

## Requirements

Ticket **EPMCDME-14075**: workflow chat progress is empty after reload while execution is still running.

- Conversation GET must expose the current step as a thought with `in_progress: true`.
- Hydrated assistant message must expose `execution_id` and `execution_status` so the UI can tell a run is still alive between steps.
- Client SSE disconnect must not, by itself, stop/abort a workflow execution.
- Intra-step token streaming after reload is not required.
- No regression for completed / aborted / interrupted executions or non-workflow chats (`ThreadedGenerator` still stops on disconnect).

## Global Constraints

- Ticket prefix: `EPMCDME-14075` in commits.
- Do not implement SSE stream-rejoin or last-event-id.
- Do not persist intra-step token chunks in `ThoughtConsumer`.
- Do not change `_handle_client_disconnect` to call `DualQueue.close()`.
- Do not change plain `ThreadedGenerator` disconnect for assistant chats.
- Do not mock `_get_execution_thoughts` in new hydrate tests; mock `WorkflowExecutionState.get_all_by_fields`.
- Nested `Thought` has no `to_camel`; document actual JSON names in handoff (`in_progress`, not assumed camelCase).
- Production files: `history_materializer.py`, `conversation.py` (`GeneratedMessage`), `dual_queue.py`. Tests: `test_history_materializer.py`, `test_dual_queue.py`.

## File Structure

| File | Responsibility |
|---|---|
| `src/codemie/core/dual_queue.py` | `is_closed()` follows persistence; `send()` skips closed streaming half |
| `src/codemie/rest_api/models/conversation.py` | Optional `execution_status` on `GeneratedMessage` |
| `src/codemie/service/conversation/history_materializer.py` | Include in-progress/failed states; copy overall status; `input_text` from `state.task` |
| `tests/codemie/core/test_dual_queue.py` | Disconnect-vs-persist contract |
| `tests/codemie/service/test_history_materializer.py` | In-progress hydrate + `execution_status` |

Leave unchanged: `rest_api/routers/utils.py` disconnect handler, `ThoughtConsumer`, `LangGraphAgent`, Details APIs, `workflow_service.py` stubs.

---

### Task 1: DualQueue disconnect does not stop persistence

**Test-first: yes — `test_streaming_close_does_not_close_dual_queue` currently fails because `is_closed()` follows the streaming queue.**

**Files:**
- Modify: `src/codemie/core/dual_queue.py`
- Test: `tests/codemie/core/test_dual_queue.py`

**Interfaces:**
- Consumes: `ThreadedGenerator.is_closed()`, `ThoughtQueue.is_closed()`, `ThoughtQueue.send()`
- Produces: `DualQueue.is_closed() -> bool` (True only when persistence is closed / `DualQueue.close()` ran); `DualQueue.send(data)` always writes persistence, skips streaming when streaming is closed

- [ ] **Step 1: Write the failing tests**

Replace `test_is_closed_reflects_streaming_queue` and update `test_client_disconnect_scenario`. Add send-after-stream-close coverage.

In `tests/codemie/core/test_dual_queue.py`, replace `test_is_closed_reflects_streaming_queue` with:

```python
    def test_streaming_close_does_not_close_dual_queue(self, dual_queue, streaming_queue, persistence_queue):
        """Client disconnect closes streaming only; DualQueue stays open for the agent."""
        assert not dual_queue.is_closed()

        streaming_queue.close()

        assert streaming_queue.is_closed()
        assert not persistence_queue.is_closed()
        assert not dual_queue.is_closed()

    def test_is_closed_after_dual_queue_close(self, dual_queue, streaming_queue, persistence_queue):
        dual_queue.close()

        assert streaming_queue.is_closed()
        assert persistence_queue.is_closed()
        assert dual_queue.is_closed()
```

Replace the body of `test_client_disconnect_scenario` so after `streaming_queue.close()`:

- `assert not dual_queue.is_closed()`
- subsequent thought is sent via `dual_queue.send(...)` (not `persistence_queue.send`)
- persistence still receives the after-disconnect thought
- streaming queue is not filled further after close (unread queue must not grow)

```python
    def test_client_disconnect_scenario(self, dual_queue, streaming_queue, persistence_queue):
        thought = Thought(
            id="thought-before-disconnect",
            message="Before disconnect",
            author_name="Agent",
            author_type=ThoughtAuthorType.Agent.value,
            in_progress=False,
        )
        result = StreamedGenerationResult(
            thought=thought,
            context={'execution_state_id': 'state-1'},
        )
        dual_queue.send(result.model_dump_json())

        streaming_queue.close()

        assert not dual_queue.is_closed()
        assert not persistence_queue.is_closed()

        persistence_item = persistence_queue.queue.get()
        assert isinstance(persistence_item, ThoughtQueueItem)
        assert persistence_item.data.id == "thought-before-disconnect"

        # Drain StopIteration from streaming close so we can assert no further puts
        streaming_queue.queue.get()  # StopIteration
        assert streaming_queue.queue.empty()

        thought2 = Thought(
            id="thought-after-disconnect",
            message="After disconnect",
            author_name="Agent",
            author_type=ThoughtAuthorType.Agent.value,
            in_progress=False,
        )
        result2 = StreamedGenerationResult(
            thought=thought2,
            context={'execution_state_id': 'state-1'},
        )
        dual_queue.send(result2.model_dump_json())

        persistence_item2 = persistence_queue.queue.get()
        assert isinstance(persistence_item2, ThoughtQueueItem)
        assert persistence_item2.data.id == "thought-after-disconnect"
        assert streaming_queue.queue.empty()
```

Keep `test_close_both_queues` as-is (it already asserts both halves close via `DualQueue.close()`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/core/test_dual_queue.py::TestDualQueue::test_streaming_close_does_not_close_dual_queue tests/codemie/core/test_dual_queue.py::TestDualQueue::test_client_disconnect_scenario -v`

Expected: FAIL — `assert not dual_queue.is_closed()` fails because `is_closed()` still returns `streaming_queue.is_closed()`.

- [ ] **Step 3: Write minimal implementation**

In `src/codemie/core/dual_queue.py`:

```python
    def send(self, data: Any) -> None:
        """
        Send message to both queues for parallel processing.

        If the streaming connection is already closed, skip the streaming queue
        so unread items do not accumulate. Persistence continues.
        """
        if not self.streaming_queue.is_closed():
            self.streaming_queue.send(data)
        self.persistence_queue.send(data)

    def is_closed(self) -> bool:
        """
        True only when persistence is closed (DualQueue.close() or equivalent).

        Streaming-only close (client disconnect) must not stop workflow agents
        that poll this method.
        """
        return self.persistence_queue.is_closed()
```

Update the class docstring / `is_closed` docstring so they match this contract. Do not change `ThreadedGenerator`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/core/test_dual_queue.py -v`

Expected: PASS (all DualQueue tests, including the rewritten disconnect cases).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/core/dual_queue.py tests/codemie/core/test_dual_queue.py
git commit -m "$(cat <<'EOF'
EPMCDME-14075: Keep workflow running after SSE disconnect

DualQueue.is_closed follows persistence, not streaming, so tab reload
does not stop the agent. Task 1.

EOF
)"
```

---

### Task 2: Materialize in-progress (and failed) execution states as thoughts

**Test-first: yes — `test_in_progress_state_without_output_is_included` fails because `_get_execution_thoughts` drops `output=None` unless status is `ABORTED`.**

**Files:**
- Modify: `src/codemie/service/conversation/history_materializer.py` (`_get_execution_thoughts`)
- Test: `tests/codemie/service/test_history_materializer.py`

**Interfaces:**
- Consumes: `WorkflowExecutionState` rows (`id`, `name`, `output`, `status`, `task`, `history_index`)
- Produces: thought dicts with `in_progress` / `interrupted` / `aborted` derived from status; `input_text` from `state.task`; includes IN_PROGRESS, FAILED, ABORTED even with empty output

- [ ] **Step 1: Write the failing tests**

Add helpers and tests to `tests/codemie/service/test_history_materializer.py`. Do **not** use the `mock_get_thoughts` fixture on these tests.

```python
def _mock_state(
    *,
    state_id: str = "state-1",
    name: str = "Find Items Todo",
    output: str | None = None,
    status: WorkflowExecutionStatusEnum = WorkflowExecutionStatusEnum.IN_PROGRESS,
    task: str | None = "Find changes to do in the document.",
    history_index: int | None = 1,
):
    state = MagicMock()
    state.id = state_id
    state.name = name
    state.output = output
    state.status = status
    state.task = task
    state.history_index = history_index
    return state
```

Patch path: `codemie.core.workflow_models.WorkflowExecutionState.get_all_by_fields` (imported inside `_get_execution_thoughts`). Also patch `WorkflowService` via existing `mock_workflow_service`. `_get_last_completed_state_output` also calls `get_all_by_fields`; the same mock return value is fine.

```python
    def test_in_progress_state_without_output_is_included(self, mock_workflow_service):
        mock_workflow_service.find_workflow_execution_by_id.return_value = _mock_execution(
            status=WorkflowExecutionStatusEnum.IN_PROGRESS, output=""
        )
        in_progress = _mock_state(output=None, status=WorkflowExecutionStatusEnum.IN_PROGRESS)

        with patch(
            "codemie.core.workflow_models.WorkflowExecutionState.get_all_by_fields",
            return_value=[in_progress],
        ):
            result = materialize_workflow_conversation([_execution_ref(index=1)])

        thoughts = result.history[0].thoughts
        assert len(thoughts) == 1
        assert thoughts[0].id == "state-1"
        assert thoughts[0].author_name == "Find Items Todo"
        assert thoughts[0].author_type == "WorkflowState"
        assert thoughts[0].message == ""
        assert thoughts[0].in_progress is True
        assert thoughts[0].interrupted is False
        assert thoughts[0].aborted is False
        assert thoughts[0].input_text == "Find changes to do in the document."

    def test_completed_state_with_output_is_not_in_progress(self, mock_workflow_service):
        mock_workflow_service.find_workflow_execution_by_id.return_value = _mock_execution(
            status=WorkflowExecutionStatusEnum.SUCCEEDED, output="done"
        )
        completed = _mock_state(
            output="step result",
            status=WorkflowExecutionStatusEnum.SUCCEEDED,
            task="Do the step",
        )

        with patch(
            "codemie.core.workflow_models.WorkflowExecutionState.get_all_by_fields",
            return_value=[completed],
        ):
            result = materialize_workflow_conversation([_execution_ref()])

        thought = result.history[0].thoughts[0]
        assert thought.message == "step result"
        assert thought.in_progress is False

    def test_aborted_state_without_output_is_included(self, mock_workflow_service):
        mock_workflow_service.find_workflow_execution_by_id.return_value = _mock_execution(
            status=WorkflowExecutionStatusEnum.ABORTED, output=""
        )
        aborted = _mock_state(output=None, status=WorkflowExecutionStatusEnum.ABORTED, task=None)

        with patch(
            "codemie.core.workflow_models.WorkflowExecutionState.get_all_by_fields",
            return_value=[aborted],
        ):
            result = materialize_workflow_conversation([_execution_ref()])

        thought = result.history[0].thoughts[0]
        assert thought.aborted is True
        assert thought.in_progress is False
        assert thought.message == ""

    def test_failed_state_without_output_is_included(self, mock_workflow_service):
        mock_workflow_service.find_workflow_execution_by_id.return_value = _mock_execution(
            status=WorkflowExecutionStatusEnum.FAILED, output=""
        )
        failed = _mock_state(output=None, status=WorkflowExecutionStatusEnum.FAILED)

        with patch(
            "codemie.core.workflow_models.WorkflowExecutionState.get_all_by_fields",
            return_value=[failed],
        ):
            result = materialize_workflow_conversation([_execution_ref()])

        thought = result.history[0].thoughts[0]
        assert thought.in_progress is False
        assert thought.message == ""
        assert thought.id == "state-1"

    def test_history_index_filters_thoughts_to_current_turn(self, mock_workflow_service):
        mock_workflow_service.find_workflow_execution_by_id.return_value = _mock_execution(
            status=WorkflowExecutionStatusEnum.IN_PROGRESS, output=""
        )
        prior = _mock_state(
            state_id="state-old",
            name="Prior Step",
            output="old output",
            status=WorkflowExecutionStatusEnum.SUCCEEDED,
            history_index=0,
        )
        current = _mock_state(
            state_id="state-new",
            name="Current Step",
            output=None,
            status=WorkflowExecutionStatusEnum.IN_PROGRESS,
            history_index=1,
        )

        with patch(
            "codemie.core.workflow_models.WorkflowExecutionState.get_all_by_fields",
            return_value=[prior, current],
        ):
            result = materialize_workflow_conversation([_execution_ref(index=1)])

        thoughts = result.history[0].thoughts
        assert len(thoughts) == 1
        assert thoughts[0].id == "state-new"
        assert thoughts[0].in_progress is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/test_history_materializer.py::TestMaterializeWorkflowConversation::test_in_progress_state_without_output_is_included tests/codemie/service/test_history_materializer.py::TestMaterializeWorkflowConversation::test_aborted_state_without_output_is_included tests/codemie/service/test_history_materializer.py::TestMaterializeWorkflowConversation::test_failed_state_without_output_is_included tests/codemie/service/test_history_materializer.py::TestMaterializeWorkflowConversation::test_history_index_filters_thoughts_to_current_turn -v`

Expected: FAIL — in-progress/failed thoughts missing (`thoughts` empty or `in_progress` False); `input_text` is `None`.

- [ ] **Step 3: Write minimal implementation**

In `_get_execution_thoughts`, include states with output **or** status in `{IN_PROGRESS, ABORTED, FAILED, INTERRUPTED}`. Derive flags from status. Set `input_text` to `state.task` (or `None` if empty). Update the docstring (it currently says “one per state that has an output”).

```python
_VISIBLE_WITHOUT_OUTPUT = {
    WorkflowExecutionStatusEnum.IN_PROGRESS,
    WorkflowExecutionStatusEnum.ABORTED,
    WorkflowExecutionStatusEnum.FAILED,
    WorkflowExecutionStatusEnum.INTERRUPTED,
}


def _get_execution_thoughts(execution_id: str, history_index: Optional[int] = None) -> List[dict]:
    """
    Retrieve thoughts for a workflow execution ordered by creation time.

    Includes completed states with output and states that are still in progress,
    aborted, failed, or interrupted even when output is empty.

    Args:
        execution_id: The workflow execution ID
        history_index: When provided, return only states tagged with this turn index.
            Falls back to all states when no states carry a history_index (legacy data).

    Returns:
        List of thought dicts, one per visible execution state
    """
    from codemie.core.workflow_models import WorkflowExecutionState

    try:
        states = WorkflowExecutionState.get_all_by_fields(
            fields={"execution_id.keyword": execution_id}, order_by="date"
        )

        if history_index is not None and any(s.history_index is not None for s in states):
            filtered = [s for s in states if s.history_index == history_index]
            if filtered:
                states = filtered

        return [
            {
                "id": state.id,
                "author_name": state.name,
                "author_type": "WorkflowState",
                "message": state.output or "",
                "input_text": state.task or None,
                "children": [],
                "in_progress": state.status == WorkflowExecutionStatusEnum.IN_PROGRESS,
                "interrupted": state.status == WorkflowExecutionStatusEnum.INTERRUPTED,
                "aborted": state.status == WorkflowExecutionStatusEnum.ABORTED,
            }
            for state in states
            if state.output or state.status in _VISIBLE_WITHOUT_OUTPUT
        ]
    except Exception as e:
        logger.error(f"Failed to get thoughts for execution {execution_id}: {e}", exc_info=True)
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/test_history_materializer.py -v`

Expected: PASS (new cases plus existing SUCCEEDED / fallback tests).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/conversation/history_materializer.py tests/codemie/service/test_history_materializer.py
git commit -m "$(cat <<'EOF'
EPMCDME-14075: Include in-progress workflow states on hydrate

Conversation GET now returns the current step as a thought with
in_progress true. Task 2.

EOF
)"
```

---

### Task 3: Expose `execution_status` on the hydrated assistant turn

**Test-first: yes — `test_materialized_message_carries_execution_status` fails because `GeneratedMessage` has no `execution_status` field.**

**Files:**
- Modify: `src/codemie/rest_api/models/conversation.py` (`GeneratedMessage`)
- Modify: `src/codemie/service/conversation/history_materializer.py` (`_materialize_execution_reference`)
- Test: `tests/codemie/service/test_history_materializer.py`

**Interfaces:**
- Consumes: `execution.overall_status: WorkflowExecutionStatusEnum`
- Produces: `GeneratedMessage.execution_status: Optional[WorkflowExecutionStatusEnum]`; JSON alias `executionStatus` via existing `ConfiguredModel` / `to_camel`

- [ ] **Step 1: Write the failing tests**

```python
    def test_materialized_message_carries_in_progress_execution_status(self, mock_workflow_service):
        mock_workflow_service.find_workflow_execution_by_id.return_value = _mock_execution(
            status=WorkflowExecutionStatusEnum.IN_PROGRESS, output=""
        )

        with patch(
            "codemie.core.workflow_models.WorkflowExecutionState.get_all_by_fields",
            return_value=[],
        ):
            result = materialize_workflow_conversation([_execution_ref()])

        msg = result.history[0]
        assert msg.execution_status == WorkflowExecutionStatusEnum.IN_PROGRESS
        dumped = msg.model_dump(by_alias=True)
        assert dumped["executionStatus"] == "In Progress"

    def test_materialized_message_carries_succeeded_execution_status(self, mock_workflow_service, mock_get_thoughts):
        mock_workflow_service.find_workflow_execution_by_id.return_value = _mock_execution(
            status=WorkflowExecutionStatusEnum.SUCCEEDED, output="done"
        )

        result = materialize_workflow_conversation([_execution_ref()])

        assert result.history[0].execution_status == WorkflowExecutionStatusEnum.SUCCEEDED
```

The between-steps case is the first test: no IN_PROGRESS thought, empty output, but status still `In Progress`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/test_history_materializer.py::TestMaterializeWorkflowConversation::test_materialized_message_carries_in_progress_execution_status tests/codemie/service/test_history_materializer.py::TestMaterializeWorkflowConversation::test_materialized_message_carries_succeeded_execution_status -v`

Expected: FAIL — `AttributeError` or assertion on missing `execution_status`.

- [ ] **Step 3: Write minimal implementation**

On `GeneratedMessage` next to `execution_id`:

```python
    execution_status: Optional[WorkflowExecutionStatusEnum] = None
```

Import `WorkflowExecutionStatusEnum` from `codemie.core.workflow_models` in `conversation.py` if not already imported.

In `_materialize_execution_reference`, pass:

```python
        execution_status=execution.overall_status,
```

into the `GeneratedMessage(...)` constructor alongside `execution_id`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/test_history_materializer.py tests/codemie/core/test_dual_queue.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/models/conversation.py src/codemie/service/conversation/history_materializer.py tests/codemie/service/test_history_materializer.py
git commit -m "$(cat <<'EOF'
EPMCDME-14075: Expose execution status on hydrated workflow turns

UI can tell a run is still alive between steps via executionStatus.
Task 3.

EOF
)"
```

---

## Frontend contract (handoff note — do not implement UI)

`GET /conversations/{id}` for a mid-run workflow turn:

- Message (`ConfiguredModel` / `to_camel`): `workflowExecutionRef`, `executionId`, `executionStatus` (`"In Progress"` / `"Succeeded"` / …).
- Nested `Thought` (plain `BaseModel`, **snake_case**): `id`, `author_name`, `author_type`, `message`, `input_text`, `in_progress`, `interrupted`, `aborted`.

Sibling UI run must consume these names; do not assume thought fields are camelCase.

## Self-review

1. **Spec coverage:** in-progress thought — Task 2; execution identity + status — Task 3; disconnect must not stop run — Task 1; aborted/completed regression — Task 2 existing+new tests; no SSE rejoin — global constraint.
2. **Placeholders:** none.
3. **Types:** `execution_status: Optional[WorkflowExecutionStatusEnum]`; thought flags from `WorkflowExecutionStatusEnum`.
