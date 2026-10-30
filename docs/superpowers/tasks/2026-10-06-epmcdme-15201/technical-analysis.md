# Technical Research

**Task**: exchange toolkit mailbox write graph ews
**Generated**: 2026-10-07
**Research path**: codegraph

---

## 1. Original Context

task_context: The full verbatim requirements for Jira story EPMCDME-15201 ("Exchange toolkit: mutating mailbox operations (send, forward, move, tag, delete) on both proven transports") are in /tmp/claude-501/ticket-15201.txt — Read that file completely (ignore its first two CLI log lines). It is the task_description verbatim: scope table, category/delete contracts, normalized outcomes (Confirmed / Not applied / Unknown), write retry policy and 90s deadline, 14 acceptance criteria, durable constraints, open questions.
Foundation: this branch (EPMCDME-15201_exchange-write-operations) is cut from epmcdme-15200, which already delivered the Exchange read transports (Graph + EWS/exchangelib behind the `auth_type` dispatch seam, credential dispatch, normalized error foundation, read retry policy) on top of EPMCDME-15199 (MS_EXCHANGE credential type, toolkit registration). Research must start from that existing Exchange toolkit code and identify where the five write ops, the shared write retry/deadline owner, EWS/Graph error normalization, permission-advice messages, and the `tools_vars.py` LLM descriptions (differentiate from SMTP EmailTool) attach.
feature_area: exchange toolkit mailbox write graph ews
run_dir: /Users/Oleksandr_Cherevach/work/codemie-dev/codemie/docs/superpowers/tasks/2026-10-06-epmcdme-15201

---

## 2. Codebase Findings

All code lives in `src/codemie_tools/ms_exchange/` (package must not import from `codemie`).

### Existing Implementations
- `toolkit.py` — `ExchangeToolkit(DiscoverableToolkit)` with only `get_definition()` returning `ExchangeToolkitUI` (`toolkit=ToolSet.MS_EXCHANGE`, `settings_config=True`). It lists exactly four tools via `Tool.from_metadata(<METADATA>, tool_class=...)`. No `get_tools()` (definition-only toolkit).
- `tools.py` — `_ExchangeReadOnlyTool(CodeMieTool)` (`config: ExchangeToolConfig`, `is_safe()` returns True) with `RetrieveFoldersTool`, `ReadTool`, `RetrieveByIdTool`, `EnrichBodyTool`. Each has a pydantic `*Input` args schema, and `execute()` returns `_dump(get_client(self.config).<method>(...))` (JSON, `sort_keys`). Module docstring: "`execute` raises typed ExchangeError directly (no wrapping)". `EnrichBodyTool` shows the host-injected-callable pattern (`ocr_model_factory`, `chat_model`).
- `tools_vars.py` — one `ToolMetadata(name, description, label, user_description, settings_config=False, config_class=ExchangeToolConfig)` per tool (`exchange_retrieve_folders`, `exchange_read`, `exchange_retrieve_by_id`, `exchange_enrich_body`). Descriptions are explicitly "(read-only)". This is where write-tool metadata and the SMTP-vs-Exchange differentiation go.
- `client_base.py` — `ExchangeClient(Protocol)` with 7 read methods (`list_folders`, `list_messages`, `get_message`, `check_inbox`, `get_text_body`, `list_attachments`, `get_attachment_bytes`); module docstring is titled "Read-only client contract" and fixes plain-dict return shapes. `MAX_PAGE = 50`.
- `client_factory.py` — `get_client(config)`; `_CLIENTS = {OAUTH_AZURE: GraphExchangeClient, NTLM: EwsExchangeClient}`; wraps construction errors into `ExchangeError`/`InternalError`. This is the `auth_type` dispatch seam.
- `graph_client.py` — `GraphExchangeClient`. `_credential = build_credential(config)` (shared `ClientSecretCredential`, `TOKEN_TIMEOUT_SECONDS=10`, `TOKEN_RETRY_TOTAL=1`). `_call(op)` builds a fresh `GraphServiceClient` per attempt (kiota retry handler disabled via `RetryHandlerOption(should_retry=False)`, comment: "`call_with_retry` owns the retry policy"), runs `call_with_retry(lambda: run_sync(_run))`, and maps non-`ExchangeError` through `map_graph_exception`. `run_sync` wraps each attempt in `asyncio.wait_for(..., REQUEST_TIMEOUT_SECONDS=60)` (fixed, not deadline-aware). `_resolve_folder` maps well-known names (includes `deleteditems`, `sentitems`) else walks `list_folders()`.
- `ews_client.py` — `EwsExchangeClient`. `build_ews_client` makes `Account(primary_smtp_address=mailbox, autodiscover=False, access_type=DELEGATE)` over NTLM `Configuration` (no `retry_policy` argument passed), `account.protocol.TIMEOUT = 60`. `_call(fn)` = `call_with_retry(fn)` + `map_ews_exception`. `_folder()` resolves well-known names via `_WELL_KNOWN_FOLDERS` (`deleteditems -> trash`, `sentitems -> sent`, ...), Inbox via `_inbox()` (folder-not-found becomes `DelegateAccessError`), else walks `_mail_folders()`. `_fetch(message_id)` returns the item via `account.fetch(ids=[(id, None)])` with a one-item cache (`_last_fetch`); a cache must not serve stale state for write reconciliation. Docstring states "Only reads are issued: nothing is marked read, moved, sent or deleted" (will become false).
- `retry.py` — `call_with_retry(fn)` (the read policy). See Section 6 for why it cannot be reused unchanged.
- `errors.py` — `ExchangeError(ToolException)`: no-arg constructor, fixed class-level `MESSAGE`; 24 subclasses. `map_graph_exception` and `map_ews_exception` hold the mapping tables (`_AADSTS_MAP`, `_GRAPH_CODE_MAP`, EWS `table`). `graph_status`/`graph_code` helpers.
- `models.py` — `ExchangeAuthType` (`OAUTH_AZURE`, `NTLM`), `ExchangeToolConfig` (mailbox, auth_type, per-path required fields validated by `_validate_selected_path`).
- Consumers of `get_client`/`ExchangeError`: `src/codemie/service/settings/settings_tester.py` (`_test_ms_exchange`, calls `check_inbox`, returns `exc.MESSAGE`).
- SMTP counterpart: `src/codemie_tools/notification/email/tools.py` `EmailTool` (`script_callable = True`, description "Use this tool when you need to send an email notification via SMTP. Supports TO, CC, BCC, and custom FROM address."; returns plain strings, swallows exceptions into "Failed to send email: ..."). Registered in `src/codemie_tools/notification/toolkit.py`.

### Architecture and Layers Affected
1. Toolkit definition: `toolkit.py` (`ExchangeToolkitUI.tools`), `tools_vars.py` (metadata).
2. Tool classes: `tools.py` (new mutating tool classes; existing base class is read-only and `is_safe=True`).
3. Client contract: `client_base.py` Protocol and `client_factory.py` dispatch.
4. Transports: `graph_client.py` (msgraph-sdk/kiota async over `asyncio.run`) and `ews_client.py` (exchangelib sync).
5. Retry and error foundation: `retry.py`, `errors.py`.
6. Host app (outside package): `ToolkitService`/`tool_discovery_service` autodiscover the toolkit via `DiscoverableToolkit`; `settings_tester.py` only touches reads.

### Integration Points
- `get_client(config)` is the single place `auth_type` selects a transport; both clients must satisfy the same Protocol.
- `CodeMieTool._run` (src/codemie_tools/base/codemie_tool.py:125) wraps ANY exception from `execute` into a new `ToolException("Error calling tool: <name> with: Arguments: {kwargs}. The root cause is: '<sanitize_string(str(ex))>'")` and logs it with the arguments. Write-tool arguments (recipients, subject, body) therefore appear in that message and the error log. `execute` return values are JSON-dumped if non-string.
- `CodeMieTool.is_safe(args)` defaults to False (conservative; used for tool-call confirmation); only the read base class overrides it.
- msgraph request builders are imported from `msgraph.generated.users.item...` (graph_client top); exchangelib `errors as ews` imported in `ews_client.py`, `retry.py`, `errors.py`.

### Patterns and Conventions
- Fixed-sentence errors only; provider text never interpolated (errors.py docstring; guide `custom-tool-creation.md` "Retry and errors" row).
- Plain-dict, JSON-friendly returns from clients; tools `_dump` them with `sort_keys`.
- One `ToolMetadata` constant per tool; `Tool.from_metadata(..., tool_class=...)` in the UI class; per-tool `settings_config=False` (test asserts it).
- Errors raised as typed classes (`raise FolderNotFoundError`, no instantiation), mapped at the `_call` boundary with `from None`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/agents/custom-tool-creation.md` — "Definition-only toolkits (MS_EXCHANGE pattern)": transport dispatch, EWS endpoint policy, "Retry and errors" row (3 retries, 90 s budget, Retry-After up to 30 s), line 39 states the toolkit declares "four read-only tools". These rows describe the read behavior and will need revising if the story owns docs.
- Other relevant guides in `.ai-run/guides/`: `development/error-handling.md`, `development/security-patterns.md`, `testing/testing-patterns.md`, `agents/agent-tools.md` (not read in detail).

### Architectural Decisions
- Kiota's own retry is deliberately disabled so `call_with_retry` is the single owner (graph_client `build_graph_client` docstring).
- `codemie_tools` must not import `codemie`; host-only dependencies are injected as callables.
- Overlapping toolkits (generic Email vs Exchange) are separate, coexisting credential types; do not merge (guide, last paragraph).

### Derived Conventions
- Constants named in UPPER_CASE at module top (`REQUEST_TIMEOUT_SECONDS`, `MAX_RETRIES`); duplicated `REQUEST_TIMEOUT_SECONDS=60` in both clients.

---

## 4. Testing Landscape

### Existing Coverage
Under `tests/codemie_tools/ms_exchange/`: `test_graph_client.py` (uses `FakeGraphError`, `patch`; tests credential built once, per-call client, error mapping/sanitising, transient retry, 429 exhaustion), `test_ews_client.py`, `test_retry.py` (a single test: total-budget stops retries, patching `retry.time.sleep` and `retry.time.monotonic`), `test_errors.py`, `test_tools.py` (a `transport` fixture and `MESSAGES` table; read routing by `auth_type`), `test_client_factory.py`, `test_toolkit.py`, `test_models.py`, `test_enrichment.py`. Also `tests/codemie/service/settings/test_exchange_settings.py` and `test_settings_tester.py`.

### Testing Framework and Patterns
pytest (8.3.3 per cached pyc names), `unittest.mock.patch`/`patch.object`, hand-rolled fake exceptions carrying `response_status_code`/`response_headers`. Time is faked by patching `time.monotonic`/`time.sleep`; there is no injectable clock or random source in `retry.py`.

### Coverage Gaps
- `ExchangeClient` Protocol itself has no direct tests (codegraph flagged "no covering tests").
- `test_toolkit.py::test_exchange_toolkit_definition` asserts `>=` three read names only; nothing guards the tool count or that write tools are not auto-safe.
- No tests exist for any write path, response-loss handling, jitter, deadline accounting, or category handling.

---

## 5. Configuration and Environment

### Environment Variables
- `EXCHANGE_EWS_ALLOWED_HOSTS` (ews_client.py `ALLOWED_HOSTS_ENV`): operator allowlist of hosts/CIDRs for private EWS endpoints.

### Configuration Files
- `src/codemie_tools/ms_exchange/models.py` `ExchangeToolConfig` (credentials from encrypted platform settings; sensitive fields `oauth_client_secret`, `service_account_password`).
- Timeouts/retry constants are in code: `REQUEST_TIMEOUT_SECONDS=60` (graph_client, ews_client), `TOKEN_TIMEOUT_SECONDS=10`, `TOKEN_RETRY_TOTAL=1`, `retry.MAX_RETRIES=3`, `BASE_BACKOFF_SECONDS=1.0`, `MAX_RETRY_AFTER_SECONDS=30`, `MAX_TOTAL_SECONDS=90`.

### Feature Flags and Deployment Concerns
None for Exchange (ticket says the polling flag is out of scope). Dependencies in play: `msgraph-sdk`, `kiota_*`, `azure-identity`, `azure-core`, `httpx`, `exchangelib`, `requests` (versions not confirmed in this run; pyproject not read).

---

## 6. Risk Indicators

- `retry.py` `call_with_retry` conflicts with the ticket's write policy on several points: `MAX_RETRIES=3` gives up to 4 attempts (ticket: 3 total); it retries any HTTP >= 500 and `ews.ErrorTimeoutExpired` (ticket: these are Unknown for writes, never replayed); `min(hint, MAX_RETRY_AFTER_SECONDS)` shortens server Retry-After above 30 s (ticket forbids shortening; 30 s cap is for local delays only); no jitter (fixed `1 * 2**attempt`, ticket: full jitter 0..1 s, 0..2 s); deadline checks only before a sleep and does not bound per-request timeouts; no injectable clock/random. Read behavior must stay unchanged, so reuse is by a separate write owner, not editing in place.
- Nested retry surfaces on the write path: `GraphExchangeClient._call` already wraps `call_with_retry`; azure-identity token acquisition has its own `retry_total=1`; exchangelib applies its own retry policy (Configuration has no `retry_policy` set, so library default applies; not verified). Counting "including SDK/transport attempts" needs a check of each.
- `run_sync` and EWS `protocol.TIMEOUT` use fixed 60 s; with a 90 s tool-call deadline a first attempt can consume most of it. Request timeouts are not currently derived from remaining time on either transport.
- Error taxonomy has no outcome dimension: `ExchangeError` carries only a fixed `MESSAGE` and a no-arg constructor, and no class distinguishes Not applied vs Unknown. `map_graph_exception` turns every status >= 500 into `UnreachableError` and timeouts into `ServiceTimeoutError`, so current classes cannot say whether a write may have committed. `AccessDeniedError.MESSAGE` hard-codes `Mail.Read`, which the ticket says must not be reused for writes. Graph code map lacks conflict statuses (409/412) and `ErrorSendAsDenied`-style codes; EWS table lacks conflict/change-key and send-as errors; `ErrorAccessDenied` maps to the generic `DelegateAccessError`. `RateLimitedError` does not carry a Retry-After value.
- `CodeMieTool._run` rewraps exceptions into a string containing the call arguments, so the outcome wording (Confirmed / Not applied / Unknown) must survive that wrapper and the argument echo exposes send bodies/recipients in logs; ticket demands no new JSON envelope.
- Tool confirmation: base `is_safe` is False; the read base class returns True. Write tools must not inherit `_ExchangeReadOnlyTool`.
- `EwsExchangeClient._fetch` caches one message; reconciliation reads after a write must bypass it.
- Delegate semantics on EWS (sender identity for send, Sent Items, MoveItem to `account.trash`) are open questions in the ticket; cannot be proven by unit tests with patched clients.
- Docs drift: `client_base.py`, `ews_client.py` docstrings and `custom-tool-creation.md` assert read-only.
- Speculative: tag version checks may need Graph `If-Match`/changeKey support through the msgraph request configuration and exchangelib `save(...)` conflict-resolution setting (exchangelib's default may auto-resolve rather than fail); both need verification against the installed library versions.
- Speculative: Protocol growth (new write methods on `ExchangeClient`) is a likely shape, but spec/plan decide it.
- Speculative: Graph `sendMail`/`forward` return no body (202), so Confirmed/Unknown for them rests on status handling only.

---

## 7. Summary for Complexity Assessment

The change attaches to an existing, compact package (`src/codemie_tools/ms_exchange/`, about ten source files). It touches the toolkit definition (`toolkit.py`, `tools_vars.py`), the tool layer (`tools.py`), the client contract and dispatch (`client_base.py`, `client_factory.py`), both transports (`graph_client.py`, `ews_client.py`), and the retry/error foundation (`retry.py`, `errors.py`). Host app code (`settings_tester.py`, toolkit service) consumes the package but currently needs no knowledge of writes. Expected file surface is roughly 8-10 source files plus corresponding test files and a guide update.

Technical novelty is concentrated in three places: a write-specific retry/deadline owner that differs from the read policy on attempt count, 5xx/timeout classification, server-delay handling, jitter and per-request timeout derivation; an outcome-aware error taxonomy (Not applied vs Unknown) that the current fixed-message, no-arg `ExchangeError` model does not express; and two provider-specific idempotency/concurrency mechanisms (post-failure native state checks for move/delete, version/change-key protected category writes). The current retry and error code has hard-coded read assumptions (Mail.Read advice, 5xx treated as transient, Retry-After capped at 30 s), so this is new behavior rather than extension.

Test posture is reasonable for reads (per-module tests, fake provider errors, patched time) but nothing exists for writes, and there is no injectable clock/random seam in `retry.py`, which the acceptance criteria (AC 10, 11) require. Key risk factors: nested retry layers across SDKs, outcome classification for lost responses, delegate/sender semantics on EWS, preserving read retry behavior, and keeping caller-visible text free of provider text and credentials through the `CodeMieTool._run` wrapper.

---

## 8. External References

`/tmp/claude-501/ticket-15201.txt` — resolved and read in full (Jira EPMCDME-15201 description, the task's source of truth; first two CLI log lines ignored). Facts the spec needs, close to quotable:

- Ops on both transports behind `auth_type`: send (`POST /users/{mbx}/sendMail` | `Message(account=account, ...).send()`), forward (`/messages/{id}/forward` | `msg.forward(to_recipients=[...])`), move (`/move` with `destinationId` | `msg.move(to_folder=folder)`), tag, delete soft (Graph move to `deleteditems` | `msg.move(to_folder=account.trash)`; not `soft_delete()`), delete hard (`/permanentDelete` | `msg.delete()`). Public API: `move(message_id, target_folder)`, `tag(message_id, category)`, `delete(message_id, soft=True)`, `send(to, subject, body)`, `forward(message_id, to)`.
- Tag: read categories, add as a set, skip write if present, version/change-key check; on conflict reread/recompute in the same attempt and deadline or return a diagnosable conflict. No replace/remove, master categories, history, locking.
- Outcomes: Confirmed / Not applied (evidence of no mutation) / Unknown (timeout, response loss, 5xx, connection error after submission, old-ID not-found do not prove no change). Use existing taxonomy; no new success/error JSON envelope; no persistent history.
- Permission advice per operation: Graph `Mail.Send` (send, forward) or `Mail.ReadWrite` (move, tag, delete); also name admin consent and mailbox access restrictions as possible causes without asserting one; no raw provider text or credentials.
- Retry: at most 3 total submissions (initial + 2) including SDK/transport attempts, one retry owner; retry only proven no-mutation temporary failures (Graph 429, server-specific EWS rejection); no replay of auth/permission/input errors, Unknown send/forward, or blind move/delete; default delete never escalates to hard delete. Honor `Retry-After`/EWS backoff without shortening; if it does not fit remaining time stop with rate-limit result and retry advice. Fallback full jitter base 1 s: 0..1 s before retry 1, 0..2 s before retry 2; 30 s cap for local delays only. One 90 s deadline covers token work, native reads, requests, waits; request timeouts bounded by remaining time; no cancel/rollback promise. Keep EPMCDME-15200 read retry unchanged.
- AADSTS7000222 on token acquisition for a write: expired credential, Not applied (existing mapping already exists: `_AADSTS_MAP["7000222"] = CredentialExpiredError`); 7000215 invalid secret, 401 InvalidAuthenticationToken, and denied permission are reported separately; generic 401 does not imply expiry.
- Tests required: per-op, per-transport (1) definite pre-mutation failure -> Not applied and unchanged state, (2) server commit then response loss -> exactly one effect and Unknown; clock/random injection tests for attempt limit, no nested multiplication, waits never shortened, jitter ranges, cap, deadline accounting, bounded request timeouts; category preservation, repeat without write, concurrent change.
- Descriptions in `tools_vars.py` must differentiate `ExchangeTool.send` from SMTP `EmailTool`.
- Out of scope: reads, credential type/config/migration/registration, polling flag, business-level dedupe, Application Access Policy setup, IMAP/POP3, certificate OAuth, Autodiscover.
- Open questions: shared-mailbox sender permission on both paths; whether platform error surfaces must be extended to distinguish expired secrets.
