# MCP (Model Context Protocol) Integration

## Quick Summary

CodeMie integrates with MCP servers via MCP-Connect bridge for dynamic tool loading. Supports stdio/HTTP transports, custom authentication headers with placeholder resolution, credential scoping (user/project), execution context propagation, and three-level caching (service→factory→bucket).

**Category**: Integration
**Complexity**: Medium-High
**Prerequisites**: HTTP client, async Python, Pydantic, LangChain tools, MCP-Connect bridge

---

## Prerequisites

- **MCP-Connect Bridge**: Running on configured URL (default: `http://localhost:3000`)
- **MCP Server**: Either command-line tool (npx/uvx) or HTTP endpoint
- **Python Libraries**: `httpx`, `pydantic`, `cachetools`, `hashlib`
- **Configuration**: `MCP_CONNECT_URL` and related config values in `config.py`
- **Understanding**: Async/await patterns, LangChain BaseTool, caching strategies

---

## Architecture Overview

```
Request (API/Workflow)
    ↓
MCPToolkitService.get_mcp_server_tools() ← Entry point
    ↓
MCPToolkitFactory (TTL cache) ← Toolkit cache by config hash
    ↓
MCPConnectClient (HTTP) ← Bridge communication
    ↓
MCP-Connect Bridge ← Process manager
    ↓
MCP Server (stdio/HTTP) ← Actual tool provider
```

**Key Components** (src/codemie/service/mcp/):
- `models.py` - MCPServerConfig, MCPExecutionContext, MCPToolDefinition
- `client.py` - MCPConnectClient (list_tools, invoke_tool)
- `toolkit.py` - MCPTool, MCPToolkit, MCPToolkitFactory
- `toolkit_service.py` - MCPToolkitService (singleton, credential resolution)

---

## Configuration Patterns

### Pattern 1: Stdio Transport (Command-based)

```python
# src/codemie/service/mcp/models.py
from codemie.service.mcp.models import MCPServerConfig

config = MCPServerConfig(
    command="npx",  # CLI command (npx, uvx, python, etc.)
    args=["-y", "@modelcontextprotocol/server-github"],
    env={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxx"},
    auth_token="bridge_token",  # MCP-Connect auth
    single_usage=False  # Cache toolkit (default)
)
```

**XOR Validation**: Must set `command` OR `url`, not both

### Pattern 2: HTTP Transport (Remote endpoint)

```python
config = MCPServerConfig(
    type="streamable-http",  # Required for HTTP
    url="http://127.0.0.1:3001/mcp",
    headers={
        "X-API-KEY": "{{API_KEY}}",  # Placeholder resolution
        "Authorization": "Bearer {{ACCESS_TOKEN}}"
    },
    env={"API_KEY": "key_val", "ACCESS_TOKEN": "token_val"},
    auth_token="bridge_token",
    single_usage=False
)
```

**Placeholder Formats**: `{{VAR}}`, `{{nested.var}}`, `{{user.name}}`
**Normalization**: `[VAR]` → `{{VAR}}`, `$VAR` treated as literal

### Pattern 3: User Context Propagation

```python
config = MCPServerConfig(
    url="http://mcp-server/api",
    headers={
        "x-user-id": "{{user.username}}",  # Auto-resolved from context
        "x-user-name": "{{user.name}}",
        "x-project": "{{project_name}}"
    },
    type="streamable-http"
)
```

**Resolution**:
1. `user.username`, `user.name` → from `get_current_user()` context
2. Other `{{vars}}` → from `server_config.env` or integration credentials

### Pattern 4: Single-Use (Stateful Servers)

```python
config = MCPServerConfig(
    command="uvx",
    args=["stateful-mcp-server"],
    single_usage=True,  # Bypass cache
    env={"SESSION_ID": "unique"}
)
```

**Effect**: New toolkit per request, no caching

---

## Credential Resolution

### Priority Order

1. **integration_alias** (project/user scoped)
2. **settings.id** (direct settings lookup)
3. **tool_creds** (inline credentials dict)

```python
# src/codemie/rest_api/models/assistant.py
mcp_server = MCPServerDetails(
    name="GitHub MCP",
    integration_alias="github-integration",  # Priority 1
    # OR
    settings=SettingsBase(id="setting-uuid"),  # Priority 2
    # OR
    # tool_creds handled separately
)

# Automatic resolution in MCPToolkitService
tools = MCPToolkitService.get_mcp_server_tools(
    mcp_servers=[mcp_server],
    user_id="user-123",       # For user+project scope
    project_name="codemie"    # For project scope
)
```

**Scoping**: User+Project → Project → Global

---

## Execution Context

### MCPExecutionContext Pattern

```python
# src/codemie/service/mcp/models.py:MCPExecutionContext
context = MCPExecutionContext(
    user_id="user-123",
    assistant_id="asst-456",
    project_name="codemie",
    workflow_execution_id="exec-789",
    request_headers={"X-Custom-Header": "value"}
)

# NOT cached - injected at invocation time
response = await client.invoke_tool(
    server_config=config,
    tool_name="search",
    tool_args={"query": "docs"},
    execution_context=context  # Passed through
)
```

### ContextAwareMCPTool Wrapper

**Problem**: Cached tools can't store request-specific context
**Solution**: Wrapper injects context per execution

```python
# src/codemie/service/mcp/toolkit.py:ContextAwareMCPTool
class ContextAwareMCPTool(MCPTool):
    def __init__(self, original_tool: MCPTool, context: MCPExecutionContext):
        super().__init__(...)  # Copy all attributes
        self._execution_context = context

    def execute(self, **kwargs):
        return self.execute_with_context(
            execution_context=self._execution_context,
            **kwargs
        )

# Usage in toolkit_service.py
context_aware_tools = [
    ContextAwareMCPTool(tool, execution_context)
    for tool in cached_tools
]
```

**Effect**: Same cached toolkit, different context per request

---

## Caching Strategy

### Three-Level Cache

| Level | Component | TTL (config) | Key | Bypass |
|-------|-----------|--------------|-----|--------|
| 1 | MCPToolkitService | `MCP_TOOLKIT_SERVICE_CACHE_TTL` (3600s) | `base_url` | N/A |
| 2 | MCPToolkitFactory | `MCP_TOOLKIT_FACTORY_CACHE_TTL` (3600s) | SHA-256 of config | `single_usage=True` |
| 3 | MCP-Connect Bucket | N/A | MD5 bucket routing | N/A |

**Cache Key Generation** (toolkit.py:_generate_cache_key):
```python
config_dict = {
    "command": server_config.url or server_config.command,
    "args": server_config.args,
    "env": server_config.env
}
cache_key = hashlib.sha256(json.dumps(config_dict, sort_keys=True).encode()).hexdigest()
```

**Bucket Routing** (client.py:_get_bucket_no):
```python
bucket_key = server_config.env.get(BUCKET_KEY) or str(server_config)
bucket_no = hashlib.md5(bucket_key.encode()).digest() % MCP_CONNECT_BUCKETS_COUNT
# For persistent: BUCKET_KEY = conversation_id (conversation affinity)
```

**Cache Invalidation**:
```python
MCPToolkitService.reset_instance()  # All caches
service.clear_cache()  # Factory cache only
service.toolkit_factory.remove_toolkit_from_cache(server_config)  # Specific toolkit
```

---

## Service Integration

### Primary Entry Point

```python
# src/codemie/service/mcp/toolkit_service.py:get_mcp_server_tools
tools = MCPToolkitService.get_mcp_server_tools(
    mcp_servers=[server1, server2],
    user_id=user.id,
    project_name="codemie",
    assistant_id="asst-123",
    workflow_execution_id="exec-456",
    request_headers={"X-Request-ID": "req-789"}
)
# Returns: list[MCPTool] (context-aware wrappers)
```

**Processing Pipeline**:
1. Resolve credentials (alias → settings → inline)
2. Normalize placeholders (URL, command, headers)
3. Process args with preprocessor (optional)
4. Get/create toolkit (cached unless `single_usage=True`)
5. Wrap tools with `ContextAwareMCPTool`

### Workflow Integration

```python
# src/codemie/workflows/nodes/tool_node.py:ToolNode
class ToolNode(BaseNode[AgentMessages]):
    def execute(self, state_schema, execution_context):
        if self._tool_config.mcp_server:
            return self._execute_mcp_tool(state_schema)
        # else: regular tool execution

    def _execute_mcp_tool(self, state_schema):
        context = MCPExecutionContext(
            user_id=self.user.id,
            assistant_id=self._assistant_id,
            project_name=self._project_name,
            workflow_execution_id=self.execution_id,
            request_headers=self.request_headers
        )

        toolkit = MCPToolkitService.get_instance().get_toolkit(
            server_config=mcp_server_config,
            execution_context=context
        )

        tool = toolkit.get_tool(tool_name)
        result = tool.execute(**processed_args)
        return result
```

### Direct Client Usage

```python
# src/codemie/service/mcp/client.py
from codemie.service.mcp.client import MCPConnectClient

client = MCPConnectClient(base_url="http://localhost:3000")

# List tools
tools = await client.list_tools(server_config, context)
# Returns: list[MCPToolDefinition]

# Invoke tool
response = await client.invoke_tool(
    server_config=server_config,
    tool_name="search",
    tool_args={"query": "test"},
    execution_context=context
)
# Returns: MCPToolInvocationResponse(content=[...], isError=False)
```

---

## Error Handling

### Exception Types

| Exception | Cause | Action |
|-----------|-------|--------|
| `MCPToolLoadException` | Tool discovery failed | Check server config, auth_token, server status |
| `MCPToolExecutionError` | Tool execution failed | Validate args, check MCP server logs |
| `httpx.ConnectError` | Bridge unreachable | Start MCP-Connect, verify network |
| `httpx.HTTPStatusError` | HTTP 4xx/5xx | Check auth (401/403), endpoint (404), bridge (500) |
| `ValidationError` | Invalid response | Verify MCP server implementation |
| `ValueError` | XOR/placeholder error | Fix config (command XOR url, {{VAR}} format) |

### Error Recovery Pattern

```python
try:
    tools = MCPToolkitService.get_mcp_server_tools(mcp_servers, ...)
except MCPToolLoadException as e:
    logger.error(f"Failed to load from {e.server_name}: {e.original_error}")
    # Fallback: skip server or retry
except MCPToolExecutionError as e:
    logger.error(f"Tool execution failed: {e}")
    # Return error to user or fallback
```

---

## Validation Rules

### Rule 1: XOR (command vs url)

```python
# ✅ VALID
MCPServerConfig(command="npx", args=[...])
MCPServerConfig(url="http://...", type="streamable-http")

# ❌ INVALID - ValueError
MCPServerConfig(command="npx", url="http://...")  # Both
MCPServerConfig()  # Neither
```

**Enforced**: `models.py:MCPServerConfig._ensure_command_xor_url()`

### Rule 2: Placeholder Format

```python
# ✅ VALID
"{{API_KEY}}"       # Target format
"[API_KEY]"         # Normalized to {{API_KEY}}
"{{user.name}}"     # Nested/context vars

# ❌ INVALID
"${API_KEY}"        # Bash syntax not supported
"{API_KEY}"         # Single braces not recognized
```

**Enforced**: `toolkit_service.py:_normalize_placeholders()`

### Rule 3: Single-Usage Cache Bypass

```python
config = MCPServerConfig(command="...", single_usage=True)
toolkit = service.get_toolkit(config, use_cache=True)
# Still bypasses cache: should_use_cache = use_cache and not single_usage
```

**Enforced**: `toolkit_service.py:get_toolkit()` line 497

### Rule 4: Bucket Routing

```python
# For persistent servers (single_usage=False)
if not mcp_server_single_usage and conversation_id:
    server_config.env[BUCKET_KEY] = conversation_id
    # Same conversation → same bucket → reuses server instance
```

**Enforced**: `toolkit_service.py:_prepare_server_config()` line 282-283

---

## Response Processing

### Content Types

```python
# src/codemie/service/mcp/models.py:MCPToolContentItem
response = MCPToolInvocationResponse(content=[...], isError=False)

# Content types:
# - "text": Plain text (item.text)
# - "error": Error message (item.text)
# - "image": Base64 image (item.data + item.mimeType)
# - "image_url": Image URL (item.image_url)
# - "data": Structured JSON (item.data)

# Processing (toolkit.py:_convert_mcp_response_to_tool_message)
if all(item.is_text() for item in response.content):
    return "\n".join(item.text for item in response.content)
else:
    # Convert images to image_url format, return JSON array
    return json.dumps(messages, ensure_ascii=False)
```

### Token Limiting

```python
# src/codemie/service/mcp/toolkit.py:MCPTool._limit_output_content
# Limit: MCP_TOOL_TOKENS_SIZE_LIMIT (default 100,000)
def limit_output(response):
    text = "\n".join(str(item) for item in response.content if not item.is_image())
    token_count = len(encoding.encode(text))

    if token_count > tokens_size_limit:
        truncated = encoding.decode(tokens[:tokens_size_limit])
        if throw_truncated_error:
            raise TruncatedOutputError(truncated)
        return f"[Truncated] Ratio: {ratio}. Output: {truncated}"

    return response
```

---

## Configuration Catalog API

### REST Endpoints

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/v1/mcp-configs` | POST | Admin | Create MCP config |
| `/v1/mcp-configs` | GET | User | List configs (paginated, filtered) |
| `/v1/mcp-configs/{id}` | GET | User | Get config by ID |
| `/v1/mcp-configs/{id}` | PUT | Admin | Update config |
| `/v1/mcp-configs/{id}` | DELETE | Admin | Delete config (if usage_count=0) |

**Filters**: `category`, `search`, `is_public`, `active_only`

### Database Model

```python
# src/codemie/rest_api/models/mcp_config.py:MCPConfig
MCPConfig(
    id="uuid",
    name="GitHub MCP",
    description="GitHub API via MCP",
    config=MCPServerConfigData(...),
    categories=["Development", "API"],  # Max 3
    required_env_vars=[
        MCPVariableDefinition(name="GITHUB_TOKEN", description="...", required=True)
    ],
    is_public=True,
    is_system=True,
    usage_count=42,
    is_active=True
)
```

---

## Examples

### Add MCP Server to Assistant

```python
# src/codemie/rest_api/models/assistant.py
from codemie.rest_api.models.assistant import MCPServerDetails

mcp_server = MCPServerDetails(
    name="GitHub Tools",
    description="GitHub API access",
    enabled=True,
    config=MCPServerConfig(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        env={"GITHUB_PERSONAL_ACCESS_TOKEN": "{{GITHUB_TOKEN}}"}
    ),
    integration_alias="github-integration",
    tools_tokens_size_limit=50000
)

assistant.mcp_servers = [mcp_server]
# Tools auto-loaded in assistant_agent.py:__init__()
```

### Complete Integration Example

```python
from codemie.service.mcp.client import MCPConnectClient
from codemie.service.mcp.toolkit_service import MCPToolkitService
from codemie.service.mcp.models import MCPServerConfig, MCPExecutionContext

# 1. Configure server
server_config = MCPServerConfig(
    type="streamable-http",
    url="http://localhost:3001/mcp",
    headers={"X-API-KEY": "{{API_KEY}}"},
    env={"API_KEY": "secret"},
    single_usage=False
)

# 2. Create context
context = MCPExecutionContext(
    user_id="user-123",
    project_name="codemie",
    request_headers={"X-Request-ID": "req-789"}
)

# 3. Get toolkit (cached)
service = MCPToolkitService.get_instance()
toolkit = service.get_toolkit(server_config=server_config, execution_context=context)

# 4. Execute tool
tool = toolkit.get_tool("search")
result = tool.execute(query="MCP docs")
```

---

## Troubleshooting

**Issue**: `MCPToolLoadException`
**Solution**: Check `MCP_CONNECT_URL`, `auth_token`, server command/URL validity

**Issue**: Headers not resolved
**Solution**: Use `{{VAR}}` format, verify env vars or credentials exist

**Issue**: Cache stale
**Solution**: Call `service.clear_cache()` or adjust TTL config

**Issue**: Bucket routing failed
**Solution**: Set `env[BUCKET_KEY]=conversation_id` for persistent servers

---

## Next Steps

- **LangChain Agents** → `.codemie/guides/agents/langchain-agent-patterns.md`
- **LangGraph Workflows** → `.codemie/guides/workflows/langgraph-workflows.md`
- **Security** → `.codemie/guides/development/security-patterns.md`
- **Configuration** → `.codemie/guides/development/configuration-patterns.md`

---

## References

### Source Files
- `src/codemie/service/mcp/models.py` - Pydantic models, XOR validation
- `src/codemie/service/mcp/client.py` - HTTP client, bucket routing
- `src/codemie/service/mcp/toolkit.py` - Tool/toolkit wrappers, caching
- `src/codemie/service/mcp/toolkit_service.py` - Service layer, credential resolution
- `src/codemie/workflows/nodes/tool_node.py` - Workflow integration
- `src/codemie/rest_api/models/mcp_config.py` - Database models
- `src/codemie/rest_api/routers/mcp_config.py` - REST API

### Related Guides
- External Services → `.codemie/guides/integration/external-services.md`
- Agent Tools → `.codemie/guides/agents/agent-tools.md`
- Configuration → `.codemie/guides/development/configuration-patterns.md`

### External Resources
- [MCP Specification](https://modelcontextprotocol.io/)
- [MCP Servers](https://github.com/modelcontextprotocol/servers)
- [MCP-Connect](https://github.com/QuantGeekDev/mcp-connect)
