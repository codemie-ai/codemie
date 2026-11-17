# Support `effort` Parameter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `effort` (`low|medium|high|xhigh|max`) as a backend-only, additive generation parameter alongside `temperature`, for Anthropic-family models — the enterprise LiteLLM path, the direct-Anthropic provider path, and (per scope correction below) Bedrock-hosted Claude models — filtered silently (never raised) when a model doesn't support it, and **clamped down to the model's actual ceiling** when it does but the requested level exceeds what that model supports — exactly mirroring the existing `temperature` capability-filter pattern, extended with a per-model ceiling instead of a plain boolean.

**Architecture:** Mirror the existing `temperature` plumbing layer-by-layer: a **per-model allowed-effort-level list** on `LLMFeatures` (ceiling-aware, not a plain boolean — static YAML + LiteLLM dynamic `supported_openai_params`/`model_info:` override detection) → a shared clamp helper → central filter functions → `LLMParams`/`get_llm_by_credentials` → the two agent runtime classes → the central `AssistantService.build_agent`/workflow-override factory → REST/workflow DTOs. No UI, no Vertex Claude wiring (no Vertex-hosted Claude models exist in the catalog, and Gemini doesn't claim effort support). Bedrock-hosted Claude models **do** get real effort forwarding — this was broadened from the original direct-Anthropic-only scope after research found that no catalog model actually uses the direct `anthropic` provider; every Claude model in `config/llms/*.yaml` is Bedrock-hosted, so a plan that only wired the direct-Anthropic path would never actually forward `effort` for a real deployed model.

**Tech Stack:** Python, Pydantic v2, LangChain (`langchain_anthropic` 1.4.0, `langchain_aws` 1.4.3, `langchain_openai`), LiteLLM (`litellm==1.99.2`, a core dependency on the direct-provider path and the engine behind the optional `codemie-enterprise` proxy path), pytest.

**Spec:** Requirements supplied inline for Jira EPMCDME-13335 (no spec.md written for this task — see Acceptance criteria below).

## Global Constraints

- Backend only — no `ui`/frontend changes.
- `effort` values: `Literal["low","medium","high","xhigh","max"]` at the DTO/API layer.
- Unsupported `effort` on a model → silently **clamped** to the model's declared ceiling (ranks `low < medium < high < xhigh < max`; pick the highest model-declared level at or below the requested one — Task 1's `clamp_effort_level`), never a validation error. This clamp is required, not optional: Anthropic's own `_apply_output_config` validation raises `BadRequestError` *unconditionally* for an over-ceiling value, with **no `drop_params` escape hatch** for this specific check (technical-analysis.md §10.4), and CodeMie never sets `litellm.drop_params` anywhere (confirmed by grep) — passing a raw unclamped value through and hoping LiteLLM drops it will not work here. Unsupported `temperature` continues to be silently dropped (unchanged).
- New hand-added per-provider effort capability declarations are limited to exactly **three** groups, all named explicitly in this plan: Task 1's rename of the two existing OpenAI `o3`/`o4-mini` `reasoning: true` entries to `effort: ["low","medium","high"]`; Task 3's **three** Bedrock-hosted Claude entries in `llm-aws-config.yaml` declaring a per-model effort level list (`claude-sonnet-4-6`, `claude-opus-4-5-20251101`, `claude-opus-4-6-20260205`); and Task 2's `model_info:` override block for the two LiteLLM-proxy-only entries (`claude-sonnet-5`, `claude-opus-4-7`) in `litellm_config.yaml`. **`claude-4-5-haiku` and `claude-4-5-sonnet` explicitly get no effort declaration at all** — both lack `supports_output_config` entirely (confirmed absent, not merely `False`) and only ever receive LiteLLM's legacy `thinking: {type: enabled, budget_tokens: N}` shape, never real `output_config`-based effort. No Vertex-provider Claude entry gains a flag (none exist in the catalog), and no other provider/model gains one.
- `get_anthropic_llm`'s own SDK field (`langchain_anthropic.ChatAnthropic.effort`) is typed `Literal["max","high","medium","low"]` — it never accepts `"xhigh"`. Task 3's `clamp_effort_level` call naturally never produces `"xhigh"` for any model's declared level list in this catalog, so no separate hardcoded drop is needed beyond the clamp itself; noted here for traceability.
- The Bedrock-hosted Claude path (`get_bedrock_llm`) has no first-class `effort` kwarg at all in the pinned `langchain-aws` 1.4.3 `ChatBedrockConverse`. Research (see Task 3) found its only discrete low/medium/high "reasoning effort" field (`additional_model_request_fields.reasoningConfig.maxReasoningEffort`) is validated as Amazon-Nova-2-only and does not apply to Claude; Claude's own parameters are passed through `additional_model_request_fields` verbatim in Anthropic's native Messages-API shape. So Bedrock forwarding reuses that native shape — `additional_model_request_fields={"output_config": {"effort": effort}}` — with the forwarded value clamped to that model's own declared ceiling list (Task 1's helper), the same mechanism as the direct path.
- Temperature-vs-effort interaction: for Anthropic (direct and Bedrock) and Azure o-series models, dropping `temperature` is **unconditional per model** (`supports_sampling_params is False`) and **not request-dependent** — it stays a static per-model check (Tasks 1 and 3), just sourced from `litellm.model_cost` (direct path) or a `model_info:` override (enterprise path) rather than `litellm.get_model_info()`, which silently drops that key in this pinned version (§10.1/§10.3). GPT-5's own temperature gating **is** request-dependent (temperature survives only when `effort` is unset/`"none"`), but GPT-5 is explicitly out of this ticket's scope — no GPT-5 model entry is touched by this plan, and the request-dependent rule is **not** implemented; future work if GPT-5 effort support is ever added.
- Commit per task using the repository's existing convention (see `.ai-run/guides/standards/git-workflow.md`).

## Review Focus

- `effort="xhigh"` reaching the direct-Anthropic path crashes `ChatAnthropic(...)` construction (pydantic `ValidationError`) unless clamped before the call — Task 3.
- `effort="xhigh"` reaching the Bedrock-hosted Claude path does **not** crash `ChatBedrockConverse(...)` (the SDK doesn't validate Claude's `additional_model_request_fields` shape) — clamping it there anyway is required for value-set parity with the direct path and with the model's own declared ceiling, not a crash-avoidance fix, and only Task 3's clamp enforces it.
- A model with `features.effort` falsy (`None`/empty) still receiving a non-null `effort` kwarg from a caller must silently drop it, not raise — Tasks 2 and 3, on both the direct-Anthropic and Bedrock branches.
- A requested `effort` whose rank sits strictly between two of a model's declared levels (e.g. `"xhigh"` requested on a model whose list is `["low","medium","high","max"]`) must clamp **down** to the nearest declared level at or below that rank (`"high"`), never jump **up** to a higher declared level (`"max"`) — Task 1's `clamp_effort_level`, exercised by Tasks 2 and 3.
- `claude-4-5-haiku` and `claude-4-5-sonnet` must never receive a `features.effort` list and must never gain an `output_config`/`effort` kwarg on any path, regardless of what a caller requests — Task 3.
- The enterprise path's `supports_output_config`/`supports_sampling_params`/ceiling signals for `claude-sonnet-5`/`claude-opus-4-7` must come from `litellm_config.yaml`'s `model_info:` override, never from `litellm.get_model_info()` directly — that call silently drops both keys in this pinned version — Task 2.
- Merging the new `output_config.effort` field into `get_bedrock_llm`'s `additional_model_request_fields` must not clobber the existing `client_headers`-derived content already assigned to that same dict — Task 3.
- Workflow-sourced `effort=None` must not clobber an already-set `assistant.effort` (mirrors the existing `temperature is not None` guard) — Task 7.
- LiteLLM-reported `model_info` missing `supports_output_config` entirely must resolve `features.effort` to `None`, not a crash — Task 1.
- Existing temperature-only models must keep working unmodified: `features.effort` defaults to `None` and omitting `effort` must never add any new keys to request kwargs — Tasks 1–3.

---

## Acceptance criteria

- [ ] A model/assistant/chat config can set `effort` to one of `low`/`medium`/`high`/`xhigh`/`max`.
- [ ] A supported Anthropic model (direct path or via LiteLLM) receives the configured `effort` value in its provider request, clamped to that model's declared ceiling when the requested value exceeds it.
- [ ] A supported Bedrock-hosted Claude model receives the configured `effort` value via `additional_model_request_fields` on the `ChatBedrockConverse` call, likewise clamped.
- [ ] A model that doesn't support `effort` has it silently dropped — no validation error.
- [ ] A model that doesn't support `temperature` continues to have it silently dropped — no regression.
- [ ] `effort="xhigh"` never reaches `langchain_anthropic.ChatAnthropic` unclamped (its SDK field rejects that value outright).
- [ ] `effort="xhigh"` never reaches `get_bedrock_llm`'s `additional_model_request_fields.output_config.effort` unclamped either, for the same value-set-parity reason.
- [ ] A requested `effort` above a model's declared ceiling is clamped to the nearest declared level at or below it — never raised, never passed through raw.
- [ ] The two stale `features.reasoning: true` YAML entries are reconciled into the new `effort` mechanism, not duplicated.
- [ ] Exactly three Bedrock-hosted Claude catalog entries (`claude-sonnet-4-6`, `claude-opus-4-5-20251101`, `claude-opus-4-6-20260205`) declare a `features.effort` level list matching their confirmed ceiling (§10.2); `claude-4-5-haiku` and `claude-4-5-sonnet` declare none.
- [ ] No Vertex-provider Claude catalog entry gains an `effort` flag (none exist in the catalog).
- [ ] `effort` is defined consistently (same `Literal`, same default) across the DTOs that currently duplicate `temperature`.
- [ ] No code under `repository/cli_analytics/*` or `enterprise/switchyard/*` is touched or renamed.

---

### Task 1: `LLMFeatures.effort` — per-model allowed-level list + clamp helper, plus a `supports_sampling_params` correction for dynamic temperature detection

**Scope correction (technical-analysis.md §10):** `effort` support is not a single yes/no per model — it has a per-model *ceiling* (`claude-sonnet-4-6`/`claude-opus-4-6-20260205` top out at `max` with no `xhigh`; `claude-opus-4-5-20251101` tops out at `high`; the two LiteLLM-proxy-only models support the full `xhigh`+`max` range). A plain boolean can't express this, and because `litellm.get_model_info()` silently drops `supports_output_config`/`supports_sampling_params`/`supports_medium_reasoning_effort`/`supports_high_reasoning_effort` in this pinned version (confirmed, §10.1), the gate and ceiling flags must be read from `litellm.model_cost` directly (dynamic path) or hand-declared `model_info:` overrides (enterprise/proxy path), never from `get_model_info()` for those four keys.

**Files:**
- Modify: `src/codemie/configs/llm_config.py:45-52` — change `effort: Optional[bool] = False` to `effort: Optional[List[Literal["low","medium","high","xhigh","max"]]] = None` on `LLMFeatures`; add a module-level `EFFORT_RANK = {"low": 0, "medium": 1, "high": 2, "xhigh": 3, "max": 4}` and `clamp_effort_level(requested: Optional[str], allowed: Optional[list[str]]) -> Optional[str]` returning the highest level in `allowed` whose rank is `<= EFFORT_RANK[requested]`, or `None` if `requested`/`allowed` is falsy or no such level exists. This intentionally does not assume `allowed` is contiguous — e.g. `allowed=["low","medium","high","max"]` with `requested="xhigh"` clamps to `"high"`, never jumping up to `"max"`.
- Modify: `config/llms/llm-azure-config.yaml:138,159` — rename `reasoning: true` → `effort: ["low","medium","high"]` on the `o3-2025-04-16`/`o4-mini-2025-04-16` entries (OpenAI o-series `reasoning_effort` has no `xhigh`/`max` tier).
- Modify: `src/codemie/enterprise/litellm/models.py:93-102` — build `features.effort` from `model_info.get("supports_output_config")` (gate) plus `supports_xhigh_reasoning_effort`/`supports_max_reasoning_effort` (ceiling): `["low","medium","high"]` plus `"xhigh"` plus `"max"` as each flag is truthy, else `None`. Also correct the existing `features.temperature` computation: AND it with `model_info.get("supports_sampling_params") is not False` — today it's set purely from `"temperature" in supported_openai_params`, which `claude-sonnet-5`/`claude-opus-4-7` satisfy even though both have `supports_sampling_params: False` (confirmed, §10.2/§10.3); without this fix those two models would send `temperature` into a request path that unconditionally raises `BadRequestError` for it.
- Test: `tests/codemie/configs/test_llm_config.py`, `tests/enterprise/litellm/test_models.py`.

**Interfaces:**
- Produces: `LLMFeatures.effort: Optional[list[str]]` (default `None`), `clamp_effort_level()`, read by Tasks 2 and 3.

- [ ] **Step 1 (test-first):** In `test_llm_config.py`: assert `LLMFeatures().effort is None`; assert `clamp_effort_level("xhigh", ["low","medium","high","max"]) == "high"` (skips the gap, doesn't jump to `max`); assert `clamp_effort_level("high", ["low","medium","high","xhigh","max"]) == "high"`; assert `clamp_effort_level("low", None) is None`. In `test_models.py`, mirroring the existing `test_maps_features`/`test_handles_missing_features` style: add a case with `supports_output_config=True, supports_max_reasoning_effort=True` ⇒ `result.features.effort == ["low","medium","high","max"]`; a case with `supports_output_config` absent ⇒ `features.effort is None`; a case with `"temperature"` in `supported_openai_params` but `supports_sampling_params=False` ⇒ `features.temperature is False`.
- [ ] **Step 2:** Run the two test files, confirm all new assertions fail.
- [ ] **Step 3:** Make the edits above.
- [ ] **Step 4:** Run, confirm pass. Also run the existing YAML-loading test(s) in `test_llm_config.py` to confirm the renamed YAML key still loads without error.
- [ ] **Step 5:** Commit.

Test-first: yes — `LLMFeatures().effort` defaults `None`; `clamp_effort_level` clamps down to the nearest declared level without skipping up over a gap; `supports_output_config`/`supports_sampling_params` drive `features.effort`/`features.temperature` correctly, including the `supports_sampling_params=False` case `get_model_info()` would otherwise miss.

---

### Task 2: Enterprise LiteLLM path — thread, clamp, and correctly source `reasoning_effort` + `temperature`

**Scope correction:** Filtering can no longer be a boolean on/off — Anthropic's own `_apply_output_config` validation raises `BadRequestError` *unconditionally* (no `drop_params` escape hatch) when a request's effort exceeds the model's declared ceiling (§10.4), and CodeMie never sets `litellm.drop_params` anywhere (confirmed by grep). Every requested `effort` value must be clamped to the model's ceiling via Task 1's `clamp_effort_level` *before* it reaches `get_litellm_chat_model`, not passed through raw.

**Files:**
- Modify: `src/codemie/enterprise/litellm/llm_factory.py:224-327` (`create_litellm_chat_model`, `_build_chat_model_base_args`) — add `effort: Optional[str] = None` param, pass as `reasoning_effort` key in the base args dict.
- Modify: `src/codemie/enterprise/litellm/llm_factory.py:1035-1073` (`_filter_litellm_params`) — replace the boolean effort check with `filtered["reasoning_effort"] = clamp_effort_level(filtered.get("reasoning_effort"), llm_model_details.features.effort)`, popping the key when the result is `None`.
- Modify: `src/codemie/enterprise/litellm/llm_factory.py:1076-1133` (`get_litellm_chat_model`) — add `effort` passthrough param.
- Modify: `litellm_config.yaml` — add a `model_info:` block to the existing `bedrock/us.anthropic.claude-sonnet-5` and `bedrock/eu.anthropic.claude-opus-4-7` entries: `supports_output_config: true`, `supports_xhigh_reasoning_effort: true`, `supports_max_reasoning_effort: true`, `supports_sampling_params: false`. These four keys are silently dropped by `litellm.get_model_info()` in this pinned version (§10.1), so the proxy's own computed response can't supply them; the config override is the only reliable source and takes precedence per the existing merge rule (§9.2).
- Test: `tests/enterprise/litellm/test_llm_factory.py`.

**Interfaces:**
- Consumes: `LLMFeatures.effort`, `clamp_effort_level()` (Task 1).
- Produces: `get_litellm_chat_model(..., effort: Optional[str] = None, ...)`, consumed by Task 3.

- [ ] **Step 1 (test-first):** Mirror the existing mocked-`create_litellm_chat_model`/`_filter_litellm_params` tests (which already set `mock_model_details.features.temperature = True`): (a) `features.effort = ["low","medium","high","max"]`, request `effort="xhigh"` ⇒ captured kwargs show `reasoning_effort="high"` (clamped, not raised, not passed through raw); (b) same `features.effort`, request `effort="max"` ⇒ `reasoning_effort="max"`; (c) `features.effort = None`, request `effort="high"` ⇒ `reasoning_effort` absent from captured kwargs.
- [ ] **Step 2:** Run, confirm failure (`TypeError: unexpected keyword argument 'effort'`).
- [ ] **Step 3:** Make the edits above.
- [ ] **Step 4:** Run, confirm pass.
- [ ] **Step 5:** Commit.

Test-first: yes — an over-ceiling request clamps to the model's nearest declared level instead of being passed through raw or raising; `features.effort=None` drops any requested value even when `effort` was passed in.

---

### Task 3: Direct-provider path — `get_llm_by_credentials`/`get_anthropic_llm`, plus Bedrock-hosted Claude effort forwarding (scope correction, now per-model ceilings)

**Scope correction — research findings:** The original draft of this task added `effort` to `get_vertex_llm`/`get_bedrock_llm` for call-signature parity only, never forwarding it, on the premise that the ticket's provider scope was "direct Anthropic + enterprise LiteLLM only." Catalog inspection then showed no model in `config/llms/*.yaml` uses `provider: "anthropic"` — every Claude model (`claude-4-5-haiku`, `claude-4-5-sonnet`, `claude-sonnet-4-6`, `claude-opus-4-5-20251101`, `claude-opus-4-6-20260205`) is `aws_bedrock`-hosted, and no Vertex-hosted Claude model exists. A plan that never forwards `effort` on the Bedrock path would therefore implement the ticket's primary intent for zero real deployed models. Per user confirmation, scope is now extended to forward `effort` to Bedrock-hosted Claude; Vertex stays parity-only (genuinely no Vertex Claude models exist, and Gemini doesn't claim effort support).

Investigated the pinned `langchain-aws` 1.4.3 package directly (`ChatBedrockConverse` in `bedrock_converse.py`):
- No first-class `effort` kwarg exists on `ChatBedrockConverse`.
- Its only discrete low/medium/high "reasoning effort" concept is `additional_model_request_fields.reasoningConfig.maxReasoningEffort`, validated by `_validate_nova_reasoning_config` — which explicitly short-circuits unless `base_model.startswith("amazon.nova-2")`. This does **not** apply to Claude.
- Claude's own extended-thinking/effort configuration is passed through `additional_model_request_fields` verbatim in Anthropic's own native Messages-API shape — the package's own docstring example uses `additional_model_request_fields={"thinking": {"type": "enabled", "budget_tokens": N}}`, matching Anthropic's native parameter name exactly (not a Bedrock-specific key).
- `langchain_anthropic.ChatAnthropic` (pinned 1.4.0, same major used by the direct path) merges its own `effort` field into that identical native shape: `output_config["effort"] = self.effort`, typed `Literal["max","high","medium","low"]` — no `"xhigh"`.
- Conclusion: the Bedrock-hosted Claude path should forward `effort` as `additional_model_request_fields={"output_config": {"effort": effort}}`, reusing Claude's native request shape rather than inventing a Bedrock-specific one or routing through Nova's unrelated `maxReasoningEffort` field — with the forwarded value **clamped to that specific model's own declared ceiling list** (Task 1's `clamp_effort_level`) rather than a single hardcoded value set shared by every model.

**Per-model ceiling (technical-analysis.md §10.2, read directly, not re-derived):** `claude-4-5-haiku` and `claude-4-5-sonnet` lack `supports_output_config` entirely (confirmed absent, not `False`) — they get **no `features.effort` declaration at all** and keep working via `temperature`/the legacy `thinking` shape exactly as today, no regression. The other three qualify: `claude-sonnet-4-6` → `["low","medium","high","max"]` (no `xhigh`); `claude-opus-4-5-20251101` → `["low","medium","high"]` (no `xhigh`/`max`); `claude-opus-4-6-20260205` → `["low","medium","high","max"]` (no `xhigh`).

**Files:**
- Modify: `src/codemie/core/dependecies.py:271-307` (`get_llm_by_credentials`) — add `effort: Optional[str] = None` param, forward to `get_litellm_chat_model` and `get_llm_by_credentials_raw`.
- Modify: `src/codemie/core/dependecies.py:409-435` (`get_llm_by_credentials_raw`) — add `effort` param, forward uniformly to `get_vertex_llm`/`get_bedrock_llm`/`get_anthropic_llm`.
- Modify: `src/codemie/core/dependecies.py:466-505` (`get_vertex_llm`) — add `effort: Optional[str] = None` param for call-signature parity only; never forwarded to `ChatVertexAI`/`ChatAnthropicVertex` (no Vertex-hosted Claude model exists in the catalog).
- Modify: `src/codemie/core/dependecies.py:508-530` (`get_bedrock_llm`) — add `effort: Optional[str] = None` param. Compute `clamped = clamp_effort_level(effort, llm_model_details.features.effort)`; when `clamped is not None`, merge `{"output_config": {"effort": clamped}}` into the `additional_model_request_fields` dict already being built from `client_headers` (without clobbering existing keys); otherwise leave `additional_model_request_fields` exactly as today.
- Modify: `src/codemie/core/dependecies.py:533-555` (`get_anthropic_llm`) — add `effort` param; compute `clamped = clamp_effort_level(effort, llm_model_details.features.effort)`; pass `effort=clamped` to `ChatAnthropic(...)` when not `None`, otherwise omit it.
- Modify: `config/llms/llm-aws-config.yaml` — add a `features:\n  effort: [...]` block to exactly the **three** qualifying Claude entries at lines 29, 59, 72 (`claude-sonnet-4-6` → `["low","medium","high","max"]`, `claude-opus-4-5-20251101` → `["low","medium","high"]`, `claude-opus-4-6-20260205` → `["low","medium","high","max"]`); none of these currently declare a `features:` block, so other `LLMFeatures` defaults are unaffected. **Do not touch** `claude-4-5-haiku`/`claude-4-5-sonnet` (lines 3, 16) — they get no `effort` declaration.
- Test: `tests/codemie/core/test_dependencies_tagging_headers.py` (new `TestAnthropicEffort` and `TestBedrockEffort` classes, mirroring the existing `TestAnthropicTaggingHeaders`/`TestVertexTaggingHeaders` mock style — `TestBedrockEffort` patches `langchain_aws.ChatBedrockConverse` and `codemie.core.dependecies.get_bedrock_runtime_client`).

**Interfaces:**
- Consumes: `LLMFeatures.effort`, `clamp_effort_level()` (Task 1).
- Produces: `get_llm_by_credentials(..., effort: Optional[str] = None, ...)`, consumed by Task 5.

- [ ] **Step 1 (test-first):**
  - `TestAnthropicEffort`: (a) `features.effort = ["low","medium","high","max"]`, call `get_anthropic_llm(llm_model_details=model, effort="xhigh")`, assert `mock_anthropic.call_args[1]["effort"] == "high"` (clamped, not dropped, not raw); (b) `features.effort = ["low","medium","high"]`, `effort="max"` ⇒ `mock_anthropic.call_args[1]["effort"] == "high"`; (c) `features.effort = None`, `effort="medium"` ⇒ `"effort" not in mock_anthropic.call_args[1]`.
  - `TestBedrockEffort`: (d) `features.effort = ["low","medium","high","max"]`, call `get_bedrock_llm(llm_model_details=model, effort="high")`, assert `mock_bedrock.call_args[1]["additional_model_request_fields"] == {"output_config": {"effort": "high"}}`; (e) `features.effort = ["low","medium","high"]`, `effort="max"` ⇒ clamped to `{"output_config": {"effort": "high"}}`; (f) `features.effort = None`, `effort="medium"` ⇒ no `output_config` key; (g) model with a pre-existing `client_headers`-derived `additional_model_request_fields` entry plus `effort="high"` (with `features.effort` including `"high"`) ⇒ both the original key and the new `output_config.effort` key are present (no clobbering); (h) `claude-4-5-haiku`/`claude-4-5-sonnet` fixtures (`features.effort = None` by construction, per Task 1/3's exclusion) with any requested `effort` ⇒ no `output_config` key added on either `get_anthropic_llm` or `get_bedrock_llm`.
- [ ] **Step 2:** Run, confirm failure (`TypeError: unexpected keyword argument 'effort'` for both classes).
- [ ] **Step 3:** Make the edits above.
- [ ] **Step 4:** Run, confirm pass.
- [ ] **Step 5:** Commit.

Test-first: yes — `effort` requests above a model's declared ceiling clamp to the nearest declared level on both `ChatAnthropic` and `ChatBedrockConverse` calls, never raising and never passing through raw; `features.effort=None` (including the explicit `claude-4-5-haiku`/`claude-4-5-sonnet` exclusion) drops any requested value on both paths; an existing Bedrock `additional_model_request_fields` entry is never clobbered by the new key.

---

### Task 4: `LLMParams` dataclass

**Files:**
- Modify: `src/codemie/core/router_chat_model.py:135-141` (`LLMParams`) — add `effort: str | None`.
- Modify: `src/codemie/core/router_chat_model.py:300-308` — pass `effort=llm_params.effort` at both `get_llm_by_credentials(...)` call sites.
- Test: `tests/codemie/core/test_router_chat_model.py`.

**Interfaces:**
- Consumes: `get_llm_by_credentials(effort=...)` (Task 3).
- Produces: `LLMParams(temperature, top_p, effort)`, consumed by Task 5.

- [ ] **Step 1 (test-first):** Mirror the existing `build_chat_model_for` test (lines ~344-356, which asserts `called_with` includes `temperature`/`top_p`): construct `LLMParams(temperature=0.2, top_p=None, effort="high")` and assert `called_with["effort"] == "high"`.
- [ ] **Step 2:** Run, confirm failure (`TypeError: __init__() got an unexpected keyword argument 'effort'`).
- [ ] **Step 3:** Make the edits above.
- [ ] **Step 4:** Run, confirm pass.
- [ ] **Step 5:** Commit.

Test-first: yes — `LLMParams(..., effort="high")` propagates `effort="high"` into the `get_llm_by_credentials` call captured by `build_chat_model_for`.

---

### Task 5: Agent runtime classes (`AIToolsAgent`, `LangGraphAgent`)

**Files:**
- Modify: `src/codemie/agents/assistant_agent.py:162-163,184-185,330-331` (`AIToolsAgent.__init__`/`_initialize_llm`) — add `effort` constructor param, `self.effort`, forward to `get_llm_by_credentials`.
- Modify: `src/codemie/agents/langgraph_agent.py:147-148,182-183,633` (`LangGraphAgent.__init__`) — same, forward via `LLMParams(temperature=self.temperature, top_p=self.top_p, effort=self.effort)`.
- Test: `tests/codemie/agents/test_assistant_agent/test_initialize_llm.py`, `tests/codemie/agents/test_langgraph_agent_initialize_llm.py`.

**Interfaces:**
- Consumes: `get_llm_by_credentials(effort=...)` (Task 3), `LLMParams(..., effort=...)` (Task 4).
- Produces: `AIToolsAgent(effort=...)`, `LangGraphAgent(effort=...)`, consumed by Task 7.

- [ ] **Step 1 (test-first):** In both existing `_initialize_llm` test files, mirror the current temperature/top_p assertions: construct the agent with `effort="high"`, assert the mocked `get_llm_by_credentials`/`LLMParams` call received `effort="high"`.
- [ ] **Step 2:** Run, confirm failure.
- [ ] **Step 3:** Make the edits above.
- [ ] **Step 4:** Run, confirm pass.
- [ ] **Step 5:** Commit.

Test-first: yes — constructing either agent class with `effort="high"` results in `effort="high"` reaching the mocked LLM-construction call.

---

### Task 6: REST + workflow DTOs

**Files:**
- Modify: `src/codemie/rest_api/models/assistant.py:337,628,667,1270,1470-1475` — add `effort: Optional[Literal["low","medium","high","xhigh","max"]] = None` next to each existing `temperature` field (the one at 1470 keeps a `description=`, matching its existing style).
- Modify: `src/codemie/core/workflow_models/workflow_models.py:57-58` (`WorkflowAssistant`) — add the same field; no new validator needed (the `Literal` type already rejects invalid strings — a hand-rolled validator mirroring `validate_temperature` at lines 83-96 would be redundant).
- Test: `tests/codemie/rest_api/models/test_map_assistant_request_metadata.py` (mirrors its existing `temperature=0.7` pattern at lines ~171-188).

**Interfaces:**
- Produces: `.effort` field on `Assistant`, `AssistantConfiguration`, `VirtualAssistantChatRequest`, `WorkflowAssistant`, etc. — consumed by Task 7.

- [ ] **Step 1 (test-first):** Mirror the existing `temperature=0.7` / `assistant.temperature == 0.7` test: set `effort="high"` on the request, assert `assistant.effort == "high"` after mapping; add one case passing `effort="invalid"` and assert Pydantic raises on construction (proves the `Literal` constraint without a custom validator).
- [ ] **Step 2:** Run, confirm failure.
- [ ] **Step 3:** Add the field to each DTO listed above.
- [ ] **Step 4:** Run, confirm pass.
- [ ] **Step 5:** Commit.

Test-first: yes — `effort="high"` round-trips through request-to-assistant mapping; `effort="invalid"` is rejected by the `Literal` type at construction.

---

### Task 7: Wire DTOs into the agent-build factory

**Files:**
- Modify: `src/codemie/service/assistant_service.py:615-616` (`build_agent`'s `agent_kwargs`) — add `"effort": assistant.effort,`.
- Modify: `src/codemie/service/assistant_service.py:724-725` (`_load_and_configure_workflow_assistant`) — add `if workflow_assistant.effort is not None: assistant.effort = workflow_assistant.effort`, mirroring the existing `temperature` guard immediately above it.
- Test: existing `build_agent`/workflow-assistant service tests (whichever file already asserts `agent_kwargs["temperature"]`/the workflow temperature override — extend in place).

**Interfaces:**
- Consumes: `Assistant.effort`/`WorkflowAssistant.effort` (Task 6), `AIToolsAgent(effort=...)`/`LangGraphAgent(effort=...)` (Task 5).

- [ ] **Step 1 (test-first):** Mirror whichever existing test asserts `agent_kwargs["temperature"] == assistant.temperature`: set `assistant.effort = "high"`, assert `agent_kwargs["effort"] == "high"`. For the workflow path, mirror the temperature-override test: `workflow_assistant.effort = "max"` ⇒ `assistant.effort == "max"`; `workflow_assistant.effort = None` with a pre-existing `assistant.effort = "high"` ⇒ stays `"high"` (not clobbered).
- [ ] **Step 2:** Run, confirm failure.
- [ ] **Step 3:** Make the two edits above.
- [ ] **Step 4:** Run, confirm pass.
- [ ] **Step 5:** Commit.

Test-first: yes — `assistant.effort` reaches `agent_kwargs["effort"]`; a `None` workflow override never clobbers an existing `assistant.effort`.

---

## Negative-constraint pass

- No UI/frontend work → every task above touches only `src/codemie/**`, `config/llms/**`, `litellm_config.yaml`, `tests/**`; no `ui`/frontend directory is referenced.
- New hand-added per-provider effort declarations are limited to three named groups → Task 1 renames the two existing OpenAI `o3`/`o4-mini` entries; Task 3 adds a `features.effort` level list to exactly the **three** qualifying Bedrock-hosted Claude entries named above; Task 2 adds a `model_info:` override to exactly the two LiteLLM-proxy-only entries. `claude-4-5-haiku`/`claude-4-5-sonnet` are explicitly excluded (lack `supports_output_config` entirely). No Vertex-provider Claude YAML entry gains an `effort` flag (none exist in the catalog), and no other provider/model gains one.
- Silent handling, never a validation error, for unsupported `effort`/`temperature` → Tasks 2 and 3 drop `temperature` and **clamp** (not raw-pass or raise) `effort` to the model's declared ceiling, on both the direct-Anthropic and Bedrock-hosted-Claude branches. This explicitly covers the over-ceiling case Anthropic's own validation would otherwise raise on unconditionally (§10.4), not just the "model has zero effort support" case. Task 6's `Literal` rejection is a distinct, intentional case (malformed *input*, not an unsupported-by-model value) and does not contradict this.
- No mandatory temperature-dedup refactor → Task 6 adds `effort` next to each existing `temperature` field without restructuring the DTOs.
- No naming collision with the CLI-analytics `effort` field or the Switchyard `reasoning` concept → no task touches `repository/cli_analytics/*` or `enterprise/switchyard/*`.
- No Vertex Claude `effort` wiring → Task 3 adds the `effort` parameter to `get_vertex_llm`'s signature for call parity only and never forwards it (confirmed: no Vertex-hosted Claude model exists in the catalog, and Gemini doesn't claim effort support). Bedrock-hosted Claude models are explicitly in scope per the correction above.
- No GPT-5 request-dependent temperature-gating rule is implemented → GPT-5 is out of this ticket's core scope; no task touches a GPT-5 model entry, and every temperature-drop check added or corrected by Tasks 1–3 is a static per-model check, never conditioned on the request's `effort`/`reasoning_effort` value.
