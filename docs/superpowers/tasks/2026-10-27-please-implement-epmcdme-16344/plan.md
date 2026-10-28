# CIMD Off-Switch (Force DCR) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** An environment-wide setting that turns off the client metadata document (CIMD) so MCP OAuth registration goes through DCR.

**Architecture:** One bool in `config.py`. In `_resolve_discovered_candidate_payload` (bridge), when on, pass the `codemie_enterprise` resolver a copy of the discovery result reporting CIMD unsupported (so its existing DCR path and cache apply); if the AS is CIMD-only, return a `config_error` payload with a dedicated reason and message. `codemie_enterprise` and the CIMD router are untouched.

**Tech Stack:** Python, pytest, mock-based unit tests.

**Spec:** `docs/superpowers/tasks/2026-10-27-please-implement-epmcdme-16344/spec.md`

Commit per task using the repository's existing convention.

## Global Constraints

- Setting name `MCP_AUTH_CIMD_DISABLED: bool = False` (default off; prod unchanged).
- Reason code `cimd_only_and_disabled`; message says the server supports only the client metadata document, which is turned off in this environment. It must NOT say the server lacks support for it.
- Do not modify `codemie_enterprise`, `router.py` CIMD serving, or the input discovery result (no mutation).
- Setting off: behavior identical to today.

## Review Focus

- Discovery result as dict vs object: copy works for both (tested in Task 2).
- AS with CIMD support and a blank/empty-string `registration_endpoint`: treated as no endpoint (tested).
- Input discovery result unchanged after the call (tested).
- Setting on and AS with neither method: resolver's own failure path still runs (tested).

---

### Task 1: Config setting and deploy docs

**Files:**
- Modify: `src/codemie/configs/config.py:575` (next to `MCP_AUTH_ENFORCE_HTTPS`)
- Modify: `deploy-templates/` values file and MCP auth doc only if other `MCP_AUTH_*` settings are listed there (commented, default off)
- Test: `tests/codemie/configs/test_mcp_auth_cimd_config.py`

**Interfaces:**
- Produces: `config.MCP_AUTH_CIMD_DISABLED: bool`

Test-first: yes — `test_mcp_auth_cimd_disabled_defaults_false` fails with AttributeError until the field exists.

- [ ] Write test asserting `Config().MCP_AUTH_CIMD_DISABLED is False` and that env `MCP_AUTH_CIMD_DISABLED=true` yields `True` (follow how the existing config tests build `Config`).
- [ ] Add the field; run `pytest tests/codemie/configs/test_mcp_auth_cimd_config.py -v` (PASS); add the commented deploy/doc entries.

### Task 2: Steer the resolver and add the CIMD-only stop

**Files:**
- Modify: `src/codemie/enterprise/mcp_auth/_discovery.py:189-215` (`_resolve_discovered_candidate_payload`, before the `try`), plus new helpers in the same file
- Modify: `src/codemie/enterprise/mcp_auth/_constants.py:98` (add `_CIMD_DISABLED_ACTION` message constant and `CIMD_ONLY_AND_DISABLED_FAILURE_REASON = "cimd_only_and_disabled"`)
- Test: `tests/enterprise/mcp_auth/test_discovery_cimd_disabled.py`

**Interfaces:**
- Produces: `_copy_result_with_cimd_unsupported(result: Any) -> Any` — returns a shallow copy (`dict(result)` for Mapping; `copy.copy` for objects, falling back to `dataclasses.replace`/`model_copy` if frozen) with `client_id_metadata_document_supported=False`.
- Produces: `_build_cimd_disabled_payload(candidate, server_name) -> dict` — like `_build_discovered_failure_payload` but with `failure_reasons=("cimd_only_and_disabled",)` and `action=_CIMD_DISABLED_ACTION`. Prefer parameterizing `_build_discovered_failure_payload` with an optional `action` argument over duplicating it.
- Consumes: `config.MCP_AUTH_CIMD_DISABLED`, `_get_discovery_result_field` from `_common.py:105`.

Test-first: yes — tests call `_resolve_discovered_candidate_payload` with `resolve_discovered_oauth2_flow` patched (mock `codemie_enterprise.mcp_auth`; reuse the deps/fixtures style of neighbouring tests in `tests/enterprise/mcp_auth/`):

- [ ] Write failing tests:
  - `test_setting_off_passes_result_unchanged`: resolver receives the same `result` object (AC1, AC8).
  - `test_on_both_methods_passes_copy_with_cimd_false`: resolver's `discovery_result` reports `client_id_metadata_document_supported` False, `registration_endpoint` preserved, and the original still True (AC2, no mutation); parametrize dict and object results.
  - `test_on_dcr_only_unchanged_behavior` (AC3).
  - `test_on_cimd_only_returns_config_error`: resolver not called; `status == "config_error"`, `error_context["failure_reasons"] == ("cimd_only_and_disabled",)`, action text contains "only the client metadata document" and "turned off in this environment" and does not contain "does not support"/"not supported" (AC4); also with empty-string `registration_endpoint`.
  - `test_on_cache_hit_receives_dcr_cache`: resolver called with `dcr_credentials_cache=deps._mcp_auth_dcr_credentials_cache` (AC5).
  - `test_on_neither_method_calls_resolver`.
- [ ] Run `pytest tests/enterprise/mcp_auth/test_discovery_cimd_disabled.py -v`; expect FAIL.
- [ ] Implement the helpers and the branch: read `config.MCP_AUTH_CIMD_DISABLED`; if on and `_get_discovery_result_field(result, "client_id_metadata_document_supported")` is truthy → if `registration_endpoint` is a non-blank string, replace `result` with the copy, else return the CIMD-disabled payload. Log once per resolution via `logger.info` with `_sanitize_log_field` (setting on, action taken: `forced_dcr` / `stopped_cimd_only`).
- [ ] Run the new tests; expect PASS. Also run `pytest tests/enterprise/mcp_auth tests/enterprise/test_mcp_auth_feature_gating.py -q`.

### Task 3: Confirm AC6 (existing tokens unaffected) and pin the resolver contract

**Files:**
- Test: `tests/enterprise/mcp_auth/test_discovery_cimd_disabled.py` (append)

Test-first: no — read-only verification; add one test only if a regression is found. Grep `_oauth2_callback.py`, `_post_auth.py`, `_initiate.py` for calls to `resolve_discovered_oauth2_flow`/`resolve_client_registration`; confirm none re-run registration (they must read the stored snapshot). Add a contract test that, with the installed `codemie_enterprise`, `resolve_client_registration` given metadata with CIMD false and a `registration_endpoint` returns the DCR method (skip via `pytest.importorskip` if the package is absent). If a callback/refresh path does re-register, return to the caller rather than expanding scope.
