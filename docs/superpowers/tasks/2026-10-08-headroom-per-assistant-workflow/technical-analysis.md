# Technical Research

**Task**: headroom compression assistant workflow langgraph pre_model_hook (EPMCDME-15645)
**Generated**: 2026-10-08
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-15645 — Research and integrate Headroom (context compression for LLM calls) into the CodeMie backend, with UI-configurable settings separately for each assistant and each workflow (plus per-workflow-node overrides). Acceptance criteria: (1) SDK integration approach researched and documented; (2) Headroom integrated per the documented approach; (3) config separately per assistant and per workflow; (4) saved config affects only the intended assistant/workflow; (5) configured integration is used when the assistant/workflow runs; (6) unconfigured assistants/workflows keep existing behavior; (7) validation and regression tests cover integration and config scope; (8) usage and configuration documented.

Design already decided in brainstorming (verify against the code, do not re-litigate): backend calls Headroom itself from a LangGraph pre_model_hook appended last in the agent hook chain (langgraph_agent.py _build_single_agent ~line 351 and supervisor graph ~line 312, via _compose_pre_model_hooks in src/codemie/agents/supervisor/pre_model_hooks.py). Sidecar mode (HTTP POST /v1/compress) when is_litellm_enabled, in-process headroom-ai SDK otherwise. Config HeadroomConfig {enabled, compress_user_messages, compress_tool_outputs, strategy lossless|balanced|aggressive}; precedence node > workflow > assistant > off; fail-open. Assistant storage follows hedging_config (src/codemie/rest_api/models/assistant.py ~344 and ~672, handlers/assistant_handlers.py ~1496); workflow storage follows pool_config (src/codemie/core/workflow_models/workflow_config.py ~246-274, workflow_models.py WorkflowAssistant ~52, src/codemie/workflows/execution_config_schema.yaml, alembic migration t1u2v3w4x5z7_add_sub_workflow_pool_config.py). Existing artifacts to build on, read them: docs/superpowers/specs/2026-10-08-headroom-per-assistant-workflow-design.md and docs/headroom/research-findings.md.

Focus on facts the plan needs: exact files/functions to change for assistant create/update/get/list/copy/export paths and workflow YAML parse/save paths; how build_agent_for_workflow (src/codemie/service/assistant_service.py ~785) and the chat path construct LangGraphAgent and where a resolved config can be passed; how sub-assistants and A2A assistants are built; existing test locations and fixtures for these areas under tests/; how customer_config.is_feature_enabled flags and settings/config classes under src/codemie/configs are defined; the latest alembic head revision and naming convention; docker-compose.yml service patterns; any reasons the hook approach could break (state shape, message types, image artifacts, cache_control in additional_kwargs, structured output). Also list risk indicators.

---

## 2. Codebase Findings

Stack: Python 3.12, Poetry, FastAPI, SQLModel/Postgres, langgraph 1.1.6, langchain 1.2.15, httpx ^0.28, tiktoken 0.12.0 (all already deps). No `headroom` code or dep exists yet (grep of src, config, docker-compose, pyproject, .ai-run: none).

### Existing Implementations (all line numbers verified against HEAD 044476dd5)

**Agent / hook chain** (`src/codemie/agents/langgraph_agent.py`, 1692 lines)
- `LangGraphAgent.__init__` (l.136-249): kwargs list ends `is_subagent`; builds `self.history_compaction_pre_model_hook` (l.198) then calls `self.init_agent()` at l.234 (hook members must be set BEFORE l.234).
- `_build_supervisor_agent` (l.293-321): `create_supervisor(..., pre_model_hook=_compose_pre_model_hooks(_strip_handoff_back_messages_pre_model_hook, _strip_subagent_task_messages_pre_model_hook))` at l.312. No history-compaction or image hook here today.
- `_build_single_agent` (l.341-358): `create_smart_react_agent(..., pre_model_hook=_compose_pre_model_hooks(self.history_compaction_pre_model_hook, _subagent_task_pre_model_hook, _image_artifact_pre_model_hook))` at l.351-357. `_image_artifact_pre_model_hook` is currently last.
- `init_agent` (l.360): subagents → supervisor, else single agent. `AIToolsAgent` (`agents/assistant_agent.py:143`) is a different class with no `pre_model_hook` code at all.
- `_initialize_llm` (l.622): `build_chat_model_for(router, ...)` returns a raw chat model (so `with_structured_output` keeps working).

**Hook helpers** (`src/codemie/agents/supervisor/pre_model_hooks.py`, 72 lines)
- `_compose_pre_model_hooks(*hooks)`: drops `None`s, returns `None` if none active; runs hooks sequentially on a copied state; a hook result containing `llm_input_messages` replaces `working_state["messages"]` for the next hook; combined updates returned. A hook returning `{}`/falsy is skipped. Passing `None` as the new last arg is a no-op, so unconfigured agents keep an identical chain.
- `_image_artifact_pre_model_hook` here ALWAYS returns `{"llm_input_messages": ...}` (even if unchanged).
- Precedent for sync hook with side work: `ConversationHistoryCompactionService.build_langgraph_pre_model_hook` (`src/codemie/service/conversation/history_compaction_service.py:83-109`) runs `asyncio.run(...)` inside the sync hook and returns `{}` when unchanged.
- `smart_react_agent.py` threads `pre_model_hook` through every branch unchanged (l.51, 89, 111, 147, 188, 304, 363).

**Chat path** (`src/codemie/service/assistant_service.py`, 977 lines)
- `build_agent` (classmethod l.503-653): early-returns `_build_bedrock_agent` (l.541) for Bedrock → `AIToolsAgent`, no hook possible. Builds `agent_kwargs` dict (l.604-621), `agent_class = LangGraphAgent if config.ENABLE_LANGGRAPH_AITOOLS_AGENT (default True, config.py:673) and not is_react else AIToolsAgent` (l.601). LangGraph-only kwargs are added in the `if agent_class == LangGraphAgent:` block (l.624-636; `is_subagent` is set there). New `headroom_config` kwarg MUST go in this block (AIToolsAgent would reject it).
- `_configure_langgraph_agent` (l.476) → `LangGraphAssistantBuilder.configure_agent_kwargs` (`service/assistant/assistant_engine_builder.py:~277`) also sets `agent_kwargs` (smart tool selection, tool confirmation, subagents). Alternative single place to inject the resolved config for both chat and workflow (it is called by both paths with the `assistant`).
- `build_agent` is called from: `assistant_handlers.py` l.645, 1128, 1189, 1290; `hedged_handler.py:485`; `a2a/server/codemie_task_manager.py:162`; `assistant_health_check_service.py:305`; `tools/assistant_factory.py:101`. All are covered if resolution lives inside `build_agent`.

**Workflow path**
- `build_agent_for_workflow` (l.785-930): signature has NO workflow config today (params: user_input, user, request_uuid, workflow_assistant, workflow_state, tool_callbacks, thread_generator, file_names, resume_execution, execution_id, project_name, mcp_server_args_preprocessor, request_headers, trace_context, disable_cache, owner_user_id, is_global). `assistant` is loaded by `_load_and_configure_workflow_assistant` (l.693): `Assistant.get_by_id(workflow_assistant.assistant_id)` or `VirtualAssistantService.create_from_virtual_asst_config` (no `assistant_id`). Agent class chosen by `_select_agent_class_for_workflow` (l.775; also excludes Bedrock and react LLMs); kwargs l.892-909; `_configure_langgraph_agent(...)` called at l.912-915.
- Carrier for the workflow-level override: `AssistantExecutionOptions` dataclass (`src/codemie/workflows/utils/utils.py:517-530`) → `initialize_assistant` (l.533) → `build_agent_for_workflow`. Callers constructing options: `workflows/nodes/agent_node.py:277-295` (has `self.workflow_config`), `workflows/workflow.py:677-692` (`Workflow.initialize_assistant`, has `self.workflow_config`), used by `workflows/supervisor_workflow.py:104`. All three have the parsed `WorkflowConfig` (with `.headroom_config` after change).
- `agent_node.py` already clones `workflow_assistant` via `model_copy(update=...)` (l.265) — precedent for per-node mutation.

**Sub-assistants / builtin subagents / A2A**
- Saved sub-assistants: `create_subagent_executors` (`assistant_engine_builder.py`) → `create_assistant_executors` → `AssistantFactory.build()` (`service/tools/assistant_factory.py:101`) → `AssistantService.build_agent(..., is_subagent=True)` → returns `agent.agent_executor`. So each sub-assistant gets its own `LangGraphAgent` and its own hook chain from `build_agent`; resolution inside `build_agent` gives per-sub-assistant config for free. Version pinning via `AssistantVersionService.apply_version_to_assistant`.
- Builtin subagents: `_build_builtin_subagent_executor` (`assistant_engine_builder.py:~160`) does `parent.model_copy(deep=False)` then `AssistantFactory.build()` → inherits parent's `headroom_config` automatically.
- Workflow-node assistants with `assistant_ids` build sub-assistants via the same `build_agent` (chat-style, config = each sub-assistant's own; the workflow/node override does NOT propagate to them unless explicitly designed).
- A2A: `AssistantType.A2A` → `A2AAssistantHandler` (`assistant_handlers.py:1321`, `get_request_handler` l.1491-1500); it calls a remote agent, no local agent built → config stored but never applied. The A2A *server* side (`codemie_task_manager.py:162`) uses `build_agent`, so is covered.
- Hedged requests: `get_request_handler` (l.1496) gates on `assistant.hedging_config is not None and customer_config.is_feature_enabled("requestHedging")`; `hedged_handler.py:485` builds the agent via `build_agent` (covered).

**Assistant model / storage / API** (`src/codemie/rest_api/models/assistant.py`)
- `AssistantRequest.hedging_config` l.344 (pydantic request model, class at l.312); `AssistantBase.hedging_config` l.672 (`SQLField(default=None, sa_column=Column(PydanticType(HedgingConfig)))`).
- Create: `routers/assistant.py` `create_assistant` (l.837) → `_build_assistant_from_create_request` (l.~748) = `Assistant(**request.model_dump(exclude={...}))` → any new field on `AssistantRequest` + `AssistantBase` flows automatically.
- Update: `update_assistant` (l.884) → `AssistantRepository.update` → `Assistant.update_assistant` (assistant.py:1104) → `_map_assistant_request` (l.886): reflection over `AssistantRequest.model_fields`, only fields in `request.model_fields_set` are copied. New field maps automatically (and an omitted field leaves stored value untouched; explicit `null` clears).
- Versioning: `AssistantConfiguration` (l.1244) and `service/assistant/assistant_version_service.py` (l.61, 119, 284, 330) copy selected fields per version (e.g. `file_attachment_enabled`); `hedging_config` is NOT versioned (master record only). `AssistantVersionCompareService.has_configuration_changes` decides whether a version is created. Rollback/apply_version therefore does not touch `hedging_config`; the same applies if `headroom_config` mirrors it.
- Get: `GET /assistants/id/{id}` (l.368) has `response_model=Assistant` → full SQL model incl. new field. Slug GET (l.382) same. List: `AssistantListResponse` (assistant.py:583) has no config fields → list needs no change. `NestedAssistantResponse` (l.604) has no hedging → not needed.
- Copy/clone: no server-side clone endpoint; the client POSTs a full config (optionally `source_assistant_id`, only used for `_update_source_clone_count`, router l.807). Marketplace publish/unpublish routers exist (l.1520, 1822) but copy fields via the same create request. Export: `ExportAssistantRequest` (`core/models.py:946`) is only `env_vars`; no assistant-config export that enumerates fields was found (nothing to change).
- Feature gate precedent: only `get_request_handler` gates on `customer_config.is_feature_enabled("requestHedging")`; stored config is kept when flag is off.

**Workflow model / storage** (`src/codemie/core/workflow_models/`)
- `WorkflowAssistant` (`workflow_models.py:52-66`): plain pydantic BaseModel (default `extra=ignore` → an unknown `headroom:` key on an assistants[] entry is SILENTLY DROPPED until a field is added). `WorkflowPoolConfig` (l.364-370) is the model for pool_config.
- `WorkflowConfigBase` (`workflow_config.py:76`): `pool_config` SQLField with `PydanticType` at l.121-123; `parse_execution_config` (l.239-275) builds `assistants = [WorkflowAssistant(**{**d, "skill_ids": ...})]` (l.246-249) and sets `self.pool_config = WorkflowPoolConfig(**pool_cfg) if pool_cfg else None` (l.273-274). `WorkflowConfig` (l.320, `table=True`, `__tablename__="workflows"`) inherits columns; `WorkflowConfigTemplate` (l.365) also extends the base.
- `parse_execution_config` callers: `workflow.py:164,258` (execution load), `workflow_service.py:90` (`get_workflow`), `:794` (`_update_workflow_values`, after `yaml_config` set), `routers/workflow.py:523,602,650,747`. In `_editable_non_boolean_fields` (workflow_service.py:61-72) `pool_config`/`headroom_config` are NOT listed: they are re-derived from `yaml_config` on every update, not copied from the request. Same will hold for `headroom_config`.
- Validation: `workflows/execution_config_schema.yaml` is loaded by TWO validators — `workflows/config_yaml_validation.py:248-263` and `workflows/validation/schema.py:629-645` (Draft202012Validator). The file contains NO `additionalProperties` anywhere today (unknown keys pass). `pool_config` top-level block at l.76-81; `$defs.Assistant` at ~l.95-125 (`required: [id]`, `anyOf` assistant_id|system_prompt).

**Workflow save path (CRITICAL)** — `routers/workflow.py`
- `create_workflow` (~l.560-615) and `update_workflow` (l.~620-660) call `parse_execution_config()`, `_normalize_workflow_model_ids` (l.101) then `_rewrite_yaml_config_from_objects` (l.128; calls at l.597, l.646).
- On HEAD this function REBUILDS `yaml_config` from only 7 keys (`messages_limit_before_summarization`, `tokens_limit_before_summarization`, `enable_summarization_node`, `assistants`, `custom_nodes`, `tools`, `states`) via `yaml.safe_dump`. Every other top-level key — including `pool_config`, `retry_policy`, `recursion_limit`, and any new workflow-level `headroom:` — is DROPPED from the stored YAML on POST/PUT. Node-level `headroom:` would survive only if added to `WorkflowAssistant` (it is `model_dump(exclude_none=True)`-ed).
- The fix exists on another branch: commits `1aa7cc0e9` (patch only normalized model ids, store YAML as sent), `0548d01fe`, `a1ee0de0d` (EPMCDME-15759). `git merge-base --is-ancestor 1aa7cc0e9 HEAD` → NOT in current branch (`EPMCDME-15645_headroom-per-assistant-workflow`, based on main).

### Architecture and Layers Affected
- Agent runtime: `LangGraphAgent` (+ supervisor builder) hook chain; new package `src/codemie/service/headroom/` (models, resolver, strategies, compressor protocol, sidecar/SDK compressors, factory, hook) per design.
- Service: `AssistantService.build_agent` / `build_agent_for_workflow`; `workflows/utils/utils.py` options; `agent_node.py`, `workflow.py`.
- Models/persistence: `AssistantRequest`/`AssistantBase`; `WorkflowConfigBase`/`WorkflowAssistant`; alembic migration.
- API/validation: assistants router (automatic), workflow router YAML rewrite, `execution_config_schema.yaml` (both validators).
- Config / deployment: `configs/config.py`, `config/customer/customer-config.yaml`, `docker-compose.yml`, `pyproject.toml`.

### Integration Points
- Sidecar vs SDK switch: `is_litellm_enabled()` in `codemie/enterprise/litellm/dependencies.py:44` = `HAS_LITELLM` (enterprise package) AND `config.LLM_PROXY_ENABLED` (config.py:700, default False). Imported lazily elsewhere (`from codemie.enterprise.litellm import is_litellm_enabled`).
- Prompt caching: `core/dependecies.py:329-348` appends `langchain_anthropic_smart_cache.SmartCacheCallbackHandler` to Claude LLM callbacks (unless `get_disable_prompt_cache()`); it adds `cache_control` at request time (callback on chat-model start), i.e. AFTER pre_model_hooks. No `cache_control` in `additional_kwargs` is set anywhere in `src/` (grep: none).
- OTel: `opentelemetry-api` ^1.41.1 and httpx instrumentation already deps.

### Patterns and Conventions
- Optional JSONB config on model = `Optional[X] = SQLField(default=None, sa_column=Column(PydanticType(X)))`; migration adds `JSONB nullable=True` column (see below).
- Feature flags: `config/customer/customer-config.yaml` entries `- id: "features:<name>"` with `settings: {enabled, name, description}` (e.g. `features:requestHedging` l.203-208, default `enabled: false`); read with `customer_config.is_feature_enabled("<name>")` = `resolve_component("features:<name>")` → False when absent (`configs/customer_config.py:375-387`). Dynamic override registry is `service/customer_config_declarations.py` (opt-in; not required).
- Platform settings: single pydantic `Config(BaseSettings)` in `src/codemie/configs/config.py:45` (flat UPPER_CASE fields with defaults, e.g. `SUBWORKFLOW_POOL_ENABLED` l.187, `SUBWORKFLOW_POOL_MAX_SIZE` l.610, `LLM_PROXY_ENABLED` l.700, secrets as plain `str = ""` e.g. `TAVILY_API_KEY` l.362). Guide: `.ai-run/guides/development/configuration-patterns.md`.
- Hook contract: sync `(state: dict) -> dict`, return `{}` for no-op, `{"llm_input_messages": [...]}` otherwise.
- New files need the EPAM Apache license header (`scripts/license_headers/check_license_headers.py`).

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists. Relevant: `agents/langchain-agent-patterns.md`, `integration/external-services.md`, `integration/llm-providers.md`, `integration/request-hedging.md` (closest analogue for a per-assistant opt-in + feature flag), `development/configuration-patterns.md`, `data/database-patterns.md`, `testing/testing-patterns.md`, `quality-gates.md`, `security/README.md` (dependency change → `/sanity` handoff). `.ai-run/guides/integration/headroom-integration.md` does not exist (design §9 plans it plus an AGENTS.md table row).

### Architectural Decisions
Design spec `docs/superpowers/specs/2026-10-08-headroom-per-assistant-workflow-design.md` (D1-D8) and `docs/headroom/research-findings.md` (options A/B/C rejected-alternatives table; LiteLLM 1.99.2 and headroom-ai 0.40.0 source facts). Both read; code matches their line references (langgraph_agent 312/351, assistant.py 344/672, assistant_handlers 1496, workflow_models 52) except where noted in section 6.

### Derived Conventions
Alembic naming is mixed: hand-picked readable ids (`t1u2v3w4x5z7_add_sub_workflow_pool_config.py`, `r7s8t9u0v1w2_add_hedging_config_to_assistants.py`) and autogen hex (`f88ad134467d_...`). Files use `revision: str`, `down_revision: Union[str, None]`, `upgrade()/downgrade()` with `op.add_column(table, sa.Column(name, postgresql.JSONB(), nullable=True))`/`op.drop_column`.

---

## 4. Testing Landscape

### Existing Coverage
- Hook chain / agent: `tests/codemie/agents/test_image_artifact_hook.py` (imports `_compose_pre_model_hooks` from langgraph_agent, builds Human/AI/Tool messages helpers), `test_langgraph_multi_assistant_supervisor.py:208` (asserts `callable(call_kwargs["pre_model_hook"])` on supervisor build), `test_langgraph_multi_assistant_runtime.py` (patches `create_react_agent` with `pre_model_hook=...` expectations), `test_langgraph_tool_confirmation.py`, `test_langgraph_multi_assistant_handoffs.py`, `tests/codemie/service/conversation/test_history_compaction_service.py` (hook precedent).
- Service: `tests/codemie/service/test_assistant_service_workflow_mapping.py` (helper `_build_agent_for_workflow` mocks collaborators at seams — direct model for headroom workflow wiring tests), `test_assistant_service_headers.py` (build_agent/build_agent_for_workflow kwargs), `test_assistant_service_methods.py`.
- Assistant API/handlers: `tests/codemie/rest_api/routers/test_assistant.py` (+ ~12 `test_assistant_*.py` siblings), `tests/codemie/rest_api/handlers/test_assistant_handlers.py`, `test_hedged_handler.py` (fixture `mock_assistant` with `assistant.hedging_config = ...`, l.62-73 — the only test touching `hedging_config`; there is no API round-trip test for it).
- Workflow: `tests/codemie/core/workflow_models/test_workflow_config.py:253` (`test_parse_execution_config_pool_config` — template for headroom parse test), `tests/codemie/workflows/test_config_yaml_validation.py:799` (`test_pool_config_passes_schema`; helper `_validate_workflow_execution_config_schema` l.119-124, 263), `tests/codemie/rest_api/routers/test_workflow.py` (create/update; on the 15759 branch also asserts YAML stored as sent), `tests/codemie/service/test_workflow_pool.py`.
- Migrations: `tests/codemie/migrations/test_*.py` (e.g. `test_d1c2b3a4e5f6_drop_interactive_features.py`, `test_c3d4e5f6a7b8_migrate_assistant_project_mapping.py`, `test_credentialtypes_enum_migrations.py`) load the revision module directly — pattern for up/down tests.
- Fixtures: `tests/conftest.py`, `tests/codemie/conftest.py` (2 fixtures at l.46, l.65), `tests/codemie/core/workflow_models/conftest.py`. Dir `tests/codemie/service/headroom/` does not exist.

### Testing Framework and Patterns
pytest 8.x (`pytest.ini`), `unittest.mock` patches at seams, per-area directories mirroring `src/` (`.ai-run/guides/testing/testing-patterns.md`: "Seam tests" — one callsite test per branch asserting the boundary value). Tests are only run/written when requested by the user per AGENTS.md.

### Coverage Gaps
No tests for: a `headroom` module (greenfield), `_compose_pre_model_hooks` itself (only indirectly), `_rewrite_yaml_config_from_objects` key-preservation on HEAD, `execution_config_schema.yaml` `assistants[]` extra keys, `hedging_config` API round-trip, alembic up/down for column additions.

---

## 5. Configuration and Environment

### Environment Variables
None for Headroom exist. Existing relevant: `LLM_PROXY_ENABLED` (config.py:700), `ENABLE_LANGGRAPH_AITOOLS_AGENT` (l.673), `AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED` (l.775, gates history-compaction hook). `.env`, `.env.example`, `.env.local` exist at repo root.

### Configuration Files
`src/codemie/configs/config.py` (settings class), `config/customer/customer-config.yaml` (feature flags; no `headroom` entry), `litellm_config.yaml` (no guardrails block on HEAD; design says keep none), `deploy-templates/` Helm chart (`values.yaml`, `templates/`, `tests/`) — no Headroom/hedging env references found (grep -i: none).

### Feature Flags and Deployment Concerns
- `docker-compose.yml` (services: mermaid-server, codemie, postgres, elasticsearch, jaeger, kibana, litellm, pyroscope, prometheus, codemie-grafana; named volumes). Patterns: pinned image tag (`ghcr.io/berriai/litellm:1.99.2`), `environment:` map, `depends_on: {svc: {condition: service_healthy}}` (codemie → postgres, elasticsearch), `healthcheck` with `test/interval/timeout/retries/start_period` (litellm uses `wget ... /health/liveliness`), `restart: on-failure|unless-stopped`, `container_name`, host `ports`. The `codemie` service gets env via `.env` file mount + inline `environment:`; it does not currently `depends_on` litellm.
- Dependency change (`headroom-ai`) → AGENTS.md Sanity Regression rule: MR description must ask reviewer to post `/sanity`. pyproject has a `[tool...]` license allow-list block (tiktoken listed at pyproject.toml:280) — a new dep may need a license entry.
- Alembic: dir `src/external/alembic/versions/` (189 files), `alembic.ini`/`env.py` in `src/external/alembic/`; a second tree `src/external/alembic_cli_analytics` is separate (not for this change). Single current head computed from `down_revision` graph: **`f88ad134467d`** (`f88ad134467d_merge_default_project_and_zephyr_.py`, a merge of `bf3cb9db22b7` and `a7f3c9e1b4d6`, 2026-09-30). New migration must set `down_revision = "f88ad134467d"` and add `assistants.headroom_config` and `workflows.headroom_config` as nullable JSONB.

---

## 6. Risk Indicators

1. **Workflow YAML is rewritten on save (blocking for AC3-5).** `_rewrite_yaml_config_from_objects` (routers/workflow.py:128, calls l.597/646) drops every top-level key outside its 7-key set. A workflow-level `headroom:` block (and today's `pool_config`, `retry_policy`) is lost on POST/PUT, so saved workflow config would never reach `parse_execution_config`. Fix exists only on the EPMCDME-15759 branch (`1aa7cc0e9`, not an ancestor of HEAD). Plan must sequence after / include that change, with a test that `headroom:` survives a PUT.
2. **Silent drop of node-level key.** `WorkflowAssistant` ignores unknown keys; schema has no `additionalProperties`. Without a typed `headroom` field plus schema entry, invalid node blocks are accepted and discarded.
3. **Agent-class gap.** Hook exists only in `LangGraphAgent`. `AIToolsAgent` (react models via `get_react_llms()`, or `ENABLE_LANGGRAPH_AITOOLS_AGENT=false`) and Bedrock agents never compress, though config can be saved — silent no-op for AC5. Passing the new kwarg to `AIToolsAgent` raises TypeError; it must go in the LangGraph-only block (assistant_service.py l.624 / l.912).
4. **Structured output bypasses the hook.** In `create_react_agent`, `generate_structured_response` invokes the model on `state["messages"]` (langgraph chat_agent_executor.py l.744-765), not on `llm_input_messages`; that final call (workflow nodes with `output_schema`, `response_format`) is uncompressed. `langgraph_patches._patch_generate_structured_response_node` already monkeypatches this node (extension point if needed).
5. **Message shapes the hook must tolerate.** Image hook injects `HumanMessage(content=[TextContentBlock, ImageContentBlock(type="image", base64=..., mime_type=...)])` (LangChain-v1 block form, not OpenAI `image_url`); hook ordering "headroom last" means it sees these list-content messages with base64 payloads (token-heavy, mis-converted by OpenAI-dict conversion). Also `ToolMessage.artifact`, AIMessage `tool_calls`, list-content Anthropic blocks (thinking/tool_use), and `additional_kwargs` markers (`METADATA_KEY_SUBAGENT_TASK`, handoff `response_metadata`) used by `_subagent_task_pre_model_hook`/`_strip_*` hooks — design keeps originals and swaps only `content`, but non-string `content` handling is unspecified. Supervisor state contains handoff ToolMessages/AIMessages with synthetic tool calls (`history.py:272-347`).
6. **Prompt-cache interaction.** `SmartCacheCallbackHandler` places `cache_control` after hooks; `balanced`/`aggressive` rewrite earlier history each turn → cache misses on Claude models (assistants already pay for caching). `disable_cache`/`get_disable_prompt_cache` is a ContextVar, unrelated to Headroom.
7. **Chain divergence.** Supervisor chain lacks history-compaction and image hooks; "append last" there means Headroom sees different inputs than single-agent. `_compose_pre_model_hooks` returns `None` when all hooks are `None`; Headroom hook with `None` config must stay `None` to preserve AC6 byte-identical behavior (test asserting identical args to `create_smart_react_agent`/`create_supervisor`).
8. **Sync I/O in graph node.** Hook is sync; sidecar uses blocking httpx (`HEADROOM_TIMEOUT_SECONDS`=10 worst case added per LLM call before fail-open). The SDK is sync with Rust core and ~15 MB wheel; Linux wheel/offline behavior unverified (spike items 1-4 in design §7).
9. **Scope propagation ambiguity.** Workflow/node overrides apply only to the node's own `LangGraphAgent`; sub-assistants of a workflow-node assistant resolve from their own `headroom_config` only (design silent). A2A *remote* assistants and Bedrock ignore config. Builtin subagent clones inherit parent config via `model_copy`.
10. **Versioning/rollback.** If `headroom_config` is not added to `AssistantConfiguration`/`AssistantVersionService`, rollback and `sub_assistants_versions` pinning ignore it (same as `hedging_config`); a version-aware design would touch ~4 places in `assistant_version_service.py`.
11. **Tests mock assistants with `Mock(spec=...)`.** `LangGraphAssistantBuilder` comments warn that unset attrs return truthy Mocks; `assistant.headroom_config` read in `build_agent` must be robust (`getattr(..., None)` + isinstance) or many existing tests (`test_assistant_service_*`) will break.
12. **Dependency/ops.** `headroom-ai` pins `litellm>=1.96.2,<2.0` (compatible with 1.99.2 but another transitive litellm consumer); poetry.lock churn and license allow-list; `/sanity` handoff required. Sidecar image `ghcr.io/headroomlabs-ai/headroom:0.40.0` not inspected; the prototype 0.39.1 image never inspected (research-findings §header).
13. **Speculative:** two schema validators (`config_yaml_validation.py` and `validation/schema.py`) share one YAML, so one schema edit covers both, but tests exist only for the former (`tests/codemie/workflows/test_config_yaml_validation.py`).
14. **Speculative:** adding `headroom_config` column to `WorkflowConfigBase` also adds it to `WorkflowConfigTemplate` (non-table model) and workflow list/response models that reuse the base — response payload shape change for all workflow GETs.

---

## 7. Summary for Complexity Assessment

The change touches five layers: agent runtime (two hook-chain edit points in `langgraph_agent.py`, one new `HeadroomConfig` kwarg on `LangGraphAgent.__init__`), a new self-contained `service/headroom/` package (~8 modules, no existing code), service wiring (`build_agent`, `build_agent_for_workflow`, `AssistantExecutionOptions` plus three call sites), persistence (two JSONB columns, one alembic migration on head `f88ad134467d`, `AssistantRequest`/`AssistantBase`/`WorkflowConfigBase`/`WorkflowAssistant` fields), and config/deployment (Config settings, customer-config flag, docker-compose service, pyproject dependency, two docs). Assistant create/update/get ride existing reflection-based mapping (`_map_assistant_request`, `Assistant(**request.model_dump())`, `response_model=Assistant`), so API code changes are near zero; list/copy/export need no change. Sub-assistants and builtin subagents flow through `AssistantService.build_agent`, so resolving there gives per-agent config for free.

Technical novelty is moderate: no prior external compression integration, a sync network call and optional native SDK inside a LangGraph node, and two runtime modes. The hook pattern itself has direct precedents (history compaction, image artifacts) and the persistence patterns copy `hedging_config`/`pool_config`. Existing test seams are good (hook-chain tests, `test_assistant_service_workflow_mapping.py` helper, config/schema/migration test templates), but the whole headroom area is greenfield with no tests, and the new tests span ~9 areas listed in the design §8.

Main risks: (1) the workflow YAML rewrite on HEAD silently drops workflow-level `headroom:` — the 15759 fix is not on this branch and is effectively a prerequisite; (2) hook coverage gaps (AIToolsAgent, Bedrock, A2A-remote, structured-output final call) that make saved config a silent no-op in some runs; (3) message-shape edge cases (image blocks, list content, handoff metadata) and Claude prompt-cache effects; (4) unresolved spike items (Linux wheel, sidecar lossless mode, offline SDK) and the `/sanity` dependency handoff. Expect a medium-to-large band driven by breadth rather than depth.

---

## 8. External References

None named by the task as external; the two repo-local artifacts it named were read in full: `/Users/Sviatoslav_Likhtarchyk/mdtu_gpt/code-assistant/docs/superpowers/specs/2026-10-08-headroom-per-assistant-workflow-design.md` (decisions D1-D8, config model §1, resolver §2, runtime/hook behaviour §3, failure handling §4, settings §5 incl. `HEADROOM_ENABLED/SIDECAR_URL/PROXY_TOKEN/TIMEOUT_SECONDS/MIN_TOKENS/AGGRESSIVE_TARGET_RATIO`, spike §7, tests §8, UI/SDK contract appendix) and `/Users/Sviatoslav_Likhtarchyk/mdtu_gpt/code-assistant/docs/headroom/research-findings.md` (LiteLLM 1.99.2 guardrail limits, headroom-ai 0.40.0 SDK `compress()`/`CompressConfig`, server `/v1/compress` `config.mode` values, options table, open items 1-4). Both resolved. Their line refs were checked against HEAD and match, except the workflow-save behaviour described in Risk 1, which the design does not mention.
