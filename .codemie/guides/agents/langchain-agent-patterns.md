# LangChain Agent Implementation Patterns

AI-optimized guide to LangChain agent patterns in CodeMie

---

## Agent Creation

### Agent Types & Configuration

| Type | Factory Function | Use Case | Schema | Source |
|------|-----------------|----------|--------|--------|
| **Tool-calling** | `create_tool_calling_agent()` | Default for most LLMs | No | `assistant_agent.py:301-310` |
| **React JSON** | `create_json_chat_agent()` | Bedrock/React-style LLMs | No | `assistant_agent.py:280-288` |
| **Structured** | `create_structured_tool_calling_agent()` | Tool-calling + output schema | Yes | `assistant_agent.py:290-299` |

### AgentExecutor Setup Pattern

**Location**: `src/codemie/agents/assistant_agent.py:215-224`

```python
agent = create_tool_calling_agent(llm=llm, tools=self.tools, prompt=prompt)
return AgentExecutor(
    agent=agent, tools=self.tools,
    max_iterations=self.recursion_limit,
    handle_parsing_errors=True,  # Auto-retry on parse failures
    return_intermediate_steps=True,  # Include tool calls
    callbacks=callbacks,
)
```

**Key Parameters**:
- `tools`: List of BaseTool instances
- `recursion_limit`: Max iterations (default: 50)
- `callbacks`: Monitoring, streaming handlers
- `handle_parsing_errors=True`: Graceful LLM output failures

### Initialization Components

**Location**: `src/codemie/agents/assistant_agent.py:108-156`

| Component | Purpose | Required |
|-----------|---------|----------|
| `agent_name` | Agent identifier | Yes |
| `tools` | Available tools list | Yes |
| `system_prompt` | Agent instructions | Yes |
| `llm_model` | Model identifier | Yes |
| `recursion_limit` | Max iterations | No (default: 50) |
| `callbacks` | Monitoring/streaming | No |

**Anti-Pattern**: Direct LLM calls bypass AgentExecutor → no tool calling. Always wrap with AgentExecutor.

---

## Prompt Engineering

### Template Structure

**Location**: `src/codemie/agents/assistant_agent.py:593-614`

| Order | Component | Purpose | Optional |
|-------|-----------|---------|----------|
| 1 | `SystemMessagePromptTemplate` | Agent persona/instructions | No |
| 2 | `MessagesPlaceholder("chat_history")` | Conversation history | Yes |
| 3 | `MessagesPlaceholder("messages")` | Additional messages | Yes |
| 4 | `HumanMessagePromptTemplate("{{input}}")` | User input | No |
| 5 | `MessagesPlaceholder("agent_scratchpad")` | Tool calls/reasoning | No |

**Template Format**: Jinja2 (`template_format="jinja2"`)
**Variables**: `{{input}}`, `{{tool_name}}`, `{{observation}}`

### Model-Specific Adaptations

**Location**: `src/codemie/agents/assistant_agent.py:594-603`

| Model Type | Adaptation | Implementation |
|------------|------------|----------------|
| No system prompt support | Use HumanMessage | Replace SystemMessagePromptTemplate with HumanMessagePromptTemplate |
| React-style (Bedrock) | React template | Append `json_react_template_v2` + custom stop sequences |

**React Pattern**: `system_prompt + json_react_template_v2` (see `assistant_agent.py:96-104`)

---

## Tool Integration

### Tool Configuration Pattern

**Location**: `src/codemie/agents/assistant_agent.py:255-278`

| Step | Action | Purpose |
|------|--------|---------|
| 1 | Create metadata dict | Pass context (request_id, user_id, llm_model, etc.) |
| 2 | Assign callbacks | Enable monitoring/streaming |
| 3 | Update tool metadata | Inject context into tools |
| 4 | Set `handle_tool_error` | Graceful error handling |

**Metadata Keys**: `REQUEST_ID`, `USER_ID`, `USER_NAME`, `LLM_MODEL`, `AGENT_NAME`
**Access**: Tools read `self.metadata[REQUEST_ID]` in `execute()` method

### Smart Tool Selection

**Location**: `src/codemie/agents/smart_tool_selector.py:55-137`

```python
selector = SmartToolSelector(tool_registry=all_tools, default_limit=3)
tool_ids, tools = selector.select_tools(query="search code", limit=3, history=msgs)
```

**Process**:
1. Elasticsearch semantic search via `ToolkitLookupService`
2. Hybrid search (semantic + keyword) with RRF + reranking
3. Filter to assistant's available tools
4. Return top-N matches

**Context Enhancement** (`smart_tool_selector.py:146-195`): Last 5 messages (200 chars each) appended to query

**Anti-Pattern**: Tools without `handle_tool_error=True` crash agent execution. Always enable error handling.

---

## Streaming Responses

### Callback Architecture

**Location**: `src/codemie/agents/callbacks/agent_streaming_callback.py:19-27`

| Callback | Purpose | When Used |
|----------|---------|-----------|
| `MonitoringCallback` | Metrics, logging, observability | Always |
| `AgentStreamingCallback` | Token-by-token streaming | When `stream_steps=True` |
| `AgentInvokeCallback` | Non-streaming fallback | When `stream_steps=False` |

**Setup**: `src/codemie/agents/assistant_agent.py:172-193`

### Streaming Events

**Location**: `src/codemie/agents/assistant_agent.py:526-548`

| Chunk Type | Trigger | Content |
|------------|---------|---------|
| `actions` | Tool call initiated | Tool name + input |
| `steps` | Tool call completed | Tool observation/output |
| `output` | Final response | Agent's answer |

**Token Streaming** (`agent_streaming_callback.py:70-81`): `on_llm_new_token()` → update thought → send to generator

**Tool Streaming** (`agent_streaming_callback.py:127-159`):
1. `on_tool_start()` → set thought with tool name/input → send
2. `on_tool_end()` → update thought with result → send → reset

**Flow**: LLM/tool event → callback → ThreadedGenerator → client receives JSON

---

## Memory Management

### History Processing Pipeline

**Location**: `src/codemie/agents/assistant_agent.py:322-330, 570-584`

| Step | Function | Purpose |
|------|----------|---------|
| 1 | `_transform_history()` | Convert `ChatMessage` → `HumanMessage`/`AIMessage` |
| 2 | `_filter_history()` | Remove empty messages |
| 3 | `_get_inputs()` | Build input dict with `input` + `chat_history` |

**Conversion Rules**:
- `ChatRole.USER` → `HumanMessage(content=...)`
- `ChatRole.ASSISTANT` → `AIMessage(content=...)`

### Prompt Integration

```python
# Template
MessagesPlaceholder("chat_history", optional=True)

# Runtime
inputs = {"input": "query", "chat_history": [HumanMessage(...), AIMessage(...)]}
```

**Benefit**: Dynamic history injection without rebuilding template

**Anti-Pattern**: Unbounded history → token limit exceeded. Window to last 5-10 messages or summarize.

---

## Error Handling & Debugging

### Error Handling Patterns

| Pattern | Location | Configuration | Behavior |
|---------|----------|---------------|----------|
| **Parsing errors** | `assistant_agent.py:215-224` | `handle_parsing_errors=True` | Auto-retry with error feedback |
| **Tool errors** | `assistant_agent.py:255-278` | `tool.handle_tool_error=True` | Log error, continue execution |
| **No tools fallback** | `assistant_agent.py:199-202` | Automatic | Use PureChatChain instead |
| **Top-level exceptions** | `assistant_agent.py:353-360` | Automatic | Log + return error string |

### Common Issues

| Issue | Symptom | Solution |
|-------|---------|----------|
| Tool not called | Agent ignores tools | Improve tool `description` clarity |
| Parsing error | "Could not parse output" | Enable `handle_parsing_errors=True` |
| Token limit | "Input too long" | Window `chat_history` (5-10 messages) |
| Timeout | Infinite loop | Lower `max_iterations` |
| Empty output | Returns "" | Enable `handle_tool_error=True` |

### Debugging

```python
# Enable verbose mode
AgentExecutor(agent=agent, tools=tools, verbose=True)

# Inspect intermediate steps
response = agent_executor.invoke(inputs)
for action, obs in response['intermediate_steps']:
    print(f"Tool: {action.tool}, Input: {action.tool_input}, Output: {obs}")
```

**Insights**: Tool call sequence, inputs/outputs, reasoning steps

---

## References

- **Agent Implementation**: `src/codemie/agents/assistant_agent.py`
- **Smart Tool Selection**: `src/codemie/agents/smart_tool_selector.py`
- **Streaming Callbacks**: `src/codemie/agents/callbacks/agent_streaming_callback.py`
- **Related Docs**:
  - [Agent Tools](./agent-tools.md) - Tool creation patterns
  - [Layered Architecture](../architecture/layered-architecture.md) - Service integration
  - [REST API Patterns](../api/rest-api-patterns.md) - API → Agent flow
- **External**: [LangChain Agents Docs](https://python.langchain.com/docs/modules/agents/)
