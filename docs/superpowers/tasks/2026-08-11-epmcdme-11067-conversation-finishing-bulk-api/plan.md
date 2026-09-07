# Platform-enforced Conversation Finishing with Bulk API Support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a platform-enforced "finished" state to `Conversation`, with user/admin/bulk finish APIs, an admin cross-user list endpoint, and HTTP 409 enforcement on message-content mutation of finished conversations.

**Architecture:** Two new nullable columns (`is_finished`, `finished_at`) on the existing `Conversation` SQLModel table plus a partial index for scheduler queries; a single `assert_mutable()` guard method on `Conversation` called explicitly at 5 existing content-mutation call sites; three new endpoints (`POST /conversations/{id}/finish`, `POST /admin/conversations/{id}/finish`, `POST /admin/conversations/finish-bulk`) plus one new list endpoint (`GET /admin/conversations`).

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy, Alembic, Pydantic, pytest.

## Global Constraints

- Enforcement scope is **message-content only** (chat-send, history upsert, AI-message edit) — never pin/rename/folder/share/feedback/delete. (spec.md "Enforcement" section)
- No server-side auto-finish timer — finish only happens via explicit API call. (spec.md "Scope")
- `already_finished: true` still counts as a *successful* bulk result, not an error. (spec.md "APIs")
- No locking around the finish/append race — accepted per the ticket's own ADR note. (spec.md "Enforcement")
- 409 `details` text must not imply the user chose to finish the conversation (it can reach real `codemie-ui` users via chat-send). Use exactly: `"This conversation was closed and can no longer receive new messages (id={self.id})."` (spec.md "Enforcement")

---

## Task 1: Add `is_finished`/`finished_at` columns and partial index

**Files:**
- Create: `src/external/alembic/versions/<new_revision>_add_is_finished_to_conversations.py`
- Modify: `src/codemie/rest_api/models/conversation.py:225-273` (the `Conversation` class field block)
- Test: `tests/codemie/rest_api/models/test_conversation_model.py`

**Interfaces:**
- Produces: `Conversation.is_finished: Optional[bool]` (default `False`), `Conversation.finished_at: Optional[datetime]` (default `None`) — every later task reads/writes these two attributes directly on a loaded `Conversation` instance.

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/rest_api/models/test_conversation_model.py` (follow the existing file's conventions for constructing a bare `Conversation()` — check the top of the file for the exact pattern used by neighboring tests, e.g. `Conversation(id=..., conversation_id=..., user_id=..., history=[])`):

```python
def test_conversation_is_finished_defaults_false():
    conversation = Conversation(
        id="conv-1",
        conversation_id="conv-1",
        user_id="user-1",
        history=[],
    )
    assert conversation.is_finished is False
    assert conversation.finished_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/models/test_conversation_model.py::test_conversation_is_finished_defaults_false -v`
Expected: FAIL with `AttributeError: 'Conversation' object has no attribute 'is_finished'`

- [ ] **Step 3: Add the fields to the model**

In `src/codemie/rest_api/models/conversation.py`, inside the `Conversation` class (after the `is_workflow_conversation` field, before the `# Legacy` comment at line 261):

```python
    is_finished: Optional[bool] = SQLField(
        default=False,
        sa_column=Column(Boolean),
        description="True once the conversation has been finished and can no longer be mutated",
    )
    finished_at: Optional[datetime] = SQLField(
        default=None,
        description="Timestamp the conversation was finished, or None if still active",
    )
```

Note: `finished_at` deliberately has **no** `sa_column=Column(DateTime(timezone=True))` override — `date`/`update_date` on the same table use plain SQLModel-inferred `DateTime` (naive, no timezone) columns, and `finished_at` should match that convention rather than introduce a tz-aware column type on the same table.

`Boolean` and `Column` are already imported at the top of this file (used by `mcp_server_single_usage`/`is_workflow_conversation`); no new imports needed for `is_finished`. Confirm `datetime` is imported (it already is, used throughout the file).

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/rest_api/models/test_conversation_model.py::test_conversation_is_finished_defaults_false -v`
Expected: PASS

- [ ] **Step 5: Write the Alembic migration**

Find the current head revision:

Run: `poetry run alembic -c src/external/alembic/alembic.ini heads` (or check `.ai-run/guides/data/database-patterns.md` for the exact invocation this repo uses if it differs)

Create `src/external/alembic/versions/<new_revision>_add_is_finished_to_conversations.py`, following the exact structure of `src/external/alembic/versions/e8f3a9b5c2d1_add_is_workflow_conversation_to_conversations.py` (add-column-plus-index precedent):

```python
"""Add is_finished/finished_at to conversations

Revision ID: <new_revision>
Revises: <current_head>
Create Date: 2026-08-11 00:00:00.000000

Adds is_finished (default False) and finished_at to conversations, plus a
partial index on (date) WHERE is_finished = false for efficient scheduler
queries finding unfinished conversations (EPMCDME-11067). See spec.md
"Data model" for why the index predicate is safe for interactive chat-write
performance — neither date nor is_finished changes on a per-message write.
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "<new_revision>"
down_revision: Union[str, None] = "<current_head>"
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("is_finished", sa.Boolean(), nullable=True, default=False))
    op.add_column("conversations", sa.Column("finished_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_conversations_unfinished",
        "conversations",
        ["date"],
        postgresql_where=sa.text("is_finished = false"),
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_unfinished", table_name="conversations")
    op.drop_column("conversations", "finished_at")
    op.drop_column("conversations", "is_finished")
```

Replace `<new_revision>` with a freshly generated Alembic revision id and `<current_head>` with the actual current head from the command above.

- [ ] **Step 6: Verify the migration round-trips**

Run against a local/test Postgres per `.ai-run/guides/development/local-testing.md`:
`poetry run alembic -c src/external/alembic/alembic.ini upgrade head` then `poetry run alembic -c src/external/alembic/alembic.ini downgrade -1` then `upgrade head` again.
Expected: all three commands succeed with no errors; `\d conversations` in `psql` shows `is_finished`, `finished_at` columns and `ix_conversations_unfinished` index after the final upgrade.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/rest_api/models/conversation.py src/external/alembic/versions/<new_revision>_add_is_finished_to_conversations.py tests/codemie/rest_api/models/test_conversation_model.py
git commit -m "EPMCDME-11067: add is_finished/finished_at columns to Conversation"
```

---

## Task 2: `Conversation.assert_mutable()` guard

**Files:**
- Modify: `src/codemie/rest_api/models/conversation.py` (add method to `Conversation` class, near `is_owned_by`/`is_managed_by` around line 627)
- Test: `tests/codemie/rest_api/models/test_conversation_model.py`

**Interfaces:**
- Consumes: `self.is_finished`, `self.id` (from Task 1)
- Produces: `Conversation.assert_mutable(self) -> None`, raising `ExtendedHTTPException(code=409, ...)` — Tasks 4 and 5 call this at the top of each guarded method.

- [ ] **Step 1: Write the failing test**

```python
def test_assert_mutable_raises_409_when_finished():
    conversation = Conversation(
        id="conv-1",
        conversation_id="conv-1",
        user_id="user-1",
        history=[],
        is_finished=True,
    )
    with pytest.raises(ExtendedHTTPException) as exc_info:
        conversation.assert_mutable()
    assert exc_info.value.code == 409


def test_assert_mutable_noop_when_not_finished():
    conversation = Conversation(
        id="conv-1",
        conversation_id="conv-1",
        user_id="user-1",
        history=[],
        is_finished=False,
    )
    conversation.assert_mutable()  # must not raise
```

Add `from codemie.core.exceptions import ExtendedHTTPException` and `import pytest` to the test file's imports if not already present (check the file header first).

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/models/test_conversation_model.py -k assert_mutable -v`
Expected: FAIL with `AttributeError: 'Conversation' object has no attribute 'assert_mutable'`

- [ ] **Step 3: Implement the guard**

In `src/codemie/rest_api/models/conversation.py`, add near `is_owned_by`/`is_managed_by`/`is_shared_with` (after line 634's `is_shared_with`):

```python
    def assert_mutable(self) -> None:
        """Raise 409 if this conversation has been finished. Called explicitly at
        every content-mutation entry point — not automatic, see spec.md "Enforcement"."""
        if self.is_finished:
            raise ExtendedHTTPException(
                code=status.HTTP_409_CONFLICT,
                message="Conversation is finished",
                details=f"This conversation was closed and can no longer receive new "
                        f"messages (id={self.id}).",
                help="Start a new conversation to continue.",
            )
```

This needs two imports at the top of `conversation.py` if not already present: `from fastapi import status` and `from codemie.core.exceptions import ExtendedHTTPException`. Check the existing import block first — `ExtendedHTTPException` may already be imported elsewhere in this file (it's used in 779 places across the codebase); `fastapi.status` likely is not, since this is a models file, not a router.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/rest_api/models/test_conversation_model.py -k assert_mutable -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/models/conversation.py tests/codemie/rest_api/models/test_conversation_model.py
git commit -m "EPMCDME-11067: add Conversation.assert_mutable() 409 guard"
```

---

## Task 3: Expose `is_finished`/`finished_at` on read models and the user list filter

**Files:**
- Modify: `src/codemie/rest_api/models/conversation.py` (`ConversationListItem` class at line 728, `ConversationResponse` class at line 752, `get_user_conversations` classmethod at line 476, `search_by_name_and_user` classmethod at line 637)
- Test: `tests/codemie/rest_api/models/test_conversation_model.py`, `tests/codemie/rest_api/routers/test_conversation.py`

**Interfaces:**
- Consumes: `Conversation.is_finished`, `Conversation.finished_at` (Task 1)
- Produces: `ConversationListItem.is_finished: Optional[bool]`, `ConversationListItem.finished_at: Optional[datetime]`, same two fields on `ConversationResponse` — Task 7's new admin list endpoint (`PaginatedListResponse[ConversationListItem]`) depends on `ConversationListItem` carrying these fields.

- [ ] **Step 1: Write the failing test**

This file uses a module-level `client = TestClient(app)`, a `user` fixture
(`User(id="123", username="testuser", name="Test User")`), and an autouse
`override_dependency` fixture that overrides `conversation_router.authenticate` —
reuse these exactly, don't invent new ones. Add:

```python
def test_get_conversation_by_id_includes_is_finished(user):
    conversation = Conversation(
        id="456",
        conversation_id="456",
        user_id=user.id,
        name="Test Conversation",
        is_finished=True,
        finished_at=datetime(2026, 8, 11, 12, 0, 0),
    )
    with patch.object(conversation_router.Conversation, "find_by_id", return_value=conversation):
        response = client.get("/v1/conversations/456")

    assert response.status_code == 200
    body = response.json()
    assert body["isFinished"] is True
    assert body["finishedAt"] is not None
```

Match the exact mocking target (`conversation_router.Conversation.find_by_id` vs.
`get_by_id`) to whatever the nearest existing `GET /conversations/{conversation_id}`
test in this file already patches — `get_conversation_by_id` in the router uses
`Conversation.find_by_id` in the non-paginated branch (see `conversation.py:229`),
so patch that same symbol for consistency with the code path actually exercised.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_conversation.py -k is_finished -v`
Expected: FAIL — `isFinished` key missing from the response body (or `None`/`False` when it should be `True`).

- [ ] **Step 3: Add fields to `ConversationListItem`**

In `src/codemie/rest_api/models/conversation.py`, inside `ConversationListItem` (after `assistant_names` at line 749):

```python
    is_finished: Optional[bool] = False
    finished_at: Optional[datetime] = None
```

- [ ] **Step 4: Add fields to `ConversationResponse`**

Inside `ConversationResponse` (after `very_last_msg_at` at line 789):

```python
    is_finished: Optional[bool] = False
    finished_at: Optional[datetime] = None
```

`ConversationResponse` has `model_config = ConfigDict(from_attributes=True)` and is built via `ConversationResponse.model_validate(conversation)` in the router (`conversation.py:262`) — these two new fields are picked up automatically from the `Conversation` ORM instance's matching attribute names, no extra wiring needed here.

- [ ] **Step 5: Populate `is_finished`/`finished_at` in `get_user_conversations`**

In `get_user_conversations` (line 476), the raw SQL `SELECT` (lines 509-533) needs `c.is_finished` and `c.finished_at` added to the column list, and the `ConversationListItem(...)` construction (lines 541-559) needs `is_finished=row.is_finished` and `finished_at=row.finished_at` added.

- [ ] **Step 6: Populate `is_finished`/`finished_at` in `search_by_name_and_user`**

Same pattern in `search_by_name_and_user` (line 637) — add both columns to its `SELECT` and to the `ConversationListItem(...)` construction at its end (this method's tail wasn't shown during planning; find and follow the exact construction pattern already used for `is_workflow_conversation` in this same method, which today's code already threads through the same way).

- [ ] **Step 7: Add `is_finished` to the user list filter**

In `_build_filter_sql`'s caller, `get_user_conversations` (line 498), add `"is_finished"` to the `allowed_filter_columns` set:

```python
        allowed_filter_columns = {
            "initial_assistant_id",
            "is_workflow_conversation",
            "folder",
            "pinned",
            "project",
            "is_finished",
        }
```

- [ ] **Step 8: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_conversation.py -k is_finished -v`
Expected: PASS

- [ ] **Step 9: Run the full existing conversation test suites to check for regressions**

Run: `poetry run pytest tests/codemie/rest_api/models/test_conversation_model.py tests/codemie/rest_api/routers/test_conversation.py tests/codemie/rest_api/routers/test_conversation_pagination.py tests/codemie/service/test_conversation_service_pagination.py -v`
Expected: all PASS — these files construct `ConversationListItem`/query `get_user_conversations` today and must not break from the new fields/columns.

- [ ] **Step 10: Commit**

```bash
git add src/codemie/rest_api/models/conversation.py tests/codemie/rest_api/routers/test_conversation.py
git commit -m "EPMCDME-11067: surface isFinished/finishedAt on conversation read paths"
```

---

## Task 4: Finish request/response models

**Files:**
- Modify: `src/codemie/rest_api/models/conversation.py` (add new classes near `UpsertHistoryResponse`/`ConversationResponse`)
- Test: none — pure data classes, exercised indirectly by Tasks 5-7's tests.

**Interfaces:**
- Produces:
  - `ConversationFinishResult(ConfiguredModel)`: `conversation_id: str`, `is_finished: bool`, `already_finished: bool`, `finished_at: Optional[datetime] = None`, `error: Optional[str] = None`
  - `ConversationFinishBulkRequest(ConfiguredModel)`: `conversation_ids: list[str]` (1-500 items)
  - `ConversationFinishBulkResponse(ConfiguredModel)`: `total: int`, `results: list[ConversationFinishResult]`
  - Task 5's `ConversationService.finish_conversation`/`finish_conversations_bulk` return these; Task 6/7's routers use them as `response_model`.

- [ ] **Step 1: Add the models**

In `src/codemie/rest_api/models/conversation.py`, add after `ConversationResponse` (after line 801, before `ConversationExportFormat` at line 804):

```python
class ConversationFinishResult(ConfiguredModel):
    """Result of one finish attempt — used as both the single-finish response and
    one item in a bulk-finish response."""

    conversation_id: str
    is_finished: bool
    already_finished: bool
    finished_at: Optional[datetime] = None
    error: Optional[str] = None


class ConversationFinishBulkRequest(ConfiguredModel):
    conversation_ids: list[str] = Field(min_length=1, max_length=500)


class ConversationFinishBulkResponse(ConfiguredModel):
    total: int
    results: list[ConversationFinishResult]
```

`ConfiguredModel` is defined in `codemie.core.models` (the camelCase-alias base every other request/response DTO in this codebase uses, e.g. `UpdateAiMessageRequest`, `CreateConversationRequest`) — import it: `from codemie.core.models import ConfiguredModel` (check whether `conversation.py` already imports from `codemie.core.models` for something else; if so, add to that existing import line instead of a new one).

- [ ] **Step 2: Verify import and class definitions are syntactically valid**

Run: `poetry run python -c "from codemie.rest_api.models.conversation import ConversationFinishResult, ConversationFinishBulkRequest, ConversationFinishBulkResponse; print('ok')"`
Expected: prints `ok` with no import errors.

- [ ] **Step 3: Commit**

```bash
git add src/codemie/rest_api/models/conversation.py
git commit -m "EPMCDME-11067: add conversation finish request/response models"
```

---

## Task 5: `ConversationService` — guard the 3 content-mutation methods and add finish logic

**Files:**
- Modify: `src/codemie/service/conversation_service.py` (`upsert_chat_history` at line 208, `upsert_conversation_with_history` at line 289, `update_conversation_ai_message` at line 855; add three new methods)
- Test: `tests/codemie/service/test_conversation_service.py`

**Interfaces:**
- Consumes: `Conversation.assert_mutable()` (Task 2), `Conversation.find_by_id` (existing), `ConversationFinishResult` (Task 4), `Ability(user).can(Action.WRITE, conversation)` (existing pattern from `upsert_conversation_history` router)
- Produces:
  - `ConversationService.finish_conversation(conversation_id: str, user: User, require_ownership: bool) -> ConversationFinishResult`
  - `ConversationService.finish_conversations_bulk(conversation_ids: list[str]) -> list[ConversationFinishResult]`
  - Task 6/7's routers call these two.

- [ ] **Step 1: Write the failing tests for the 3 guarded methods**

This file uses `mock_user`/`mock_admin_user` fixtures (`MagicMock`-based, with
`.id`/`.name`/`.is_admin` set — see the top of the file) and
`unittest.mock.patch`. Add, reusing those exact fixtures:

```python
def test_upsert_chat_history_raises_409_when_finished(mock_user, mock_assistant):
    finished_conversation = Conversation(
        id="conv-1", conversation_id="conv-1", user_id=mock_user.id,
        history=[], is_finished=True,
    )
    request = AssistantChatRequest(conversation_id="conv-1", text="hello")

    with patch.object(Conversation, "find_by_id", return_value=finished_conversation):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ConversationService.upsert_chat_history(
                assistant_response="hi", time_elapsed=1.0,
                tokens_usage=MagicMock(input_tokens=1, output_tokens=1, money_spent=0.0),
                request=request, assistant=mock_assistant, user=mock_user, thoughts=[],
            )
    assert exc_info.value.code == 409


def test_upsert_conversation_with_history_raises_409_when_finished(mock_user):
    finished_conversation = Conversation(
        id="conv-1", conversation_id="conv-1", user_id=mock_user.id,
        history=[], is_finished=True,
    )
    request = UpsertHistoryRequest(assistant_id="asst-1", history=[GeneratedMessage(message="hi", role="User")])

    with patch.object(Conversation, "find_by_id", return_value=finished_conversation):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ConversationService.upsert_conversation_with_history("conv-1", request, mock_user)
    assert exc_info.value.code == 409


def test_update_conversation_ai_message_raises_409_when_finished():
    conversation = Conversation(
        id="conv-1", conversation_id="conv-1", user_id="u1", history=[], is_finished=True,
    )
    request = UpdateAiMessageRequest(message_index=0, message="edited")

    with pytest.raises(ExtendedHTTPException) as exc_info:
        ConversationService.update_conversation_ai_message(conversation, history_index=0, request=request)
    assert exc_info.value.code == 409
```

`update_conversation_ai_message(cls, conversation: Conversation, history_index: int,
request: UpdateAiMessageRequest)` (`conversation_service.py:855`) takes an
**already-loaded** `Conversation` instance as a parameter — its caller in the
`conversation.py` router (`update_conversation_history_by_index`) is the one that
loads it via `Conversation.get_by_id`. So this third test needs no `find_by_id`
mocking at all, unlike the two above — just construct the finished `Conversation`
directly and pass it in. Implement all three tests as written, matching
`AssistantChatRequest`/`UpsertHistoryRequest`'s actual required fields (check
`codemie.core.models.AssistantChatRequest` and
`codemie.rest_api.models.conversation.UpsertHistoryRequest` for any required
fields not shown here and fill them in; both are already imported at the top of
this test file or need adding alongside the existing `UpdateAiMessageRequest`
import).

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/test_conversation_service.py -k "raises_409_when_finished" -v`
Expected: FAIL — no 409 raised today (`is_finished` doesn't exist as an enforcement concept yet).

- [ ] **Step 3: Add the guard calls**

In `src/codemie/service/conversation_service.py`:

In `upsert_chat_history` (line 208), immediately after the conversation is resolved (after line 227's `conversation, should_create_conversation, schedule_naming = cls._find_or_create_conversation(...)`), add:

```python
        if not should_create_conversation:
            conversation.assert_mutable()
```

(Guarded only when the conversation already existed — a brand-new conversation is never finished, and `_find_or_create_conversation` returns a fresh in-memory `Conversation()` with `is_finished` defaulting to `False` in the create branch anyway, so this check is technically redundant but explicit is better than relying on the default for a security-relevant guard.)

In `upsert_conversation_with_history` (line 289), immediately after the existence check (after line 318's `conversation = Conversation.find_by_id(conversation_id)`), add:

```python
        if conversation:
            conversation.assert_mutable()
```

Note this must be added **before** the existing `if conversation:` / `else:` branch at line 320 — restructure so the guard runs, then the existing branch logic continues unchanged.

In `update_conversation_ai_message` (line 855), which receives `conversation` as an
already-loaded parameter (its caller loads it, this method doesn't), add the guard
as the very first line of the method body, before the existing `messages = [...]`
list-comprehension:

```python
    @classmethod
    def update_conversation_ai_message(
        cls, conversation: Conversation, history_index: int, request: UpdateAiMessageRequest
    ):
        conversation.assert_mutable()
        messages = [
            history_message
            for history_message in conversation.history
            if history_message.history_index == history_index
        ]
        ...  # rest of the existing method body unchanged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/test_conversation_service.py -k "raises_409_when_finished" -v`
Expected: PASS (all 3)

- [ ] **Step 5: Run the full existing service test suite to check for regressions**

Run: `poetry run pytest tests/codemie/service/test_conversation_service.py -v`
Expected: all PASS — the guard must not fire for non-finished conversations, which is the overwhelming majority of existing test cases.

- [ ] **Step 6: Write the failing test for `finish_conversation`**

```python
def test_finish_conversation_marks_finished_and_returns_result(mock_user):
    conversation = Conversation(
        id="conv-1", conversation_id="conv-1", user_id=mock_user.id, history=[],
    )
    with patch.object(Conversation, "find_by_id", return_value=conversation), \
         patch.object(Conversation, "update", return_value=None):
        result = ConversationService.finish_conversation("conv-1", mock_user, require_ownership=True)
    assert result.is_finished is True
    assert result.already_finished is False
    assert result.finished_at is not None


def test_finish_conversation_idempotent_when_already_finished(mock_user):
    conversation = Conversation(
        id="conv-1", conversation_id="conv-1", user_id=mock_user.id, history=[],
        is_finished=True, finished_at=datetime(2026, 8, 11, 12, 0, 0),
    )
    with patch.object(Conversation, "find_by_id", return_value=conversation):
        result = ConversationService.finish_conversation("conv-1", mock_user, require_ownership=True)
    assert result.is_finished is True
    assert result.already_finished is True


def test_finish_conversation_raises_404_when_not_found(mock_user):
    with patch.object(Conversation, "find_by_id", return_value=None):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ConversationService.finish_conversation("missing-id", mock_user, require_ownership=True)
    assert exc_info.value.code == 404


def test_finish_conversation_raises_403_when_not_owner_and_ownership_required(mock_user):
    conversation = Conversation(
        id="conv-1", conversation_id="conv-1", user_id="someone-else", history=[],
    )
    with patch.object(Conversation, "find_by_id", return_value=conversation):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ConversationService.finish_conversation("conv-1", mock_user, require_ownership=True)
    assert exc_info.value.code == 403


def test_finish_conversation_skips_ownership_check_when_not_required(mock_admin_user):
    conversation = Conversation(
        id="conv-1", conversation_id="conv-1", user_id="someone-else", history=[],
    )
    with patch.object(Conversation, "find_by_id", return_value=conversation), \
         patch.object(Conversation, "update", return_value=None):
        result = ConversationService.finish_conversation("conv-1", mock_admin_user, require_ownership=False)
    assert result.is_finished is True
```

Add `from datetime import datetime` and `from codemie.core.exceptions import ExtendedHTTPException`
to this test file's imports if not already present.

- [ ] **Step 7: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/test_conversation_service.py -k finish_conversation -v`
Expected: FAIL — `finish_conversation` doesn't exist yet (`AttributeError`).

- [ ] **Step 8: Implement `_finish_loaded_conversation`, `finish_conversation`, `finish_conversations_bulk`**

Add to `ConversationService` in `src/codemie/service/conversation_service.py` (near `add_feedback`/`remove_feedback` around line 531, since these are the other single-conversation-mutation-by-id methods in this class):

```python
    @classmethod
    def _finish_loaded_conversation(cls, conversation: Conversation) -> ConversationFinishResult:
        if conversation.is_finished:
            return ConversationFinishResult(
                conversation_id=conversation.id,
                is_finished=True,
                already_finished=True,
                finished_at=conversation.finished_at,
            )
        conversation.is_finished = True
        conversation.finished_at = datetime.now()
        conversation.update()
        return ConversationFinishResult(
            conversation_id=conversation.id,
            is_finished=True,
            already_finished=False,
            finished_at=conversation.finished_at,
        )

    @classmethod
    def finish_conversation(cls, conversation_id: str, user: User, require_ownership: bool) -> ConversationFinishResult:
        conversation = Conversation.find_by_id(conversation_id)
        if not conversation:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=CONVERSATION_NOT_FOUND_MESSAGE,
                details=f"The conversation with ID [{conversation_id}] could not be found in the system.",
                help=CONVERSATION_NOT_FOUND_HELP,
            )
        if require_ownership and not Ability(user).can(Action.WRITE, conversation):
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Access denied",
                details=f"User {user.id} does not have write access to conversation {conversation_id}.",
                help="Only the conversation owner can finish it.",
            )
        return cls._finish_loaded_conversation(conversation)

    @classmethod
    def finish_conversations_bulk(cls, conversation_ids: list[str]) -> list[ConversationFinishResult]:
        results = []
        for conversation_id in conversation_ids:
            conversation = Conversation.find_by_id(conversation_id)
            if not conversation:
                results.append(
                    ConversationFinishResult(
                        conversation_id=conversation_id,
                        is_finished=False,
                        already_finished=False,
                        finished_at=None,
                        error="not_found",
                    )
                )
                continue
            results.append(cls._finish_loaded_conversation(conversation))
        return results
```

This needs `ConversationFinishResult` imported from `codemie.rest_api.models.conversation` (add to the existing `from codemie.rest_api.models.conversation import (...)` block at the top of this file, alongside `Conversation`, `UpsertHistoryRequest`, etc.) and `status` from `fastapi` (check if already imported; `conversation_service.py` is a service file, may not import `fastapi.status` yet — add `from fastapi import status` if missing). `CONVERSATION_NOT_FOUND_MESSAGE`/`CONVERSATION_NOT_FOUND_HELP` already exist as constants imported by `conversation.py` router from `feedback.py` (`from codemie.rest_api.routers.feedback import CONVERSATION_NOT_FOUND_MESSAGE, CONVERSATION_NOT_FOUND_HELP`) — reuse the same import here for consistency with the existing 404 message used elsewhere for conversations, rather than inventing new text.

- [ ] **Step 9: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/test_conversation_service.py -k finish_conversation -v`
Expected: PASS (all 5)

- [ ] **Step 10: Write and run the failing/passing test for `finish_conversations_bulk`**

```python
def test_finish_conversations_bulk_mixed_results():
    conv1 = Conversation(id="conv-1", conversation_id="conv-1", user_id="u1", history=[])
    conv2 = Conversation(
        id="conv-2", conversation_id="conv-2", user_id="u1", history=[],
        is_finished=True, finished_at=datetime(2026, 8, 11, 12, 0, 0),
    )
    by_id_lookup = {"conv-1": conv1, "conv-2": conv2, "conv-3": None}

    with patch.object(Conversation, "find_by_id", side_effect=lambda cid: by_id_lookup[cid]), \
         patch.object(Conversation, "update", return_value=None):
        results = ConversationService.finish_conversations_bulk(["conv-1", "conv-2", "conv-3"])

    by_id = {r.conversation_id: r for r in results}
    assert by_id["conv-1"].is_finished is True and by_id["conv-1"].already_finished is False
    assert by_id["conv-2"].is_finished is True and by_id["conv-2"].already_finished is True
    assert by_id["conv-3"].error == "not_found"
```

Run: `poetry run pytest tests/codemie/service/test_conversation_service.py -k finish_conversations_bulk -v`
Expected: FAIL then PASS after Step 8's implementation (already written — this step is verifying the bulk path specifically since Step 8 covered the single-item helper it reuses).

- [ ] **Step 11: Commit**

```bash
git add src/codemie/service/conversation_service.py tests/codemie/service/test_conversation_service.py
git commit -m "EPMCDME-11067: guard content-mutation methods and add finish logic to ConversationService"
```

---

## Task 6: `WorkflowService` — guard the 2 workflow content-mutation methods

**Files:**
- Modify: `src/codemie/service/workflow_service.py` (`create_workflow_execution` at line 176, `append_user_message_on_resume` at line 456)
- Test: `tests/codemie/service/test_workflow_service.py` (or the correct existing test file for `WorkflowService` — confirm the exact filename via `poetry run pytest --collect-only tests/codemie/service/ -k workflow_service` before writing, since the spec's guess at this filename is unverified)

**Interfaces:**
- Consumes: `Conversation.assert_mutable()` (Task 2), `Conversation.get_by_id` (existing)

- [ ] **Step 1: Write the failing test for `append_user_message_on_resume`**

This file uses `user`/`admin_user` fixtures (real `User(...)` instances, not
mocks — see the top of the file) and `unittest.mock.patch`. Add:

```python
def test_append_user_message_on_resume_raises_409_when_conversation_finished(user):
    execution = WorkflowExecution(
        workflow_id="wf-1", execution_id="exec-1", conversation_id="conv-1",
        history=[], overall_status=WorkflowExecutionStatusEnum.INTERRUPTED,
        project=EXAMPLE_PROJECT, created_by=user.as_user_model(),
    )
    finished_conversation = Conversation(
        id="conv-1", conversation_id="conv-1", user_id=user.id, history=[], is_finished=True,
    )
    workflow_service = WorkflowService()

    with patch.object(Conversation, "get_by_id", return_value=finished_conversation), \
         patch.object(WorkflowExecution, "update", return_value=None):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            workflow_service.append_user_message_on_resume(execution, "hello")
    assert exc_info.value.code == 409
```

Check `WorkflowExecution`'s actual required constructor fields against
`codemie.core.workflow_models.workflow_execution.WorkflowExecution` before
finalizing — the fields above are inferred from `append_user_message_on_resume`'s
own usage (`execution.history`, `execution.conversation_id`, `execution.workflow_id`)
and may need adjustment to satisfy the model's full required-field set.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/service/test_workflow_service.py -k append_user_message_on_resume_raises_409 -v`
Expected: FAIL — no 409 raised today.

- [ ] **Step 3: Add the guard to `append_user_message_on_resume`**

In `src/codemie/service/workflow_service.py`, in `append_user_message_on_resume` (line 456), immediately after the conversation is loaded (after line 488's `conversation = Conversation.get_by_id(execution.conversation_id)`), add:

```python
            conversation.assert_mutable()
```

This must run **before** line 489's `conversation.history = [...]` append.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/service/test_workflow_service.py -k append_user_message_on_resume_raises_409 -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for `create_workflow_execution`'s chat branch**

```python
def test_create_workflow_execution_raises_409_when_existing_conversation_finished(user):
    workflow_config = WorkflowConfig(
        id="wf-1", name="Test Workflow", project=EXAMPLE_PROJECT, mode=WorkflowMode.CHAT,
    )
    finished_conversation = Conversation(
        id="conv-1", conversation_id="conv-1", user_id=user.id, history=[], is_finished=True,
    )

    with patch.object(Conversation, "get_by_id", return_value=finished_conversation):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            WorkflowService.create_workflow_execution(
                workflow_config=workflow_config,
                user=user.as_user_model(),
                user_input="hi",
                conversation_id="conv-1",
            )
    assert exc_info.value.code == 409
```

Check `WorkflowConfig`'s and `create_workflow_execution`'s actual required
arguments (its `user` parameter is typed `UserEntity`, per the method signature —
use `user.as_user_model()` or the equivalent conversion this test file's other
`WorkflowConfig`-constructing tests already use, matching that exact pattern
rather than guessing) against the nearest existing `WorkflowConfig(...)`
construction in this file before finalizing.

- [ ] **Step 6: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/service/test_workflow_service.py -k create_workflow_execution_raises_409 -v`
Expected: FAIL.

- [ ] **Step 7: Add the guard to `create_workflow_execution`**

In `create_workflow_execution` (line 176), in the `is_chat_execution` branch, after the existing conversation is fetched (after line 203's `conversation = Conversation.get_by_id(conversation_id)` inside the `try` block), add the guard **inside that same `try`**, right after the successful fetch:

```python
                try:
                    conversation = Conversation.get_by_id(conversation_id)
                    conversation.assert_mutable()
                    logger.debug(f"Using existing conversation {conversation_id} for workflow execution")
                except Exception:
                    ...
```

**Careful**: `assert_mutable()` raises `ExtendedHTTPException`, and this code sits inside a bare `except Exception:` that currently treats *any* failure to fetch as "conversation doesn't exist yet, create a new one" (line 204's comment: `# Create new conversation for this workflow chat`). If `assert_mutable()`'s exception is swallowed by that `except Exception:`, a finished conversation would silently get a *new* conversation created instead of a 409 — the opposite of what we want. Re-raise `ExtendedHTTPException` explicitly so it isn't caught by the generic fallback:

```python
                try:
                    conversation = Conversation.get_by_id(conversation_id)
                    conversation.assert_mutable()
                    logger.debug(f"Using existing conversation {conversation_id} for workflow execution")
                except ExtendedHTTPException:
                    raise
                except Exception:
                    # Create new conversation for this workflow chat
                    conversation = Conversation(...)
                    ...
```

Confirm `ExtendedHTTPException` is imported in `workflow_service.py` already (check the top-of-file import block; if absent, add `from codemie.core.exceptions import ExtendedHTTPException`).

- [ ] **Step 8: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/service/test_workflow_service.py -k create_workflow_execution_raises_409 -v`
Expected: PASS

- [ ] **Step 9: Run the full existing workflow service test suite to check for regressions**

Run: `poetry run pytest tests/codemie/service/test_workflow_service.py -v`
Expected: all PASS — in particular any test that relies on the "conversation not found → create new" fallback for a genuinely-missing conversation ID must still pass (that path is untouched, only the `ExtendedHTTPException` re-raise is new).

- [ ] **Step 10: Commit**

```bash
git add src/codemie/service/workflow_service.py tests/codemie/service/test_workflow_service.py
git commit -m "EPMCDME-11067: guard workflow chat-send and resume against finished conversations"
```

---

## Task 7: User finish endpoint

**Files:**
- Modify: `src/codemie/rest_api/routers/conversation.py` (add route near `upsert_conversation_history` at line 412, and its imports)
- Test: `tests/codemie/rest_api/routers/test_conversation.py`

**Interfaces:**
- Consumes: `ConversationService.finish_conversation` (Task 5), `ConversationFinishResult` (Task 4)

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/rest_api/routers/test_conversation.py`. `client` is a
module-level `TestClient(app)` (not a fixture — reference it directly, don't add
it as a test parameter); `user` is the injected fixture, and the autouse
`override_dependency` fixture already wires `conversation_router.authenticate` to
return it — see Task 3 Step 1's `test_get_conversation_by_id_includes_is_finished`
for the exact pattern this file uses:

```python
def test_finish_conversation_success(user):
    conversation = Conversation(
        id="456", conversation_id="456", user_id=user.id, history=[],
    )
    with patch.object(conversation_router.Conversation, "find_by_id", return_value=conversation), \
         patch.object(conversation_router.Conversation, "update", return_value=None):
        response = client.post("/v1/conversations/456/finish")

    assert response.status_code == 200
    body = response.json()
    assert body["isFinished"] is True
    assert body["alreadyFinished"] is False


def test_finish_conversation_idempotent(user):
    conversation = Conversation(
        id="456", conversation_id="456", user_id=user.id, history=[],
        is_finished=True, finished_at=datetime(2026, 8, 11, 12, 0, 0),
    )
    with patch.object(conversation_router.Conversation, "find_by_id", return_value=conversation):
        response = client.post("/v1/conversations/456/finish")

    assert response.status_code == 200
    assert response.json()["alreadyFinished"] is True


def test_finish_conversation_404_when_missing(user):
    with patch.object(conversation_router.Conversation, "find_by_id", return_value=None):
        response = client.post("/v1/conversations/does-not-exist/finish")
    assert response.status_code == 404


def test_finish_conversation_403_when_not_owner(user):
    conversation = Conversation(
        id="456", conversation_id="456", user_id="someone-else", history=[],
    )
    with patch.object(conversation_router.Conversation, "find_by_id", return_value=conversation):
        response = client.post("/v1/conversations/456/finish")
    assert response.status_code == 403
```

The router itself is unprefixed and mounted with `/v1` at the app level (confirmed
by the existing `PUT /v1/conversations/{conversation_id}` reference in this same
file's `_find_or_create_conversation` comment) — the paths above are correct as
written. Patches target `conversation_router.Conversation` (the name bound inside
`codemie.rest_api.routers.conversation`, per this file's existing import
`import codemie.rest_api.routers.conversation as conversation_router`), not the
model module directly, matching this file's own patching convention.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_conversation.py -k test_finish_conversation -v`
Expected: FAIL with 404 (route doesn't exist) for all four.

- [ ] **Step 3: Add the endpoint**

In `src/codemie/rest_api/routers/conversation.py`, add near `upsert_conversation_history` (after it, or before `delete_conversation_by_id` at line 299 — pick a spot near the other single-conversation-by-id mutation endpoints):

```python
@router.post(
    "/conversations/{conversation_id}/finish",
    response_model=ConversationFinishResult,
)
def finish_conversation(conversation_id: str, user: User = Depends(authenticate)) -> ConversationFinishResult:
    """
    Mark a conversation as finished (idempotent, owner-only).

    Once finished, further message-append/edit operations on this conversation
    return HTTP 409.
    """
    return ConversationService.finish_conversation(conversation_id, user, require_ownership=True)
```

Add `ConversationFinishResult` to the existing `from codemie.rest_api.models.conversation import (...)` block at the top of this file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_conversation.py -k test_finish_conversation -v`
Expected: PASS (all four)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/routers/conversation.py tests/codemie/rest_api/routers/test_conversation.py
git commit -m "EPMCDME-11067: add POST /conversations/{id}/finish endpoint"
```

---

## Task 8: Admin single and bulk finish endpoints

**Files:**
- Modify: `src/codemie/rest_api/routers/admin.py` (add two routes, and imports)
- Test: `tests/codemie/rest_api/routers/test_admin_router.py` (confirmed filename — this
  file does **not** use `TestClient`/HTTP; it calls router functions directly and
  patches dependencies with `unittest.mock.patch`, targeting the module path constant
  `ADMIN_MODULE = "codemie.rest_api.routers.admin"` already defined at the top of the
  file. The router-level `admin_access_only` dependency is exercised at the FastAPI
  wiring level, not per-test — so 403 is tested by calling
  `codemie.rest_api.security.authentication.admin_access_only` directly with a
  non-admin `User`, the same way this file's existing tests do (do not invent an
  HTTP-based 403 test here; follow the file's own convention).

**Interfaces:**
- Consumes: `ConversationService.finish_conversation` / `finish_conversations_bulk` (Task 5), `ConversationFinishResult` / `ConversationFinishBulkRequest` / `ConversationFinishBulkResponse` (Task 4)

- [ ] **Step 1: Write the failing tests**

```python
def test_admin_finish_conversation_success():
    admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
    expected = ConversationFinishResult(
        conversation_id="conv-1", is_finished=True, already_finished=False,
        finished_at=datetime(2026, 8, 11, 12, 0, 0),
    )
    with patch(f"{ADMIN_MODULE}.ConversationService.finish_conversation", return_value=expected) as mock_finish:
        result = admin_finish_conversation("conv-1", user=admin)
    assert result.is_finished is True
    mock_finish.assert_called_once_with("conv-1", admin, require_ownership=False)


def test_admin_finish_conversations_bulk_mixed_results():
    admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
    expected = [
        ConversationFinishResult(conversation_id="conv-1", is_finished=True, already_finished=False, finished_at=datetime(2026, 8, 11, 12, 0, 0)),
        ConversationFinishResult(conversation_id="conv-2", is_finished=False, already_finished=False, finished_at=None, error="not_found"),
    ]
    payload = ConversationFinishBulkRequest(conversation_ids=["conv-1", "conv-2"])

    with patch(f"{ADMIN_MODULE}.ConversationService.finish_conversations_bulk", return_value=expected):
        response = admin_finish_conversations_bulk(payload)

    assert response.total == 2
    results_by_id = {r.conversation_id: r for r in response.results}
    assert results_by_id["conv-1"].is_finished is True
    assert results_by_id["conv-2"].error == "not_found"


def test_conversation_finish_bulk_request_rejects_empty_list():
    with pytest.raises(ValidationError):
        ConversationFinishBulkRequest(conversation_ids=[])
```

`ConversationFinishBulkRequest`'s empty-list rejection is a Pydantic-level
`min_length=1` validation (Task 4), so it's tested by constructing the model
directly rather than through the endpoint function — add
`from pydantic import ValidationError` to this test file's imports.
Add `admin_finish_conversation`, `admin_finish_conversations_bulk` to the existing
`from codemie.rest_api.routers.admin import (...)` block at the top of this file
(alongside `add_application`, `get_applications`, etc.), and
`from codemie.rest_api.models.conversation import ConversationFinishResult, ConversationFinishBulkRequest`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_admin_router.py -k "admin_finish_conversation or conversation_finish_bulk_request" -v`
Expected: FAIL (`ImportError`, symbols don't exist yet) for all three.

- [ ] **Step 3: Add the endpoints**

In `src/codemie/rest_api/routers/admin.py`, add (near the other conversation-adjacent admin routes at lines 73-84):

```python
@router.post(
    "/admin/conversations/{conversation_id}/finish",
    response_model=ConversationFinishResult,
)
def admin_finish_conversation(conversation_id: str, user: User = Depends(authenticate)) -> ConversationFinishResult:
    """Finish a single conversation as admin (idempotent, no ownership required)."""
    return ConversationService.finish_conversation(conversation_id, user, require_ownership=False)


@router.post(
    "/admin/conversations/finish-bulk",
    response_model=ConversationFinishBulkResponse,
)
def admin_finish_conversations_bulk(payload: ConversationFinishBulkRequest) -> ConversationFinishBulkResponse:
    """Finish multiple conversations in one request, with per-ID success/error reporting."""
    results = ConversationService.finish_conversations_bulk(payload.conversation_ids)
    return ConversationFinishBulkResponse(total=len(results), results=results)
```

The router-level `dependencies=[Depends(authenticate), Depends(admin_access_only)]` already enforces admin-only access on every route in this file — `admin_finish_conversations_bulk` doesn't need a `user` param at all since `finish_conversations_bulk` doesn't take one. `admin_finish_conversation` still declares `user: User = Depends(authenticate)` because `ConversationService.finish_conversation` requires a `user` argument (used only for the `require_ownership=False` no-op path, but the signature is shared with the user-facing endpoint from Task 7).

Add `ConversationService` and `ConversationFinishResult, ConversationFinishBulkRequest, ConversationFinishBulkResponse` to this file's imports (check the top-of-file import block for the existing pattern; `admin.py` may not currently import `ConversationService` at all — add both a new `from codemie.service.conversation_service import ConversationService` line and extend/add the `from codemie.rest_api.models.conversation import (...)` import).

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_admin_router.py -k "admin_finish_conversation or conversation_finish_bulk_request" -v`
Expected: PASS (all three)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/routers/admin.py tests/codemie/rest_api/routers/test_admin_router.py
git commit -m "EPMCDME-11067: add admin single and bulk conversation finish endpoints"
```

---

## Task 9: `GET /admin/conversations` list endpoint

**Files:**
- Modify: `src/codemie/rest_api/models/conversation.py` (new `Conversation.get_all_conversations_admin` classmethod)
- Modify: `src/codemie/rest_api/routers/admin.py` (new route, imports)
- Test: `tests/codemie/rest_api/models/test_conversation_model.py`, `tests/codemie/rest_api/routers/test_admin.py`

**Interfaces:**
- Consumes: `Conversation.is_finished`, `Conversation.date` (Task 1), `ConversationListItem` (Task 3), `PaginatedListResponse`/`PaginationData` (existing, `rest_api/models/base.py:567-576`)
- Produces: `Conversation.get_all_conversations_admin(is_finished: Optional[bool], started_after: Optional[datetime], page: int, per_page: int) -> tuple[list[ConversationListItem], int]`

- [ ] **Step 1: Write the failing test for the model classmethod**

This file never touches a real database — `get_user_conversations` and
`search_by_name_and_user` are both tested by
`@patch('codemie.rest_api.models.conversation.get_session')` and a
`MagicMock` session whose `.exec(...).all()` returns hand-built row objects (see
`test_conversation_search_by_name_and_user` for the exact pattern: it builds rows
via a `_make_row(...)` helper and sets `mock_get_session.return_value.__enter__.return_value
= mock_session`). Follow that same pattern — `get_all_conversations_admin` (Step 3)
also uses `get_session()`, not a direct `Session(engine)`, specifically so it's
mockable the same way:

```python
@patch('codemie.rest_api.models.conversation.get_session')
def test_get_all_conversations_admin_filters_by_is_finished(mock_get_session):
    mock_session = MagicMock()
    mock_session.exec.return_value.one.return_value = 2  # count query
    row = SimpleNamespace(
        conversation_id="conv-1", conversation_name="Test", folder=None, pinned=False,
        date=datetime(2026, 8, 11, 10, 0, 0), update_date=None,
        assistant_ids=[], initial_assistant_id=None, is_workflow_conversation=False,
        is_finished=False, finished_at=None,
    )
    # First .exec(...).one() call (count) returns 2; second .exec(...).all() (rows) returns [row]
    mock_session.exec.side_effect = [
        MagicMock(one=MagicMock(return_value=2)),
        MagicMock(all=MagicMock(return_value=[row])),
    ]
    mock_get_session.return_value.__enter__.return_value = mock_session

    items, total = Conversation.get_all_conversations_admin(
        is_finished=False, started_after=None, page=0, per_page=20
    )
    assert total == 2
    assert len(items) == 1
    assert items[0].is_finished is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/models/test_conversation_model.py -k get_all_conversations_admin -v`
Expected: FAIL — `AttributeError: type object 'Conversation' has no attribute 'get_all_conversations_admin'`

- [ ] **Step 3: Implement the classmethod**

In `src/codemie/rest_api/models/conversation.py`, add near `get_user_conversations` (after it, before `find_messages` at line 562). Use `get_session()` (already imported in this file, used by `get_user_conversations`), not `Session(cls.get_engine())`, so the method is mockable the same way as its neighbors:

```python
    @classmethod
    def get_all_conversations_admin(
        cls,
        is_finished: Optional[bool] = None,
        started_after: Optional[datetime] = None,
        page: int = 0,
        per_page: int = 20,
    ) -> tuple[List[ConversationListItem], int]:
        """
        Admin-scoped conversation list across all users, for scheduler-driven
        discovery of unfinished conversations (EPMCDME-11067). Filtering by
        is_finished=False uses the ix_conversations_unfinished partial index.
        """
        statement = select(cls)
        if is_finished is not None:
            statement = statement.where(cls.is_finished == is_finished)
        if started_after is not None:
            statement = statement.where(cls.date >= started_after)

        with get_session() as session:
            total = session.exec(select(func.count()).select_from(statement.subquery())).one()

            paginated_statement = statement.order_by(cls.date.asc()).offset(page * per_page).limit(per_page)
            rows = session.exec(paginated_statement).all()

            items = [
                ConversationListItem(
                    id=row.conversation_id,
                    name=row.conversation_name or "",
                    folder=row.folder,
                    pinned=row.pinned,
                    date=row.update_date or row.date,
                    update_date=row.update_date,
                    assistant_ids=row.assistant_ids,
                    initial_assistant_id=row.initial_assistant_id,
                    is_workflow=bool(row.is_workflow_conversation),
                    workflow_id=row.initial_assistant_id if row.is_workflow_conversation else None,
                    conversation_id=row.conversation_id if row.is_workflow_conversation else None,
                    is_finished=row.is_finished,
                    finished_at=row.finished_at,
                )
                for row in rows
            ]
            return items, total
```

Confirm `func` is imported from `sqlalchemy` at the top of this file (used elsewhere for aggregate queries in this codebase per `workflow_service.py`'s `func.count()`; if `conversation.py` doesn't already import it, add `from sqlalchemy import func`). `select` and `get_session` are already imported in this file (used by `get_user_conversations`/`search_by_name_and_user`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/models/test_conversation_model.py -k get_all_conversations_admin -v`
Expected: PASS

- [ ] **Step 5: Write the failing router-level tests**

Following the same direct-call + `unittest.mock.patch` convention as
`test_admin_router.py` (see Task 8):

```python
def test_list_admin_conversations_filters_by_is_finished():
    items = [
        ConversationListItem(id="conv-1", date=datetime(2026, 8, 11, 10, 0, 0), is_finished=False),
    ]
    with patch(f"{ADMIN_MODULE}.Conversation.get_all_conversations_admin", return_value=(items, 1)) as mock_query:
        response = list_admin_conversations(is_finished=False, started_after=None, page=0, per_page=20)

    assert response.pagination.total == 1
    assert all(item.is_finished is False for item in response.data)
    mock_query.assert_called_once_with(is_finished=False, started_after=None, page=0, per_page=20)


def test_list_admin_conversations_paginates():
    items = [ConversationListItem(id="conv-1", date=datetime(2026, 8, 11, 10, 0, 0))]
    with patch(f"{ADMIN_MODULE}.Conversation.get_all_conversations_admin", return_value=(items, 3)):
        response = list_admin_conversations(is_finished=None, started_after=None, page=0, per_page=1)

    assert response.pagination.total == 3
    assert len(response.data) == 1
    assert response.pagination.pages == 3
```

Add `list_admin_conversations` to the existing `from codemie.rest_api.routers.admin
import (...)` block, and `from codemie.rest_api.models.conversation import
ConversationListItem` to this test file's imports. No separate 403-for-non-admin
test here — the router-level `admin_access_only` dependency wiring is FastAPI
framework behavior, not this endpoint's own logic, and this file's existing tests
don't re-test that dependency per endpoint (confirmed by Task 8's equivalent
scoping decision).

- [ ] **Step 6: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_admin_router.py -k list_admin_conversations -v`
Expected: FAIL (`ImportError`, symbol doesn't exist yet)

- [ ] **Step 7: Add the endpoint**

In `src/codemie/rest_api/routers/admin.py`:

```python
@router.get(
    "/admin/conversations",
    response_model=PaginatedListResponse[ConversationListItem],
)
def list_admin_conversations(
    is_finished: Optional[bool] = Query(None, alias="isFinished"),
    started_after: Optional[datetime] = Query(None, alias="startedAt"),
    page: int = Query(DEFAULT_PAGE, ge=0),
    per_page: int = Query(DEFAULT_CONVERSATIONS_PER_PAGE, ge=1, le=MAX_CONVERSATIONS_PER_PAGE, alias="perPage"),
) -> PaginatedListResponse[ConversationListItem]:
    """
    Paginated, cross-user conversation list for admin/scheduler use. Filter by
    isFinished to find unfinished conversations at scale (EPMCDME-11067).
    """
    items, total = Conversation.get_all_conversations_admin(
        is_finished=is_finished, started_after=started_after, page=page, per_page=per_page
    )
    pages = (total + per_page - 1) // per_page if per_page > 0 else 0
    return PaginatedListResponse(
        data=items,
        pagination=PaginationData(page=page, per_page=per_page, total=total, pages=pages),
    )
```

Add imports: `Conversation`, `ConversationListItem` from `codemie.rest_api.models.conversation`; `PaginatedListResponse`, `PaginationData` from `codemie.rest_api.models.base`; `DEFAULT_PAGE`, `DEFAULT_CONVERSATIONS_PER_PAGE`, `MAX_CONVERSATIONS_PER_PAGE` from `codemie.service.constants`; `Query` from `fastapi` (check if `fastapi` imports in this file already include `Query` — the router imports block at the top of `admin.py` wasn't fully sourced during planning, verify and extend rather than duplicate); `datetime` from the `datetime` module.

- [ ] **Step 8: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_admin_router.py -k list_admin_conversations -v`
Expected: PASS (both)

- [ ] **Step 9: Commit**

```bash
git add src/codemie/rest_api/models/conversation.py src/codemie/rest_api/routers/admin.py tests/codemie/rest_api/models/test_conversation_model.py tests/codemie/rest_api/routers/test_admin_router.py
git commit -m "EPMCDME-11067: add GET /admin/conversations paginated list endpoint"
```

---

## Task 10: Full regression pass and quality gates

**Files:** none (verification only)

- [ ] **Step 1: Run the full targeted test suite**

Run: `poetry run pytest tests/codemie/rest_api/models/test_conversation_model.py tests/codemie/rest_api/routers/test_conversation.py tests/codemie/rest_api/routers/test_conversation_pagination.py tests/codemie/rest_api/routers/test_admin_router.py tests/codemie/service/test_conversation_service.py tests/codemie/service/test_conversation_service_pagination.py tests/codemie/service/test_workflow_service.py tests/codemie/core/test_ability_conversation.py -v`
Expected: all PASS.

- [ ] **Step 2: Run quality gates**

Follow `.ai-run/guides/quality-gates.md` for the exact commands (typically `make ruff` / `make lint` / project's configured gate commands) and run them, reporting exactly what was run and its result.

- [ ] **Step 3: Commit any lint/format fixes**

```bash
git add -A
git commit -m "EPMCDME-11067: fix lint/format issues"
```

(Only if Step 2 produced changes; skip if clean.)
