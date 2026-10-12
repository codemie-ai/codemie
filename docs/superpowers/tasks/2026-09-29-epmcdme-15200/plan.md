# EPMCDME-15200 Exchange read transports and MS_EXCHANGE healthcheck Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Read-only Exchange operations over Graph (`oauth_azure`) and EWS/NTLM (`ntlm`) behind one toolkit, plus a live-mailbox SettingsTester healthcheck.

**Architecture:** Two clients implement one contract; a dispatcher picks by `config.auth_type`. Four `CodeMieTool` subclasses call the dispatcher. `execute` itself raises typed, fixed-sentence errors (the workflow ToolNode bypasses `_run`). Spec: `spec.md` in this dir; follow its error table and enrich_body section.

**Tech Stack:** msgraph-sdk ^1.0, azure-identity (existing), exchangelib ^5.5, requests-ntlm ^1.3, pytest + unittest.mock.

## Global Constraints

- All new code in `src/codemie_tools/ms_exchange/`; EPAM Apache-2.0 header on each file.
- Commit per task using the repository's existing convention.
- Error messages are fixed sentences; never interpolate `str(provider_exc)`; no tenant/client/correlation ids, URLs, accounts, message ids, secrets in messages or logs.
- Timeouts, retries, `Retry-After` cap, `MAX_PAGE`, enrich caps (10 attachments, 10 MB each, 25 MB total) are named module constants, not env config.
- No changes to Slice-1 `ExchangeToolConfig`, credential type, or Jira code. Tests use fixture-only credentials and patch `build_graph_client` / `build_ews_client`.

## Task 1: Dependencies

**Files:** Modify `pyproject.toml` (near line 100, beside azure-identity) and the lockfile.
- [ ] Add `msgraph-sdk = "^1.0"`, `exchangelib = "^5.5"`, `requests-ntlm = "^1.3"`; regenerate lockfile with no other pin changes. Verify by `python -c "import msgraph, exchangelib, requests_ntlm"`.

Test-first: no — manifest change; verified by import.

## Task 2: Typed errors and retry helper

**Files:** Create `errors.py`, `retry.py`; Test `tests/codemie_tools/ms_exchange/test_errors.py`.

**Interfaces:** Produces `ExchangeError(ToolException)` base with class-level `MESSAGE`; one subclass per spec taxonomy entry (credential-expired, credential-invalid, tenant, app-not-found, token-rejected, consent, mailbox-not-enabled, policy-denied, mailbox-not-found, folder-not-found, message-not-found, unreachable, rate-limited, tls-config, bad-endpoint, redirect, ews-credentials-rejected, delegate-access, internal). `map_graph_exception(exc)` and `map_ews_exception(exc)` return an `ExchangeError` (unmapped gives internal). AADSTS codes 7000222/7000215/90002/700016 keyed on code, not "401". `call_with_retry(fn)` retries only transient (429, 5xx, ErrorServerBusy, ErrorInternalServerTransientError, ErrorTimeoutExpired), honours `Retry-After`/`BackOffMilliseconds` capped by `MAX_RETRY_AFTER_SECONDS`, never retries credentials/TLS/config errors.

- [ ] Write failing tests: each class's exact sentence; message contains none of the provider text passed in; mapping table cases; retry counts, cap, non-retry cases.
- [ ] Implement; run tests.

Test-first: yes — mapping a `ClientAuthenticationError` with AADSTS7000222 to the credential-expired class fails until implemented.

## Task 3: Client contract and Graph client

**Files:** Create `client_base.py` (Protocol with `list_folders`, `list_messages(folder,start,count)`, `get_message(id)`, `check_inbox`, `get_text_body(id)`, `list_attachments(id)`, `get_attachment_bytes(id, attachment_id)`), `graph_client.py`; Test `test_graph_client.py`.

**Interfaces:** Produces `build_graph_client(config: ExchangeToolConfig)` (ClientSecretCredential + `GraphServiceClient`, `/users/{mailbox}/...`) and `GraphExchangeClient`. Async SDK bridged from sync using the loop-detect/thread/`asyncio.run` pattern at `src/codemie/service/mcp/toolkit.py:204-240`.

- [ ] Failing tests: `list_messages` orders by `receivedDateTime` desc then id, caps count at `MAX_PAGE`, no read-marking; Prefer `outlook.body-content-type="text"` header sent on message/body reads; `check_inbox` issues one request with `$top=1&$select=id` and empty Inbox succeeds; errors go through `map_graph_exception`; bridge works with and without a running loop.
- [ ] Implement; run tests.

Test-first: yes — a `read` call asserting the Prefer header fails before the client exists.

## Task 4: EWS client and dispatcher

**Files:** Create `ews_client.py`, `client_factory.py`; Test `test_ews_client.py`, `test_client_factory.py`.

**Interfaces:** Produces `build_ews_client(config)` and `EwsExchangeClient`; `get_client(config) -> client` dispatching on `auth_type`. Consumes Task 2 mappers and Task 3 Protocol.

- [ ] Failing tests: `Configuration(server=ews_url)` with NTLM, `autodiscover=False`, `access_type=DELEGATE` asserted; service account authenticates while mailbox is a distinct identity; `check_inbox` runs an executed (not lazy) ID-only FindItem max 1; `RedirectError` maps to redirect class; TLS trust failure is non-retried; deterministic sort and window in `list_messages`; text body else tag-stripped HTML.
- [ ] Implement; run tests.

Test-first: yes — asserting `autodiscover=False` and `DELEGATE` on the built Configuration/Account fails first.

## Task 5: Read tools and toolkit registration

**Files:** Create `tools.py`, `tools_vars.py`; Modify `toolkit.py` (register tools in `ExchangeToolkitUI.tools` and `get_definition()`; set `settings_config: bool = True` on `ExchangeToolkitUI` as in `open_api/toolkit.py:31`); Test `test_tools.py`, extend `tests/codemie_tools/ms_exchange/test_toolkit.py`.

**Interfaces:** Produces `RetrieveFoldersTool`, `ReadTool`, `RetrieveByIdTool` (`args_schema`, `config: ExchangeToolConfig`, `is_safe` True; precedent `itsm/servicenow`). `ReadTool` args `folder` (default Inbox), `start`, `count`. `execute` raises typed errors directly. `tools_vars.py` entries pass `config_class=ExchangeToolConfig` to `Tool.from_metadata` (as `open_api/tools_vars.py:40`), so the UI renders the Exchange config; the toolkit test asserts `settings_config` is True and each tool's `config_class` is `ExchangeToolConfig`.

- [ ] Failing tests: each tool routes to the client chosen by `auth_type` on both transports; `retrieve_by_id` not-found gives message-not-found (folder/mailbox not-found are distinct); identical `read` calls return identical output; typed error raised when calling `execute` directly; toolkit lists the tools.
- [ ] Implement; run tests.

Test-first: yes — toolkit definition test expecting the three tools fails while `tools=[]`.

## Task 6: Enrichment and enrich_body tool

**Files:** Create `enrichment.py`; Modify `tools.py` (add `EnrichBodyTool`), `tools_vars.py`, `toolkit.py`; Test `test_enrichment.py`, extend `test_tools.py`.

**Interfaces:** Produces `enrich(body: str, attachments: Iterable[(filename, mime, bytes)], ocr) -> str` and `EnrichBodyTool` with args `message_id`, `ocr_model: str | None` (documented in description and `args_schema`). Extractors: `ImageProcessor.extract_text_from_image_bytes` (`codemie_tools/utils/image_processor.py:66`), `PDFProcessor.extract_text_as_markdown` (`file_analysis/pdf/processor.py:220`), `extract_docx_text` (`file_analysis/workers/docx_workers.py:37`). OCR model resolution: `ocr_model` set builds model via the pattern at `src/codemie/service/tools/toolkit_service.py:806-819`; else injected `chat_model`; else images skipped "no OCR model provided".

- [ ] Failing tests: identical output shape on both transports (body then one labelled section per attachment, message order); image/PDF/docx extracted; skipped lines with fixed reasons for item/reference, over-cap (count, per-file, total), unsupported, no-OCR-model, extraction failure; PDF and docx extract in all three OCR cases; all three OCR resolution cases; no mailbox writes; fetch errors reuse taxonomy classes.
- [ ] Implement; run tests.

Test-first: yes — image with neither model source expected to yield the "no OCR model provided" line.

## Task 7: MS_EXCHANGE SettingsTester handler

**Files:** Modify `src/codemie/service/settings/settings_tester.py` (import block, `handlers` dict, new `_test_ms_exchange`); Test `tests/codemie/service/settings/test_settings_tester.py` (registration precedent `test_sharepoint_handler_is_registered`).

**Interfaces:** Consumes `get_client`, taxonomy. `_test_ms_exchange(self) -> Tuple[bool, str]` builds `ExchangeToolConfig(**self.credential_values)` inside the handler, catches pydantic `ValidationError` returning a sanitised config-invalid message, runs `check_inbox()`, returns `(True, "Connection and Inbox access verified.")`, and on `ExchangeError` returns `(False, exc.MESSAGE)`; unmapped errors give the internal sentence. Do not use `humanize_error`.

- [ ] Failing tests: handler registered; success text; empty Inbox succeeds; invalid config returns sanitised message without raw dump; each transport failure returns the fixed taxonomy sentence; no secrets or ids in returned text or logs; masked saved credentials still resolve.
- [ ] Implement; run tests.

Test-first: yes — `handlers[CredentialTypes.MS_EXCHANGE]` lookup raises `SettingsTesterHandlerNotFound` today.

## Negative-constraint pass

- Non-goals (writes, ingestion, polling, Autodiscover, env-config, Jira change, Slice-1/`ExchangeToolConfig` change, extra file types): no task touches them; Tasks 3-6 are read-only; Task 4 asserts `autodiscover=False`; Task 6 adds no Jira edits.
- "Never dispatch mark-read", "no provider text in messages": Tasks 2, 3, 7.
- "Never retry rejected credentials/TLS": Task 2.
- "No bodies/attachments in healthcheck": Task 7 uses `check_inbox` only.
- UI handoff (codemie-ui) is out of this repo; no task.
