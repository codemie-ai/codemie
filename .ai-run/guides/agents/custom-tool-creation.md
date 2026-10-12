# Custom Tool Creation

## Choose The Right Layer

Use `codemie_tools` for reusable external capabilities and `codemie/agents/tools` for platform-facing adapters.

| Avoid | Prefer |
|---|---|
| One-off tool logic in agent prompts | Tool class with explicit inputs and outputs |
| Duplicating an existing provider toolkit | Extend the nearest toolkit package |

Evidence: provider toolkits exist under `src/codemie_tools/git/`, `src/codemie_tools/cloud/`, `src/codemie_tools/qa/`, and `src/codemie_tools/data_management/`.

## Tests

Add or update focused tests when tool schema, serialization, or provider behavior changes.

| Avoid | Prefer |
|---|---|
| Only manual testing for schema changes | Unit tests around schema and toolkit behavior |
| Broad integration tests for simple transformations | Narrow tests near the tool package |

Evidence: existing toolkit tests live under `tests/codemie_tools/` and agent tool tests under `tests/codemie/agents/tools/`.

## Registry

A new `CodeMieTool` subclass registers itself in `codemie_tools/base/tool_registry.py` automatically; new static facts about tool classes extend `ToolRecord` there, not a new scan.

## Adding a Native Connector (platform requirements)

A native toolkit is not a runtime plug-in. It cannot be dropped in and hot-registered — adding one touches a closed enum, a migration, settings wiring, and a hand-authored UI mapping, and only takes effect after a redeploy of both backend and UI.

| Requirement | What it means | Evidence |
|---|---|---|
| 1. Closed-enum members | Add `CredentialTypes.<NAME>` and `ToolSet.<NAME>="<UI label>"` in the same PR as the toolkit class. The `CredentialTypes` name is the DB label and is irreversible once rows exist. The `ToolSet` label is persisted in assistant JSON. | `src/codemie_tools/base/models.py` — `CredentialTypes.MS_EXCHANGE = "MSExchange"`, `ToolSet.MS_EXCHANGE = "Microsoft Exchange"` |
| 2. Enum migration | An Alembic migration using `op.sync_enum_values()` with `.name` labels, whose downgrade deletes the new type's rows first and then shrinks the enum — never a raw `ALTER TYPE`. Concurrent branches touching the same enum can fork the migration chain, so run `alembic heads` right before writing the migration and again right before merging. | `src/external/alembic/versions/5189d4d2afaf_add_ms_exchange_credential_type.py` |
| 3. Settings wiring | A `__CREDENTIAL_CONFIG_TO_TYPE` entry mapping the config class to the new credential type, plus the secret keys added to `LIST_OF_SENSITIVE_FIELDS` so they get masked/encrypted. | `src/codemie/service/settings/settings.py` |
| 4. UI credential mapping | A hand-authored codemie-ui `CREDENTIAL_UI_MAPPING` entry. This is not schema-driven and must be added by hand in the separate codemie-ui repo, with secret fields flagged `sensitive: true`. | codemie-ui `src/utils/settingsUIConfig.ts` — `CREDENTIAL_UI_MAPPING` |
| 5. Redeploy | Both backend and UI must be redeployed — there is no hot registration of new credential types or toolkits. | — |

Reference implementation: `MS_EXCHANGE`.

- Toolkit class: `src/codemie_tools/ms_exchange/toolkit.py` — autodiscovered `ExchangeToolkit(DiscoverableToolkit)` whose `get_definition()` returns `ExchangeToolkitUI`, declaring four read-only tools (folders, read, retrieve-by-id, enrich-body). The toolkit has no `get_tools()`.
- Config: `src/codemie_tools/ms_exchange/models.py` — `ExchangeToolConfig` (dual-path `oauth_azure`/`ntlm`; secrets marked `sensitive`).

### Definition-only toolkits (MS_EXCHANGE pattern)

A toolkit can declare its tools only in `get_definition()`. The platform builds each tool from the definition and the stored credentials, so the toolkit needs no `get_tools()` override.

| Concern | Rule | Evidence |
|---|---|---|
| Direct-credential execution | Not supported. `get_tool_with_direct_creds` calls `toolkit.get_tools()`, which a definition-only toolkit doesn't implement, so `POST /v1/tools/{name}/invoke` with raw `tool_creds` fails for it. Do not build the config from caller-supplied `tool_creds`: for OAuth-backed toolkits (Jira, Confluence, GitLab) that would let a caller set `integration_id` and `acting_user_id` and act as another user. Use a saved integration instead; the server fills in those fields. | `src/codemie/service/tools/tool_execution_service.py` — `get_tool_with_direct_creds` |
| Assistant-runtime injection | Tools that declare `ocr_model_factory` get a lambda that builds the model lazily, independent of `chat_model`. `_build_ocr_model` accepts only names from `llm_service.get_multimodal_llm_names()` (each multimodal model's `base_name` and `deployment_name`) because the name is LLM-supplied. When `projectModelOverride` is on it also rejects, with `ModelNotWhitelistedException`, a model the assistant's or workflow's project whitelist excludes (an empty whitelist denies all, no whitelist allows all); it does not fall back to the project default, which may not be multimodal. | `src/codemie/service/tools/toolkit_service.py` — `_build_ocr_model` |
| Discovery schema | A frozen config field that has a default, such as `credential_type`, is not reported as required. Plain defaults still count as required, because many configs default credentials to `""`. String annotations (`from __future__ import annotations`) are resolved via `get_type_hints`. | `src/codemie/service/tools/discovery/config_extractor.py` — `_has_usable_default`, `_resolved_type_hints` |
| Connection test | Register a handler in `SettingsTester`. It returns only fixed sentences or `ExchangeError.MESSAGE`, never provider text, which can carry tenant IDs, URLs and accounts. | `src/codemie/service/settings/settings_tester.py` — `_test_ms_exchange` |
| Transport dispatch | One config class with `auth_type` selects the client. `oauth_azure` uses Microsoft Graph (`msgraph-sdk`) and `ntlm` uses EWS (`exchangelib`). Both implement the `ExchangeClient` Protocol, which fixes plain-dict return shapes and a `MAX_PAGE` cap. | `src/codemie_tools/ms_exchange/client_factory.py`, `client_base.py`, `models.py` |
| EWS endpoint policy | `ews_url` must be https. Its host, and every A/AAAA address it resolves to, is checked before connecting. Loopback, link-local (cloud metadata), unspecified and multicast addresses are always rejected. Other non-global addresses (RFC 1918, ULA, CGNAT) are allowed only when the operator env var `EXCHANGE_EWS_ALLOWED_HOSTS` lists the host name or a CIDR that covers the address; this is how on-prem Exchange is enabled. Failed or slow DNS gives `UnreachableError`. DNS rebinding between the check and exchangelib's own connect remains a known residual risk. | `src/codemie_tools/ms_exchange/ews_client.py` — `_check_host` |
| Retry and errors | Transient failures (HTTP 429/5xx, EWS server-busy and timeout) go through `call_with_retry`. It retries at most 3 times within a 90 s total budget and honours Retry-After up to 30 s. All failures surface as typed `ExchangeError` subclasses with class-level `MESSAGE` text. | `src/codemie_tools/ms_exchange/retry.py`, `errors.py` |

Package boundary: `codemie_tools` must not import from `codemie`. Host-only dependencies (model building) are therefore injected as callables, as with `ocr_model_factory` on `EnrichBodyTool` (`src/codemie_tools/ms_exchange/tools.py`).

Overlapping toolkits: a protocol-generic toolkit and a vendor-native one for the same domain (for example generic Email/IMAP and Microsoft Exchange) are separate, coexisting `CredentialTypes`. Tell them apart by protocol and target estate; do not merge them or rename one to avoid the overlap.
