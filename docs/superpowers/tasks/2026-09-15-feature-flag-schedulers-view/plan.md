# EPMCDME-15013: Schedulers Feature Flag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backend feature flag `features:schedulers` that controls whether the Schedulers navigation item is visible in the CodeMie UI.

**Architecture:** A new `SettingDeclaration` constant (`SCHEDULERS`) is registered in `customer_config_declarations.py` and added to the `DECLARATIONS` tuple. The `GET /v1/config` endpoint already serves all declared components to the frontend (`appInfoStore.configs`), so no API changes are needed. A disabled-by-default YAML entry is added in `customer-config.yaml`.

**Tech Stack:** Python, Pydantic, YAML

**Spec:** https://jiraeu.epam.com/browse/EPMCDME-15013

## Global Constraints

- Feature flag `component_id` must be `"features:schedulers"` (follows existing `features:<camelCaseKey>` namespace).
- Default value of `enabled` must be `false` (hidden by default per AC #5).
- Implementation must not change any existing feature flag behavior.
- No new API endpoints, no schema changes — the existing `/v1/config` endpoint already exposes all declared components.

---

### Task 1: Register `SCHEDULERS` feature flag declaration

**Files:**
- Modify: `src/codemie/service/customer_config_declarations.py`
- Modify: `config/customer/customer-config.yaml`
- Test: `tests/codemie/service/test_customer_config_declarations.py` (create)

**Interfaces:**
- Consumes: `SettingDeclaration`, `FieldDeclaration`, `FieldType` from `customer_config_declarations` module (already imported in the file).
- Produces: `SCHEDULERS: SettingDeclaration` constant; `DECLARATIONS` tuple updated to include it.

- [ ] **Step 1: Write failing test**

```python
# tests/codemie/service/test_customer_config_declarations.py
from codemie.service.customer_config_declarations import DECLARATIONS, SCHEDULERS, by_component_id


def test_schedulers_declaration_registered():
    ids = [d.component_id for d in DECLARATIONS]
    assert "features:schedulers" in ids


def test_schedulers_declaration_has_enabled_switch():
    assert SCHEDULERS.component_id == "features:schedulers"
    assert SCHEDULERS.label == "Schedulers"
    field_names = {f.name for f in SCHEDULERS.fields}
    assert "enabled" in field_names
    from codemie.service.customer_config_declarations import FieldType
    enabled_field = next(f for f in SCHEDULERS.fields if f.name == "enabled")
    assert enabled_field.type is FieldType.SWITCH


def test_by_component_id_finds_schedulers():
    decl = by_component_id("features:schedulers")
    assert decl is not None
    assert decl is SCHEDULERS
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/service/test_customer_config_declarations.py -v
```

Expected: FAIL — `ImportError: cannot import name 'SCHEDULERS'`

- [ ] **Step 3: Add `SCHEDULERS` declaration constant and register it**

In `src/codemie/service/customer_config_declarations.py`, add after the `WEB_SEARCH` block and before `DECLARATIONS`:

```python
SCHEDULERS = SettingDeclaration(
    component_id="features:schedulers",
    label="Schedulers",
    description="Schedulers navigation item and REST API for listing, toggling, and viewing run history.",
    fields=[
        FieldDeclaration(
            name="enabled",
            type=FieldType.SWITCH,
            label="Enable Schedulers",
        ),
    ],
)
```

Update the `DECLARATIONS` tuple to include `SCHEDULERS`:

```python
DECLARATIONS: tuple[SettingDeclaration, ...] = (CHAT_DISCLAIMER, RELEASE_NOTES_RECENT_COUNT, WEB_SEARCH, SCHEDULERS)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/test_customer_config_declarations.py -v
```

Expected: PASS (3 tests).

- [ ] **Step 5: Add disabled-by-default YAML entry**

In `config/customer/customer-config.yaml`, add the `features:schedulers` component entry in the `components` list alongside the other `features:*` entries (e.g. after `features:webSearch`):

```yaml
  - id: "features:schedulers"
    settings:
      enabled: false
      name: "Schedulers"
      description: "Schedulers navigation and REST API"
```

- [ ] **Step 6: Verify the config loads cleanly**

```bash
poetry run python -c "
from codemie.configs.customer_config import customer_config
enabled = customer_config.is_feature_enabled('schedulers')
print('schedulers enabled:', enabled)
assert enabled is False, 'Expected disabled by default'
print('OK')
"
```

Expected output:
```
schedulers enabled: False
OK
```

- [ ] **Step 7: Run broader declaration-related tests**

```bash
poetry run pytest tests/codemie/service/ -k "customer_config" -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/codemie/service/customer_config_declarations.py \
        config/customer/customer-config.yaml \
        tests/codemie/service/test_customer_config_declarations.py
git commit -m "EPMCDME-15013: Add feature flag for Schedulers navigation visibility

Generated with AI

Co-Authored-By: codemie-ai <codemie.ai@gmail.com>"
```

---

## Test-first summary

| Task | Test-first | Failing test description |
|---|---|---|
| Task 1 | yes | `test_schedulers_declaration_registered` — ImportError on missing `SCHEDULERS` constant |
