# Custom Tool Creation

## Quick Summary

Create custom tools in CodeMie by extending `CodeMieTool`, defining Pydantic schemas for inputs, implementing `execute()` method, and organizing in toolkits for agent integration. Includes error handling, token limits, and semantic tool discovery.

**Category**: Agents | **Complexity**: Medium
**Prerequisites**: Pydantic, LangChain BaseTool, async/await, type hints

---

## Step-by-Step Guide

### Step 1: Define Tool Schema

```python
from pydantic import BaseModel, Field

class MyToolInput(BaseModel):
    query: str = Field(description="User query text to process")
    max_results: int = Field(default=10, description="Maximum number of results to return")
    filter_type: Optional[str] = Field(default=None, description="Optional filter ('recent', 'popular')")
```

**Best Practices**:
- Rich descriptions for LLM guidance
- Type hints for validation
- Defaults for optional params
- Validators for complex checks

### Step 2: Create ToolMetadata

```python
# src/codemie/agents/tools/my_tool/my_tool_vars.py
from codemie_tools.base.models import ToolMetadata

MY_CUSTOM_TOOL = ToolMetadata(
    name="my_custom_tool",
    description="""
    Tool to [describe what it does].
    Use when you need to [specific use case].

    REQUIRED parameters:
    - 'query': The user input text
    - 'max_results': Number of results (default 10)

    OPTIONAL parameters:
    - 'filter_type': Apply filtering ('recent', 'popular', None)
    """,
    react_description="[Optimized description for ReAct agents - more concise]"
)
```

**Purpose**: Enables semantic tool discovery and LLM tool selection

### Step 3: Implement Tool Class

```python
# src/codemie/agents/tools/my_tool/my_tool.py
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie.configs import logger

class MyCustomTool(CodeMieTool):
    """Custom tool with error handling and validation."""

    # Tool metadata
    base_name: str = MY_CUSTOM_TOOL.name
    name: str = MY_CUSTOM_TOOL.name
    description: str = MY_CUSTOM_TOOL.description
    args_schema: Type[BaseModel] = MyToolInput

    # Configuration
    tokens_size_limit: int = 15000
    api_endpoint: Optional[str] = None

    def execute(self, query: str, max_results: int = 10, filter_type: Optional[str] = None) -> str:
        """Execute tool logic with error handling."""
        try:
            # Validate inputs
            if not query or len(query.strip()) == 0:
                return "Error: Query cannot be empty"

            # Execute tool logic
            results = self._perform_search(query, max_results, filter_type)

            # Check token limits
            if self._exceeds_token_limit(results):
                results = self._truncate_results(results, self.tokens_size_limit)
                logger.warning(f"{self.name}: Results truncated to token limit")

            return self._format_results(results)

        except Exception as e:
            logger.error(f"{self.name} execution failed: {e}", exc_info=True)
            return f"Error executing tool: {str(e)}"

    def _perform_search(self, query: str, max_results: int, filter_type: Optional[str]):
        """Implement actual search logic."""
        results = []
        # Call external API, database, or service
        if self.api_endpoint:
            response = self._call_api(query, max_results, filter_type)
            results = response.get("data", [])
        return results[:max_results]

    def _exceeds_token_limit(self, results) -> bool:
        from codemie.core.utils import calculate_tokens
        return calculate_tokens(str(results)) > self.tokens_size_limit

    def _truncate_results(self, results, token_limit: int):
        """Truncate results to fit within token limit."""
        return results[:len(results) // 2]

    def _format_results(self, results) -> str:
        """Format results for agent consumption."""
        if not results:
            return "No results found"

        formatted = "Search Results:\n\n"
        for idx, result in enumerate(results, 1):
            formatted += f"{idx}. {result}\n"
        return formatted
```

**Source**: `src/codemie/agents/tools/code/tools.py`, `src/codemie/agents/tools/kb/search_kb.py`

### Step 4: Create Toolkit

```python
# src/codemie/agents/tools/my_tool/my_tool_toolkit.py
from codemie.agents.tools.base import BaseToolkit
from codemie_tools.base.models import ToolKit, Tool, ToolSet

class MyToolkitUI(ToolKit):
    """UI representation for toolkit."""
    toolkit: ToolSet = ToolSet.CUSTOM_TOOLS
    tools: List[Tool] = [Tool.from_metadata(MY_CUSTOM_TOOL)]

class MyCustomToolkit(BaseToolkit):
    """Toolkit for custom tool instantiation and configuration."""

    api_endpoint: Optional[str] = None

    @classmethod
    def get_tools_ui_info(cls):
        return MyToolkitUI().model_dump()

    def get_tools(self, **config) -> List[BaseTool]:
        """Instantiate tools with configuration."""
        tool = MyCustomTool(
            api_endpoint=self.api_endpoint or config.get("api_endpoint"),
            tokens_size_limit=config.get("tokens_size_limit", 15000)
        )
        return [tool]
```

**Pattern**: Toolkit groups related tools, provides UI metadata, handles configuration

**Source**: `src/codemie/agents/tools/code/code_toolkit.py`, `src/codemie/agents/tools/base/base_toolkit.py`

### Step 5: Register Toolkit

```python
# src/codemie/agents/tools/__init__.py
from .my_tool.my_tool_toolkit import MyCustomToolkit

__all__ = [
    "MyCustomToolkit",  # Add here for imports
    # ...
]
```

---

## Schema Patterns

### Basic Schema with Validation

```python
from pydantic import BaseModel, Field, validator

class ToolInput(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=100)

    @validator('query')
    def validate_query(cls, v):
        if not v.strip():
            raise ValueError("Query cannot be empty or whitespace")
        return v.strip()
```

### Complex Schema with Nested Models

```python
class FilterConfig(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    categories: List[str] = Field(default_factory=list)

class AdvancedToolInput(BaseModel):
    query: str
    filters: Optional[FilterConfig] = None
    sort_by: str = Field(default="relevance")
```

---

## Error Handling Patterns

### Graceful Error Recovery

```python
def execute(self, query: str, **kwargs):
    """Execute with comprehensive error handling."""
    try:
        # Validate inputs
        if not self._validate_input(query):
            return "Invalid input: Query must be non-empty string"

        # Attempt primary operation
        result = self._primary_operation(query)

    except ConnectionError as e:
        # Retry with exponential backoff
        logger.warning(f"Connection failed, retrying: {e}")
        result = self._retry_with_backoff(query)

    except ValueError as e:
        # Return user-friendly error
        return f"Invalid parameter: {str(e)}"

    except Exception as e:
        # Log and return generic error
        logger.error(f"Unexpected error in {self.name}: {e}", exc_info=True)
        return f"Tool execution failed. Please try again."

    return result
```

### Token Limit Handling

```python
# src/codemie/agents/tools/kb/search_kb.py:42-48
class SearchKBTool(CodeMieTool):
    truncate_message: str = (
        "The query provided to this tool is overly broad, which resulted in "
        "a truncated output. **Please ask the user to narrow down their query** "
        "or provide more specific details. Below is the truncated output:\n"
    )
    tokens_size_limit: int = Field(default_factory=lambda: 20000)
```

**Source**: `src/codemie/agents/tools/kb/search_kb.py`

---

## Testing Custom Tools

### Unit Tests

```python
# tests/codemie/agents/tools/my_tool/test_my_custom_tool.py
import pytest

def test_tool_execution_valid_input():
    """Test tool executes successfully with valid input."""
    tool = MyCustomTool()
    result = tool.execute(query="test query", max_results=5)
    assert result is not None
    assert "Error" not in result

def test_tool_execution_empty_query():
    """Test tool handles empty query gracefully."""
    tool = MyCustomTool()
    result = tool.execute(query="", max_results=5)
    assert "Error" in result

def test_toolkit_get_tools():
    """Test toolkit returns configured tools."""
    toolkit = MyCustomToolkit(api_endpoint="https://api.example.com")
    tools = toolkit.get_tools()
    assert len(tools) == 1
    assert tools[0].name == "my_custom_tool"

def test_token_limit_enforcement():
    """Test results are truncated when exceeding token limit."""
    tool = MyCustomTool(tokens_size_limit=100)
    large_query = "x" * 1000
    result = tool.execute(query=large_query, max_results=100)
    from codemie.core.utils import calculate_tokens
    assert calculate_tokens(result) <= tool.tokens_size_limit
```

### Integration Test with Agent

```python
def test_tool_with_agent():
    """Test custom tool works with AgentExecutor."""
    from langchain.agents import AgentExecutor, create_tool_calling_agent
    from langchain_core.prompts import ChatPromptTemplate

    toolkit = MyCustomToolkit()
    tools = toolkit.get_tools()

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a helpful assistant."),
        ("human", "{input}"),
    ])

    llm = # ... initialize LLM
    agent = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools)

    result = executor.invoke({"input": "Search for Python documentation"})
    assert result is not None
```

**See**: [Testing Patterns](../testing/testing-patterns.md) for comprehensive testing strategies

---

## Complete Example

Full tool implementation with error handling and token limits:

```python
# src/codemie/agents/tools/my_tool/my_tool.py
from typing import Optional, Type
from pydantic import BaseModel, Field
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.models import ToolMetadata
from codemie.configs import logger

# Schema
class MyToolInput(BaseModel):
    query: str = Field(description="Search query text")
    max_results: int = Field(default=5, ge=1, le=20, description="Max results (1-20)")

# Metadata
MY_TOOL = ToolMetadata(
    name="my_custom_tool",
    description="Tool description with clear use cases and parameters",
)

# Tool Class
class MyCustomTool(CodeMieTool):
    base_name: str = MY_TOOL.name
    name: str = MY_TOOL.name
    description: str = MY_TOOL.description
    args_schema: Type[BaseModel] = MyToolInput
    tokens_size_limit: int = 10000

    def execute(self, query: str, max_results: int = 5) -> str:
        try:
            if not query.strip():
                return "Error: Query cannot be empty"

            results = self._perform_search(query, max_results)
            return self._format_results(results)

        except Exception as e:
            logger.error(f"Tool execution failed: {e}", exc_info=True)
            return f"Error: {str(e)}"

    def _perform_search(self, query: str, max_results: int):
        # Implement search logic
        return []

    def _format_results(self, results):
        return "\n".join(str(r) for r in results)
```

```python
# src/codemie/agents/tools/my_tool/my_toolkit.py
from codemie.agents.tools.base import BaseToolkit

class MyToolkit(BaseToolkit):
    def get_tools(self) -> List[BaseTool]:
        return [MyCustomTool()]

    @classmethod
    def get_tools_ui_info(cls):
        return {"toolkit": "my_toolkit", "tools": [MY_TOOL.name]}
```

**See**: Full example implementations in `src/codemie/agents/tools/code/` directory

---

## Anti-Patterns to Avoid

### ❌ No Schema Validation

```python
# WRONG: Missing args_schema = no input validation
class BadTool(BaseTool):
    name = "bad_tool"

    def _run(self, query):  # No type hints, no schema
        return query.upper()  # Crashes if query is not string
```

### ❌ Silent Errors

```python
# WRONG: Swallowing exceptions without logging
def execute(self, query: str):
    try:
        return self._process(query)
    except Exception:
        return ""  # Agent has no idea what went wrong
```

### ❌ Ignoring Token Limits

```python
# WRONG: Returning unlimited data
def execute(self, query: str):
    results = self.database.query(query)
    return str(results)  # Could return 100MB of data
```

### ❌ Hardcoded Configuration

```python
# WRONG: Hardcoded values instead of configuration
class BadTool(BaseTool):
    API_KEY = "hardcoded-key-123"  # Security risk
    ENDPOINT = "https://prod.example.com"  # Can't test
```

**Why It's Wrong**:
- **No Schema**: Runtime errors, poor error messages, agent confusion
- **Silent Errors**: Impossible to debug, agent retries unnecessarily
- **No Token Limits**: Context overflow, LLM failures, poor performance
- **Hardcoded Config**: Can't test, can't deploy to different environments, security risks

---

## Best Practices Checklist

### Tool Design
- [ ] Clear purpose - tool does one thing well
- [ ] Type hints everywhere (args, returns, class attributes)
- [ ] Schema validation catches bad inputs before execution
- [ ] Descriptive metadata helps LLM select tool correctly
- [ ] Actionable error messages ("Query too short" not "Invalid input")

### Error Handling
- [ ] Graceful degradation - return partial results if possible
- [ ] User-friendly errors explain what went wrong and how to fix it
- [ ] Log everything with `exc_info=True` for debugging
- [ ] Retry logic with exponential backoff for transient failures
- [ ] Token limits enforced to prevent context overflow

### Testing
- [ ] Unit tests for tool execution, error cases, edge cases
- [ ] Integration tests with AgentExecutor
- [ ] Mock external calls - don't hit real APIs in tests
- [ ] Test token limits - verify truncation logic works
- [ ] Test schema validation - ensure bad inputs are caught

### Configuration
- [ ] Inject dependencies via `__init__` or `get_tools()`
- [ ] Use environment variables for environment-specific values
- [ ] Provide sensible defaults, allow overrides
- [ ] Validate configuration at instantiation time

---

## Verification & Troubleshooting

**Verification Steps**:
1. Check registration: `ToolsInfoService.get_tools_info()` includes your tool
2. Test directly: `tool.execute(...)` with sample inputs
3. Test with agent: Use `SmartToolSelector` to verify semantic discovery

**Common Issues**:

| Issue | Solution |
|-------|----------|
| Tool not discoverable | Improve ToolMetadata description with keywords |
| Schema validation fails | Match args_schema with execute() signature |
| Token limit errors | Implement tokens_size_limit and truncation |
| Silent failures | Log with `exc_info=True`, return descriptive errors |

---

## References

**Source Files**:
- Examples: `src/codemie/agents/tools/code/tools.py`, `src/codemie/agents/tools/kb/search_kb.py`
- Base Classes: `codemie_tools/base/codemie_tool.py`, `langchain_core/tools/base.py`
- Toolkits: `src/codemie/agents/tools/base/base_toolkit.py`, `src/codemie/agents/tools/code/code_toolkit.py`

**Related Guides**:
- [Agent Tools Overview](./agent-tools.md) - Tool system architecture and patterns
- [LangChain Agent Patterns](./langchain-agent-patterns.md) - Using tools with agents
- [Testing Patterns](../testing/testing-patterns.md) - Test strategies (ONLY when explicitly requested)

**External Resources**: [LangChain Custom Tools](https://python.langchain.com/docs/modules/agents/tools/custom_tools), [Pydantic Documentation](https://docs.pydantic.dev/)

---
