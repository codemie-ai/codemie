# EPMCDME-12699: AWS Session Token Removal Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two bugs that prevent a user from clearing an optional sensitive credential field (e.g. `aws_session_token`) when editing an AWS integration.

**Architecture:** One pre-filter line added in `update_settings` (write path) strips empty sensitive fields before the key-pruning step so they are treated as "absent" and deleted from stored credentials. One guard added in `hide_sensitive_fields` (read path) prevents empty/None values from being masked as `**********` in GET responses.

**Tech Stack:** Python 3, SQLModel, pytest, `unittest.mock` (already used in the test file)

---

## File Map

| File | Change |
|---|---|
| `src/codemie/service/settings/settings.py` | Add pre-filter in `update_settings` after `_prepare_cred_values` call |
| `src/codemie/service/settings/base_settings.py` | Add `and cred.value` guard in `hide_sensitive_fields` |
| `tests/codemie/service/settings/test_settings_service.py` | Append four new tests |

---

### Task 1: Write failing tests for the write-path bug

**Files:**
- Modify: `tests/codemie/service/settings/test_settings_service.py`

**Context:** `SettingsService.update_settings` calls `Settings.get_by_id` and `Settings.update` — both must be mocked. `_encrypt_fields` encrypts sensitive values, so mock it to return the input unchanged. `check_alias_unique` and `check_webhook_unique` must also be mocked to avoid DB calls.

- [ ] **Step 1: Append three failing tests to `tests/codemie/service/settings/test_settings_service.py`**

Add the following at the end of the file:

```python
# ---------------------------------------------------------------------------
# update_settings: clearing sensitive fields
# ---------------------------------------------------------------------------

def _make_aws_setting_with_token():
    """AWS setting that already has a session token stored."""
    return Settings(
        id="aws-setting-id",
        user_id="user123",
        project_name="test_project",
        alias="my-aws",
        credential_type=CredentialTypes.AWS,
        credential_values=[
            CredentialValues(key="aws_access_key_id", value="enc_access_key"),
            CredentialValues(key="aws_secret_access_key", value="enc_secret_key"),
            CredentialValues(key="aws_session_token", value="enc_session_token"),
            CredentialValues(key="region", value="us-east-1"),
        ],
        setting_type=SettingType.USER,
    )


def _make_update_request(session_token_value):
    """SettingRequest that submits the four AWS fields with the given session_token_value."""
    from codemie.rest_api.models.settings import SettingRequest
    return SettingRequest(
        project_name="test_project",
        alias="my-aws",
        credential_type=CredentialTypes.AWS,
        credential_values=[
            CredentialValues(key="aws_access_key_id", value="enc_access_key"),
            CredentialValues(key="aws_secret_access_key", value="enc_secret_key"),
            CredentialValues(key="aws_session_token", value=session_token_value),
            CredentialValues(key="region", value="us-east-1"),
        ],
    )


@patch.object(SettingsService, '_clear_litellm_user_credentials_cache_if_needed')
@patch.object(SettingsService, 'check_webhook_unique')
@patch.object(SettingsService, 'check_alias_unique')
@patch.object(SettingsService, '_encrypt_fields', side_effect=lambda creds, **_: creds)
@patch.object(Settings, 'update')
@patch.object(Settings, 'get_by_id')
def test_update_settings_clears_sensitive_field_when_value_is_empty_string(
    mock_get_by_id, mock_update, mock_encrypt, mock_alias, mock_webhook, mock_cache
):
    """Submitting empty string for a sensitive field removes it from stored credentials."""
    mock_get_by_id.return_value = _make_aws_setting_with_token()

    SettingsService.update_settings(
        credential_id="aws-setting-id",
        request=_make_update_request(""),
        user_id="user123",
    )

    stored = mock_get_by_id.return_value
    stored_keys = [c.key for c in stored.credential_values]
    assert "aws_session_token" not in stored_keys


@patch.object(SettingsService, '_clear_litellm_user_credentials_cache_if_needed')
@patch.object(SettingsService, 'check_webhook_unique')
@patch.object(SettingsService, 'check_alias_unique')
@patch.object(SettingsService, '_encrypt_fields', side_effect=lambda creds, **_: creds)
@patch.object(Settings, 'update')
@patch.object(Settings, 'get_by_id')
def test_update_settings_clears_sensitive_field_when_value_is_none(
    mock_get_by_id, mock_update, mock_encrypt, mock_alias, mock_webhook, mock_cache
):
    """Submitting None for a sensitive field removes it from stored credentials."""
    mock_get_by_id.return_value = _make_aws_setting_with_token()

    SettingsService.update_settings(
        credential_id="aws-setting-id",
        request=_make_update_request(None),
        user_id="user123",
    )

    stored = mock_get_by_id.return_value
    stored_keys = [c.key for c in stored.credential_values]
    assert "aws_session_token" not in stored_keys


@patch.object(SettingsService, '_clear_litellm_user_credentials_cache_if_needed')
@patch.object(SettingsService, 'check_webhook_unique')
@patch.object(SettingsService, 'check_alias_unique')
@patch.object(SettingsService, '_encrypt_fields', side_effect=lambda creds, **_: creds)
@patch.object(Settings, 'update')
@patch.object(Settings, 'get_by_id')
def test_update_settings_preserves_existing_sensitive_field_when_masked(
    mock_get_by_id, mock_update, mock_encrypt, mock_alias, mock_webhook, mock_cache
):
    """Submitting the masked sentinel preserves the existing encrypted value (no regression)."""
    mock_get_by_id.return_value = _make_aws_setting_with_token()

    SettingsService.update_settings(
        credential_id="aws-setting-id",
        request=_make_update_request("**********"),
        user_id="user123",
    )

    stored = mock_get_by_id.return_value
    token_cred = next((c for c in stored.credential_values if c.key == "aws_session_token"), None)
    assert token_cred is not None
    assert token_cred.value == "enc_session_token"
```

- [ ] **Step 2: Run the three new tests to confirm they fail**

```bash
poetry run pytest tests/codemie/service/settings/test_settings_service.py \
  -k "test_update_settings_clears_sensitive_field_when_value_is_empty_string or \
      test_update_settings_clears_sensitive_field_when_value_is_none or \
      test_update_settings_preserves_existing_sensitive_field_when_masked" \
  -v
```

Expected: `FAILED` for the first two (session_token still present), `PASSED` for the third (regression baseline).

---

### Task 2: Fix the write path in `update_settings`

**Files:**
- Modify: `src/codemie/service/settings/settings.py:439-445`

**Context:** `_prepare_cred_values` at line 439 returns `prepared_creds`. Insert one list-comprehension filter immediately after it, before the key-pruning block at line 441. The filter removes any entry where the key is in `LIST_OF_SENSITIVE_FIELDS` and the value is `None` or `""`. Step A's existing key-pruning then removes those keys from stored `credential_values` because they are absent from `prepared_cred_keys`.

- [ ] **Step 3: Apply the one-line pre-filter in `update_settings`**

Current code (`settings.py`, lines 439–445):

```python
        prepared_creds = cls._prepare_cred_values(request.credential_type, request.credential_values)

        # Remove credentials that are no longer in the prepared credentials
        prepared_cred_keys = [cred.key for cred in prepared_creds]
        user_setting.credential_values = [
            cred for cred in user_setting.credential_values if cred.key in prepared_cred_keys
        ]
```

Replace with:

```python
        prepared_creds = cls._prepare_cred_values(request.credential_type, request.credential_values)

        # Strip sensitive fields with empty values so Step A treats them as absent and deletes them
        prepared_creds = [
            c for c in prepared_creds
            if not (c.key in cls.LIST_OF_SENSITIVE_FIELDS and c.value in (None, ""))
        ]

        # Remove credentials that are no longer in the prepared credentials
        prepared_cred_keys = [cred.key for cred in prepared_creds]
        user_setting.credential_values = [
            cred for cred in user_setting.credential_values if cred.key in prepared_cred_keys
        ]
```

- [ ] **Step 4: Run the three write-path tests to confirm they now pass**

```bash
poetry run pytest tests/codemie/service/settings/test_settings_service.py \
  -k "test_update_settings_clears_sensitive_field_when_value_is_empty_string or \
      test_update_settings_clears_sensitive_field_when_value_is_none or \
      test_update_settings_preserves_existing_sensitive_field_when_masked" \
  -v
```

Expected: all three `PASSED`.

- [ ] **Step 5: Run the full settings test suite to check for regressions**

```bash
poetry run pytest tests/codemie/service/settings/ -v
```

Expected: all tests `PASSED`.

- [ ] **Step 6: Commit the write-path fix**

```bash
git add src/codemie/service/settings/settings.py \
        tests/codemie/service/settings/test_settings_service.py
git commit -m "EPMCDME-12699: Remove empty sensitive credentials on update"
```

---

### Task 3: Write failing test for the read-path bug

**Files:**
- Modify: `tests/codemie/service/settings/test_settings_service.py`

**Context:** `hide_sensitive_fields` lives in `BaseSettingsService` but is inherited by `SettingsService`. It iterates `data.credential_values` and replaces the value of any key in `LIST_OF_SENSITIVE_FIELDS` with `"**********"`. The test calls it directly on a `Settings` object — no mocks needed.

- [ ] **Step 7: Append one failing test**

```python
# ---------------------------------------------------------------------------
# hide_sensitive_fields: empty values must not be masked
# ---------------------------------------------------------------------------

def test_hide_sensitive_fields_does_not_mask_empty_string():
    """An empty-string sensitive field must not be replaced with the masked sentinel."""
    setting = Settings(
        id="test-id",
        user_id="user123",
        project_name="test_project",
        alias="test",
        credential_type=CredentialTypes.AWS,
        credential_values=[
            CredentialValues(key="aws_session_token", value=""),
        ],
        setting_type=SettingType.USER,
    )

    result = SettingsService.hide_sensitive_fields(setting)

    token_cred = next(c for c in result.credential_values if c.key == "aws_session_token")
    assert token_cred.value == ""
```

- [ ] **Step 8: Run the new test to confirm it fails**

```bash
poetry run pytest tests/codemie/service/settings/test_settings_service.py \
  -k "test_hide_sensitive_fields_does_not_mask_empty_string" -v
```

Expected: `FAILED` — `assert "" == "**********"` (value is being masked incorrectly).

---

### Task 4: Fix the read path in `hide_sensitive_fields`

**Files:**
- Modify: `src/codemie/service/settings/base_settings.py:80-86`

**Context:** The masking condition must skip credentials whose value is falsy (`None` or `""`). Add `and cred.value` to the existing `if` condition.

- [ ] **Step 9: Apply the guard in `hide_sensitive_fields`**

Current code (`base_settings.py`, lines 80–86):

```python
    @classmethod
    def hide_sensitive_fields(cls, data: SettingsBase, force_all: bool = False):
        for cred in data.credential_values:
            if (force_all or cred.key in cls.LIST_OF_SENSITIVE_FIELDS) and not any(
                cred.value == as_is for as_is in PASSTHROUGH_PHRASES
            ):
                cred.value = cls.MASKED_VALUE
        return data
```

Replace with:

```python
    @classmethod
    def hide_sensitive_fields(cls, data: SettingsBase, force_all: bool = False):
        for cred in data.credential_values:
            if (force_all or cred.key in cls.LIST_OF_SENSITIVE_FIELDS) and cred.value and not any(
                cred.value == as_is for as_is in PASSTHROUGH_PHRASES
            ):
                cred.value = cls.MASKED_VALUE
        return data
```

- [ ] **Step 10: Run the read-path test to confirm it passes**

```bash
poetry run pytest tests/codemie/service/settings/test_settings_service.py \
  -k "test_hide_sensitive_fields_does_not_mask_empty_string" -v
```

Expected: `PASSED`.

- [ ] **Step 11: Run the full settings test suite to check for regressions**

```bash
poetry run pytest tests/codemie/service/settings/ -v
```

Expected: all tests `PASSED`.

- [ ] **Step 12: Commit the read-path fix**

```bash
git add src/codemie/service/settings/base_settings.py \
        tests/codemie/service/settings/test_settings_service.py
git commit -m "EPMCDME-12699: Do not mask empty sensitive fields in GET responses"
```

---

### Task 5: Run quality gates

**Files:** none (validation only)

- [ ] **Step 13: Run the full test suite**

```bash
poetry run pytest tests/ -v
```

Expected: all tests `PASSED`.

- [ ] **Step 14: Run lint**

```bash
poetry run ruff check src/ tests/
```

Expected: no errors. If there are any, fix them before proceeding.

- [ ] **Step 15: Run format check**

```bash
poetry run ruff format --check src/ tests/
```

Expected: no reformatting needed. If files need reformatting, run `poetry run ruff format src/ tests/` then re-commit the affected files.

---

### Test-first summary

| Task | Test-first | Failing test description |
|---|---|---|
| Task 1 | yes | `aws_session_token` still present after update with `""` or `None` |
| Task 3 | yes | `hide_sensitive_fields` replaces `""` with `**********` |
