# Agent Tool Patterns

## Tool Ownership

Put reusable tool integrations under `codemie_tools`; put agent runtime adapters under `codemie/agents/tools`.

| Avoid | Prefer |
|---|---|
| Embedding provider API calls inside prompts or routers | Implement a tool or toolkit class |
| Mixing platform tool schema logic with provider clients | Keep schema/adapters near `src/codemie/agents/tools/` |

Evidence: agent tools live under `src/codemie/agents/tools/`; reusable integrations live under `src/codemie_tools/`.

## Schema Compatibility

Keep tool schemas compatible with LangChain/LangGraph expectations.

| Avoid | Prefer |
|---|---|
| Returning complex unserialized Python objects | Return structured, serializable outputs |
| Changing schema behavior without tests | Add focused tests under `tests/codemie/agents/tools/` |

Evidence: schema compatibility code exists at `src/codemie/agents/tools/schema_compatibility.py`; tests exist under `tests/codemie/agents/tools/test_schema_compatibility.py`.
