# MCP Integration

## MCP Configuration

Keep MCP configuration and authentication behavior behind existing service and router modules.

| Avoid | Prefer |
|---|---|
| Adding MCP auth logic to unrelated routers | Use MCP config/auth routers and services |
| Treating MCP tools as static app code | Load/configure through existing MCP service boundaries |

Evidence: MCP config router is registered at `src/codemie/rest_api/main.py:695`; MCP auth router is included at `src/codemie/rest_api/main.py:713`.

## Slow-Start Troubleshooting

If an MCP server reports 500 errors only on first connection (e.g. during `npx`/`uvx` dependency download), the initialization timeout is the most likely cause.

| Variable | Service | Default | Unit | Purpose |
|---|---|---|---|---|
| `MCP_SERVER_INIT_TIMEOUT` | CodeMie | `300.0` | seconds | Backend guard — `create_toolkit` times out if server start exceeds this |
| `MCP_CONNECT_INIT_TIMEOUT` | MCP-Connect | `30000` | **milliseconds** | Bridge-side guard around `session.initialize()` |
| `MCP_CLIENT_TIMEOUT` | CodeMie | `300.0` | seconds | Per-invocation timeout for `tools/call` requests |

The effective initialization budget is `min(MCP_SERVER_INIT_TIMEOUT, MCP_CONNECT_INIT_TIMEOUT / 1000)` — the smaller value wins. Raise **both** variables together. Example for a 120-second budget:

```
# CodeMie deployment
MCP_SERVER_INIT_TIMEOUT=120

# MCP-Connect deployment
MCP_CONNECT_INIT_TIMEOUT=120000
```

The timeout applies to all transports (stdio, SSE, streamable-http) and both cached and single-use modes.

## Server Instructions

An MCP server may declare usage instructions at handshake. When the `mcpServerInstructions` feature flag is enabled, CodeMie appends them to the system prompt, after the assistant's own prompt, in a delimited block labelled as untrusted server text.

| Variable | Service | Default | Purpose |
|---|---|---|---|
| `MCP_INSTRUCTIONS_NONCE_SECRET` | CodeMie | unset | Keys the block delimiters. Set the same value of at least 32 characters on every API replica so a conversation's prompt stays identical across pods and restarts. |

When unset, each process uses its own key. Prompts then differ between instances and restarts, which breaks provider prompt caching, and a warning is logged once per process when instructions are first rendered. A value shorter than 32 characters fails at startup.

Instructions are rendered only for servers whose tools are in the agent's final tool list. Shadowed or filtered servers contribute nothing.
