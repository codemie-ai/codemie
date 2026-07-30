# Technical Research

**Task**: image generation gemini litellm vertexai dial
**Generated**: 2026-07-29T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

Bug: generate_workspace_image_v2 tool in CodeMie fails with HTTP 404 "Route is not found" when calling the DIAL/LiteLLM image generation endpoint.

Error from production (LiteLLM):
  litellm.NotFoundError: Vertex_aiException
  Publisher model `projects/or2-msq-epmd-edp-anthos-t1iylu/locations/global/publishers/google/models/gemini-3.1-flash-image-preview` was not found or your project does not have access to it.

Error from DIAL (lab):
  HTTP 404 "Route is not found" when calling /openai/images/generations or /openai/images/edits with model `gemini-3.1-flash-image-preview`.

The model `gemini-3.1-flash-image-preview` is listed in DIAL with `chat_completion: True` but does NOT support the OpenAI-compatible images API endpoint.

The code flow:
1. Config: IMAGE_GENERATION_MODEL defaults to "gemini-3.1-flash-image-preview" (config.py:67)
2. _build_workspace_image_generator() in toolkit_settings_service.py creates a LiteLLMImageGenerator when AZURE_OPENAI_URL is set
3. LiteLLMImageGenerator.edit() calls AzureOpenAI.images.edit() which calls the /openai/deployments/{model}/images/edits endpoint
4. DIAL returns 404 because gemini-3.1-flash-image-preview only supports chat_completion, not the images API

There is also a ChatModelImageGenerator class that uses LangChain chat model invoke — its edit() raises NotImplementedError.

Key question: What's the correct fix strategy? Options include:
A. Fix the model name to one that actually supports image generation via the images API
B. Route Gemini image models through ChatModelImageGenerator instead of LiteLLMImageGenerator
C. Implement ChatModelImageGenerator.edit() using the chat completion API for image generation

Jira: EPMCDME-13718

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/configs/config.py:67` — `IMAGE_GENERATION_MODEL` env var; defaults to `"gemini-3.1-flash-image-preview"`; three-level resolution: request override → assistant field → global config default
- `src/codemie_tools/data_management/file_system/image_generator.py` — defines the full generator abstraction:
  - `ImageGenerator` — structural `Protocol` with `.edit()` and `.generate()` methods
  - `LiteLLMImageGenerator` — wraps `AzureOpenAI`; `.edit()` calls `client.images.edit()` → maps to `/openai/deployments/{model}/images/edits`; `.generate()` calls `client.images.generate()`
  - `ChatModelImageGenerator` — wraps a LangChain `BaseChatModel`; `.generate()` invokes the model via multimodal message; `.edit()` raises `NotImplementedError` (stub, never implemented)
  - `is_gemini_image_model(model_id)` — name-based heuristic returning `True` when both `"gemini"` and `"image"` appear in `model_id`
- `src/codemie/service/tools/toolkit_settings_service.py` — `_build_workspace_image_generator()`:
  - Branch 1: `LLM_PROXY_ENABLED + LITE_LLM_URL` → returns `LiteLLMImageGenerator` (production path, triggers the LiteLLM `NotFoundError`)
  - Branch 2: `AZURE_OPENAI_URL + AZURE_OPENAI_API_KEY` → returns `LiteLLMImageGenerator` (lab/DIAL path, triggers the 404)
  - No Branch 3: no `ChatModelImageGenerator` fallback; returns `None` if neither URL is set
  - Contrast: `_build_image_generator()` (file-system variant) DOES have a `ChatModelImageGenerator` fallback via `get_llm_by_credentials()`
- `src/codemie_tools/data_management/workspace/generate_image_tool_v2.py` — `GenerateWorkspaceImageToolV2.execute()`:
  - Always calls `image_generator.edit()` (never `.generate()`)
  - Gemini path: uses `extra_body={"response_format": {"image": {"aspect_ratio": ..., "image_size": ...}}}`, no `size`/`output_format`
  - Non-Gemini path: uses `size` and `output_format` parameters
  - Gemini detection here also relies on `is_gemini_image_model()`
- `src/codemie_tools/data_management/workspace/toolkit.py` — `AgentWorkspaceToolkit.get_tools()` instantiates `GenerateWorkspaceImageToolV2` with the `image_generator` injected
- `src/codemie/service/tools/toolkit_service.py:316` — `get_workspace_image_generation_tool()` — entry point guarded by `assistant.enable_image_generation == True`
- `src/codemie/configs/llm_config.py` — `LLMModel` dataclass has `supports_image_generation: bool = False`; populated from YAML configs but unused in `_build_workspace_image_generator` routing logic
- `config/llms/llm-dial-config.yaml` and `llm-gcp-config.yaml` — `gemini-3.1-flash-image-preview` entry has `provider: google_vertexai` but `supports_image_generation` is NOT set to `true`

### Architecture and Layers Affected

- **Config layer** — `config.py:67` (`IMAGE_GENERATION_MODEL`), `llm-dial-config.yaml` / `llm-gcp-config.yaml` (`LLMModel.supports_image_generation`)
- **Service layer** — `toolkit_settings_service.py` (`_build_workspace_image_generator`), `toolkit_service.py` (`get_workspace_image_generation_tool`)
- **Generator abstraction layer** — `image_generator.py` (`ChatModelImageGenerator.edit()`, `is_gemini_image_model()`)
- **Tool implementation layer** — `generate_image_tool_v2.py` (`GenerateWorkspaceImageToolV2.execute()`)
- **External SDK layer** — `openai.AzureOpenAI` (images API), `langchain-google-vertexai.ChatVertexAI` (chat completion API)

### Integration Points

- **DIAL / LiteLLM proxy**: `LITE_LLM_URL` → `LiteLLMImageGenerator` → `AzureOpenAI` → `/openai/deployments/{model}/images/edits` (currently 404 for Gemini)
- **DIAL direct (Azure-compatible)**: `AZURE_OPENAI_URL` → `LiteLLMImageGenerator` → same images endpoint (same 404)
- **VertexAI via LangChain**: `ChatVertexAI` / `get_vertex_llm()` → multimodal chat completion — this is the path that WORKS for Gemini image models but is not wired into `_build_workspace_image_generator`
- **Internal**: `toolkit_settings_service` → `image_generator.py` → `generate_image_tool_v2.py` → `workspace/toolkit.py` → `toolkit_service.py`

### Patterns and Conventions

- `ImageGenerator` is a `typing.Protocol` (structural subtyping) — new implementations need only match the method signatures, no explicit base class inheritance required
- Three-level model resolution: request param → assistant field → global `IMAGE_GENERATION_MODEL` env var
- Generator construction uses environment variable branching (LiteLLM proxy → Azure direct → fallback), mirroring the pattern in `_build_image_generator` but missing the final ChatModel branch
- Gemini model detection uses the `is_gemini_image_model()` name-heuristic function; the `LLMModel.supports_image_generation` flag exists in config but is currently unused in workspace routing
- `ChatModelImageGenerator` uses LangChain `BaseChatModel.invoke()` with a multimodal `HumanMessage` containing image bytes as base64 `image_url` content parts

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/llm-providers.md` — covers provider configuration patterns and LiteLLM proxy gating; no image-generation-specific guidance found

### Architectural Decisions

- No ADRs specifically covering image generation routing strategy were found
- The existing `_build_image_generator` (file-system variant) does implement a ChatModel fallback — this is the precedent for the workspace variant to follow
- `LLMModel.supports_image_generation` field was added to `llm_config.py` but was never wired into the workspace generator selection logic, suggesting an incomplete earlier attempt to introduce model-capability-based routing

### Derived Conventions

- When two generator implementations exist for a domain, a capability-detection branch in the builder function selects between them — the file-system `_build_image_generator` is the template
- Model capability should be checked from `LLMModel.supports_image_generation` (config-driven) or from the `is_gemini_image_model()` heuristic (name-driven); the config-driven approach is more robust
- `ChatModelImageGenerator.generate()` (not `.edit()`) is the only implemented multimodal path in the chat model class; `.edit()` for inpainting would need to encode both the base image and mask as content parts

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie_tools/data_management/workspace/test_generate_image_tool_v2.py` — covers `GenerateWorkspaceImageToolV2.execute()`, Gemini plan with aspect ratio/size, size parsing helpers, base+mask image creation, normalize helpers
- `tests/codemie_tools/data_management/file_system/test_generate_image_tool.py` — covers `LiteLLMImageGenerator`, `ChatModelImageGenerator`, and the file-system `GenerateImageTool` (not the workspace v2 variant)
- `tests/codemie/service/tools/test_toolkit_settings_service.py` — covers model resolution priority and file-system toolkit construction; does NOT test `_build_workspace_image_generator` routing
- `tests/codemie/service/tools/test_image_generator_token_tracking.py` — covers `_build_image_generator` ChatModel path; applies only to the file-system variant, not the workspace builder

### Testing Framework and Patterns

- Framework: **pytest 8.3.1**, pytest-mock 3.14, unittest.mock
- Patterns: `mocker.patch()` for external SDK calls, mock `ImageGenerator` implementations passed directly to tool constructors, fixture-based image byte generation for encode/decode tests

### Coverage Gaps

- `_build_workspace_image_generator()` routing logic has **no test coverage** — no test verifies which generator class is returned under which env var combination
- `ChatModelImageGenerator.edit()` has no tests (the method currently raises `NotImplementedError`)
- The new Gemini routing branch (once added) would need tests for: Gemini model → `ChatModelImageGenerator` returned; non-Gemini model → `LiteLLMImageGenerator` returned; `ChatModelImageGenerator.edit()` invokes LangChain with correct multimodal content

---

## 5. Configuration and Environment

### Environment Variables

- `IMAGE_GENERATION_MODEL` — global default image model name; currently `"gemini-3.1-flash-image-preview"` (config.py:67); root of the misrouting
- `AZURE_OPENAI_URL` — DIAL endpoint URL; triggers `LiteLLMImageGenerator` branch in `_build_workspace_image_generator`
- `AZURE_OPENAI_API_KEY` — paired with `AZURE_OPENAI_URL` for direct Azure/DIAL auth
- `LLM_PROXY_ENABLED` — boolean flag enabling LiteLLM proxy path (takes priority over Azure direct)
- `LITE_LLM_URL` — LiteLLM proxy base URL; used in production path that triggers `NotFoundError`

### Configuration Files

- `src/codemie/configs/config.py` — all env var declarations including `IMAGE_GENERATION_MODEL`
- `config/llms/llm-dial-config.yaml` — DIAL model registry; `gemini-3.1-flash-image-preview` entry present with `provider: google_vertexai` but `supports_image_generation` not set
- `config/llms/llm-gcp-config.yaml` — GCP model registry; same model entry, same omission

### Feature Flags and Deployment Concerns

- `assistant.enable_image_generation` — per-assistant toggle in `toolkit_service.py:316`; gates entry to the workspace image generation tool
- `LLM_PROXY_ENABLED` — acts as a routing flag between LiteLLM proxy and direct Azure path; both paths currently land on `LiteLLMImageGenerator`
- No migration required; the fix is purely code/config change (no DB schema impact)
- If `ChatModelImageGenerator` path is used for VertexAI Gemini, `GOOGLE_APPLICATION_CREDENTIALS` or equivalent GCP auth must be available in the deployment environment — this should already be present given other VertexAI usage, but should be verified

---

## 6. Risk Indicators

- **Root cause confirmed in `_build_workspace_image_generator`**: both branches (`LITE_LLM_URL` and `AZURE_OPENAI_URL`) unconditionally return `LiteLLMImageGenerator`, which then calls the OpenAI images endpoint that DIAL does not support for Gemini models — this is a routing gap, not a model availability issue
- **`ChatModelImageGenerator.edit()` is a stub** — `raise NotImplementedError` at `image_generator.py`; Option C requires nontrivial implementation: LangChain multimodal `HumanMessage` with base64-encoded image bytes for both the base image and the inpainting mask
- **No tests for `_build_workspace_image_generator` routing** — `test_toolkit_settings_service.py` does not assert which generator class is instantiated; new routing logic will land untested without deliberate test additions
- **`LLMModel.supports_image_generation` field is inert** — the config field exists and is populated from YAML, but `_build_workspace_image_generator` does not read it; using it as the routing discriminator requires wiring it through from model config lookup to builder logic
- **Option A (change model name) may be blocked externally** — no evidence of an alternative model in DIAL/LiteLLM that supports both Gemini-quality image generation AND the OpenAI images API endpoint; this option depends on infrastructure availability outside the codebase
- **Option B (route to ChatModel without implementing `.edit()`) is incomplete** — `ChatModelImageGenerator.edit()` raises `NotImplementedError`; Option B must be combined with Option C to be functional
- **Gemini model detection is name-based heuristic** — `is_gemini_image_model()` uses string matching on model ID; fragile if model names change (e.g., a model named `gemini-3.2-pro` for chat would incorrectly match if it contained "image" elsewhere)
- **`generate_image_tool_v2.py` always calls `.edit()`, never `.generate()`** — the Gemini multimodal path must be implemented in `ChatModelImageGenerator.edit()` specifically; the existing `ChatModelImageGenerator.generate()` implementation cannot be reused directly
- **No guides on image generation architecture** — `llm-providers.md` covers general LiteLLM proxy config but not image routing; conventions must be derived from `_build_image_generator` as the reference implementation

---

## 7. Summary for Complexity Assessment

The bug is a confirmed routing defect: `_build_workspace_image_generator()` in `toolkit_settings_service.py` always constructs a `LiteLLMImageGenerator`, which calls the OpenAI-compatible images API endpoint. DIAL routes `gemini-3.1-flash-image-preview` exclusively via the chat completion endpoint, not the images endpoint, causing the 404. The fix touches four layers — config (`IMAGE_GENERATION_MODEL`, YAML model registry), service (`_build_workspace_image_generator` routing branch), generator abstraction (`ChatModelImageGenerator.edit()` implementation), and indirectly the tool layer (`GenerateWorkspaceImageToolV2` already has Gemini-specific `extra_body` logic, which should remain intact). Estimated file change surface: 3–5 files, with the bulk of new code in `image_generator.py` (implementing `.edit()`) and `toolkit_settings_service.py` (adding a Gemini detection branch).

The task is technically novel in one dimension: `ChatModelImageGenerator.edit()` has never been implemented, and implementing it requires encoding both base image and inpainting mask as LangChain multimodal content parts, then extracting the generated image from the LangChain response. The surrounding patterns are well-established — `ChatModelImageGenerator.generate()` provides the exact template, and the file-system `_build_image_generator` provides the template for adding a conditional ChatModel branch in the builder. Option C (implement `.edit()` + add Gemini routing branch) is the most self-contained fix and avoids dependency on external infrastructure changes.

Test coverage posture is mixed-to-weak for the affected area. `GenerateWorkspaceImageToolV2.execute()` has reasonable test coverage in `test_generate_image_tool_v2.py`, but `_build_workspace_image_generator` routing has no tests at all, and `ChatModelImageGenerator.edit()` has no tests because it was never implemented. The fix must include: unit tests for the new `.edit()` implementation (mocking the LangChain model invoke), and routing tests in `test_toolkit_settings_service.py` asserting that a Gemini image model produces a `ChatModelImageGenerator` while non-Gemini produces a `LiteLLMImageGenerator`. Complexity is moderate — the logic is well-bounded, but the new `ChatModelImageGenerator.edit()` implementation requires careful handling of image encoding, content-part structure, and response parsing, all of which are not covered by existing documentation.
