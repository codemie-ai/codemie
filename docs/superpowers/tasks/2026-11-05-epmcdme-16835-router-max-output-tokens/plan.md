# Router Output Limit From Tiers Implementation Plan

> **Superseded in part.** This plan records the original implementation. The design changed after the architecture reviews of 2026-11-09 and 2026-11-10 (request cap 64,000 characters, no tool output in the report prompt, tool-declared written keys, keys-only reports for hidden assistants), and after the live test of 2026-11-10 (the cut-off-call guard on every agent's post-model hook, which this plan's "the call can run before the stop" no longer describes). `spec.md` is the current design; where the two differ, the spec wins (re-review AR-9).

> **For agentic workers:** sdlc-task runs this plan inline (superpowers:test-driven-development). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the LiteLLM catalog is stored, a declared LiteLLM auto-router with no positive `max_output_tokens` gets the smallest `max_output_tokens` of its tier models, so codemie forwards `max_tokens` for the router.

**Architecture:** A pure staticmethod on `LLMService` (sibling of `_router_max_input_tokens`) computes the limit from `(tiers, by_name)`. A second staticmethod applies it in place to the router entries of the chat list inside `initialize_default_litellm_models`, the single sink for startup, the recovery loop and admin reload. `llm_factory` is unchanged: it already forwards a positive `max_output_tokens` as `max_tokens`.

**Tech Stack:** Python 3.12, Pydantic, pytest (xdist), Poetry, Ruff.

**Spec:** `docs/superpowers/tasks/2026-11-05-epmcdme-16835-router-max-output-tokens/spec.md`

## Global Constraints

- "Positive" means an `int` that isn't a `bool` and is `> 0`, the same rule as `llm_factory._resolve_max_output_tokens`.
- Use the min across all distinct tier models. If any tier is missing from the catalog or has no positive value, the result is None (router left unset, proxy default).
- A positive `max_output_tokens` already on the router entry is never overwritten.
- No eager `codemie.enterprise` import in `src/codemie/service/llm_service/llm_service.py`.
- Log format: `router_output_limit model=<base_name> max_output_tokens=<value> source=router_tiers`, at INFO, once per router whose value is derived.
- Commit messages: `EPMCDME-16835: <Description>`.

## Review Focus

- A tier `max_output_tokens` of `True`, `0`, `-1` or `"128000"` must count as unknown (router stays unset), never as a value. Test in Task 1.
- Several tiers naming the same model (today's router uses one model for simple/medium) must resolve once; the result is that model's value. Test in Task 1.
- A router entry with an invalid own value (`0`) must still derive from its tiers. Test in Task 2.
- A tier that names the router itself (or another router with no value) is unknown, so it stays unset with no recursion. Test in Task 1.
- Non-router models must be untouched, even with no `max_output_tokens`. Test in Task 2.

---

### Task 1: Tier-derived output limit helper

**Files:**
- Modify: `src/codemie/service/llm_service/llm_service.py` (add the staticmethod right after `_router_max_input_tokens`, ~L159)
- Test: `tests/codemie/service/llm_service/test_llm_service_litellm_integration.py` (new class `TestRouterMaxOutputTokens`)

**Interfaces:**
- Produces: `LLMService._router_max_output_tokens(tiers: RouterTiers | None, by_name: dict[str, LLMModel]) -> int | None`

**Test-first: yes.** `TestRouterMaxOutputTokens` fails with `AttributeError: type object 'LLMService' has no attribute '_router_max_output_tokens'`.

- [ ] **Step 1: Write the failing tests.** Build tiers with a local helper, `_tiers(simple, medium, complex, reasoning) -> RouterTiers` (tier model names), and catalog entries with `LLMModel(base_name=..., deployment_name=..., label=..., enabled=True, provider=LLMProvider.AWS_BEDROCK, max_output_tokens=...)`. Tests:
  - `test_returns_min_across_distinct_tiers`: tiers `a,a,b,c` with a=128000, b=64000, c=128000 → `64000`
  - `test_shared_tier_model_resolves_once`: tiers `a,a,b,b` with a=b=128000 → `128000`
  - `test_none_when_tiers_absent`: `tiers=None` → `None`
  - `test_none_when_any_tier_missing_from_catalog`: tiers `a,a,b,missing` → `None`
  - `test_none_when_any_tier_has_no_limit`: b has `max_output_tokens=None` → `None`
  - `test_tier_value_must_be_positive_int`: parametrized over b's value `[0, -1, True]` → `None`. For a string, use `model_construct` or `MagicMock` for b with `max_output_tokens="128000"` → `None`.
  - `test_tier_naming_the_router_itself_is_unknown`: tiers `a,a,router,router`, where `by_name["router"]` has `max_output_tokens=None` → `None`
- [ ] **Step 2: Run and confirm they fail.** Run: `poetry run pytest tests/codemie/service/llm_service/test_llm_service_litellm_integration.py::TestRouterMaxOutputTokens -v -p no:xdist`. Expected: FAIL, AttributeError.
- [ ] **Step 3: Implement `_router_max_output_tokens`.** Same shape as `_router_max_input_tokens`: a set of the four tier model names, `by_name.get`, return None on the first unresolved or non-positive value (positive per Global Constraints), else `min`. One-line docstring noting it mirrors the input-window rule: an unknown tier must not allow a limit it might reject.
- [ ] **Step 4: Run and confirm they pass.** Same command. Expected: all PASS.
- [ ] **Step 5: Commit.** `git commit -m "EPMCDME-16835: Derive a router's output limit from its tier models"`

### Task 2: Apply the derived limit when the LiteLLM catalog is stored

**Files:**
- Modify: `src/codemie/service/llm_service/llm_service.py` (`initialize_default_litellm_models` ~L735, plus a new staticmethod)
- Test: `tests/codemie/service/llm_service/test_llm_service_litellm_integration.py` (new class `TestRouterOutputLimitOnCatalogLoad`)

**Interfaces:**
- Consumes: `LLMService._router_max_output_tokens` (Task 1)
- Produces: `LLMService._apply_router_output_limits(chat_models: list[LLMModel]) -> None`, which mutates router entries in place. `initialize_default_litellm_models` calls it on `user_models.chat_models` before storing.

**Test-first: yes.** After `initialize_default_litellm_models`, the stored router's `max_output_tokens` is `None` instead of the derived value.

- [ ] **Step 1: Write the failing tests.** Use the `llm_service` fixture. Router built as `LLMModel(base_name="claude-router-standard", ..., litellm_router=LiteLLMRouterConfig(tiers=<tiers a,a,b,b>))`, with tier models a=128000 and b=64000 in the same `LiteLLMModels(chat_models=[...])`. Tests:
  - `test_router_without_limit_gets_min_of_tiers`: the stored router has `max_output_tokens == 64000`
  - `test_router_own_positive_limit_wins`: router `max_output_tokens=32000` → stays `32000`
  - `test_router_invalid_own_limit_is_derived`: router `max_output_tokens=0` → `64000`
  - `test_router_with_unknown_tier_stays_unset`: tier b absent from `chat_models` → `None`
  - `test_non_router_models_untouched`: a plain model with `max_output_tokens=None` stays `None`, and tier values are unchanged
  - `test_logs_derived_limit`: patch `codemie.service.llm_service.llm_service.logger`; assert an INFO call with `"router_output_limit model=claude-router-standard max_output_tokens=64000 source=router_tiers"`, and no such call for a router whose own value wins
- [ ] **Step 2: Run and confirm they fail.** Run: `poetry run pytest tests/codemie/service/llm_service/test_llm_service_litellm_integration.py::TestRouterOutputLimitOnCatalogLoad -v -p no:xdist`. Expected: the derivation tests FAIL (`None != 64000`); the "wins", "unknown" and "untouched" tests may already pass.
- [ ] **Step 3: Implement `_apply_router_output_limits`.**
  - `by_name = {m.base_name: m for m in chat_models}`.
  - For each model where `is_declared_litellm_router()` and the own value is not positive: compute `_router_max_output_tokens(model.litellm_router.tiers, by_name)`.
  - When it isn't None, set `model.max_output_tokens` and log the INFO line from Global Constraints.
  - Call it first thing in `initialize_default_litellm_models`.
- [ ] **Step 4: Run and confirm they pass.** Same command, plus the existing `TestLiteLLMModelInitialization` class. Expected: all PASS.
- [ ] **Step 5: Commit.** `git commit -m "EPMCDME-16835: Set a router's output limit from its tiers when the LiteLLM catalog loads"`

### Task 3: Seam test, router chat model carries `max_tokens`

**Files:**
- Test: `tests/enterprise/litellm/test_llm_factory.py` (add to `TestMaxOutputTokensForwarding`)

**Interfaces:**
- Consumes: `LLMService.initialize_default_litellm_models` (Task 2), `map_litellm_to_llm_model` (existing), `TestMaxOutputTokensForwarding._create` (existing helper)

**Test-first: no.** This is a regression guard at the outer boundary (testing guide, "Seam Tests for Policy Helpers"). The behavior was delivered in Task 2, so it passes when written. To confirm it actually guards, comment out the call in `initialize_default_litellm_models` once and see it fail.

- [ ] **Step 1: Write `test_router_from_proxy_catalog_forwards_tier_limit_end_to_end`.**
  - Map three raw `/model/info` dicts with `map_litellm_to_llm_model`:
    - tier `claude-sonnet-5` and tier `claude-haiku-5-5`: `max_output_tokens: 128000`, `supported_openai_params: ["max_tokens", "temperature"]`, `litellm_provider: "bedrock"`, `litellm_params.model` as a bedrock anthropic id
    - router `claude-router-standard`: no `max_output_tokens`, `supported_openai_params: ["max_tokens", "temperature"]`, `model_info.litellm_router: {"tiers": {simple/medium → claude-haiku-5-5, complex/reasoning → claude-sonnet-5}}`
  - Feed them to a fresh `LLMService(LLMConfig(yaml_file=Path("tests/service/llm_test_config.yaml"), llm_models=[], embeddings_models=[]))` via `initialize_default_litellm_models(LiteLLMModels(chat_models=[...]))`.
  - Take the router from `default_litellm_models` and pass it to `self._create(router)`.
  - Assert `kwargs["max_tokens"] == 128000`.
- [ ] **Step 2: Write `test_router_from_proxy_catalog_logs_model_config_source`.** Same setup through `self._create_logged(router)`. Assert the line `"llm_output_limit model=claude-router-standard max_tokens=128000 source=model_config"`.
- [ ] **Step 3: Run and confirm they pass.** Run: `poetry run pytest tests/enterprise/litellm/test_llm_factory.py::TestMaxOutputTokensForwarding -v -p no:xdist`. Expected: all PASS.
- [ ] **Step 4: Check that the test guards.** Temporarily remove the `_apply_router_output_limits` call, rerun the Step 3 command, and expect the two new tests to FAIL (`KeyError: 'max_tokens'` and an unset log line). Then restore the call.
- [ ] **Step 5: Lint and run the area tests.** Run `make ruff`, then `poetry run pytest tests/codemie/service/llm_service tests/codemie/service/test_llm_service.py tests/enterprise/litellm -q`. Expected: ruff clean, all PASS.
- [ ] **Step 6: Commit.** `git commit -m "EPMCDME-16835: Cover router max_tokens forwarding from the proxy catalog"`

---

# Scope extension (2026-11-06): ticket AC1–AC4

**Spec:** spec.md, "Scope extension (2026-11-06)". Executed inline (native), TDD.

### Task 4: Truncation facts and fallback text

**Files:** Create `src/codemie/agents/truncation_report.py`; Test `tests/codemie/agents/test_truncation_report.py`

**Interfaces — Produces:**
- `current_turn(messages: Sequence[BaseMessage]) -> list[BaseMessage]` — messages after the last `HumanMessage`.
- `collect_facts(turn_messages, truncated: AIMessage | None) -> TruncationFacts` — `completed: list[ToolCallFact]`, `interrupted: list[ToolCallFact]`; `ToolCallFact(name, request, succeeded, ran, keys, output_excerpt)`. Keys from `"key": "<PROJ>-<N>"` in tool output.
- `render_fallback(facts) -> str`.

Test-first: yes — create-then-truncated turn yields one completed fact with key `EPMCDME-16793` and one interrupted fact with `ran=False`; fallback text names both and says later steps did not run.

### Task 5: Model-written report with fallback

**Files:** same module; tests same file; `src/codemie/configs/config.py` (`AGENT_TRUNCATION_STATUS_REPORT_ENABLED: bool = True`).

**Interfaces — Produces:** `build_status_report(exc: TokenLimitExceededException, llm_provider: Callable[[], BaseChatModel], enabled: bool) -> str | None` — None when no turn messages; model text when the call succeeds and is non-empty; fallback otherwise. Never raises. Inputs capped (request 8000 chars, per call 1500).

Test-first: yes — fake model text is returned; model raising → fallback; empty → fallback; disabled → fallback without calling the model; no messages → None; prompt contains the key and the capped request.

### Task 6: Attach turn messages and deliver the report

**Files:** `src/codemie/core/exceptions.py` (`TokenLimitExceededException.turn_messages`, `.truncated_message`), `src/codemie/agents/langgraph_agent.py` (observed run messages, `_check_for_truncated_response(..., turn_messages=None)`, `_send_error_to_thread`, `_get_user_error_message`), `src/codemie/agents/supervisor/pre_model_hooks.py` (pass `state["messages"]`).

Test-first: yes — hook-raised exception carries state messages; consumer-raised exception carries observed messages; streamed final chunk and non-streaming result contain `AGENT_MSG_TOKEN_LIMIT` + report; `agent_error.message` stays short.

### Task 7: AC4 regression tests

**Files:** `tests/codemie_tools/core/project_management/jira/test_tools.py` (large payloads), `tests/codemie/agents/test_truncation_status_report_flow.py` (real graph, fake model: create Story ~12 KB then truncated sub-task → report names key; fallback on report failure; full flow completes when not truncated).

Test-first: n/a for pure regression tests over Task 4–6 behaviour; each must fail if the report wiring is removed (checked by reverting the delivery line).
