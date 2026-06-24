# DSP X-Header and Context Metadata Propagation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Propagate filtered `X-*` headers from the originating HTTP request and assistant context metadata (conversation ID, message ID, assistant ID, LLM model) to Data Source Provider (DSP) tool calls, for consistency with MCP tool propagation.

**Architecture:** Two categories of data are forwarded to the DSP `invoke_tool()` HTTP call via the `_headers` parameter: (1) filtered incoming `X-*` headers (only when `propagate_headers=True`, already handled by `extract_custom_headers`) and (2) assistant execution context headers (`X-Conversation-Id`, `X-Message-Id`, `X-Assistant-Id`, `X-LLM-Model`) always forwarded when propagation is enabled. The propagation chain threads new fields through `ToolkitService` → provider toolkit lambdas → dynamically-built tool class instances → `ProviderToolFactory._generate_execute()` → `invoke_tool(_headers=...)`.

**Tech Stack:** Python 3.12, FastAPI, LangChain `BaseTool`, Pydantic `create_model`, `urllib3` OpenAPI client

---

## Background: Two Types of DSP Tools

There are two independent provider tool creation paths — both need `request_headers` and context threaded through:

1. **Toolkit tools** (configured toolkits, non-datasource): `get_tools()` → `_get_tools()` → `add_tools_with_creds()` → `get_provider_toolkits_methods()` lambda → tool constructor
2. **Datasource context tools** (index-based, datasource actions): `get_tools()` → `add_context_tools()` → `_add_provider_context_tools()` → tool constructor

---

## File Map

| File | Change |
|------|--------|
| `src/codemie/service/provider/provider_tool_factory.py` | Add `request_headers`, `conversation_id`, `assistant_id`, `llm_model` fields to dynamically built tool class; build and pass `_headers` in `execute()` |
| `src/codemie/service/tools/toolkit_service.py` | Fix `get_provider_toolkits_methods()` lambda signature bug; add context params to `get_provider_toolkits_methods()`, `get_toolkit_methods()`, `add_tools_with_creds()`, `add_context_tools()`, `_add_provider_context_tools()` |
| `tests/codemie/service/provider/test_provider_tool_factory.py` | Add tests verifying `_headers` passed to `invoke_tool()` with X-* headers and context headers |
| `tests/codemie/service/tools/test_toolkit_service.py` | Update provider toolkit tests to assert context params thread through |

---

## Task 1: Add Header Fields to `ProviderToolFactory`

This is the injection point. The generated tool class gains new optional instance fields; `execute()` builds the `_headers` dict from them.

**Files:**
- Modify: `src/codemie/service/provider/provider_tool_factory.py`
- Test: `tests/codemie/service/provider/test_provider_tool_factory.py`

- [ ] **Step 1: Write failing test — X-* header propagation in `execute()`**

Add this test to `tests/codemie/service/provider/test_provider_tool_factory.py`:

```python
@patch("codemie.clients.provider.client.Configuration", return_value=MagicMock())
@patch("codemie.clients.provider.client.ApiClient", return_value=MagicMock())
@patch("codemie.clients.provider.client.ToolInvocationManagementApi.invoke_tool")
@patch("codemie.service.provider.datasource.ProviderDatasourceSchemaService.schema_for")
@patch("codemie.service.provider.util.decrypt_datasource_provider_fields", return_value={})
def test_execute_propagates_request_headers(
    _mock_decrypt, _mock_schema, mock_invoke_tool, mock_api_client, mock_api_config, tool_factory
):
    mock_result = MagicMock()
    mock_result.result = "ok"
    mock_invoke_tool.return_value = mock_result

    tool_class = tool_factory.build()
    instance = tool_class(
        user=User(id="u1", auth_token="tok"),
        project_id="proj1",
        request_uuid="req-uuid-1",
        request_headers={"X-Tenant-ID": "tenant-abc", "X-Correlation-ID": "corr-xyz"},
        conversation_id="conv-111",
        assistant_id="asst-222",
        llm_model="gpt-4o",
    )

    instance.execute(param1="val")

    _call_kwargs = mock_invoke_tool.call_args
    headers_passed = _call_kwargs.kwargs.get("_headers") or _call_kwargs[1].get("_headers")
    assert headers_passed is not None
    assert headers_passed["X-Tenant-ID"] == "tenant-abc"
    assert headers_passed["X-Correlation-ID"] == "corr-xyz"
    assert headers_passed["X-Conversation-Id"] == "conv-111"
    assert headers_passed["X-Message-Id"] == "req-uuid-1"
    assert headers_passed["X-Assistant-Id"] == "asst-222"
    assert headers_passed["X-LLM-Model"] == "gpt-4o"


@patch("codemie.clients.provider.client.Configuration", return_value=MagicMock())
@patch("codemie.clients.provider.client.ApiClient", return_value=MagicMock())
@patch("codemie.clients.provider.client.ToolInvocationManagementApi.invoke_tool")
@patch("codemie.service.provider.datasource.ProviderDatasourceSchemaService.schema_for")
@patch("codemie.service.provider.util.decrypt_datasource_provider_fields", return_value={})
def test_execute_no_headers_when_request_headers_none(
    _mock_decrypt, _mock_schema, mock_invoke_tool, mock_api_client, mock_api_config, tool_factory, tool_params
):
    mock_result = MagicMock()
    mock_result.result = "ok"
    mock_invoke_tool.return_value = mock_result

    tool_class = tool_factory.build()
    instance = tool_class(**tool_params)  # tool_params has no request_headers

    instance.execute(param1="val")

    _call_kwargs = mock_invoke_tool.call_args
    headers_passed = _call_kwargs.kwargs.get("_headers")
    assert headers_passed is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source .venv/bin/activate && poetry run pytest tests/codemie/service/provider/test_provider_tool_factory.py::test_execute_propagates_request_headers tests/codemie/service/provider/test_provider_tool_factory.py::test_execute_no_headers_when_request_headers_none -v
```

Expected: FAIL — `invoke_tool` is called without `_headers` kwarg, and `test_execute_propagates_request_headers` fails on `headers_passed is None`.

- [ ] **Step 3: Update `build()` to declare new optional fields**

In `src/codemie/service/provider/provider_tool_factory.py`, update the `build()` method's `__annotations__` dict and set default `None` for the new fields:

```python
def build(self, datasource: Optional[ProviderIndexInfo] = None):
    """Dynamically build a tool class based on provider configuration."""
    klass_name = to_class_name(self.tool_config.name) + self.CLASSNAME_POSTFIX

    klass = type(
        klass_name,
        (ProviderToolBase,),
        {
            "__module__": __name__,
            "__annotations__": {
                "name": str,
                "base_name": str,
                "description": str,
                "args_schema": Type[BaseModel],
                "user": User,
                "project_id": str,
                "request_uuid": str,
                "request_headers": Optional[dict],
                "conversation_id": Optional[str],
                "assistant_id": Optional[str],
                "llm_model": Optional[str],
            },
            "name": self._tool_name,
            "base_name": self.tool_config.name,
            "description": self.tool_config.description,
            "args_schema": self._generate_args_schema(),
            "request_headers": None,
            "conversation_id": None,
            "assistant_id": None,
            "llm_model": None,
        },
    )
    klass.name = self._tool_name
    klass.base_name = self.tool_config.name
    klass.description = self.tool_config.description
    klass.args_schema = self._generate_args_schema()
    klass.execute = self._generate_execute()
    klass.datasource = datasource or None

    return klass
```

You will also need to add `Optional` to the import at the top (it's already imported from `typing`).

- [ ] **Step 4: Update `_generate_execute()` to build and pass `_headers`**

Replace the current `execute` inner function in `_generate_execute()`:

```python
def _generate_execute(self):
    """Generate tool execute method"""
    context = self

    def execute(self, *_args, **kwargs):
        log_prefix = f"Execute provider tool '{context.tool_config.name}' [{self.request_uuid}]:"
        host = context.provider_config.service_location_url

        api_client: provider_client.ToolInvocationManagementApi = ProviderAPIClient(
            user=self.user,
            url=host,
            provider_security_config=context.provider_config.configuration,
            log_prefix=log_prefix,
        ).build()

        if context.datasource:
            schema = ProviderDatasourceSchemaService(
                provider=context.provider_config,
            ).schema_for(
                toolkit_id=context.toolkit_config.toolkit_id,
            )
            configuration_params = decrypt_datasource_provider_fields(
                params=context.datasource.provider_fields.base_params, schema=schema.base_schema
            )
        else:
            configuration_params = {}

        payload = {
            "user_id": self.user.id,
            "project_id": self.project_id,
            "configuration": {"configuration_type": context.CONFIGURATION_TYPE, "parameters": configuration_params},
            "parameters": kwargs,
            "async": False,
        }

        # Build propagated headers when request_headers is set (propagate_headers was True)
        invoke_headers = None
        if self.request_headers is not None:
            invoke_headers = dict(self.request_headers)
            if self.conversation_id:
                invoke_headers["X-Conversation-Id"] = self.conversation_id
            invoke_headers["X-Message-Id"] = self.request_uuid
            if self.assistant_id:
                invoke_headers["X-Assistant-Id"] = self.assistant_id
            if self.llm_model:
                invoke_headers["X-LLM-Model"] = self.llm_model

        try:
            logger.info(f"{log_prefix} Invoking tool")
            response = api_client.invoke_tool(
                toolkit_name=context.toolkit_config.name,
                tool_name=context.tool_config.name,
                x_correlation_id=self.request_uuid,
                tool_invocation_request=payload,
                _headers=invoke_headers,
            )
            logger.info(f"{log_prefix} Invoked tool successfully")
            return response.result
        except MaxRetryError:
            msg = context.CONNECTION_ERROR_MSG.format(host=host)
            logger.warning(f"{log_prefix} {msg}")
            raise ProviderConnectionError(msg)
        except Exception as e:
            logger.error(f"{log_prefix} Failed to invoke tool: {str(e)}")
            raise e

    return execute
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
source .venv/bin/activate && poetry run pytest tests/codemie/service/provider/test_provider_tool_factory.py -v
```

Expected: ALL PASS — including the new propagation tests and the unchanged existing tests.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/provider/provider_tool_factory.py tests/codemie/service/provider/test_provider_tool_factory.py
git commit -m "feat(EPMCDME-12070): add request_headers and context fields to ProviderToolFactory

When request_headers is not None (propagate_headers=True), invoke_tool receives
_headers containing the filtered X-* request headers plus context metadata:
X-Conversation-Id, X-Message-Id, X-Assistant-Id, X-LLM-Model."
```

---

## Task 2: Thread Context Params Through `ToolkitService` (Toolkit Tools Path)

This fixes the signature mismatch bug in `get_provider_toolkits_methods` and threads `request_headers` + context metadata from `_get_tools()` → `add_tools_with_creds()` → provider toolkit lambdas → tool constructors.

**Files:**
- Modify: `src/codemie/service/tools/toolkit_service.py`
- Test: `tests/codemie/service/tools/test_toolkit_service.py`

- [ ] **Step 1: Write failing test — `add_tools_with_creds` threads request_headers**

In `tests/codemie/service/tools/test_toolkit_service.py`, locate the `test_add_tools_with_creds` test and add a new test alongside it:

```python
@patch("codemie.service.tools.toolkit_service.ProviderToolkitsFactory")
def test_add_tools_with_creds_propagates_request_headers(mock_provider_factory, mock_assistant, mock_user, mock_request):
    """request_headers and context params must reach provider tool constructors."""
    from unittest.mock import call as mock_call

    # Set up a provider toolkit
    mock_provider_toolkit = Mock()
    mock_provider_toolkit.get_tools_ui_info.return_value = {"toolkit": "my_provider_toolkit"}
    mock_provider_factory.get_toolkits.return_value = [mock_provider_toolkit]

    # get_toolkit().get_tools() returns a single mock tool class
    mock_tool_class = Mock()
    mock_provider_toolkit.get_toolkit.return_value.get_tools.return_value = [mock_tool_class]

    # Configure the assistant to use the provider toolkit
    from codemie.rest_api.models.assistant import ToolKitDetails, Tool
    mock_assistant.toolkits = [
        Mock(toolkit="my_provider_toolkit", tools=[Mock(name="some_tool")])
    ]
    mock_request.conversation_id = "conv-abc"
    mock_request.tools_config = None

    request_headers = {"X-Tenant-ID": "tenant-1"}

    ToolkitService.add_tools_with_creds(
        assistant=mock_assistant,
        user=mock_user,
        llm_model="gpt-4o",
        request_uuid="req-uuid",
        request=mock_request,
        request_headers=request_headers,
    )

    mock_tool_class.assert_called_once()
    call_kwargs = mock_tool_class.call_args.kwargs
    assert call_kwargs.get("request_headers") == request_headers
    assert call_kwargs.get("conversation_id") == "conv-abc"
    assert call_kwargs.get("assistant_id") == mock_assistant.id
    assert call_kwargs.get("llm_model") == "gpt-4o"
```

- [ ] **Step 2: Run the failing test**

```bash
source .venv/bin/activate && poetry run pytest tests/codemie/service/tools/test_toolkit_service.py::TestToolkitService::test_add_tools_with_creds_propagates_request_headers -v 2>&1 | tail -20
```

Expected: FAIL — `add_tools_with_creds` does not yet accept `request_headers`.

- [ ] **Step 3: Fix `get_provider_toolkits_methods` signature and add context params**

Replace the current `get_provider_toolkits_methods` classmethod in `toolkit_service.py`:

```python
@classmethod
def get_provider_toolkits_methods(
    cls,
    request_headers: dict[str, str] | None = None,
    conversation_id: str | None = None,
    assistant_id: str | None = None,
    llm_model: str | None = None,
):
    """Get provider toolkit methods.

    Returns:
        Dictionary mapping provider toolkit names to their factory methods
    """
    provider_toolkits = ProviderToolkitsFactory.get_toolkits()
    toolkit_methods = {}

    for toolkit in provider_toolkits:
        toolkit_name = toolkit.get_tools_ui_info()['toolkit']

        toolkit_methods[toolkit_name] = lambda assistant, user, _llm_model, request_uuid, request, _toolkit=toolkit: [
            tool(
                project_id=assistant.project,
                user=user,
                request_uuid=request_uuid,
                request_headers=request_headers,
                conversation_id=request.conversation_id if request else conversation_id,
                assistant_id=assistant.id,
                llm_model=_llm_model,
            )
            for tool in _toolkit.get_toolkit().get_tools()
        ]
    return toolkit_methods
```

Note: the lambda signature is fixed to include `_llm_model` (matching the 5-arg call from `add_tools_with_creds`). Context params (`request_headers`, `conversation_id`, `assistant_id`, `llm_model`) are captured from the outer scope at lambda creation time. `conversation_id` and `assistant_id` are also derived directly from `request` and `assistant` in the lambda body for the most accurate per-request values.

- [ ] **Step 4: Update `get_toolkit_methods` to pass context params**

Update the `get_toolkit_methods` classmethod signature and its call to `get_provider_toolkits_methods`:

```python
@classmethod
def get_toolkit_methods(
    cls,
    request_headers: dict[str, str] | None = None,
    conversation_id: str | None = None,
    assistant_id: str | None = None,
    llm_model: str | None = None,
):
    """Get mapping of toolkit types to their factory methods."""
    return {
        ToolSet.PLUGIN: lambda assistant, user, llm_model, request_uuid, request: cls._get_plugin_tools_delegate(
            assistant, user, request
        ),
        ToolSet.RESEARCH: lambda assistant, user, llm_model, request_uuid, request: ResearchToolkit.get_toolkit(
            configs=ResearchConfig(
                google_search_api_key=config.GOOGLE_SEARCH_API_KEY,
                google_search_cde_id=config.GOOGLE_SEARCH_CSE_ID,
                tavily_search_key=config.TAVILY_API_KEY,
            ).model_dump()
        ).get_tools(),
        ToolSet.PLATFORM_TOOLS: lambda assistant, user, llm_model, request_uuid, request: PlatformToolkit(
            user=user
        ).get_tools(),
        ToolSet.FILE_SYSTEM: lambda assistant, user, llm_model, request_uuid, request: (
            ToolkitSettingService.get_file_system_toolkit(
                assistant,
                assistant.project,
                user,
                llm_model,
                request_uuid,
                request.tools_config if request else None,
                cls._get_file_objects_from_request(request),
            )
        ),
        AGENT_WORKSPACE_TOOLKIT: lambda assistant, user, llm_model, request_uuid, request: (
            ToolkitSettingService.get_agent_workspace_toolkit(
                assistant,
                assistant.project,
                user,
                llm_model,
                request_uuid,
                request,
            )
        ),
        **cls.get_provider_toolkits_methods(
            request_headers=request_headers,
            conversation_id=conversation_id,
            assistant_id=assistant_id,
            llm_model=llm_model,
        ),
    }
```

- [ ] **Step 5: Update `add_tools_with_creds` to accept and use context params**

Add new parameters to `add_tools_with_creds` and pass them to `get_toolkit_methods`:

```python
@classmethod
def add_tools_with_creds(
    cls,
    assistant: Assistant,
    user: User,
    llm_model: str,
    request_uuid: str,
    request: AssistantChatRequest = None,
    skip_filtering: bool = False,
    augmented_toolkits: Optional[list] = None,
    request_headers: dict[str, str] | None = None,
):
    """Add tools that require credentials from various toolkits."""
    tools = []
    toolkit_methods = cls.get_toolkit_methods(
        request_headers=request_headers,
        conversation_id=request.conversation_id if request else None,
        assistant_id=assistant.id,
        llm_model=llm_model,
    )
    toolkits_to_process = augmented_toolkits if augmented_toolkits is not None else assistant.toolkits
    for toolkit in toolkits_to_process:
        toolkit_method = toolkit_methods.get(
            toolkit.toolkit if isinstance(toolkit.toolkit, ToolSet) else str(toolkit.toolkit)
        )
        if toolkit_method:
            all_toolkit_tools = toolkit_method(assistant, user, llm_model, request_uuid, request)
            if skip_filtering:
                tools.extend(all_toolkit_tools)
            else:
                include_internal = not isinstance(assistant, VirtualAssistant)
                tools.extend(
                    cls.filter_tools(
                        toolkits_to_process,
                        toolkit.toolkit,
                        all_toolkit_tools,
                        include_internal,
                    )
                )
    return tools
```

- [ ] **Step 6: Update the `add_tools_with_creds` call in `_get_tools`**

In `_get_tools`, find the `add_tools_with_creds` call (around line 778) and add `request_headers`:

```python
tools.extend(
    cls.add_tools_with_creds(
        assistant, user, llm_model, request_uuid, request,
        augmented_toolkits=augmented_toolkits,
        request_headers=request_headers,
    )
)
```

- [ ] **Step 7: Run tests**

```bash
source .venv/bin/activate && poetry run pytest tests/codemie/service/tools/test_toolkit_service.py -v 2>&1 | tail -40
```

Expected: ALL PASS, including new test and unchanged existing tests.

- [ ] **Step 8: Commit**

```bash
git add src/codemie/service/tools/toolkit_service.py tests/codemie/service/tools/test_toolkit_service.py
git commit -m "feat(EPMCDME-12070): thread request_headers through ToolkitService to provider toolkit tools

Fix lambda signature mismatch in get_provider_toolkits_methods (was 4 params, now
correctly 5 to match add_tools_with_creds call). Add request_headers, conversation_id,
assistant_id, llm_model context params through get_toolkit_methods →
get_provider_toolkits_methods → provider tool constructors."
```

---

## Task 3: Thread Context Params Through Datasource Context Tools Path

The second provider tool path goes through `add_context_tools` → `_add_provider_context_tools`. These tools are instantiated with `project_id`, `user`, `request_uuid`, `datasource` — we add the same context params.

**Files:**
- Modify: `src/codemie/service/tools/toolkit_service.py`
- Test: `tests/codemie/service/tools/test_toolkit_service.py`

- [ ] **Step 1: Write failing test for `_add_provider_context_tools`**

Add this test to `tests/codemie/service/tools/test_toolkit_service.py` in the provider context tools test class:

```python
@patch("codemie.service.tools.toolkit_service.ProviderToolkitsFactory")
@patch("codemie.service.tools.toolkit_service.ToolkitService._find_index")
def test_add_provider_context_tools_propagates_request_headers(
    mock_find_index, mock_provider_factory, mock_assistant, mock_user
):
    """request_headers must reach datasource tool constructors."""
    mock_index = Mock()
    mock_index.provider_fields.provider_id = "test-provider"
    mock_find_index.return_value = mock_index

    mock_tool_class = Mock()
    mock_tool_class.base_name = "some_tool"
    mock_toolkit_instance = Mock()
    mock_toolkit_instance.get_datasource_tools.return_value = [mock_tool_class]
    mock_toolkit_class = Mock(return_value=mock_toolkit_instance)
    mock_provider_factory.get_toolkits_for_provider.return_value = [mock_toolkit_class]

    mock_assistant.toolkits = [Mock(tools=[Mock(name="some_tool")])]
    mock_assistant.project = "proj"
    mock_assistant.id = "asst-99"

    tools = []
    context = Mock()
    context.context_type = "PROVIDER"
    context.name = "some-context"

    request_headers = {"X-Custom": "val"}

    ToolkitService._add_provider_context_tools(
        tools,
        mock_assistant,
        context,
        mock_user,
        "req-uuid",
        request_headers=request_headers,
        conversation_id="conv-xyz",
        llm_model="gpt-4o",
    )

    mock_tool_class.assert_called_once()
    call_kwargs = mock_tool_class.call_args.kwargs
    assert call_kwargs.get("request_headers") == request_headers
    assert call_kwargs.get("conversation_id") == "conv-xyz"
    assert call_kwargs.get("assistant_id") == "asst-99"
    assert call_kwargs.get("llm_model") == "gpt-4o"
```

- [ ] **Step 2: Run the failing test**

```bash
source .venv/bin/activate && poetry run pytest "tests/codemie/service/tools/test_toolkit_service.py::TestProviderContextTools::test_add_provider_context_tools_propagates_request_headers" -v 2>&1 | tail -20
```

Expected: FAIL — `_add_provider_context_tools` doesn't accept `request_headers`.

- [ ] **Step 3: Update `_add_provider_context_tools` signature and tool instantiation**

Replace the current `_add_provider_context_tools` method:

```python
@classmethod
def _add_provider_context_tools(
    cls,
    tools: list[BaseTool],
    assistant: Assistant,
    context: Context,
    user: User,
    request_uuid: str,
    request_headers: dict[str, str] | None = None,
    conversation_id: str | None = None,
    llm_model: str | None = None,
):
    index_info = cls._find_index(
        klass=ProviderIndexInfo,
        project_name=assistant.project,
        repo_name=context.name,
    )

    if index_info is None:
        return

    provider_id = index_info.provider_fields.provider_id
    provider_toolkits = ProviderToolkitsFactory.get_toolkits_for_provider(provider_id)
    tool_names = [tool.name for toolkit in assistant.toolkits for tool in toolkit.tools]

    for toolkit in provider_toolkits:
        context_tools = toolkit().get_datasource_tools(datasource=index_info)

        tools.extend(
            [
                tool(
                    project_id=assistant.project,
                    user=user,
                    request_uuid=request_uuid,
                    datasource=index_info,
                    request_headers=request_headers,
                    conversation_id=conversation_id,
                    assistant_id=assistant.id,
                    llm_model=llm_model,
                )
                for tool in context_tools
                if tool.base_name in tool_names
            ]
        )
```

- [ ] **Step 4: Update `add_context_tools` to accept and pass `request_headers`**

In `add_context_tools`, add `request_headers: dict[str, str] | None = None` as a parameter and pass it (along with `llm_model` and `conversation_id` from `request`) to `_add_provider_context_tools`:

```python
@classmethod
def add_context_tools(
    cls,
    assistant: Assistant,
    request: AssistantChatRequest,
    llm_model: str,
    user: User,
    request_uuid: str,
    is_react: bool = True,
    exclude_extra_context_tools: bool = False,
    request_headers: dict[str, str] | None = None,
):
    tools = []

    for context in assistant.context:
        if context.context_type == ContextType.KNOWLEDGE_BASE:
            cls._add_kb_tools(tools, context, assistant, llm_model)
        # ... other context types unchanged ...
        elif context.context_type == ContextType.PROVIDER:
            cls._add_provider_context_tools(
                tools,
                assistant,
                context,
                user,
                request_uuid,
                request_headers=request_headers,
                conversation_id=request.conversation_id if request else None,
                llm_model=llm_model,
            )
        # ... remaining context types unchanged ...

    return tools
```

Note: Only the PROVIDER branch gets the new params. Other branches (`_add_kb_tools`, `_add_code_tools`, etc.) are unchanged.

- [ ] **Step 5: Update the `add_context_tools` call in `get_tools`**

In `get_tools`, find the `add_context_tools(...)` call (around line 437) and add `request_headers`:

```python
tools.extend(
    cls.add_context_tools(
        assistant,
        request,
        llm_model,
        user,
        request_uuid,
        is_react,
        exclude_extra_context_tools,
        request_headers=request_headers,
    )
)
```

- [ ] **Step 6: Run all provider-related tests**

```bash
source .venv/bin/activate && poetry run pytest tests/codemie/service/tools/test_toolkit_service.py tests/codemie/service/provider/ -v 2>&1 | tail -40
```

Expected: ALL PASS.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/service/tools/toolkit_service.py tests/codemie/service/tools/test_toolkit_service.py
git commit -m "feat(EPMCDME-12070): thread request_headers through datasource context tools path

Update _add_provider_context_tools, add_context_tools, and the get_tools call site
to propagate request_headers, conversation_id, assistant_id, and llm_model to
provider datasource tool constructors."
```

---

## Task 4: Run Full Test Suite and Lint

Verify the complete change set doesn't break anything.

**Files:** All modified files above.

- [ ] **Step 1: Run full test suite**

```bash
source .venv/bin/activate && poetry run pytest tests/ -x -q 2>&1 | tail -30
```

Expected: ALL PASS (or at minimum, no new failures introduced by this feature).

- [ ] **Step 2: Run ruff lint and format check**

```bash
source .venv/bin/activate && poetry run ruff check src/codemie/service/provider/provider_tool_factory.py src/codemie/service/tools/toolkit_service.py && poetry run ruff format --check src/codemie/service/provider/provider_tool_factory.py src/codemie/service/tools/toolkit_service.py
```

Expected: No lint errors. If format errors, run `poetry run ruff format <file>` for each.

- [ ] **Step 3: Fix any lint issues found**

```bash
source .venv/bin/activate && poetry run ruff check --fix src/codemie/service/provider/provider_tool_factory.py src/codemie/service/tools/toolkit_service.py && poetry run ruff format src/codemie/service/provider/provider_tool_factory.py src/codemie/service/tools/toolkit_service.py
```

- [ ] **Step 4: Commit any lint fixes**

If lint made changes:
```bash
git add src/codemie/service/provider/provider_tool_factory.py src/codemie/service/tools/toolkit_service.py
git commit -m "style(EPMCDME-12070): apply ruff lint fixes"
```

---

## Self-Review Checklist

**Spec coverage:**

| Requirement | Task |
|-------------|------|
| Allowed X-* headers forwarded to DSP when `propagate_headers=True` | Task 1 Step 4: `request_headers` → `_headers` in `invoke_tool()` |
| Blocked headers never forwarded | Covered: `extract_custom_headers()` already filters them before `request_headers` is populated |
| `propagate_headers=False` → no X-* headers propagated | Task 1 Step 4: `invoke_headers = None` when `request_headers is None` |
| Context tools (datasource tools) receive propagated headers | Task 3 |
| Toolkit tools receive propagated headers | Task 2 |
| Conversation ID + Message ID forwarded | Task 1 Step 4: `X-Conversation-Id`, `X-Message-Id` |
| Assistant ID forwarded | Task 1 Step 4: `X-Assistant-Id` |
| LLM model forwarded | Task 1 Step 4: `X-LLM-Model` |
| Backward compatibility | New fields have `= None` defaults; `_headers=None` passed when no propagation |

**Potential gotcha:** The dynamically-built tool class uses `create_model` pattern via `type(...)`. Pydantic may or may not accept unknown constructor kwargs depending on `model_config`. If the tool class raises `ValidationError` for unknown `request_headers` kwarg, check if `ProviderToolBase` or `CodeMieTool` has `model_config = ConfigDict(extra='ignore')`. If not, the `__annotations__` addition in Task 1 Step 3 ensures it's a declared field (not extra), so it should be fine.

**Type consistency check:** `request_headers: dict[str, str] | None` is consistently used in `provider_tool_factory.py`, `toolkit_service.py`, and the tests.
