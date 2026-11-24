# Headroom Context Compression per Assistant and Workflow — Design

Ticket: EPMCDME-15645. Date: 2026-10-08. Status: draft for review.
Research and rejected options: `docs/headroom/research-findings.md`.

## Goal

Let a user turn on Headroom context compression for an individual assistant, workflow, or workflow node, and choose what is
compressed and how aggressively. Assistants and workflows without a Headroom config behave exactly as today.

## Decisions

| # | Decision |
|---|---|
| D1 | Two runtime modes: **sidecar** (Headroom container, called over HTTP) and **SDK** (`headroom-ai` in-process). |
| D2 | Mode is chosen automatically: `is_litellm_enabled` → sidecar, otherwise SDK. No mode setting. |
| D3 | The backend calls Headroom itself (approach A). No LiteLLM guardrail is configured. |
| D4 | Integration point is a LangGraph `pre_model_hook`, appended last in the agent's hook chain. |
| D5 | Config levels: assistant, workflow, workflow node. Precedence **node > workflow > assistant > off**, merged field by field. |
| D6 | Config fields: `enabled`, `compress_user_messages`, `compress_tool_outputs`, `strategy`. |
| D7 | Every failure fails open: the call proceeds with the original messages. |
| D8 | This spec implements the backend. UI and CodeMie SDK changes follow the contract in the appendix. |

## 1. Config model

`src/codemie/service/headroom/models.py`:

```python
class HeadroomStrategy(str, Enum):
    LOSSLESS = "lossless"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"

class HeadroomConfig(BaseModel):
    """Stored on assistants and workflows. All fields required, with defaults."""
    enabled: bool = False
    compress_user_messages: bool = True
    compress_tool_outputs: bool = True
    strategy: HeadroomStrategy = HeadroomStrategy.BALANCED

class HeadroomOverride(BaseModel):
    """Workflow node override. Unset fields inherit."""
    enabled: bool | None = None
    compress_user_messages: bool | None = None
    compress_tool_outputs: bool | None = None
    strategy: HeadroomStrategy | None = None

@dataclass(frozen=True)
class ResolvedHeadroomConfig:
    compress_user_messages: bool
    compress_tool_outputs: bool
    strategy: HeadroomStrategy
```

Meaning of the toggles:

- `compress_user_messages` — the "input": human messages.
- `compress_tool_outputs` — the "output": tool results fed back into context.
- System messages and earlier assistant replies are **never** compressed. System prompts keep the provider prompt-cache
  prefix stable; assistant replies stay verbatim because users refer back to them ("follow step 4 of your plan") and
  the UI promises they are not compressed.

Turning `enabled` on with no other change gives the default: both toggles on, `balanced`.

### Storage

| Level | Field | Storage |
|---|---|---|
| Assistant | `headroom_config: HeadroomConfig \| None` on `AssistantRequest` and `AssistantBase` (`src/codemie/rest_api/models/assistant.py`) | JSONB via `PydanticType`, new alembic migration. Same pattern as `hedging_config`. |
| Workflow | top-level `headroom:` in workflow YAML → `WorkflowConfigBase.headroom_config: HeadroomOverride \| None` | Parsed in `workflow_config.py` next to `pool_config`; JSONB column, same migration. Typed as `HeadroomOverride` so that unset fields inherit from the assistant instead of being filled with defaults. |
| Workflow node | `headroom:` on an `assistants[]` YAML entry → `WorkflowAssistant.headroom: HeadroomOverride \| None` (`workflow_models.py:52`) | Lives in the YAML only. Applies to every state that references that assistant entry. |

`execution_config_schema.yaml` gains the `headroom` object at the top level and on `assistants[]` items, with the same field names,
`additionalProperties: false`, and `strategy` as an enum.

## 2. Resolution

`resolver.resolve(node: HeadroomOverride | None, workflow: HeadroomOverride | None, assistant: HeadroomConfig | None) -> ResolvedHeadroomConfig | None`

1. Return `None` if the platform setting `HEADROOM_ENABLED` is false, or feature flag `customer_config.is_feature_enabled("headroom")` is false.
2. For each field, take the first non-`None` value from: node, workflow, assistant, then the `HeadroomConfig` default.
   `enabled` resolves the same way, so a node can switch off a workflow that is on, or switch on a workflow that has no block.
3. Return `None` if the resolved `enabled` is false; otherwise a `ResolvedHeadroomConfig`.

Callers:

- Chat: `resolve(None, None, assistant.headroom_config)`.
- Workflow node: `build_agent_for_workflow` resolves `(workflow_assistant.headroom, workflow.headroom_config, assistant.headroom_config)`.
  `assistant` is the loaded saved assistant, or `None` for an inline YAML assistant.

Stored config is kept even while the flag is off. It is only ignored at runtime, as `hedging_config` is
(`assistant_handlers.py:1496`).

## 3. Runtime

### Units (`src/codemie/service/headroom/`)

| File | Responsibility |
|---|---|
| `models.py` | Types in section 1. |
| `resolver.py` | Section 2. |
| `strategies.py` | One table: strategy → sidecar `config` body and → SDK `CompressConfig` kwargs. The only place strategy values live. |
| `compressor.py` | `HeadroomCompressor` protocol: `compress(messages: list[dict], model: str, cfg: ResolvedHeadroomConfig) -> CompressOutcome`. `CompressOutcome(messages, tokens_before, tokens_after)`. |
| `sidecar_compressor.py` | Sync httpx `POST {HEADROOM_SIDECAR_URL}/v1/compress`, Bearer `HEADROOM_PROXY_TOKEN`, timeout `HEADROOM_TIMEOUT_SECONDS`. |
| `sdk_compressor.py` | Lazy `from headroom import compress`; sets `HEADROOM_OFFLINE=1`, `HEADROOM_BEACON=off`, `DO_NOT_TRACK=1`, `HEADROOM_CCR_BACKEND=memory` before import. |
| `factory.py` | `get_compressor()`: sidecar if `is_litellm_enabled`, else SDK. Process-wide singleton. |
| `hook.py` | `build_headroom_pre_model_hook(cfg, llm_model)`. |

### Strategy table (confirmed by the spike, findings section 7)

| Strategy | SDK `CompressConfig` | Sidecar `config` |
|---|---|---|
| `lossless` | `mode="agent"` | `{}` (`mode` unset; needs the sidecar to run with `HEADROOM_LOSSLESS=1`) |
| `balanced` | `mode=None`, `target_ratio=None` | `{"mode": "lossy_inline"}` |
| `aggressive` | `mode=None`, `target_ratio=HEADROOM_AGGRESSIVE_TARGET_RATIO` (`0.2`) | `{"mode": "lossy_inline", "target_ratio": 0.2}` |

Both paths also pass `compress_user_messages` from the resolved config.

- The 0.40.0 sidecar has no per-request lossless mode (`lossless` is a 400). The sidecar runs with `HEADROOM_LOSSLESS=1`, which makes an unset `mode`
  lossless-only (byte-exact folds for log, search and diff output; JSON arrays untouched), while `lossy_inline` stays lossy. `lossless` is therefore
  weaker than SDK `mode="agent"` on JSON tool output.
- `target_ratio` is the Kompress keep fraction. It has an effect only when Kompress can run (`onnxruntime` plus its model); otherwise `aggressive`
  behaves like `balanced`.
- SDK default mode emits `Retrieve more: hash=...` markers that the model cannot resolve; `sdk_compressor.py` swaps in a marker-free pipeline
  (findings section 7, item 3).
- Sidecar `compression_ratio` is `tokens_after / tokens_before`. `CompressOutcome` uses the token counts only.

### Hook behaviour per LLM call

1. Read `state["messages"]`, already processed by earlier hooks (history compaction runs first).
2. Count tokens locally (tiktoken). Below `HEADROOM_MIN_TOKENS` → return `{}`.
3. Convert to OpenAI dicts and call `get_compressor().compress(...)`.
4. If the result has a different message count than the input → fail open.
5. Build the output list by index, starting from the original `BaseMessage` objects:
   - System messages: keep the original.
   - Human messages: take the compressed `content` only if `compress_user_messages`.
   - Tool messages: take the compressed `content` only if `compress_tool_outputs`.
   - AI messages: keep the original.
   - Always keep the original `additional_kwargs`, `tool_calls`, `tool_call_id`, `id`, and `name`. Only `content` changes.
6. If nothing changed → `{}`; otherwise `{"llm_input_messages": [...]}`. Checkpointed history is never modified.

### Wiring

- `LangGraphAgent.__init__` takes `headroom_config: ResolvedHeadroomConfig | None = None` and builds the hook once.
- The hook is appended **last** in `_compose_pre_model_hooks(...)` in `_build_single_agent` (`langgraph_agent.py:351`) and in the supervisor
  graph (`langgraph_agent.py:312`).
- `AssistantService` passes the resolved config for chat. `build_agent_for_workflow` passes it for workflow nodes. Sub-assistants
  build their own `LangGraphAgent`, so they get their own config.
- With `headroom_config=None`, the hook is `None` and the hook chains are exactly as today.

### Out of scope

- LLM calls that do not run through `LangGraphAgent`: datasource processors, generators, workflow tool nodes, direct
  `get_llm_by_credentials` callers. A2A assistants (they do not run a local agent).
- Traffic that goes straight to LiteLLM (CLI proxy endpoints).
- Headroom's output shaper (model output verbosity and effort).

## 4. Failure handling

All of these log a warning without message content and return `{}`, so the call proceeds with the original messages:

- sidecar timeout, connection error, non-2xx response
- SDK `ImportError`. Logged once, after which the SDK compressor disables itself for the process.
- message-count mismatch
- any exception during conversion or mapping

A compression failure never raises into the agent.

## 5. Configuration

Added to the platform config (`src/codemie/configs/`), following `configuration-patterns.md`:

| Setting | Default | Purpose |
|---|---|---|
| `HEADROOM_ENABLED` | `false` | Kill switch. |
| `HEADROOM_SIDECAR_URL` | `http://headroom:8787` | Sidecar base URL. |
| `HEADROOM_PROXY_TOKEN` | — (secret) | Bearer token for the sidecar. Never logged. |
| `HEADROOM_TIMEOUT_SECONDS` | `10` | Sidecar request timeout. |
| `HEADROOM_MIN_TOKENS` | `1000` | Skip compression below this prompt size. |
| `HEADROOM_AGGRESSIVE_TARGET_RATIO` | `0.2` | `target_ratio` for `aggressive` (Kompress keep fraction; lower is more aggressive). |

Feature flag: `headroom` in customer config, which the UI uses to show the controls.

## 6. Security, observability, deployment

- **Security:** the sidecar is on the internal network only, with Bearer auth. Offline and telemetry opt-outs apply in both modes. No message
  content goes in logs or span attributes.
- **Observability:** OTel span `headroom.compress` with `codemie.headroom.mode` (`sidecar` / `sdk`), `codemie.headroom.strategy`,
  `codemie.headroom.tokens_before`, `codemie.headroom.tokens_after`, `codemie.headroom.outcome`
  (`compressed` / `unchanged` / `not_applied` / `skipped_small` / `failed_open`; `not_applied` means the compressor reported a saving but no message could take it), plus a matching structured log line.
  Duration is the span's own duration, so there is no separate `duration_ms` attribute.
- **Deployment:**
  - `docker-compose.yml` gains the `headroom` service from the prototype, image `ghcr.io/headroomlabs-ai/headroom:0.40.0`, with
    `HEADROOM_OFFLINE=1`, `HEADROOM_BEACON=off`, `DO_NOT_TRACK=1`, `HEADROOM_LOSSLESS=1`, `HEADROOM_HOST=0.0.0.0`, `HEADROOM_COMPRESS_ALLOW_REMOTE=1`,
    `HEADROOM_SKIP_UPSTREAM_CHECK=1`, `HEADROOM_PROXY_TOKEN`, resource limits, and a healthcheck on `GET /readyz`
    (`python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/readyz', timeout=5)"`, `start_period` of at least 30 s).
  - `litellm_config.yaml` gets **no** Headroom guardrail. The docs warn that adding one with `default_on: true` would compress traffic twice.
- **Dependency:** `headroom-ai = "0.40.0"` (pinned, no extras) in `pyproject.toml`. Because dependencies change, the MR description must ask a
  reviewer to post `/sanity`.
- **Prompt-cache trade-off:** `balanced` and `aggressive` rewrite older history and can lower provider prompt-cache hits. `lossless` is
  deterministic and cache-safe. This is stated in the docs and in the UI help text.

## 7. Spike (first plan task, no production code)

Record the results in `docs/headroom/research-findings.md` §7 and update the strategy table:

1. Accepted `config.mode` values on the 0.40.0 sidecar; choose the sidecar mapping for `lossless`.
2. Linux wheel availability for `headroom-ai` 0.40.0.
3. SDK `compress()` with no ML extras, offline.
4. A value for `HEADROOM_AGGRESSIVE_TARGET_RATIO`, and whether the beacon runs for SDK-only use.

If item 2 fails, SDK mode falls back to "not available" (the SDK compressor disables itself and logs once). Sidecar mode is unaffected.

## 8. Testing

Tests go under `tests/codemie/service/headroom/` and existing agent, workflow and API test locations, per `testing-patterns.md`.

| Area | Cases |
|---|---|
| resolver | precedence table; field-level merge; workflow `enabled: true` alone keeps the assistant's `strategy`; node `enabled: false` over workflow on; node on with no workflow block; kill switch off → `None`; flag off → `None` |
| strategies | each strategy → expected sidecar body and SDK kwargs |
| sidecar_compressor | request shape and auth header; success; timeout, connection error, 4xx, 5xx → fail open |
| sdk_compressor | args come from strategy; `ImportError` disables once; offline env vars set before import |
| hook | system untouched; disallowed types reverted; metadata kept; count mismatch → `{}`; below min tokens → no call; unchanged → `{}` |
| agent wiring | hook last in single-agent and supervisor chains; `None` config → chains unchanged (AC6) |
| workflow wiring | `build_agent_for_workflow` passes resolved config; YAML schema accepts valid and rejects invalid `headroom` blocks |
| API | assistant create, update and get round-trip `headroom_config`; invalid strategy → 422 |
| migration | upgrade and downgrade for both new columns |

Live check (`local-verification.md`): local stack with the sidecar. One assistant with Headroom on and one with it off. Check the span outcomes,
and that the off assistant's requests are unchanged.

## 9. Documentation

- `docs/headroom/research-findings.md` — AC1 (written).
- `.ai-run/guides/integration/headroom-integration.md` plus a row in the AGENTS.md guide table: architecture, settings, precedence, deployment,
  how to add a strategy, and user-facing meaning of each toggle and strategy (AC8).

## Appendix A — Contract for UI and CodeMie SDK

**Assistant API** (`POST/PUT /v1/assistants`, `GET` responses):

```json
"headroom_config": {
  "enabled": true,
  "compress_user_messages": true,
  "compress_tool_outputs": true,
  "strategy": "balanced"
}
```

`null` or absent means not configured. `strategy` is one of `lossless`, `balanced`, `aggressive`.

**Workflow YAML:**

```yaml
headroom:                 # workflow level, HeadroomOverride; unset fields inherit from the assistant
  enabled: true
  strategy: balanced
assistants:
  - id: researcher
    assistant_id: <saved assistant id>
    headroom:             # node level, HeadroomOverride; every field optional
      strategy: lossless
  - id: summarizer
    headroom:
      enabled: false
```

**UI checklist**

- Only shown when the `headroom` feature flag is on.
- Assistant form, section "Context compression (Headroom)":
  - enable toggle
  - "Compress user input" checkbox
  - "Compress tool outputs" checkbox
  - strategy select with help text, including the prompt-cache note
- Workflow settings: the same controls.
- Workflow node: Inherit / On / Off, plus optional per-field overrides. Inherited values are shown greyed out.

**CodeMie SDK checklist** (`codemie-sdk-python`)

- Add `HeadroomStrategy` and `HeadroomConfig` types.
- Add `headroom_config` to the assistant create, update and response models.
