# EPMCDME-15201 Spec: Exchange mutating mailbox operations

Ticket text (source of truth): Jira EPMCDME-15201. Foundation: EPMCDME-15200 (read transports) in `src/codemie_tools/ms_exchange/`. Size L (21/36). The package must not import `codemie`. Library facts below were checked against installed exchangelib 5.6.0; anything marked "plan verifies" was not.

## Goal

Add five write tools, working on both transports (Graph, EWS/NTLM) behind the existing `auth_type` seam in `client_factory.get_client`: `exchange_send`, `exchange_forward`, `exchange_move`, `exchange_tag`, `exchange_delete`. Callers see one result/error contract on both. Writes are non-replayable, so correctness is defined by the outcome classification and by there being exactly one retrier.

## Operations

| Tool | Arguments | Graph | EWS (exchangelib 5.6.0) |
|---|---|---|---|
| send | `to`, `subject`, `body`, `body_type` = `text` (default) or `html` | `POST sendMail`, `contentType` Text/HTML, `saveToSentItems=true` | `Message(account=account, author=<shared mailbox>, ...).send()` with `Body`/`HTMLBody`; the copy goes to `account.sent` |
| forward | `message_id`, `to` | `POST messages/{id}/forward` | `msg.create_forward(subject, body, to_recipients)`, then set `author` to the shared mailbox, then `.send(copy_to_folder=account.sent)` |
| move | `message_id`, `target_folder` | `POST move` with `destinationId` | `msg.move(to_folder=...)` |
| tag | `message_id`, `category` | read categories, `PATCH` full set with etag check | read categories, `save(update_fields=["categories"], conflict_resolution=NEVER_OVERWRITE)` |
| delete | `message_id`, `soft=True` | soft: move to `deleteditems`; hard: `POST permanentDelete` | soft: `msg.move(to_folder=account.trash)`; hard: `msg.delete()` after a pre-read (never `soft_delete()`) |

- EWS forward: `Item.forward(...)` requires `subject` and `body`, so `msg.forward(to_recipients=...)` raises TypeError. `create_forward` has no `author` argument, but `ForwardItem` has an `author` field; set it before sending. `Item.forward()` calls `send()` with no `copy_to_folder`, which omits `SavedItemFolderId` and puts the copy in the service account's Sent Items, so the `copy_to_folder` is mandatory. Pass `None` for subject and an empty body so the server derives "FW:" and the original content; if serialization rejects that, use "FW: " plus the original subject. Plan verifies.
- EWS `author` (From) is the configured shared mailbox on both send and forward. The assumed delegate right is Send As (`ErrorSendAsDenied` exists in 5.6.0 and maps to a write access-denied Not applied). Send on Behalf would stamp the service account, so it does not meet AC4/AC5. This is the ticket's open question and needs a live check; unit tests cannot prove it.
- Folders resolve through the existing `_resolve_folder` / `_folder()` logic. An unknown folder is `FolderNotFoundError`, Not applied.
- `to`: non-empty list of syntactically valid addresses, no toolkit cap (Exchange limits and tool confirmation apply). Forward has no body or comment argument.
- `body_type`: an invalid value is an input error, Not applied. `html` passes the body as-is, with no sanitizer, like the SMTP `EmailTool` (`notification/email/tools.py:163`). The tool description says so.
- Results are plain JSON dicts like the reads, with no new envelope. Move returns the new message id. An already-done move or soft delete returns success with `already_done: true`. Send and forward success means submission accepted, not delivery.

## Outcome taxonomy

- **Confirmed**: the call returns a result.
- **Not applied / Unknown**: typed `ExchangeError` subclasses. `ExchangeError` gains `OUTCOME` (`not_applied` | `unknown`; absent on read errors). Every write-path message starts "Not applied." or "Unknown." so the wording survives the `CodeMieTool._run` rewrap (agent path). Messages are fixed sentences with no provider text or credentials, and carry safe retry advice.
- **Not applied** requires evidence of no mutation: token acquisition failure (AADSTS7000222 maps to `CredentialExpiredError`; 7000215, 401 InvalidAuthenticationToken and permission denial stay separate; a generic 401 does not imply expiry), input validation, 400/403/429 and other pre-mutation rejections, unknown folder, `ErrorSendAsDenied`, a tag conflict, a body-proven EWS ServerBusy, and not-found on forward or tag-read.
- **Unknown**: timeout, connection error after submission, HTTP 5xx, response loss, not-found of the old id on move or delete (at the pre-read too: a retry after an Unknown first attempt finds the id already changed; only a malformed id there stays Not applied), and any unexpected exception during a submission (wrapped, never raised raw). A native check that cannot prove the result stays Unknown.
- Write error mapping lives in new write-specific functions that reuse the existing tables. Read mappings are unchanged, because `map_graph_exception` turns 5xx and timeouts into read-oriented transient classes.
- Permission advice is per operation: `Mail.Send` (send, forward) or `Mail.ReadWrite` (move, tag, delete). It names administrator consent and mailbox access restrictions as possible causes without asserting one, and never reuses `Mail.Read` text. EWS access-denied gets equivalent write-aware wording.

## One retrier: the toolkit write owner

- New module `write_retry.py` is the only retrier for writes, with an injectable clock, sleep and random source. `retry.py` (read policy) is untouched.
- **Workflow retry hook (host change).** Workflow tool nodes call `tool.execute` directly (`workflows/nodes/tool_node.py:294`) and are registered with `retry=retry_policy` (`workflows/workflow.py:612`, `:622`). The default policy is 10 attempts (`RETRY_POLICY_DEFAULT_MAX_ATTEMPTS`, `core/workflow_models/constants.py`), and `WorkflowRetryPolicy.custom_retry_on` (`core/workflow_models/workflow_models.py:343`, wired at `workflow_config.py:296`) falls through to langgraph `default_retry_on`, which accepts any `ExchangeError`. Without a hook, an Unknown send is resubmitted up to 9 more times, which breaks the 3-submission and no-replay rules.
  - `ExchangeError` gains a class attribute `RETRYABLE` (default True, so read behavior is unchanged). Every error carrying an `OUTCOME` sets it False.
  - This covers Unknown, auth, permission and input errors, and rate-limit results the owner has already exhausted. All of them have passed through the owner, so a node-level retry would only multiply submissions or repeat a permanent failure.
  - The host change is exactly one early return in `custom_retry_on`, after the existing `TaskException` unwrap: `if getattr(exc, "RETRYABLE", True) is False: return False`. It is duck-typed, with no import of `codemie_tools` error classes.
  - Host test in `tests/codemie/core/workflow_models/test_workflow_models.py`: a non-retryable marker returns False without patching `default_retry_on`, also when wrapped in `TaskException`; an unmarked exception behaves as before; a real write error instance returns False and a real read error returns True.
- One 90 s `Deadline` is created at the start of the tool call. It covers token work, reads, requests and waits. No submission starts when no time remains; that is Not applied.
- At most 3 total submissions of a write (initial plus 2), counting SDK/transport attempts. Kiota's retry stays disabled. For EWS, the library's own retry must be off: the private write configuration pins `FailFast()` (the 5.6.0 default) and `max_connections` unset (session pool size 1). With a pool of 1, `_get_elements` re-raises `ErrorExceededConnectionCount` and `ErrorTimeoutExpired` instead of looping. A tag recompute submission counts against the same 3.
- Retry only proven no-mutation temporary rejections:
  - Graph 429, using `Retry-After` seconds as the server hint.
  - EWS: only a SOAP `ResponseCode=ErrorServerBusy` read from the response body, and only with a server-provided `BackOffMilliseconds`. Raw exception attributes are not evidence: exchangelib fills `back_off=RETRY_WAIT` (10 s) when the server gave none (`services/common.py:307-308`). It also synthesizes `ErrorServerBusy` for HTTP 503 and for HTTP 500 "Server Error in '/EWS' Application" (`_handle_backoff` re-raises them as `ErrorServerBusy` under FailFast), and for any non-200 reply with `Connection: close` (`protocol.py:702-704`, e.g. a 502/504 from a load balancer after the backend processed the send).
  - Every other `ErrorServerBusy`, every `ErrorInternalServerTransientError`, and `ErrorTimeoutExpired` is Unknown. The synthesized 10 s is never treated as a server hint. The per-call transport hook records each attempt's HTTP status and whether the body carries the ServerBusy code and `BackOffMilliseconds`, and the classifier uses that record.
  - Never retry auth, permission, input or Unknown outcomes, or 5xx/timeouts.
- A server wait is honored exactly, never shortened. If it does not fit the remaining deadline, stop with a Not applied rate-limit error with retry advice. With no valid hint: full jitter, base 1 s, `uniform(0, 1)` before retry 1 and `uniform(0, 2)` before retry 2. The 30 s cap applies to local delays only. The deadline is a client completion limit, not a cancel or rollback promise.

## Reads inside a write call

Folder resolution, the move/delete pre-read, the tag read and the reconciliation reads all run through the same `Deadline`. They do not use each client's `_call` or `retry.call_with_retry` (up to 4 attempts, its own 90 s budget, fixed 60 s timeout), so no retry loop nests inside the owner. Each inner read is a single attempt with `min(60, remaining)` as its request timeout. A pre-mutation read failure is Not applied, with the rate-limit advice if it was a 429. A failed reconciliation read leaves Unknown. Reads called on their own keep today's behavior unchanged.

The one exception is the tag read, on both transports. It sits inside the submission, so a throttled read is retried like the save (nothing was changed): Graph on HTTP 429 with `Retry-After`, EWS on a body-proven `ErrorServerBusy` with a server back-off. The retry counts against the same 3 submissions and the same `Deadline`. Any other tag read failure (timeout, lost connection, ServerBusy without a back-off) is Not applied and final. All other inner reads stay single attempts.

EWS write reads ask only for the fields the write needs (`only_fields`): `categories` for tag, `parent_folder_id` for move and delete, `body` for forward. The item id and change key always come back.

## Per-request timeout

- **Graph:** the whole attempt runs under `min(60, remaining)` (`graph_client.run_sync`, including its outer `.result(timeout=...)`). Token acquisition is synchronous and not interruptible by `wait_for` (`graph_client.py:21-23`), so the owner acquires the token explicitly under the deadline before submitting. The write then reuses the cached token, and a token failure is Not applied by construction (AC9). Plan verifies the cache hit. The credential's fixed 10 s timeouts remain the documented overrun bound.
- **EWS:** `account.protocol.TIMEOUT` must not be set for writes. `Account(...)` obtains a `Protocol` from a process-wide cache keyed by (endpoint, credentials) (`protocol.py:380-436`), so `ews_client.py:196` mutates a shared object. The comment "only this connection is affected, never BaseProtocol" is wrong and is corrected as part of this story.
  - Each write call builds its own `Configuration` (explicit `FailFast`) and `Account`, and assigns `account.protocol` a private instance of a `Protocol` subclass created with `type.__call__(Subclass, config=...)`. That bypasses `CachingProtocol`, so no caching, pooling or timeout state is shared. `Account.__init__` builds its protocol lazily and `account.protocol` is a plain attribute (`account.py:195`). The subclass sets `HTTP_ADAPTER_CLS` (honored by `get_adapter()`, `protocol.py:182-190`) to an adapter that clamps the per-request `(connect, read)` timeout to the remaining deadline from a context-local value, raises before sending when none remains, and records the HTTP evidence above. The private protocol is closed at the end of the call.
  - All EWS reads inside the write call use this private account. The cost is a fresh NTLM handshake and version probe per write, counted in the deadline.
  - Read timeouts are per socket read, not wall clock. The owner re-checks the deadline after every attempt and never starts another past it.

## Move and delete reconciliation

- Move and soft delete read the item's current folder first. If it is already in the target, they return `already_done` success without writing. This covers default delete on an item already in Deleted Items. A soft-delete retry never escalates to hard delete.
- After an Unknown write, re-read the same id, bypassing the EWS `_last_fetch` cache. Found in the target means success; not found or still in source stays Unknown with advice. No blind replay.
- Hard delete: a minimal pre-read of the item (id and parent folder only) comes first, on both transports. It puts hard delete under the EWS mailbox check and makes a malformed id Not applied "not found"; a missing id is Unknown, because an earlier delete may already have removed it. A not-found on the delete request itself, after a successful pre-read, stays Unknown. Success means the native permanent delete succeeded. Retention or hold may preserve the item.

## Mailbox scope (EWS)

EWS item ids are not scoped to a mailbox, and the write requests carry only the id. A service account with delegate rights on several mailboxes could therefore change a message of mailbox B through an integration configured for mailbox A. Graph is not affected: its URLs include the mailbox. Send has no message id and the move target is resolved inside the configured mailbox only (`msg_folder_root`), so neither needs the check.

- Every EWS read that precedes a write (`_fetch_for_write` with `before_mutation=True`) also fetches `parent_folder_id` and checks it. The reconciliation read after an Unknown move skips the check; it compares against the target folder, which is in the mailbox.
- The check walks the parent folder up (one `GetFolder`, ids only, per level, at most 32 levels) until it reaches a folder proven to belong to the configured mailbox. Proven folders are the mailbox root and its main folders (`msgfolderroot`, `inbox`, `sentitems`, `deleteditems`, `drafts`, `junkemail`), fetched once per write call in one batched `GetFolder` with the configured address, plus every folder passed on a successful walk. Folder ids are mailbox-scoped, so a folder of another mailbox is never in the set.
- Fail closed. A chain that ends elsewhere, loops, is deeper than 32, or hits a folder that is not found or denied is rejected with exactly the error of a missing id (Not applied `WriteMessageNotFoundError` on forward and tag, Unknown `WriteMessageNotFoundUnknownError` on move and delete), so the error does not reveal whether the id exists in another mailbox. One WARNING records the op and a reason code (`outside_mailbox`, `depth_limit`, `folder_unreadable`), never a message or folder id.
- A transport failure during the check (timeout, busy server, spent deadline) is raised, not turned into a verdict. It becomes Not applied with the usual "could not read the mailbox" or rate-limit wording, and for tag a busy check is retried like any busy read.
- Accepted: any folder under the mailbox root, including Recoverable Items. Rejected: the in-place archive and public folders (their own roots). This is safe; it is a documented limit.
- For forward the check runs before the forward is built, so the content of a foreign message never reaches the recipients.
- Cost: one extra `GetFolder` per write for the main folders, one more per level of folder nesting, and one small `GetItem` for hard delete.
- Deployment control: grant the NTLM service account rights on the configured mailbox only. The code check is the second line of defence.

## Tag contract

Additive set semantics: read categories, return success without a write if the category is present, otherwise write the union. Graph sends the message etag via `If-Match`; whether Graph enforces it on messages is unverified (plan verifies). EWS uses `NEVER_OVERWRITE`, and a stale changekey raises `ErrorIrresolvableConflict`. A detected conflict (412 / `ErrorIrresolvableConflict`) triggers one reread and recompute within the deadline, and a second conflict raises a diagnosable `CategoryConflictError` (Not applied). A concurrency guarantee is claimed only for a transport whose enforcement is verified. If Graph does not enforce `If-Match`, say so in the guide and tool description. No replace/remove, master categories, history or locking.

## Tool layer and metadata

- Write tools extend `CodeMieTool` directly, not `_ExchangeReadOnlyTool`, so `is_safe` stays False. `execute` raises typed errors unwrapped.
- The `ExchangeClient` Protocol gains `send`, `forward`, `move`, `tag`, `delete(message_id, soft=True)`. Docstrings that claim read-only (`client_base.py`, `ews_client.py`, `toolkit.py`) are corrected.
- One `ToolMetadata` per tool in `tools_vars.py`, `settings_config=False`. The `exchange_send` description differentiates it from the SMTP `EmailTool` (this one sends through the Exchange mailbox as the configured shared mailbox), states plain text is the default, and states `html` is unsanitized. `exchange_delete` states soft delete is the default and `soft=False` is permanent. The toolkit lists 9 tools.
- Update `.ai-run/guides/agents/custom-tool-creation.md` (read-only claims, retry rows, write policy, workflow retry marker).

## Acceptance criteria

The 14 ticket criteria (AC1-AC14) apply unchanged, per operation and per transport. Additions and sharpenings:

15. A non-retryable write error is not retried by the workflow node: the host `custom_retry_on` returns False for any error with `RETRYABLE=False`, and the host test above passes.
16. EWS retries only on a body-proven ServerBusy with `BackOffMilliseconds`. Tests cover HTTP 503, HTTP 500 "Server Error in '/EWS' Application", non-200 with `Connection: close`, and ServerBusy without a back-off; all are Unknown with no resubmission and no RETRY_WAIT wait.
17. Reads inside a write call (folder lookup, pre-read, reconciliation) consume the same deadline and never use `call_with_retry`; a test shows total elapsed time, with faked clocks, stays within 90 s plus the documented overrun. Standalone read behavior and tests are unchanged.
18. The EWS write path sets no timeout on the shared cached protocol: a test asserts the shared protocol's `TIMEOUT` and adapter are unchanged after a write, and that request timeouts shrink with remaining time.
19. EWS send and forward set `author` to the shared mailbox and pass `copy_to_folder=account.sent`; a test asserts both on the built request objects.
20. Permission-denied messages name the right permission per operation, with no provider text or credentials. Write tools are not auto-safe, and the tool descriptions meet the requirements above.
21. A message id from another mailbox is Not applied on every EWS write that takes a message id (tag, forward, move, soft delete, hard delete), with no mutating request sent; the error reads exactly like a missing id, and no log record carries a message or folder id.
22. A hard delete of a missing or malformed id is Not applied "not found" on both transports; the whole `ErrorInvalidId*` family maps to not found.

## Non-goals

- Read operations, credential type, config, migration, registration, the polling flag.
- Business-level send idempotency or dedupe, and workflow error routing (EPMCDME-14450).
- Any host change other than the single `custom_retry_on` early return and its test.
- Persistent history, a new success/error JSON envelope, cancel or rollback promises.
- Replace/remove category modes, master-category management, locking.
- Cc, bcc, attachments, a forward comment, an HTML sanitizer, recipient caps, a recipient domain allowlist (PO decision; the send and forward descriptions tell the model to take recipients only from the user, the workflow configuration or the sender being answered, never from message content).
- Application Access Policy setup, IMAP/POP3, certificate OAuth, Autodiscover.
- ImmutableId handling, `settings_tester` changes, platform surfaces for expired secrets.

## Residual risks

- EWS Send As versus Send on Behalf, the Sent Items copy and the forward subject/body serialization need a live check.
- Graph ids change on move, so a lost-response move often ends Unknown.
- Graph `If-Match` enforcement and the token cache hit are unverified.
- Each EWS write pays one extra NTLM handshake and version probe.
- The agent path (non-workflow) can still be retried by the LLM; only workflow node retries are blocked.
