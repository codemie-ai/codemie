---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: f11d9a6242b009ec1d3c5227b436c432ea018e37
---

# Code review — 2026-10-29-hide-mcp-service-section-from-assistant-config-page

**Decision:** approve (check round, confidence final high · check high)  
**Reviewed head:** `f11d9a6` · **Rounds:** final → check

**Closed:** 2 fixed · 0 moot · 0 decided

CR-001 verified resolved: resolve_components()'s mcpConnect filter at customer_config_service.py:171 now reads `if config.STANDALONE and not mcp_connect_available(config):`, so outside standalone the condition is always False and GET /v1/config is byte-for-byte unchanged regardless of MCP_CONNECT_ENABLED, confirmed against mcp_connect_available's own non-standalone short-circuit in config.py:1184-1190. CR-002 verified resolved: the new parametrize in test_customer_config_service.py covers the full standalone x enabled x url 2x2x2 matrix, and the added toolkit_service/tool_node parametrized cases cover every behaviorally distinct combination of that matrix, cross-checked against the real gating branches in toolkit_service.py:1056-1078 (fetch/warn) and tool_node.py:126-127 (raise). No new findings raised this round; business_review and standards_review carried forward unchanged from the prior verdict.

## Open — 0

## Pre-existing (not blocking) — 3

- **Workflow validation calls MCP unguarded** — `src/codemie/workflows/config_resources_validation.py:181` — `_is_tool_available` calls `MCPToolkitService.get_mcp_server_tools` directly with no `mcp_connect_available` gate; in standalone with MCP unavailable this can attempt a real connection to an empty URL and raise an uncaught non-`ValueError` instead of failing validation cleanly. Pre-existing: this file is untouched by this diff, which scopes its four integration points to `config.py`, `customer_config_service.py`, `toolkit_service.py` and `tool_node.py` only.
- **Duplicate unguarded MCP validation call** — `src/codemie/workflows/validation/resources.py:376` — identical `_is_tool_available` duplicate with the same unguarded call. Pre-existing: same reasoning as above; also a pre-existing code duplication between the two files, unrelated to this change.
- **MCP tool preview endpoint unguarded** — `src/codemie/service/tools/mcp_tools_info_service.py:58` — `get_mcp_toolkit_info` (reachable from `POST /v1/assistants/mcp_tools`) never checks `mcp_connect_available`, so it still attempts a connection using a blank standalone URL instead of failing fast, inconsistent with the now-hidden `mcpConnect` UI. Pre-existing: file untouched by this diff and outside its four defined integration points.

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| Non-standalone: GET /v1/config identical to today regardless of MCP_CONNECT_ENABLED/URL | fail (final round) | New filter ties mcpConnect to MCP_CONNECT_ENABLED outside standalone; no such tie existed before this diff -- CR-001 |
| Non-standalone: MCP runtime behavior identical to today | pass | toolkit_service/tool_node already gated on MCP_CONNECT_ENABLED pre-diff; helper reduces to that flag outside standalone |
| Standalone, flag true + URL set: mcpConnect present and MCP works | pass | verified against config.py, customer_config_service.py, toolkit_service.py, tool_node.py |
| Standalone, flag false: mcpConnect absent | pass | mcp_connect_available short-circuits on MCP_CONNECT_ENABLED |
| Standalone, URL empty/missing: mcpConnect absent | pass | bool(URL) False for '' and None |
| mcpConnect absent => MCP Servers section hidden on assistant page (existing UI behavior) | na | frontend behavior, out of scope for this backend diff |
| mcpConnect absent => workflow selector/admin page/settings tabs/onboarding hide MCP (existing behavior) | na | frontend behavior, out of scope for this backend diff |
| Standalone + MCP unavailable + assistant with saved MCP servers: answers without MCP tools, no connection error, warning logged | pass | toolkit_service.py elif config.STANDALONE branch logs warning, no MCP call made |
| Standalone + MCP unavailable + workflow MCP node: fails with clear message, not raw connection error | pass | tool_node.py raises ValueError before any MCP call |
| Standalone + MCP_CONNECT_URL empty at startup: one warning logged listing degraded features | pass | main.py _run_standalone_startup |
| Non-standalone + MCP_CONNECT_URL empty at startup: no new warning | pass | _run_standalone_startup returns immediately when not STANDALONE |
| No new feature flag introduced; only MCP_CONNECT_ENABLED and mcpConnect are used | pass | only mcp_connect_available added, derived from existing fields |
| Tests cover standalone x enabled x url matrix for both config response and runtime MCP behavior | partial (final round) | resolve_components covers 4/8 combos; toolkit_service/tool_node each add only 1 new case -- CR-002 |
| Non-goal: no non-standalone behavior change, including startup logging | fail (final round) | Same regression as the GET /v1/config row above -- CR-001 |
| Non-goal: no deployment-level MCP Connect key setting | pass | no new setting added |
| Non-goal: no user-facing chat notice that MCP tools were skipped | pass | only a server-side logger.warning added |
| Non-goal: no dependency checks/startup warnings for other services | pass | main.py only adds the MCP warning |
| Non-goal: no UI code changes beyond tests | pass | diff touches only backend src/ and tests/ |
| Non-goal: no removing/migrating MCP servers already saved on assistants | pass | no storage/migration code touched |
| plan.md Review Focus: mcp_connect_available treats MCP_CONNECT_URL=None same as '' | pass | bool(None) is False |
| plan.md Review Focus: resolve_components filter is a no-op when mcpConnect absent from enabled | pass | list comprehension filter, no KeyError |
| plan.md Review Focus: tool_node.py error message text stays unchanged | pass | verified identical text at tool_node.py:127 |
| plan.md Review Focus: toolkit_service.py warning fires only on the skip path, not every assistant run | pass | verified; fires on every skip though, not only when assistant has saved MCP servers -- matches spec design as written |

Standards: commit-format pass · code-quality pass · security pass
