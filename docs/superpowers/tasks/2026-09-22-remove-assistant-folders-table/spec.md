# Spec: Remove assistant_folders Table — Derived/Stateless Implementation

**Branch**: EPMCDME-13270
**Date**: 2026-09-22
**Status**: Delivered

> This spec was rewritten after implementation. The original version specified a `DROP TABLE`
> migration layered on top of the `CREATE`; that is not what shipped. See **Approach** for why,
> and **Superseded decisions** for the record of what changed.

---

## Problem

The `assistant_folders` Postgres table was a redundant persistence layer. Every row it stored is
derivable from `Conversation.initial_assistant_id` by a GROUP BY, and the original migration's own
backfill SELECT proved the derivation. It cost a write path (three `ensure_registered` call sites),
a lock strategy (`SELECT FOR UPDATE` on a registration row), and a second source of truth that had
to stay in sync with `conversations`.

The reviewer put it plainly on the MR: a UI redesign should not require a new table and migrations.

---

## Approach

The table is removed from the branch entirely rather than created and then dropped.

`assistant_folders` had never been deployed to any database — it existed only in this unmerged
branch. A `CREATE` followed by a compensating `DROP` would have left both migrations in the
permanent history for a net effect of zero, and left the reviewer reading a diff that still adds a
table. Deleting the migration outright leaves the branch as if the table had never been proposed.

`AssistantFolder` (ORM class) is replaced by a derived query against `Conversation`. The Pydantic
response models (`AssistantFolderListItem`, `AssistantFolderDeleteResponse`) are decoupled from the
ORM and unchanged. Both `/v1/assistant-folders` endpoints keep their URLs, because codemie-ui calls
them — see **Endpoint retention**.

---

## Scope

### Migration layer

Seven migration files existed on this branch; one ships.

Deleted:

| Revision | File | Why it goes |
|---|---|---|
| `v3w4x5y6a7b8` | `add_assistant_folders` | creates the table |
| `w2x3y4z5a6b7` | `drop_assistant_folders` | compensating drop, unnecessary once the create is gone |
| `5bba1414c0ca` | `merge_assistant_folders_and_chargeback_` | merge head created by the table's branch |
| `b77bc0702dea` | `merge_main_into_epmcdme_13270` | same |
| `b873650c15c9` | `merge_epmcdme_13270_and_project_` | same |
| `8bc166e53346` | `merge_epmcdme_13270_and_litellm_spend_` | same |

The four merge migrations existed only to reconcile alembic heads that the table's migration had
created. With it gone they have nothing to merge.

Retained: `u2v3w4x5y6a7_add_import_source_to_conversations`, with `down_revision` re-pointed from
`9b9b4c585e54` to `d8e4f1a2b6c3` — main's single head. Re-pointing is safe precisely because no
database has applied this revision yet.

Result: the branch adds exactly one migration and resolves to exactly one alembic head, the same
state as branching from main today and adding one column.

### Model layer

The `AssistantFolder` SQLModel table class and its four classmethods (`ensure_registered`,
`get_by_user`, `delete_registration`, `delete_by_user`) are removed. `AssistantFolderListItem` and
`AssistantFolderDeleteResponse` stay in `assistant_folder.py` unchanged — they have no ORM
dependency.

### Service layer

**`get_assistant_folders`** queries `Conversation` for distinct `initial_assistant_id`, filtering
`import_source IS NULL` and `is_workflow_conversation` NULL-or-false. Those filters mirror the
original backfill SQL and the existing delete logic. The distinct ids go to `Assistant.get_by_ids`
to resolve names and icon URLs.

**`delete_assistant_folder`** takes `(user, assistant_id, remove_conversations: bool)`. With
`remove_conversations=False` it returns immediately with an empty response — a derived folder
cannot be deleted independently of its conversations. With `True` it runs the same
conversation-delete transaction as before, filtering on `(user_id, initial_assistant_id, folder
NULL-or-empty, import_source IS NULL, not pinned, not workflow)`, preserving the cascade deletes
for `WorkflowExecution`, `SharedConversation` and `ConversationMetrics`. The `SELECT FOR UPDATE` on
the registration row is gone; row-level locks on `Conversation` suffice.

The three `ensure_registered` call sites are removed with no replacement — a derived query reflects
state automatically.

### Router and core models

`DELETE /v1/assistant-folders/{assistant_id}` takes `remove_conversations: bool = False` in place of
`action: AssistantFolderDeleteAction`. `GET /v1/assistant-folders` is unchanged.
`DELETE /conversations` no longer calls `AssistantFolder.delete_by_user`. The
`AssistantFolderDeleteAction` enum is deleted.

### Endpoint retention

Both endpoints were removed at one point in this task on the reasoning that the sidebar could group
client-side, since `GET /conversations` already returns `initial_assistant_id`, `assistant_icon` and
`assistant_names` per chat. Checking codemie-ui on its own `EPMCDME-13270` branch disproved that:

- `GET v1/assistant-folders` — `src/store/chats.ts:667`
- `DELETE v1/assistant-folders/{id}` — `src/store/chats.ts:118`
- `assistantFolders` drives both `unifiedChatSidebarViewModel.ts` and
  `focusedChatSidebarViewModel.ts`, the two Organize-by modes in the story

Both endpoints were restored. The lesson is recorded here so the next reader does not re-derive it:
the endpoints are stateless now, but they are not unused.

---

## Acceptance Criteria

- The branch adds exactly one migration (`u2v3w4x5y6a7`) and resolves to exactly one alembic head.
- `assistant_folders` appears nowhere in the diff against main — no create, no drop, no merge.
- `GET /v1/assistant-folders` returns the same list the table-backed endpoint returned, derived live
  from `Conversation.initial_assistant_id`.
- `DELETE /v1/assistant-folders/{assistant_id}?remove_conversations=true` deletes exactly the
  conversations the old `delete_folder_and_chats` action deleted, cascades included.
- `...?remove_conversations=false` returns 200 with `deleted_conversation_ids=[]` and
  `folder_deleted=false`; no database writes.
- `DELETE /conversations` completes after the `AssistantFolder.delete_by_user` call is removed.
- No reference to `AssistantFolder`, `ensure_registered`, `delete_by_user`, `delete_registration`,
  `get_by_user` or `AssistantFolderDeleteAction` remains in non-test application code.
- `make ruff` passes.

All of the above hold as delivered.

---

## Known open issue

`DELETE /v1/assistant-folders/{assistant_id}` and codemie-ui disagree on the query parameter.

The backend accepts `remove_conversations: bool` (default `False`). codemie-ui sends
`?action=delete_chats_only|delete_folder_and_chats` (`store/chats.ts:113`), written against the
contract this task replaced. FastAPI ignores the unknown parameter, so the endpoint takes the
default and returns 200 with an empty `deleted_conversation_ids`. The UI's own fallback — deleting
conversations one by one — fires only on HTTP 404, so it does not engage. The user sees no error and
nothing is deleted.

Neither repository's `main` has this endpoint, so nothing is broken in production; the defect ships
only if both MRs merge unchanged. Tracked for a fix on the codemie-ui side, where sending
`remove_conversations` is the smaller change and the honest contract: with a derived folder,
"delete the folder" and "delete its chats" are the same operation, and whether an emptied folder
stays visible is a UI decision.

---

## Non-goals

- Dismissed-folder state (JSONB on `user_preferences`) — separate ticket if the product needs it.
- A downgrade path — nothing is dropped, so there is nothing to roll back.
- Changes to `import_source` handling.
- URL changes to either `/v1/assistant-folders` endpoint.
- Changes to `DELETE /conversations/folder/{folder:path}`.
- Changes to `UserPreferences`, `ConversationFolder` or `WorkflowExecution` beyond existing cascades.

---

## Key Decisions

- Delete the create/drop/merge migrations outright (over shipping `CREATE` + `DROP`) — the table was
  never deployed, so the branch can simply not propose it.
- Re-point `add_import_source` at main's head (over adding a seventh merge migration) — leaves one
  migration and one head.
- Keep both `/v1/assistant-folders` endpoints (over deleting them as redundant) — codemie-ui calls
  both, verified in that repository.
- `remove_conversations=false` is a no-op (over treating it as `true` or raising 400).
- Pydantic response models stay in `assistant_folder.py` (over moving them) — avoids import churn.

---

## Superseded decisions

Recorded so the earlier artifacts and review history stay readable:

| Original decision | Replaced by |
|---|---|
| Add a `DROP TABLE` migration with `down_revision = "5bba1414c0ca"` | Delete the create migration and all four merge migrations; no drop is needed |
| `5bba1414c0ca` is the alembic head any new migration must chain off | That revision no longer exists; the head is `u2v3w4x5y6a7`, chained off main's `d8e4f1a2b6c3` |
| The drop migration is the task's central deliverable | The central deliverable is the absence of the table from the diff |
