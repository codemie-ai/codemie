# EPMCDME-16411 (MR !4647): one project model policy, resolved once per assistant

## Intent

Reviewer note #2501663: the agreed rule does not hold for the root or for sub-assistants. The model is chosen in two places (`_ask.py:130-132`, `build_agent`), with two different orders. `build_agent` writes its result back into `assistant.llm_model_type` and `request.llm_model` (`assistant_service.py:566-569`), so every child receives the parent's substitute as if the user had picked it. The whitelist policy exists five times with different semantics. `get_project_models` collapses `None` into `[]`, so the five sites disagree on unconfigured versus deny-all. The fix belongs in this MR, with no follow-ups.

**Rule, applied to the root and to every child.** Take the chat pick if the assistant's project allows it. Otherwise take the assistant's own `llm_model_type` if allowed. Otherwise take the project default, otherwise the first allowed model. The chat pick is `request.llm_model`, or the conversation's stored `llm_model` when the request has none.

## Design

1. **`ProjectModelPolicy`** lives in `service/llm/model_availability_service.py`. It is built from the raw `Application` and has three modes:
   - *unrestricted*: flag off, global assistant, missing project, or `allowed_models is None`;
   - *deny-all*: `allowed_models == []`;
   - *whitelist*.

   `choose(candidates)` returns the first allowed candidate, then the default, then the first allowed model. When it substitutes, it logs a `model_availability_event=` line and sends `MODEL_AVAILABILITY_FALLBACKS_TOTAL_METRIC`. In unrestricted mode it returns the first non-empty candidate, else `default_model`. In deny-all mode it raises `ModelNotWhitelistedException`.

   `check(model)` rejects without substituting and sends `MODEL_AVAILABILITY_VALIDATIONS_TOTAL_METRIC`.

   A populated whitelist with no default still raises `NoDefaultModelException`. `get_project_models` returns `allowed_models` as stored, so `None` stays `None`. The five sites become thin callers of the policy:
   - `enforce_allowed_model`
   - `resolve_requested_model`
   - `resolve_model_for_execution`
   - `validate_model_for_asset_creation`
   - `_ensure_project_allows_model`
2. **`resolve_assistant_model(assistant, user_pick) = policy_for(assistant).choose([user_pick, assistant.llm_model_type])`** is called once per assistant in `build_agent`, for the root and for every `assistant_ids` child. The pre-resolution in `_prepare_assistant_for_execution` (`_ask.py:130-132`) is removed.
3. **Inputs stay untouched.** `build_agent` writes to neither `assistant` nor `request`. `request.llm_model` always means the user's pick, and children receive the original request. The resolved model travels explicitly into tools, `agent_kwargs` and `configure_agent_kwargs`. Built-in clones (`_build_builtin_subagent_executor`) get the parent's resolved model explicitly. The unused `AssistantFactory.llm_model` goes. The resolved model is exposed on the agent `build_agent` returns. `assistant_handlers.py:417` and `conversation_service.py:426` read it from there.
4. **Supervisor descriptions** come from the executors that were actually built. `create_subagent_executors` reports the names it built. `configure_agent_kwargs` (`assistant_engine_builder.py:315-327`) filters the descriptions to those names.
5. **Agent-level re-resolution stays as an idempotent guard**, routed through `policy.choose([model])`. It covers `AIToolsAgent._resolve_llm_model`, `get_llm_by_credentials` and workflow `base_node`.

**Catalog special case.** For a `[]` whitelist, `catalog.py:189-200` shows the assistant's own model with no `project_default` marker, which is what it shows today. This is a display-only branch, written out where the catalog calls the policy. It is not a second policy semantic. Execution with `[]` still raises.

## Acceptance criteria

- AC1 (fix 1): `ProjectModelPolicy` is the only implementation. All five sites delegate to it. `get_project_models` keeps `None` distinct from `[]`. `choose` logs and sends the fallback metric only when it substitutes. `check` never substitutes.
- AC2 (fix 2): `build_agent` calls `resolve_assistant_model` once per assistant. `_ask.py` no longer resolves the model.
- AC3 (fix 3): After `build_agent`, the caller's `assistant.llm_model_type` and `request.llm_model` are unchanged, and so is `request.model_fields_set`. Built-in clones run on the parent's resolved model. The returned agent exposes the resolved model, and the two metrics/history readers use it.
- AC4 (fix 4): If a subagent fails to build, it is missing from both the handoff tools and the supervisor descriptions.
- AC5 (fix 5): A table test covers the policy across:
  - unrestricted, deny-all, and whitelist with or without a default;
  - an allowed pick, a disallowed pick with the own model allowed, both disallowed, and an empty pick;
  - flag off and global.
- AC6 (reviewer example, same project): flag on, whitelist `[D, M]`, default `D`, child configured with `M`, user picks `X`. The child runs on `M`. A root configured with `M` also runs on `M`. Both go through the real `build_agent`.
- AC7 (reviewer example, cross-project): same setup, but the child's project allows `X`. The child runs on `X`, even though the parent substituted.
- AC8: When the request carries no `llm_model`, the conversation-stored `llm_model` is the user pick for the root and for every child.
- AC9: Flag off, `[]` at execution and global assistants behave as on main. Catalog detail with `[]` shows the own model with no marker.
- AC10: The tests that asserted in-place mutation, or patched `codemie.core.dependecies.ModelAvailabilityService.get_project_models`, are rewritten to the new contract. The quality gates pass.

## Behaviour changes vs main

All of these happen only with `projectModelOverride` on.

1. Asset creation with `allowed_models is None` and `default_model` set is rejected today by `validate_model_for_asset_creation` (`model_availability_service.py:184-191`). It becomes unrestricted. This is the `None`/`[]` collapse the reviewer named as the root cause: an unconfigured whitelist means no restriction, at every site.
2. When the user pick is not allowed, the root and the children now fall back to their own `llm_model_type` before the project default.
3. Cross-project children check the user's original pick, not the parent's substitute.
4. Children now honour the conversation-stored `llm_model`. On main it was skipped, because `request.llm_model` was written back and recorded in `model_fields_set`.

Flag off, `[]` deny-all at execution and global assistants behave as on main.

## Non-goals

- No model resolution for the root of `build_agent_for_workflow`. The workflow node model stays with `base_node`. Its `assistant_ids` children still resolve in `build_agent`, with the workflow request's `llm_model` as the user pick.
- No change to how a whitelist with no default is handled (it still raises `NoDefaultModelException`).
- No write-back of the resolved model into `Conversation.llm_model`.
- No REST contract, DB schema, UI or `subagents.yaml` change.
- No change to `ExtendedHTTPException` or `describe_exception`.

## Left to the builder

- The policy's exact module and class layout.
- Whether to delete thin wrappers or keep them as aliases.
- The attribute name for the resolved model on the agent.
- The log wording.
