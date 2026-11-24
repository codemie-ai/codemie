# Headroom core (slice 1) Implementation Plan

**Goal:** Land the unit-tested `src/codemie/service/headroom/` package, settings, feature flag, dependency and sidecar compose service. Nothing is wired to any agent.
**Spec:** `spec.md` in this directory. **Design:** `docs/superpowers/specs/2026-10-08-headroom-per-assistant-workflow-design.md`. **Findings:** `docs/headroom/research-findings.md`.
**Conventions:** Commit per task using the repository's existing convention, subject `EPMCDME-15645: <Description>`. New files carry the EPAM Apache header (copy from `src/codemie/service/conversation/history_compaction_service.py:1-13`), `from __future__ import annotations`, `X | None` unions. Tests live in `tests/codemie/service/headroom/` (add `__init__.py`) and mock at seams. No whole-suite, lint or browser-verification tasks here; the calling flow owns them.
**Safety for spike tasks:** downloaded wheels and extracted files are untrusted. Use a new empty dir per download under `/tmp`, keep helper scripts elsewhere, pass paths as arguments, run Python with `-I`, never inside the project env and never import code from the download dir.

## Task 1: Spike - Linux wheel availability (gates the dependency)
Run `pip download headroom-ai==0.40.0 --no-deps --only-binary=:all: --python-version 3.12 --platform manylinux2014_x86_64` (repeat with `manylinux2014_aarch64`) into an empty `/tmp` dir; inspect filenames and metadata only. Record result and date in `docs/headroom/research-findings.md` section 7, item 2.
**If no Linux wheel exists, stop and return `needs_input` (options: re-scope to sidecar-only without the dependency; pick another version). Do not run Task 4.**
Test-first: no - evidence and docs only.

## Task 2: Spike - sidecar modes, offline SDK, aggressive ratio, beacon
Read the 0.40.0 wheel source (proxy `/v1/compress` handler, SDK `compress`, beacon code) and, in a throwaway venv under `/tmp` (`python -I`), run `compress()` with no ML extras and `HEADROOM_OFFLINE=1`. Record in findings section 7:
- item 1: accepted `config.mode` values, the sidecar mapping chosen for `lossless`, the `/v1/compress` response field names (messages, token counts), the health route, and the env var the server reads for Bearer auth (say so if it enforces none);
- item 3: works or not without ML extras, offline;
- item 4: chosen `HEADROOM_AGGRESSIVE_TARGET_RATIO`, and whether the beacon runs for SDK-only use.
Update the strategy table in the design doc section 3 to match.
Test-first: no - evidence and docs only.

## Task 3: Settings and feature flag
Modify `src/codemie/configs/config.py` (next to `SUBWORKFLOW_POOL_MAX_SIZE`, l.610): add `HEADROOM_ENABLED=False`, `HEADROOM_SIDECAR_URL="http://headroom:8787"`, `HEADROOM_PROXY_TOKEN=""`, `HEADROOM_TIMEOUT_SECONDS=10`, `HEADROOM_MIN_TOKENS=1000`, `HEADROOM_AGGRESSIVE_TARGET_RATIO=<Task 2 value>`, with comments stating the blocking-call cost and that the token is never logged. Modify `config/customer/customer-config.yaml`: add `features:headroom`, `enabled: false`, modelled on `features:requestHedging` (l.203-208).
Test-first: yes - new `tests/codemie/configs/test_headroom_config.py` asserts each default and that `SHIPPED_CUSTOMER_CONFIG` (pattern in `tests/codemie/configs/test_customer_config.py:25`) has `features:headroom` disabled.

## Task 4: Add `headroom-ai` dependency
Gated on Task 1. In `pyproject.toml` add `headroom-ai = "0.40.0"` (no extras) after `litellm`; relock with a targeted `poetry lock` (never a wholesale regeneration, `.ai-run/guides/security/dependencies.md`). Confirm `litellm` stays 1.99.2 in the lock diff and `poetry check --lock` passes. Add a `[tool.pip-licenses]` entry (pyproject.toml:274) only if the license check rejects it. Commit `pyproject.toml` and `poetry.lock` together.
Test-first: no - dependency change; `poetry check --lock` is the check.

## Task 5: Package scaffold and models
Create `src/codemie/service/headroom/__init__.py` and `models.py` exactly per design section 1 (`HeadroomStrategy`, `HeadroomConfig`, `HeadroomOverride`, frozen `ResolvedHeadroomConfig`).
Test-first: yes - `test_models.py` fails on import; asserts defaults (enabled False, both toggles True, balanced), the three strategy values, `HeadroomOverride` all-None, and that `ResolvedHeadroomConfig` rejects mutation.

## Task 6: Resolver
Create `resolver.py`: `resolve(node, workflow, assistant)` per design section 2. Read `config.HEADROOM_ENABLED` and `customer_config.is_feature_enabled("headroom")` at call time (lazy, so tests patch them).
Test-first: yes - `test_resolver.py` fails with no module; cases: precedence table, workflow `enabled: true` alone keeps the assistant's `strategy`, node `enabled: false` over workflow on, node on with no workflow block, kill switch off gives `None`, flag off gives `None`, merged disabled gives `None`.

## Task 7: Strategy table
Create `strategies.py`: one table, strategy to sidecar `config` body and SDK `CompressConfig` kwargs, using the Task 2 mapping and `config.HEADROOM_AGGRESSIVE_TARGET_RATIO` (read at call time). Both accessors also take `compress_user_messages`. No other module names strategy values.
Test-first: yes - `test_strategies.py` asserts, for each of lossless, balanced, aggressive, the exact sidecar body and SDK kwargs, and that `compress_user_messages` is passed through.

## Task 8: Compressor protocol and sidecar compressor
Create `compressor.py` (new symbols) and `sidecar_compressor.py`.
```python
@dataclass(frozen=True)
class CompressOutcome:
    messages: list[dict]; tokens_before: int; tokens_after: int

class HeadroomCompressionError(Exception): ...

class HeadroomCompressor(Protocol):
    mode: str  # "sidecar" | "sdk"
    def compress(self, messages: list[dict], model: str, cfg: ResolvedHeadroomConfig) -> CompressOutcome: ...
```
`SidecarCompressor`: sync `httpx.post` to `{HEADROOM_SIDECAR_URL}/v1/compress`, `Authorization: Bearer <token>` (header omitted when token empty), timeout from settings, body from `strategies`; timeout, `httpx.HTTPError`, non-2xx or malformed body raise `HeadroomCompressionError`. Never log the token or message content.
Test-first: yes - `test_sidecar_compressor.py` with `httpx.post` patched: asserts URL, header, body, timeout and parsed outcome; timeout, connection error, 400 and 500 each raise `HeadroomCompressionError`.

## Task 9: SDK compressor
Create `sdk_compressor.py`: on first use assign `HEADROOM_OFFLINE=1`, `HEADROOM_BEACON=off`, `DO_NOT_TRACK=1` in `os.environ`, then `from headroom import compress` (lazy). Build `CompressConfig` from `strategies`. First `ImportError` is logged once, the compressor disables itself for the process, and further calls raise `HeadroomCompressionError` without retrying the import. Module must import with `headroom-ai` absent.
Test-first: yes - `test_sdk_compressor.py` injects a fake `headroom` via `sys.modules`: asserts kwargs from strategy, env vars set before the import runs, success outcome; with import blocked, one log line and the import attempted once across two calls.

## Task 10: Factory
Create `factory.py`: `get_compressor()` cached with `functools.lru_cache(maxsize=1)`; `SidecarCompressor` iff `is_litellm_enabled()` (import lazily from `codemie.enterprise.litellm`), else `SDKCompressor`.
Test-first: yes - `test_factory.py` patches `is_litellm_enabled`: asserts sidecar for True, SDK for False, same instance on a second call (`cache_clear()` in a fixture).

## Task 11: Fail-open pre-model hook
Create `hook.py` with `build_headroom_pre_model_hook(cfg: ResolvedHeadroomConfig | None, llm_model: str)`; returns `None` for `None` cfg, else a sync `(state) -> dict` closure implementing spec section 1 hook contract. Per call: collect `state["messages"]`; count text tokens with `codemie.core.utils.calculate_tokens` and return `{}` below `config.HEADROOM_MIN_TOKENS` without calling the compressor; build the request by copying each message with `content` reduced to its text blocks only (image blocks dropped, count preserved) and `convert_to_openai_messages`; call `get_compressor().compress`; on count mismatch return `{}`; map back by index onto the original `BaseMessage` objects via `model_copy(update={"content": ...})`, only when the original content is a `str`, never for System or AI, Human only if `compress_user_messages`, Tool only if `compress_tool_outputs`; return `{}` if nothing changed else `{"llm_input_messages": [...]}`. Wrap the whole body in `except Exception` that logs a warning (exception type only, no content) and returns `{}`. The state and checkpointed messages are never mutated.
Test-first: yes - `test_hook.py` (compressor patched at `hook.get_compressor`) fails with no module: `None` cfg gives `None`; below min tokens makes no compressor call; system untouched; each toggle respected; list-content and image-block messages keep original content; `additional_kwargs`, `tool_calls`, `tool_call_id`, `id`, `name`, `artifact` preserved; count mismatch, `HeadroomCompressionError` and an unexpected exception each return `{}`; unchanged returns `{}`; input list and messages not mutated.

## Task 12: `headroom.compress` span and structured log
Modify `hook.py` to wrap the compressor path in `codemie.core.otel_tracing.span("headroom.compress")` and set `codemie.headroom.mode`, `.strategy`, `.tokens_before`, `.tokens_after`, `.duration_ms`, `.outcome` (compressed|unchanged|skipped_small|failed_open), including for skipped-small calls; emit one `logger.info` line with the same fields. No message content in either.
Test-first: yes - extend `test_hook.py` with `hook.span` patched to capture attributes: asserts the outcome per path (compressed, unchanged, skipped_small, failed_open), the mode from the compressor, and that no attribute or log record contains message text.

## Task 13: docker-compose `headroom` service
Modify `docker-compose.yml`: add `headroom` after `litellm` (l.117-151), following its pattern: image `ghcr.io/headroomlabs-ai/headroom:0.40.0`, `container_name: headroom`, `environment` with `HEADROOM_OFFLINE=1`, `HEADROOM_BEACON=off`, `HEADROOM_COMPRESS_ALLOW_REMOTE=1`, `HEADROOM_PROXY_TOKEN` (`${HEADROOM_PROXY_TOKEN:-}`), a `healthcheck` (route and tool from Task 2; `interval/timeout/retries/start_period` as litellm), `restart: unless-stopped`, `mem_limit` and `cpus`. No `ports:` publish (internal network only) and no `depends_on` added to `codemie`; `litellm_config.yaml` and `deploy-templates/` untouched.
Test-first: no - config only; the file must stay valid compose YAML (`docker compose config -q` when Docker is available, otherwise `yaml.safe_load`).

## Notes
- negative-constraints: honored. No `depends_on` from `codemie` or `HEADROOM_ENABLED` flip (Task 13, Task 3); no mode setting (Task 10); no guardrail or `litellm_config.yaml` change (Task 13); system messages and non-string content never replaced, no content in logs or spans (Tasks 11, 12); no DB, migration, `LangGraphAgent`, `AssistantService`, YAML-schema or API changes (nothing here touches them); no ML extras, no async compressor, no Helm change.
- The MR description must ask a reviewer to post `/sanity` (Task 4 changes dependencies); the caller owns MR creation.
