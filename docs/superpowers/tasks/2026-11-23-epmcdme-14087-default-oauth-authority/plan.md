# EPMCDME-14087 Default OAuth authority for Email (oauth_azure) Implementation Plan

**Goal:** A blank, `None` or missing `oauth_authority` on an Email integration with `auth_type=oauth_azure` resolves to `https://login.microsoftonline.com`, so MSAL receives `<base>/<tenant>`.

**Architecture:** One module-level helper in `models.py` resolves the authority in this order: explicit non-empty value, then customer `tool_defaults.email.oauth_authority` (read at call time via `get_tool_default`), then the built-in constant. The helper feeds both the model field default (a never-stored key) and `EmailTool._get_oauth_token_azure` (a stored `""` or `None` bypasses the Pydantic default, so the fix must sit where the authority is built).

**Conventions:** Commit per task using the repository's existing convention (commits are a later stage; do not commit here). Backend only. Run only the new tests and the neighbouring email and settings-tester tests, never the full suite. Quality gates come in a later stage.

## Acceptance criteria

- Test passes for a valid Microsoft tenant configuration when Authority is left blank (blank, `None` and missing all resolve).
- No invalid authority (`/<tenant>`) is built from an empty value.
- Basic auth, an explicit authority and the customer `.cn` override are not regressed.
- Regression tests exist for blank, missing, `None`, explicit and override.
- Note: the ticket's `//https://...` prefix was not reproducible from any payload the product sends. It is not part of this fix. The reproduced symptom is the blank authority giving `/<tenant>`.

## Task 1: Authority resolver and model default

**Files:** Modify `src/codemie_tools/notification/email/models.py:35-36,85-89`. Test `tests/codemie_tools/notification/email/test_models.py`.

Test-first: yes — `EmailToolConfig().oauth_authority` is `""` today, and `resolve_oauth_authority` cannot be imported yet.

- [ ] Add the new test functions first (plain pytest, `monkeypatch`, same style as the file). Patch `customer_config.tool_defaults` and call `resolve_oauth_authority`:
  - `""`, `None` and the no-arg call return `DEFAULT_OAUTH_AUTHORITY` when `tool_defaults` is `{}`.
  - An explicit value is returned unchanged (include a `.cn` URL).
  - With `tool_defaults={"email": {"oauth_authority": "https://login.microsoftonline.cn"}}`, blank or `None` returns the `.cn` value, and an explicit value still beats it.
  - `EmailToolConfig().oauth_authority == DEFAULT_OAUTH_AUTHORITY` for a never-stored key. This relies on the shipped YAML having no email override, and it is import-time, so do not monkeypatch for it.
- [ ] In `models.py`, add above the class a module constant `DEFAULT_OAUTH_AUTHORITY = "https://login.microsoftonline.com"` (no trailing slash) and `_TOOL_NAME = "email"`. Set `TOOL_NAME: ClassVar[str] = _TOOL_NAME`.
- [ ] Add the new function:

```python
def resolve_oauth_authority(value: Optional[str] = None) -> str:
    """Explicit value, else customer tool_defaults, else the Microsoft public-cloud authority."""
    return value or get_tool_default(_TOOL_NAME, "oauth_authority") or DEFAULT_OAUTH_AUTHORITY
```

- [ ] Change the `oauth_authority` field default (`models.py:86`) to `resolve_oauth_authority()`. Leave the description and placeholder as they are, because they already state this default. Leave `oauth_scope` untouched.
- [ ] Run `poetry run pytest tests/codemie_tools/notification/email/test_models.py -q`.

## Task 2: Use the resolver where the authority is built

**Files:** Modify `src/codemie_tools/notification/email/tools.py:23-27,59`. Test `tests/codemie_tools/notification/email/test_email_tools.py` (a `unittest.TestCase` file, so use `patch`/`patch.object` rather than `monkeypatch`).

Test-first: yes — with a blank authority, `ConfidentialClientApplication` receives `authority="/<tenant>"` instead of `https://login.microsoftonline.com/<tenant>`.

- [ ] Add a test class or methods that patch `codemie_tools.notification.email.tools.ConfidentialClientApplication`. The mock's `acquire_token_silent` returns `{"access_token": "tok"}`, so there is no network. Build an `oauth_azure` config with an explicit scope and `oauth_tenant_id="tenant-guid"`, call `EmailTool(config=...)._get_oauth_token_azure()`, and assert the `authority` kwarg and that the token is returned. Cases:
  - `oauth_authority=""`, `None`, and key omitted: `https://login.microsoftonline.com/tenant-guid`.
  - Explicit `https://login.microsoftonline.cn`: `https://login.microsoftonline.cn/tenant-guid`.
  - `patch.object(customer_config, "tool_defaults", {"email": {"oauth_authority": "https://custom.example"}})` with a blank authority: `https://custom.example/tenant-guid`. An explicit value still wins.
- [ ] In `tools.py`, import `resolve_oauth_authority` from `codemie_tools.notification.email.models`. Change line 59 to `f"{resolve_oauth_authority(self.config.oauth_authority)}/{self.config.oauth_tenant_id}"`. Leave scope, tenant handling and trimming as they are.
- [ ] Run `poetry run pytest tests/codemie_tools/notification/email -q`.

## Task 3: Test-button path regression

**Files:** Test only, `tests/codemie/service/settings/test_settings_tester.py`, next to `test_test_email_success` (:181).

Test-first: yes — the Test path with a blank authority fails with the MSAL address error until Tasks 1 and 2 land.

- [ ] Add one test that builds a `TestSettingRequest` for `CredentialTypes.EMAIL` with `auth_type=oauth_azure`, `url=smtp.office365.com:587`, a tenant, a client id and secret, `oauth_from_email`, an explicit `oauth_scope`, and `oauth_authority` given as `CredentialValues(key='oauth_authority', value='')`. Patch `codemie_tools.notification.email.tools.ConfidentialClientApplication` (silent token returns `{"access_token": "tok"}`) and `codemie_tools.notification.email.tools.smtplib.SMTP`. Assert `SettingsTester(request)._test_email()` returns `(True, '')` and that the authority is `https://login.microsoftonline.com/<tenant>`.
- [ ] Run `poetry run pytest tests/codemie/service/settings/test_settings_tester.py -k email -q`, then the three files together.

## Negative-constraint pass

- Out-of-scope items are not planned: `oauth_scope` default, tenant-required validation, trailing-slash or whitespace trimming, frontend, YAML or README text. The resolver only does `value or ... or ...`.
- The `tool_defaults` override must keep winning over the built-in default: Tasks 1 and 2 test the order, and the helper reads it at call time.
- No trailing slash on the built-in constant: Task 1 asserts the exact string.
- No full-suite or quality-gate task, and no commits.

## Known follow-ups (not in this plan)

- A blank `oauth_scope` still becomes `[""]` and fails for users who leave it blank.
- A missing tenant still yields `/None`, and there is no required-field validation for the oauth_azure fields.
- A trailing slash or whitespace in the authority is not trimmed (`https://.../` gives `//<tenant>`).
- The UI shows no default in the Authority field: `settingsUIConfig.ts` has no `defaultValue`, and `appInfo.ts` reads only `url` and `auth_type` for email.
- The YAML example `customer-config.yaml:497` includes `<tenant-id>`, and the README and field text are misleading.
