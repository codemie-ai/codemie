# Headroom Integration (context compression)

Headroom compresses the messages sent to the model on each LangGraph model call to cut token usage.
A stored per-assistant config drives it, and a workflow or a workflow node can override it field by field.
Unconfigured assistants and workflows behave exactly as before.

Design: `docs/superpowers/specs/2026-10-08-headroom-per-assistant-workflow-design.md`.

## Architecture

```text
Assistant.headroom_config (HeadroomConfig | None, JSONB column assistants.headroom_config)
  -> AssistantService.build_agent
       resolve_headroom_config(assistant, ...)   # codemie.service.headroom.scope -> resolver.resolve
       -> ResolvedHeadroomConfig | None       # None = gate off, config absent or disabled
  -> LangGraphAgent(headroom_config=<resolved>)   # only set when not None
       build_headroom_pre_model_hook(resolved, llm_model, request_uuid)   # codemie.service.headroom.hook
       -> LAST hook in _compose_pre_model_hooks(...)
            single agent: history compaction, subagent task, image artifact, headroom
            supervisor:   strip handoff-back, strip subagent task, headroom
  -> Headroom sidecar (HTTP) or SDK compressor, chosen by HEADROOM_MODE; any failure fails open (original messages)
  -> savings > 0: RequestSummaryManager.add_headroom -> TokensUsage.headroom
       -> GeneratedMessage.headroom / WorkflowExecution.tokens_usage (JSON, no migration)
       -> metric headroom_compaction_usage + GET /assistants/metrics/{id}
```

- **Naming trap**: the assistant field `headroom_config` is the stored `HeadroomConfig | None`; the
  `LangGraphAgent` kwarg `headroom_config` is the resolved `ResolvedHeadroomConfig | None`.
- Import Headroom symbols from submodules (`codemie.service.headroom.models|resolver|scope|hook`); the package
  `__init__` re-exports nothing. `WorkflowHeadroomLayers` and `resolve_headroom_config` live in `scope.py`.
  The sidecar and SDK compressors, `strategies.py` and the shared `CompressOutcome` / `HeadroomCompressionError`
  (`base.py`) live in the `codemie.service.headroom.compressors` subpackage.
- `build_agent` passes the kwarg only inside the `LangGraphAgent` branch and only when resolve returns a
  config. `AIToolsAgent`, the Bedrock early return and remote A2A never receive it.
- Both gates live inside `resolve()`, so no handler or router checks them. A stored config is kept while
  a gate is off and ignored at runtime, as `hedging_config` is.
- Builtin subagent clones inherit the parent's config. Each sub-assistant builds its own `LangGraphAgent`
  via `build_agent(is_subagent=True)` and resolves its own config; the supervisor hook covers only the
  supervisor's own model calls.

## Settings

| Setting | Where | Purpose |
|---|---|---|
| `HEADROOM_MODE` | `src/codemie/configs/config.py` | The only switch: `off` (default), `sdk` or `sidecar` |
| `HEADROOM_SDK_ALLOW_MODEL_DOWNLOAD` | `config.py` | SDK mode only: lets the SDK download the Kompress model (default `HEADROOM_OFFLINE=0` instead of `1`), default `false` |
| `HEADROOM_SIDECAR_URL` | `config.py` | Sidecar base URL, default `http://localhost:8787` (a sidecar in the same pod); `docker-compose.yml` sets `http://headroom:8787` |
| `HEADROOM_PROXY_TOKEN` | `config.py` | Bearer token for the sidecar. Secret: never log it |
| `HEADROOM_TIMEOUT_SECONDS` | `config.py` | Bound on the blocking compression call, default `10` |
| `HEADROOM_MIN_TOKENS` | `config.py` | Skip compression below this size, default `1000` |
| `HEADROOM_AGGRESSIVE_TARGET_RATIO` | `config.py` | Keep fraction for `aggressive`; only where the ML compressor runs |
| `features:headroom` | `config/customer/customer-config.yaml` (`components`, `settings.enabled`) | Customer feature flag; a missing entry means disabled |

### Flag matrix

Effective compression needs a mode other than `off` **and** `features:headroom` enabled.

| `HEADROOM_MODE` | Result |
|---|---|
| `sidecar` | Sidecar over HTTP, whether or not LiteLLM is enabled |
| `sdk` | In-process SDK, whether or not LiteLLM is enabled; needs the `headroom-sdk` extra |
| `off` (default) | Headroom off |

The mode is read once per process (the compressor is cached), so changing it needs a restart. Add the
feature entry in the same shape as the neighbouring `features:*` components. The flag gates both modes:

```yaml
  - id: "features:headroom"
    settings:
      enabled: true
      name: "Headroom"
      description: "Compress large tool outputs and context with Headroom (sdk or sidecar) to reduce token usage"
```

## Precedence

Each field takes the first non-`None` of node, workflow, assistant, then the `HeadroomConfig` default
(node > workflow > assistant > off). Turning `enabled` on with nothing else gives both toggles on and
`balanced`. System messages are never compressed.

- `build_agent` (chat) resolves the assistant layer only: `resolve(None, None, stored)`.
- `build_agent_for_workflow` (LangGraph workflow node agents) resolves all three layers. The layers
  travel as one `WorkflowHeadroomLayers(node, workflow)` argument, filled by `initialize_assistant`
  from `WorkflowAssistant.headroom` and `AssistantExecutionOptions.headroom_workflow`.
- Inheritance is per field: a node block with only `strategy` keeps the workflow's `enabled`.
- A block with `strategy` but no `enabled` anywhere in the chain resolves OFF: `enabled` defaults to
  false. A node with `enabled: true` alone (for example an inline assistant) enables with defaults.
- A node `enabled: false` beats a workflow `enabled: true`.

### Workflow YAML

```yaml
headroom:                      # workflow level, applies to every assistant node
  enabled: true
  strategy: balanced
assistants:
  - id: analyst
    assistant_id: <uuid>
    headroom:                  # node level, wins over the workflow block
      strategy: aggressive
  - id: scribe
    system_prompt: "..."
    headroom:
      enabled: false           # opt this node out
```

Both blocks accept `enabled`, `compress_user_messages`, `compress_tool_outputs`, `strategy`
(`lossless`, `balanced`, `aggressive`); unknown keys inside them are rejected by the YAML schema.
The workflow block is parsed into `workflows.headroom_config` (JSONB, nullable); the node block lives
in the `assistants[]` entry of the YAML. The workflow list response (`WorkflowConfigListResponse`)
excludes `headroom_config`; read it from the detail endpoint.

## Strategies

| Strategy | Meaning | Prompt-cache impact |
|---|---|---|
| `lossless` | Deterministic, no information loss. Needs the sidecar run with `HEADROOM_LOSSLESS=1` | Cache-safe |
| `balanced` (default) | Lossy inline compression of older content | Can lower provider prompt-cache hits |
| `aggressive` | Same as `balanced` with a lower keep fraction where the ML compressor runs | Can lower provider prompt-cache hits |

`balanced` and `aggressive` rewrite older history, which can reduce provider prompt-cache hits.
Pick `lossless` when cache hit rate matters more than size.

**Prose needs the Kompress model.** Free-form text (user messages, prose in tool results) is compressed by the Kompress ML
model (`chopratejas/kompress-v2-base`); without it only structured content (JSON, logs, code) is reduced and the log
line reads `outcome=unchanged`. The sidecar downloads the model from HuggingFace on first start, so it needs
`HEADROOM_OFFLINE=0` (set in `docker-compose.yml`) and about 770 MiB of memory (`mem_limit: 2g`). With
`HEADROOM_OFFLINE=1` the sidecar logs `Kompress model not ready; requests will not be compressed`, and `aggressive`
behaves exactly like `balanced`. In an air-gapped deployment, pre-load the model into the sidecar image or a volume.

`aggressive` is very lossy: a 121-token paragraph came back as 37 tokens of fragments with `HEADROOM_AGGRESSIVE_TARGET_RATIO`
at its 0.2 default, against 99 tokens for `balanced`. Raise the ratio if the output is too degraded.

`compress_user_messages` covers human messages. `compress_tool_outputs` covers tool results fed back into context.
System prompts and earlier assistant replies are never compressed, whatever the toggles: users refer back to the
assistant's own words, and the hook sends plain strings, which bypass the SDK's default protection of assistant text
blocks, so the hook itself discards the compressed assistant text.

### Adding a strategy

`src/codemie/service/headroom/compressors/strategies.py` is the only place strategy values are named.

1. Add the value to `HeadroomStrategy` in `src/codemie/service/headroom/models.py`.
2. Handle it in `sidecar_config` and `sdk_compress_kwargs` in `compressors/strategies.py`.
3. Extend `tests/codemie/service/headroom/` and update this table and the UI help text.

No other module may compare against strategy values.

## SDK and sidecar modes

| | `sidecar` | `sdk` |
|---|---|---|
| Where it runs | Separate `headroom` container, HTTP `/v1/compress` | In the backend process (worker thread) |
| Package | Not imported by the backend | `headroom-ai`, imported lazily on first use, only in `sdk` mode |
| Install | Nothing extra in the backend image | Poetry extra `headroom-sdk`; `headroom-sdk-ml` adds Kompress (`transformers`, `onnxruntime`, `huggingface-hub`) |
| Image build | n/a | `docker build --build-arg INSTALL_HEADROOM_SDK=true` (or `ml`); default `false` |
| Prose, `balanced`/`aggressive` | Kompress in the sidecar image | Kompress only with `headroom-sdk-ml` plus a cached model or `HEADROOM_SDK_ALLOW_MODEL_DOWNLOAD=true` |

With the package missing in `sdk` mode the import failure is logged once and the SDK stays disabled for the process;
requests fail open. In `sidecar` or `off` mode `headroom` is never imported.

**Parity.** Both modes accept the same strategies and return the same `CompressOutcome`. The only difference is
Kompress: the sidecar image ships it; the SDK needs the `headroom-sdk-ml` extra (about 770 MiB RSS in the backend
process) and either a pre-cached model or `HEADROOM_SDK_ALLOW_MODEL_DOWNLOAD=true`. Without Kompress, SDK mode logs one
warning at first use and `balanced`/`aggressive` compress structured content only (`aggressive` equals `balanced`).
The `headroom-sdk-ml` extra raises the locked `huggingface-hub`, `onnxruntime` and `regex` versions for the whole
environment (Poetry locks one version per package).

## Savings, metric and analytics

The hook reports a saving only for the `compressed` outcome, recounted locally on the merged messages (the same
counter in both modes, so it does not overstate when the toggles discard parts of the compressor's output), and only
when `tokens_saved > 0`. It needs a request id: `LangGraphAgent` passes its `request_uuid`.

- **Metric** `headroom_compaction_usage` (`MetricName.HEADROOM_COMPACTION_USAGE`), emitted once per completed chat turn
  (`ConversationService`) and once per workflow execution (`WorkflowExecutionService`), never when nothing was saved.
  Attributes: `user_id`, `user_email`, `project`, `conversation_id`, `assistant_id`, `request_id`, `tokens_original`,
  `tokens_compacted`, `tokens_saved` (ints), `mode`, `strategy`, `calls`.
- **Analytics** under `/v1/analytics/headroom/`: `summary`, `by-strategy`, `timeseries`. They are gated by
  `features:headroom` (404 when it is off), not by `routingAnalytics`. There is no by-mode breakdown: a deployment
  runs one mode.
  - `summary` metrics: `tokens_original`, `tokens_compacted`, `tokens_saved`, `saved_percent`
    (`tokens_saved / tokens_original * 100`, two decimals, `0` when nothing was original), `request_count`,
    `session_count` and `mode`: the one mode seen in the period, `mixed` when more than one appears (for example during
    an sdk to sidecar migration), left out when there are no events.
  - `by-strategy` columns: `strategy`, `request_count`, `tokens_original`, `tokens_compacted`, `tokens_saved`,
    `saved_percent` (same formula, per row).
- **Persistence** is JSON only: `GeneratedMessage.headroom` and `WorkflowExecution.tokens_usage.headroom`; there is no
  Alembic migration.

## Deployment

1. Run the Alembic migrations before the new application version starts: `d5241114c3dd` (adds nullable JSONB
   `headroom_config` to both `assistants` and `workflows`).
2. Run the `headroom` sidecar service from `docker-compose.yml` (no published port; the `codemie`
   service gets `HEADROOM_SIDECAR_URL=http://headroom:8787`). Its `HEADROOM_PROXY_TOKEN` must equal the backend's.
   Locally it is opt-in, behind the `headroom` compose profile (also part of `full`): start it with
   `COMPOSE_PROFILES=app,headroom` and set `HEADROOM_PROXY_TOKEN` in `.env`. Without a token the sidecar exits at
   start-up ("Refusing to bind '0.0.0.0'"), so it is kept out of the base services.
3. Set `HEADROOM_MODE=sidecar` (or `sdk`) and enable `features:headroom`. `sdk` needs an image built with
   `INSTALL_HEADROOM_SDK=true|ml`; the sidecar service is not needed in that mode.
4. `headroom-ai` is an optional dependency (extra `headroom-sdk`) and the lock file changes. The MR description must
   ask a reviewer to post `/sanity` (dependency and Dockerfile change; see `.ai-run/guides/security/README.md`).
5. Merge: the commits `dcd28aaf9`, `ddf7559c9` and `ba6a40c74` lack the `EPMCDME-####:` prefix (review finding CR-008);
   reword or squash them when merging.

## Known limitations

- Only string content and lists of bare `{"type": "text", "text": ...}` blocks are rewritten. A message with an image,
  a `cache_control` marker or any other block kind keeps its original content; when that leaves nothing to apply the
  log line reads `outcome=not_applied` although `tokens_after` is lower, because that figure is the compressor's.

- The hook keeps the original messages when the reported `tokens_after` is not below `tokens_before`. The
  `tokens_before`/`tokens_after` span attributes come from the compressor, so they can overstate the saving: the
  compressed assistant replies (always) and tool messages (with `compress_tool_outputs` off) are discarded by the hook.
  The request-summary savings are recounted locally and are not affected.

- SDK mode sets `HEADROOM_OFFLINE`, `HEADROOM_BEACON`, `DO_NOT_TRACK` and `HEADROOM_CCR_BACKEND` in the process
  environment (the SDK reads them on every call). `HEADROOM_BEACON=off`, `DO_NOT_TRACK=1` and
  `HEADROOM_CCR_BACKEND=memory` are forced: an inherited different value is overridden with a warning naming the key.
  Only `HEADROOM_OFFLINE` keeps an operator-set value, so a preset `HEADROOM_OFFLINE=1` keeps the SDK from
  downloading Kompress.
- SDK mode without the `headroom-ai` package (an image built with `INSTALL_HEADROOM_SDK=false`) logs one error the
  first time the mode is resolved and then fails open on every request. `INSTALL_HEADROOM_SDK` accepts only
  `true`, `ml`, `false` or empty; any other value fails the image build.
- A blank `HEADROOM_MODE` counts as `off` and the value is case-insensitive. Events merged from several
  strategies or modes (workflow nodes, supervisor plus subagents) have no single label and are reported under the
  `mixed` row of `by-strategy`; the `summary` `mode` reads `mixed` when the period holds several modes.
- Without Kompress (no `headroom-sdk-ml` extra, or no model available) SDK mode compresses structured content only.
- SDK mode runs the compression in a worker thread and gives up after `HEADROOM_TIMEOUT_SECONDS`. A timed-out call
  keeps its worker until the SDK returns; at most four such workers exist.

- `headroom_config` is not versioned (same as `hedging_config`): a headroom-only edit creates no new
  version, and applying an old version keeps the master value.
- The hook is synchronous and blocking (up to `HEADROOM_TIMEOUT_SECONDS`) on every model call. It fails
  open: on any error the original messages go to the model.
- A2A-served assistants and the assistant health check call `build_agent` and compress when configured.
- Supervisor and sub-assistants compress independently; a mixed setup is expected.
- Workflow and node overrides apply only to the node's own agent. Sub-assistants and toolkit-built
  agents do not receive them; they compress from their saved assistant config.
- Typo'd keys outside the two new `headroom` objects (for example a misspelled top-level key) are
  silently ignored, as for the rest of the workflow YAML schema.
- Not supported: `AIToolsAgent`, Bedrock, remote A2A, structured-output compression, prompt-cache
  mitigation. These never compress, whatever the workflow or node says.

## API contract for UI and SDK repos

`POST/PUT /v1/assistants` accept, and `GET` responses return:

```json
"headroom_config": {
  "enabled": true,
  "compress_user_messages": true,
  "compress_tool_outputs": true,
  "strategy": "balanced"
}
```

- `null` or absent means not configured. On update, an absent field leaves the stored value unchanged.
- `strategy` is one of `lossless`, `balanced`, `aggressive`; any other value returns 422.
- `GET /v1/assistants/metrics/{conversation_id}` adds `headroom_tokens_saved` and `headroom_tokens_original`
  (assistant and workflow conversations). Both keys are absent unless tokens were saved: show the savings only
  when they are present.
- UI: show only when the `headroom` feature flag is on. Section "Context compression (Headroom)":
  enable toggle, "Compress user input", "Compress tool outputs", strategy select with help text that
  includes the prompt-cache note.
- SDK (`codemie-sdk-python`): add `HeadroomStrategy` and `HeadroomConfig` types and add
  `headroom_config` to the assistant create, update and response models.

## Verification

- `tests/codemie/rest_api/models/test_assistant_headroom_config.py`: request and model fields, 422 on
  an invalid strategy.
- `tests/codemie/migrations/test_add_headroom_config.py`: revision chain, `headroom_config` on `assistants` and `workflows`, single head.
- `tests/codemie/agents/test_langgraph_headroom_hook.py`: hook is last in both chains.
- `tests/codemie/service/headroom/test_factory.py`, `test_resolver.py`, `compressors/test_sdk_compressor.py`, `test_hook.py`: mode
  selection, gating, lazy SDK import, Kompress warning, savings capture.
- `tests/codemie/service/monitoring/test_headroom_monitoring_service.py`,
  `tests/codemie/service/analytics/handlers/test_headroom_handler.py`,
  `tests/codemie/rest_api/routers/test_analytics_headroom.py`: metric and analytics.
- `tests/codemie/rest_api/routers/test_assistant_metrics_headroom.py`: conversation usage fields.
- `tests/codemie/service/test_assistant_service_headroom.py`: `build_agent` wiring and gating.
- `tests/codemie/service/test_assistant_service_workflow_headroom.py`: `build_agent_for_workflow`
  precedence matrix and gating.
