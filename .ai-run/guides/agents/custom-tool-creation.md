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

- Toolkit class: `src/codemie_tools/ms_exchange/toolkit.py` — autodiscovered `ExchangeToolkit(DiscoverableToolkit)` whose `get_definition()` returns `ExchangeToolkitUI`, declaring nine tools: four reads (folders, read, retrieve-by-id, enrich-body) and five writes (send, forward, move, tag, delete). Reads change nothing in the mailbox; writes follow the write policy below. The toolkit has no `get_tools()`.
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
| Retry and errors (reads) | Transient read failures (HTTP 429/5xx, EWS server-busy and timeout) go through `call_with_retry`. It retries at most 3 times within a 90 s total budget and honours Retry-After up to 30 s. All failures surface as typed `ExchangeError` subclasses with class-level `MESSAGE` text. Writes do not use this path; see the write policy. | `src/codemie_tools/ms_exchange/retry.py`, `errors.py` |
| Retry and errors (writes) | `write_retry.run_write` is the only retrier for writes; `call_with_retry` and Kiota's retry handler (disabled) must not wrap a mutating request. See the write policy. | `src/codemie_tools/ms_exchange/write_retry.py` |

Package boundary: `codemie_tools` must not import from `codemie`. Host-only dependencies (model building) are therefore injected as callables, as with `ocr_model_factory` on `EnrichBodyTool` (`src/codemie_tools/ms_exchange/tools.py`).

Overlapping toolkits: a protocol-generic toolkit and a vendor-native one for the same domain (for example generic Email/IMAP and Microsoft Exchange) are separate, coexisting `CredentialTypes`. Tell them apart by protocol and target estate; do not merge them or rename one to avoid the overlap.

### Exchange write policy

Applies to `send`, `forward`, `move`, `tag` and `delete` on both transports. A write that is retried blindly can duplicate mail, so retry is allowed only after the attempt is proven to have changed nothing.

| Concern | Rule | Evidence |
|---|---|---|
| Single retry owner | `run_write` owns retry. At most 3 submissions per tool call. Reads inside a write call are single attempts (`_inner_read`), not retried, except the tag read, which sits inside the submission and is retried like the save (see What may be retried). EWS write reads pass `only_fields` so they never download the whole message. | `write_retry.py` — `MAX_SUBMISSIONS`, `run_write`; `graph_client.py`, `ews_client.py` — `_inner_read` |
| One deadline | One 90 s `Deadline` per tool call covers token acquisition, reads, waits and submissions. It is a client-side completion limit, not a cancel or rollback promise. A server-supplied wait is honoured as given; a wait that does not fit in the remaining time stops the call. | `write_retry.py` — `Deadline`, `DEADLINE_SECONDS` |
| Outcome taxonomy | Confirmed: the call returned a result. Not applied: proven to have changed nothing; message starts `Not applied.`. Unknown: may or may not have changed the mailbox; message starts `Unknown.` and tells the caller to check the mailbox first. After a submission, timeouts, connection loss, 5xx and unexpected exceptions are Unknown. | `errors.py` — `NotAppliedError`, `UnknownOutcomeError` |
| Error attributes | `ExchangeError.OUTCOME` is a `WriteOutcome` member (`NOT_APPLIED`, `UNKNOWN`) or `None` (reads). `ExchangeError.RETRYABLE` is `True` for reads and `False` for both write outcomes. | `errors.py` — `ExchangeError`, `WriteOutcome` |
| What may be retried | Only proven no-mutation rejections. Graph: HTTP 429, with `Retry-After` as the wait. EWS: a SOAP `ErrorServerBusy` whose response body carries `BackOffMilliseconds`; exchangelib's own exception attributes are not evidence. A throttled tag read is retried the same way on both transports (a read timeout or lost connection is not). HTTP 503, 500, ServerBusy without a back-off, transient server errors and timeouts are mapped, never resubmitted. | `graph_client.py` — `_retry_after_seconds`; `ews_client.py` — `server_busy_evidence`, `_ews_write_classifier` |
| EWS private protocol | Each write call builds its own `Protocol` (`build_ews_write_session`): no cache, no shared pool, `FailFast`, and an HTTP adapter bound to the call's `Deadline` that clamps timeouts and records transport evidence. Never mutate `TIMEOUT` or the adapter on the shared cached `Protocol` that the read client uses. The session is closed when the call ends. | `ews_client.py` — `_private_protocol`, `DeadlineAdapter`, `build_ews_write_session` |
| Graph token and Kiota | Kiota's `RetryHandler` is off, so `run_write` is the only retry layer. The token is acquired explicitly under the Deadline with the CAE flag Kiota uses (`enable_cae=True`) so the write that follows reuses it. | `graph_client.py` — `_start_write`, `RetryHandlerOption` |
| Workflow retry | The workflow retry hook skips any exception whose `RETRYABLE` is `False` (duck-typed `getattr(exc, "RETRYABLE", True) is False`; other falsy values do not skip), after unwrapping `TaskException`. Write errors set it to `False`, so a workflow node is not re-run after a Not applied or Unknown write. | `src/codemie/core/workflow_models/workflow_models.py` — `custom_retry_on` |
| Tag concurrency | Additive only: read categories, write the union. Graph sends the message etag as `If-Match` and handles a 412 with one reread and recompute, then `CategoryConflictError`. A conflict that finds no submission left (earlier throttled reads used them) still ends as `CategoryConflictError`, never as rate limiting: `Retry.on_exhausted` carries the error to raise. Whether Graph enforces `If-Match` on messages is unverified, so no concurrency guarantee is claimed for the Graph transport. EWS uses `NEVER_OVERWRITE` (a stale changekey raises `ErrorIrresolvableConflict`) and is the verified one. | `graph_client.py` module docstring; `ews_client.py` — `NEVER_OVERWRITE` |
| EWS mailbox scope | EWS item ids are not scoped to a mailbox, so every EWS read before a write also fetches `parent_folder_id` and walks it up (one `GetFolder` per level, at most 32) to a folder proven to be in the configured mailbox. Anything else (another mailbox, a loop, too deep, unreadable) is Not applied `WriteMessageNotFoundError` with the text of a missing id; one WARNING logs the op and a reason code, never ids. A transport failure during the check is a read failure, not a verdict. Send and the move target need no check. Hard delete has a minimal pre-read so it is covered too. Deployment: give the service account rights on the configured mailbox only; the code check is the second line of defence. Graph URLs already include the mailbox. | `ews_client.py` — `_fetch_for_write`, `_require_in_mailbox`, `_mailbox_rejection`, `_known_folder_ids` |
| EWS send identity | `send` and `forward` set From to the configured shared mailbox and rely on the service account having delegate Send As on it. Send on Behalf would stamp the service account and does not meet the requirement. Live check outstanding: unit tests cannot prove it. | `ews_client.py` — `_shared_mailbox`; `ErrorSendAsDenied` maps to a Not applied access-denied error |
