# Technical Research

**Task**: headroom assistant headroom_config langgraph hook build_agent migration (EPMCDME-15645 slice 2)
**Generated**: 2026-10-09
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-15645 slice 2 (assistant-scope): per-assistant Headroom context-compression configuration and agent wiring in the CodeMie backend. Slice 1 (the self-contained src/codemie/service/headroom/ package: models HeadroomConfig/HeadroomOverride/ResolvedHeadroomConfig, resolver.resolve(node, workflow, assistant) -> ResolvedHeadroomConfig | None, strategies, compressors, factory, fail-open hook builder build_headroom_pre_model_hook(cfg, llm_model), settings, features:headroom flag) is already on this branch (stacked, not merged). Slice 2 must: (a) add assistants.headroom_config JSONB column + alembic migration (down_revision is the single head; verify the current head, which may have moved since f88ad134467d), (b) add headroom_config: Optional[HeadroomConfig] to AssistantRequest and AssistantBase following hedging_config (src/codemie/rest_api/models/assistant.py ~344 and ~672, handlers/assistant_handlers.py ~1496), (c) add a headroom_config kwarg to LangGraphAgent and append the slice 1 hook LAST in the _compose_pre_model_hooks chains of _build_single_agent (~line 351) and the supervisor graph (~line 312) in src/codemie/agents/langgraph_agent.py, (d) resolve with resolve(None, None, assistant.headroom_config) in AssistantService.build_agent (src/codemie/service/assistant_service.py ~503) and pass it only on the LangGraph-only branch (never to AIToolsAgent, Bedrock or remote A2A), (e) behave identically when headroom_config is None/flag off/kill switch off (AC6), read assistant.headroom_config defensively because many tests use Mock(spec=...) assistants, (f) add tests, and (g) write .ai-run/guides/integration/headroom-integration.md plus an AGENTS.md Guide Imports table row. Out of scope: workflow/node config, workflow YAML and AssistantExecutionOptions (slice 3), UI and CodeMie SDK repos.

(Focus list from the caller: exact slice 1 signatures/behaviour, LangGraphAgent.__init__ placement, every LangGraphAgent/AIToolsAgent construction site, supervisor/sub-assistant hook flow, create/update/get/list/copy/export/version paths, alembic head and migration tests, Mock(spec=Assistant) fixtures, AGENTS.md table, UI-contract docs, risks.)

---

## 2. Codebase Findings

### Existing Implementations

Slice 1, verified in `src/codemie/service/headroom/` (634 lines, 9 modules):
- `models.py`: `HeadroomConfig(BaseModel)` fields `enabled=False, compress_user_messages=True, compress_tool_outputs=True, strategy=HeadroomStrategy.BALANCED`; `HeadroomOverride` (all `None`); `@dataclass(frozen=True) ResolvedHeadroomConfig(compress_user_messages, compress_tool_outputs, strategy)` (no `enabled` field). `HeadroomStrategy` is `str, Enum` (`lossless|balanced|aggressive`).
- `resolver.py`: `resolve(node, workflow, assistant) -> ResolvedHeadroomConfig | None`. Returns `None` first if `not config.HEADROOM_ENABLED or not customer_config.is_feature_enabled("headroom")` (both read at call time, from `codemie.service.headroom.resolver.config/customer_config`, not from `assistant_service.config`). Per-field first-non-None over (node, workflow, assistant) via `getattr(layer, field, None)`, then `HeadroomConfig()` defaults; `None` when resolved `enabled` is false. With the gates off it never touches the `assistant` argument.
- `hook.py`: `build_headroom_pre_model_hook(cfg: ResolvedHeadroomConfig | None, llm_model: str) -> Callable[[dict], dict] | None`. Returns `None` when `cfg is None`; otherwise a closure that never raises (returns `{}` on any failure) and returns `{"llm_input_messages": [...]}` only when something changed. Skips below `HEADROOM_MIN_TOKENS`. Opens span `headroom.compress`, logs one info line, content-free.
- `factory.py` (`get_compressor`, lru_cached; sidecar if `is_litellm_enabled()` else SDK), `sidecar_compressor.py`, `sdk_compressor.py`, `strategies.py`, `compressor.py`, `__init__.py` (docstring only, no re-exports; import from submodules).
- Settings in `src/codemie/configs/config.py:613-623`: `HEADROOM_ENABLED=False` (kill switch), `HEADROOM_SIDECAR_URL`, `HEADROOM_PROXY_TOKEN`, `HEADROOM_TIMEOUT_SECONDS=10`, `HEADROOM_MIN_TOKENS=1000`, `HEADROOM_AGGRESSIVE_TARGET_RATIO=0.2`. Flag `features:headroom` is `enabled: false` in `config/customer/customer-config.yaml:332`. `headroom-ai = "0.40.0"` in pyproject; `headroom` service in `docker-compose.yml:154`.

Assistant model (`src/codemie/rest_api/models/assistant.py`):
- `AssistantRequest` (line 312): `hedging_config: Optional[HedgingConfig] = None` at line 344 (plain pydantic).
- `AssistantBase` (642): `hedging_config: Optional[HedgingConfig] = SQLField(default=None, sa_column=Column(PydanticType(HedgingConfig)))` at line 672. `PydanticType` (`models/base.py:242`) is a JSONB `TypeDecorator`. `Assistant(table=True)` at 1076 inherits it; `VirtualAssistant`/`VirtualIdeAssistant`/prebuilt models also inherit `AssistantBase`, so they gain the field with no extra work.
- Verified empirically: the `Assistant` table-model constructor validates, so a dict passed as a `PydanticType` field is coerced/validated (a bad `hedging_config` dict raised `ValidationError`). `Assistant(**assistant.model_dump())` round-trips safely.

### Architecture and Layers Affected

Models (AssistantRequest, AssistantBase) -> DB migration (alembic) -> agent runtime (`LangGraphAgent`) -> service (`AssistantService.build_agent`) -> docs (guide, AGENTS.md). No router change needed.

Persistence paths (all pick the field up without code, as `hedging_config` does):
- Create: `routers/assistant.py:749 _build_assistant_from_create_request` does `Assistant(**request.model_dump(exclude={...}))`; the new field flows through. Clone/copy goes through the same create request (`source_assistant_id` is excluded, the rest is copied), so the config is copied by the client payload.
- Update: `Assistant._map_assistant_request` (`assistant.py:886`) iterates `request.model_fields` by reflection and applies fields in `model_fields_set`. Nothing to add.
- Get: `response_model=Assistant` (`routers/assistant.py:371,385`), so `headroom_config` is returned automatically. List uses `AssistantListResponse` (line 583), which has no `hedging_config` either; no change.
- Versioning: `AssistantConfiguration` (line 1244) and `assistant_version_service.py` (lines 61, 119, 284, 330) and `assistant_version_compare_service.py:61-` carry only versioned fields; `hedging_config` is not among them. `apply_version_to_assistant` (line 374) rebuilds from the master dump, so `headroom_config` stays at its master value. A headroom-only update will not create a new version (same as hedging). Leave versioning alone.
- Export/import: no explicit field lists reference `hedging_config`; none needed.

### Integration Points

`LangGraphAgent` (`src/codemie/agents/langgraph_agent.py`):
- `__init__` (line 136) is keyword-only-style with defaults; last param `is_subagent: bool = False` (line 169). `history_compaction_pre_model_hook` is set at lines 198-205, `self.is_subagent` at ~211, and `self.agent_executor = self.init_agent()` at line 234. Any new attribute must be assigned before line 234 (init_agent -> `_build_*` reads it).
- `_build_supervisor_agent` (line 291): `create_supervisor(... pre_model_hook=_compose_pre_model_hooks(_strip_handoff_back_messages_pre_model_hook, _strip_subagent_task_messages_pre_model_hook))` at lines 312-315. Currently has no history-compaction hook.
- `_build_single_agent` (line 340): `create_smart_react_agent(... pre_model_hook=_compose_pre_model_hooks(self.history_compaction_pre_model_hook, _subagent_task_pre_model_hook, _image_artifact_pre_model_hook))` at lines 351-357.
- `_compose_pre_model_hooks` (`agents/supervisor/pre_model_hooks.py:47`) drops `None` hooks, returns `None` if none remain, and feeds each hook's `llm_input_messages` into the next hook's state. So a trailing headroom hook sees the output of the earlier hooks, and a `None` headroom hook leaves the chain identical (AC6).
- Supervisor `pre_model_hook` only covers the supervisor's own LLM calls.

`AssistantService` (`src/codemie/service/assistant_service.py`) construction sites:
- `_build_bedrock_agent` (line 325): `AIToolsAgent`, reached by the early return at ~line 541 in `build_agent`. Do not touch.
- `build_agent` (line 503): class chosen at line 601 `LangGraphAgent if config.ENABLE_LANGGRAPH_AITOOLS_AGENT and not is_react else AIToolsAgent`; common `agent_kwargs` dict 604-621; `if agent_class == LangGraphAgent:` block at 624 calls `_configure_langgraph_agent(...)` and sets `agent_kwargs["is_subagent"]`. This block is the only place for the headroom kwarg.
- `build_agent_for_workflow` (line 785): own `agent_kwargs` and an `if agent_class == LangGraphAgent:` block at line 912. Slice 3; leave unchanged (it passes no `headroom_config`, so workflow nodes stay uncompressed).
- Remote A2A assistants never reach `build_agent`: `get_request_handler` (`assistant_handlers.py:1493`) returns `A2AAssistantHandler` first. Nothing to guard beyond the Bedrock early return.
- Callers of `build_agent` (all inherit the change): `assistant_handlers.py` (645, 1128, 1189, 1290), `hedged_handler.py:485`, `rest_api/a2a/server/codemie_task_manager.py:162` (served-via-A2A local assistants), `assistant_health_check_service.py:305`, `service/tools/assistant_factory.py:101`.
- Sub-assistants: `LangGraphAssistantBuilder.create_subagent_executors` (`service/assistant/assistant_engine_builder.py`) -> `create_assistant_executors` / `AssistantFactory.build()` -> `AssistantService.build_agent(is_subagent=True)` -> each sub-assistant is its own `LangGraphAgent`; `agent.agent_executor` is used as the subgraph. So each sub-assistant resolves its own `headroom_config` automatically. `AssistantFactory.build` has a version-pinning step that goes through `apply_version_to_assistant` (field preserved).
- Builtin subagent clones (`_build_builtin_subagent_executor`, `parent.model_copy(deep=False)`) copy the parent's `headroom_config` by value, so they compress per the parent's config with no code change. In a workflow, a node assistant's own sub-assistants will also pick up their saved assistant-level config even though the node itself is not wired (slice 3).

### Patterns and Conventions

- `hedging_config` is the template: same type pattern on both models; "stored config is kept while the flag is off, ignored at runtime" (spec section 2). The gate for hedging lives in `get_request_handler` (`assistant_handlers.py:1496`), not in the model. Headroom does not need a handler change: gating is inside `resolve()`.
- Spec decision (design doc section 3 Wiring): `LangGraphAgent.__init__` takes `headroom_config: ResolvedHeadroomConfig | None = None` (the resolved type, not `HeadroomConfig`) and builds the hook once with `build_headroom_pre_model_hook(headroom_config, llm_model)`.
- `LangGraphAssistantBuilder` already carries explicit "Mock(spec=...) returns Mock" tolerance helpers (`_coerce_list`, `_safe_get_runtime_attr`); that is the precedent for defensive reads.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/` exists. Relevant: `architecture/layered-architecture.md`, `agents/langchain-agent-patterns.md`, `data/database-patterns.md` (Alembic under `src/external/alembic`, "inspect heads before changing the chain"), `api/rest-api-patterns.md`, `testing/testing-patterns.md`, `integration/request-hedging.md` (nearest precedent for a per-assistant JSON config guide; note it is not in the AGENTS.md table).
- `AGENTS.md` Guide Imports table (rows are `| Name | \`path\` | purpose |`). Integration rows run lines 36-44; the last is `| FAQ Git | \`.ai-run/guides/integration/faq-git-integration.md\` | ... |` at line 44, immediately before `| Code Quality |`. New row goes after FAQ Git: `| Headroom | \`.ai-run/guides/integration/headroom-integration.md\` | ... |`. `CLAUDE.md` only imports `AGENTS.md`; no other mirror of the table found. The new guide file does not exist yet.
- Existing guide style example: `integration/faq-git-integration.md` (title, architecture block, contracts).

### Architectural Decisions

- `docs/superpowers/specs/2026-10-08-headroom-per-assistant-workflow-design.md`: D4 hook appended last; D5 precedence node > workflow > assistant; D7 fail-open; section 9 lists the guide contents (architecture, settings, precedence, deployment, how to add a strategy, user-facing meaning of each toggle and strategy). Appendix A is the UI/SDK contract: `headroom_config` object on `POST/PUT /v1/assistants` and `GET`; `null`/absent means not configured.
- `docs/headroom/research-findings.md`: sidecar-vs-SDK rationale.

### Derived Conventions

Name migrations `{12-hex}_{slug}.py` with `revision: str`, `down_revision: Union[str, None]`, `upgrade()`/`downgrade()`, and the Apache licence header in newer files; `op.add_column('assistants', sa.Column(..., postgresql.JSONB(), nullable=True))` as in `r7s8t9u0v1w2_add_hedging_config_to_assistants.py`.

---

## 4. Testing Landscape

### Existing Coverage

- Slice 1: `tests/codemie/service/headroom/` (resolver, hook, models, factory, strategies, both compressors) and `tests/codemie/configs/test_headroom_config.py`. `test_resolver.py` patches `codemie.service.headroom.resolver.config` and `.customer_config` in an autouse fixture.
- Agent: `tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py:195-208` constructs a real `LangGraphAgent(**config)` with `create_supervisor` patched and asserts `callable(call_kwargs["pre_model_hook"])`; `test_smart_tool_selection.py:394` and its siblings are placeholders. `test_image_artifact_hook.py` covers `_compose_pre_model_hooks` ordering (line 193). Template for "hook last" tests: patch `create_smart_react_agent` / `create_supervisor` and inspect `call_args.kwargs["pre_model_hook"]`.
- Service: `tests/codemie/service/test_assistant_service_headers.py` and `test_assistant_service_build_agent_deleted_datasource.py` patch `AIToolsAgent`/`LangGraphAgent` and use `Mock(spec=Assistant)`. `test_assistant_service_methods.py:80,126` read `mock_aitools_agent.call_args[1]`.
- Models: `tests/codemie/rest_api/models/test_assistant_interactive_enabled.py` is the exact template for request-field, `model_fields_set`, `_map_assistant_request` round trip, and `AssistantBase.model_fields[...]` default tests.
- Migrations: `tests/codemie/migrations/` has data-transform tests that import helpers from `external.alembic.versions.<id>_<slug>` (e.g. `test_k5l6m7n8o9p0_...`) and `test_credentialtypes_enum_migrations.py` which uses `alembic.script.ScriptDirectory` for chain assertions; `tests/codemie/repository/cli_analytics/postgres/test_migrations.py` also exists. No test enforces a single head today.

### Testing Framework and Patterns

pytest with `unittest.mock` (`patch` decorators, `Mock(spec=Assistant)`, `MagicMock`); no DB for unit tests. Tests only run when asked (AGENTS.md rule); commands come from `.ai-run/guides/quality-gates.md`.

### Coverage Gaps

No test for: `headroom_config` on request/model; headroom hook in either chain; `build_agent` passing/omitting the kwarg; the migration file. `build_agent` callers using `Mock(spec=Assistant)`: ~178 occurrences of `spec=Assistant` across tests (service, handlers, workflows).

Fixture impact (verified in venv): before the column exists, `Mock(spec=Assistant).headroom_config` raises `AttributeError`; after it exists it returns a plain `Mock`. So code using `getattr(assistant, "headroom_config", None)` works both ways. Because `resolve()` returns `None` before reading the layer when `HEADROOM_ENABLED` is False (default) the Mock never reaches the resolver in existing tests. If any test sets the gates on, a Mock would pass through `getattr(layer, "enabled")` as truthy; an `isinstance(value, HeadroomConfig)` guard in `build_agent` avoids that. Tests that assert exact `LangGraphAgent` kwargs: none found (`LangGraphAgent` is patched but only `call_args` of `get_tools` is inspected). Tests that bypass `__init__` (`object.__new__`/`__new__`: `test_langgraph_tool_confirmation.py:36`, `test_langgraph_truncation.py:45`, `test_mcp_auth_required_propagation.py:68`, `test_langgraph_agent_initialize_llm.py:40`) either patch `init_agent` or never call `_build_*`, so a new attribute read only inside `_build_*` will not break them (they set `history_compaction_pre_model_hook = None` by hand; the new attribute is not needed there).

---

## 5. Configuration and Environment

### Environment Variables

`HEADROOM_ENABLED`, `HEADROOM_SIDECAR_URL`, `HEADROOM_PROXY_TOKEN` (secret), `HEADROOM_TIMEOUT_SECONDS`, `HEADROOM_MIN_TOKENS`, `HEADROOM_AGGRESSIVE_TARGET_RATIO` (all from slice 1, `config.py:613-623`; docker-compose passes `HEADROOM_PROXY_TOKEN`). Slice 2 adds none.

### Configuration Files

- Alembic: `src/external/alembic/` (`alembic.ini`, `env.py`, `versions/`).
- **Alembic head verified with `ScriptDirectory.get_heads()` on 2026-10-09: `['f88ad134467d']`** (`f88ad134467d_merge_default_project_and_zephyr_.py`, a no-op merge of `bf3cb9db22b7` and `a7f3c9e1b4d6`). `origin/main` has the same 188 version files and no diff in `versions/`, so the head has not moved. Re-run before writing the migration; if main advances first, re-parent.
- Customer flag `features:headroom` and `config/customer/customer-config.yaml` already exist.

### Feature Flags and Deployment Concerns

Two gates, both inside `resolve()`: `config.HEADROOM_ENABLED` and `customer_config.is_feature_enabled("headroom")`. Migration must run on deploy; the column is nullable JSONB so old rows read as `None`. Rolling deploy: an old pod ignores the new column; a new pod against an un-migrated DB would fail on SELECT, which is the normal alembic-before-app ordering used for `hedging_config`.

---

## 6. Risk Indicators

- Alembic head is a single revision today, but any other branch merging a migration first creates two heads. Re-check immediately before writing and before merge.
- `AssistantBase` is shared by `Assistant`, virtual assistants and prebuilt models; the new column appears in every `model_dump()` (e.g. `apply_version_to_assistant`, list/response wrappers). Low risk, but any snapshot/golden test of an assistant dump would change. Not found in a grep, not exhaustively tested.
- Naming collision: assistant field `headroom_config: HeadroomConfig | None` vs the `LangGraphAgent` kwarg `headroom_config: ResolvedHeadroomConfig | None`. Same name, different types; easy to pass the wrong one. Spec fixes the kwarg to the resolved type.
- Supervisor chain: the hook compresses only the supervisor's own calls; sub-assistants compress only if they have their own config. A compressed supervisor and uncompressed sub-assistants (or the reverse) is expected behaviour but worth documenting in the guide.
- Builtin subagent clones inherit the parent's `headroom_config` through `model_copy`; a workflow node's sub-assistants will compress via their saved config before slice 3 wires node-level resolution. Behavioural, not a bug, but must be stated in the guide.
- A2A server path (`codemie_task_manager.py:162`) and the health check (`assistant_health_check_service.py:305`) call `build_agent` and will compress when enabled. Health check compression would add sidecar latency to checks.
- Mock-assistant tests: safe with default settings (see section 4), but an unguarded `resolve(None, None, assistant.headroom_config)` would receive a `Mock` if a test enables the gates. Use a defensive read plus `isinstance` check.
- Speculative: add `headroom_config` to the LangGraph kwargs only when the resolved value is not `None`, so the kwargs seen by existing tests and by mocks stay byte-identical (AC6); alternatively always pass it and rely on the `None` default.
- Speculative: a migration test would import the revision module and assert `down_revision == "f88ad134467d"` plus `upgrade`/`downgrade` call `op.add_column`/`op.drop_column` on `assistants`; the existing migration tests are transform-oriented, so there is no ready template for a pure add-column test.
- Hook is blocking and sync inside the LangGraph node (sidecar HTTP, 10 s default timeout) on every model call; fail-open but latency adds up; the 1000-token floor is the only guard.
- No UI-contract doc exists in the repo for `AssistantRequest` (FastAPI OpenAPI is generated); no checked-in schema file mentions `interactive_enabled`/`hedging_config`. The contract lives only in design Appendix A and the new guide.

---

## 7. Summary for Complexity Assessment

Layers touched: Pydantic/SQLModel models (two one-line field additions in `assistant.py`), one Alembic migration (single nullable JSONB column, mirrors `r7s8t9u0v1w2`), the `LangGraphAgent` runtime (one `__init__` kwarg, one attribute built before `init_agent()`, two `_compose_pre_model_hooks` call sites), one block in `AssistantService.build_agent`, and docs (new guide, one AGENTS.md row). Roughly 5 source files plus 1 migration and 2 doc files; all create/update/get/clone paths are reflection-based and need no code. Versioning, list responses, routers and workflow code stay untouched.

Technical novelty is low: every pattern has a direct precedent (`hedging_config` for model/migration/storage, `history_compaction_pre_model_hook` for a conditional hook, `is_subagent` for a build_agent kwarg). The slice 1 API (`resolve`, `build_headroom_pre_model_hook`) matches the task description exactly. The only design-sensitive points are placing the attribute before `init_agent()`, passing the resolved (not stored) type, and keeping the non-LangGraph branches untouched.

Test posture: strong slice 1 coverage and clear templates for each new test (interactive_enabled model tests, supervisor `create_supervisor` patching, assistant-service `Mock(spec=Assistant)` patching). Existing mock-based fixtures stay safe with default settings. Key risks are alembic head drift, the type-name collision, and behavioural side effects on sub-assistants, builtin clones, A2A server and health-check paths that call `build_agent`.

---

## 8. External References

None named by the task. (The task's "read first" documents are in-repo and were read: `docs/superpowers/specs/2026-10-08-headroom-per-assistant-workflow-design.md`, `docs/headroom/research-findings.md`, slice 1's `technical-analysis.md` (selectively), and `src/codemie/service/headroom/*`.)
