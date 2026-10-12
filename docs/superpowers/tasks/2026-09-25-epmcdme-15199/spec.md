# Spec: EPMCDME-15199 — Exchange platform enablement (Slice 1 of 3)

## Goal

Install the Exchange credential type, its dual-path config shape, toolkit registration, a reversible enum migration and a default-off polling flag, so an Exchange integration can be configured and its secrets held encrypted before any transport exists. No protocol code, no new dependencies. Releases together with Slice 2 (EPMCDME-15200).

## Design

1. **Enums** (`src/codemie_tools/base/models.py`): add `CredentialTypes.MS_EXCHANGE = "MSExchange"` and `ToolSet.MS_EXCHANGE = "Microsoft Exchange"` in the same PR as the toolkit class. The PG label is the member `.name` (`MS_EXCHANGE`). `CredentialTypes.MS_EXCHANGE`'s `.value` is `"MSExchange"` — compact CamelCase with no separator, following the existing multi-word precedent `MS_TEAMS = "MSTeams"`, `AZURE_DEVOPS = "AzureDevOps"`, `SHAREPOINT = "SharePoint"`, `ZEPHYR_SCALE = "ZephyrScale"` (`src/codemie_tools/base/models.py:68-116`). This is the key the codemie-ui `CREDENTIAL_UI_MAPPING` object derives via `.value.toLowerCase()`. It is distinct from `ToolSet.MS_EXCHANGE`'s `.value`, `"Microsoft Exchange"` — the UI toolkit-selector label — and neither value substitutes for the other.
2. **`ExchangeToolConfig`** (new package `src/codemie_tools/ms_exchange/`, extending `CodeMieToolConfig`; `credential_type` frozen and excluded, as in `notification/email/models.py`):
   - `auth_type: Optional[ExchangeAuthType] = Field(default=None)`, where `ExchangeAuthType(str, Enum)` is `oauth_azure | ntlm`. There is no default, no `get_tool_default` and no fallback.
   - `mailbox` is a `RequiredField`. It is not Azure-OAuth-specific, so it stays unprefixed.
   - Graph/OAuth path: `oauth_tenant_id`, `oauth_client_id`, `oauth_client_secret` — all `Field(default=None, ...)`. Never bare `Field(...)`. This `oauth_*` prefix matches the sibling `!4044` (EPMCDME-12864) `EmailToolConfig` naming rather than the ticket's literal `tenant_id`/`client_id`/`client_secret` — see Decisions.
   - EWS/NTLM path: `ews_url`, `service_account_username`, `service_account_password` — all `Field(default=None, ...)`.
   - `oauth_client_secret` and `service_account_password` carry `json_schema_extra={"sensitive": True}`. The other fields are identifiers, not secrets.
   - `@model_validator(mode="after")`: a missing `auth_type` raises an error naming `auth_type` as required. Otherwise, each missing field of the selected path raises an error naming that field (`oauth_tenant_id`, `oauth_client_id`, `oauth_client_secret` for `oauth_azure`; `ews_url`, `service_account_username`, `service_account_password` for `ntlm`). Fields of the other path are ignored.
   - No polling or sync-state field belongs on this config. Any cursor, last-poll-time or sync-state a future mail-poll trigger needs lives on its own trigger record, not on the credential — see Design §6 and Open Risks for why that trigger has no owning ticket yet.
3. **Toolkit**: `ExchangeToolkitUI(ToolKit)` with `toolkit=ToolSet.MS_EXCHANGE` and `tools=[]`, plus `ExchangeToolkit(DiscoverableToolkit)` whose `get_definition()` returns it. Shape follows `src/codemie_tools/itsm/toolkit.py`. There is no entitlement gating, so it shows up in `get_tools_info` alongside Jira and ServiceNow.
4. **Settings wiring** (`src/codemie/service/settings/settings.py`):
   - Map `ExchangeToolConfig -> CredentialTypes.MS_EXCHANGE` in `__CREDENTIAL_CONFIG_TO_TYPE` (lines 228-258).
   - Add `oauth_client_secret` and `service_account_password` to `LIST_OF_SENSITIVE_FIELDS` (lines 134-161). The list already carries `client_secret` for other credential types (e.g. the Email toolkit) — add the new `oauth_client_secret` key, not a rename of the existing one, since other types still use the unprefixed key.
   - Encryption, masking (`MASKED_VALUE`) and decryption use the existing `base_settings.py` paths unchanged.
   - `SettingsTester` gets no handler, so "Test integration" raises `SettingsTesterHandlerNotFound` until EPMCDME-15200 adds the Test-button healthcheck for the two read transports.
5. **Migration** (`src/external/alembic/versions/`), following `8b2c1a4d5e6f_add_ms_teams_credential_type.py`:
   - **Head risk (must-fix):** as of this writing, `!4044` (EPMCDME-12864, the generic Email/IMAP toolkit) has its own migration chain `e90eeb6a2d92 -> d3e4f5a6b7c8`, forking from `main`'s current head `u2v3w4x5y6a7` — both off the common ancestor `d8e4f1a2b6c3`. EPMCDME-7171 (AWS_MFA) is a second, independent fork candidate. Run `poetry run alembic heads` immediately before writing this migration, and again immediately before merging it — not only once at the start of implementation. Whichever of this Story, `!4044`, or EPMCDME-7171 merges last must rebase its migration's `down_revision` onto the then-current head.
   - `upgrade` calls `op.sync_enum_values` on `codemie.credentialtypes`, with the full label list as of the verified head plus `MS_EXCHANGE`, `affected_columns=[TableReference('codemie','settings','credential_type')]` and `enum_values_to_rename=[]`.
   - `downgrade` runs `DELETE FROM codemie.settings WHERE credential_type='MS_EXCHANGE'` first, then syncs back to exactly the pre-migration list.
6. **Feature flag — `features:exchangePolling`**: add `EXCHANGE_POLLING = SettingDeclaration(component_id="features:exchangePolling", ...)` with one `enabled` SWITCH field to `DECLARATIONS` in `src/codemie/service/customer_config_declarations.py`.
   - **Scope of this slice — declaration only.** This flag gates a future Exchange mail-poll trigger mechanism. Neither its design nor the ticket that will build it is decided yet — see Open Risks. EPMCDME-15200 (Slice 2) does **not** build this: its own Out of Scope and Durable Architectural Constraints sections state verbatim that "polling cadence, batching and deduplication" belong to "the calling flow," and that its toolkit is stateless. "The calling flow" is not currently a ticket. Nothing in this spec should be read as EPMCDME-15200, or any other named ticket, committing to build the poller.
   - **Why a dedicated flag rather than the existing cron scheduler (context only):** the platform's cron scheduler enforces a 1-hour minimum frequency at save time (`scheduler_settings_service._validate_minimum_hourly_frequency`, out of scope) and a 1-minute cron granularity floor regardless, and every tick is a full LLM run (`invoke_assistant`/`invoke_workflow`) with no cheap pre-check — a poor fit for mail polling. This is why the flag is introduced now rather than reused from `features:schedulersView`, not a statement that Slice 1 or Slice 2 builds the replacement. The pattern in `Cron.__watch_stale_indexing` (`src/codemie/triggers/bindings/cron.py:272`) is noted only as one candidate shape of prior art for whoever eventually owns that work — not as a design this Story or EPMCDME-15200 commits to.
   - Default off: the component is absent or disabled in the customer-config YAML, so `is_feature_enabled("exchangePolling")` is False.
   - It can be overridden at runtime via `PUT /config/declarations/features:exchangePolling`, is served by `GET /v1/config`, and needs no redeploy.
   - Nothing in the backend reads this flag in this slice. codemie-ui, and whichever future ticket ends up implementing the mail-poll trigger, will consume it.
7. **Positioning vs. `!4044` (EPMCDME-12864, generic Email/IMAP toolkit):** `!4044` adds a generic IMAP/SMTP "Email" connector under `codemie_tools/email/`. Exchange is the Microsoft-native connector: Graph for Exchange Online, with EWS/NTLM as a required fallback for on-prem estates that have no Azure AD connectivity and where IMAP is often disabled. The two are not competing credential types and do not need reconciling — each has its own `CredentialTypes` member and its own config class. This is unrelated to the EPMCDME-13773 "competing Graph credential type" concern, which is about delegated user OAuth (Authorization Code + PKCE) versus this Story's application-permission / client-credentials model, not about Email vs. Exchange. Extending or modifying the Email toolkit is out of scope here (see Non-goals).
8. **Docs**: add a new section `## Adding a Native Connector (platform requirements)` to `.ai-run/guides/agents/custom-tool-creation.md`. It states that a native toolkit is not a runtime plug-in and that adding one requires:
   1. The closed-enum members `CredentialTypes.<NAME>` and `ToolSet.<NAME>="<UI label>"`, in the same PR as the toolkit class. The `CredentialTypes` name is the DB label, irreversible once rows exist. The `ToolSet` label is persisted in assistant JSON.
   2. An Alembic `op.sync_enum_values()` migration with `.name` labels, whose downgrade deletes the new type's rows and then shrinks the enum. Note that concurrent Stories touching the same enum can fork the migration chain — verify `alembic heads` right before writing and right before merging.
   3. Settings wiring: a `__CREDENTIAL_CONFIG_TO_TYPE` entry, plus the secret keys in `LIST_OF_SENSITIVE_FIELDS`.
   4. A hand-authored codemie-ui `CREDENTIAL_UI_MAPPING` entry.
   5. A redeploy of both backend and UI, since there is no hot registration.

   It also notes, as a worked example of two native connectors coexisting rather than colliding, that the generic Email/IMAP toolkit (`!4044`) and the Microsoft-native Exchange toolkit (this Story) are separate credential types by design, distinguished by protocol and target estate, not a naming collision to resolve.

   The section names MS_EXCHANGE (EPMCDME-15199) as the reference implementation, and notes that REQ-3.1.9 is an external requirement ID.

## Acceptance criteria

The **Repo** column shows where each criterion is satisfied. "UI" means the separate codemie-ui repo, which is out of scope here.

| # | Criterion | Repo |
|---|---|---|
| AC1 | The toolkit provider scan discovers `ExchangeToolkit`, and `ToolSet.MS_EXCHANGE` resolves with no import error. | backend |
| AC2 | `get_tools_info` returns the Exchange toolkit with no entitlement filter. The selector rendering it is checked in the UI with Slice 2. | backend (API); UI (render) |
| AC3 | A config without `auth_type` is rejected with a message naming `auth_type`. | backend |
| AC4 | For `oauth_azure`, a missing `oauth_tenant_id`, `oauth_client_id` or `oauth_client_secret` is rejected, naming that field. For `ntlm`, the same applies to `ews_url`, `service_account_username` and `service_account_password`. | backend |
| AC5 | For both paths, `oauth_client_secret` and `service_account_password` are encrypted at rest, masked on read, and decrypted only into the in-memory `ExchangeToolConfig` returned by `get_config`. | backend |
| AC6 | Selecting an `auth_type` shows only that path's fields, via `shouldShow` predicates with no `auth_type` default. | UI only |
| AC7 | `downgrade` deletes `MS_EXCHANGE` rows before shrinking the enum, and restores exactly the pre-migration label set. No other rows change. | backend |
| AC8 | `features:exchangePolling` resolves False by default, resolves True after a runtime override, and is served through `GET /v1/config`. It gates a mail-poll trigger mechanism not yet assigned to any ticket; this spec makes no claim about what implements the gated behavior or when (see Open Risks). | backend (flag only; consumer unresolved) |
| AC9 | The guide section lists the closed-enum members, the enum migration and the redeploy as requirements. | backend |
| AC10 | Tests cover discovery, enum registration, migration upgrade and downgrade, and validation of both paths. They use fixture-only credentials. | backend |

## Testing

- Use pytest, following `tests/codemie_tools/notification/email/test_models.py`, `tests/codemie_tools/base/test_toolkit_provider.py`, `tests/codemie/service/settings/test_settings_service.py` and `tests/codemie/service/test_customer_config_declarations.py`.
- The migration test mocks `alembic.op`. It asserts the upgrade and downgrade `sync_enum_values` lists (downgrade equals upgrade minus `MS_EXCHANGE`), and that the `DELETE` executes before the shrink. The repo has no real-PostgreSQL harness.
- Credential values are obvious fixtures such as `"test-secret"`.

## Non-goals

- Exchange protocol code: Graph and EWS clients and all mailbox operations (Slice 2, EPMCDME-15200).
- The dependencies azure-identity, msgraph-sdk-python, exchangelib and requests-ntlm (Slice 2).
- Mutating operations (Slice 3).
- The mail-poll trigger mechanism itself, and any backend consumer of `features:exchangePolling` — no ticket currently owns this (see Open Risks); it is not asserted to be EPMCDME-15200's job.
- Any change to `_validate_minimum_hourly_frequency` or the 1-minute cron granularity floor.
- Any polling/sync-state field on `ExchangeToolConfig` — that state belongs to whatever future trigger record eventually implements polling.
- A `SettingsTester` handler for MS_EXCHANGE.
- Tools inside the Exchange toolkit, including metadata stubs.
- IMAP, POP3, OAUTH_AZURE_CERT and Autodiscover.
- A second Graph credential type or duplicate config UX (EPMCDME-13773 coordination).
- Extending or modifying the generic Email/IMAP toolkit (`!4044`, EPMCDME-12864) — Exchange coexists with it as a separate, Microsoft-native connector; the two are not reconciled or merged here.
- codemie-ui code changes: the `CREDENTIAL_UI_MAPPING` entry and flag gating are tracked in that repo.
- An `auth_type` default of any kind.

## Risks

- **Migration head collision (two candidates):** the Alembic head is unverified as of this writing. Two other in-flight changes can fork the chain: EPMCDME-7171 (AWS_MFA) and `!4044`/EPMCDME-12864 (its chain `e90eeb6a2d92 -> d3e4f5a6b7c8` and `main`'s `u2v3w4x5y6a7` both fork off `d8e4f1a2b6c3`). Whichever of the three merges last must rebase `down_revision`. Run `poetry run alembic heads` both right before writing and right before merging this migration.
- **No ticket owns the mail-poll trigger mechanism.** `features:exchangePolling` is being introduced now, declaration-only, to gate a mechanism that does not yet have a design or an assigned ticket. EPMCDME-15200's own Out of Scope and Durable Architectural Constraints sections explicitly assign "cadence, batching and deduplication" to "the calling flow" and state its toolkit is stateless — it does not build the poller. "The calling flow" is not currently a ticket. This is a gap for the product owner to assign, not an implementation detail this spec can settle. The cron-unsuitability rationale and the `Cron.__watch_stale_indexing` pattern remain informational prior art / a candidate approach for whoever picks up that future work — they are not a commitment by this Story or by EPMCDME-15200.
- The mapping from env `FEATURE_EXCHANGE_POLLING` to `features:exchangePolling` is unconfirmed. The YAML default must be verified during implementation. (Carried forward — informational only, doesn't change Slice 1 scope.)
- `_process_module_generic` catches only `ImportError`. A missing enum member would fail loudly, not silently as the ticket says.
- The `"Microsoft Exchange"` label is effectively irreversible once it is saved in assistant JSON.
- Informational, not a Slice 1 scope change: EPMCDME-15200 should evaluate reusing `!4044`'s msal Azure client-credentials code for its Graph read transport, rather than adding `azure-identity` redundantly. No new dependency is added in this slice regardless of that choice.
- **Downstream UI-integration handoff (informational, not this spec's deliverable):** after Stage 5 implementation of this ticket, if `spec.md` turns out to be insufficient on its own for the engineer/agent implementing the codemie-ui side to understand the backend integration contract, a dedicated handoff document will be produced then — separately from, and unrelated to, this repo's own Stage 10 MR-readiness handoff. That artifact is planned post-implementation and contingent on need; it is out of scope for this spec's own acceptance criteria.
