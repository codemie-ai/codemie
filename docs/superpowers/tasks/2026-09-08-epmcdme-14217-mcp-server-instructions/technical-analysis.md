# Technical Research

**Task**: mcp toolkit assistant-prompt skill-execution (EPMCDME-14217)
**Generated**: 2026-09-08
**Research path**: filesystem (repo is NOT codegraph-indexed — no `.codegraph/` directory at `/Users/oleg_sotnichenko/codemie-dev/codemie`)

---

## 1. Original Context

Title: MCP server-side instructions are not applied before tool calls when connected to a Skill or Assistant

## Summary
MCP server-side instructions are not applied before tool calls when connected to a Skill or Assistant.

## Description
The Support Portal MCP server provides server-side instructions that should guide assistant behavior before any MCP tool invocation. However, when the Support Portal MCP server is connected in CodeMie to a Skill or Assistant, those server instructions do not appear to be applied in the conversation context before tool calls.

Instead, the assistant starts calling MCP tools immediately and only then provides an answer. This causes incorrect runtime behavior because the server-provided instructions are expected to be available and followed before the first tool call.

The MCP server description indicates that, before the first use of the tool in a session, usage instructions should be retrieved by calling the tool with {sysparm_help=true}, and subsequent invocations should follow the returned instructions.

Reference MCP server page: https://mcp.epam.com/description?server=support

## Steps to Reproduce
1. Open CodeMie.
2. Connect the Support Portal MCP server to a Skill or Assistant.
3. Start a conversation with the Skill or Assistant.
4. Ask a question that requires the Support Portal MCP server tools.
5. Observe the order of assistant behavior and MCP tool invocation.

## Expected Result
- MCP server-side instructions are applied to the conversation context before any MCP tool call.
- Before the first tool usage in the session, the assistant/runtime retrieves and applies the server usage instructions as required by the MCP server description.
- The assistant follows those instructions for the first and subsequent MCP tool calls.
- Tool calls are made only after the relevant server instructions are available in the conversation context.

## Actual Result
- The assistant attempts to call MCP tools immediately.
- MCP server-side instructions do not appear to influence the assistant before the first tool call.

## Affected Areas
- CodeMie MCP integration runtime
- Assistant execution flow
- Skill execution flow
- MCP server instruction retrieval and injection
- Tool invocation orchestration

## Acceptance Criteria
- The runtime retrieves MCP server instructions before the first MCP tool invocation in a session when required by the server.
- Retrieved server-side instructions are injected into the conversation context before any MCP tool call is planned or executed.
- Assistants connected to the MCP server follow the server instructions from the first response/tool call onward.
- Skills connected to the MCP server follow the server instructions from the first response/tool call onward.
- Subsequent MCP tool calls in the same session reuse the already retrieved instructions unless refresh is required.
- Existing MCP tool invocation behavior remains backward compatible for MCP servers without server-side instructions.
- Automated regression coverage validates that MCP server instructions are applied before tool calls for both Assistant and Skill execution paths.

---

## 2. Codebase Findings

### Headline finding — corrects a starting assumption

The task brief asks whether "the MCP SDK `InitializeResult` (with its `instructions` field) is accessible" at the point where the client session initializes. **It is not, and there is no MCP client session in this repo at all.**

- There is **no `mcp` Python SDK dependency**. `pyproject.toml` and `poetry.lock` contain no `mcp`, no `langchain-mcp-adapters`. No `ClientSession`, `InitializeResult`, `stdio_client`, `sse_client`, or `streamablehttp_client` appears anywhere under `src/`.
- The MCP `initialize` handshake is owned by an **external Node service, MCP-Connect**, which lives in a separate repository. The Python backend is only an HTTP client of that bridge.
- `MCPConnectClient` (`/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie/service/mcp/client.py:131`) POSTs `{"method": "tools/list", ...}` to `{config.MCP_CONNECT_URL}/bridge` and parses the reply into `MCPListToolsResponse`, which declares only `tools:` (`src/codemie/service/mcp/models.py:378-389`).

So the root cause is two-layer: the bridge does not surface `instructions`, **and** the backend wire model could not carry it if it did. Because `MCPListToolsResponse` uses default pydantic config (extra fields ignored), a bridge that started returning `instructions` today would have it silently dropped with no warning or log — there is zero observability of this gap.

`grep -rn "instructions" src/codemie/service/mcp/*.py` returning nothing is therefore confirmed and explained.

### Existing Implementations

MCP layer (`/Users/oleg_sotnichenko/codemie-dev/codemie/`):
- `src/codemie/service/mcp/client.py` — `MCPConnectClient`; HTTP client to the MCP-Connect bridge. `list_tools` at L131, parsing at L169-171. Transport type (`stdio` / `sse` / `streamable-http`) is a passthrough string only (`MCPServerConfig.type` → `MCPToolInvocationRequest.http_transport_type`, client.py:120).
- `src/codemie/service/mcp/models.py` — wire models. `MCPToolDefinition` (L360: `name`/`description`/`inputSchema`), `MCPListToolsResponse` (L378-389), `MCPServerConfig` (stdio implied by `command`, HTTP by `url`, XOR-validated L309), `MCPExecutionContext`.
- `src/codemie/service/mcp/toolkit.py` — `MCPTool`, `ContextAwareMCPTool`, `MCPToolkit` (L478), `MCPToolkitFactory` (L697), `create_toolkit` (L724), `_list_tools_with_init_timeout` (L800), `_generate_cache_key` (L845).
- `src/codemie/service/mcp/toolkit_service.py` — `MCPToolkitService` (L154); entry point `get_mcp_server_tools` (L185); `_collect_server_results` (L281); `_process_single_mcp_server` (L756).
- `src/codemie/service/mcp_config_service.py` — `MCPConfigService` CRUD (L203+).
- `src/codemie/rest_api/routers/mcp_config.py` — REST CRUD (L42-215).
- `src/codemie/rest_api/models/mcp_config.py` — `MCPServerConfigData` (L97), SQLModel table `MCPConfig` (L159, `mcp_configs`), request/response models (L252/276/299).
- `src/codemie/rest_api/models/assistant.py` — `MCPServerDetails` (L174), the per-assistant MCP entry.

Assistant/skill runtime:
- `src/codemie/service/assistant_service.py` — `AssistantService.build_agent` (L500, runs **fresh per chat turn**), `_prepare_system_prompt` (L435), `get_system_prompt` (L243), `_skills_suffix` (L77), `_prepare_workflow_system_prompt` (L716/722), `decorate_system_prompt` (L641).
- `src/codemie/service/tools/toolkit_service.py` — `ToolkitService.get_tools` (L466), the single map from assistant/skill toolkits to LangChain tools; MCP branch at L1009 gated by `config.MCP_CONNECT_ENABLED`; `_merge_skill_mcp_servers` (L432-463); `_forward_mcp_auth_warnings_to_request` (L1027).
- `src/codemie/service/skills/skill_contributions.py` — `SkillContributionsResolver.resolve_for_assistant` → `SkillContributions(skills, toolkits, mcp_servers, builtin_subagents)`, cached on the runtime-only attribute `assistant._runtime_skill_contributions`.
- `src/codemie/agents/langgraph_agent.py` — `LangGraphAgent.init_agent` (L322), `_prepare_llm_for_agent` / `bind_tools` (L282), `_get_system_prompt(from_request=True)` (L1059), `_build_single_agent` (L305) with the pre-model hook chain.
- `src/codemie/agents/assistant_agent.py` — `AIToolsAgent._get_system_prompt` (L289/299/306), the react variant.
- `src/codemie/agents/tools/skill/skill_tool.py` — `SkillTool.execute(skill) -> str`, `_SKILL_OUTPUT_TEMPLATE`.
- `src/codemie/service/mcp/auth_warnings.py` — `record_mcp_auth_warnings` / `get_mcp_auth_warnings`; the existing side-channel pattern for lifting extra data out of the tool-loading loop.

### Architecture and Layers Affected

- **External / integration** — MCP-Connect Node bridge (separate repo). Owns `initialize`; must be changed first.
- **Service / MCP** — `MCPConnectClient`, `MCPToolkitFactory`, `MCPToolkitService`, wire models.
- **Service / orchestration** — `ToolkitService.get_tools`, `AssistantService.build_agent`, `_prepare_system_prompt`.
- **Agent** — `LangGraphAgent` / `AIToolsAgent` prompt assembly and tool binding.
- **Persistence** — only if instructions are stored rather than fetched live (`mcp_configs` table).
- **Workflow** — `src/codemie/workflows/nodes/tool_node.py:134` is a parallel consumer of MCP tools with its own prompt path.

### Integration Points

- Ordering that makes the fix feasible: in `build_agent`, `ToolkitService.get_tools(...)` runs at ~L561/566, and `_prepare_system_prompt` runs at L584. **Tools, including MCP, are already resolved before the prompt is assembled.** This ordering holds today but is not enforced by any contract.
- `get_mcp_server_tools` has **five other callers** besides the assistant path: `src/codemie/workflows/nodes/tool_node.py:134`, `src/codemie/workflows/config_resources_validation.py:181`, `src/codemie/workflows/validation/resources.py:348`, `src/codemie/service/tools/mcp_tools_info_service.py:58`, `src/codemie/service/mcp/mcp_tester.py:85`. Changing its return type breaks all five; a contextvar side channel does not.
- Lossy boundary: at `src/codemie/service/mcp/toolkit_service.py:819`, `tools = toolkit.get_tools()` discards the `MCPToolkit` object — including its `description` — and only a flat `list[MCPTool]` propagates upward.

### Patterns and Conventions

- **Skills contribute MCP servers through the exact same path as assistants.** `ToolkitService._merge_skill_mcp_servers` (L432) unions skill MCP servers into the assistant's list before `get_mcp_server_tools` is called. Skills are *not* a separate executor and have *no* separate prompt builder — a skill's markdown `content` is delivered lazily as a `ToolMessage` from `SkillTool.execute`, never merged into the system message. Practically, one correct fix at the assistant prompt seam covers both acceptance criteria (Assistant and Skill), provided skill-contributed servers are resolved before the prompt is built — which they are.
- Prompt-suffix append precedent: `_prepare_system_prompt` already concatenates `_skills_suffix` (L449), the output-schema prompt (L452-455), and the A2UI block via `render_prompt_section()` from `src/codemie/core/a2ui/catalog.py` (L466-468). A2UI is the cleanest in-repo model for a conditionally rendered prompt section.
- Runtime-only attribute precedent: `assistant._runtime_skill_contributions`.
- Side-channel precedent: `record_mcp_auth_warnings` / `get_mcp_auth_warnings`.
- Sandboxed Jinja2 rendering (`render_system_prompt_with_vars`, `TemplateSecurityError`) — untrusted text must be appended *after* rendering, never fed into template input.
- There is **no** generic `get_tools_description` / prompt-hint protocol on `BaseToolkit`. `MCPToolkit.description` exists (toolkit.py:492) but is dead weight: nothing reads it into any prompt.

---

## 3. Documentation Findings

### Guides and Architecture Docs

`/Users/oleg_sotnichenko/codemie-dev/codemie/.ai-run/guides/` exists:
- `integration/mcp-integration.md` — directly relevant; documents MCP config boundaries and the init-timeout triad (`MCP_SERVER_INIT_TIMEOUT` / `MCP_CONNECT_INIT_TIMEOUT` ms / `MCP_CLIENT_TIMEOUT`). Will need a new section on instruction injection.
- `testing/testing-patterns.md` — mandates co-locating tests under the nearest `tests/<pkg path>/`, mocking at provider boundaries, and has a "Seam Tests for Policy Helpers" rule that maps directly onto this ticket: a unit test on an injector helper is not sufficient; a callsite test asserting the boundary `system_prompt` is required.
- `testing/testing-service-patterns.md`, `testing/testing-api-patterns.md`, `development/configuration-patterns.md`, `agents/tool-overview.md`, `agents/agent-tools.md`, `agents/custom-tool-creation.md`, `agents/langchain-agent-patterns.md`, `architecture/layered-architecture.md`, `architecture/service-layer-patterns.md`, `standards/code-quality.md`, `standards/git-workflow.md`, `quality-gates.md`, `project.md`.
- `/Users/oleg_sotnichenko/codemie-dev/codemie/AGENTS.md` — guide routing table (MCP at L41, Testing at L44-46); explicit policy at L112 that tests are written and run only when explicitly requested. This ticket's acceptance criteria explicitly request regression coverage, so that policy is satisfied here.
- `docs/workflows/08_integration_capabilities.md`, `docs/workflows/02_configuration_reference.md` — MCP in workflows.

### Architectural Decisions

- Comment at `src/codemie/service/mcp/toolkit.py:861-867` records a past cross-user credential leak and explains why the toolkit cache key hashes credentials into the key. Any change to caching must preserve that property.
- `MCPServerConfig` XOR validation (models.py:309) — a server is stdio (`command`) or HTTP (`url`), never both.

### Derived Conventions

- Three coexisting feature-flag mechanisms; pick deliberately:
  1. `config.<FLAG>` env-backed booleans (`src/codemie/configs/config.py`) — operator-level.
  2. `customer_config.is_feature_enabled("<camelCaseKey>")` (`src/codemie/configs/customer_config.py:290-302`) — per-tenant; **defaults to True when the key is absent**. Precedent: the `interactiveElements` gate inside `_prepare_system_prompt` (L456-468).
  3. Per-request booleans on `AssistantChatRequest`, ANDed with (2).
- Alembic migrations live under `src/external/alembic/versions/` (precedents `9ad50751a56a_mcp_config.py`, `d5e9f2a3b1c7_combined_mcp_config_setup.py`).

### External Documentation Findings

- Spec definition, byte-identical across revisions 2024-11-05, 2025-03-26 and 2025-06-18: `"Instructions describing how to use the server and its features."` … `"This can be used by clients to improve the LLM's understanding of available tools, resources, etc. It can be thought of like a \"hint\" to the model. For example, this information MAY be added to the system prompt."` — https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2025-06-18/schema.ts (L206-212). Declared optional: `instructions?: string;`.
- Normative strength is only `MAY`. No spec page prescribes placement, ordering, or per-server labelling. The intended shape — append to the system prompt — matches the design this ticket asks for.
- Lifecycle page shows it as a top-level key of the initialize response: `"instructions": "Optional instructions for the client"` — https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle
- Python SDK (not used by this repo, but relevant to the bridge if it is TS/Python-agnostic): `async def initialize(self) -> types.InitializeResult:` returns the result; `InitializeResult.instructions: str | None = None` — https://github.com/modelcontextprotocol/python-sdk/blob/v1.12.4/src/mcp/types.py (L333-341).
- Important gotcha stated by langchain-mcp-adapters: `"There is no way to recover an InitializeResult from a session that is already initialized: the MCP SDK caches only capabilities and discards instructions, serverInfo and protocolVersion."` — https://github.com/langchain-ai/langchain-mcp-adapters/blob/main/langchain_mcp_adapters/server_info.py. **Implication for the bridge fix: `instructions` must be captured at handshake time; it cannot be read back later from a live session.**
- Where a library does expose it: `load_mcp_server_info(session, ...) -> InitializeResult` and `MultiServerMCPClient.get_server_info(...) -> dict[str, InitializeResult]`, the latter opening a fresh handshake per call (results not cached) — same repo, `client.py` L218-300. On the normal `load_mcp_tools` / `get_tools` path, instructions are dropped.
- Forward-compatibility note: on MCP `main`, `InitializeResult` is marked `"Removed in protocol 2026-07-28; sent/received on sessions negotiating <= 2025-11-25."` and a new `DiscoverResult` (`server/discover`) also carries `instructions`. A design that assumes only the initialize handshake will age out.
- `https://mcp.epam.com/description?server=support` is **not fetchable** — HTTP 403 via WebFetch; direct request redirects to EPAM SSO and ends at HTTP 400. It is an authenticated internal gateway. Its actual instruction text (the `{sysparm_help=true}` convention cited in the ticket) could not be verified and should be confirmed with the reporter.

---

## 4. Testing Landscape

### Existing Coverage

MCP core, `/Users/oleg_sotnichenko/codemie-dev/codemie/tests/codemie/service/mcp/`:
- `test_client.py` (70K) — `MCPConnectClient` list_tools/invoke_tool over the bridge; mocks `codemie.service.mcp.client.httpx.AsyncClient`.
- `test_models.py` (40K) — pydantic models incl. `MCPListToolsResponse`, `MCPToolDefinition`, `MCPServerConfig`.
- `test_models_1_2_auth_config.py`, `test_models_execution_context.py`.
- `test_toolkit_mcp_tool.py` (32K), `test_toolkit_mcp_toolkit_creation.py`, `..._init.py`, `..._factory.py`, `..._access.py`, `..._validation.py` — construction, tool name/description mapping, caching.
- `test_toolkit_service_*.py` — 14 files, incl. `test_toolkit_service_auth_resolver.py` (108K).
- `test_filter_tools_by_config.py`, `test_preprocess_command.py`, `test_normalize_placeholders.py`, `test_execution_context_integration.py`, `test_mcp_tester.py`.

MCP elsewhere: `tests/codemie/service/tools/test_mcp_tools_info_service.py`, `tests/codemie/rest_api/routers/test_assistant_mcp_tools.py`, `tests/codemie/rest_api/routers/test_mcp_managed.py`, `tests/codemie/configs/test_managed_mcp_config.py`, `tests/codemie/configs/test_mcp_commands_config.py`, `tests/codemie/configs/test_config_mcp_headers.py`, `tests/codemie/service/test_mcp_config_service_auth_*.py` (4), `tests/codemie/agents/test_mcp_auth_required_propagation.py`, `tests/codemie/rest_api/test_mcp_auth_required_handler.py`, `tests/codemie/workflows/test_mcp_auth_required_storage.py`, `tests/codemie/workflows/test_workflow_mcp_servers.py`, `tests/enterprise/mcp_auth/` (17 files).

Prompt assembly:
- `tests/codemie/service/test_assistant_service_methods.py` — `class TestPrepareSystemPrompt` (~L446-550), plus prompt tests at L1020-1049. **The single closest existing seam** for asserting injected MCP instructions.
- `tests/codemie/core/test_a2ui_catalog_and_adapter.py` (L122-181) — best in-repo precedent for asserting a rendered prompt section is present and complete.
- `tests/codemie/service/assistant/test_langgraph_assistant.py`, `test_configure_agent_kwargs_confirmation.py`, `tests/codemie/service/subagents/test_builtin_subagents_registry.py`, `tests/codemie/service/test_assistant_service_headers.py`, `tests/codemie/service/tools/test_tool_discovery_service.py`.

### Testing Framework and Patterns

- pytest `^8.3.1`, pytest-asyncio `^0.23.7`, pytest-mock `^3.14.0`, pytest-env `^1.1.3`, pytest-cov `^5.0.0`, pytest-httpx `^0.35.0` (`pyproject.toml:182-191`).
- `pytest.ini`: `testpaths = tests`, `pythonpath = src`, `--import-mode=importlib`, `ENV=local`.
- `tests/conftest.py` — session-autouse DB engine mock, stubs `tree_sitter_languages` / `python_calamine`, loads `tests/.env.test` (no MCP vars). No MCP-specific conftest fixture; MCP fixtures are local per module.
- Mock convention: no real MCP `ClientSession` is mocked anywhere, because none exists. Tests mock `httpx.AsyncClient` at the bridge boundary, or patch `MCPConnectClient.list_tools` directly. This is the natural place to inject a fake `instructions` value in new tests. Mixed `unittest.TestCase` and plain pytest classes.

### Coverage Gaps

- No `instructions` field exists anywhere in the MCP layer, so no test can exist today.
- **No test asserts the final assembled system prompt end-to-end.** `TestPrepareSystemPrompt` mocks `get_system_prompt` and only asserts substring/suffix behaviour of the decorations.
- No test covers `LangGraphAgent._get_system_prompt` composition (langgraph_agent.py:1059-1068).
- No workflow-node equivalent test for prompt + MCP instruction assembly; `test_workflow_mcp_servers.py` covers the server list only.
- Skill prompt path is untested beyond `"available skills" in result`.
- Bridge-side behaviour (whether MCP-Connect forwards `instructions`) is untestable from this repo.

---

## 5. Configuration and Environment

### Environment Variables

All in `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie/configs/config.py`:
- `MCP_CONNECT_ENABLED` (L544, default True) — master switch for the bridge.
- `MCP_CONNECT_URL` (L545, `http://localhost:3000`) — bridge base URL.
- `MCP_CONNECT_BUCKETS_COUNT` (L546) — bridge bucket sharding.
- `MCP_CLIENT_TIMEOUT` (L566, 300.0s) — per-invocation `tools/call` timeout.
- `MCP_SERVER_INIT_TIMEOUT` (L567, 300.0s) — guards the call that would carry `instructions`.
- `MCP_TOOL_TOKENS_SIZE_LIMIT` (L547, 30000) — relevant: injected instructions interact with the token budget.
- `MCP_TOOLKIT_SERVICE_CACHE_SIZE` / `_TTL` (L554-555, 100 / 3600s) and `MCP_TOOLKIT_FACTORY_CACHE_SIZE` / `_TTL` (L558-559, 50 / 600s) — relevant: cached toolkits would cache stale instructions.
- `MCP_AUTH_ENABLED` (L490), `MCP_AUTH_HMAC_SECRET` (L491), `MCP_AUTH_TMS_*` (L509-529), `MCP_AUTH_REDIS_KEY_NAMESPACE` (L531), `MCP_AUTH_ENFORCE_HTTPS` (L533), `MCP_AUTH_DISCOVERY_CONCURRENCY_LIMIT` (L535), `MCP_AUTH_*_TIMEOUT_SECONDS` (L536-539), `MCP_AUTH_CALLBACK_KEEP_TAB_OPEN` (L543), `BROKER_AUTH_LOCATION_URL` (L317).
- No MCP entries in `.env.example`.

### Configuration Files

- `src/codemie/configs/config.py` — all `MCP_*` settings (pydantic `BaseSettings`).
- `src/codemie/configs/customer_config.py` — per-tenant toggles, `is_feature_enabled` (L290-302).
- `src/codemie/configs/managed_mcp_config.py` — loads `managed-mcp-servers.yaml` from a ConfigMap dir; the file is not committed and degrades to an empty list.
- `src/codemie/configs/mcp_commands_config.py` — allowed MCP commands.
- `pytest.ini`, `tests/.env.test`, `.coveragerc`.

### Feature Flags and Deployment Concerns

- Caching TTLs are a deployment-visible behaviour: with `MCP_TOOLKIT_FACTORY_CACHE_TTL=600` and `MCP_TOOLKIT_SERVICE_CACHE_TTL=3600`, a server changing its instructions is invisible for up to an hour.
- The toolkit cache key (`_generate_cache_key`, toolkit.py:845) is a SHA-256 of sorted JSON of `{command|url, args, env, headers, auth_token, auth_config, bucket_key}`. It is not keyed by user/assistant/conversation; per-user isolation is emergent from credentials landing in `env`/`headers`/`auth_token`. `MCPToolkitService` is a process singleton, so the cache is process-wide.
- Storage choice affects migration cost: adding a column to the `mcp_configs` SQLModel table requires an Alembic migration; adding the field inside the JSON `config` blob (`MCPServerConfigData`, stored as `PydanticType`) needs no DDL.

---

## 6. Risk Indicators

- **Cross-repo blocker.** The fix cannot be completed in `codemie` alone. MCP-Connect (external Node repo, `POST /bridge`) must be changed to return `InitializeResult.instructions`. Per the langchain-mcp-adapters note, the bridge must capture it *at handshake time* — it cannot be read back from an already-initialized session. This dominates the schedule and should be confirmed before planning backend work.
- **Silent-drop today.** `MCPListToolsResponse` ignores extra fields, so a bridge already returning `instructions` would be discarded with no log. Zero observability of the gap.
- **Three prompt-assembly paths must stay in sync**, or one silently loses instructions: chat (`assistant_service.py:435` `_prepare_system_prompt`), workflow (`assistant_service.py:716/722` `_prepare_workflow_system_prompt`), and Bedrock (`_build_bedrock_agent`, ~L296, early-returns before the normal path). Agent-level `_get_system_prompt` exists in two more files (`langgraph_agent.py:1059`, `assistant_agent.py:299`).
- **`request.system_prompt` override.** `_get_system_prompt(from_request=True)` replaces the whole prompt string with the request-supplied one. Injection performed only at `_prepare_system_prompt` is silently dropped on that path.
- **Return-contract fragility.** Carrying instructions upward by changing `get_mcp_server_tools`'s return type breaks five other callers (workflow tool_node, two validators, mcp_tools_info_service, mcp_tester). The existing `record_mcp_auth_warnings` contextvar pattern avoids this and is the lower-risk route.
- **Prompt-injection / untrusted content.** MCP `instructions` is third-party server text going into the system prompt. It must be length-capped, clearly fenced and attributed (e.g. `<mcp_server_instructions name="...">`), appended *after* Jinja rendering (never as template input — `render_system_prompt_with_vars` is sandboxed deliberately), and probably gated per tenant. No sanitizer exists for this path; `test_template_security.py` covers templates only.
- **Duplication under recursion.** Sub-assistants and builtin subagents re-enter `build_agent` and re-resolve the same MCP servers, so the same instructions block can be appended N times and burn context. Dedupe by server name.
- **Ordering assumption.** Injection at `_prepare_system_prompt` relies on `get_tools` having already run (L561/566 before L584). True today, unenforced, and smart tool selection already defers toolkit choice — a future lazy MCP resolution would yield empty instructions.
- **Token budget and prompt caching.** Appending instructions changes the system-prompt prefix, which can invalidate provider prompt caching (`get_disable_prompt_cache()` is already tracked in `build_agent`), and interacts with `MCP_TOOL_TOKENS_SIZE_LIMIT=30000` and `tests/codemie/agents/test_langgraph_truncation.py`.
- **Failure-mode discipline.** MCP loading already raises `MCPToolLoadException` / `MCPAuthenticationRequiredException` mid-assembly. Instruction retrieval must be best-effort (mirroring `_run_discovery_probe_and_collect_failures`) and must never add a hard-failure path — required by the "backward compatible for servers without instructions" acceptance criterion.
- **Stale-cache semantics.** Instructions cached on `MCPToolkit` ride the existing TTL cache for free, but stay stale up to 600s (factory) / 3600s (service) after a server updates them. "Refresh if required" in the acceptance criteria needs an explicit decision here.
- **Ticket ambiguity — two different requests conflated.** The acceptance criteria ask for the standard MCP `instructions` mechanism (inject server hints into context). The Description additionally cites a server-specific convention: calling the tool with `{sysparm_help=true}` before first use. Those are different features — passive prompt injection versus an active mandatory pre-flight tool call. The referenced server page is behind EPAM SSO and could not be read to disambiguate. This must be resolved before design.
- **Test-harness coupling not visible here.** `Makefile:73-74` runs `uvx codemie-test-harness --sanity-api`; the harness lives in `codemie-sdk` and its MCP catalog expectations are invisible from this repo. It can break silently.
- **Worktree hazard.** A sibling checkout `codemie-13968/` contains byte-identical copies of these files. Verify every edit lands under `/Users/oleg_sotnichenko/codemie-dev/codemie/`.
- No test anywhere asserts a fully assembled system prompt — the regression coverage this ticket demands has to build that seam from scratch.

---

## 7. Summary for Complexity Assessment

**Layers and change surface.** This task spans the MCP service layer, the orchestration layer, and the agent layer of the Python backend — and, critically, an **external repository**. The `initialize` handshake that produces `instructions` happens entirely inside the MCP-Connect Node bridge; the backend never constructs an MCP `ClientSession` and does not even depend on the `mcp` Python SDK. Nothing can be injected until MCP-Connect surfaces `instructions` in its `tools/list` (or a new endpoint) response. On the backend side the surface is moderate but wide: a new field on `MCPListToolsResponse` and `MCPToolDefinition`-adjacent models (`src/codemie/service/mcp/models.py`), plumbing through `MCPToolkitFactory.create_toolkit` / `MCPToolkit` (`toolkit.py`), a side channel out of `MCPToolkitService._process_single_mcp_server` (which currently discards the toolkit object at `toolkit_service.py:819`), and an append in `AssistantService._prepare_system_prompt` plus its two sibling assemblers (`_prepare_workflow_system_prompt`, `_build_bedrock_agent`). Realistically 6-10 source files plus tests, on top of a cross-repo dependency. One structural mercy: skills contribute MCP servers through the same `_merge_skill_mcp_servers` path as assistants and have no separate prompt builder, so a single correct injection at the assistant seam satisfies both the Assistant and Skill acceptance criteria.

**Technical novelty.** The mechanics follow established in-repo patterns closely — the A2UI `render_prompt_section()` block and the static `_skills_suffix` are exact precedents for a conditionally appended prompt section, and `record_mcp_auth_warnings` / `get_mcp_auth_warnings` is an existing contextvar side channel for lifting extra data out of the tool-loading loop (preferable to changing `get_mcp_server_tools`'s return type, which has five other callers). What is genuinely new is the security posture: this would be the first place untrusted third-party server text is concatenated into a system prompt, requiring fencing, length capping, dedupe under subagent recursion, and probably a `customer_config` gate. Two design questions are unresolved and should be settled before planning. First, the ticket conflates two different features — the standard MCP `instructions` field (spec-normative only as a `MAY`) versus a server-specific `{sysparm_help=true}` mandatory pre-flight tool call; the referenced EPAM server page is behind SSO and unreadable, so the reporter must disambiguate. Second, cache semantics: instructions would ride the existing toolkit TTL caches for free but go stale for up to 600s/3600s, which bears directly on the "reuse unless refresh is required" criterion.

**Test coverage posture — mixed, trending bad where it matters.** The MCP layer itself is heavily tested (roughly 30 dedicated files, several very large, all mocking at the `httpx.AsyncClient` bridge boundary — a convenient place to fake an `instructions` value). Prompt assembly is the opposite: `TestPrepareSystemPrompt` mocks `get_system_prompt` and asserts only substrings, no test anywhere asserts a fully assembled system prompt, `LangGraphAgent._get_system_prompt` is untested, and the skill prompt path is checked only by `"available skills" in result`. The ticket's final acceptance criterion demands regression coverage across both Assistant and Skill paths, and the repo's own `testing/testing-patterns.md` "Seam Tests for Policy Helpers" rule forbids satisfying that with a unit test on an injector helper alone — a callsite test asserting the boundary `system_prompt` is required. That seam does not exist yet and must be built.

**Key risk factors for scoring:** external-repo blocking dependency (highest); silent-drop with zero observability today; three-to-five divergent prompt assembly paths plus a `request.system_prompt` override that bypasses the chosen seam; untrusted-content injection into the system prompt; prompt-cache invalidation and token budget; requirement ambiguity that cannot be resolved from the codebase; and an absent end-to-end prompt test seam.
