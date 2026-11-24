# Headroom per assistant (slice 2: assistant scope) - Implementation Plan

**Goal:** store `headroom_config` on an assistant and compress its LangGraph model calls via the slice 1 hook; unconfigured assistants behave exactly as today.
**Spec:** `spec.md` (A1-A8). **Research:** `technical-analysis.md`.
**Commits:** commit per task using the repository's existing convention (`EPMCDME-15645: Description`). Each task leaves the repo importable with green tests.
**Tests:** mirror `src` under `tests/codemie/`, `unittest.mock`, no DB. Import slice 1 symbols from submodules (`codemie.service.headroom.models|resolver|hook`); the package `__init__` re-exports nothing. Slice 1 package code is not modified.

Naming trap: the assistant field `headroom_config` is `HeadroomConfig | None` (stored); the `LangGraphAgent` kwarg `headroom_config` is `ResolvedHeadroomConfig | None` (resolved).

---

### Task 1: Model fields

**Files:** modify `src/codemie/rest_api/models/assistant.py`; create `tests/codemie/rest_api/models/test_assistant_headroom_config.py`.

- Add `headroom_config: Optional[HeadroomConfig] = None` to `AssistantRequest` right after `hedging_config` (~line 344), and `headroom_config: Optional[HeadroomConfig] = SQLField(default=None, sa_column=Column(PydanticType(HeadroomConfig)))` to `AssistantBase` right after `hedging_config` (~line 672). Import `HeadroomConfig` from `codemie.service.headroom.models`.
- Do not touch `AssistantConfiguration`, versioning services, list responses or routers (create/update/get/copy are reflection-based).
- Tests (template `test_assistant_interactive_enabled.py`): request default `None`; accepts dict and `HeadroomConfig`; `model_fields_set` set only when supplied; `Assistant._map_assistant_request` applies it; `Assistant(**assistant.model_dump())` round-trips; `AssistantBase.model_fields["headroom_config"].default is None`; invalid `strategy` raises `ValidationError` on `AssistantRequest` (FastAPI maps that to 422, A3).

**Test-first: yes** - `AssistantRequest(headroom_config={"enabled": True, "strategy": "balanced"})` fails today (field ignored/absent) and a bad `strategy` does not raise.

---

### Task 2: Alembic migration

**Files:** create `src/external/alembic/versions/<new 12-hex>_add_headroom_config_to_assistants.py`; create `tests/codemie/migrations/test_add_headroom_config_to_assistants.py`.

- Immediately before writing, re-verify the single head with `ScriptDirectory.get_heads()` (2026-10-09: `['f88ad134467d']`); set `down_revision` to the verified head.
- Copy the shape of `r7s8t9u0v1w2_add_hedging_config_to_assistants.py`: `op.add_column('assistants', sa.Column('headroom_config', postgresql.JSONB(), nullable=True))` in `upgrade()`, `op.drop_column('assistants', 'headroom_config')` in `downgrade()`. Include the Apache licence header used by newer migration files.
- Test: load the revision module by file path, assert `down_revision` equals the head recorded at write time, patch `op` and assert `add_column`/`drop_column` call args (table `assistants`, nullable JSONB column); assert via `ScriptDirectory` that the script directory has exactly one head and it is this revision (A1).

**Test-first: yes** - the head assertion fails (head is still `f88ad134467d`) and the module does not exist before the migration is added.

---

### Task 3: LangGraphAgent kwarg and hook chains

**Files:** modify `src/codemie/agents/langgraph_agent.py`; create `tests/codemie/agents/test_langgraph_headroom_hook.py`.

- `__init__` (signature ends at `is_subagent` line 167): add `headroom_config: ResolvedHeadroomConfig | None = None`. Next to the `history_compaction_pre_model_hook` assignment (lines 198-205), set `self.headroom_pre_model_hook = build_headroom_pre_model_hook(headroom_config, llm_model)`. It must be assigned before `self.init_agent()` (line 234).
- `_build_single_agent` (`_compose_pre_model_hooks` at ~351-357): append `self.headroom_pre_model_hook` as the LAST argument. `_build_supervisor_agent` (~312-315): append it last after `_strip_subagent_task_messages_pre_model_hook`. `_compose_pre_model_hooks` already drops `None`, so unconfigured chains are unchanged.
- Imports: `ResolvedHeadroomConfig` from `codemie.service.headroom.models`, `build_headroom_pre_model_hook` from `codemie.service.headroom.hook`.
- Tests (template: `test_langgraph_multi_assistant_supervisor.py:195-208`, patch `create_smart_react_agent` / `create_supervisor`, inspect `call_args.kwargs["pre_model_hook"]`): with a `ResolvedHeadroomConfig` and `build_headroom_pre_model_hook` patched to a sentinel hook, the composed hook runs the sentinel after the earlier hooks (single agent and supervisor); with no `headroom_config` the builder is called with `None` and the chain output equals today's (A4).

**Test-first: yes** - the composed hook for a configured agent never invokes the headroom hook (kwarg rejected by `__init__` today).

---

### Task 4: build_agent wiring

**Files:** modify `src/codemie/service/assistant_service.py`; create `tests/codemie/service/test_assistant_service_headroom.py`.

- In `build_agent`, inside the existing `if agent_class == LangGraphAgent:` block (line 624, after `agent_kwargs["is_subagent"] = is_subagent`): read `stored = getattr(assistant, "headroom_config", None)`; accept only if `isinstance(stored, HeadroomConfig)`; `resolved = resolve(None, None, stored)` (from `codemie.service.headroom.resolver`); `if resolved is not None: agent_kwargs["headroom_config"] = resolved`. A non-`HeadroomConfig` value (e.g. a `Mock`) is treated as `None`. Prefer a small private helper on `AssistantService` to keep `build_agent` short.
- No handler gate. Do not change the `AIToolsAgent` path, `_build_bedrock_agent` / its early return, or `build_agent_for_workflow` (line 912 block).
- Tests (templates: `test_assistant_service_headers.py`, `test_assistant_service_build_agent_deleted_datasource.py`; patch `codemie.service.assistant_service.LangGraphAgent`/`AIToolsAgent` and `codemie.service.headroom.resolver.config`/`customer_config`): resolved config reaches `LangGraphAgent` kwargs as the `ResolvedHeadroomConfig` (A5); `AIToolsAgent` kwargs never contain the key; gate off / kill switch off / `headroom_config=None` leave kwargs with no `headroom_config` key (A6); `Mock(spec=Assistant)` works both with default gates and with both gates on (A7); Bedrock path never receives it.

**Test-first: yes** - `LangGraphAgent` is constructed without `headroom_config` for an enabled stored config today.

---

### Task 5: Integration guide and AGENTS.md row

**Files:** create `.ai-run/guides/integration/headroom-integration.md`; modify `AGENTS.md`.

- Guide (style of `integration/faq-git-integration.md`): architecture block (stored `HeadroomConfig` -> `resolve` -> `LangGraphAgent` hook last in chain); settings (`HEADROOM_*` from `config.py:613-623`, `features:headroom` in `customer-config.yaml`); precedence (node > workflow > assistant, only assistant active in this slice); deployment (migration before app, sidecar service in `docker-compose.yml`, `/sanity` for dependency changes); strategy meanings incl. prompt-cache trade-off; how to add a strategy (`strategies.py` is the only place strategy values live); known limitations from the spec; API contract handoff to UI/SDK (design Appendix A: `headroom_config` on `POST/PUT /v1/assistants` and `GET`, `null`/absent = not configured, invalid strategy = 422).
- AGENTS.md: insert `| Headroom | \`.ai-run/guides/integration/headroom-integration.md\` | <short purpose> |` immediately after the `FAQ Git` row (line 44).

**Test-first: no** - documentation only.

---

## Negative-constraint pass

- No workflow/node config, YAML, `execution_config_schema.yaml`, `AssistantExecutionOptions`, `build_agent_for_workflow`: no task touches them (Task 4 explicitly leaves the line 912 block alone).
- No AIToolsAgent / Bedrock / remote A2A support: Task 4 adds the key only inside the LangGraph block; Bedrock early return untouched; A2A never reaches `build_agent`.
- No versioning of `headroom_config`: Task 1 leaves `AssistantConfiguration` and version services alone; limitation stated in Task 5.
- No change to routers, handlers, list responses or slice 1 package: no task edits them; gating stays inside `resolve()`.
- No structured-output compression, no prompt-cache mitigation: none planned.
- Kwarg only when resolved non-None: Task 4. No whole-suite gate, lint, manual verification or commit tasks: none planned.
