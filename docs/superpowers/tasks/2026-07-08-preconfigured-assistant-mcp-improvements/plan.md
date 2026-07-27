# Plan: Preconfigured Assistant MCP Integration Improvements

**Ticket:** EPMCDME-8996  
**Spec:** [spec.md](./spec.md)  
**Branch:** EPMCDME-8996_mcp-assistant-creation-update-improvements  

---

## Task Breakdown

### task-1 — Add `_validate_mcp_server_names()` to `AssistantBase.validate_fields()`

**File:** `src/codemie/rest_api/models/assistant.py`  
**Test-first: yes** — Write a test that calls `validate_fields()` on an `AssistantBase`-derived instance with duplicate `mcp_servers` names and asserts a non-empty error string is returned.

**Implementation:**

1. Add `_validate_mcp_server_names(self) -> Optional[str]` after `_validate_prompt_variables` (line ~933). Follow the same pattern:
   ```python
   def _validate_mcp_server_names(self) -> Optional[str]:
       names = [s.name for s in (self.mcp_servers or [])]
       duplicates = {n for n in names if names.count(n) > 1}
       if duplicates:
           return f"Duplicate MCP server names detected: {', '.join(sorted(duplicates))}"
       return None
   ```
   The `or []` guard prevents `TypeError` when `validate_fields()` is called on an object whose `mcp_servers` field is `None` (can occur on objects read back from Elasticsearch).
2. Register in `validate_fields()` after the `prompt_variables_error` block:
   ```python
   mcp_names_error = self._validate_mcp_server_names()
   if mcp_names_error:
       return mcp_names_error
   ```

**Test file:** `tests/codemie/rest_api/models/test_assistant_model.py`  
Test cases (add to a new `class TestAssistantValidateMCPServerNames`):
- No MCP servers → `_validate_mcp_server_names()` returns `None`.
- One MCP server → no error.
- Two MCP servers, distinct names → no error.
- Two MCP servers, same name → error string contains `"Duplicate MCP server names"` and the duplicate name.
- Three MCP servers with one duplicate pair → error lists the duplicate name.
- `validate_fields()` propagates the error (test with mocked validators to isolate).

Also add to `TestCheckSlugUniqueness`:
- `test_returns_conflict_when_self_id_is_none` — documents that `_check_slug_uniqueness()` always returns a conflict when `self.id=None` and ES returns a hit. This test is GREEN as-is (documents existing behaviour); the fix is at the call site (task-3), not here.

---

### task-2 — Wire `sanitize_for_save()` + `validate_fields()` into `create_preconfigured_assistant()`

**File:** `src/external/deployment_scripts/preconfigured_assistants.py`  
**Test-first: yes** — Write a test that calls `create_preconfigured_assistant()` with a template that has two MCP servers sharing the same name, and asserts that a `ValidationException` is raised (not swallowed).

**Implementation:**

1. Add import at top of file:
   ```python
   from codemie.service.mcp.access_control import MCPAccessControlService
   from codemie.exceptions import ValidationException
   ```
2. In `create_preconfigured_assistant()`, **after** the `if existing_assistant:` early-return branch and **before** the `Assistant()` constructor call, insert:
   ```python
   validated_mcp_servers = assistant_template.mcp_servers
   if assistant_template.mcp_servers:
       if error := assistant_template.validate_fields():
           raise ValidationException(
               f"MCP validation failed for template '{assistant_slug}': {error}"
           )
       validated_mcp_servers = MCPAccessControlService.sanitize_for_save(assistant_template.mcp_servers)
   ```
   Pass `mcp_servers=validated_mcp_servers` to the `Assistant()` constructor. Do **not** write back to `assistant_template.mcp_servers`.

**Test file:** `tests/external/deployment_scripts/test_preconfigured_assistants.py`  
New test cases:
- `create_preconfigured_assistant` with template having 1 MCP server → `sanitize_for_save` called with that server; assistant created successfully.
- `create_preconfigured_assistant` with template having 3 MCP servers (distinct names) → all 3 propagated to `Assistant()` constructor.
- `create_preconfigured_assistant` with template having 2 MCP servers with the same name → `ValidationException` raised.
- `create_preconfigured_assistant` in restricted mode with inline-config MCP servers → `ValidationException` raised (via `sanitize_for_save` → `validate_on_save`).

---

### task-3 — Wire `sanitize_for_save()` + `_validate_mcp_server_names()` into `update_assistant_content()`

**File:** `src/external/deployment_scripts/preconfigured_assistants.py`  
**Test-first: yes** — Write `test_update_assistant_content_mcp_template_id_none_does_not_raise` first: a real `Assistant` template with `id=None` and `mcp_servers=[_MCP_SERVER_A]`, an ES mock returning a persisted match, assert no `ValidationException` is raised and `save()` is called (RED before fix, GREEN after).

**Why `_validate_mcp_server_names()` and not `validate_fields()` (CR-002):** YAML templates are cached in `AssistantService._cached_base_assistant_templates` with `id=None` and `project=CODEMIE_PROJECT_NAME`. Calling `validate_fields()` triggers `_check_slug_uniqueness()`, which queries ES, finds the persisted assistant, and compares `existing.id (UUID) != self.id (None)` → always `True` → false-positive slug-conflict `ValidationException` on every deployment update. `_validate_mcp_server_names()` is identity-independent and is all the validation needed here; `sanitize_for_save()` covers catalog/access-control.

**Implementation:**

In `update_assistant_content()`, before building `fields_to_check`:
```python
validated_mcp_servers = assistant_template.mcp_servers
if assistant_template.mcp_servers:
    if error := assistant_template._validate_mcp_server_names():
        raise ValidationException(
            f"MCP validation failed for template '{existing_assistant.slug}': {error}"
        )
    validated_mcp_servers = MCPAccessControlService.sanitize_for_save(assistant_template.mcp_servers)
```

Ordering: validate first (on original list), then sanitize into local variable. Since `sanitize_for_save()` does not change server names, name-duplicate results are identical either way; validating first gives earlier feedback.

Replace `'mcp_servers': assistant_template.mcp_servers` in `fields_to_check` with `'mcp_servers': validated_mcp_servers`. Do **not** write back to `assistant_template.mcp_servers` — the template is a shared cached object and mutating it would corrupt all subsequent callers in the same process.

**Test file:** `tests/external/deployment_scripts/test_preconfigured_assistants.py`  
New test cases:
- `test_update_assistant_content_mcp_template_id_none_does_not_raise` — CR-002 regression: real template with `id=None`, ES mock returns a hit; no `ValidationException` raised; `save()` called.
- `update_assistant_content` with template having 1 MCP server → `sanitize_for_save` called; `save()` called.
- `update_assistant_content` with template having 2 MCP servers with same name → `ValidationException` raised before `save()`.
- `update_assistant_content` in restricted mode with inline server → `ValidationException` from `sanitize_for_save` propagates.

---

### task-4 — Wire `MCPConfigService.adjust_usage()` into the deployment script (warn-and-continue)

**File:** `src/external/deployment_scripts/preconfigured_assistants.py`  
**Test-first: yes** — Write a test that calls `create_preconfigured_assistant()` with a template having a server with `mcp_config_id` set and asserts `MCPConfigService.adjust_usage()` is called with the correct increment set.

**Implementation:**

1. Add import:
   ```python
   from codemie.service.mcp_config_service import MCPConfigService
   ```
2. In `create_preconfigured_assistant()`, after `preconfigured_assistant.save(refresh=True)`:
   ```python
   if validated_mcp_servers:
       config_ids = {s.mcp_config_id for s in validated_mcp_servers if s.enabled and s.mcp_config_id}
       try:
           MCPConfigService.adjust_usage(increments=config_ids, decrements=set())
       except Exception as e:
           logger.warning(f"Failed to track MCP usage on create for '{assistant_slug}': {e}", exc_info=True)
   ```
   Use `validated_mcp_servers` (the sanitized list), not `assistant_template.mcp_servers`.
3. In `update_assistant_content()`, capture IDs **before** the `setattr` loop (so `existing_assistant.mcp_servers` still holds the old value), then call `adjust_usage` **after** `save()` succeeds:

   ```python
   if updates:
       if 'mcp_servers' in updates:
           old_ids = {s.mcp_config_id for s in (existing_assistant.mcp_servers or []) if s.enabled and s.mcp_config_id}
           new_ids = {s.mcp_config_id for s in (validated_mcp_servers or []) if s.enabled and s.mcp_config_id}

       for field, value in updates.items():
           logger.info(f"Updating {field} for assistant '{existing_assistant.slug}'")
           setattr(existing_assistant, field, value)

       existing_assistant.save()
       logger.info(f"Assistant '{existing_assistant.slug}' updated successfully.")

       if 'mcp_servers' in updates:
           try:
               MCPConfigService.adjust_usage(increments=new_ids - old_ids, decrements=old_ids - new_ids)
           except Exception as e:
               logger.warning(f"Failed to track MCP usage changes for '{existing_assistant.slug}': {e}", exc_info=True)

       return True

   return False
   ```

   **Ordering invariant (CR-001):** `old_ids` must be captured before `setattr` overwrites `existing_assistant.mcp_servers`, and `adjust_usage` must be called after `save()` so a failed ES write cannot leave PostgreSQL `usage_count` rows permanently incremented/decremented.

**Test file:** `tests/external/deployment_scripts/test_preconfigured_assistants.py`  
New test cases:
- Create with 1 catalog-backed server (`mcp_config_id` set) → `adjust_usage` called with that ID in `increments`.
- Create with inline-only servers (no `mcp_config_id`) → `adjust_usage` NOT called.
- Update where new template adds a catalog server → `adjust_usage` called with increment.
- `adjust_usage` raises → warning logged; `save()` already succeeded (no re-raise).
- `save()` raises (ES write failed) → `adjust_usage` NOT called (regression guard for CR-001 ordering bug).

---

### task-5 — Multi-MCP regression and >2-server scenario tests

**File:** `tests/external/deployment_scripts/test_preconfigured_assistants.py`  
**Test-first: no** — These are end-to-end scenario tests over the already-implemented create/update functions. They verify no regressions for 1-MCP and 2-MCP cases, and validate the >2-MCP acceptance criterion.

**Test cases:**
- `test_create_preconfigured_assistant_with_one_mcp_server` — existing assistant `None`; template has 1 inline MCP server; asserts `mcp_servers` propagated to `Assistant()` constructor with that 1 server.
- `test_create_preconfigured_assistant_with_two_mcp_servers` — template has 2 inline MCP servers, distinct names; asserts both propagated.
- `test_create_preconfigured_assistant_with_three_mcp_servers` — template has 3 inline MCP servers (including one with `integration_alias`), all distinct names; asserts all 3 propagated and `sanitize_for_save` called once.
- `test_update_assistant_content_adds_mcp_servers` — existing assistant has `mcp_servers=[]`; template has 3 servers; asserts the update path sets all 3 and calls `save()`.
- `test_update_assistant_content_removes_mcp_servers` — existing assistant has 2 servers; template has `mcp_servers=[]`; asserts `mcp_servers` updated to `[]`.
- `test_update_assistant_content_no_change_with_same_mcp_servers` — existing and template have same 2 servers; asserts `save()` not called (no-op).
- `test_manage_preconfigured_assistants_with_mcp_servers` — `manage_preconfigured_assistants()` with one enabled assistant whose template has 3 MCP servers; asserts the create path runs without errors.

---

## Execution Order

1. task-1 (model validation, no external deps)
2. task-2 (create path, depends on task-1 for `validate_fields()`)
3. task-3 (update path, depends on task-2 import additions)
4. task-4 (usage tracking, depends on task-3 structure)
5. task-5 (scenario tests, validates the whole flow)

---

## Risk Notes

- `validate_fields()` calls `_check_slug_uniqueness()` which does an ES query. YAML templates are loaded with `project=CODEMIE_PROJECT_NAME` but `id=None`. When ES returns a hit (the persisted assistant), `_check_slug_uniqueness()` compares `existing.id (UUID) != self.id (None)` → always `True` → false-positive slug-conflict error. **This is why the update path uses `_validate_mcp_server_names()` directly instead of `validate_fields()`.** The create path is safe: it only reaches validation when `existing_assistant=None`, so when `_check_slug_uniqueness()` queries ES it also gets `None` back and returns `""` (no error).
- `_check_categories()` does a lazy import of `category_service`. In the deployment script context this is safe; the service is initialized at startup before `manage_preconfigured_assistants()` runs.
- `MCPConfigService.adjust_usage()` issues a `SELECT FOR UPDATE` in a transaction. The deployment script runs outside a request context. Since `adjust_usage()` manages its own session, this is safe — but callers wrap it in try/except per the warn-and-continue policy.
