# Spec: Preconfigured Assistant MCP Integration Improvements

**Ticket:** EPMCDME-8996  
**Branch:** EPMCDME-8996_mcp-assistant-creation-update-improvements  
**Slug:** preconfigured-assistant-mcp-improvements  

---

## Problem Statement

The deployment script `src/external/deployment_scripts/preconfigured_assistants.py` bypasses every validation guard that the REST router correctly invokes on user-driven assistant create/update. Specifically:

1. `MCPAccessControlService.sanitize_for_save()` is never called — restricted-mode enforcement, catalog-entry validity checks, and duplicate `mcp_config_id` detection are all absent.
2. `AssistantBase.validate_fields()` is never called — including a missing `name` uniqueness check that does not yet exist on either path.
3. `MCPConfigService.adjust_usage()` is never called — `usage_count` on catalog MCP configs is inaccurate for preconfigured assistants.
4. All tests in `test_preconfigured_assistants.py` use `mcp_servers = []`; multi-MCP scenarios have zero coverage.

At runtime, duplicate `MCPServerDetails.name` values cause ambiguous credential resolution in `MCPToolkitService` (uses `"MCP:{server.name}"` as the per-user integration lookup key), which is a correctness failure.

---

## Solution Design

### Change 1 — Add `_validate_mcp_server_names()` to `AssistantBase.validate_fields()`

**File:** `src/codemie/rest_api/models/assistant.py`

Add a new private method mirroring the existing `_validate_prompt_variables` duplicate-key pattern:

```python
def _validate_mcp_server_names(self) -> Optional[str]:
    names = [s.name for s in (self.mcp_servers or [])]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        return f"Duplicate MCP server names detected: {', '.join(sorted(duplicates))}"
    return None
```

The `or []` guard is required because `validate_fields()` calls this method unconditionally and `mcp_servers` can be `None` on objects read from Elasticsearch. Without the guard, `validate_fields()` raises `TypeError` rather than returning a clean validation error.

Register this check in `validate_fields()` alongside the existing validations. This fix applies to **both** the REST router path and the deployment script path.

### Change 2 — Wire `MCPAccessControlService.sanitize_for_save()` and validation into the deployment script

**File:** `src/external/deployment_scripts/preconfigured_assistants.py`

**Create path** (`create_preconfigured_assistant()`), after fetching the template and before calling `Assistant()`:
1. Validate first: call `validate_fields()` on the template to catch name duplicates and other structural violations.
2. Then sanitize: `validated_mcp_servers = MCPAccessControlService.sanitize_for_save(assistant_template.mcp_servers)`.
3. Use `validated_mcp_servers` (never `assistant_template.mcp_servers`) for the `Assistant()` constructor and `adjust_usage()`.
4. Let `ValidationException` propagate — fail-fast behavior required for restricted-mode deployments.

**Update path** (`update_assistant_content()`), before building `fields_to_check`:
1. Validate first: call `_validate_mcp_server_names()` on the template (**not** `validate_fields()`).
2. Then sanitize: `validated_mcp_servers = MCPAccessControlService.sanitize_for_save(assistant_template.mcp_servers)`.
3. Use `validated_mcp_servers` in `fields_to_check`, `new_ids`, and everywhere else — never write back to `assistant_template.mcp_servers`.
4. Let `ValidationException` propagate — same fail-fast policy.

**Why the update path uses `_validate_mcp_server_names()` and not `validate_fields()` (CR-002):** YAML-loaded template objects are cached in `AssistantService._cached_base_assistant_templates` with `id=None`. `validate_fields()` calls `_check_slug_uniqueness()`, which queries ES for the slug, finds the persisted assistant, and compares `existing.id (UUID) != self.id (None)` — this is always `True`, producing a false-positive slug-conflict `ValidationException` on every update. `sanitize_for_save()` already handles catalog/access-control checks; `_validate_mcp_server_names()` is the only identity-independent check that adds value.

**Why `validated_mcp_servers` local variable (not mutating `assistant_template.mcp_servers`):** The template object is shared across all callers via the in-memory cache. Mutating `assistant_template.mcp_servers` would corrupt the cached template for all subsequent callers within the same process, causing them to see the sanitized (possibly stripped) list instead of the original YAML definition. Always capture the sanitized result as a local variable.

**Behavioral decision (confirmed):** In restricted mode (`mcpCustomServersDisabled=true`) with inline-config templates (no `mcp_config_id`), `sanitize_for_save()` raises `ValidationException`. The startup fails with a clear error. Operators must fix the template to use catalog-referenced configs or disable restricted mode.

### Change 3 — Wire `MCPConfigService.adjust_usage()` into the deployment script for catalog-referenced servers

**File:** `src/external/deployment_scripts/preconfigured_assistants.py`

After a successful `create_preconfigured_assistant()` or `update_assistant_content()` for an assistant that has servers with `mcp_config_id` set, call `MCPConfigService.adjust_usage()` with the appropriate deltas (positive on create, differential on update, negative-to-zero on delete). This mirrors the `_track_mcp_usage_on_create/delete/changes()` logic in the REST router. Since no current templates use `mcp_config_id`, this change has no functional impact today but closes the accounting gap for future catalog-backed templates.

This change is **best-effort**: if `adjust_usage()` raises, log a warning rather than failing the deployment — usage count inaccuracy is non-critical; startup failure for a count mismatch would be worse.

**Ordering invariant (CR-001):** In `update_assistant_content()`, `old_ids` (pre-update `mcp_config_id` set) must be captured from `existing_assistant.mcp_servers` **before** the `setattr` loop that overwrites it. `adjust_usage()` must be called **after** `existing_assistant.save()` succeeds. Calling it before `save()` creates a TOCTOU failure: if the Elasticsearch write raises, PostgreSQL `usage_count` rows are already committed; on the next deployment retry the same delta fires again, causing double-increments or double-decrements.

---

## Affected Files

| File | Change |
|---|---|
| `src/codemie/rest_api/models/assistant.py` | Add `_validate_mcp_server_names()` method and register it in `validate_fields()` |
| `src/external/deployment_scripts/preconfigured_assistants.py` | Call `sanitize_for_save()`, `validate_fields()`, and `adjust_usage()` from `create_preconfigured_assistant()` and `update_assistant_content()` |
| `tests/external/deployment_scripts/test_preconfigured_assistants.py` | Add tests for 1-MCP, 2-MCP, and >2-MCP scenarios (create + update), duplicate name validation, restricted-mode fail-fast, and `sanitize_for_save()` call assertion |
| `tests/codemie/rest_api/models/test_assistant_model.py` | Add `TestAssistantBaseValidation` cases for `_validate_mcp_server_names()` |

---

## Behavioral Decisions

| Decision | Rationale |
|---|---|
| `sanitize_for_save()` fail-fast in restricted mode | Consistent with mcp-integration.md directive; exposes misconfigurations at startup rather than silently at runtime |
| `adjust_usage()` warn-and-continue on failure; called after `save()` with IDs captured before `setattr` | Usage count inaccuracy is non-critical so failure only logs a warning. Calling after `save()` and capturing old IDs before `setattr` prevents double-increment/decrement on retry if the ES write fails. |
| Create path calls `validate_fields()`; update path calls `_validate_mcp_server_names()` directly | `validate_fields()` includes `_check_slug_uniqueness()`, which produces a false-positive slug conflict when `self.id=None` (all YAML-loaded templates). The create path is safe because `existing_assistant=None` means ES has no hit, so `_check_slug_uniqueness()` returns `""`. The update path must avoid the ES query entirely and call `_validate_mcp_server_names()` directly. |
| Name uniqueness check in `AssistantBase` (not deployment script) | Applies to all save paths; avoids duplicating logic; consistent with existing `_validate_prompt_variables` placement |

---

## Out of Scope

- `AssistantVersionService` not called from deployment script (existing technical debt; noted but not addressed).
- `integration_alias` existence validation at startup (would require `preconfigured_workflows.py` to run before `preconfigured_assistants.py`; not the current order).
- Renaming `MCPServerDetails.settings` to `environment_vars` (pending rename; noted but not in this ticket).
- UI changes to the assistant configuration form (the task description mentions UI but the implementation gap is entirely in the backend deployment script; no frontend changes are required to meet the acceptance criteria).
- `AssistantBase` calling `validate_fields()` as a Pydantic model validator (would change the REST router validation path; out of scope — change only the deployment script caller).

---

## Acceptance Criteria Traceability

| AC | How met |
|---|---|
| Users can create/update assistants linked to multiple MCP servers with distinct labels | Change 1: name uniqueness enforced at model level; Change 2: deployment script validates before save |
| System enforces uniqueness and validity of all MCP plugin labels and endpoints | Change 1: `_validate_mcp_server_names()`; Change 2: `sanitize_for_save()` wired in |
| No duplicate plugin IDs or omissions; configuration accurately reflected | Change 2: `sanitize_for_save()` checks duplicate `mcp_config_id`; `validate_fields()` checks name duplicates |
| Errors surfaced with clear, specific messages | `ValidationException` from `sanitize_for_save()` contains field-level messages; new `_validate_mcp_server_names()` uses the same pattern as `_validate_prompt_variables` |
| Automated tests for multi-MCP scenarios (>2) | Change 3: new `test_preconfigured_assistants.py` tests with 3+ MCP servers |
| No regressions for 1-MCP or 2-MCP use cases | Change 3: explicit 1-MCP and 2-MCP regression tests in deployment script test file |
