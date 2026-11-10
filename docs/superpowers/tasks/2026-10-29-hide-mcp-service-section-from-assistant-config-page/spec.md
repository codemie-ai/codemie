# Spec: Standalone MCP Servers Visibility (EPMCDME-15689)

## Intent

Today `MCP_CONNECT_ENABLED` (backend tool execution gate) and `mcpConnect`
(customer-config component served by `GET /v1/config`, which the UI uses to
show/hide every MCP entry point) are two independent switches with nothing
keeping them in sync. In standalone deployments MCP Connect is frequently
absent (`MCP_CONNECT_URL` empty), yet the UI still advertises MCP and saved
MCP servers fail at runtime with raw connection errors. This change makes
`mcpConnect` availability — and the runtime behavior behind it — reflect
whether MCP Connect is actually usable, but **only in standalone mode**.
Non-standalone deployments must stay byte-for-byte unchanged.

## Design

### New helper: `mcp_connect_available`

A new module-level function in `src/codemie/configs/config.py`, next to the
existing `retrieval_available(cfg: Config) -> bool` (`config.py:1180-1181`),
following the same shape as `ElasticSearchClient.is_configured()`
(`src/codemie/clients/elasticsearch.py:27-30`):

```python
def mcp_connect_available(cfg: Config) -> bool:
    if not cfg.STANDALONE:
        return cfg.MCP_CONNECT_ENABLED
    return cfg.MCP_CONNECT_ENABLED and bool(cfg.MCP_CONNECT_URL)
```

This is the single source of truth every integration point below reads.
Outside standalone it reduces to today's check exactly — no behavior change.

### Integration point 1 — config resolution (`GET /v1/config`)

`resolve_components()` (`src/codemie/service/customer_config_service.py:157-171`)
already drops the leaderboard component when `LEADERBOARD_ENABLED` is false
(lines 168-169). Add a second post-filter of the same shape that drops the
`mcpConnect` component id when `mcp_connect_available(config)` is false.
Non-standalone requests are unaffected (helper reduces to current flag).

### Integration point 2 — assistant MCP tools (toolkit_service)

`src/codemie/service/tools/toolkit_service.py:1055` guards
`if config.MCP_CONNECT_ENABLED:` before adding MCP tools. Change the
condition to `mcp_connect_available(config)`. The existing `else` path
(skip, no tools added) already avoids a raw connection error; add a warning
log on that path so the assistant's run logs why MCP tools were skipped
(AC: "logs a warning").

### Integration point 3 — workflow MCP tool node

`src/codemie/workflows/nodes/tool_node.py:125-126` raises
`ValueError("MCP Connect is not enabled in configuration")` when
`not config.MCP_CONNECT_ENABLED`. Change the condition to
`not mcp_connect_available(config)`. This closes the gap where, today,
standalone + enabled-flag-true + empty-URL falls through into a real
connection attempt inside `MCPToolkitService.get_mcp_server_tools` instead
of this clear, existing message.

### Integration point 4 — startup warning

Add one warning logged during application startup when
`config.STANDALONE and config.MCP_CONNECT_ENABLED and not config.MCP_CONNECT_URL`
(deliberately-disabled, i.e. `MCP_CONNECT_ENABLED=False`, logs nothing — the
operator turned it off on purpose). The message lists the degraded
features: MCP Servers on assistants, workflow MCP tools, the admin MCP
page, the MCP settings tab, and the MCP onboarding steps. Locate this next
to other startup/config logging in the FastAPI lifespan/startup hook.

### Assumption

`MCPConnectClient(config.MCP_CONNECT_URL)` singleton construction at import
time (`src/codemie/service/mcp/toolkit_service.py:1994`) must not raise
when the URL is empty; this is unchanged by this spec and is assumed safe
based on current standalone Dockerfile usage (`standalone/Dockerfile:122`
already ships an empty `MCP_CONNECT_URL`).

## Acceptance Criteria

- Non-standalone: `GET /v1/config` and MCP runtime behavior identical to
  today regardless of `MCP_CONNECT_ENABLED` / `MCP_CONNECT_URL`.
- Standalone, flag true + URL set: `mcpConnect` present and MCP works.
- Standalone, flag false: `mcpConnect` absent.
- Standalone, URL empty/missing: `mcpConnect` absent.
- `mcpConnect` absent ⇒ MCP Servers section hidden on the assistant page,
  rest of the page works normally (no UI code change — existing behavior).
- `mcpConnect` absent ⇒ workflow tool selector, admin MCP page, settings
  tabs, onboarding tour all hide MCP entry points (existing behavior).
- Standalone + MCP unavailable + assistant with saved MCP servers: answers
  without MCP tools, no connection error, warning logged.
- Standalone + MCP unavailable + workflow MCP node: fails with a clear
  "MCP not available" message, not a raw connection error.
- Standalone + `MCP_CONNECT_URL` empty at startup: one warning logged,
  listing the degraded features.
- Non-standalone + `MCP_CONNECT_URL` empty at startup: no new warning.
- No new feature flag introduced; only `MCP_CONNECT_ENABLED` and
  `mcpConnect` are used.
- Tests cover standalone on/off × `MCP_CONNECT_ENABLED` on/off ×
  `MCP_CONNECT_URL` set/empty, for both the config response and runtime
  MCP behavior (see test-impact notes below).

## Non-goals

- A deployment-level MCP Connect key setting.
- A user-facing chat notice that MCP tools were skipped.
- Any change to non-standalone behavior, including startup logging.
- Dependency checks/startup warnings for other services (Elasticsearch,
  Mermaid, Redis, web search).
- UI code changes beyond tests — the UI already hides MCP when
  `mcpConnect` is absent.
- Removing or migrating MCP servers already saved on assistants.

## Test-impact notes (for the planning stage, not written here)

- `tests/codemie/configs/test_customer_config.py` (~L837,
  `PRE_EXISTING_SHIPPED_COMPONENT_IDS`) — extend for the new post-filter.
- `tests/codemie/service/tools/test_toolkit_service_headers.py`,
  `tests/codemie/workflows/test_tool_node_context.py` — already mock
  `config.MCP_CONNECT_ENABLED`; extend to mock `config.STANDALONE` /
  `config.MCP_CONNECT_URL` combinations via `mcp_connect_available`.
- `tests/codemie/standalone/test_standalone_compose_contract.py` —
  standalone compose contract, unaffected but a useful cross-check.
- UI: `codemie-ui` `NewAssistantPage.integration.test.tsx:1718-1745`
  already covers `mcpConnect`-absent hiding on the assistant page; no new
  UI test is required by this story.
