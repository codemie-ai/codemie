# Headroom Integration — Research Findings

Ticket: EPMCDME-15645. Date: 2026-10-08.
Design: `docs/superpowers/specs/2026-10-08-headroom-per-assistant-workflow-design.md`.

Versions inspected (wheels extracted, source read, nothing executed): `litellm==1.99.2` (our pin), `headroom-ai==0.40.0` (PyPI latest).
The prototype Docker image `ghcr.io/headroomlabs-ai/headroom:0.39.1` was **not** inspected; server facts below come from the 0.40.0 wheel.

## 1. Prototype state

Branch `EPMCDME-15645-headroom-verify` (uncommitted, in a stash) ran Headroom as a LiteLLM `pre_call` guardrail with `default_on: true`:
a `headroom` service in `docker-compose.yml` and a `guardrails:` block in `litellm_config.yaml`. That applies to every call through the proxy and
cannot be scoped to an assistant or workflow, so it does not meet the ticket.

Earlier verification (Excel tool tests): Headroom does rewrite traffic, but the recurring test failures were traced to a fixture defect, not Headroom.

## 2. LiteLLM per-request guardrail (litellm 1.99.2)

- A `default_on: false` guardrail runs only when named in the request: top-level body `"guardrails": ["name"]` or `"metadata": {"guardrails": [...]}`
  (`litellm/proxy/litellm_pre_call_utils.py:2810-2820`, `litellm/integrations/custom_guardrail.py:595-612, 786-852`).
- Key/team guardrails are unioned with request guardrails; a client can add but not remove them (`litellm_pre_call_utils.py:2592-2633`).
  A team with `modify_guardrails: false` gets 403 on any request that names guardrails (`litellm/proxy/auth/auth_checks.py:654-711`).
- Guardrail dynamic `extra_body` is premium-only and the Headroom hook does not read it (`custom_guardrail.py:881-919`).

## 3. LiteLLM built-in `headroom` guardrail

- Location: `litellm/proxy/guardrails/guardrail_hooks/headroom/headroom.py`.
- Accepted params: `mode`, `api_base`, `api_key`, `model`, `unreachable_fallback` (`fail_closed` default / `fail_open`), `default_on`. Nothing else.
- It posts only `{"messages", "model"}` to `{api_base}/v1/compress` (`headroom.py:421-433`). **No per-request compression options reach Headroom.**
- Header `x-headroom-bypass: true` skips compression; a client can therefore bypass a forced guardrail unless the gateway strips it.

**Conclusion:** through the built-in guardrail, Headroom is on/off per request only. Per-assistant input/tool-output toggles and strategies are impossible there.

## 4. Headroom Python SDK (`headroom-ai` 0.40.0)

- Apache-2.0, Python >= 3.10, compiled Rust core (`cp310-abi3`), ~15 MB wheel.
- Core deps include `litellm>=1.96.2,<2.0` (compatible with our 1.99.2 pin), tiktoken, pydantic. No torch/onnx in core; ML is in extras.
- API: `from headroom import compress` — **sync only**.
  `compress(messages, model=..., model_limit=200000, optimize=True, hooks=None, config: CompressConfig | None = None, **kwargs) -> CompressResult`.
  `CompressResult`: `messages, tokens_before, tokens_after, tokens_saved, compression_ratio, transforms_applied, diagnostics, lossless, reverted_messages`.
- `CompressConfig` fields: `compress_user_messages` (False), `compress_system_messages` (True), `protect_recent` (4), `protect_analysis_context`,
  `frozen_message_count`, `target_ratio`, `min_tokens_to_compress` (250), `kompress_model`, `savings_profile`, `diagnostics`, `mode` (None | `"agent"`), `verify_lossless`.
- `mode="agent"` (shorthand `densify()`): lossless densification, deterministic, prompt-cache-safe.
- No per-call tool exclusion: `HEADROOM_PROTECT_TOOL_RESULTS` is a server setting; the SDK uses built-in `DEFAULT_EXCLUDE_TOOLS`.
- Kompress ML model downloads from Hugging Face on first use unless `HEADROOM_OFFLINE=1`. Telemetry beacon is on by default; disable with
  `HEADROOM_BEACON=off` / `DO_NOT_TRACK=1`.
- LangChain integration exists (`HeadroomChatModel`, needs `langchain` extra). Not used — see design.

## 5. Headroom server `/v1/compress`

Per-request body: `messages`, `model`, `token_budget`, `config.{compress_user_messages, target_ratio, protect_recent, protect_analysis_context,
frozen_message_count, session_id, mode}`. `config.mode` accepts unset, `"ccr"`, `"lossy_inline"`, `"lossless_then_lossy"`; anything else is 400
(`headroom/proxy/handlers/openai.py:10581-10900`).

Server-wide only: `HEADROOM_PROTECT_TOOL_RESULTS`, `HEADROOM_EXCLUDE_TOOLS`, `HEADROOM_MIN_TOKENS`, `HEADROOM_SAVINGS_PROFILE`,
`HEADROOM_COMPRESS_SYSTEM_MESSAGES`, `HEADROOM_DISABLE_KOMPRESS`, `HEADROOM_LOSSLESS_ONLY`, `HEADROOM_OFFLINE`, `HEADROOM_BEACON`, others.

Headroom also has a proxy-only **output shaper** (`headroom/proxy/output_shaper.py`) that steers model output verbosity and effort. Out of scope:
"output" in the ticket means tool outputs in context, which is input compression.

## 6. Options considered

| Option | Summary | Verdict |
|---|---|---|
| A. Backend → sidecar / SDK | Backend calls Headroom itself in a LangGraph pre-model hook: HTTP `/v1/compress` to the sidecar when LiteLLM is enabled, in-process `compress()` otherwise. | **Chosen.** Only option that honours every per-assistant setting in both deployment modes. |
| B. Custom LiteLLM guardrail | Our own `CustomGuardrail` in the LiteLLM image, config passed via request metadata. | Rejected: ships code inside a third-party image, needs new `extra_body` plumbing, SDK mode still needs a second path. |
| C. Built-in guardrail per strategy | One Headroom container per strategy, backend picks guardrail name. | Rejected: input / tool-output toggles need a container per combination. |
| `HeadroomChatModel` wrapper | Wrap the chat model. | Rejected: `build_chat_model_for` returns raw clients so `with_structured_output()` keeps working; a wrapper breaks that. |

## 7. Open items (resolve in the spike)

1. ~~Sidecar 0.40.0: confirm accepted `config.mode` values and pick the closest lossless equivalent to SDK `mode="agent"`.~~ **Resolved 2026-10-08.**
   Evidence: 0.40.0 wheel source (`headroom/proxy/handlers/openai.py` `handle_compress`, `proxy/server.py`, `proxy/bind_policy.py`), the upstream `Dockerfile`
   at git tag `v0.40.0`, and a real server run from a throwaway venv (`headroom-ai[proxy,code]==0.40.0`, the extras the image installs) on loopback.
   The published image `ghcr.io/headroomlabs-ai/headroom:0.40.0` itself was **not** pulled or run.
   - **Accepted `config.mode`:** unset/`null`, `"ccr"`, `"lossy_inline"`, `"lossless_then_lossy"` (alias of `lossy_inline`). Anything else is
     `400 {"error":{"type":"invalid_request",...}}`; verified for `"lossless"` and `"agent"`. **There is no per-request lossless mode.**
   - **What unset means depends on a server-wide env var.** With `HEADROOM_LOSSLESS` unset/`0`, unset mode is the default marker-free *lossy* pipeline
     (tabular compaction of JSON arrays, whole log blocks replaced by `[N lines omitted: ...]`). With `HEADROOM_LOSSLESS=1`, unset mode is lossless-only:
     only byte-exact folds for log, search and diff output (e.g. `... (repeated 300 times)`); JSON arrays are left untouched. `lossy_inline` forces
     lossy behaviour regardless of `HEADROOM_LOSSLESS`. Measured on the sample in item 3 (37,499 tokens): `HEADROOM_LOSSLESS=1` + unset mode gave 37,499
     (JSON array and unique-line log untouched); `lossy_inline` and the default (`HEADROOM_LOSSLESS=0`) both gave 10,488. A log of 501 near-identical lines
     went 10,070 to 127 tokens in both server modes (fold), and a 300-line grep result 6,055 to 4,561.
   - **Chosen sidecar mapping:** run the sidecar with `HEADROOM_LOSSLESS=1`. `lossless` sends `config` with `mode` unset (strictly lossless, closest
     the server offers to SDK `mode="agent"`, but it saves nothing on JSON arrays where the SDK saves 27.6%); `balanced` and `aggressive` send
     `mode: "lossy_inline"`. Risk: if the sidecar is deployed without `HEADROOM_LOSSLESS=1`, `lossless` silently becomes lossy `balanced`; pin the env var in compose.
   - **Request:** `POST {base}/v1/compress`, `Content-Type: application/json`, body
     `{"messages": [OpenAI-format dicts], "model": "<model name>", "config": {...}}`. `messages` and `model` are required (400 if missing; empty `messages` returns
     a pass-through payload). Optional `token_budget` (int) only replaces the model context limit; it did not change output in the test. `config` keys:
     `mode`, `compress_user_messages` (bool), `target_ratio` (float), `protect_recent` (int), `protect_analysis_context` (bool),
     `frozen_message_count` (int >= 0), `session_id` (str; stateful sidecar session, rejected together with `compress_user_messages`). Unknown keys are ignored.
     The route is loopback-only unless the server has `HEADROOM_COMPRESS_ALLOW_REMOTE=1`.
   - **Response 200:** `messages` (same count and keys as the input; verified 11 to 11, `tool_call_id`/`name` kept), `tokens_before`, `tokens_after`,
     `tokens_saved`, `compression_ratio`, `transforms_applied`, `transforms_summary`, `ccr_hashes`. **`compression_ratio` is `tokens_after / tokens_before`**
     (1.0 = no change), the opposite of the SDK's saved fraction; derive savings from the token counts. Fail-open cases still return 200 with the original
     `messages`, zeros for the counts and `compression_skipped: true, skip_reason: "compression_timeout"` (server compression timeout 30 s).
     Errors: 400 invalid request, 401 bad or missing token, 503 `compression_error`.
     Default and `lossy_inline` output carries no `Retrieve more: hash=` marker (`ccr_hashes: []`); only `mode: "ccr"` does.
   - **Health route:** `GET /readyz` (readiness, 200 when ready), `GET /livez` (liveness only), `GET /health`; all three are exempt from token auth. `/readyz` and
     `/health` also HEAD-probe the upstream (`https://api.anthropic.com`, cached 30 s) and report it in `checks.upstream`; set `HEADROOM_SKIP_UPSTREAM_CHECK=1`
     so health does not depend on egress. Start-up took about 20 s on a laptop (use a `start_period` of at least 30 s).
   - **Healthcheck tool (from the upstream Dockerfile at tag `v0.40.0`, not verified against the pulled image):** the default `runtime` target is
     `python:3.13-slim` plus `curl`, with `HEALTHCHECK ... CMD curl --fail --silent "http://127.0.0.1:${HEADROOM_PORT:-8787}/readyz"`, `ENTRYPOINT ["headroom","proxy"]`,
     `EXPOSE 8787`, user `nonroot`, extras `proxy,code,bedrock` (onnxruntime present, no torch). A distroless `runtime-slim` target also exists with no curl and a
     `python3 -c urllib` healthcheck; which one the `0.40.0` tag points at was not checked. `python3` is present in both, so the compose healthcheck uses
     `python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/readyz', timeout=5)"` (curl works only on the default variant).
   - **Auth env var:** `HEADROOM_PROXY_TOKEN`. When set, requests from non-loopback peers must send `Authorization: Bearer <token>` or `X-Headroom-Proxy-Token: <token>`,
     otherwise `401 {"error":"unauthorized"}`; loopback peers and `GET /health|/livez|/readyz` are exempt; the token is stripped before any upstream forward. This was read
     from `proxy/server.py` (`_security_gate`) and not exercised end to end (non-loopback connections were blocked in the spike sandbox). When it is **not** set, no auth is
     enforced at all. The image binds `HEADROOM_HOST=127.0.0.1` by default; the backend container needs `HEADROOM_HOST=0.0.0.0`, and a `0.0.0.0` bind with no token and no
     `HEADROOM_ALLOW_UNAUTHENTICATED_BIND=1` refuses to start (verified: `Refusing to bind '0.0.0.0'`).
   - **Beacon on the sidecar:** unlike SDK-only use (item 4), the server's proxy outcome path feeds the upload beacon, which is on by default. Set `HEADROOM_BEACON=off` and
     `DO_NOT_TRACK=1` (`HEADROOM_OFFLINE=1` also disables it, but it also blocks the Kompress model download, so `target_ratio` then has no effect; see item 4).
2. ~~Confirm Linux wheels exist for `headroom-ai` 0.40.0 (only macOS arm64 was checked).~~ **Resolved 2026-10-08: yes, both architectures.**
   PyPI publishes for 0.40.0: `headroom_ai-0.40.0-cp310-abi3-manylinux_2_28_x86_64.whl`, `headroom_ai-0.40.0-cp310-abi3-manylinux_2_28_aarch64.whl`,
   the macOS (`macosx_10_12_x86_64`, `macosx_11_0_arm64`) and `win_amd64` wheels, and an sdist (`headroom_ai-0.40.0.tar.gz`). There is no `py3-none-any` wheel
   (the last pure-python wheel is 0.20.15). All files declare `Requires-Python: >=3.10` (wheel METADATA checked; abi3, so one wheel serves 3.12).
   `pip download headroom-ai==0.40.0 --no-deps --only-binary=:all: --python-version 3.12 --platform <tag>`:
   `manylinux_2_28_x86_64` and `manylinux_2_28_aarch64` resolve (15.6 MB / 15.4 MB); `manylinux2014_x86_64`, `manylinux2014_aarch64` and
   `manylinux_2_17_x86_64` do **not** (pip falls back to the old 0.20.x line and fails on the 0.40.0 pin). The wheels need glibc >= 2.28: fine for the
   Debian-based `codemie-base-python` images in `Dockerfile` (glibc version of those images not verified here), not for Alpine/musl or CentOS 7.
   Verdict: a usable Linux wheel exists for Python 3.12 on x86_64 and aarch64; no source build (Rust toolchain) is needed.
3. ~~Confirm SDK `compress()` works with no ML extras installed (Kompress disabled / offline).~~ **Resolved 2026-10-08: yes.**
   Setup: throwaway venv (Python 3.12.7, macOS arm64 wheel), `pip install headroom-ai==0.40.0` only (69 packages, 243 MB; core deps resolved `litellm 1.104.2`,
   `tiktoken`, `tokenizers`, `boto3`; no torch, onnxruntime or transformers), run with `python -I`, `HEADROOM_OFFLINE=1`, and a socket tripwire that records every connect.
   Result: `from headroom import compress, CompressConfig` imports in about 0.1 s; **zero outbound connections** once the tiktoken cache was warm.
   Sample: 11 OpenAI-format messages, one `tool` message holding a 400-record JSON array (60,581 chars) and one holding a 600-line log (48,489 chars), model `gpt-4o`.

   | Call | tokens_before | tokens_after | saved | Notes |
   |---|---|---|---|---|
   | `CompressConfig(mode="agent")` | 37,499 | 27,147 | 10,352 (27.6%) | `lossless=True`, `reverted_messages=1` (log left as is); JSON message 60,581 to 28,635 chars |
   | `CompressConfig()` (default) | 37,499 | 10,516 | 26,983 (72.0%) | JSON densified to 32,262 chars; the log became `[600 lines omitted: 600 INFO]` plus a CCR marker |
   | `CompressConfig(target_ratio=0.2)` | 37,499 | 10,516 | 26,983 | identical to default: no ML extras, so `target_ratio` is a no-op |

   Things the hook and compressor must handle:
   - **Tokenizer:** with a cold tiktoken cache, `HEADROOM_OFFLINE=1` does not stop tiktoken fetching `o200k_base` from `openaipublic.blob.core.windows.net`; Headroom then
     falls back to estimation (31,163 instead of 37,499 on the sample). Air-gapped deployments need `TIKTOKEN_CACHE_DIR` pre-populated.
   - **Default (non-agent) SDK mode emits CCR markers** such as `Retrieve more: hash=...` that the model cannot resolve (no retrieve tool in our agents). The sidecar default is
     marker-free. `CompressConfig` has no switch for this; replacing the private singleton `sys.modules["headroom.compress"]._pipeline` with
     `TransformPipeline(transforms=[ContentRouter(ContentRouterConfig(ccr_enabled=False, ccr_inject_marker=False))])` removes the marker (verified: 94 to 66 tokens on a log,
     no marker). It uses a private attribute, so it needs a test that fails when Headroom changes it.
   - **Disk side effect:** the SDK creates `$HOME/.headroom/ccr_store.db` (SQLite, mode 0600) on first use, even in `mode="agent"`, and the default mode stores original tool output
     there. `HEADROOM_CCR_BACKEND=memory` prevents the file (verified: no file created). Set it before import.
   - **Errors are swallowed:** `compress()` catches every exception and returns the original messages with `tokens_before == tokens_after == 0`; treat zero counts on non-empty
     input as failure. A result with `tokens_after > tokens_before` is reverted to the originals by an inflation guard.
   - Cost: first call about 0.4 s with a warm tiktoken cache (3 to 4 s on a cold cache, from the failed download), later default-mode calls 30 to 50 ms on this sample. `compress()` is sync.
4. ~~Choose `HEADROOM_AGGRESSIVE_TARGET_RATIO`; confirm whether the telemetry beacon runs for SDK-only use.~~ **Resolved 2026-10-08.**
   - **Value: `0.2`.** `target_ratio` is the *keep* fraction for Kompress, the ML text compressor: `SDK CompressConfig.target_ratio` / sidecar `config.target_ratio` are passed to
     `ContentRouter` and then to `KompressCompressor.compress(target_ratio=...)`, which ranks words by model importance score and keeps the top `int(n_words * target_ratio)`
     (lower = more aggressive). `None` lets the model's own threshold decide. It does **not** affect SmartCrusher (JSON) or the log, search and diff compressors.
     `0.2` is the value Headroom's own `CompressConfig` docstring gives as the "Aggressive (logs, search results)" example (Headroom's `agent-90` profile uses `0.10`, its
     `balanced` profile `0.30`). Measured with the ONNX Kompress model on 1,960 tokens of number-heavy prose: model decides keeps 89%, `0.5` keeps 65%, `0.3` keeps 50%, `0.1` keeps 41%
     (numbers, paths and error names are always kept, so the floor is well above the ratio).
   - **Caveat:** `target_ratio` only does something when Kompress can run. It needs `onnxruntime` (in the image's `[proxy]` extra; **not** in a plain `pip install headroom-ai`) and the
     ~261 MB ONNX model from Hugging Face, which `HEADROOM_OFFLINE=1` forbids unless it is pre-cached. Without that, `aggressive` equals `balanced`: verified on both the SDK and
     the sidecar (identical token counts, and the sidecar logs `Kompress ... HEADROOM_OFFLINE forbids fetching it`). In the SDK-only deployment `aggressive` is therefore currently
     the same as `balanced`; the UI help text should not promise more.
   - **Beacon, SDK-only use: not uploaded.** `is_beacon_enabled()` defaults to on (opt-out; `HEADROOM_BEACON=off`, `DO_NOT_TRACK=1`, or `HEADROOM_OFFLINE=1` switch it off). In SDK use,
     `compress()` only stages in-memory counters (`BeaconCompressionObserver`, `record_content_shapes`). The aggregator that posts to
     `headroom-beacon.headroom-beacon.workers.dev` is created only by the proxy outcome path and the MCP server, neither of which the SDK touches. Verified: with no
     opt-out variables set and the tripwire on, there were zero connections, including at interpreter exit. We still set `HEADROOM_OFFLINE=1`, `HEADROOM_BEACON=off`, `DO_NOT_TRACK=1`
     so a future SDK change cannot start uploading. The sidecar server is different; see item 1.

   **Strategy mapping (confirmed).** `compress_user_messages` is added to both from the resolved config. Sidecar must run with `HEADROOM_LOSSLESS=1`.

   | Strategy | Sidecar `config` body | SDK `CompressConfig` kwargs |
   |---|---|---|
   | `lossless` | `{}` (`mode` unset) | `mode="agent"` |
   | `balanced` | `{"mode": "lossy_inline"}` | `mode=None, target_ratio=None` (no extra kwargs) |
   | `aggressive` | `{"mode": "lossy_inline", "target_ratio": 0.2}` | `mode=None, target_ratio=0.2` |
