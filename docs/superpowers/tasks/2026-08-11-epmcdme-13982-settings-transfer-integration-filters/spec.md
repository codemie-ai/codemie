# Spec: Settings Transfer Integration Filters (EPMCDME-13982)

## Overview

Extend `POST /v1/settings/transfer` with two optional filters:

1. `type_of_integration` — controls whether user-scoped integrations, project-scoped integrations, or both are included in the transfer.
2. `list` — an optional list of integration aliases; when provided, only those aliases are transferred.

Omitting either field preserves current behaviour (all integrations, all types).

---

## Request Schema

```python
class TypeOfIntegration(BaseModel):
    user_integrations: bool = True
    project_integrations: bool = True

class TransferSettingsRequest(BaseModel):
    source_project_name: str = Field(min_length=1)
    target_project_name: str = Field(min_length=1)
    mode: TransferMode
    type_of_integration: TypeOfIntegration = Field(default_factory=TypeOfIntegration)
    integrations_list: list[str] | None = Field(default=None, alias="list")
```

JSON wire name for the list field is `list` (via alias); the Python attribute is `integrations_list` to avoid shadowing the builtin. `model_config = ConfigDict(populate_by_name=True)` is added so tests can construct the model by Python name.

---

## Validation Rules

| Condition | Error |
|---|---|
| Both `type_of_integration` flags are `False` | 422 — "At least one integration type must be selected." |
| `list` contains aliases not present in the source project candidates | 422 — "Unknown integration identifiers: `<aliases>`." |

The unknown-identifier check compares the requested aliases against the post-type-filter candidate set. An alias that exists in the source project but is excluded by the type filter counts as unknown.

---

## Service Changes (`SettingsTransferService`)

### `_select_candidates` signature

```python
@classmethod
def _select_candidates(
    cls,
    source_project_name: str,
    setting_types: set[SettingType],
    integrations_list: list[str] | None,
) -> list[Settings]:
```

Steps after ES fetch and subsystem-managed exclusion:

1. **Type filter**: keep rows where `row.setting_type in setting_types`.
2. **List filter**: if `integrations_list` is not `None`, keep rows whose `row.alias` is in the requested set, then raise 422 for any requested alias not matched.

### `transfer` signature

```python
@classmethod
def transfer(
    cls,
    source_project_name: str,
    target_project_name: str,
    mode: TransferMode,
    setting_types: set[SettingType],
    integrations_list: list[str] | None,
) -> TransferSettingsResponse:
```

`transfer()` derives `setting_types` from the request's `type_of_integration` before calling `_select_candidates`. All downstream methods (`_partition`, `_validate_aliases_present`, `_validate_no_collisions`, `_apply`) are unchanged.

### Router

Validates `type_of_integration` (both-false check), derives `setting_types`, then passes `setting_types` and `integrations_list` to `SettingsTransferService.transfer()`.

---

## Unchanged Behaviour

- Advisory lock, collision check, and `_apply` are untouched.
- `_partition` (COPY_BLOCKED_TYPES) remains the sole authority for copy-blocking; it is orthogonal to the new type filter.
- The response shape (`TransferSettingsResponse`) is unchanged — transferred/skipped counts and lists already exist.

---

## Acceptance Criteria Coverage

| AC | How covered |
|---|---|
| Filter by user integrations | `SettingType.USER` in `setting_types` |
| Filter by project integrations | `SettingType.PROJECT` in `setting_types` |
| Transfer both in one request | Both flags `True` (default) |
| Optional `list` field | `integrations_list: list[str] | None = None` |
| Omitted `list` → all matching | `integrations_list is None` skips list filter |
| Provided `list` → subset only | list filter in `_select_candidates` |
| Clear response (transferred/skipped/failed) | Existing `TransferSettingsResponse` fields |

---

## Test Coverage

### Router tests (`test_settings_transfer.py`)

- Update all existing `assert_called_once_with` to include `setting_types` and `integrations_list`.
- Add: user-only filter, project-only filter, list provided, list omitted, both-flags-false → 422.

### Service tests (`test_settings_transfer_service.py`)

- Update `TestTransfer` (9 tests) to pass `setting_types` and `integrations_list` to `transfer()`.
- Add `TestSelectCandidatesFiltering`:
  - user-only filter excludes project rows
  - project-only filter excludes user rows
  - both flags → all rows pass
  - list filter returns subset
  - list with unknown alias → 422
  - list with alias excluded by type filter → 422 (counts as unknown)
- Add: both-flags-false → 422 in `transfer()`.
