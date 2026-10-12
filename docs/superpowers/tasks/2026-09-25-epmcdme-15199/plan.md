# EPMCDME-15199 Exchange Platform Enablement (Slice 1) Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use `- [ ]` syntax.

**Goal:** Register the MS_EXCHANGE credential type and toolkit, a dual-path `ExchangeToolConfig`, encrypted secrets, a reversible enum migration, a default-off `features:exchangePolling` declaration and connector docs. This slice adds no protocol code.

**Architecture:** The toolkit is autodiscovered in `codemie_tools` (ITSM shape). The config follows the Email precedent but has no `auth_type` default. Settings wiring reuses the `base_settings.py` encrypt/mask paths. The migration follows `8b2c1a4d5e6f_add_ms_teams_credential_type.py`. The flag is one `SettingDeclaration`.

**Tech Stack:** Python, Pydantic v2, Alembic, pytest + `unittest.mock`.

Commit per task using the repository's existing convention.

## Global Constraints
- `CredentialTypes.MS_EXCHANGE = "MSExchange"`; `ToolSet.MS_EXCHANGE = "Microsoft Exchange"`; the PG label is `MS_EXCHANGE`.
- No authentication method is selected by default. Do not use `get_tool_default` and do not add an `"ntlm"` fallback. The schema's `"default": null` for `auth_type` is allowed.
- Secrets are only `oauth_client_secret` and `service_account_password`.
- Add no new dependencies and no Exchange protocol code. Do not change the Email toolkit, `_validate_minimum_hourly_frequency` or codemie-ui.
- Test credentials are obvious fixtures such as `"test-secret"`.

---

### Task 1: Enums and autodiscovered Exchange toolkit

**Files:**
- Modify: `src/codemie_tools/base/models.py`. Add `MS_EXCHANGE = "MSExchange"` after `MS_TEAMS` in `CredentialTypes` (~line 68 onward). Add `MS_EXCHANGE = "Microsoft Exchange"` after `SHAREPOINT` in `ToolSet` (~line 118 onward).
- Create: `src/codemie_tools/ms_exchange/__init__.py`, `src/codemie_tools/ms_exchange/toolkit.py`
- Test: `tests/codemie_tools/ms_exchange/test_toolkit.py`

**Interfaces:** Produces `CredentialTypes.MS_EXCHANGE`, `ToolSet.MS_EXCHANGE`, `ExchangeToolkitUI` and `ExchangeToolkit`.

Test-first: yes — the provider scan (as in `tests/codemie_tools/base/test_toolkit_provider.py`, with the `lru_cache` cleared) includes `ExchangeToolkit`. Its definition has `toolkit == ToolSet.MS_EXCHANGE` and `tools == []`. `CredentialTypes.MS_EXCHANGE.value == "MSExchange"`, and `ToolSet.MS_EXCHANGE.value == "Microsoft Exchange"`. `get_tools_info` in `src/codemie/service/tools/tools_info_service.py` returns the Exchange toolkit when the enterprise and provider sources are patched to empty. This covers AC2, and the function has no existing tests.

- [ ] Write the failing tests and run `poetry run pytest tests/codemie_tools/ms_exchange/test_toolkit.py -v`. Expect FAIL (AttributeError or missing toolkit).
- [ ] Add the enum members. Create `toolkit.py` by mirroring `src/codemie_tools/itsm/toolkit.py`: `ExchangeToolkitUI(ToolKit)` with `toolkit=ToolSet.MS_EXCHANGE`, `tools=[]` and label `"Microsoft Exchange"`, and `ExchangeToolkit(DiscoverableToolkit).get_definition()` returning it. Add no entitlement gating.
- [ ] Re-run. Expect PASS.

### Task 2: ExchangeToolConfig dual-path validation

**Files:**
- Create: `src/codemie_tools/ms_exchange/models.py`
- Test: `tests/codemie_tools/ms_exchange/test_models.py` (shape follows `tests/codemie_tools/notification/email/test_models.py`)

**Interfaces:** Consumes `CredentialTypes.MS_EXCHANGE`. Produces `ExchangeAuthType` and `ExchangeToolConfig`.

Test-first: yes — each of these fails:
- A config without `auth_type`, and one with `auth_type=None`, each raise `ValidationError` whose message contains `auth_type`. No authentication method is selected by default.
- `mailbox` missing, `""` and `"   "` each raise an error naming `mailbox`. Parametrize these three cases across both auth paths, with each path otherwise valid. `RequiredField` supplies a `""` default and does not reject empty values at construction.
- For `oauth_azure`, parametrize over the three `oauth_*` fields: dropping each one raises an error naming it.
- For `ntlm`, parametrize over `ews_url`, `service_account_username` and `service_account_password` the same way.
- Valid configs for both paths pass, and fields of the other path are ignored.
- `model_json_schema()` marks only the two secrets `sensitive`.

- [ ] Write the tests and run them. Expect FAIL (ImportError).
- [ ] Implement:

```python
class ExchangeAuthType(str, Enum):
    OAUTH_AZURE = "oauth_azure"
    NTLM = "ntlm"

_PATH_FIELDS = {
    ExchangeAuthType.OAUTH_AZURE: ("oauth_tenant_id", "oauth_client_id", "oauth_client_secret"),
    ExchangeAuthType.NTLM: ("ews_url", "service_account_username", "service_account_password"),
}

class ExchangeToolConfig(CodeMieToolConfig):
    credential_type: CredentialTypes = Field(default=CredentialTypes.MS_EXCHANGE, exclude=True, frozen=True)
    auth_type: Optional[ExchangeAuthType] = Field(default=None, description="oauth_azure (Graph) or ntlm (EWS)")
    mailbox: str = RequiredField(description="Target mailbox address")
    oauth_tenant_id: Optional[str] = Field(default=None)
    oauth_client_id: Optional[str] = Field(default=None)
    oauth_client_secret: Optional[str] = Field(default=None, json_schema_extra={"sensitive": True})
    ews_url: Optional[str] = Field(default=None)
    service_account_username: Optional[str] = Field(default=None)
    service_account_password: Optional[str] = Field(default=None, json_schema_extra={"sensitive": True})

    @model_validator(mode="after")
    def _validate_selected_path(self):
        if self.auth_type is None:
            raise ValueError("auth_type is required (oauth_azure or ntlm)")
        if not (self.mailbox or "").strip():
            raise ValueError("mailbox is required")
        for name in _PATH_FIELDS[self.auth_type]:
            if not getattr(self, name):
                raise ValueError(f"{name} is required when auth_type={self.auth_type.value}")
        return self
```

Copy the exact `RequiredField`, `credential_type` and `description` kwargs from `notification/email/models.py`. Add no polling or sync-state fields.
- [ ] Re-run. Expect PASS.

### Task 3: Settings wiring for encryption, masking and injection

**Files:**
- Modify: `src/codemie/service/settings/settings.py`
  - Lines 134-161: append `"oauth_client_secret"` and `"service_account_password"` to `LIST_OF_SENSITIVE_FIELDS`. Keep the existing `client_secret` key.
  - Lines 228-258: add `ExchangeToolConfig: CredentialTypes.MS_EXCHANGE` to `__CREDENTIAL_CONFIG_TO_TYPE`.
- Test: `tests/codemie/service/settings/test_exchange_settings.py` (patterns from `test_settings_service.py` and `test_sharepoint_auth_switch.py`)

**Interfaces:** Consumes `ExchangeToolConfig`.

Test-first: yes — for each auth path:
- Saved credential values have the secret field encrypted and differing from `"test-secret"`.
- `hide_sensitive_fields` returns `MASKED_VALUE` for the secret.
- `get_config(..., ExchangeToolConfig)` returns an instance with the decrypted `"test-secret"`.
- `mailbox` and `ews_url` stay plaintext.

- [ ] Write the tests and run them. Expect FAIL (`get_config` raises ValueError for the unmapped class, and the secret is not encrypted).
- [ ] Apply the two edits. Add no `SettingsTester` handler.
- [ ] Re-run. Expect PASS.

### Task 4: Reversible credentialtypes enum migration

**Files:**
- Create: `src/external/alembic/versions/<rev>_add_ms_exchange_credential_type.py`
- Test: `tests/external/alembic/test_add_ms_exchange_credential_type.py`

**Interfaces:** Consumes the `MS_EXCHANGE` label.

Test-first: yes — load the migration module with `importlib.util.spec_from_file_location` and patch its `op`.
- `upgrade()` calls `sync_enum_values` once, with `new_values == PRE + ["MS_EXCHANGE"]`, `affected_columns=[TableReference("codemie","settings","credential_type")]` and `enum_values_to_rename=[]`.
- `downgrade()` calls `op.execute` with the `DELETE ... WHERE credential_type='MS_EXCHANGE'` before `sync_enum_values(new_values == PRE)`, checked via `mock.mock_calls` order.
- `PRE` equals the enum list of the `down_revision` migration.

- [ ] Run `poetry run alembic heads` and set `down_revision` to the single head. Build `PRE` from that head's current label list. `fa14587c0de1`'s `_ENUM_VALUES_FOLDED` is the last known state; include any later additions. If there are multiple heads, stop and report.
- [ ] Write the test and run it. Expect FAIL (file missing).
- [ ] Write the migration by mirroring `8b2c1a4d5e6f_add_ms_teams_credential_type.py`. Use `op.sync_enum_values` only, never raw `ALTER TYPE`.
- [ ] Re-run. Expect PASS. Re-run `alembic heads` right before merge and rebase `down_revision` if `!4044` or EPMCDME-7171 landed first.

### Task 5: features:exchangePolling declaration

**Files:**
- Modify: `src/codemie/service/customer_config_declarations.py:158-171`. Add `EXCHANGE_POLLING = SettingDeclaration(component_id="features:exchangePolling", label="Exchange polling", description="Controls future Exchange polling.", fields=[FieldDeclaration(name="enabled", type=FieldType.SWITCH, label="Enable Exchange polling")])` and append it to `DECLARATIONS`.
- Test: `tests/codemie/service/test_customer_config_declarations.py`. Extend it, reusing fixtures from `tests/codemie/configs/test_customer_config_override.py`.

Test-first: yes — `features:exchangePolling` is in the declaration registry with one SWITCH `enabled` field. With no YAML component and no override, `customer_config.is_feature_enabled("exchangePolling")` is False. After `save_setting` of `enabled=True` (cache cleared), it is True, and the component appears in `resolve_components()`, which `GET /v1/config` serves.

- [ ] Write the tests and run them. Expect FAIL (component not declared).
- [ ] Add the declaration. Confirm that no shipped customer-config YAML enables `exchangePolling`. Add no backend reader of the flag.
- [ ] Re-run. Expect PASS.

### Task 6: Native-connector platform-requirements guide

**Files:** Modify: `.ai-run/guides/agents/custom-tool-creation.md`

Test-first: no — documentation only.

- [ ] Append `## Adding a Native Connector (platform requirements)`, containing:
  - The statement that a native toolkit is not a runtime plug-in.
  - The five requirements from spec Design §8: closed-enum members in the same PR; the `sync_enum_values` migration with delete-then-shrink downgrade and the `alembic heads` check before writing and before merging; the settings wiring; the hand-authored codemie-ui `CREDENTIAL_UI_MAPPING` entry; and a backend and UI redeploy.
  - A note that Email/IMAP (`!4044`) and Exchange coexist as separate credential types.
  - MS_EXCHANGE (EPMCDME-15199) named as the reference implementation, and REQ-3.1.9 noted as an external requirement ID.

---

## Negative-constraint pass
| Constraint | Honored by |
|---|---|
| No authentication method selected by default; no `get_tool_default` or `ntlm` fallback | Task 2 (tests for a missing auth type and for `auth_type=None`) |
| No polling or sync-state field on the config | Task 2 |
| No protocol code, no new deps, no tools or stubs (`tools=[]`) | Tasks 1 and 2 |
| No raw `ALTER TYPE`; downgrade touches only MS_EXCHANGE rows | Task 4 |
| Do not rename the existing `client_secret` sensitive key | Task 3 |
| No `SettingsTester` handler | Task 3 |
| No backend consumer of the flag; no polling design implied; no change to `_validate_minimum_hourly_frequency` or cron | Task 5 |
| No Email toolkit change, no second Graph credential type, no UI code | No task touches these |

## Follow-up handoff: codemie-ui

This is not an implementation task and does not count toward the task total. It records the UI-side work agreed for after implementation:
- A `settingsUIConfig` entry with serverEnum `'MSExchange'` and displayName `'Microsoft Exchange'`, matching the Tools label.
- Dual-path auth form fields.
- `LIST_OF_SENSITIVE_FIELDS` entries.
- An SDK enum bump.
