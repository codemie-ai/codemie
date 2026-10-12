# Technical Research

**Task**: ms_exchange exchange toolkit graph ews settings-tester
**Generated**: 2026-09-29
**Research path**: codegraph

---

## 1. Original Context

work on https://jiraeu.epam.com/browse/EPMCDME-15200 — the full ticket body (summary, story, entry conditions, scope, acceptance criteria, out of scope, constraints) is at docs/superpowers/tasks/2026-09-29-epmcdme-15200/ticket.md; read it first. Branch epmcdme-15200 was created from epmcdme-15199 (Slice 1, pre-requirement: codemie_tools/ms_exchange package with models.py/toolkit.py, MS_EXCHANGE credential type, ExchangeToolConfig) — research that existing code as the baseline. Backend repo only (codemie); the codemie-ui testable flag is a separate repo and just noted.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie_tools/ms_exchange/models.py` — `ExchangeAuthType` (str Enum: `oauth_azure`, `ntlm`); `ExchangeToolConfig(CodeMieToolConfig)` with `credential_type=CredentialTypes.MS_EXCHANGE` (frozen, excluded), `auth_type`, `mailbox` (RequiredField), `oauth_tenant_id/client_id/client_secret`, `ews_url`, `service_account_username/password`. Secrets carry `json_schema_extra={"sensitive": True}`. An after-model-validator `_validate_selected_path` requires auth_type, non-blank mailbox, and the selected path's three fields (`_PATH_FIELDS`).
- `src/codemie_tools/ms_exchange/toolkit.py` — `ExchangeToolkitUI(ToolKit)` (toolkit=`ToolSet.MS_EXCHANGE`, `tools=[]`) and `ExchangeToolkit(DiscoverableToolkit).get_definition()`. Toolkit currently has no tools and no `config_class`/`settings_config` set.
- `src/codemie_tools/ms_exchange/__init__.py` exists. No `tools.py`, `graph_client.py` or `ews_client.py` exist yet (Glob of `**/ms_exchange/**`).
- `src/codemie_tools/base/models.py` — `CredentialTypes.MS_EXCHANGE = "MSExchange"`, `ToolSet.MS_EXCHANGE = "Microsoft Exchange"`.
- `src/codemie/service/settings/settings.py` — references `ExchangeToolConfig` (11 callers in codegraph); slice-1 credential validation lives there.

### Architecture and Layers Affected
- Tools layer (`codemie_tools/ms_exchange/`): toolkit, config model, future tool classes and transport clients.
- Settings service layer: `src/codemie/service/settings/settings_tester.py` (`SettingsTester(SettingsService)`), called by `src/codemie/rest_api/routers/user_settings.py`.
- Dependency manifest and lockfile (pyproject/poetry). Not opened in this run.
- codemie-ui (separate repo): `testable` switch only.

### Integration Points
- `SettingsTester.__init__` loads the saved Settings by `setting_id`, drops masked values from the request, overlays the new ones, decrypts, then flattens to `self.credential_values` (dict). `test()` looks up `self.handlers[credential_type]` and raises `SettingsTesterHandlerNotFound` if absent. `MS_EXCHANGE` is NOT in the `handlers` dict today (17 types registered, imports at file top).
- Handler shape: `_test_x(self) -> Tuple[bool, str]`, e.g. `return XTool(config=XConfig(**self.credential_values)).healthcheck()`.
- `CodeMieTool.healthcheck()` (`src/codemie_tools/base/codemie_tool.py:100`) wraps `_healthcheck()` and returns `(False, humanize_error(e))` on any exception, `(True, "")` otherwise. Default `_healthcheck` raises NotImplementedError. A success message like "Connection and Inbox access verified." must therefore be supplied by the handler (the xWiki handler `_test_xwiki` is a precedent for post-processing the message).
- `humanize_error` is in `codemie_tools/base/utils.py`; error text is the UI-visible failure path (relevant to the no-secrets/no-message-IDs criterion).
- Toolkit discovery: `DiscoverableToolkit` subclasses are autodiscovered by toolkit_provider; `get_definition()` currently returns an empty tool list.

### Patterns and Conventions
- Tool classes extend `CodeMieTool`, declare `args_schema`, `name`, `description`, `config: <Config>`, implement `execute()` and optionally `_healthcheck()` and `is_safe(args)` (default False). `ServiceNowTableTool` is the compact precedent (`_healthcheck` calls `execute` with a limit-1 query; `is_safe` uses `_http_method_is_safe`).
- Tool metadata is in `tools_vars.py` files (e.g. `itsm/servicenow/tools_vars.py`) and `Tool.from_metadata(...)` builds UI entries.
- `_run` validates `required_at_runtime` config fields, wraps all errors in `ToolException` with sanitized text.
- Files carry the EPAM Apache-2.0 header.

---

## 3. Documentation Findings

### Guides and Architecture Docs
No exchange- or native-connector-specific guide is present under `.ai-run/guides/` (Glob for `*xchange*` and `*native*` found nothing; the "native-connector platform-requirements guide" from recent 15199 commits was not found under that path). Relevant existing guides (not read in this run): `agents/custom-tool-creation.md`, `agents/agent-tools.md`, `agents/tool-overview.md`, `testing/testing-patterns.md`, `development/error-handling.md`, `security/dependencies.md`, `quality-gates.md`.

### Architectural Decisions
From the ticket: Graph primary, EWS/NTLM required fallback; operator explicitly selects `auth_type`; no Autodiscover; retries must never change mailbox state; credentials only in encrypted storage.

### Derived Conventions
Healthcheck via tool class + SettingsTester handler; one config class per credential type; fixtures patch the client builder.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie_tools/ms_exchange/test_models.py` — config validation (auth_type missing/None, mailbox, per-path required fields, blank fields, valid oauth/ntlm).
- `tests/codemie_tools/ms_exchange/test_toolkit.py` — `ExchangeToolkitUI` default definition.
- `tests/codemie/service/settings/test_exchange_settings.py` — settings service handling of Exchange config/enum.
- `tests/codemie/service/settings/test_settings_tester.py` — `test_test_handler_found`, `test_test_handler_not_found`, `test_sharepoint_handler_is_registered` (registration-check precedent).
- Healthcheck test precedents: `tests/codemie_tools/notification/email/test_email_tools.py`, `tests/codemie_tools/sharepoint/test_tools.py` (success, bad credentials, missing permission cases).

### Testing Framework and Patterns
pytest (8.3.3 per pycache), unittest.mock patching, Arrange/Act/Assert comments. Ticket mandates patching `build_graph_client` in `graph_client.py` and its EWS counterpart.

### Coverage Gaps
No tests for any transport, tools, or an MS_EXCHANGE SettingsTester handler (none of that code exists yet).

---

## 5. Configuration and Environment

### Environment Variables
None found for Exchange. Timeouts/retry values for the new transports have no existing config entries (`codemie.configs.config` is the central place; `TOOL_TOKENS_SIZE_LIMIT` is an example of a tool-related setting).

### Configuration Files
`pyproject.toml` / lockfile govern the four new dependencies (azure-identity, msgraph-sdk-python, exchangelib, requests-ntlm); not inspected, and codegraph returned nothing for dependencies. Existing `azure_client.py` under `codemie_tools/cloud/azure` uses its own token handling and is not a msgraph precedent.

### Feature Flags and Deployment Concerns
Ticket references a polling feature flag from 15199 (not inspected). Release coupling: 15199 must ship with this slice. Local `__pycache__` shows both cpython-312 and cpython-313 runs in this checkout; ticket states Python 3.12 is required by the new libraries.

---

## 6. Risk Indicators

- Two new transports plus four new dependencies with lockfile regeneration; compatibility with existing pins unchecked (pyproject not read).
- msgraph-sdk-python major version must be confirmed (v0.x vs v1.x differ); ticket lists as open.
- Stated Python 3.12 requirement versus 3.13 bytecode present locally: verify the project's supported Python range.
- No live Exchange test environments (entry condition 3); mocked tests cannot prove NTLM handshake or consent behaviour.
- `enrich_body` blocked on EPMCDME-11264 return contract; fallback is deferring it.
- `_healthcheck` returns no success message; UI text "Connection and Inbox access verified." needs handler-level handling. `humanize_error` output could leak secrets or message IDs if not verified.
- `_exchange_token` in `broker_token_exchange_provider.py` has no tests but is unrelated (name collision only).
- Speculative: one-tool-vs-four decision affects only class count in tools.py (ticket says so).
- Speculative: the failure taxonomy (429, 401 expiry, consent, NTLM delegate revoked) needs typed exceptions; no existing Exchange error types exist.
- Two-repo change: codemie-ui `testable: true` is out of this repo.

---

## 7. Summary for Complexity Assessment

The baseline is a thin Slice-1 package: a validated `ExchangeToolConfig`, an empty `ExchangeToolkit`, the credential type and ToolSet entries, and tests for those. Everything this slice adds is new: `graph_client.py`, `ews_client.py`, `tools.py`, tool metadata, and an MS_EXCHANGE entry in `SettingsTester.handlers` (a small, well-precedented change: one dict entry plus one `_test_*` method delegating to `CodeMieTool.healthcheck()`). Layers touched are the tools package, the settings-tester service, and dependency manifests, with a separate UI repo change.

Technical novelty is high for this codebase: two SDKs with different auth models (azure-identity plus msgraph vs exchangelib plus NTLM), a two-identity model with no precedent, a bounded-retry and error-taxonomy requirement, and four new dependencies. The tool and healthcheck mechanics themselves follow established patterns (ServiceNow, SharePoint, Email).

Test posture: existing tests cover config and toolkit definition only; all transport, tool and tester-handler tests are new. Live-environment proof is a delivery gate outside CI. Key risks are dependency compatibility, SDK version choice, error-message leakage and the unresolved enrich_body contract.

---

## 8. External References

None named by the task beyond the ticket, which was read: `/Users/Oleksandr_Cherevach/work/codemie-dev/codemie/docs/superpowers/tasks/2026-09-29-epmcdme-15200/ticket.md` (resolved). Its Jira URL (https://jiraeu.epam.com/browse/EPMCDME-15200) was not fetched; the local file holds the full body. Key facts: Graph via ClientSecretCredential with `$top=1&$select=id` on Inbox; EWS via exchangelib `Configuration(server=ews_url)`, NTLM, `autodiscover=False`, `access_type=DELEGATE`, FindItem ID-only max 1; success text "Connection and Inbox access verified."; four read ops (retrieve_folders, read, retrieve_by_id, enrich_body); files graph_client.py, ews_client.py, tools.py in `codemie_tools/ms_exchange/`.
