# EPMCDME-14984: MCP + DSP user_context Opt-Out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a default-off `allow_user_context` opt-out on both the MCP path (two-level precedence: server-catalog default overridden per-assistant) and the DSP path (single flat boolean on `Provider`), so PII-bearing `user_context` is only forwarded to an external MCP-Connect/DSP server when explicitly enabled.

**Architecture:** Two independent gates sharing only a field name, no shared resolution code. MCP: field on `MCPServerConfigData` (persisted default) mirrors onto runtime `MCPServerConfig` by matching field names, gets a sibling override field on `MCPServerDetails`, resolved once in `_build_mcp_server_config()`, enforced in `_build_request_payload()`. DSP: field on `ProviderBase`/`CreateProviderRequest`/`UpdateProviderRequest`, enforced directly in `ProviderToolFactory.invoke()`, plus a targeted fix to `ProviderService.update()`'s truthy-only field-copy so an explicit `False` isn't dropped.

**Tech Stack:** Python, Pydantic/SQLModel, pytest.

**Spec:** `docs/superpowers/tasks/2026-09-14-epmcdme-14984-mcp-user-context-opt-out/spec.md`

## Global Constraints

- Default is `False` (off) on every new field, on both paths — this is a live behavior change on DSP (currently unconditional send), stakeholder-approved.
- No shared precedence/resolution code between the MCP and DSP gates — `allow_user_context` is a shared field name only.
- Backend enforcement only — no MCP-Connect changes, no UI.
- What `user_context` *contains* when sent is unchanged on both paths (existing `extra_attributes` exclusion, existing shape) — only whether it's sent changes.
- No migration/backfill — all new fields are additive with safe defaults on existing JSONB-backed Pydantic models.
- Commit per task, referencing EPMCDME-14984, following this branch's existing commit-message convention (see recent commits on `EPMCDME-13260-mcp-user-context`).

---

### Task 1: MCP server-level default field (catalog + runtime mirror)

**Files:**
- Modify: `src/codemie/rest_api/models/mcp_config.py:97` (`MCPServerConfigData`)
- Modify: `src/codemie/service/mcp/models.py:200` (`MCPServerConfig`)
- Test: `tests/codemie/service/mcp/test_access_control.py` (or nearest existing test covering `resolve_catalog_config`)

**Interfaces:**
- Produces: `MCPServerConfigData.allow_user_context: bool` (default `False`), `MCPServerConfig.allow_user_context: bool` (default `False`) — later tasks read/overwrite the runtime field by this exact name.

- [ ] **Step 1 (Test-first: yes — a `MCPServerConfigData` with `allow_user_context=True` produces a `MCPServerConfig` with `allow_user_context=True` after `resolve_catalog_config()`'s `MCPServerConfig(**entry.config.model_dump())` call; currently fails because the field doesn't exist on either model).** Write the failing test, run it, confirm the failure is "field does not exist" (not an unrelated error).
- [ ] **Step 2:** Add `allow_user_context: bool = Field(default=False, description="...")` to `MCPServerConfigData`, matching the `single_usage`/`allow_issuer_prefix_match` declaration style already on that model. Add the identical field/default to `MCPServerConfig`.
- [ ] **Step 3:** Run the test, confirm it passes. Commit.

---

### Task 2: MCP per-assistant override field

**Files:**
- Modify: `src/codemie/rest_api/models/assistant.py:174` (`MCPServerDetails`)
- Test: `tests/codemie/rest_api/models/test_assistant_model.py`

**Interfaces:**
- Produces: `MCPServerDetails.allow_user_context: Optional[bool]` (default `None`), declared as a **top-level sibling of `config`**, not nested inside it — Task 3 reads this exact attribute.

- [ ] **Step 1 (Test-first: yes — constructing `MCPServerDetails` with `allow_user_context=True/False/omitted` round-trips the value unchanged and it serializes as a top-level key, not inside `config`; currently fails because the field doesn't exist).**
- [ ] **Step 2:** Add `allow_user_context: Optional[bool] = Field(default=None, description="...")` to `MCPServerDetails` at the top level (sibling of `config`). Do not nest it inside `config` — `resolve_catalog_config()`'s `model_copy(update={"config": resolved_config})` (`access_control.py:159-187`) replaces `config` wholesale and would silently discard anything nested there.
- [ ] **Step 3:** Run the test, confirm it passes. Commit.

---

### Task 3: MCP precedence resolution

**Files:**
- Modify: `src/codemie/service/mcp/toolkit_service.py:1514-1569` (`_build_mcp_server_config`)
- Test: `tests/codemie/service/mcp/test_toolkit_service.py` (or nearest existing test covering `_build_mcp_server_config`)

**Interfaces:**
- Consumes: `MCPServerDetails.allow_user_context` (Task 2), `MCPServerConfig.allow_user_context` (Task 1).
- Produces: the `MCPServerConfig` returned by `_build_mcp_server_config()` always carries one final, resolved `allow_user_context` bool — Task 4 reads this resolved value, not the raw inputs.

- [ ] **Step 1 (Test-first: yes — three cases: assistant override `True` flips a server default of `False` to `True`; assistant override `False` flips a server default of `True` to `False`; assistant override `None`/omitted leaves the server default standing in both directions; all fail today since no overwrite happens).**
- [ ] **Step 2:** After `actual_config: MCPServerConfig` is built in `_build_mcp_server_config()` (already carrying the resolved server default from Task 1), add: if `mcp_server.allow_user_context is not None`, set `actual_config.allow_user_context = mcp_server.allow_user_context`.
- [ ] **Step 3:** Run the tests, confirm all three cases pass. Commit.

---

### Task 4: MCP enforcement in request payload + masked logging

**Files:**
- Modify: `src/codemie/service/mcp/client.py:100-129` (`_build_request_payload`)
- Modify: `src/codemie/service/mcp/client.py:239-243` (`invoke_tool` masked debug log)
- Test: `tests/codemie/service/mcp/test_client.py`

**Interfaces:**
- Consumes: `server_config.allow_user_context` (the resolved bool from Task 3), already in scope as a parameter of `_build_request_payload`.

- [ ] **Step 1 (Test-first: yes — four cases: `allow_user_context=False` drops `user_context` from the built payload for both `list_tools()` and `invoke_tool()` even when `execution_context.user_context` is set; `allow_user_context=True` leaves it present, unchanged shape; the masked debug log in `invoke_tool()` fires only when `allow_user_context=True` and `user_context` is present, not merely when `execution_context.user_context is not None`; all fail today since there is no gate).**
- [ ] **Step 2:** In `_build_request_payload()`, after unpacking `execution_context.to_request_fields()`, drop the `user_context` key from that dict when `server_config.allow_user_context` is `False`, before constructing `MCPToolInvocationRequest`. Do not touch `MCPExecutionContext` construction in `toolkit_service.py`'s `get_mcp_server_tools()`, and do not touch `to_request_fields()`'s own `exclude=...`/`extra_attributes` mechanics — the gate acts only on `_build_request_payload()`'s output.
- [ ] **Step 3:** In `invoke_tool()`, change the masked-log condition from `execution_context is not None and execution_context.user_context is not None` to also require `server_config.allow_user_context`.
- [ ] **Step 4:** Run the tests, confirm all pass. Commit.

---

### Task 5: MCP restricted-mode allow-list

**Files:**
- Modify: `src/codemie/service/mcp/access_control.py:26-39` (`_CATALOG_REF_ALLOWED_FIELDS`)
- Test: `tests/codemie/service/mcp/test_access_control.py`

**Interfaces:**
- Consumes: `MCPServerDetails.allow_user_context` (Task 2).

- [ ] **Step 1 (Test-first: yes — under `mcpCustomServersDisabled` restricted mode, a catalog-referenced `MCPServerDetails` with an explicit `allow_user_context` value passes `_validate_restricted_mode()`; currently fails because the field isn't in the allow-list and any non-`None` field not listed is treated as a violation).**
- [ ] **Step 2:** Add `"allow_user_context"` to `_CATALOG_REF_ALLOWED_FIELDS`.
- [ ] **Step 3:** Run the test, confirm it passes. Commit.

---

### Task 6: DSP config fields

**Files:**
- Modify: `src/codemie/rest_api/models/provider.py:255-259` (`ProviderBase`)
- Modify: `src/codemie/rest_api/models/provider.py:279-322` (`CreateProviderRequest`, `UpdateProviderRequest`)
- Test: `tests/codemie/rest_api/models/test_provider_model.py`, `tests/codemie/service/provider/test_provider_service.py`

**Interfaces:**
- Produces: `ProviderBase.allow_user_context: bool` (default `False`), `CreateProviderRequest.allow_user_context: bool` (default `False`), `UpdateProviderRequest.allow_user_context: Optional[bool]` (default `None`) — Task 7 reads `self.provider_config.allow_user_context`; Task 8 reads `UpdateProviderRequest`'s field.

- [ ] **Step 1 (Test-first: yes — `ProviderService.create()` with `allow_user_context` omitted from `CreateProviderRequest` persists `False`; with `allow_user_context=True` persists `True`; both fail today since the field doesn't exist anywhere in the model family).**
- [ ] **Step 2:** Add `allow_user_context: bool = Field(default=False, description="...")` to `ProviderBase`. `CreateProviderRequest` and `UpdateProviderRequest` are standalone `BaseModel`s, not `ProviderBase` subclasses — add `allow_user_context: bool = Field(default=False, ...)` to `CreateProviderRequest` and `allow_user_context: Optional[bool] = Field(default=None, ...)` to `UpdateProviderRequest` explicitly.
- [ ] **Step 3:** Run the tests, confirm they pass. Commit.

---

### Task 7: DSP enforcement in `ProviderToolFactory.invoke()`

**Files:**
- Modify: `src/codemie/service/provider/provider_tool_factory.py:131-177` (`invoke`)
- Test: `tests/codemie/service/provider/test_provider_tool_factory.py`

**Interfaces:**
- Consumes: `self.provider_config.allow_user_context` (Task 6), already in scope on `self`.

- [ ] **Step 1 (Test-first: yes — `invoke()` with `provider_config.allow_user_context=False` sends the `ToolInvocationRequest` with no `user_context` kwarg and does not emit the masked debug log; with `allow_user_context=True` sends `user_context` in the same shape as today and emits the masked log; both fail today since the send/log are unconditional).**
- [ ] **Step 2:** Wrap the `user_context=UserContext.from_user(user).model_dump(exclude_none=True)` kwarg (line 175) and the masked debug log (line 177) both behind `if self.provider_config.allow_user_context:`.
- [ ] **Step 3:** Run the tests, confirm they pass. Commit.

---

### Task 8: DSP `ProviderService.update()` truthy-check fix

**Files:**
- Modify: `src/codemie/service/provider/provider_service.py:54-66` (`update`)
- Test: `tests/codemie/service/provider/test_provider_service.py`

**Interfaces:**
- Consumes: `UpdateProviderRequest.allow_user_context: Optional[bool]` (Task 6).

- [ ] **Step 1 (Test-first: yes — a provider created/updated with `allow_user_context=True`, then updated again with `UpdateProviderRequest(allow_user_context=False)`, ends up with `allow_user_context=False` on the persisted row; fails today because the existing `if value:` loop treats explicit `False` as "not provided" and silently skips it, leaving the provider stuck at `True`).**
- [ ] **Step 2:** Replace the field-copy loop, special-casing only `allow_user_context` — every other field keeps today's truthy-only behavior unchanged:
  ```python
  provided = request.dict(exclude_unset=True)
  for field, value in request.dict().items():
      if value or (field == "allow_user_context" and field in provided):
          setattr(provider, field, value)
  ```
- [ ] **Step 3:** Run the test, confirm it passes; also run the existing `test_update` case to confirm no regression on other fields. Commit.

---

## Negative-constraint pass

- **No change to MCP-Connect:** no task touches `codemie-mcp-connect-service`; Task 4 only changes what this backend sends, MCP-Connect keeps treating `user_context` unmodified. Honored.
- **No change to what `user_context` contains when sent:** Task 4 explicitly excludes touching `to_request_fields()`'s `exclude=...`/`extra_attributes` mechanics; Task 7 reuses the same `UserContext.from_user(user).model_dump(exclude_none=True)` call unchanged, only gating whether it runs. Honored.
- **No UI:** no task touches a router response model's UI consumption or any frontend code — all eight tasks are model/service files only. Honored.
- **No shared precedence-resolution utility across MCP and DSP:** Task 3 (MCP precedence) and Task 8 (DSP truthy fix) touch disjoint files (`toolkit_service.py` vs `provider_service.py`) with no shared helper; Task 7's gate is a bare `if self.provider_config.allow_user_context:`, not a call into MCP's resolution code. Honored.
- **No change to `MCPExecutionContext`'s per-call construction:** Task 4 step 2 explicitly states it does not touch `get_mcp_server_tools()`'s context construction; no other task touches that method. Honored.
- **No broader fix to `ProviderService.update()`'s truthy-check semantics:** Task 8's replacement loop special-cases only `field == "allow_user_context"`; every other field keeps the original `if value:` behavior verbatim, and Step 3 re-runs `test_update` to confirm no regression. Honored.
- **No migration/backfill:** all eight tasks add fields with safe defaults to existing JSONB-backed Pydantic models (`MCPServerConfigData`, `MCPServerConfig`, `MCPServerDetails`, `ProviderBase`, `CreateProviderRequest`, `UpdateProviderRequest`); none adds a schema migration or backfill script. Honored.
