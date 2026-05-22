# MCP Integration

## MCP Configuration

Keep MCP configuration and authentication behavior behind existing service and router modules.

| Avoid | Prefer |
|---|---|
| Adding MCP auth logic to unrelated routers | Use MCP config/auth routers and services |
| Treating MCP tools as static app code | Load/configure through existing MCP service boundaries |

Evidence: MCP config router is registered at `src/codemie/rest_api/main.py:695`; MCP auth router is included at `src/codemie/rest_api/main.py:713`.
