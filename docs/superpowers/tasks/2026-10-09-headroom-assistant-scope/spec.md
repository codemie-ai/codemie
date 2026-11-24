# Headroom per assistant (slice 2: assistant scope) - Spec

Ticket: EPMCDME-15645, slice 2 of 3. Size M (18/36). Branch `EPMCDME-15645_headroom-assistant-scope`, stacked on
`EPMCDME-15645_headroom-per-assistant-workflow` (slice 1, draft MR !4461); its MR targets the slice 1 branch until !4461 merges.
Full design (D1-D8, Appendix A API contract): `docs/superpowers/specs/2026-10-08-headroom-per-assistant-workflow-design.md`.
Slice 1 spec: `docs/superpowers/tasks/2026-10-08-headroom-per-assistant-workflow/spec.md`.

## Goal

A user can store a Headroom config on an assistant, and when that assistant runs on the LangGraph agent its model calls are
compressed by the slice 1 hook. Assistants without a config behave exactly as today.

## Behavior

1. **Storage.** `assistants.headroom_config` is a nullable JSONB column added by one new alembic migration, modelled on
   `r7s8t9u0v1w2_add_hedging_config_to_assistants.py`. `down_revision` is the live single head (`f88ad134467d` on 2026-10-09);
   re-verify with `ScriptDirectory.get_heads()` immediately before writing and again before merge.
2. **API model.** `headroom_config: Optional[HeadroomConfig] = None` on `AssistantRequest` (next to `hedging_config`, ~line 344) and on
   `AssistantBase` as `SQLField(default=None, sa_column=Column(PydanticType(HeadroomConfig)))` (~line 672). Create, update, get and
   copy paths are reflection-based and need no code. An invalid `strategy` is a 422. `null`/absent means not configured.
3. **Agent.** `LangGraphAgent.__init__` takes `headroom_config: ResolvedHeadroomConfig | None = None` (the resolved type, not the
   stored `HeadroomConfig`). It builds the hook once with `build_headroom_pre_model_hook(headroom_config, llm_model)`, assigned before
   `self.init_agent()`. The hook is appended **last** in `_compose_pre_model_hooks(...)` in `_build_single_agent`
   (`langgraph_agent.py` ~351) and in `_build_supervisor_agent` (~312). A `None` hook leaves both chains identical to today.
4. **Service.** `AssistantService.build_agent` (`assistant_service.py` ~503) reads the stored config defensively
   (`getattr(assistant, "headroom_config", None)`, accepted only if `isinstance(..., HeadroomConfig)`, because many tests use
   `Mock(spec=Assistant)`), calls `resolve(None, None, stored)`, and sets `agent_kwargs["headroom_config"]` **only inside the
   `if agent_class == LangGraphAgent:` block and only when the resolved value is not `None`**. Kwargs are therefore unchanged when
   unconfigured. `AIToolsAgent`, the Bedrock early return and remote A2A (never reach `build_agent`) never receive it.
   `build_agent_for_workflow` is not touched.
5. **Gating.** Both gates (`HEADROOM_ENABLED`, `features:headroom`) live inside `resolve()`; no handler change. Stored config is kept
   while a gate is off and ignored at runtime, as `hedging_config` is.
6. **Inheritance (no extra code).** Builtin subagent clones (`model_copy`) inherit the parent's config. Each sub-assistant builds its
   own `LangGraphAgent` via `build_agent(is_subagent=True)` and resolves its own config. The supervisor hook covers only the
   supervisor's own model calls.
7. **Docs.** New `.ai-run/guides/integration/headroom-integration.md`: architecture, settings, precedence, deployment (migration before
   app, sidecar, `/sanity` for dependency changes), strategy meanings including the prompt-cache trade-off, how to add a strategy
   (`strategies.py` is the only place strategy values live), known limitations, and the API contract handoff to UI and SDK repos
   (design Appendix A). One AGENTS.md Guide Imports row, `Headroom`, inserted after the FAQ Git row.

## Acceptance criteria

- A1. Migration adds nullable JSONB `assistants.headroom_config`; upgrade and downgrade work; single head after it; `down_revision` is the verified head.
- A2. `AssistantRequest` and `AssistantBase` accept, serialize and round-trip `headroom_config`; default is `None`.
- A3. API create, update and get round-trip `headroom_config`; an invalid strategy returns 422.
- A4. With a resolved config, the hook is the last element of the composed pre-model chain for both the single agent and the supervisor; with `None` it is absent and the chain is unchanged.
- A5. `build_agent` passes `headroom_config` to `LangGraphAgent` when resolve returns a config, and never to `AIToolsAgent` or via the Bedrock path.
- A6. With no config, flag off or kill switch off, `LangGraphAgent` kwargs are identical to before this change (no `headroom_config` key).
- A7. `Mock(spec=Assistant)` assistants work unchanged in `build_agent`, including with the gates on.
- A8. The guide exists with the sections above and the AGENTS.md row follows the FAQ Git row.

## Ticket AC coverage

- Satisfied by slice 2: AC3 and AC4 for assistants, AC5 for assistants, AC6 (regression tests), AC7 partly (assistant-scope model, API, agent, service and migration tests), AC8 (guide).
- Deferred to slice 3: AC3, AC4, AC5 for workflows and nodes; AC7 workflow tests.

## Non-goals

- Workflow-level and node-level config, workflow YAML, `execution_config_schema.yaml`, `AssistantExecutionOptions`, `build_agent_for_workflow` (slice 3, depends on the EPMCDME-15759 workflow-save fix being merged).
- UI and CodeMie SDK repos (handoff is the API contract in design Appendix A, repeated in the guide).
- AIToolsAgent, Bedrock and remote A2A support.
- Structured-output compression.
- Versioning of `headroom_config`.
- Prompt-cache mitigation.
- Any change to routers, handlers, list responses or slice 1 package code.

## Known limitations (stated in the guide)

- `headroom_config` is not versioned (same as `hedging_config`): a headroom-only edit creates no new version, and applying an old version keeps the master value.
- The hook is synchronous and blocking (up to `HEADROOM_TIMEOUT_SECONDS`) on every model call; it fails open.
- A2A-served assistants and the assistant health check call `build_agent` and compress when configured.
- Supervisor and sub-assistants compress independently; a mixed setup is expected.
- Until slice 3, a workflow node's own sub-assistants compress from their saved assistant config, though the node itself is not wired.

## Tests

Model and serialization round trip (template `tests/codemie/rest_api/models/test_assistant_interactive_enabled.py`); API create/update/get and 422;
agent chain ordering by patching `create_smart_react_agent` and `create_supervisor` and inspecting `pre_model_hook`; `build_agent` wiring
(LangGraph yes, AIToolsAgent and Bedrock no) with `Mock(spec=Assistant)`; unconfigured regression (A6); migration revision, head and
add/drop column. Tests are written and run only when requested, per AGENTS.md; commands come from `.ai-run/guides/quality-gates.md`.
