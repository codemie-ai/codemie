# EPMCDME-16411: One Project Model Policy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]` syntax.

**Goal:** One `ProjectModelPolicy`, resolved once per assistant in `build_agent`, with no mutation of `assistant`/`request`.
**Architecture:** Policy class in `service/llm/model_availability_service.py`; the five existing sites become thin callers; `build_agent` computes the model with `resolve_assistant_model` and passes it explicitly to tools, agent kwargs, and children.
**Tech Stack:** Python, FastAPI, SQLModel, pytest + unittest.mock.
**Spec:** `docs/superpowers/tasks/2026-10-30-epmcdme-16411-unified-model-policy/spec.md`
**Research:** `.../technical-analysis.md` (same dir). Line refs below come from it.

Commit per task using the repository's existing convention.

## Global Constraints

- Every behaviour change is gated on `projectModelOverride`. Flag off, `[]` at execution (raises), and global assistants behave as on main.
- Modes: *unrestricted* (flag off / global / missing project / `allowed_models is None`), *deny-all* (`[]`), *whitelist*.
- A populated whitelist with no default still raises `NoDefaultModelException`.
- No REST/DB/UI/`subagents.yaml` change. No write-back into `Conversation.llm_model`. Don't touch `ExtendedHTTPException`/`describe_exception`.
- The resolved model is exposed as the existing `agent.llm_model` (set at `assistant_agent.py:190`, `langgraph_agent.py:185`). Don't add a new attribute.

## Review Focus

1. Global assistant with a project that has `[]`: must stay unrestricted, not raise. Covered in Task 1 table.
2. User pick `""`/`None` with own model disallowed: default, then `allowed[0]`. Task 1.
3. A child's version-pinned config (`create_assistant_executors` pins): resolution uses the pinned `llm_model_type`. Task 5 chain test.
4. Missing project (`NotFoundException`) → unrestricted, never 500. Task 1.
5. Request with explicit `llm_model=None` in `model_fields_set` must not pick up the conversation model. This matches main. Task 4.

---

### Task 1: `ProjectModelPolicy` + table test

**Files:** Modify `src/codemie/service/llm/model_availability_service.py:31-102`; Test `tests/codemie/service/llm/test_project_model_policy.py` (new)

**Produces:**
```python
class PolicyMode(StrEnum): UNRESTRICTED; DENY_ALL; WHITELIST
class ProjectModelPolicy:
    mode: PolicyMode; allowed_models: list[str]; default_model: str | None; project_name: str | None
    @classmethod
    def for_project(cls, project: Application | str | None, is_global: bool = False) -> "ProjectModelPolicy"
    def choose(self, candidates: list[str | None], *, context: str = "") -> str | None
    def check(self, model: str) -> None  # raises ModelNotWhitelistedException
```

`Test-first: yes — parametrized table over mode × pick (allowed / disallowed with own allowed / both disallowed / empty) plus flag-off and global fails because ProjectModelPolicy does not exist`

- [ ] Write the table test. It must cover every AC5 row. Also assert:
  - the fallback metric and the `model_availability_event=` log fire only on substitution;
  - `check` emits `MODEL_AVAILABILITY_VALIDATIONS_TOTAL_METRIC` for allowed and blocked, and never returns a substitute;
  - deny-all `choose` raises;
  - whitelist without default raises `NoDefaultModelException`;
  - a missing project resolves to unrestricted;
  - `get_project_models` returns `allowed_models=None` for an unconfigured project.
- [ ] Run `pytest tests/codemie/service/llm/test_project_model_policy.py -v`. Expect FAIL (ImportError).
- [ ] Implement. `choose` reuses `pick_allowed_model` (L54-74) ordering over the non-empty candidates. In unrestricted mode it returns the first non-empty candidate, else `default_model`. Change `ProjectModelConfig.allowed_models` to `list[str] | None` and drop the collapse at L89-90.
- [ ] Re-run the test. Expect PASS. Then fix the existing `test_model_availability_service.py::get_project_models` cases that asserted `[]` for None.

### Task 2: Service-level wrappers + catalog delegate to the policy

**Files:** Modify `model_availability_service.py:105-307` (`resolve_requested_model`, `validate_model_for_asset_creation`, `resolve_model_for_execution`), `src/codemie/rest_api/routers/assistant/catalog.py:189-200`; Test `tests/codemie/service/llm/test_model_availability_service.py`, `tests/codemie/rest_api/routers/test_assistant.py`

**Consumes:** Task 1 `ProjectModelPolicy`.

`Test-first: yes — validate_model_for_asset_creation with allowed_models=None and default_model set passes (fails today: rejected at L187); resolve_requested_model with [] raises ModelNotWhitelistedException; catalog detail with [] shows the own model and no project_default marker`

- [ ] Write the three tests and run them. Expect the first two to FAIL. The catalog test pins current behaviour.
- [ ] Rewrite each function as a thin caller:
  - `resolve_requested_model` → `choose([requested])`, still returns `(picked, picked != requested)`;
  - `resolve_model_for_execution` → `choose([requested, fallback_model])`;
  - `validate_model_for_asset_creation` → `check`.

  The spec leaves it to the builder whether to keep these as aliases. Keep them to limit churn. In `catalog.py`, handle `PolicyMode.DENY_ALL` explicitly as a display-only branch: return the own model with no marker.
- [ ] Run `pytest tests/codemie/service/llm tests/codemie/rest_api/routers/test_project_model_availability*.py tests/codemie/rest_api/routers/test_assistant.py -v`. Expect PASS.

### Task 3: Core/toolkit sites and idempotent guards delegate to the policy

**Files:**
- Modify `src/codemie/core/dependecies.py:231-286` (`enforce_allowed_model`, `get_llm_by_credentials`).
- Modify `src/codemie/service/tools/toolkit_service.py:861-872`.
- Modify `src/codemie/agents/assistant_agent.py:~289-314` (`_resolve_llm_model`).
- Modify `src/codemie/workflows/nodes/base_node.py:148`.
- Modify `src/codemie/service/llm_service/llm_service.py:526`.
- Tests: `tests/codemie/core/test_dependecies.py:199-293`, `test_toolkit_service.py`, `workflows/nodes/test_model_selection.py`.

**Consumes:** Task 1.

`Test-first: yes — enforce_allowed_model / AIToolsAgent._resolve_llm_model tests patch ProjectModelPolicy.for_project instead of codemie.core.dependecies.ModelAvailabilityService.get_project_models, and fail until the sites call the policy`

- [ ] Retarget the existing tests to the policy patch point and run them. Expect FAIL.
- [ ] Make the changes:
  - `enforce_allowed_model(requested, project, assistant_model)` → `for_project(project).choose([requested, assistant_model])`;
  - `_ensure_project_allows_model` → `check`;
  - agent, `get_llm_by_credentials` and `base_node` guards → `choose([model])`. Keep the `fail_open` handling in `base_node`.

  Remove the raw-field None/`[]` re-reads and the duplicated `"projectModelOverride"` literals. Use `PROJECT_MODEL_OVERRIDE_FEATURE`.
- [ ] Re-run the listed tests. Expect PASS.

### Task 4: `resolve_assistant_model` in a non-mutating `build_agent`; remove `_ask.py` pre-resolution

**Files:**
- Modify `src/codemie/service/assistant_service.py:89-118, 541-569` and the downstream uses of `llm_model`.
- Modify `src/codemie/rest_api/routers/assistant/_ask.py:87-134`.
- Modify `src/codemie/rest_api/handlers/assistant_handlers.py:417`.
- Modify `src/codemie/service/conversation_service.py:426`.
- Tests: `tests/codemie/service/tools/test_assistant_factory.py:420-623`, `tests/codemie/service/test_assistant_service_methods.py`.

**Produces:** `resolve_assistant_model(assistant: Assistant, user_pick: str | None) -> str | None` in `model_availability_service.py`. It returns `ProjectModelPolicy.for_project(assistant.project, assistant.is_global).choose([user_pick, assistant.llm_model_type])`. `build_agent` returns an agent whose `.llm_model` is the resolved model.

`Test-first: yes — after build_agent, caller's assistant.llm_model_type, request.llm_model and request.model_fields_set are unchanged and agent.llm_model == M for whitelist [D, M], default D, own M, pick X (root case of AC6)`

- [ ] Rewrite the in-place-mutation assertions in `test_assistant_factory.py` to the new contract. Add the root AC6 test and an AC8 root test: no `llm_model` in the request, conversation stores M, and the result is M. Run them. Expect FAIL.
- [ ] Make the changes:
  - `build_agent` computes the user pick as `request.llm_model`, or the conversation's `llm_model` when `'llm_model' not in request.model_fields_set`. Read it into a local and don't set it on the request.
  - Call `resolve_assistant_model` once and pass the result explicitly.
  - Delete the write-backs at L566-569.
  - Delete `_resolve_chosen_execution_model` and the L130-132 write in `_prepare_assistant_for_execution`.
  - Point the two metrics/history readers at `agent.llm_model`.
- [ ] Run `pytest tests/codemie/service/tools/test_assistant_factory.py tests/codemie/service/test_assistant_service_methods.py tests/codemie/rest_api/routers/test_project_model_availability_execution.py -v`. Expect PASS.

### Task 5: Audit the other `build_agent` callers after the write-back is removed

**Files:**
- Inspect and modify as needed: `rest_api/handlers/assistant_handlers.py:663,1146,1207,1308`, `hedged_handler.py:485`, `a2a/server/codemie_task_manager.py:277`, `service/assistant/assistant_health_check_service.py:305`.
- Tests are in each caller's existing test module.

**Consumes:** Task 4 `agent.llm_model`.

`Test-first: yes — for each caller that reads assistant.llm_model_type or request.llm_model after build_agent (for metrics, response payloads, history or health-check results), a test asserting it reports agent.llm_model under a substituting whitelist; fails while it still reads the unmutated input`

- [ ] At each call site, list every read of `assistant.llm_model_type` / `request.llm_model` that happens after `build_agent` returns. Record what each one means: user pick or effective model.
- [ ] For each read that means "effective model", write the failing test, switch the read to `agent.llm_model`, and rerun. Reads that mean "user pick" stay as they are. Note them in the commit message body.
- [ ] If no caller reads post-build, record "no post-build readers" per site in the commit body. In that case there is no code change and no test.

### Task 6: Children receive the original request; built-in clones get the parent's resolved model

**Files:**
- Modify `src/codemie/service/assistant/assistant_engine_builder.py:136-251, 281-327`.
- Modify `src/codemie/service/tools/assistant_factory.py` (drop `llm_model` from `__init__`; `build()` L61-98).
- Test `tests/codemie/service/assistant/test_model_resolution_chain.py` (new), `test_create_subagent_executors.py`.

**Consumes:** Task 4.

`Test-first: yes — parent→child through real build_agent (stop at get_react_llms via the _PolicyPassed sentinel pattern from test_assistant_factory.py): AC6 child with own M runs on M; AC7 child in a project allowing X runs on X while the parent substituted; AC8 child gets the conversation model; builtin clone runs on parent's resolved model`

- [ ] Write the chain tests and run them. Expect FAIL. Today the child sees the parent's substitute and the builtin clone reads the mutated `llm_model_type`.
- [ ] Make the changes:
  - `create_subagent_executors` passes the caller's original (unmutated) request to `create_assistant_executors`.
  - `_build_builtin_subagent_executor` takes an explicit `resolved_model: str | None` parameter and uses it instead of the clone's `llm_model_type`. Clones skip re-resolution.
  - Remove `AssistantFactory.llm_model`.
- [ ] Run `pytest tests/codemie/service/assistant tests/codemie/service/tools/test_assistant_factory.py -v`. Expect PASS.

### Task 7: Supervisor descriptions come from the built executors

**Files:** Modify `assistant_engine_builder.py:181-327` (`create_subagent_executors` return, `configure_agent_kwargs`); Test `test_create_subagent_executors.py`, `test_configure_agent_kwargs_confirmation.py`, `test_script_tool_confirmation.py`

**Produces:** `create_subagent_executors(...) -> tuple[list[Executor], set[str]]`. The set holds the built subagent names. `configure_agent_kwargs` filters `get_subagent_descriptions_with_contributions` output to those names.

`Test-first: yes — a sub-assistant whose build raises is absent from both handoff tools and supervisor descriptions (AC4); fails today because descriptions come from Assistant.get_by_ids`

- [ ] Write the test and run it. Expect FAIL.
- [ ] Implement the return change and the filter. Update the two confirmation test modules for the new tuple return.
- [ ] Run `pytest tests/codemie/service/assistant -k "subagent or configure_agent_kwargs or script_tool" -v`. Expect PASS.

---

## Negative-constraint pass

- No root resolution in `build_agent_for_workflow`: no task touches it. Its children resolve through Task 6 with the workflow request's pick.
- Whitelist without default keeps raising: Task 1 test.
- No write-back into `Conversation.llm_model`: Task 4 reads it only.
- No REST/DB/UI/YAML change: Task 2's catalog change is internal and keeps the response shape.
- `ExtendedHTTPException`/`describe_exception` untouched: no task edits them.
- `build_agent` must not mutate its inputs: Task 4 test. The `[]` catalog branch is display-only, not a policy semantic: Task 2.
