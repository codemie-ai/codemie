# EPMCDME-11766 Inline MCP credentials on test and tool fetch - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** `POST /assistants/mcp/test` and `POST /assistants/mcp_tools` use inline `settings.credential_values` as MCP server env when `settings.id` is absent, restricted to the catalog entry's `required_env_vars`.

**Architecture:** An opt-in keyword `allow_inline_credentials: bool = False` is threaded down to `_resolve_credentials_with_priority`, where a new short-circuit branch builds the env dict from the request's own `credential_values`. Only the two test-path services pass `True`.

**Tech Stack:** Python, pytest, `unittest.mock`.

**Spec:** `/home/volhamakarava/codemie-dev/codemie/docs/superpowers/tasks/2026-10-21-epmcdme-11766-infongen-mcp-server-integration-fail/spec.md` (backend scope only; the UI story is out of this plan).

Commit per task using the repository's existing convention (ticket `EPMCDME-11766`).

## Global Constraints

- Flag name and default: `allow_inline_credentials: bool = False`. Runtime, workflow and validation callers must not change.
- No change to request or response models (openapi baseline untouched).
- Never log inline values (counts only); never put them in exception text; never call `SettingsService`; never persist; do not mutate the request.
- Out of scope: error wording, restricted-mode gap, `MASKED_VALUE`, `tool_creds` allow-list.

## Review Focus

- Alias set and inline values both present: alias still wins (unchanged Priority 1).
- `credential_values` empty or None with the flag True: returns `{}` with no `_resolve_credentials_by_id` call.
- `mcp_config_id` set but `MCPConfig.find_by_id` returns None: `{}`, no error.
- Allowed names match case-sensitively; a value of any type for an allowed name passes through untouched.
- A secret sentinel never appears in caplog output on the inline path.

---

### Task 1: Inline credential branch in the resolver

**Files:**
- Modify: `src/codemie/service/mcp/toolkit_service.py:1468-1511` (`_resolve_credentials_with_priority`); add a new classmethod next to it.
- Test: `tests/codemie/service/mcp/test_toolkit_service_three_state.py` (follow the existing `patch.object(MCPToolkitService, ...)` style).

**Interfaces:**
- Produces: `_resolve_credentials_with_priority(..., ignore_integration_alias: bool = False, allow_inline_credentials: bool = False) -> dict[str, Any]`
- Produces: `MCPToolkitService._resolve_inline_credentials(cls, mcp_server: MCPServerDetails) -> dict[str, Any]`

**Test-first: yes - with `settings` (no id, `credential_values=[A,B]`), `mcp_config_id="cfg"`, `use_custom_config=False`, catalog `required_env_vars=[A]`, and `allow_inline_credentials=True`, the resolver returns `{"A": ...}`; fails today because the inline values are never read and `_resolve_credentials_by_id` is called instead.**

- [ ] **Step 1: Write failing tests** (patch `codemie.rest_api.models.mcp_config.MCPConfig.find_by_id`, `_resolve_credentials_by_id`, `SettingsService.retrieve_setting`):
  - `test_inline_returns_only_declared_names`: result equals `{"A": "a"}`; `B` dropped; `_resolve_credentials_by_id` and `retrieve_setting` not called.
  - `test_inline_ignored_for_custom_config`, `..._without_config_id`, `..._when_entry_has_no_required_env_vars`, `..._when_entry_missing`: result `{}`, by-id not called.
  - `test_settings_id_wins_over_inline`: id present, flag True -> by-id called with that id; result is its return value.
  - `test_flag_false_keeps_current_behaviour`: no id, flag False -> `_resolve_credentials_by_id` called once (as today).
  - `test_alias_wins_over_inline`: alias set, flag True -> `_resolve_credentials_by_alias` result returned.
  - `test_inline_does_not_log_values_or_mutate_request`: use sentinel secret `"s3cr3t-value"`, assert it is absent from `caplog.text` and that `settings.credential_values` is unchanged.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/service/mcp/test_toolkit_service_three_state.py -v`; expect the new tests to FAIL.
- [ ] **Step 3: Implement.** Add the keyword parameter. In the `elif mcp_server.settings:` branch: if `settings.id` is truthy keep the by-id call; elif `allow_inline_credentials` return `cls._resolve_inline_credentials(mcp_server)` (log only `Applied {n} environment variables from inline settings` when n > 0) without touching `_resolve_credentials_by_id`; else keep the current call. `_resolve_inline_credentials` returns `{}` when `use_custom_config` or no `mcp_config_id`; otherwise does a local-import `MCPConfig.find_by_id(mcp_config_id)`, builds `allowed = {v.name for v in (entry.required_env_vars or [])}` (required and optional), returns `{}` if the entry is None or `allowed` is empty, else a new dict from `settings.credential_values` filtered to `item.key in allowed` (reuse the `{item.key: item.value}` shape of `_extract_credentials_from_settings`). Update the docstring with the new arg.
- [ ] **Step 4: Run** the same command; expect PASS.

### Task 2: Thread the flag down the call chain

**Files:**
- Modify in `src/codemie/service/mcp/toolkit_service.py`: `get_mcp_server_tools` (:185), `_collect_server_results` (:281), `_process_single_server_for_tools` (:325), `_process_single_mcp_server` (:755, call at :796), `_prepare_server_config` (:906, call at :946), `_build_mcp_server_config` (:1514, call at :1564).
- Test: `tests/codemie/service/mcp/test_toolkit_service_three_state.py`.

**Interfaces:**
- Consumes: Task 1 `allow_inline_credentials` on `_resolve_credentials_with_priority`.
- Produces: keyword-only `allow_inline_credentials: bool = False` on each method above, forwarded unchanged by name. In `get_mcp_server_tools` place it after `marketplace_scope`. In `_prepare_server_config` put it after the `*`.

**Test-first: yes - `_build_mcp_server_config(server, "user", "proj", allow_inline_credentials=True)` calls `_resolve_credentials_with_priority` with `allow_inline_credentials=True`; fails today with `TypeError` (unexpected keyword).**

- [ ] **Step 1: Write failing tests**, modelled on `test_build_config_forwards_ignore_alias_flag` (:94): `test_build_config_forwards_inline_flag` (True forwarded, default False), and `test_prepare_server_config_forwards_inline_flag` (patch `_build_mcp_server_config`, assert kwarg). Add `test_get_mcp_server_tools_forwards_inline_flag`: patch `_collect_server_results` and `MCPAccessControlService.filter_for_runtime`, assert the kwarg reaches it.
- [ ] **Step 2: Run** the file with pytest; expect FAIL.
- [ ] **Step 3: Implement** the pass-through on every method listed. Behaviour with the default `False` must stay byte-identical. Do not touch `ensure_discovered_snapshot_for_server` or other callers.
- [ ] **Step 4: Run** the file again, plus `poetry run pytest tests/codemie/service/mcp -q`; expect PASS (existing tests unchanged).

### Task 3: Test and tool-fetch services opt in

**Files:**
- Modify: `src/codemie/service/mcp/mcp_tester.py:85-87` (`MCPServerTester.test`); `src/codemie/service/tools/mcp_tools_info_service.py:58-63` (`get_mcp_toolkit_info`).
- Test: `tests/codemie/service/mcp/test_mcp_tester.py`, `tests/codemie/service/tools/test_mcp_tools_info_service.py` (reuse their existing `get_mcp_server_tools` mocks).

**Interfaces:**
- Consumes: Task 2 `get_mcp_server_tools(..., allow_inline_credentials=...)`.

**Test-first: yes - after `MCPServerTester(...).test()` and `MCPToolsInfoService.get_mcp_toolkit_info(...)`, the mocked `get_mcp_server_tools` was called with `allow_inline_credentials=True`; fails today because neither passes it.**

- [ ] **Step 1: Write failing assertions** in one new test per file using the existing success-path fixtures: `mock.call_args.kwargs["allow_inline_credentials"] is True`.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/service/mcp/test_mcp_tester.py tests/codemie/service/tools/test_mcp_tools_info_service.py tests/codemie/rest_api/routers/test_assistant_mcp_tools.py -v`; expect the new tests to FAIL.
- [ ] **Step 3: Implement** by adding `allow_inline_credentials=True` to both calls. Leave messages and hints untouched.
- [ ] **Step 4: Run** the same command; expect PASS.

---

**Spec coverage:** R1 -> Tasks 2, 3. R2, R3 -> Task 1. R4, R5 -> Task 1 (`_resolve_inline_credentials`). R6 -> Task 1 (new dict merged by the existing `env_vars.update`). R7 -> Task 1 log and mutation test. Acceptance criteria 1-2 -> Tasks 1 and 3; 3-6 -> Task 1; 7 -> Task 1 caplog test; 8 -> Task 2 default-False and the existing suite; 9 -> no model edits.

**negative-constraints:** honoured. No `SettingsService` calls and no persistence on the inline path (Task 1); no logging of values (Task 1); no change to runtime, workflow or validation callers (Tasks 2 and 3 touch only the two test-path services); no error wording changes (Task 3); no API model changes.
