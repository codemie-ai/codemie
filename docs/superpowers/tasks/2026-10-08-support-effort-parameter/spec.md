# Spec: `effort` generation parameter (EPMCDME-13335)

Status: retroactive. Written after plan.md was approved and tasks 1-6 were committed; it records the settled design, it does not reopen it. Size: L (23/36).

## Problem

Newer Anthropic models (claude-sonnet-5, claude-opus-4-7) reject `temperature`, and CodeMie has no way to send Anthropic's `effort` control. Anthropic also raises on an over-ceiling `effort` regardless of LiteLLM `drop_params` (technical-analysis.md 10.4), and CodeMie never sets `drop_params`. Today users hit provider 400s, or cannot use effort at all.

## Scope

Backend only. The separate codemie-ui repo owns UI: showing or hiding settings, help text, and the token-cost warning for `xhigh`/`max`.

Providers in scope:
- Direct Anthropic (`get_anthropic_llm`).
- Bedrock-hosted Claude (`get_bedrock_llm`).
- Any enterprise LiteLLM-path model that LiteLLM reports as supporting effort.
- Extended by `docs/superpowers/tasks/2026-10-27-effort-all-reasoning-models/spec.md`: LiteLLM models reporting `supports_reasoning` (GPT-5.x, o-series, Gemini, others), the Azure/DIAL path (`reasoning_effort` on `AzureChatOpenAI`), and Vertex Gemini (`thinking_budget`).

## Design

### API shape
- DTO and workflow field `effort: Optional[Literal["low","medium","high","xhigh","max"]] = None`, added beside every existing `temperature` field in `rest_api/models/assistant.py` and in `WorkflowAssistant`. The `Literal` is the only validation (malformed input is rejected at construction).
- On the LiteLLM/OpenAI-style wire the same value is sent as `reasoning_effort`.

### Capability model
- `LLMFeatures.effort` is `Optional[list[level]]`, default `None`: the ordered levels the model allows, i.e. a ceiling, not a boolean. `None` or empty means no effort support.
- `clamp_effort_level(requested, allowed)` in `configs/llm_config.py` returns the highest allowed level whose rank is at or below the requested rank (`low<medium<high<xhigh<max`), else `None`. It never jumps up across a gap: requesting `xhigh` against `[low,medium,high,max]` yields `high`.

### Capability sources
- With LiteLLM, the proxy's `model_info` is the only source of truth for capability flags: LiteLLM's built-in model map overlaid by the `model_info:` block of the deployment in `litellm_config.yaml`. CodeMie does not read `litellm.model_cost` and adds no effort override of its own. Without LiteLLM, the static YAML catalog below is used.
- In the pinned litellm==1.99.2, `litellm.get_model_info()` drops `supports_sampling_params` and `supports_output_config`, so a model that needs them declares them in its `model_info:` block.
- Static catalog (`config/llms/*.yaml`):
  - `claude-sonnet-4-6` and `claude-opus-4-6-20260205` declare `[low,medium,high,max]`.
  - `claude-opus-4-5-20251101` declares `[low,medium,high]`.
  - `claude-4-5-haiku` and `claude-4-5-sonnet` declare nothing. They have only a thinking budget and no native effort, so they keep working through `temperature`.
  - The stale `reasoning: true` flags on azure o3/o4-mini are renamed to `effort: [low,medium,high]`.
- Enterprise path (`enterprise/litellm/models.py`):
  - `features.effort` is derived from `supports_output_config` (gate) plus `supports_xhigh_reasoning_effort` and `supports_max_reasoning_effort`. Because the proxy's computed info loses `supports_output_config`, a `supports_xhigh_reasoning_effort` or `supports_max_reasoning_effort` flag also opens the gate: they are the signals LiteLLM itself uses to accept `output_config.effort`. Widened by `docs/superpowers/tasks/2026-10-27-effort-all-reasoning-models/spec.md`: `supports_reasoning` also opens the gate, except for Claude without `supports_output_config`; Gemini is capped at `high`.
  - `features.temperature` additionally requires `supports_sampling_params is not False`.
  - claude-sonnet-5 and claude-opus-4-7 get a `model_info:` override in `litellm_config.yaml`, because the proxy's computed info loses these keys.

### Filtering and forwarding (silent, never raised)
- Unsupported combinations are filtered or clamped silently, matching how `temperature` is treated today.
- Enterprise: `_filter_litellm_params` clamps `reasoning_effort` against `features.effort` and removes the key when the result is `None`.
- Direct and Bedrock:
  - `get_anthropic_llm` passes the clamped value to `ChatAnthropic(effort=...)` and omits the kwarg when `None`. The SDK type has no `xhigh`.
  - `get_bedrock_llm` merges `{"output_config": {"effort": clamped}}` into `additional_model_request_fields` without clobbering the existing `client_headers`-derived content.
  - `get_vertex_llm` maps Gemini effort to `thinking_budget` (see `docs/superpowers/tasks/2026-10-27-effort-all-reasoning-models/spec.md`). The Vertex-hosted Claude branch still never forwards it.
- Threading: `effort` goes through `get_llm_by_credentials` and `_raw`, `LLMParams`, `AIToolsAgent`, `LangGraphAgent`, and `AssistantService.build_agent`.
- Workflow override: a workflow assistant with `effort=None` must not overwrite an already-set `assistant.effort`, mirroring the existing `temperature` guard.

### Frontend contract
`v1/llm_models` already serializes the full `LLMModel`, including `LLMFeatures`. The new `features.effort` list and the corrected `features.temperature` reach the UI with no new endpoint.

## Acceptance criteria

1. `effort` accepts exactly low|medium|high|xhigh|max on every DTO that carries `temperature`, and on `WorkflowAssistant`. Any other value is rejected at DTO construction.
2. `LLMFeatures().effort is None`; `clamp_effort_level` clamps down to the nearest declared level, never up, and returns `None` for falsy inputs or when nothing is at or below the request.
3. Enterprise path: a requested level above a model's ceiling is sent as `reasoning_effort` at the clamped level. A model with `features.effort` unset gets no `reasoning_effort` key.
4. Direct Anthropic: `ChatAnthropic` receives the clamped `effort`, never `xhigh`. With no declaration the kwarg is absent.
5. Bedrock Claude: `additional_model_request_fields` carries `output_config.effort` at the clamped level, preserves pre-existing keys, and is unchanged when `features.effort` is unset.
6. claude-4-5-haiku and claude-4-5-sonnet never gain an `effort` declaration or an `output_config` kwarg, whatever the caller requests.
7. Dynamic detection: `features.effort` is derived from the proxy `model_info` per the Capability sources section. A Claude model with none of `supports_output_config`, `supports_xhigh_reasoning_effort` and `supports_max_reasoning_effort` gets `None` without a crash.
8. `features.temperature` is `False` when `supports_sampling_params is False`, even if `temperature` is in `supported_openai_params`. Temperature is therefore never sent to claude-sonnet-5 or claude-opus-4-7.
9. The azure o3/o4-mini `reasoning: true` flags are replaced by `effort: [low,medium,high]`. No `reasoning` key remains in `config/llms`, and the YAML catalog loads.
10. `effort` reaches both agent classes through `LLMParams` and `get_llm_by_credentials` (tasks 1-6, done).
11. `AssistantService.build_agent` passes `assistant.effort` into the agent kwargs. A workflow override with a non-None `effort` replaces `assistant.effort`; a `None` override leaves an existing value intact.
12. Models without effort support, and requests that omit `effort`, produce byte-identical request kwargs to before. No new keys appear and `temperature` behavior is unchanged.

## Non-goals

- Any UI behavior: setting visibility, help text, the `xhigh`/`max` token-consumption warning.
- GPT-5-family request-dependent temperature gating (temperature kept only when effort is unset or `none`). No GPT-5 entry is touched by this spec; `docs/superpowers/tasks/2026-10-27-effort-all-reasoning-models/spec.md` later declares effort on GPT-5 entries without changing temperature.
- A litellm version upgrade.
- Backfilling claude-sonnet-5 and claude-opus-4-7 into the static `config/llms` catalog.
- Vertex-hosted Claude forwarding (none exist in the catalog).
- Any change to the CLI-analytics `effort` field (`repository/cli_analytics/*`) or the Switchyard `reasoning` concept (`enterprise/switchyard/*`).
- Refactoring the duplicated `temperature` DTO fields into a shared schema.
- Setting `litellm.drop_params`, or any new endpoint.
- Declaring effort on any model beyond the three groups named in Capability sources. Superseded by `docs/superpowers/tasks/2026-10-27-effort-all-reasoning-models/spec.md`, which declares it on GPT-5.x, o-series and Vertex Gemini entries.

## Open risks

- The deployed enterprise proxy is not this repo and `codemie-enterprise` is not installed locally. The `model_info:` override precedence was verified against the bundled open-source litellm proxy, not the real one.
- Ceilings come from litellm 1.99.2's cost map and hand-written YAML. They can go stale when providers change limits or models are added, and a catalog drift (e.g. a missing static entry) silently yields "no effort support".
- `medium` and `high` have no litellm capability flag, so any model with effort support is assumed to allow both. A model that does not would get a provider 400.
- Enterprise ceiling is derived from `supports_output_config` plus the xhigh/max flags. Models that expose `reasoning_effort` without `supports_output_config` (e.g. most OpenAI-style models via proxy) get no effort support until declared via `model_info:`. Resolved by `docs/superpowers/tasks/2026-10-27-effort-all-reasoning-models/spec.md`: `supports_reasoning` now opens the gate.
- Temperature suppression is static per model. If a model's `supports_sampling_params` is absent, the name-based heuristic in litellm is not mirrored, so temperature may still be sent to such a model.
- Sending both `temperature` and `effort` stays possible for models that support both. That is intended, but the UI must explain it.