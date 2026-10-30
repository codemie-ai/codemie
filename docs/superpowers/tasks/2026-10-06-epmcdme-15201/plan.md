# EPMCDME-15201 Exchange write operations Implementation Plan

**Goal:** five write tools (send, forward, move, tag, delete) on Graph and EWS behind `client_factory.get_client`, with outcome-aware errors and one retry owner. Source of truth: `spec.md` (same dir) and the ticket.

**Conventions:** package `src/codemie_tools/ms_exchange/` never imports `codemie`. Tests in `tests/codemie_tools/ms_exchange/` (pytest, `patch`, hand-rolled fake provider errors, patched clock). Fixed-sentence errors only. Commit per task using the repository's existing convention. Do not touch `retry.py` or read behaviour. Library versions are the installed ones (exchangelib 5.6.0); T1 settles every "plan verifies" item before dependent tasks start.

---

### T1 - Spike: verify library facts the spec leaves open
Files: create `tests/codemie_tools/ms_exchange/test_write_library_facts.py`.
Offline tests (no network) that pin: (a) msgraph-sdk message PATCH request configuration accepts an `If-Match` header and what the installed kiota stack does with it (record whether Graph enforcement can only be live-verified); (b) `ClientSecretCredential.get_token` called twice with a mocked HTTP transport hits the network once, and `AzureIdentityAuthenticationProvider` then reuses that cached token; (c) exchangelib 5.6.0: `FailFast` is the `Configuration` default, `ErrorSendAsDenied` and `ErrorIrresolvableConflict` exist, `type.__call__(ProtocolSubclass, config=...)` bypasses `CachingProtocol`, `HTTP_ADAPTER_CLS` is honored by `get_adapter()`, `create_forward(None, "", ...)` serializes (else "FW: "+subject fallback), `ForwardItem` has `author`, `send(copy_to_folder=...)` emits `SavedItemFolderId`.
Each failed fact: stop and record the deviation in the test docstring, then adjust the dependent task (tag concurrency claim, forward fallback) before continuing.
Test-first: yes - the tests fail until each assumed fact is confirmed or the spec assumption is replaced by the observed behaviour.

### T2 - Outcome-aware errors and write mappings
Files: `errors.py` (extend), `tests/.../test_errors.py` and new `test_write_errors.py`.
- `ExchangeError` gains class attrs `OUTCOME = None` and `RETRYABLE = True` (read errors unchanged). Add bases `NotAppliedError` / `UnknownOutcomeError` (OUTCOME `not_applied` / `unknown`, `RETRYABLE = False`); their messages start "Not applied." / "Unknown." and carry retry advice.
- New fixed-sentence subclasses: `WriteRateLimitedError` (Not applied, advice to retry later), `SendAccessDeniedError` (`Mail.Send`), `WriteAccessDeniedError` (`Mail.ReadWrite`), both naming admin consent and mailbox access restrictions as possible causes without asserting one, an EWS send-as/write-denied equivalent, `CategoryConflictError`, `InvalidWriteInputError`, `WriteUnknownError` variants for timeout/5xx/connection loss. Reuse existing Not applied classes for token errors (`CredentialExpiredError` via `_AADSTS_MAP`), `FolderNotFoundError`, `MessageNotFoundError`; each gets a write-path non-retryable twin only where the existing class lacks `OUTCOME`.
- New `map_graph_write_exception(exc, op)` and `map_ews_write_exception(exc, op)` that reuse `_AADSTS_MAP`/`_GRAPH_CODE_MAP`/EWS table but never the read 5xx/timeout classes: 400/403/429/other pre-mutation rejections, 412, `ErrorSendAsDenied`, `ErrorIrresolvableConflict` -> Not applied; timeout, connection error, 5xx, any unexpected exception -> Unknown; old-id not-found on move/delete -> Unknown. 401 generic must not map to expired credential. `map_graph_exception`/`map_ews_exception` untouched.
Test-first: yes - a table test per mapping (provider exception -> class, OUTCOME, message prefix, permission name per op, no provider text in message) fails until the functions exist; a read-error test asserts `RETRYABLE is True` and `OUTCOME is None`.

### T3 - Host change: workflow retry hook
Files: `src/codemie/core/workflow_models/workflow_models.py:343-361` (one early return in `custom_retry_on`, after the `TaskException` unwrap: `if getattr(exc, "RETRYABLE", True) is False: return False`; duck-typed, no `codemie_tools` error import), `tests/codemie/core/workflow_models/test_workflow_models.py`.
No other host change.
Test-first: yes - a marker exception with `RETRYABLE=False` returns False without patching `default_retry_on`, also wrapped in `TaskException`; an unmarked exception behaves as before; a real write error instance returns False and a real read `ExchangeError` returns True.

### T4 - Write retry owner (`write_retry.py`, new)
Files: create `write_retry.py`, `tests/.../test_write_retry.py`.
Only retrier for writes; independent of `retry.py`. Injectable clock, sleep, random. Must implement: one `Deadline` (90 s) created at tool-call start with `remaining()` and `expired()`; at most 3 submissions; retry only when the transport classifier returns a proven-no-mutation temporary rejection; server hint honored exactly (never shortened, not capped by 30 s); if the hint exceeds remaining time stop with `WriteRateLimitedError`; no hint -> `uniform(0, 1)` before retry 1, `uniform(0, 2)` before retry 2, local delay capped at 30 s; no submission starts without remaining time (Not applied); re-check deadline after every attempt; unexpected exceptions during a submission are wrapped Unknown, never raised raw.
```python
class Deadline:  # new
    def __init__(self, seconds: float = 90.0, clock: Callable[[], float] = time.monotonic): ...
    def remaining(self) -> float: ...

Classify = Callable[[BaseException], "Retry | Stop"]  # Retry(hint_seconds | None), Stop(error)
def run_write(submit, classify, deadline, *, sleep=time.sleep, rand=random.uniform): ...
```
Test-first: yes - fake clock/sleep/random tests: attempt cap is 3, no multiplication, hint 45 s waited in full, hint over remaining time stops with rate-limit result and no sleep, jitter bounds per retry, 30 s cap on local delay only, auth/permission/input and Unknown never replayed, deadline spent on prior reads shrinks waits.

### T5 - Graph write plumbing, send and forward
Files: `graph_client.py` (`run_sync` accepts a timeout; add write-call helper next to `_call`), `tests/.../test_graph_write.py`.
Write helper: create the `Deadline`, acquire the token explicitly via `self._credential.get_token` under the deadline (failure -> Not applied via `map_graph_write_exception`), then run the attempt under `min(60, remaining)` including the outer `.result(timeout=...)`, through `run_write`. Kiota retry stays off. Inner reads (folder resolve, pre-read, reconciliation) are single attempts with `min(60, remaining)`, never `_call`/`call_with_retry`; pre-mutation read failure is Not applied (429 gets rate-limit advice), failed reconciliation read stays Unknown. Add `send(to, subject, body, body_type)` (`sendMail`, Text/HTML, `saveToSentItems=true`) and `forward(message_id, to)`; validate `to` (non-empty, syntactically valid addresses) and `body_type` before any request. Honor T1 token-cache result.
Test-first: yes - patched request builders: 403 before mutation -> Not applied and fake mailbox unchanged; committed-then-timeout -> exactly one effect and Unknown with no resubmission; 429 with `Retry-After` retried within 3 submissions; AADSTS7000222 at token step -> `CredentialExpiredError`, zero Graph requests; forward not-found -> Not applied; invalid `body_type` -> input error.

### T6 - Graph move, delete and tag
Files: `graph_client.py`, `tests/.../test_graph_write.py`.
- `move`: pre-read folder; already in target -> `{"already_done": true, ...}` without write; success returns new message id; after Unknown, reconcile by re-reading the same id (found in target = success, else Unknown).
- `delete(message_id, soft=True)`: soft = move to `deleteditems` (already there -> `already_done`, never escalates to hard); hard = `permanentDelete`.
- `tag`: read categories (`If-Match` etag per T1), return success without write if present, else PATCH the union; 412 -> one reread/recompute inside the same deadline and the 3-submission budget, second conflict -> `CategoryConflictError`. If T1 shows Graph does not enforce `If-Match`, make no concurrency claim (message and docs).
Test-first: yes - per op the pre-mutation-failure and commit-then-loss cases; tag preserves Finance when adding Processed, repeat issues no PATCH, concurrent change triggers one recompute then conflict error; default delete of an item in Deleted Items issues no request and never calls permanentDelete.

### T7 - EWS private protocol and write plumbing
Files: `ews_client.py` (new write-account builder beside `build_ews_client`), `tests/.../test_ews_write.py`.
Per write call: build own `Configuration` with explicit `FailFast`, `max_connections` unset, and a private `Protocol` subclass instance created with `type.__call__` and assigned to `account.protocol`; its `HTTP_ADAPTER_CLS` adapter clamps `(connect, read)` timeout to remaining deadline from a context-local value, raises before sending when none remains, and records per attempt: HTTP status, whether the body carries `ErrorServerBusy` and `BackOffMilliseconds`. Close the protocol at call end. Classifier retries only a body-proven `ErrorServerBusy` with server `BackOffMilliseconds`; HTTP 503, HTTP 500 "Server Error in '/EWS' Application", non-200 with `Connection: close`, ServerBusy without back-off, `ErrorInternalServerTransientError`, `ErrorTimeoutExpired` -> Unknown, no resubmission, no 10 s `RETRY_WAIT`. All EWS reads in the write call use this account, single attempt, bypassing `_last_fetch`. Never set `protocol.TIMEOUT` on the shared cached protocol (do not touch `build_ews_client` read behaviour).
Test-first: yes - tests assert the shared cached protocol's `TIMEOUT` and adapter are unchanged after a write, request timeouts shrink with remaining time, and each listed status/body combination classifies as specified.

### T8 - EWS send and forward
Files: `ews_client.py`, `tests/.../test_ews_write.py`.
`send`: `Message(account, author=<shared mailbox>, ...)` with `Body`/`HTMLBody`, `.send(copy_to_folder=account.sent)`. `forward`: fetch uncached, `create_forward(None, "", to_recipients)` (or T1 fallback), set `author` to the shared mailbox, `.send(copy_to_folder=account.sent)`. Delegate Send As is the assumption; `ErrorSendAsDenied` -> write access-denied Not applied.
Test-first: yes - assert `author` and `copy_to_folder=account.sent` on the built request objects for both ops (AC19); pre-mutation failure -> Not applied with unchanged fake state; commit-then-connection-loss -> one effect, Unknown, no resubmit.

### T9 - EWS move, delete and tag
Files: `ews_client.py`, `tests/.../test_ews_write.py`.
Same reconciliation semantics as T6. Move: `msg.move(to_folder=_folder(...))`. Soft delete: `msg.move(to_folder=account.trash)` (never `soft_delete()`); hard: `msg.delete()`. Tag: `save(update_fields=["categories"], conflict_resolution=NEVER_OVERWRITE)`; `ErrorIrresolvableConflict` -> one reread/recompute, then `CategoryConflictError`; recompute counts against the 3 submissions.
Test-first: yes - per op pre-mutation failure and commit-then-loss; category preservation, repeat without save, stale changekey conflict path; default delete in Deleted Items makes no write.

### T10 - Protocol, tools, metadata, toolkit
Files: `client_base.py`, `tools.py`, `tools_vars.py`, `toolkit.py`, `tests/.../test_tools.py`, `test_toolkit.py`.
Add `send`, `forward`, `move`, `tag`, `delete(message_id, soft=True)` to the `ExchangeClient` Protocol. Five tool classes extending `CodeMieTool` directly (not `_ExchangeReadOnlyTool`; `is_safe` stays False), pydantic input models, `execute` returns `_dump(...)` and raises typed errors unwrapped. One `ToolMetadata` each (`exchange_send`, `exchange_forward`, `exchange_move`, `exchange_tag`, `exchange_delete`), `settings_config=False`. `exchange_send` description: sends through the Exchange mailbox as the configured shared mailbox (unlike SMTP `EmailTool`), plain text default, `html` unsanitized; `exchange_delete`: soft default, `soft=False` permanent; `exchange_tag`: additive, and the Graph concurrency caveat from T1 if applicable. Toolkit lists 9 tools.
Test-first: yes - toolkit exposes exactly 9 tools with write names; write tools `is_safe` False; routing by `auth_type` through a patched `get_client` for each tool; description assertions (SMTP differentiation, html unsanitized, soft default); a write error survives the `CodeMieTool._run` rewrap with its "Not applied."/"Unknown." prefix.

### T11 - Docs and comments
Files: `.ai-run/guides/agents/custom-tool-creation.md` (read-only claims incl. line 39, retry rows, write policy: one owner, 3 submissions, 90 s deadline, outcome taxonomy, private EWS protocol, workflow `RETRYABLE` marker, Graph `If-Match` finding), `ews_client.py` docstring "Only reads are issued" and the `protocol.TIMEOUT` comment at `:196` (shared cached Protocol, correct the "never BaseProtocol" claim), `client_base.py` and `toolkit.py` read-only docstrings.
Test-first: no - documentation and comment corrections only.

---

## Self-review

negative-constraints (each mapped, none violated): no `soft_delete()` and no soft-to-hard escalation (T6, T9); no replay of Unknown/auth/permission/input (T4, T7); no shortening of server waits (T4); no change to read retry (T2 leaves read mappings, T4 separate module, T7 leaves `build_ews_client`); no new JSON envelope (T10 `_dump` dicts); no provider text or `Mail.Read` advice in write errors (T2); no host change besides one early return (T3); no replace/remove tags, cc/bcc, attachments, sanitizer, settings_tester change (not planned); no concurrency claim without verification (T1, T6, T10, T11); no subagent or commit blocks.
