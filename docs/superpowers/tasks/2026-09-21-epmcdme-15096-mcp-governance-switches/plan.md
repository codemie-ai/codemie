# MCP Governance Switches — mcpCustomServersDisabled Declaration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register `mcpCustomServersDisabled` as a runtime-toggleable `SettingDeclaration` so admins can flip it via the existing customer-config mechanism, with no router, service, resolver, enforcement, or UI changes.

**Architecture:** Single additive entry to the existing `DECLARATIONS` registry in `customer_config_declarations.py`, following the exact single-`SWITCH`-field shape already used by `WEB_SEARCH` and `SCHEDULERS`. The generic router (`PUT/DELETE /v1/config/declarations/{component_id}`), the generic read path (`GET /v1/config`), and the shared resolver (`component_resolution.resolve()`, already consumed by `MCPAccessControlService`) all pick this up automatically once the declaration exists — no other production code changes.

**Tech Stack:** Python, Pydantic (`SettingDeclaration` / `FieldDeclaration` models), pytest.

## Global Constraints

- `component_id` must be byte-for-byte `mcpCustomServersDisabled` — no rename, no `features:` prefix (both `access_control.py` and `codemie-ui/src/constants/mcp.ts` hardcode this exact string; a rename breaks enforcement).
- No changes to `codemie/config/customer/customer-config.yaml`, `rest_api/routers/customer_config.py`, `component_resolution.py`, `access_control.py`, or any UI/SDK code — all already generically support any declared component.
- Commit per task using the repository's existing convention.

---

### Task 1: Add the `mcpCustomServersDisabled` declaration to the registry

**Files:**
- Modify: `src/codemie/service/customer_config_declarations.py:158-171`
- Test: `tests/codemie/service/test_customer_config_declarations.py`

**Interfaces:**
- Consumes: `SettingDeclaration`, `FieldDeclaration`, `FieldType.SWITCH` (all already defined in this file, `src/codemie/service/customer_config_declarations.py:35-105`).
- Produces: module-level constant `MCP_CUSTOM_SERVERS_DISABLED`, added to the `DECLARATIONS` tuple, consumed by `by_component_id("mcpCustomServersDisabled")` and by the shared resolver that `MCPAccessControlService` already calls.

**Test-first: yes — the following three tests fail with `AssertionError`/`None` today because no declaration with this `component_id` exists.**

- [ ] **Step 1: Write the three failing tests**

Append to `tests/codemie/service/test_customer_config_declarations.py` (after `test_by_component_id_finds_schedulers`, mirroring that test's own SCHEDULERS pattern one section above it):

```python
def test_mcp_custom_servers_disabled_declaration_registered():
    ids = [d.component_id for d in DECLARATIONS]
    assert "mcpCustomServersDisabled" in ids


def test_mcp_custom_servers_disabled_has_enabled_switch():
    assert MCP_CUSTOM_SERVERS_DISABLED.component_id == "mcpCustomServersDisabled"
    assert MCP_CUSTOM_SERVERS_DISABLED.label == "MCP Custom Servers Disabled"
    field_names = {f.name for f in MCP_CUSTOM_SERVERS_DISABLED.fields}
    assert field_names == {"enabled"}
    enabled_field = next(f for f in MCP_CUSTOM_SERVERS_DISABLED.fields if f.name == "enabled")
    assert enabled_field.type is FieldType.SWITCH


def test_by_component_id_finds_mcp_custom_servers_disabled():
    decl = by_component_id("mcpCustomServersDisabled")
    assert decl is not None
    assert decl is MCP_CUSTOM_SERVERS_DISABLED
```

Add `MCP_CUSTOM_SERVERS_DISABLED` to the existing import block at the top of the test file (`tests/codemie/service/test_customer_config_declarations.py:17-27`), alongside `SCHEDULERS`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/test_customer_config_declarations.py -k mcp_custom_servers_disabled -v`
Expected: FAIL — `ImportError: cannot import name 'MCP_CUSTOM_SERVERS_DISABLED'` (or, once the import is stubbed out, `AssertionError` on the registered-in-`DECLARATIONS` check).

- [ ] **Step 3: Add the declaration and register it**

In `src/codemie/service/customer_config_declarations.py`, insert a new constant between `SCHEDULERS` (ending at line 169) and the `DECLARATIONS` tuple (line 171):

```python
MCP_CUSTOM_SERVERS_DISABLED = SettingDeclaration(
    component_id="mcpCustomServersDisabled",
    label="MCP Custom Servers Disabled",
    description="When enabled, restricts users to catalog-referenced MCP servers only. Custom inline MCP server configuration is not permitted.",
    fields=[
        FieldDeclaration(
            name="enabled",
            type=FieldType.SWITCH,
            label="Restrict to catalog MCP servers",
        ),
    ],
)
```

Then update the `DECLARATIONS` tuple at line 171 to add it:

```python
DECLARATIONS: tuple[SettingDeclaration, ...] = (
    CHAT_DISCLAIMER,
    RELEASE_NOTES_RECENT_COUNT,
    WEB_SEARCH,
    SCHEDULERS,
    MCP_CUSTOM_SERVERS_DISABLED,
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/test_customer_config_declarations.py -v`
Expected: PASS — all tests in the file, including the three new ones and the pre-existing aggregate tests `test_key_is_derived_from_component_id` and `test_every_declaration_has_a_unique_key` (which cover the new entry automatically, with no changes needed to those tests themselves).

- [ ] **Step 5: Commit**

Commit `src/codemie/service/customer_config_declarations.py` and `tests/codemie/service/test_customer_config_declarations.py` together, using the repository's existing commit-message convention.

---

## Self-Review

**Spec coverage:**
- "Add `MCP_CUSTOM_SERVERS_DISABLED` to `customer_config_declarations.py`, register in `DECLARATIONS`" → Task 1, Step 3.
- Three named tests (`test_mcp_custom_servers_disabled_declaration_registered`, `test_mcp_custom_servers_disabled_has_enabled_switch`, `test_by_component_id_finds_mcp_custom_servers_disabled`) → Task 1, Step 1.
- Admin-only write / everyone-can-read / default-off / enforcement-inherits-toggle / admin-settings-page acceptance criteria (spec §"Why this is sufficient") all follow automatically from registering the declaration in the shared registry — no additional task needed, per the spec's own reasoning and the technical analysis's confirmation that router, resolver, and UI already handle any declared component generically.

**Negative-constraint pass** (spec Non-goals + inline asides):
- "No changes to `rest_api/routers/customer_config.py`, the write/read auth gates, or `component_resolution.py`" — honored: Task 1 touches only `customer_config_declarations.py` and its test file.
- "No changes to `access_control.py` / `MCPAccessControlService`" — honored: not touched.
- "No UI changes in codemie-ui... or SDK changes in codemie-sdk" — honored: not touched.
- "No `FEATURE_*`-style env-var override... explicitly considered and rejected" (would require `features:mcpCustomServersDisabled`) — honored: Task 1's `component_id` is the unprefixed `"mcpCustomServersDisabled"`, verbatim, not `features:`-prefixed.
- "Should not 'improve' this into a `features:`-prefixed id" (technical-analysis.md §6 risk) — honored: same as above.

No task runs the full quality-gate suite, manual/browser verification, code review, or a standalone commit task — those remain the calling flow's own stages.

negative-constraints: all addressed above (none omitted).

---

Plan complete and saved to `docs/superpowers/tasks/2026-09-21-epmcdme-15096-mcp-governance-switches/plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
