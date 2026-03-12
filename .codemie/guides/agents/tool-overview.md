# Tool System Overview

## Quick Summary

CodeMie's tool system provides agents with capabilities to interact with code repositories, cloud platforms, knowledge bases, and external services. Tools are discovered dynamically using semantic search and invoked through LangChain's BaseTool interface. Toolkits organize related tools and handle instantiation with context-specific configurations.

**Category**: Agent
**Complexity**: Medium
**Prerequisites**: LangChain agents, Pydantic models, async/await patterns

---

## Implementation

### Tool Categories

| Category | Tools | Use Cases |
|----------|-------|-----------|
| **code** | SearchCodeRepoTool, GetRepoFileTreeTool, ReadFileFromStorageTool | Code search, repo navigation, file reading |
| **cloud-aws** | S3Tool, BedrockTool, KMSTool | AWS operations (storage, LLM, encryption) |
| **cloud-azure** | BlobTool, AzureOpenAITool, KeyVaultTool | Azure operations (storage, LLM, secrets) |
| **cloud-gcp** | GCSToolGCP Tool, VertexAITool, KMSTool | GCP operations (storage, LLM, encryption) |
| **git** | GitCloneTool, GitDiffTool, GitLogTool | Git operations and repository management |
| **kb** | SearchKBTool | Knowledge base semantic search |
| **plugin** | PluginToolkit (NATS-based) | External system integrations via NATS |
| **ide** | IDE operations | Editor-specific functionality |

### Tool Discovery Pattern

Agents discover tools dynamically using SmartToolSelector and ToolkitLookupService:

```python
# src/codemie/agents/smart_tool_selector.py:19-33
class SmartToolSelector:
    """Selects relevant tools using semantic search via ToolkitLookupService.

    Leverages hybrid search with reciprocal rank fusion (RRF) and reranking.
    """

    def __init__(
        self,
        tool_registry: dict[str, BaseTool],
        default_limit: int = 3,
    ):
        self.tool_registry = tool_registry
        self.default_limit = default_limit
        self.name_to_id = {tool.name: tool_id for tool_id, tool in tool_registry.items()}
```

```python
# src/codemie/agents/smart_tool_selector.py:55-86
def select_tools(
    self,
    query: str,
    limit: Optional[int] = None,
    history: Optional[list] = None,
) -> tuple[list[str], list[BaseTool]]:
    """Select relevant tools using semantic search.

    Uses ToolkitLookupService for Elasticsearch-based hybrid search
    with reranking to find most relevant tools.
    """
    limit = limit or self.default_limit
    search_query = self._build_search_query(query, history)
    available_tool_names = list(self.name_to_id.keys())

    # Elasticsearch semantic search via ToolkitLookupService
    toolkits = ToolkitLookupService.search_tools(
        query=search_query,
        tool_names=available_tool_names,
        limit=limit
    )
```

### Tool Categories with Examples

#### 1. Code Tools

**SearchCodeRepoTool** - Semantic code search across repository:

```python
# src/codemie/agents/tools/code/tools.py:34-49
class GetRepoFileTreeTool(CodeMieTool, BaseCodeToolMixin):
    base_name: str = REPO_TREE_TOOL.name
    name: str = REPO_TREE_TOOL.name
    description: str = REPO_TREE_TOOL.description
    args_schema: Optional[Type[BaseModel]] = GetRepoTreeInput
    tokens_size_limit: int = 20000

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        if self.metadata:
            self.metadata[TOOL_TYPE] = ToolType.PLUGIN

    def execute(self, *args, **kwargs):
        return get_repo_tree(code_fields=self.code_fields)
```

**CodeToolkit** - Manages code tool instantiation:

```python
# src/codemie/agents/tools/code/code_toolkit.py:59-76
def get_tools(
    self,
    code_fields: CodeFields,
    llm_model: Any,
    history: List[ChatMessage],
    top_k: int,
    is_react: bool = True,
    thread_generator: ThreadedGenerator = None,
) -> List[BaseTool]:
    tools = [
        CodeToolkit.search_code_tool(code_fields=code_fields, top_k=top_k, is_react=is_react),
        CodeToolkit.get_repo_tree_tool(code_fields=code_fields, is_react=is_react),
    ]

    if self.sonar_creds:
        tools.extend(self.get_sonar_tools())
    return tools
```

#### 2. Cloud-AWS Tools

AWS tools from src/codemie_tools/cloud/aws/:

```python
# Referenced in src/codemie/service/provider/provider_toolkits_factory.py
from codemie_tools.cloud.aws import (
    S3Toolkit,      # S3 bucket operations
    BedrockToolkit, # AWS Bedrock LLM access
    KMSToolkit      # KMS encryption/decryption
)

# Tools provide AWS service integration with credential management
```

#### 3. Cloud-Azure Tools

Azure tools from src/codemie_tools/cloud/azure/:

```python
# Referenced in src/codemie/service/provider/provider_toolkits_factory.py
from codemie_tools.cloud.azure import (
    BlobToolkit,       # Azure Blob Storage operations
    AzureOpenAIToolkit, # Azure OpenAI service access
    KeyVaultToolkit    # Azure Key Vault secrets
)
```

#### 4. Cloud-GCP Tools

GCP tools from src/codemie_tools/cloud/gcp/:

```python
# Referenced in src/codemie/service/provider/provider_toolkits_factory.py
from codemie_tools.cloud.gcp import (
    GCSToolkit,      # Google Cloud Storage operations
    VertexAIToolkit, # Vertex AI LLM access
    KMSToolkit       # Cloud KMS encryption
)
```

#### 5. Git Tools

Git tools from src/codemie_tools/core/vcs/ and src/codemie_tools/git/:

```python
# Referenced in src/codemie/service/git_api/git_api_service.py
from codemie_tools.core.vcs.gitlab.toolkit import GitLabToolkit

# Provides: GitCloneTool, GitDiffTool, GitLogTool, GitStatusTool
# Handles repository operations and version control
```

#### 6. Knowledge Base Tools

**SearchKBTool** - Semantic search across indexed knowledge bases:

```python
# src/codemie/agents/tools/kb/search_kb.py:42-60
class SearchKBTool(CodeMieTool):
    kb_index: Optional[IndexInfo] = None
    llm_model: Optional[str] = None
    base_name: str = "search_kb"
    name_template: str = base_name + "_{}"
    tokens_size_limit: int = Field(default_factory=lambda: 20000)
    description_template: str = """
    Use this tool when you need to get or search additional project context.
    Tool get the following input parameters: "query": string text with detailed
    user query which will be used to find relevant context.
    Tool knowledge description: {}.
    """
```

**KBToolkit** instantiation:

```python
# src/codemie/agents/tools/kb/kb_toolkit.py:18-31
class KBToolkit(BaseToolkit):
    @classmethod
    def get_tools(cls, kb_index: IndexInfo, llm_model: str) -> List[BaseTool]:
        search_tool = SearchKBTool(
            kb_index=kb_index,
            llm_model=llm_model,
        )
        return [search_tool]
```

#### 7. Plugin Tools

**PluginToolkit** - NATS-based external integrations:

```python
# src/codemie/agents/tools/plugin/plugin_toolkit.py:32-77
class PluginToolkit(BaseModel, BaseToolkit):
    """NATS-based plugin system for external tool integrations."""

    plugin_creds: Dict[str, str] = Field(default={})

    def get_tools(self) -> List[BaseTool]:
        tools: List[BaseTool] = []
        client = Client()

        if not self.plugin_creds:
            raise ValueError("Missing plugin credentials.")

        plugin_key = self.plugin_creds.get("plugin_key")
        tool_defs = self._run_async_task(plugin_manager.get_plugin_config(plugin_key))

        for tool in tool_defs:
            tools.append(ToolConsumer(client=client, tool=tool))

        return tools
```

#### 8. IDE Tools

IDE-specific operations:

```python
# src/codemie/agents/tools/ide/ide_toolkit.py
# Provides editor-specific functionality integration
```

### Agent-Tool Integration

Agents receive tools and use AgentExecutor for invocation:

```python
# src/codemie/agents/assistant_agent.py:108-156
class AIToolsAgent:
    def __init__(
        self,
        agent_name: str,
        tools: list[BaseTool],  # Tools passed here
        request: AssistantChatRequest,
        system_prompt: str,
        llm_model: str,
        ...
    ):
        self.tools = tools
        self.agent_executor = self.init_agent()
```

```python
# src/codemie/agents/assistant_agent.py:195-223
def init_agent(self):
    llm = self._initialize_llm()
    callbacks = self.configure_callbacks(llm)

    if not self.tools:
        return self._create_fallback_agent(llm)

    if self.llm_model in llm_service.get_react_llms():
        agent = self._create_react_agent(llm)
    else:
        agent = self._create_tool_calling_agent(llm)

    self._configure_tools(callbacks)

    return AgentExecutor(
        agent=agent,
        tools=self.tools,  # Tools bound to executor
        verbose=self.verbose,
        max_iterations=self.recursion_limit,
        callbacks=callbacks,
    )
```

### Tool Metadata Pattern

Tools use ToolMetadata for registration and discovery:

```python
# src/codemie/agents/tools/code/tools_vars.py:3-26
CODE_SEARCH_TOOL = ToolMetadata(
    name="search_code_repo",
    description="""
    Tool to search code context for repository in generic approach.
    Repository description: {}.
    You must use this tool anytime, because you need context from repository.
    REQUIRED parameters:
    - 'query': raw user input text query;
    - 'file_path': list of relevant file paths for filtration.
    OPTIONAL parameters:
    - 'keywords_list': keywords to filter results.
    """,
    react_description="""[Optimized description for ReAct agents]"""
)
```

### Base Classes

All tools inherit from these interfaces:

```python
# src/codemie/agents/tools/base/base_toolkit.py:4-11
class BaseToolkit(ABC):
    @abstractmethod
    def get_tools(self, *args, **kwargs):
        pass

    @abstractmethod
    def get_tools_ui_info(self, *args, **kwargs):
        pass
```

```python
# LangChain core interface (external)
from langchain_core.tools import BaseTool

# All tools extend BaseTool with:
# - name: str
# - description: str
# - args_schema: Type[BaseModel]
# - _run() or execute() method
```

---

## Anti-Pattern

### What NOT to Do

**❌ Don't create tools without ToolMetadata:**

```python
# Missing metadata registration
class MyCustomTool(BaseTool):
    name = "my_tool"
    description = "Does something"

    def _run(self, query: str):
        return "result"

# Problem: No ToolMetadata = not discoverable via semantic search
```

**❌ Don't bypass toolkit pattern:**

```python
# Directly instantiating tools without toolkit
tool1 = SearchCodeRepoTool(code_fields=fields, top_k=5)
tool2 = GetRepoFileTreeTool(code_fields=fields)

# Problem: Loses configuration management, UI integration, consistency
```

**❌ Don't mix sync/async incorrectly:**

```python
# Blocking async operations in tool execution
class BadTool(BaseTool):
    def _run(self, query: str):
        # ❌ Blocking async call
        result = asyncio.run(self.async_operation())
        return result
```

### Why It's Wrong

- **Missing ToolMetadata**: Tools won't appear in semantic search, breaking SmartToolSelector
- **Bypassing Toolkits**: Loses centralized configuration, UI representation, and maintenance
- **Async Misuse**: Causes event loop conflicts, especially in NATS-based plugin tools
- **No Schema Validation**: Missing args_schema leads to runtime errors and poor error messages

---

## Best Practices

### Tool Design

- **Single Responsibility**: Each tool does one thing well (search code, read file, etc.)
- **Clear Descriptions**: Write descriptions for both humans and LLMs (use react_description)
- **Schema Validation**: Use Pydantic args_schema for input validation
- **Error Handling**: Return meaningful errors vs throwing exceptions
- **Token Limits**: Implement tokens_size_limit to avoid context overflow

### Toolkit Organization

- **Group Related Tools**: CodeToolkit contains all code-related tools
- **Configuration Injection**: Pass context (code_fields, credentials) via get_tools()
- **UI Integration**: Implement get_tools_ui_info() for frontend representation
- **Lazy Instantiation**: Create tools only when needed (e.g., Sonar tools only if configured)

### Agent Integration

- **Use SmartToolSelector**: Let semantic search select relevant tools dynamically
- **Pass to AgentExecutor**: Always use AgentExecutor to manage tool invocation
- **Handle Callbacks**: Configure callbacks for monitoring and streaming
- **Set Iteration Limits**: Prevent infinite loops with max_iterations

---

## Verification

### Check Tool Registration

```bash
# Verify tool appears in Elasticsearch index
curl -X GET "localhost:9200/codemie_tools/_search?q=name:search_code_repo"
```

### Test Tool Discovery

```python
# Verify SmartToolSelector finds tool
selector = SmartToolSelector(tool_registry, default_limit=3)
tool_ids, tools = selector.select_tools("search code files", limit=3)
assert "search_code_repo" in [t.name for t in tools]
```

### Validate Tool Execution

```python
# Test tool directly
tool = CodeToolkit.search_code_tool(code_fields, top_k=5)
result = tool._run(query="authentication logic", file_path=["src/auth/"])
assert len(result) > 0
```

---

## Troubleshooting

### Tool Not Found by Agent

**Symptom**: Agent says "I don't have a tool for that"

**Causes**:
- Tool not registered in ToolkitLookupService
- Tool name/description doesn't match semantic query
- Tool not included in agent's tool_registry

**Fix**:
1. Verify tool indexed: Check Elasticsearch index
2. Improve tool description: Add relevant keywords
3. Check tool_registry: Ensure tool passed to agent init

### Tool Execution Fails

**Symptom**: Tool returns error or empty result

**Causes**:
- Missing credentials (cloud tools)
- Invalid input schema
- Timeout or connection issues

**Fix**:
1. Check credentials configuration
2. Validate input against args_schema
3. Review tool logs for specific errors
4. Increase timeout if needed

### Semantic Search Returns Wrong Tools

**Symptom**: SmartToolSelector picks irrelevant tools

**Causes**:
- Tool descriptions too generic
- Query doesn't match tool semantics
- RRF scoring needs tuning

**Fix**:
1. Enhance tool descriptions with specific keywords
2. Add react_description for ReAct agents
3. Adjust ToolkitLookupService search parameters

---

## Next Steps

- **Create Custom Tools**: See [custom-tool-creation.md](custom-tool-creation.md) for step-by-step guide
- **Agent Integration**: See [langchain-agent-patterns.md](langchain-agent-patterns.md) for agent setup
- **Testing Patterns**: See testing documentation for tool testing strategies

---

## References

- **Source (agent layer)**: `src/codemie/agents/tools/` — code, kb, ide, platform, plugin, skill toolkits
- **Source (tool library)**: `src/codemie_tools/` — all domain tools (cloud, git/vcs, qa, data, file analysis, etc.)
- **Related Patterns**: [langchain-agent-patterns.md](langchain-agent-patterns.md), [service-layer-patterns.md](../architecture/service-layer-patterns.md)
- **LangChain Docs**: https://python.langchain.com/docs/modules/agents/tools/

---
