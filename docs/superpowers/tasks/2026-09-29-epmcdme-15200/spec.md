# EPMCDME-15200 Spec: Exchange read transports and MS_EXCHANGE healthcheck

Ticket: `ticket.md` (same dir). Baseline: Slice 1 (EPMCDME-15199) in `src/codemie_tools/ms_exchange/` (`models.py`, `toolkit.py`). Size XL (27/36); the split recommendation was overridden by the user, so both transports ship in this slice.

## Goal
Read-only mailbox operations over Microsoft Graph (`auth_type=oauth_azure`, primary) and EWS/NTLM (`auth_type=ntlm`, required fallback) behind one toolkit contract. Also a live-mailbox `SettingsTester` healthcheck for `CredentialTypes.MS_EXCHANGE`. The operator selects `auth_type` explicitly; there is no default transport.

## Design
- **Layout** (all in `src/codemie_tools/ms_exchange/`): `graph_client.py` (exposes `build_graph_client`), `ews_client.py` (mirrors it with its own connection builder), `tools.py`, `tools_vars.py` (UI metadata via `Tool.from_metadata`), `errors.py` (typed exceptions and fixed messages), `enrichment.py` (shared, transport-agnostic extraction). Neither transport is embedded in `tools.py`.
- **Client contract**: both clients implement `list_folders()`, `list_messages(folder, start, count)`, `get_message(id)`, `check_inbox()`, `get_text_body(id)`, `list_attachments(id)` and `get_attachment_bytes(id, attachment_id)`. A dispatcher picks the client from `config.auth_type`. This is also the extension point for Slice 3 writes.
- **Tools**: one `CodeMieTool` subclass per operation: `retrieve_folders`, `read`, `retrieve_by_id`, `enrich_body`. They follow `itsm/servicenow` (`args_schema`, `config: ExchangeToolConfig`, `is_safe` True). The toolkit's `tools` list and `get_definition()` register them.
- **`read` window**: `folder` (default Inbox), `start` (offset), `count` (capped by a max-page constant). Sorted by `receivedDateTime` descending then id, so identical calls return identical results. It never reads past the window and never marks messages read.
- **Graph**: `ClientSecretCredential` (azure-identity, already a prod dependency) and `msgraph-sdk-python` v1.x `GraphServiceClient` against `/users/{mailbox}/...`. Send `Prefer: outlook.body-content-type="text"` on `read`, `retrieve_by_id` and `enrich_body`. No manual JWT handling.
- **EWS**: `exchangelib` `Configuration(server=ews_url)`, NTLM via `requests-ntlm`, `autodiscover=False`, `access_type=DELEGATE`. The service account authenticates and the mailbox is a distinct identity. Autodiscover is never attempted.
- **Async bridge**: the async Graph SDK is called from the sync `execute`. Copy the in-repo precedent `src/codemie/service/mcp/toolkit.py:204-240` (detect a running loop, offload to a thread, `asyncio.run`). `SettingsTester.test_setting` is a plain `def` (FastAPI threadpool) and LangChain's default `_arun` uses `run_in_executor`, so no live path has a running loop. There is no dedicated running-loop error class; an unexpected bridge failure falls under a generic sanitised internal-error fallback.
- **Error wrapping caveat**: the workflow ToolNode calls `tool.execute(**args)` directly (`tool_node.py:294`), bypassing `_run`. `ToolException` wrapping therefore holds only on the agent path. Error typing must not rely on that wrapper, so `execute` itself raises the typed, sanitised exceptions.
- **Retry/timeouts**: named constants, documented in the module docstring. Finite timeouts. Bounded retries only for transient failures. A `Retry-After` header is honoured and capped by a named constant. Rejected credentials, TLS trust failures and config errors are never retried. Retries only repeat reads, so they cannot change mailbox state.

## enrich_body
Read-side and read-only. It takes a message id and an optional `ocr_model`, and returns a single `str`. It triggers no downstream ingestion, sends nothing and marks nothing read.

**Contract provenance:** EPMCDME-11264 (Done) defines NO `enrich_body` return contract. Its API returns `list[dict]`, posts a Jira comment, and its caps are env-config. The `str` return and the 10 / 10 MB / 25 MB named non-env caps are this ticket's user decisions. Entry condition 2 is treated as resolved by that decision, not by 11264's contract.

- **Body**: text body first. Graph uses the Prefer header. EWS uses `text_body`, else HTML with tags stripped.
- **Attachments**: Graph uses `GET .../messages/{id}/attachments` (fileAttachment, `contentBytes`; large attachments are fetched separately). EWS uses `item.attachments` FileAttachment `.content`.
- **Shared extraction step** (`enrichment.py`): both transports feed it the same neutral input (filename, mime, bytes), so output has an identical shape. Output is the body, then one labelled section per attachment, in message order.
- **Extractors** (reusable without Jira coupling, verified): images via `ImageProcessor.extract_text_from_image_bytes` (`codemie_tools/utils/image_processor.py:64`); digital PDF via `PDFProcessor.extract_text_as_markdown(bytes)` (`file_analysis/pdf/processor.py:220`); docx via `extract_docx_text(bytes)` (`file_analysis/workers/docx_workers.py:37`). Jira's `_run_ocr` is only a 12-line Jira-bound wrapper, so no shared-helper extraction is needed and Jira is untouched. Every other type is listed as unsupported.
- **OCR model resolution** (images only; PDF and docx need no LLM). `ocr_model: str | None` is an `args_schema` arg, documented in the tool description:
  1. `ocr_model` set: the tool builds a multimodal model itself via `get_llm_by_credentials` (pattern at `src/codemie/service/tools/toolkit_service.py:806-819`, `llm_service.get_multimodal_llms()`) and OCRs images.
  2. Unset but a `chat_model` was injected at construction (assistant chat): use it.
  3. Neither: images are listed as skipped with the fixed reason "no OCR model provided".
  
  Workflow ToolNode (`tool_service.py:100-106` via `get_core_tools`, no `llm_model`) never injects `chat_model`, so chat works out of the box while workflows must set `ocr_model` on the node. There is no `ExchangeToolConfig`, Slice-1 or UI change for this.
- **Caps** (named non-env constants): max 10 attachments per message, 10 MB per attachment, 25 MB total. Anything over a cap is listed as skipped.
- **Skipped attachments** are never dropped silently. Item and reference attachments, over-cap, unsupported type, no OCR model and extraction failure each get a listed line with a fixed sanitised reason (no provider text). These are non-fatal notes. Transport errors while fetching reuse the existing taxonomy classes.

## Error taxonomy
Each class in `errors.py` carries a FIXED, sanitised, user-facing sentence, and tests assert that sentence. Messages never interpolate `str(provider_exc)`. `humanize_error` (`src/codemie_tools/base/utils.py:184`) is raw `str(e)`, and raw azure or exchangelib text leaks tenant/client ids, correlation ids, login URLs, EWS URLs and the service account. The handler and tools emit their own strings. The exception-to-class mapping is specified as a table in the plan.

**Graph token layer** (`azure.core` `ClientAuthenticationError` from `ClientSecretCredential`, keyed on AADSTS code, not on "401"):
- 7000222 = expired secret (credential-expired class)
- 7000215 = invalid secret (credential-invalid class)
- 90002 = bad tenant (tenant class)
- 700016 = unknown app (app-not-found class)
- Graph 401 `InvalidAuthenticationToken` is a separate token-rejected class. Bare 401 is not the expired-secret signal.

**Graph API**:
- 403 `Authorization_RequestDenied` = consent missing or revoked
- 403 `MailboxNotEnabledForRESTAPI` = mailbox unlicensed or not REST-enabled
- 403 Application Access Policy denial = policy-denied
- 404 `ErrorInvalidUser`/`ResourceNotFound` = mailbox-not-found
- 404 `ErrorFolderNotFound` = folder-not-found
- 404 `ErrorItemNotFound` = message-not-found
- Unreachable (DNS, refused, 5xx, timeout) is a separate class from mailbox-not-found.
- 429 (retry exhausted) = rate-limited. That class also covers EWS `ErrorServerBusy` (`BackOffMilliseconds` honoured under the cap), `ErrorInternalServerTransientError` and `ErrorTimeoutExpired` (bounded retry).

**TLS**: a trust failure (e.g. self-signed cert) is a config-error class, excluded from the transient retry path.

**EWS** (`exchangelib` exceptions):
- `TransportError`/`SOAPError` on the configured URL = bad-endpoint
- `RedirectError` (an Autodiscover-style redirect) = its own class; it is evidence that `autodiscover=False` held
- NTLM `UnauthorizedError` = rejected service-account credentials
- `ErrorAccessDenied`/`ErrorNonExistentMailbox` = delegate access revoked or mailbox missing
- `ErrorImpersonateUserDenied` is not mapped (unreachable under DELEGATE)

**Fallback**: any unmapped exception becomes a generic sanitised internal-error class.

**Attachment notes** (non-fatal, in `enrich_body` output, not exceptions): attachment-too-large, attachment-unsupported, attachment-not-a-file, no-OCR-model, attachment-extraction-failed.

## Healthcheck and UI transfer
- Add `MS_EXCHANGE` to `SettingsTester.handlers` with `_test_ms_exchange`. It builds `ExchangeToolConfig(**self.credential_values)` **inside the handler and catches pydantic `ValidationError` itself**. The config is built outside `CodeMieTool.healthcheck()`, so otherwise the error escapes as a generic 422 with a raw dump. It returns a sanitised config-invalid message. The handler then runs the selected client's `check_inbox()` and emits its own strings from the taxonomy.
- Graph: list-messages on Inbox with `$top=1&$select=id`, no further pages. EWS: an executed FindItem on Inbox, ID-only, max 1 (a lazy query does not count). Zero items is a success. No bodies or attachments, and no IDs or secrets logged.
- Transfer facts: failure goes through `user_settings.py` as `ExtendedHTTPException(400, "Integration test failed", details=<typed message>)`, which the UI shows verbatim as `Integration test failed<br> details`. Success is 200 `{success, message}`. The success text "Connection and Inbox access verified." is currently DISCARDED by the UI (`TestIntegration.tsx:88` hardcodes `toaster.info('Integration test successful')`). See the UI handoff below.

## Dependencies
THREE new production dependencies: `msgraph-sdk` (^1.0), `exchangelib` (^5.5), `requests-ntlm` (^1.3). `azure-identity ^1.16.0` is already a prod dependency (`pyproject.toml:100`). Verified: they resolve with zero pin changes (msgraph-sdk 1.63.0, exchangelib 5.6.0; python >=3.12,<3.14). Add them to `pyproject.toml` and regenerate the lockfile.

## Acceptance criteria
1. `retrieve_folders`, `read`, `retrieve_by_id` and `enrich_body` work on both transports through separate tool classes, selected by `auth_type`.
2. `read` honours `folder`/`start`/`count`, is bounded, sorts deterministically, marks nothing read, and identical calls return identical results.
3. `retrieve_by_id` returns the message, or a message-not-found error. Folder and mailbox not-found are separate classes.
4. Graph `read`, `retrieve_by_id` and `enrich_body` send the text-body Prefer header.
5. EWS uses the explicit endpoint, NTLM, `autodiscover=False` and DELEGATE; tests assert all of it. A `RedirectError` maps to its own class.
6. `enrich_body` returns a `str` with the same shape on both transports: text body, then one labelled section per attachment. Images (OCR), digital PDF and docx are extracted. Item/reference, over-cap, unsupported, no-OCR-model and failed attachments each appear as a listed line with a fixed sanitised reason. Nothing is dropped silently.
7. OCR model resolution has tests for all three cases: `ocr_model` set (tool builds the model), only an injected `chat_model` (used), and neither (images skipped with "no OCR model provided"). PDF and docx extract in all three. `ocr_model` is documented in the tool description and `args_schema`. No `ExchangeToolConfig` change.
8. `enrich_body` enforces the caps (10 attachments, 10 MB each, 25 MB total), adds no ingestion, marks nothing read, changes no mailbox state and leaves Jira behaviour unchanged.
9. Every class in the taxonomy is a distinct typed error with a fixed sanitised sentence asserted in tests. No provider text, tenant/client/correlation ids, URLs or accounts appear in messages. AADSTS codes map as specified. Attachment notes are non-fatal. Typing holds when `execute` is called directly, without the `_run` wrapper.
10. Retry is bounded, honours a capped `Retry-After`, and never retries rejected credentials or TLS/config errors.
11. The sync `execute` bridges to the async Graph SDK using the `mcp/toolkit.py` precedent, and works under the threadpool `test_setting` path and direct calls. Unexpected failures give the generic sanitised error.
12. `MS_EXCHANGE` handler is registered. It catches `ValidationError` and returns a sanitised config-invalid message. Graph does one `$top=1&$select=id` request with no paging; EWS runs an executed max-1 ID-only FindItem. An empty Inbox succeeds and success returns "Connection and Inbox access verified."
13. Test does no send/move/delete/mark-read and fetches no bodies or attachments. No secrets or message IDs appear in the UI or logs. Masked saved credentials resolve.
14. Mocked tests for both transports and the handler cover the ticket flows, enrichment (each attachment type, skip reason, caps) and each taxonomy class, using fixture-only credentials and patching `build_graph_client` and its EWS counterpart.
15. THREE new deps added (msgraph-sdk v1.x, exchangelib, requests-ntlm), lockfile regenerated, quality gates pass.

## Owed post-implementation handoff: codemie-ui (separate repo/MR, outside backend scope)
New branch `EPMCDME-15200_*` off `EPMCDME-15199_add-ms-exchange-integration`. The user decided to include these items:
- (a) `src/utils/settingsUIConfig.ts:535` `testable` false to true.
- (b) `src/utils/__tests__/settingsUIConfig.test.ts:172` flip the assertion and add a Test-button-renders test.
- (c) `src/pages/integrations/components/TestIntegration.tsx:88` use `toaster.info(response.message || 'Integration test successful')` (cross-cutting, user-approved), so the success text reaches the user.

## Delivery gate
Both live environments are a delivery gate outside CI. Mocked tests cannot prove the NTLM handshake, delegate enforcement or consent propagation.

## Non-goals
- Write-side enrichment, i.e. annotating the email (the wording of client REQ-2.4.2/2.4.4 could imply it). If ever wanted, it is a separate mutating operation for Slice 3 (EPMCDME-15201).
- Mutating operations (Slice 3).
- Polling, batching, dedup.
- IMAP/POP3, certificate OAuth, Autodiscover, Application Access Policy setup.
- Changes to Slice 1 config, credential type, migration, flag or docs; any `ExchangeToolConfig` change for OCR.
- Business logic on retrieved messages; env-configurable timeouts, retries or caps.
- Extracting file types beyond images, digital PDF and docx.
- Changes to Jira attachment behaviour.
- Workflow error-handling changes (EPMCDME-14450).
- Implementing the codemie-ui changes in this repo (listed above as a handoff only).

## Release note
EPMCDME-15199 must ship in the same release as this slice.
