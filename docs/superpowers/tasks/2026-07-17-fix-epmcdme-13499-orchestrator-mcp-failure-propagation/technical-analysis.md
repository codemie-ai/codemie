# Technical Analysis: Fix Orchestrator MCP Failure Propagation

## Codebase Findings

### Affected Components

**Primary file**: `src/codemie/service/tools/assistant_factory.py`
- Function: `create_assistant_executors()` (lines 89-158)
- Issue location: Exception handler at line 155-156
- Purpose: Creates LangGraph agent executors for sub-assistants used by orchestrator agents

**Related files**:
- `src/codemie/service/assistant/assistant_engine_builder.py` - Calls `create_assistant_executors()`
- `src/codemie/agents/langgraph_agent.py` - Consumes the returned executors
- `src/codemie/service/mcp/toolkit_service.py` - Source of `MCPToolLoadException`
- `src/codemie/rest_api/routers/assistant.py` - API error handling layer

### Current Behavior

**Exception handling in `assistant_factory.py:155-156`**:
```python
except Exception as e:
    logger.error(f"Failed to create executor for assistant {assistant.id}: {str(e)}")
    # Execution continues - no re-raise
```

When an `MCPToolLoadException` occurs during subagent initialization:
1. Error is logged
2. Subagent is skipped (not added to `executors` list)
3. Function returns empty or partial list
4. Orchestrator builds with `has_subagents=False`
5. LLM generates hallucinated responses instead of routing to subagents

### Error Propagation Path

**Direct subagent invocation** (works correctly):
```
MCP error → MCPToolLoadException → AssistantFactory.build() → 
assistant_service.py → rest_api/routers/assistant.py:2285 → 
ExtendedHTTPException → User sees error
```

**Via orchestrator** (broken):
```
MCP error → MCPToolLoadException → caught at assistant_factory.py:155 → 
logged but swallowed → returns [] → orchestrator builds without subagents →
LLM hallucinates → User sees misleading success
```

### MCP Exception Hierarchy

From `src/codemie/service/mcp/models.py`:
```python
class MCPToolLoadException(Exception):
    """Custom exception for MCP tool loading failures"""
    def __init__(self, server_name: str, original_error: Exception):
        ...
```

Raised in `toolkit_service.py:_process_single_mcp_server()` when:
- MCP server is unreachable
- Invalid URL configuration
- Authentication failures (some cases)
- Connection timeouts

### API Error Handling

From `rest_api/routers/assistant.py:2278-2298`:
```python
except MCPAuthenticationRequiredException:
    raise  # Special handling for auth
except ExtendedHTTPException as ehe:
    raise ehe
except Exception as e:
    # Catches MCPToolLoadException and converts to proper error response
    error = _create_assistant_error("Assistant Error", ...)
    raise error from e
```

This layer properly handles `MCPToolLoadException` when it reaches the API.

## Risk Indicators

### Low Risk Factors
1. **Targeted change**: Single exception handler modification
2. **Well-defined exception type**: `MCPToolLoadException` is a specific, documented exception
3. **Existing error handling**: API layer already handles this exception properly
4. **No new behavior**: Just exposing existing error path that works for direct invocation

### Compatibility Considerations
1. **Backwards compatibility**: Other exception types continue to be caught and logged
2. **Error surfacing**: Users will now see MCP configuration errors they should have seen before
3. **Orchestrator contracts**: No change to LangGraph agent initialization contracts

### Test Coverage
- Existing tests for direct subagent invocation cover MCP error handling
- May need integration test for orchestrator + broken MCP subagent scenario
- No changes to MCP client or toolkit service logic

## Implementation Notes

### Change Required
In `src/codemie/service/tools/assistant_factory.py:155`:

```python
except Exception as e:
    logger.error(f"[...] ERROR Failed to create executor for assistant {assistant.id}: {e}")
    # Add specific handling for MCP failures
    if isinstance(e, MCPToolLoadException):
        raise  # Re-raise to propagate to API layer
    # Continue catching other exceptions for backwards compatibility
```

### Import Required
Add to imports at top of file:
```python
from codemie.service.mcp.models import MCPToolLoadException
```

### Debugging Code Removal
The following debug logging added during investigation should be **retained** as it provides valuable diagnostics:
- `assistant_factory.py`: MCP error logging (lines added)
- `assistant_engine_builder.py`: Subagent creation logging
- `langgraph_agent.py`: Agent initialization logging
- `toolkit_service.py`: MCP server processing logging (fix `.get()` → `getattr()`)

These logs helped identify the root cause and will help diagnose future issues.

## Dependencies

**Imports needed**:
- `MCPToolLoadException` from `codemie.service.mcp.models`

**No changes needed to**:
- MCP client
- Toolkit service
- LangGraph agent
- API routers

## Testing Strategy

1. **Manual verification**: 
   - Configure orchestrator with subagent that has invalid MCP server URL
   - Invoke orchestrator
   - Verify proper error response (not hallucinated success)

2. **Regression check**:
   - Verify direct subagent invocation still works
   - Verify orchestrator with valid MCP configuration still works
   - Verify non-MCP exceptions still log but don't crash

3. **Integration test** (optional follow-up):
   - Add test case: orchestrator + subagent with broken MCP → expects error response
