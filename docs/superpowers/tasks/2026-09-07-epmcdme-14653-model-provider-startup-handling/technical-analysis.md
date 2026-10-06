# Technical Research

**Task**: model provider startup readiness healthcheck lifespan llm config
**Generated**: 2026-09-07
**Research path**: codegraph (primary) + targeted filesystem verification for exact settings/line numbers

---

## 1. Original Context

EPMCDME-14653 — Model Provider startup handling

## Goal

Make model-provider readiness a startup-time fact instead of a first-message surprise: the app starts whether or not a provider is configured, and an operator learns immediately which case they're in.

## Scope

Startup/readiness gains a check for configured model-provider credentials (Azure OpenAI or AWS Bedrock). A configured provider changes nothing on the chat path. No provider configured still lets the app start, but readiness output and startup log must name the missing configuration before any chat is attempted.

## Out of scope

- Retrieval capability detection and availability.
- Vector storage availability checks.
- Feature visibility and capability fencing beyond model-provider readiness.
- Provider-selection logic, multi-provider fallback, credential rotation.
- Changing the `/healthcheck` HTTP status code or its probe semantics.
- Live provider reachability (no outbound call to Azure/AWS at startup — configuration presence only).
- Validating provider credentials held inside LiteLLM proxy configuration (see Notes).

## Acceptance criteria

**EPMCDME-14347#AC10** Given an operator who supplies credentials for a single hosted model provider — an Azure OpenAI or AWS Bedrock key is enough — and configures nothing else, when they start the application, then they reach a working CodeMie they can chat with and messages are answered by that provider.

**EPMCDME-14347#AC10 Negative:** Given no model provider has been configured, when the operator starts the application, then it starts and names the model configuration that is missing, rather than presenting a working product that fails on the first message.

## What "configured" means

- `MODELS_ENV` selects a model profile — `config/llms/llm-<MODELS_ENV>-config.yaml` — and that file loads.
- The profile contains at least one enabled chat model.
- A global default model exists (`ModelCategory.GLOBAL`).
- The credentials required by the selected profile's provider are present:
  - Azure OpenAI — `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_URL`
  - AWS Bedrock — `AWS_BEDROCK_REGION` plus resolvable AWS credentials from the standard credential chain

A configuration that satisfies credentials but not the profile (for example `AZURE_OPENAI_API_KEY` set while `MODELS_ENV=aws`) is reported as **not configured**, with the mismatch named.

## Notes and assumptions (from ticket)

- Providers beyond Azure/Bedrock (`dial` default, `gcp`, and `LLMProvider` values `google_vertexai`, `anthropic`, `vertex_ai-anthropic_models`) must be reported as configured, not falsely flagged — check is provider-agnostic over whatever the selected profile's default model declares.
- No Bedrock "key" setting exists — Bedrock credentials resolve through the ambient AWS credential chain.
- LiteLLM proxy mode (`LLM_PROXY_ENABLED=true` and `LLM_PROXY_MODE=lite_llm`): the check reports the proxy as the configured provider and does not inspect LiteLLM's own credentials.
- No secret values logged or returned — only setting names in the WARNING; `/healthcheck` returns a status enum and provider name.

---

## 2. Codebase Findings

### Existing Implementations

- `codemie/src/codemie/rest_api/main.py:706` — `async def lifespan(app: FastAPI)`, the FastAPI lifespan context manager. Existing startup assertions run near the end of the startup block:
  - line 788: `assert_oauth_state_signing_secret_configured()`
  - line 789: `assert_token_vault_available()`
  - line 790: `warn_insecure_oauth_storage_for_enabled_providers()`
  - line 791: `_initialize_deployment_version()`
  - **Insertion point**: immediately after line 790 (`warn_insecure_oauth_storage_for_enabled_providers()`), before `_initialize_deployment_version()`. New call computes readiness once and stores it: `app.state.model_provider_readiness = check_model_provider_readiness()`.
  - Imports for the three existing oauth_security functions are at lines 113-115 (`from codemie.service.oauth_security import ...`) — add the new function to that import block or a sibling import line.

- `codemie/src/codemie/service/oauth_security.py` (121 lines, read in full) — **exact precedent** for the new module's shape:
  - Pure functions only, imports `config` and `logger` from `codemie.configs`.
  - `assert_*` functions raise `RuntimeError` naming the exact missing setting, used for fatal startup failures (not the pattern here — ticket requires app to still start).
  - `warn_if_insecure_token_storage(provider_label)` / `warn_insecure_oauth_storage_for_enabled_providers()` — logs `logger.warning(...)` naming the condition, never secret values. **This is the pattern to mirror**: a `check_model_provider_readiness()` function that returns a small result object (status + provider + list of missing settings) and a caller in `lifespan()` that logs INFO (configured) or WARNING (not configured) based on the result.

- `codemie/src/codemie/rest_api/routers/common.py` (66 lines, read in full) — current `healthcheck()` (lines 54-65):
  ```python
  @router.get("/healthcheck", include_in_schema=False)
  def healthcheck():
      import os
      import psutil
      process = psutil.Process(os.getpid())
      return {
          "status": "healthy",
          "memory_usage_mb": ...,
          "cpu_percent": ...,
          "worker_pid": os.getpid(),
      }
  ```
  Takes no parameters. To add `model_provider`, inject `request: Request` (FastAPI, already imported at top-level in this file's package elsewhere in the router set) and read `request.app.state.model_provider_readiness`. No `response_model` is set on this route today — free to add the field to the returned dict without a schema migration; endpoint keeps `include_in_schema=False` and HTTP 200.

- `codemie/src/codemie/rest_api/main.py:283-306` — `_initialize_enterprise_services(app)` / `_setup_litellm_features()`: existing precedent for **computing something once and stashing on `app.state`** (`app.state.litellm_service = litellm_service`) rather than recomputing per request. Follow the same shape for `app.state.model_provider_readiness`.

- `codemie/src/codemie/enterprise/litellm/dependencies.py:44-70` — `is_litellm_enabled()`: `HAS_LITELLM and config.LLM_PROXY_ENABLED`. **Does not itself check `LLM_PROXY_MODE`.**

- `codemie/src/codemie/core/errors.py:800` — `if HAS_LITELLM and config.LLM_PROXY_ENABLED and config.LLM_PROXY_MODE == "lite_llm":` — this is the **actual existing precedent condition** for "LiteLLM proxy mode" as the ticket describes it (not `is_litellm_enabled()` alone). Reuse this exact three-part condition for the proxy branch of the readiness check.

- `codemie/src/codemie/configs/llm_config.py` (153 lines, read in full):
  - `LLMProvider(Enum)`: `AZURE_OPENAI = "azure_openai"`, `AWS_BEDROCK = "aws_bedrock"`, `GOOGLE_VERTEX_AI = "google_vertexai"`, `ANTHROPIC = "anthropic"`, `VERTEX_AI_ANTHROPIC = "vertex_ai-anthropic_models"`.
  - `ModelCategory(str, Enum)` includes `GLOBAL = "global"`.
  - `LLMModel.provider: Optional[LLMProvider]`, `LLMModel.enabled: bool`, `LLMModel.is_default_for(category)`.
  - Module-level singleton: `llm_config = LLMConfig(yaml_file=config.LLM_TEMPLATES_ROOT / f"llm-{config.MODELS_ENV}-config.yaml")` at line 147 — **loads and parses the YAML at import time**, not inside `lifespan()`. By the time `lifespan()` runs, the profile has already loaded successfully (a YAML load failure raises at import and crashes the process before `lifespan` is ever reached — this is a pre-existing behavior, out of scope to change; see Risk Indicators).

- `codemie/src/codemie/service/llm_service/llm_service.py`:
  - `LLMService.get_all_llm_model_info()` (lines 40-51): if `config.LLM_PROXY_ENABLED` and LiteLLM models are initialized, returns LiteLLM-sourced models; otherwise returns `self.llm_config.llm_models` (YAML). This already implements the "at least one enabled chat model" and profile-source logic generically.
  - `LLMService.get_default_model_for_category(category)` (lines 196-223): returns the model explicitly marked default for `category`, else falls back to the model marked default for `ModelCategory.GLOBAL`, else `None`. **Reuse directly**: `llm_service.get_default_model_for_category(ModelCategory.GLOBAL)` is exactly the "global default model exists" check from the AC, and its `.provider` field is exactly the provider to key credential checks on.
  - A module-level `llm_service` instance is expected to exist in this file (constructed with the `llm_config` singleton) — confirm the exact singleton import path (e.g. `from codemie.service.llm_service.llm_service import llm_service`) when implementing.

- `codemie/src/codemie/configs/config.py` (exact settings, verified by direct grep):
  - `MODELS_ENV: str = "dial"` (line 52)
  - `AZURE_OPENAI_API_KEY: str = ""` (line 60)
  - `AZURE_OPENAI_URL: str = ""` (line 61)
  - `LLM_TEMPLATES_ROOT: Path = Path(__file__).absolute().parents[3] / "config/llms"` (line 107)
  - `AWS_BEDROCK_REGION: str = ""` (line 377)
  - `AWS_BEDROCK_MAX_RETRIES`, `AWS_BEDROCK_READ_TIMEOUT` (lines 374-375) — retry/timeout only, no Bedrock access-key setting exists (confirms ticket's assumption).
  - `LLM_PROXY_MODE: Literal["internal", "lite_llm"] = "internal"` (line 653)
  - `LLM_PROXY_ENABLED: bool = False` (line 654)

- `codemie/src/codemie/core/dependecies.py:168-189` — `get_bedrock_runtime_client()`: builds `boto3.client("bedrock-runtime", region_name=config.AWS_BEDROCK_REGION or <default>, config=Config(...))` **without** passing explicit `aws_access_key_id`/`aws_secret_access_key` — confirms Bedrock chat-path credentials are resolved through boto3's standard/ambient credential chain (env vars, shared credentials file, IAM role), matching the AC's "resolvable AWS credentials from standard credential chain." The readiness check should use `boto3.Session().get_credentials() is not None` — a local, non-network resolution (no outbound call), consistent with the ticket's "configuration presence only" requirement.

- Actual per-profile `provider` values observed directly in the YAML files (`codemie/config/llms/llm-*-config.yaml`):
  - `llm-azure-config.yaml`: default (`default_for_categories: [global]`) model `gpt-5-2025-08-07` — provider not shown in the truncated read but siblings are `provider: "azure_openai"`.
  - `llm-aws-config.yaml`: global default model `claude-sonnet-4-6`, `provider: "aws_bedrock"`.
  - `llm-dial-config.yaml`: global default model `gpt-4.1`, `provider: "azure_openai"` — confirms `dial` profile's default model happens to declare the same provider enum as Azure OpenAI (DIAL is accessed as an Azure-OpenAI-compatible endpoint), so it is naturally covered by the Azure credential check rather than needing special-casing.
  - `llm-gcp-config.yaml`: global default model `gemini-3.1-pro`, `provider: "google_vertexai"` — falls into the "other provider, report configured, no credential check" bucket per ticket scope.

### Architecture and Layers Affected

FastAPI lifespan/startup layer (`codemie/rest_api/main.py`) → new pure-function readiness module (new file, sibling to `codemie/service/oauth_security.py`) → LLM config layer (`codemie/configs/llm_config.py`, `codemie/service/llm_service/llm_service.py`) → central `Config` (`codemie/configs/config.py`) → optional LiteLLM enterprise layer (`codemie/enterprise/litellm/`) → readiness result surfaced via `codemie/rest_api/routers/common.py` reading `app.state`.

### Integration Points

- `boto3` (already a dependency, used elsewhere for Bedrock clients) for the AWS credential-chain check.
- No new external service calls — all checks are local (config values, in-memory model list, local credential-chain resolution).

### Patterns and Conventions

- Small pure-function startup guard module, no new framework — mirrors `oauth_security.py` exactly.
- `assert_*` naming = raises and fails startup (not used here, since the ticket requires the app to still start).
- `warn_*` naming = logs and continues (the pattern for the "not configured" case).
- Compute once in `lifespan()`, stash on `app.state`, read back in request handlers rather than recomputing (`app.state.litellm_service` precedent).
- Never log or return secret values — only setting names (`oauth_security.py` never logs `MCP_AUTH_HMAC_SECRET`'s value, only whether it's configured).

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/llm-providers.md` — confirms "keep AWS, Azure, GCP, Anthropic, LiteLLM paths pluggable"; confirms `config/llms/` and `MODELS_ENV` (README.md:61); confirms LiteLLM proxy is gated via `is_litellm_enabled()`.
- `.ai-run/guides/development/configuration-patterns.md` — use `MODELS_ENV` and provider config files; avoid hardcoding provider choices; avoid reading env vars directly in feature code — extend `Config` in `config.py` rather than raw `os.getenv` (not needed here since all required settings already exist on `Config`).
- `.ai-run/guides/architecture/service-layer-patterns.md` / `layered-architecture.md` — general guidance; the new readiness module belongs in `codemie/service/` alongside `oauth_security.py` per the ticket's own pointer.
- `AGENTS.md` — routes "Integrations" tasks to `llm-providers.md` (P0); routes "Development"/config tasks to `configuration-patterns.md`.

### Architectural Decisions

None found specific to startup readiness beyond the existing `oauth_security.py` precedent, which is itself the de facto decision record for "how CodeMie does startup guards."

### Derived Conventions

- Startup guards live as free functions in a dedicated `codemie/service/*.py` module, imported into `main.py`, and called explicitly and synchronously inside `lifespan()` in a fixed order near the end of the startup sequence.

---

## 4. Testing Landscape

### Existing Coverage

- `codemie/tests/codemie/rest_api/test_startup_integration.py` — covers `lifespan()`; the natural home for new readiness-check startup scenarios (provider configured / not configured / mismatched profile).
- `codemie/tests/codemie/service/test_oauth_token_vault_guard.py` — pattern for testing `assert_*`-style guards.
- `codemie/tests/codemie/service/test_oauth_security_warn.py` — pattern for testing `warn_*`-style guards (closest analog to the new module's warn-and-continue behavior).

### Testing Framework and Patterns

pytest, with fixture-based config overrides (inferred from `test_*.py` naming and the guide references cited above).

### Coverage Gaps

- No existing test file for `healthcheck()` — new.
- No existing test file for `llm_config.py` or a model-provider readiness concept — new module needs new test file(s), e.g. `codemie/tests/codemie/service/test_model_provider_readiness.py`.

---

## 5. Configuration and Environment

### Environment Variables

- `MODELS_ENV` (default `"dial"`) — selects `config/llms/llm-<MODELS_ENV>-config.yaml`.
- `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_URL` — Azure OpenAI credentials.
- `AWS_BEDROCK_REGION` — Bedrock region; no Bedrock access-key setting exists (ambient AWS credential chain instead).
- `LLM_PROXY_ENABLED` (bool, default `False`), `LLM_PROXY_MODE` (`"internal"` | `"lite_llm"`, default `"internal"`) — together gate LiteLLM proxy mode per `core/errors.py:800` precedent.

### Configuration Files

- `config/llms/llm-<MODELS_ENV>-config.yaml` (`dial`, `azure`, `aws`, `gcp` profiles exist today) — each declares `llm_models` with `provider`, `enabled`, and `default_for_categories`.

### Feature Flags and Deployment Concerns

- `LLM_PROXY_ENABLED` / `LLM_PROXY_MODE` are the only relevant flags; no separate feature flag needed for this readiness check itself.

---

## 6. Risk Indicators

- `llm_config.py` loads and validates the YAML profile at **module import time**, not inside `lifespan()`. A malformed or missing `MODELS_ENV` profile file currently crashes the process at import, before the new readiness check (or any lifespan code) would even run. This is pre-existing behavior and out of scope to change — the new check only needs to handle the "file loaded, but credentials/profile mismatch" case, not "file failed to load."
- `healthcheck()` currently takes no parameters; adding `model_provider` requires adding a `Request` (or `app: FastAPI = Depends(...)`) parameter — a small signature change to a route with no existing tests.
- `is_litellm_enabled()` alone is **not** sufficient to detect "LiteLLM proxy mode" as the ticket defines it — must additionally check `config.LLM_PROXY_MODE == "lite_llm"` (see `core/errors.py:800` precedent) to match the ticket's stated condition precisely.
- SDK-side `LLMProvider` enums (`codemie_sdk` Python, Node) diverge slightly in naming from the core enum (e.g. `GOOGLE_VERTEXAI` vs `GOOGLE_VERTEX_AI`) — irrelevant unless a provider-name string from this check leaks into an SDK-consumed response; the `/healthcheck` field is a new, unversioned surface so this is a latent risk only, not a blocker.
- `boto3.Session().get_credentials()` can, in the IAM-role case, involve a local metadata-service lookup with its own short timeout — acceptable per existing `get_bedrock_runtime_client()` precedent (same ambient-chain resolution already happens on the real chat path), and is not the "live provider reachability" call the ticket excludes (no call to Bedrock/Azure APIs themselves).

---

## 7. Summary for Complexity Assessment

This is a self-contained, additive change touching three layers: a new pure-function readiness-check module (new file, `codemie/service/`, following the exact shape of the existing `oauth_security.py`), one new call site inside the existing `lifespan()` function in `main.py` (single line addition into an already-established sequence of startup guards), and one existing endpoint (`GET /healthcheck` in `common.py`) gaining one field read from `app.state`. All the underlying data needed already exists and is exposed through existing, tested abstractions — `llm_config.llm_models`, `LLMService.get_default_model_for_category(ModelCategory.GLOBAL)`, and `Config` fields for Azure/Bedrock credentials and LiteLLM proxy mode — so no new config plumbing or YAML schema changes are required.

Novelty is low: the module directly mirrors an existing, well-tested precedent (`oauth_security.py`) for both structure and the assert-vs-warn distinction, and the LiteLLM-proxy-mode condition (`LLM_PROXY_ENABLED and LLM_PROXY_MODE == "lite_llm"`) is copied from an existing precedent in `core/errors.py`. The main design decision — keying credential checks off the *selected profile's actual default model's `provider` field* rather than off `MODELS_ENV` string values directly — is already fully supported by existing `LLMModel.provider` / `LLMProvider` enum data, confirmed against the real `dial`, `azure`, `aws`, and `gcp` YAML profiles.

Test coverage posture: `test_startup_integration.py` and the two `oauth_security` test files give a clear template for both the new module's unit tests and its `lifespan()` integration test; `healthcheck()` and `llm_config.py` currently have no dedicated tests, so all new tests are additions rather than modifications of existing suites. Risk is concentrated in two small, well-scoped spots: (1) correctly reusing the `LLM_PROXY_MODE == "lite_llm"` precedent rather than only checking `is_litellm_enabled()`, and (2) the `healthcheck()` signature change to reach `app.state` (minor, no existing test to break).
