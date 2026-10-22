# A2A Server Protocol v1.0 Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reshape CodeMie's in-house A2A wire types, JSON-RPC dispatch, and `CodemieTaskManager` from the pre-v1.0 draft shape to the confirmed A2A spec v1.0 shape, and implement real server-side streaming, without touching client code or version negotiation.

**Architecture:** `types.py` is the single source of wire shape; every other file (router, `utils.py`, task manager) is a consumer that must move in lockstep with it. Work bottom-up: wire types first (Tasks 1-4), then the conversion layer (Task 5), then the task-manager contract and its existing-method plumbing (Task 6), then task-manager new behavior (Tasks 7-8), then the router (Tasks 9-11), the last consumer of everything above it. `src/codemie/rest_api/a2a/client/*` and `src/codemie/rest_api/handlers/assistant_handlers.py` import the same renamed classes but are explicitly out of scope (separate slice); Task 2 adds name aliases so those files keep importing unchanged rather than crash — see the Non-Goals-adjacent note at the end of this plan for the one place that compatibility is necessarily incomplete.

**Tech Stack:** FastAPI, Pydantic v2, SQLModel, pytest + pytest-asyncio, httpx `AsyncClient`/`ASGITransport`.

**Spec:** `docs/superpowers/tasks/2026-09-29-a2a-server-protocol/spec.md`

Commit per task using the repository's existing convention.

## Global Constraints

- Target: A2A spec v1.0, negotiated value `"1.0"`.
- Discovery path: `/.well-known/agent-card.json`. No `kind`/`type` discriminator anywhere on the wire. All renamed/new fields are camelCase.
- Discovery endpoint's existing auth/Ability split (global assistants skip auth; private/project assistants require `authenticate` + `Ability(user).can(Action.READ, assistant)` → 403) is preserved byte-for-byte.
- No Alembic migration and no compat-read shim for pre-existing `a2a_tasks` JSONB rows — the shape change is an accepted breaking change.
- Non-goals (do not implement, do not add tasks for): A2A client code, `A2A-Version` header negotiation, Agent Card `signatures`, OAuth2 device-code/PKCE, the `tenant` field, `ListTasks`/`SubscribeToTask`/push-notification-config CRUD (must resolve to `MethodNotFound`, not be wired in), token-level LLM streaming (`agent.invoke_with_a2a_output` stays a single non-streaming call — only the SSE transport is new).

## Review Focus

- A router response wrapping a `Task` (`SendMessage`/`GetTask`/`CancelTask`) must show `taskId` in the actual HTTP JSON body, not `id` — easy to fix the Pydantic model and forget `by_alias=True` at the serialization call site. Pinned in Task 10.
- Calling `CancelTask` on a task already in a terminal state (`COMPLETED`/`FAILED`/`CANCELED`) must not silently re-write it to `CANCELED`; a reasonable caller expects `TaskNotCancelableError`. Pinned in Task 7.
- If `agent.invoke_with_a2a_output` raises mid-stream, the SSE connection must still end with a terminal `FAILED` frame, not hang or drop to a bare 500. Pinned in Task 8.
- `GetTask`/`CancelTask` for an unknown task id, dispatched through the full router POST path (not just the task-manager unit), must come back as a 200 JSON-RPC body carrying `TaskNotFoundError`, not an uncaught 500. Pinned in Task 10.
- `FilePart`/`DataPart` messages must still convert cleanly through `convert_messages_to_chat_messages` once the `type` discriminator is gone — an `isinstance` chain that silently drops a non-`TextPart` message would ship a regression invisible to text-only tests. Pinned in Task 5.

---

### Task 1: Flatten `Part` — remove the wire discriminator

**Files:**
- Modify: `src/codemie/rest_api/a2a/types.py:39-72` (`TextPart`, `FileContent`, `FilePart`, `DataPart`, `Part`)
- Test: `tests/codemie/rest_api/a2a/test_types.py` (new file)

**Interfaces:**
- Produces: `TextPart(text: str, metadata: dict|None)`, `FilePart(file: FileContent, metadata)`, `DataPart(data: dict, metadata)` — none carry a `type` field. `Part = Union[TextPart, FilePart, DataPart]`, no `Field(discriminator=...)`.

Test-first: yes — a test constructing each of `TextPart`/`FilePart`/`DataPart` and asserting `"type" not in part.model_dump()`, plus a test that `Part` still validates a dict shaped like `{"text": "hi"}` without a `type` key when passed through a `TypeAdapter(Part)`.

- [ ] **Step 1: Write the failing tests**

```python
from pydantic import TypeAdapter
from codemie.rest_api.a2a.types import TextPart, FilePart, DataPart, Part, FileContent


def test_text_part_has_no_type_field():
    assert "type" not in TextPart(text="hi").model_dump()


def test_part_union_validates_without_discriminator():
    part = TypeAdapter(Part).validate_python({"text": "hi"})
    assert isinstance(part, TextPart)
```

- [ ] **Step 2: Remove `type: Literal[...]` from `TextPart`/`FilePart`/`DataPart` and drop `Field(discriminator="type")` from `Part`**

At `types.py:39-72`: delete the `type` field line from each of the three classes; change line 72 to `Part = Union[TextPart, FilePart, DataPart]` (plain `Union`, no `Annotated`/`Field`). A stray `"type"` key on an incoming dict (e.g. from `BedrockAdapter`, out of scope) is silently ignored by Pydantic's default `extra="ignore"` — no `model_config` change needed here. Run `pytest tests/codemie/rest_api/a2a/test_types.py -v` — expect PASS.

---

### Task 2: JSON-RPC method/class renames and the v1.0 `A2ARequest` union

**Files:**
- Modify: `src/codemie/rest_api/a2a/types.py:186-249`
- Test: `tests/codemie/rest_api/a2a/test_types.py`

**Interfaces:**
- Produces: `SendMessageRequest(method: Literal["SendMessage"], params: TaskSendParams)` / `SendMessageResponse(result: Task|None)`; `SendStreamingMessageRequest(method: Literal["SendStreamingMessage"], params: TaskSendParams)` / `SendStreamingMessageResponse(result: TaskStatusUpdateEvent|TaskArtifactUpdateEvent|None)`; `GetTaskRequest.method: Literal["GetTask"]` (class name unchanged); `CancelTaskRequest.method: Literal["CancelTask"]` (class name unchanged); new `GetExtendedAgentCardRequest(method: Literal["GetExtendedAgentCard"], params: TaskIdParams|None = None)` / `GetExtendedAgentCardResponse(result: "AgentCard"|None)`. `A2ARequest = TypeAdapter(Annotated[Union[SendMessageRequest, SendStreamingMessageRequest, GetTaskRequest, CancelTaskRequest, GetExtendedAgentCardRequest], Field(discriminator="method")])`. Also produces backward-compatible name bindings `SendTaskRequest = SendMessageRequest`, `SendTaskResponse = SendMessageResponse`, `SendTaskStreamingRequest = SendStreamingMessageRequest`, `SendTaskStreamingResponse = SendStreamingMessageResponse`, consumed only by the out-of-scope `a2a/client/*` and `handlers/assistant_handlers.py` (which this task does not otherwise touch).

Test-first: yes — a test that `A2ARequest.validate_python({"jsonrpc": "2.0", "method": "SendMessage", "params": {...}})` resolves to `SendMessageRequest`, and one that an unrelated method name (e.g. `"ListTasks"`) raises a `ValidationError` (proving it is not in the union).

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from pydantic import ValidationError
from codemie.rest_api.a2a.types import A2ARequest, SendMessageRequest


def test_a2a_request_resolves_send_message():
    req = A2ARequest.validate_python(
        {"jsonrpc": "2.0", "method": "SendMessage",
         "params": {"id": "t1", "message": {"role": "user", "parts": [{"text": "hi"}]}}}
    )
    assert isinstance(req, SendMessageRequest)


def test_a2a_request_rejects_list_tasks():
    with pytest.raises(ValidationError):
        A2ARequest.validate_python({"jsonrpc": "2.0", "method": "ListTasks", "params": {}})
```

- [ ] **Step 2: Rename classes and literals, add the two new classes, rebuild the union, add compat aliases**

At `types.py:186-238`: rename `SendTaskRequest`→`SendMessageRequest` (`method: Literal["SendMessage"] = "SendMessage"`), `SendTaskResponse`→`SendMessageResponse`, `SendTaskStreamingRequest`→`SendStreamingMessageRequest` (`method: Literal["SendStreamingMessage"] = "SendStreamingMessage"`), `SendTaskStreamingResponse`→`SendStreamingMessageResponse`; change `GetTaskRequest.method` to `Literal["GetTask"] = "GetTask"` and `CancelTaskRequest.method` to `Literal["CancelTask"] = "CancelTask"` (keep both class names). Add:

```python
class GetExtendedAgentCardRequest(JSONRPCRequest):
    method: Literal["GetExtendedAgentCard"] = "GetExtendedAgentCard"
    params: TaskIdParams | None = None


class GetExtendedAgentCardResponse(JSONRPCResponse):
    result: "AgentCard" | None = None
```

Place `GetExtendedAgentCardResponse` after `AgentCard`'s definition (or use the forward-ref string form shown, resolved via `model_rebuild()` at module end) since `AgentCard` is defined later in the file. Rebuild the union at `types.py:240-249` to the five classes above, `Field(discriminator="method")`. Immediately below the union, add the compatibility aliases:

```python
# Backward-compatible names for the pre-v1.0 classes, consumed by
# a2a/client/* and handlers/assistant_handlers.py (out of scope for this
# slice; the a2a-client-protocol slice removes these).
SendTaskRequest = SendMessageRequest
SendTaskResponse = SendMessageResponse
SendTaskStreamingRequest = SendStreamingMessageRequest
SendTaskStreamingResponse = SendStreamingMessageResponse
```

`GetTaskRequest`/`CancelTaskRequest` need no alias — their class names are unchanged. Run the two new tests — expect PASS. (Existing imports in `codemie_task_manager.py`, `task_manager.py`, and their tests will not resolve correctly yet — e.g. fixtures still passing `method="tasks/send"` will fail validation — until Task 6 updates them; that is expected at this point in the sequence, not a regression to chase now.)

---

### Task 3: `Task` and event envelopes — `taskId`, `contextId`

**Files:**
- Modify: `src/codemie/rest_api/a2a/types.py:102-126`
- Modify: `src/codemie/rest_api/routers/a2a.py:132` (add `by_alias=True`; the router dispatch itself is rewritten in Task 10, this is a one-line forward-fix so the alias is exercised end-to-end as soon as it exists)
- Test: `tests/codemie/rest_api/a2a/test_types.py`

**Interfaces:**
- Produces: `Task.id` unchanged as the Python/SQL attribute (required by the inherited `BaseModelWithSQLSupport.save()`/`update()`, which reference `self.id` directly and auto-generate it via `uuid4()` — renaming the attribute breaks those inherited methods and the physically-named `id` DB column), but serializes as `taskId` on the wire via alias; `Task.contextId: str | None = None` added. `TaskStatusUpdateEvent.id`/`TaskArtifactUpdateEvent.id` get the **same alias treatment** (attribute stays `id`, wire shows `taskId`) rather than a plain rename — `a2a/client/adapter.py::BedrockAdapter` (out of scope) constructs both with an `id=` keyword today, and a plain field rename would raise `TypeError: unexpected keyword argument` there even though that file is never modified by this slice.

Test-first: yes — a test that `Task(id="t1", ...).model_dump(by_alias=True)` contains `"taskId": "t1"` and not `"id"`, and that `Task.model_validate({"taskId": "t1", ...})` round-trips to `task.id == "t1"`; a matching pair of tests for `TaskStatusUpdateEvent`.

- [ ] **Step 1: Write the failing tests**

```python
from codemie.rest_api.a2a.types import Task, TaskStatus, TaskState, TaskStatusUpdateEvent


def test_task_serializes_id_as_task_id():
    task = Task(id="t1", status=TaskStatus(state=TaskState.SUBMITTED))
    dumped = task.model_dump(by_alias=True)
    assert dumped["taskId"] == "t1"
    assert "id" not in dumped


def test_task_accepts_task_id_on_input():
    task = Task.model_validate({"taskId": "t1", "status": {"state": "submitted"}})
    assert task.id == "t1"


def test_status_update_event_accepts_id_kwarg_and_serializes_task_id():
    event = TaskStatusUpdateEvent(id="t1", status=TaskStatus(state=TaskState.WORKING))
    assert event.model_dump(by_alias=True)["taskId"] == "t1"
```

- [ ] **Step 2: Add the alias fields and `contextId`**

Define a shared alias pair once near the top of `types.py` (after the imports, before `TaskState`): `_TASK_ID_ALIAS = dict(serialization_alias="taskId", validation_alias=AliasChoices("taskId", "id"))` (import `AliasChoices`, `ConfigDict` from `pydantic`). At `types.py:102-113`, override the inherited `id` field and add `contextId`:

```python
class Task(BaseModelWithSQLSupport, table=True):
    __tablename__ = "a2a_tasks"
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = SQLField(default=None, primary_key=True, **_TASK_ID_ALIAS)
    contextId: str | None = None
    sessionId: str | None = None
    status: TaskStatus = SQLField(sa_column=Column(PydanticType(TaskStatus)))
    artifacts: List[Artifact] | None = SQLField(default=None, sa_column=Column(PydanticListType(Artifact)))
    history: List[Message] | None = SQLField(default=None, sa_column=Column(PydanticListType(Message)))
```

At `types.py:115-125`, apply the same alias to the event classes (attribute name stays `id`):

```python
class TaskStatusUpdateEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str = Field(**_TASK_ID_ALIAS)
    status: TaskStatus
    final: bool = False
    metadata: dict[str, Any] | None = None


class TaskArtifactUpdateEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str = Field(**_TASK_ID_ALIAS)
    artifact: Artifact
    metadata: dict[str, Any] | None = None
```

Then at `routers/a2a.py:132`, change `JSONResponse(response.model_dump(exclude_none=True))` to `JSONResponse(response.model_dump(exclude_none=True, by_alias=True))`. Run the three new tests — expect PASS.

---

### Task 4: `AgentCard` v1.0 shape

**Files:**
- Modify: `src/codemie/rest_api/a2a/types.py:313-354`
- Test: `tests/codemie/rest_api/a2a/test_types.py`

**Interfaces:**
- Produces: new `AgentInterface(url: str, transport: str)`; `AgentCapabilities.extendedAgentCard: bool = False` added; `AgentCard` loses `url: str`, gains `supportedInterfaces: List[AgentInterface]`. Does not add `signatures` (non-goal).

Test-first: yes — a test that `AgentCard(**valid_kwargs_without_url)` validates and that `"url"` is absent from `AgentCard.model_fields` (proving the field is gone, not just optional).

- [ ] **Step 1: Write the failing test**

```python
from codemie.rest_api.a2a.types import AgentCard, AgentCapabilities, AgentInterface, AgentSkill


def test_agent_card_has_no_url_field():
    card = AgentCard(
        name="a", version="1.0.0",
        capabilities=AgentCapabilities(extendedAgentCard=True),
        supportedInterfaces=[AgentInterface(url="http://x", transport="JSONRPC")],
        skills=[AgentSkill(id="s", name="s")],
    )
    assert "url" not in card.model_fields
```

- [ ] **Step 2: Add `AgentInterface`, `extendedAgentCard`, `supportedInterfaces`; remove `url`**

At `types.py:318-322`, add `extendedAgentCard: bool = False` to `AgentCapabilities`. Before `AgentCard` (around line 339), add:

```python
class AgentInterface(BaseModel):
    url: str
    transport: str
```

At `types.py:339-354`, delete the `url: str` line and add `supportedInterfaces: List[AgentInterface]` in its place. Run the new test — expect PASS.

---

### Task 5: Conversion layer — `a2a/utils.py`

**Files:**
- Modify: `src/codemie/rest_api/a2a/utils.py:28-40` (imports), `:44-70` (`convert_to_task_request`), `:92-124` (`convert_messages_to_chat_messages`), `:215-258` (`assistant_to_agent_card`)
- Modify: `tests/codemie/rest_api/a2a/test_utils.py` (update `test_assistant_to_agent_card` for the new shape)

**Interfaces:**
- Consumes: `TextPart`/`FilePart`/`DataPart` (Task 1), `SendMessageRequest`/`SendStreamingMessageRequest` (Task 2), `AgentCard`/`AgentInterface`/`AgentCapabilities.extendedAgentCard` (Task 4).
- Produces: `assistant_to_agent_card(assistant, request) -> AgentCard` with `supportedInterfaces=[AgentInterface(url=<same URL built today>, transport="JSONRPC")]`, `capabilities.streaming=True`, `capabilities.extendedAgentCard=True`; `convert_messages_to_chat_messages`/`convert_to_task_request` unchanged signatures, both now referencing the canonical Task-2 class names directly (not the compat aliases — this file is in scope).

Test-first: yes — a test that a `Message` containing a `FilePart` converts to a `ChatMessage` whose text includes the file placeholder (proving the `isinstance` branch for `FilePart` still fires post-flattening — the Review Focus item), and an updated `test_assistant_to_agent_card` asserting `card.supportedInterfaces[0].url` matches today's URL, `"url" not in card.model_fields`, and both capability flags are `True`.

- [ ] **Step 1: Write/update the failing tests**

```python
from codemie.rest_api.a2a.types import Message, FilePart, FileContent
from codemie.rest_api.a2a.utils import convert_messages_to_chat_messages


def test_convert_messages_handles_file_part_without_type_field():
    msg = Message(role="user", parts=[FilePart(file=FileContent(uri="http://f", name="a.txt"))])
    chat_messages = convert_messages_to_chat_messages([msg])
    assert "a.txt" in chat_messages[0].message
```

(Update the existing `test_assistant_to_agent_card` in `test_utils.py` to assert on `supportedInterfaces`/`capabilities` instead of `url`, per the Interfaces block above. `convert_to_task_request`'s existing `isinstance(result, SendTaskRequest)`/`SendTaskStreamingRequest` assertions need no change — those names still resolve via the Task 2 aliases to the same classes this file now imports canonically.)

- [ ] **Step 2: Switch to `isinstance`, rebuild `assistant_to_agent_card`, use canonical class names**

At `utils.py:28-40`, change the imports of `SendTaskRequest`/`SendTaskStreamingRequest` to `SendMessageRequest`/`SendStreamingMessageRequest`, and add `FilePart`/`DataPart` alongside the existing `TextPart` import. At `utils.py:44-70` (`convert_to_task_request`), replace the two class references with the canonical names (return-type union and the `request_cls = SendStreamingMessageRequest if ... else SendMessageRequest` line). At `utils.py:110-116`, replace `if part.type == "text": ... elif part.type == "file": ... elif part.type == "data": ...` with `isinstance(part, TextPart)` / `isinstance(part, FilePart)` / `isinstance(part, DataPart)` (same branch bodies, condition only). At `utils.py:249-257`, replace `url=f"{...}"` with `supportedInterfaces=[AgentInterface(url=f"{request.base_url.scheme}://{request.base_url.netloc}{config.API_ROOT_PATH}/v1/a2a/assistants/{assistant.id}", transport="JSONRPC")]`, and change `capabilities=AgentCapabilities(streaming=False, ...)` to `capabilities=AgentCapabilities(streaming=True, pushNotifications=False, stateTransitionHistory=True, extendedAgentCard=True)`; import `AgentInterface` at the top. Run both tests — expect PASS.

---

### Task 6: Rename plumbing — ABC, `CodemieTaskManager`'s existing methods, existing tests

Purely mechanical: carries no new behavior, only makes the Task-2 renames compile everywhere in scope. Test-first: no — single step, verified by running the existing (updated) test files rather than by new tests.

**Files:**
- Modify: `src/codemie/rest_api/a2a/server/task_manager.py`
- Modify: `src/codemie/rest_api/a2a/server/codemie_task_manager.py:1-83` (imports; `on_get_task`/`on_send_task`/`on_send_task_subscribe` signatures — method *names* unchanged except the subscribe one)
- Modify: `tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py:24-103` (imports and fixtures)

**Interfaces:**
- Produces: `TaskManager.on_get_task`/`on_send_task` unchanged in name, retyped to `SendMessageRequest`/`SendMessageResponse`; new abstract `on_cancel_task(request: CancelTaskRequest) -> CancelTaskResponse` and `on_get_extended_agent_card(request: GetExtendedAgentCardRequest, http_request) -> GetExtendedAgentCardResponse`; `on_send_task_subscribe` renamed to `on_send_streaming_message(request: SendStreamingMessageRequest) -> AsyncIterable[SendStreamingMessageResponse] | JSONRPCResponse`. `CodemieTaskManager` mirrors all of the above.

- [ ] **Step 1: Rename types and the one method everywhere in scope**

In `task_manager.py`: replace the `SendTaskResponse, SendTaskRequest, SendTaskStreamingResponse, SendTaskStreamingRequest` import with `SendMessageResponse, SendMessageRequest, SendStreamingMessageResponse, SendStreamingMessageRequest, CancelTaskRequest, CancelTaskResponse, GetExtendedAgentCardRequest, GetExtendedAgentCardResponse`; retype `on_get_task`/`on_send_task`'s signatures to the new names (keep method names); rename the `on_send_task_subscribe` abstract method to `on_send_streaming_message` with the renamed request/response types; add `on_cancel_task` and `on_get_extended_agent_card` abstract methods (`pass` bodies), mirroring the existing `@abstractmethod` style.

In `codemie_task_manager.py:28-45`, make the same import swap. Retype `on_send_task`, `_invoke_agent`, `_validate_request`, `_setup_agent`, `_process_agent_response`, `_process_error_response` (every place they currently annotate `SendTaskRequest`/`SendTaskResponse`/`SendTaskStreamingRequest`) to `SendMessageRequest`/`SendMessageResponse`/`SendStreamingMessageRequest`. At line 80-83, rename `on_send_task_subscribe`→`on_send_streaming_message` (body is filled in by Task 8; leave it raising `new_not_implemented_error(request.id)` for now so this task stays behavior-neutral).

In `test_codemie_task_manager.py:24-45` (imports), swap in the same new names. In the fixtures (`send_task_request`, `get_task_request`, `send_task_streaming_request`, lines 91-103): change `SendTaskRequest`→`SendMessageRequest` and its `method="tasks/send"`→`method="SendMessage"`; `GetTaskRequest`'s `method="tasks/get"`→`method="GetTask"`; `SendTaskStreamingRequest`→`SendStreamingMessageRequest` and its `method="tasks/sendSubscribe"`→`method="SendStreamingMessage"`. Run `pytest tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py -v` — every test except `test_on_send_task_subscribe` should PASS (that one still exercises the not-yet-renamed method name and is rewritten in Task 8; skip or accept its failure here).

---

### Task 7: `CodemieTaskManager` — real parts, cancel, extended card

**Files:**
- Modify: `src/codemie/rest_api/a2a/server/codemie_task_manager.py:208-210` (`_create_response_parts`)
- Modify: `src/codemie/rest_api/a2a/server/codemie_task_manager.py` (add `on_cancel_task`, `on_get_extended_agent_card`)
- Modify: `tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py` (dict-part assertions → `TextPart`)

**Interfaces:**
- Consumes: `TextPart` (Task 1), `CancelTaskRequest`/`CancelTaskResponse`, `GetExtendedAgentCardRequest`/`GetExtendedAgentCardResponse` (Task 2), `TaskNotCancelableError` (already defined in `types.py:289-292`), `assistant_to_agent_card` (Task 5), the renamed ABC (Task 6).
- Produces: `_create_response_parts(content: str) -> list[TextPart]`; `on_cancel_task(request) -> CancelTaskResponse`; `on_get_extended_agent_card(request, http_request) -> GetExtendedAgentCardResponse`.

Test-first: yes — four tests: (1) `_create_response_parts` returns `[TextPart(text=content)]` instead of a raw dict; (2) `on_cancel_task` on a `WORKING` task transitions it to `CANCELED` and persists via `update_store`; (3) `on_cancel_task` on a task already `COMPLETED` returns `TaskNotCancelableError` and does not call `update_store` (the Review Focus item); (4) `on_cancel_task` for an unknown task id returns `TaskNotFoundError` (the AC's explicit not-found requirement).

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from unittest.mock import patch
from codemie.rest_api.a2a.types import (
    TextPart, CancelTaskRequest, TaskIdParams, Task, TaskStatus, TaskState,
    TaskNotCancelableError, TaskNotFoundError,
)


def test_create_response_parts_returns_text_part(task_manager):
    parts = task_manager._create_response_parts("hi")
    assert parts == [TextPart(text="hi")]


@pytest.mark.asyncio
async def test_on_cancel_task_transitions_working_to_canceled(task_manager, mock_task):
    mock_task.status = TaskStatus(state=TaskState.WORKING)
    request = CancelTaskRequest(id="r1", params=TaskIdParams(id="test-task-id"))
    with patch.object(Task, "find_by_id", return_value=mock_task):
        with patch.object(task_manager, "update_store", return_value=mock_task) as mock_update:
            response = await task_manager.on_cancel_task(request)
    mock_update.assert_called_once()
    assert mock_update.call_args.kwargs["status"].state == TaskState.CANCELED
    assert response.result == mock_task


@pytest.mark.asyncio
async def test_on_cancel_task_rejects_terminal_task(task_manager, mock_task):
    mock_task.status = TaskStatus(state=TaskState.COMPLETED)
    request = CancelTaskRequest(id="r1", params=TaskIdParams(id="test-task-id"))
    with patch.object(Task, "find_by_id", return_value=mock_task):
        with patch.object(task_manager, "update_store") as mock_update:
            response = await task_manager.on_cancel_task(request)
    mock_update.assert_not_called()
    assert isinstance(response.error, TaskNotCancelableError)


@pytest.mark.asyncio
async def test_on_cancel_task_unknown_id_returns_not_found(task_manager):
    request = CancelTaskRequest(id="r1", params=TaskIdParams(id="missing"))
    with patch.object(Task, "find_by_id", return_value=None):
        response = await task_manager.on_cancel_task(request)
    assert isinstance(response.error, TaskNotFoundError)
```

- [ ] **Step 2: Implement**

At `codemie_task_manager.py:208-210`, change `_create_response_parts` to `return [TextPart(text=content)]`. Add `on_cancel_task`: load `Task.find_by_id(request.params.id)`; if not found, `return CancelTaskResponse(id=request.id, error=TaskNotFoundError())` (mirrors `on_get_task`'s pattern); if `task.status.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED)`, `return CancelTaskResponse(id=request.id, error=TaskNotCancelableError())`; otherwise call `self.update_store(task_id=request.params.id, status=TaskStatus(state=TaskState.CANCELED), artifacts=[])` and `return CancelTaskResponse(id=request.id, result=updated_task)`. Add `on_get_extended_agent_card(self, request, http_request)`, calling `assistant_to_agent_card(self.assistant, http_request)` (same helper the discovery route uses) and wrapping it in `GetExtendedAgentCardResponse(id=request.id, result=card)` — this method needs the FastAPI `Request` object, so its Task-10 router call site must pass it through as `http_request`. In the test file, update every fixture/assertion still expecting `{"type": "text", ...}` dicts from `_create_response_parts` (`test_create_response_parts`, `test_process_agent_response_input_required`, `test_process_agent_response_completed`, `test_process_error_response`) to expect `[TextPart(text=...)]` instead. Run the updated test file — expect PASS.

---

### Task 8: Real server-side streaming

**Files:**
- Modify: `src/codemie/rest_api/a2a/server/codemie_task_manager.py:80-83` (`on_send_streaming_message`, renamed by Task 6)
- Modify: `src/codemie/rest_api/a2a/server/codemie_task_manager.py:167-206` (`_handle_a2a_stream`, `_create_streaming_response`)
- Modify: `tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py` (replace the self-mocking `test_on_send_task_subscribe`)

**Interfaces:**
- Consumes: `SendStreamingMessageRequest`/`SendStreamingMessageResponse`, `TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent` (Tasks 2-3).
- Produces: `on_send_streaming_message(request) -> AsyncIterable[SendStreamingMessageResponse]` — a real async generator, no longer raising `UnsupportedOperationError`.

Test-first: yes — replace the self-mocking test with one that calls the *real* `on_send_streaming_message`, mocking only `Task.find_by_id`/`update_store`/agent invocation, and asserts the yielded sequence is `[working event, terminal event]`; a second test forces `agent.invoke_with_a2a_output` to raise and asserts the generator still yields exactly one terminal `TaskStatusUpdateEvent` with `state=FAILED` (the Review Focus item — no hang, no unhandled exception escaping the generator).

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from unittest.mock import patch, MagicMock
from codemie.rest_api.a2a.types import TaskState, TaskStatusUpdateEvent, Task, TaskStatus
from codemie.service.assistant_service import AssistantService


@pytest.mark.asyncio
async def test_on_send_streaming_message_yields_working_then_completed(
    task_manager, send_task_streaming_request, mock_task
):
    mock_task.status = TaskStatus(state=TaskState.SUBMITTED)
    agent_mock = MagicMock()
    agent_mock.invoke_with_a2a_output.return_value = {"content": "done"}
    with patch.object(Task, "find_by_id", return_value=mock_task), \
         patch.object(task_manager, "update_store", return_value=mock_task), \
         patch.object(AssistantService, "build_agent", return_value=agent_mock):
        events = [e async for e in task_manager.on_send_streaming_message(send_task_streaming_request)]
    assert isinstance(events[0].result, TaskStatusUpdateEvent)
    assert events[0].result.status.state == TaskState.WORKING
    assert events[-1].result.status.state == TaskState.COMPLETED


@pytest.mark.asyncio
async def test_on_send_streaming_message_yields_failed_on_agent_error(
    task_manager, send_task_streaming_request, mock_task
):
    agent_mock = MagicMock()
    agent_mock.invoke_with_a2a_output.side_effect = Exception("boom")
    with patch.object(Task, "find_by_id", return_value=mock_task), \
         patch.object(task_manager, "update_store", return_value=mock_task), \
         patch.object(AssistantService, "build_agent", return_value=agent_mock):
        events = [e async for e in task_manager.on_send_streaming_message(send_task_streaming_request)]
    assert events[-1].result.status.state == TaskState.FAILED
```

- [ ] **Step 2: Implement**

Replace the `raise new_not_implemented_error(request.id)` body of `on_send_streaming_message` with: load `task = Task.find_by_id(request.params.id) or self.upsert_task(request.params)`, then `async for event in self._handle_a2a_stream(request, task): yield event`. Fix `_create_streaming_response` (`codemie_task_manager.py:203-206`) to build `TaskStatusUpdateEvent(id=task.id, status=task.status, final=(state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED, TaskState.INPUT_REQUIRED)))` (note: `id=`, matching the Task-3 alias — not `taskId=`) instead of the current `SendTaskStreamingResponse(id=request_id, result=task_result)` built from a full `Task` — the pre-existing latent type mismatch this slice is required to fix. Rename remaining `SendTaskStreamingResponse`/`SendTaskStreamingRequest` references in this file to `SendStreamingMessageResponse`/`SendStreamingMessageRequest`. In the test file, delete the old `test_on_send_task_subscribe` (it mocked the method under test and verified nothing) and add the two tests above. Run both new tests — expect PASS.

---

### Task 9: Discovery route path and Swagger example

**Files:**
- Modify: `src/codemie/rest_api/routers/a2a.py:51-56`
- Modify: `src/codemie/rest_api/a2a/types.py:380-397` (`A2ARequestBody`)
- Modify: `tests/codemie/rest_api/routers/test_a2a_router.py` (path string in all 5 existing discovery tests)

**Interfaces:**
- Produces: `GET /v1/a2a/assistants/{assistant_id}/.well-known/agent-card.json` (was `agent.json`); `A2ARequestBody.method` example is `"SendMessage"` with a `params` example shaped for `TaskSendParams`.

Test-first: yes — update the 5 existing discovery tests' request path to `.well-known/agent-card.json`, and add one asserting the response body has no `"url"` key and does have `"supportedInterfaces"` and `capabilities.extendedAgentCard is True` (AC4 field-shape check the analysis found missing today).

- [ ] **Step 1: Update existing tests, add the field-shape test**

```python
@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
async def test_get_agent_card_shape_matches_v1(mock_find_by_id, global_assistant):
    mock_find_by_id.return_value = global_assistant
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        resp = await ac.get("/v1/a2a/assistants/global-id-123/.well-known/agent-card.json")
    data = resp.json()
    assert "url" not in data
    assert data["supportedInterfaces"][0]["transport"] == "JSONRPC"
    assert data["capabilities"]["extendedAgentCard"] is True
```

(Update the URL string `/.well-known/agent.json` → `/.well-known/agent-card.json` in each of the 5 pre-existing tests in this file.)

- [ ] **Step 2: Implement**

At `routers/a2a.py:51-52`, change the route path to `"/assistants/{assistant_id}/.well-known/agent-card.json"`. At `types.py:383-397`, change `method` default/description to `"SendMessage"` and rebuild the `params` example dict to `{"id": "task-123", "message": {"role": "user", "parts": [{"text": "Hello, can you help me with this question?"}]}, "historyLength": 10}` (drop the `type` key from the example part per Task 1). Run all 6 discovery tests — expect PASS.

---

### Task 10: Router dispatch table — `SendMessage`/`GetTask`/`CancelTask`/`GetExtendedAgentCard`

**Files:**
- Modify: `src/codemie/rest_api/routers/a2a.py:27-38` (imports), `:115-135` (dispatch)
- Test: `tests/codemie/rest_api/routers/test_a2a_router.py`

**Interfaces:**
- Consumes: `CodemieTaskManager.on_send_task`/`on_get_task`/`on_cancel_task`/`on_get_extended_agent_card` (Tasks 6-7), `A2ARequest` (Task 2).
- Produces: `POST /v1/a2a/assistants/{id}` dispatches by `json_rpc_request.method` through a `{"SendMessage": ..., "GetTask": ..., "CancelTask": ..., "GetExtendedAgentCard": ...}` table (`SendStreamingMessage` handled separately in Task 11); any other method returns a 200 JSON-RPC body with `MethodNotFoundError`, never a 500.

Test-first: yes — router-level tests (httpx `AsyncClient`, `authenticate`/`Ability`/`Assistant.find_by_id`/agent invocation patched, matching this file's existing pattern) for: `SendMessage` success (asserts `taskId` in the JSON body, not `id` — the by_alias Review Focus item), `GetTask` for an unknown id (asserts 200 body with `TaskNotFoundError`, not 500 — the other Review Focus item), `CancelTask` success, `GetExtendedAgentCard` success, and one non-goal method (`"ListTasks"`) asserting `MethodNotFound`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
@patch("codemie.rest_api.routers.a2a.authenticate")
@patch("codemie.rest_api.routers.a2a.Ability")
async def test_send_message_response_uses_task_id_alias(mock_ability, mock_authenticate, mock_find_by_id, private_assistant, mock_user):
    mock_find_by_id.return_value = private_assistant
    mock_authenticate.return_value = mock_user
    mock_ability.return_value.can.return_value = True
    body = {"jsonrpc": "2.0", "id": "r1", "method": "SendMessage",
            "params": {"id": "task-1", "message": {"role": "user", "parts": [{"text": "hi"}]}}}
    with patch("codemie.rest_api.a2a.server.codemie_task_manager.AssistantService.build_agent") as mock_build:
        mock_build.return_value.invoke_with_a2a_output.return_value = {"content": "ok"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            resp = await ac.post("/v1/a2a/assistants/private-id-123", json=body)
    data = resp.json()
    assert "taskId" in data["result"]
    assert "id" not in data["result"]


@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
@patch("codemie.rest_api.routers.a2a.authenticate")
@patch("codemie.rest_api.routers.a2a.Ability")
async def test_get_task_unknown_id_returns_200_with_task_not_found(mock_ability, mock_authenticate, mock_find_by_id, private_assistant, mock_user):
    mock_find_by_id.return_value = private_assistant
    mock_authenticate.return_value = mock_user
    mock_ability.return_value.can.return_value = True
    body = {"jsonrpc": "2.0", "id": "r1", "method": "GetTask", "params": {"id": "missing"}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        resp = await ac.post("/v1/a2a/assistants/private-id-123", json=body)
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32001


@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
@patch("codemie.rest_api.routers.a2a.authenticate")
@patch("codemie.rest_api.routers.a2a.Ability")
async def test_cancel_task_success(mock_ability, mock_authenticate, mock_find_by_id, private_assistant, mock_user):
    mock_find_by_id.return_value = private_assistant
    mock_authenticate.return_value = mock_user
    mock_ability.return_value.can.return_value = True
    body = {"jsonrpc": "2.0", "id": "r1", "method": "CancelTask", "params": {"id": "missing"}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        resp = await ac.post("/v1/a2a/assistants/private-id-123", json=body)
    # unknown id -> not-found error, proving CancelTask is wired through the dispatch table at all
    assert resp.json()["error"]["code"] == -32001


@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
@patch("codemie.rest_api.routers.a2a.authenticate")
@patch("codemie.rest_api.routers.a2a.Ability")
async def test_get_extended_agent_card_returns_card(mock_ability, mock_authenticate, mock_find_by_id, private_assistant, mock_user):
    mock_find_by_id.return_value = private_assistant
    mock_authenticate.return_value = mock_user
    mock_ability.return_value.can.return_value = True
    body = {"jsonrpc": "2.0", "id": "r1", "method": "GetExtendedAgentCard", "params": {}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        resp = await ac.post("/v1/a2a/assistants/private-id-123", json=body)
    assert resp.json()["result"]["name"] == "Private Assistant"


@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
@patch("codemie.rest_api.routers.a2a.authenticate")
@patch("codemie.rest_api.routers.a2a.Ability")
async def test_list_tasks_returns_method_not_found(mock_ability, mock_authenticate, mock_find_by_id, private_assistant, mock_user):
    mock_find_by_id.return_value = private_assistant
    mock_authenticate.return_value = mock_user
    mock_ability.return_value.can.return_value = True
    body = {"jsonrpc": "2.0", "id": "r1", "method": "ListTasks", "params": {}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        resp = await ac.post("/v1/a2a/assistants/private-id-123", json=body)
    assert resp.json()["error"]["code"] == -32601
```

- [ ] **Step 2: Implement**

Update the imports at `routers/a2a.py:27-38` to the Task 2/6 names (`SendMessageRequest`, `SendStreamingMessageRequest`, `CancelTaskRequest`, `GetExtendedAgentCardRequest`, `MethodNotFoundError`). Replace the `isinstance` chain at `:122-131` with:

```python
DISPATCH = {
    "SendMessage": lambda tm, req: tm.on_send_task(req),
    "GetTask": lambda tm, req: tm.on_get_task(req),
    "CancelTask": lambda tm, req: tm.on_cancel_task(req),
    "GetExtendedAgentCard": lambda tm, req: tm.on_get_extended_agent_card(req, request),
}
handler = DISPATCH.get(json_rpc_request.method)
if handler is None:
    response = JSONRPCResponse(id=json_rpc_request.id, error=MethodNotFoundError())
else:
    response = await handler(task_manager, json_rpc_request)
return JSONResponse(response.model_dump(exclude_none=True, by_alias=True))
```

`SendStreamingMessage` is intentionally absent from `DISPATCH` here — Task 11 adds its branch before this fallback so it still takes the streaming path rather than falling through to `MethodNotFoundError`. Run all new and existing tests in this file — expect PASS.

---

### Task 11: `SendStreamingMessage` — SSE `StreamingResponse`

**Files:**
- Modify: `src/codemie/rest_api/routers/a2a.py:15-20` (imports), `:90-136` (`execute_a2a_request`)
- Test: `tests/codemie/rest_api/routers/test_a2a_router.py`

**Interfaces:**
- Consumes: `CodemieTaskManager.on_send_streaming_message` (Task 8).
- Produces: for `method == "SendStreamingMessage"`, the endpoint returns `StreamingResponse(media_type="text/event-stream")` that iterates the task manager's async generator and writes one `data: <json>\n\n` frame per `SendStreamingMessageResponse`, JSON-encoded with `by_alias=True`.

Test-first: yes — a router test that POSTs a `SendStreamingMessage` body, asserts `resp.headers["content-type"].startswith("text/event-stream")`, and parses `resp.text` into `data:`-prefixed frames, asserting the last frame's decoded JSON has a terminal task state.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
@patch("codemie.rest_api.routers.a2a.authenticate")
@patch("codemie.rest_api.routers.a2a.Ability")
async def test_send_streaming_message_returns_sse_frames(mock_ability, mock_authenticate, mock_find_by_id, private_assistant, mock_user):
    mock_find_by_id.return_value = private_assistant
    mock_authenticate.return_value = mock_user
    mock_ability.return_value.can.return_value = True
    body = {"jsonrpc": "2.0", "id": "r1", "method": "SendStreamingMessage",
            "params": {"id": "task-2", "message": {"role": "user", "parts": [{"text": "hi"}]}}}
    with patch("codemie.rest_api.a2a.server.codemie_task_manager.AssistantService.build_agent") as mock_build:
        mock_build.return_value.invoke_with_a2a_output.return_value = {"content": "ok"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            resp = await ac.post("/v1/a2a/assistants/private-id-123", json=body)
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = [json.loads(line[len("data: "):]) for line in resp.text.splitlines() if line.startswith("data: ")]
    assert frames[-1]["result"]["status"]["state"] in ("completed", "input-required", "failed")
```

- [ ] **Step 2: Implement**

Add `from fastapi.responses import StreamingResponse` at `routers/a2a.py:19`. In `execute_a2a_request`, before the `DISPATCH.get(...)` lookup added in Task 10, branch: `if isinstance(json_rpc_request, SendStreamingMessageRequest): return StreamingResponse(_stream_events(task_manager, json_rpc_request), media_type="text/event-stream")`. Add a module-level helper:

```python
async def _stream_events(task_manager, request):
    async for event in task_manager.on_send_streaming_message(request):
        yield f"data: {event.model_dump_json(exclude_none=True, by_alias=True)}\n\n"
```

Run the new test — expect PASS. Run the full `tests/codemie/rest_api/routers/test_a2a_router.py`, `tests/codemie/rest_api/a2a/test_utils.py`, `tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py`, and `tests/codemie/rest_api/a2a/test_types.py` — expect all PASS, confirming the full slice.

---

## Known, accepted collateral outside this slice's tests

`a2a/client/adapter.py::BedrockAdapter.make_streaming_request` explicitly constructs `SendTaskStreamingRequest(method="message/stream", ...)` (line 88). The Task-2 alias keeps the class name importable, but the `method` field's `Literal` now only accepts `"SendStreamingMessage"`, so that one call would raise a `ValidationError` if it ran. No test in the repository exercises this method today (confirmed by search), so this task does not newly break the suite — but it is a real latent break in out-of-scope client code, left for the `a2a-client-protocol` slice per the guide's documented slice ordering, not silently missed.
