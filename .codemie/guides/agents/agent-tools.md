# Agent Tools

## Quick Summary

LangChain tool creation patterns in CodeMie using `CodeMieTool` base class with Pydantic schemas, token limiting, error handling, and toolkit organization for code, cloud, KB, and plugin integrations.

**Category**: Agents | **Complexity**: Medium

---

## Tool Base Classes

### CodeMieTool Structure

```python
# codemie_tools/base/codemie_tool.py:17-25
from langchain_core.tools import BaseTool

class CodeMieTool(BaseTool):
    base_name: Optional[str] = None
    name: str  # Required - Display name for LLM
    description: str  # Required - When to use this tool
    args_schema: Type[BaseModel]  # Required - Pydantic schema
    handle_tool_error: bool = True
    tokens_size_limit: int = 30_000
    throw_truncated_error: bool = False
    output_format: ToolOutputFormat = ToolOutputFormat.TEXT

    @abstractmethod
    def execute(self, *args, **kwargs) -> Any:
        """Your tool logic here"""
        pass
```

**Key Fields**:

| Field | Purpose | Default |
|-------|---------|---------|
| `name` | Tool identifier shown to LLM | Required |
| `description` | When/how LLM should use tool (critical!) | Required |
| `args_schema` | Pydantic model for parameters | Required |
| `tokens_size_limit` | Max output tokens | 30,000 |
| `handle_tool_error` | Catch exceptions vs propagate | True |
| `output_format` | TEXT, JSON, HTML, etc. | TEXT |

**Source**: `codemie_tools/base/codemie_tool.py`

### Tool Execution Flow

```python
# codemie_tools/base/codemie_tool.py:48-61
def _run(self, *args, **kwargs):
    try:
        self._validate_config()  # Check required fields
        result = self.execute(*args, **kwargs)  # Your implementation
        output, _ = self._limit_output_content(result)  # Truncate if needed
        return self._post_process_output_content(output, *args, **kwargs)
    except Exception as ex:
        error_message = (f"Error calling tool: {self.name} with: \n"
                         f"Arguments: {kwargs}. Root cause: '{str(ex)}'")
        logger.error(f"{error_message}. Stacktrace: {traceback.format_exc()}")
        raise ToolException(error_message) from ex
```

**Flow**: Validate → Execute → Limit output → Post-process → Return (or raise ToolException)

---

## Tool Schema Patterns

### Pydantic Schema Best Practices

```python
# src/codemie/agents/tools/code/tools_models.py:22-36
from pydantic import BaseModel, Field

class SearchInput(BaseModel):
    query: str = Field(
        description="""Detailed user query for finding and filtering relevant context.
        Must be detailed with context for searching documents."""
    )
    file_path: Optional[List[str]] = Field(
        description="""List of file paths from repository tree which might be relevant
        to user input for additional filtration.""",
        default=[],
    )
    keywords_list: Optional[List[str]] = Field(
        description="""Relevant keywords to enhance search results;
        return empty list if no additional filtering needed.""",
        default=[],
    )
```

**Schema Guidelines**:
- **Rich descriptions**: LLM uses these to understand when/how to use parameters
- **Type hints**: str, int, List[str], Optional[X]
- **Defaults**: Provide defaults for optional parameters
- **Validation**: Pydantic validates at runtime

### Anti-Pattern: Vague Descriptions

```python
# ❌ WRONG: Unclear parameter purpose
class BadSchema(BaseModel):
    query: str = Field(description="The query")  # Too vague!
    path: str = Field(description="A path")  # What kind of path?

# ✅ CORRECT: Clear, context-rich descriptions
class GoodSchema(BaseModel):
    query: str = Field(description="Detailed search query based on user task for finding code files")
    path: str = Field(description="Relative file path in repository (e.g., 'src/api/routes.py')")
```

**Source**: `src/codemie/agents/tools/code/tools_models.py`

---

## Tool Implementation Patterns

### Basic Tool

```python
# src/codemie/agents/tools/code/tools.py:34-49
from codemie_tools.base.codemie_tool import CodeMieTool

class GetRepoFileTreeTool(CodeMieTool, BaseCodeToolMixin):
    base_name: str = REPO_TREE_TOOL.name
    name: str = REPO_TREE_TOOL.name
    description: str = REPO_TREE_TOOL.description
    args_schema: Type[BaseModel] = GetRepoTreeInput
    tokens_size_limit: int = 20000

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.metadata = {TOOL_TYPE: ToolType.PLUGIN}

    def execute(self, query: str):
        return get_repo_tree(code_fields=self.code_fields)
```

**Key Steps**:
1. Extend `CodeMieTool` + optional mixins
2. Set `name`, `description`, `args_schema`
3. Initialize metadata in `__init__`
4. Implement `execute()` method

**Source**: `src/codemie/agents/tools/code/tools.py`

### Tool with LLM Filtering

```python
# src/codemie/agents/tools/code/tools.py:52-96 (consolidated)
class GetRepoFileTreeToolV2(CodeMieTool, CodeRepoBaseToolMixin):
    args_schema: Type[BaseModel] = GetRepoTreeInputV2
    tokens_size_limit: int = 20000
    max_tokens_per_batch: int = 50000

    def execute(self, query: str, file_path: Optional[str] = None):
        # Get repo tree (with optional path filtering)
        repo_tree = self._get_tree(file_path)

        # Filter if output too large
        tokens_count = self.calculate_tokens_count(repo_tree)
        if tokens_count > self.tokens_size_limit:
            logger.info(f"Applying LLM filtering, tokens: {tokens_count}")
            return self.filter_tree_by_relevance(query, repo_tree)

        return repo_tree

    def filter_tree_by_relevance(self, query: str, sources: List[str]):
        try:
            llm_model = self.metadata.get("llm_model")
            llm = get_llm_by_credentials(llm_model=llm_model)
            filter_chain = REPO_TREE_FILTER_RELEVANCE_PROMPT | llm.with_structured_output(FilteredDocuments)

            batches = self._create_batches(sources, self.max_tokens_per_batch)
            final_filtered_documents = []
            for batch in batches:
                filtered = filter_chain.invoke({"sources": str(batch), "query": query})
                final_filtered_documents.extend(filtered.sources)

            return final_filtered_documents
        except Exception as e:
            logger.error(f"Error filtering documents: {str(e)}")
            return sources  # Fallback to unfiltered
```

**Pattern**: Token counting → LLM filtering → batched processing with fallback

---

## Toolkit Organization

### Toolkit Pattern

```python
# src/codemie/agents/tools/base/base_toolkit.py:4-11
from abc import ABC, abstractmethod

class BaseToolkit(ABC):
    @abstractmethod
    def get_tools(self, *args, **kwargs):
        """Return list of tool instances"""
        pass

    @abstractmethod
    def get_tools_ui_info(self, *args, **kwargs):
        """Return UI metadata for frontend"""
        pass
```

**Purpose**: Group related tools, provide UI metadata, factory methods

**Source**: `src/codemie/agents/tools/base/base_toolkit.py`

### CodeToolkit Implementation

```python
# src/codemie/agents/tools/code/code_toolkit.py:43-75 (consolidated)
class CodeToolkit(BaseToolkit):
    sonar_creds: Optional[Dict[str, str]] = None

    @classmethod
    def get_tools_ui_info(cls):
        return CodeToolkitUI().model_dump()

    def get_tools(self, code_fields: CodeFields, llm_model: Any,
                  top_k: int, is_react: bool = True) -> List[BaseTool]:
        tools = [
            CodeToolkit.search_code_tool(code_fields, top_k, is_react),
            CodeToolkit.get_repo_tree_tool(code_fields, is_react),
        ]

        if self.sonar_creds:
            tools.extend(self.get_sonar_tools())

        return tools

    @staticmethod
    def search_code_tool(code_fields, top_k, is_react=True):
        name = CodeToolkit._tool_name(CODE_SEARCH_TOOL, code_fields)
        description = CodeToolkit._tool_description(CODE_SEARCH_TOOL, code_fields, is_react)
        return SearchCodeRepoTool(name=name, description=description, code_fields=code_fields, top_k=top_k)
```

**Pattern**: Factory methods create tool instances with context-specific configuration

**Source**: `src/codemie/agents/tools/code/code_toolkit.py`

---

## Tool Metadata and Calling

### Metadata Injection

```python
# src/codemie/agents/assistant_agent.py:255-278
for tool in self.tools:
    tool.metadata = {
        REQUEST_ID: self.request_uuid,
        USER_ID: self.user.id,
        USER_NAME: self.user.name,
        LLM_MODEL: self.llm_model,
        AGENT_NAME: self.agent_name,
    }
```

**Usage in execute()**:
```python
def execute(self, query: str):
    request_id = self.metadata.get(REQUEST_ID, "")
    llm_model = self.metadata.get(LLM_MODEL, "default-model")
    # Use metadata for logging, LLM calls, monitoring
```

**Source**: `src/codemie/agents/assistant_agent.py`

### Tool Invocation Flow

```python
# LangChain AgentExecutor handles tool calling automatically
tools = [SearchCodeRepoTool(...), GetRepoFileTreeTool(...)]
agent = create_tool_calling_agent(llm=llm, tools=tools, prompt=prompt)
agent_executor = AgentExecutor(agent=agent, tools=tools)

# When agent needs code search:
# 1. LLM generates: {"tool": "search_code_repo", "tool_input": {"query": "auth logic"}}
# 2. AgentExecutor calls: search_code_tool.execute(query="auth logic")
# 3. Tool returns results
# 4. Results passed back to LLM for next step
```

---

## Error Handling

### ToolException Pattern

```python
# codemie_tools/base/codemie_tool.py:48-61
def _run(self, *args, **kwargs):
    try:
        result = self.execute(*args, **kwargs)
        return result
    except Exception as ex:
        error_message = (f"Error calling tool: {self.name} with: \n"
                         f"Arguments: {kwargs}. Root cause: '{str(ex)}'")
        logger.error(f"{error_message}. Stacktrace: {traceback.format_exc()}")
        raise ToolException(error_message) from ex
```

**Error Flow**: Exception in execute() → logged → wrapped in ToolException → agent sees error

### Error Configuration

```python
# Tool-level
class MyTool(CodeMieTool):
    throw_truncated_error: bool = False  # Set True to fail on truncation

# Agent-level (applies to all tools)
for tool in self.tools:
    tool.handle_tool_error = self.handle_tool_error
```

**Settings**:
- `throw_truncated_error=False`: Truncate silently (default)
- `throw_truncated_error=True`: Raise TruncatedOutputError
- `handle_tool_error=True`: Tool errors logged, agent continues
- `handle_tool_error=False`: Tool errors crash agent

### Healthcheck Pattern

```python
# codemie_tools/base/codemie_tool.py:67-76
def healthcheck(self):
    try:
        self._healthcheck()  # Tool-specific validation
    except Exception as e:
        return False, humanize_error(e)
    return True, ""
```

**Usage**: Verify tool configuration/credentials before agent run

**Source**: `codemie_tools/base/codemie_tool.py`

---

## Common Issues and Solutions

| Issue | Symptom | Solution |
|-------|---------|----------|
| Tool not called | Agent doesn't use tool | Improve `description` clarity and detail |
| Wrong parameters | LLM passes incorrect args | Improve `Field(description=...)` with examples |
| Truncated output | Tool returns "..." | Increase `tokens_size_limit` or add LLM filtering |
| Tool error | "ToolException" in output | Check `execute()` logic, validate inputs |
| Missing metadata | Tool can't access context | Ensure metadata set by agent configuration |

**Debugging**: Test tools directly by calling `tool.execute()` with parameters. Check `tool.description` and `tool.args_schema.schema()` for clarity.

---

## Anti-Patterns to Avoid

### ❌ Side Effects Not Described

```python
# WRONG: Tool modifies state but description doesn't mention it
class BadTool(CodeMieTool):
    description: str = "Get user information"  # Says "get" but updates!

    def execute(self, user_id: int):
        user = get_user(user_id)
        user.last_accessed = datetime.now()
        user.save()  # Side effect not described!
        return user

# CORRECT: Describe ALL side effects
class GoodTool(CodeMieTool):
    description: str = "Get user information and update last_accessed timestamp"
```

### ❌ Database Logic in Tool Execute Method

**CRITICAL ANTI-PATTERN**: Never put database queries directly in tool execution methods!

```python
# WRONG: Database logic directly in tool (violates layered architecture)
class BadAnalyticsTool(CodeMieTool):
    def execute(self, user_name: str, project: str):
        from sqlmodel import Session, select
        from codemie.clients.postgres import PostgresClient

        # ❌ DB queries in tool layer - WRONG!
        with Session(PostgresClient.get_engine()) as session:
            stmt = select(ConversationAnalytics).where(
                ConversationAnalytics.user_name == user_name,
                ConversationAnalytics.project == project
            )
            results = session.exec(stmt).all()

        return format_results(results)

# CORRECT: Tool calls service, service handles DB
class GoodAnalyticsTool(CodeMieTool):
    def execute(self, user_name: str, project: str):
        from codemie.service.conversation_service import ConversationService

        # ✅ Call service method - follows layered architecture
        results = ConversationService.get_analytics(
            user_name=user_name,
            project=project
        )

        return format_results(results)
```

**Why this matters**:
- **Layered Architecture**: Tools (API layer) → Services (business logic) → Repositories (data access)
- **Separation of Concerns**: Tools handle input/output formatting, services handle business logic
- **Testability**: Service methods can be tested independently without mocking DB
- **Reusability**: Service methods can be called from tools, API endpoints, background jobs
- **Maintainability**: DB query logic in one place, not scattered across tools

**Correct Pattern**:
1. Tool validates user permissions and parses inputs
2. Tool calls service method with business parameters
3. Service handles DB queries, joins, aggregations, business logic
4. Tool transforms service results into output format

**Example**: See `src/codemie/agents/tools/platform/platform_tool.py:742-749` (GetConversationAnalyticsTool calling ConversationService)

---

## References

**Source Files**:
- Tool Implementation: `src/codemie/agents/tools/code/tools.py`
- Base Classes: `codemie_tools/base/codemie_tool.py`
- Toolkits: `src/codemie/agents/tools/base/base_toolkit.py`, `src/codemie/agents/tools/code/code_toolkit.py`
- Tool Models: `src/codemie/agents/tools/code/tools_models.py`
- Agent Integration: `src/codemie/agents/assistant_agent.py:255-278`

**Related Guides**:
- [Custom Tool Creation](./custom-tool-creation.md) - Step-by-step guide for building custom tools from scratch
- [LangChain Agent Patterns](./langchain-agent-patterns.md) - Agent setup and tool integration
- [Testing Patterns](../testing/testing-patterns.md) - Tool testing strategies (ONLY when explicitly requested)

**External Resources**: [LangChain Tools Documentation](https://python.langchain.com/docs/modules/tools/), [Pydantic Documentation](https://docs.pydantic.dev/)

---