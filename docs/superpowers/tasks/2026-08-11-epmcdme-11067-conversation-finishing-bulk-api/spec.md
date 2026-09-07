# Platform-enforced Conversation Finishing with Bulk API Support

**Ticket**: EPMCDME-11067
**Date**: 2026-08-11

## Problem

Customers want a platform capability to stop users from continuing conversations that
are considered stale, to reduce the chance of the model hallucinating on very long
chats. Today `Conversation` has no notion of a terminal state — any conversation can
be appended to indefinitely.

## Scope

CodeMie exposes reactive finish APIs (single, user; single, admin; bulk, admin) plus
finished-state fields on every conversation read/list response (`is_finished`/
`finished_at` on get/list/search responses, `isFinished`/`finishedAt` on the finish
endpoints themselves — see "Data model" for why the casing differs between the two).
Enforcement blocks
message-content mutation once a conversation is finished. The "N hours" staleness
policy itself is **not** implemented server-side — it lives entirely in whatever
external scheduler calls these APIs (confirmed with stakeholder; matches the ticket's
own Implementation Plan and scenarios, which only describe scheduler-driven closure).

**Out of scope**: a background/cron job inside CodeMie that auto-finishes conversations
by age. If a future ticket needs that, it can be layered on top of the same
`is_finished` field without touching the enforcement path built here.

## Data model

`Conversation` (`src/codemie/rest_api/models/conversation.py:225`) gets two new columns:

```python
is_finished: Optional[bool] = SQLField(default=False, sa_column=Column(Boolean))
finished_at: Optional[datetime] = SQLField(default=None, sa_column=Column(DateTime(timezone=True)))
```

Alembic migration (new revision under `src/external/alembic/versions/`) adds both
columns plus a partial index:

```sql
CREATE INDEX ix_conversations_unfinished ON conversations (date) WHERE is_finished = false;
```

**Why a partial index, and why it's safe for interactive chat writes**: neither `date`
nor `is_finished` is touched by any of the 5 content-mutation call sites below — both
are set once at conversation creation and only `is_finished`/`finished_at` change again,
exactly once, at finish time. This mirrors the existing `is_workflow_conversation`
index (`e8f3a9b5c2d1_add_is_workflow_conversation_to_conversations.py`), which is also
set once at creation and never mutated afterward — confirmed precedent that indexing a
creation-time-immutable column on this table doesn't add per-message write cost.
Postgres HOT (Heap-Only Tuple) updates skip index maintenance entirely when the
updated columns aren't part of an index's key/predicate columns, so this index costs
nothing on the per-message chat-send path and is only paid once, at finish (already a
non-interactive/admin/scheduler-driven operation). Scheduler list queries
(`GET /admin/conversations?isFinished=false&...`) are the direct beneficiary — this
keeps that query off a full-table scan as data grows (AC6/AC9).

`ConversationListItem` (line 728) and `ConversationResponse` get `is_finished` /
`finished_at` fields, so every read path — get-by-id, list, search — surfaces state
(AC5).

**Correction found during code review**: both `ConversationListItem` and
`ConversationResponse` are plain `BaseModel` subclasses (no `alias_generator`) — every
existing field on them, including `is_workflow_conversation`/`conversation_id`, already
serializes as snake_case, and always has. `is_finished`/`finished_at` follow that same
existing convention and serialize as-is (`is_finished`/`finished_at`), **not**
`isFinished`/`finishedAt`. Only the new finish-endpoint DTOs
(`ConversationFinishResult`/`ConversationFinishBulkRequest`/`ConversationFinishBulkResponse`)
extend `ConfiguredModel` and get the camelCase alias. This means the field is
`isFinished`/`finishedAt` when returned by `POST .../finish`, but `is_finished`/
`finished_at` when returned by get/list/search — an inconsistency between endpoint
families for conceptually the same field. Retrofitting `ConversationListItem`/
`ConversationResponse` to `ConfiguredModel` was considered and rejected: it would
change the wire format of every other existing field on those two DTOs (assistant_ids,
conversation_id, etc.), a breaking change for current consumers (`codemie-ui` and any
other client) that this ticket does not intend to make. The query parameters on
`GET /admin/conversations` (`isFinished`, `startedAt`, `perPage`) are unaffected by
this — they're explicit `Query(..., alias=...)` declarations, independent of the
response body's field casing.

`_build_filter_sql`'s `allowed_filter_columns` gains `is_finished` so it can be used as
the user list-endpoint query filter (`isFinished=true|false`) (AC6, resolves the
ticket's own "do we need `/stale`?" question raised and resolved in ticket comments —
no, filter the existing list).

**Correction found during planning**: no admin-scoped, cross-user, paginated
conversation list endpoint exists today — `admin.py`'s only conversation route is
`/admin/users/{user_id}/conversations/{conversation_id}` (single conversation, one
user). The ticket's own scheduler example (`GET /admin/conversations?isFinished=false
&startedAt=...&page=0&per_page=100`) needs a genuinely new route, not a filter param
on something existing. Confirmed with stakeholder to build it as part of this ticket —
see the new `GET /admin/conversations` endpoint below, since AC6/AC9 (finding
unfinished conversations across all users, at scale) aren't satisfiable without it.

## APIs

### `POST /conversations/{conversation_id}/finish` (user)

In `src/codemie/rest_api/routers/conversation.py`. Owner-only
(`Ability(user).can(Action.WRITE, conversation)`, same check `upsert_conversation_history`
already uses). Idempotent: if already finished, returns 200 with the existing
`finished_at` rather than erroring. 404 if the conversation doesn't exist, 403 if not
owner.

### `POST /admin/conversations/{conversation_id}/finish` (admin)

In `src/codemie/rest_api/routers/admin.py`. The router already applies
`Depends(admin_access_only)` at the router level, so no per-route ownership check is
needed. Same idempotent/404 behavior as the user endpoint, no ownership requirement.

### `POST /admin/conversations/finish-bulk` (admin)

Same router. Request:

```python
class ConversationFinishBulkRequest(BaseModel):
    conversation_ids: list[str] = Field(min_length=1, max_length=500)
```

Response, modeled on the existing `BulkAssignmentResultItem` pattern
(`rest_api/routers/projects.py:329`):

```python
class ConversationFinishResultItem(BaseModel):
    conversation_id: str
    is_finished: bool
    already_finished: bool
    finished_at: Optional[datetime]
    error: Optional[str] = None  # e.g. "not_found"

class ConversationFinishBulkResponse(BaseModel):
    total: int
    results: list[ConversationFinishResultItem]
```

**Not all-or-nothing** (unlike `bulk_assign_users_to_project`): a missing ID doesn't
fail the whole batch — the scheduler's use case is "close whatever's closeable, tell
me what wasn't." Each ID is handled independently:
- not found → `error: "not_found"`, `is_finished: false`
- already finished → `already_finished: true, is_finished: true` (still a success)
- fresh finish → `is_finished: true, already_finished: false`

This satisfies AC3 (per-ID success/error reporting) and the ticket's own sample
response shape.

### `GET /admin/conversations` (admin) — new endpoint

Paginated (`page`/`per_page`, reusing `DEFAULT_PAGE` / `DEFAULT_CONVERSATIONS_PER_PAGE`
/ `MAX_CONVERSATIONS_PER_PAGE` from `service/constants.py`), filterable by `isFinished`
(bool) and `startedAt` (date lower bound), admin-only via the router-level dependency.
Returns `PaginatedListResponse[ConversationListItem]` — the existing generic
pagination wrapper in `rest_api/models/base.py`, already used by
`activity_events_router.py` / `skill_events.py`, not a new response shape. Backed by a
new `Conversation.get_all_conversations_admin(...)` classmethod that queries across
all users (no `user_id` filter, unlike `get_user_conversations`) and orders by `date`,
which the new partial index directly serves for the scheduler's `isFinished=false`
case.

## Enforcement (HTTP 409 on finished conversations)

**Scope decision**: enforcement blocks **message-content** mutation only — chat-send
and history-editing operations. It does **not** block metadata operations (pin, rename,
move folder, share, submit/remove feedback rating) — those remain allowed on a finished
conversation. This follows AC4's literal wording ("message-append/modify APIs") over the
Implementation Plan's looser "all mutation actions except deletion" framing, since AC4
is what verification will actually test against.

A single guard, added as a `Conversation` instance method:

```python
def assert_mutable(self) -> None:
    if self.is_finished:
        raise ExtendedHTTPException(
            code=status.HTTP_409_CONFLICT,
            message="Conversation is finished",
            details=f"This conversation was closed and can no longer receive new "
                    f"messages (id={self.id}).",
            help="Start a new conversation to continue.",
        )
```

**Wording note**: this text can reach a real end user, not just an API log — the
assistant chat-send path (call site #1 below) is used interactively by `codemie-ui`,
so if an admin/scheduler finishes a conversation a human still has open, this 409
`details` text is what they'll actually see on their next "send." It's phrased to not
imply the user did something wrong (they didn't decide to finish it), without claiming
a cause this backend ticket doesn't know (who/why finished it is a UI-layer concern,
see the Human-facing UX note below).

Called as the first line, right after loading the conversation and before any
mutation, at the 5 identified content-mutation call sites:

1. `ConversationService.upsert_chat_history` (`service/conversation_service.py:208`) —
   assistant chat send. Covers the main interactive path users use to continue a
   conversation.
2. `ConversationService.upsert_conversation_with_history` (`service/conversation_service.py:289`) —
   `PUT /conversations/{id}/history` bulk history import.
3. `ConversationService.update_conversation_ai_message` (`service/conversation_service.py:855`) —
   AI message edit.
4. `WorkflowService.create_workflow_execution` (`service/workflow_service.py:176`),
   chat-execution branch — workflow chat send (the workflow-conversation equivalent of #1).
5. `WorkflowService.append_user_message_on_resume` (`service/workflow_service.py:456`) —
   workflow chat resume (interactive workflow continuation).

No locking is added — the ticket's own Implementation Plan accepts minor race
conditions between a concurrent append and finish.

**Explicitly out of scope for 409**: `ConversationService.add_feedback` /
`remove_feedback` (rate/unrate a message — metadata on the message, not content),
pin/rename/folder/share endpoints, and `delete_conversation_by_id` (explicitly excluded
by the ticket).

### Human-facing UX (out of scope for this ticket, flagged follow-on)

This ticket is a backend platform capability only (per the ticket's own Business Need:
"a platform capability, that will be used from their own UI applications") — it does
not design or build how a human is told their conversation was finished. That said,
the person continuing a chat doesn't decide when it finishes — an admin or external
scheduler can finish it without their knowledge, which is the entire point of the
feature (stopping continued use of a stale chat). `codemie-ui` itself is a consumer of
the assistant chat-send path this ticket guards, so a real user can hit the 409 above
with no other explanation in the product UI. Recommend a follow-on `codemie-ui` ticket
to surface `isFinished`/`finishedAt` in the chat UI proactively (e.g. a banner or
disabled input) rather than relying on the human to first attempt to send a message
and read a raw API error.

## Testing

Extends existing suites, no new test infrastructure:

- `tests/codemie/rest_api/models/test_conversation_model.py` — `is_finished`/`finished_at`
  defaults, `assert_mutable()` behavior.
- `tests/codemie/rest_api/routers/test_conversation.py` — user finish endpoint
  (idempotency, 403 non-owner, 404 missing).
- New/extended admin router tests — admin finish + finish-bulk endpoints (403
  non-admin, per-ID results, not-found handling, idempotency), and the new
  `GET /admin/conversations` list endpoint (isFinished filter, pagination, 403
  non-admin).
- `tests/codemie/service/test_conversation_service.py` — 409 on each of the 3
  `ConversationService` guarded methods.
- `tests/codemie/service/test_workflow_service.py` (or equivalent) — 409 on the 2
  `WorkflowService` guarded methods.
- `tests/codemie/core/test_ability_conversation.py` — owner vs. admin permission
  checks on the new endpoints.

## Open items resolved during brainstorming

- Auto-expiry is purely reactive/scheduler-driven — no server-side timer.
- Chat-send (both assistant and workflow) is in scope for 409 enforcement, not just
  the history-import endpoints.
- Enforcement scope is message-content only, not the broader "all mutations except
  deletion" reading of the Implementation Plan.
- Partial index `(date) WHERE is_finished = false` confirmed safe for interactive
  chat-write performance based on static analysis of which columns each write path
  touches, with the caveat that page-level HOT eligibility (fillfactor, real tuple
  sizes) hasn't been verified against a live database profile.

## Open items resolved during code review

- `ConversationListItem`/`ConversationResponse` stay snake_case (see the "Data model"
  correction above) — the camelCase claim in the original spec draft was inaccurate;
  keeping the existing wire format avoids a breaking change to current consumers.
- The migration backfills `is_finished` via `server_default=sa.false()` (not just a
  Python-side ORM default), so pre-existing rows are queryable by the scheduler's
  `isFinished=false` filter — the original draft's plain `default=False` would have
  left them `NULL` and invisible to that filter.
- The accepted "minor race conditions between a concurrent append and finish" is
  narrower than it first reads: `Conversation.update()`'s `session.merge()` could
  otherwise silently revert a finish that committed between an in-flight mutation's
  load and its persist call. All 5 guarded write paths now re-check `is_finished`
  against the DB (not the in-memory value) immediately before persisting, closing
  that specific failure mode while still accepting the spec's originally-scoped,
  looser append/finish ordering race.
