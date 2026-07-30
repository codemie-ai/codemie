# EPMCDME-13718 Fix Image Generation 404 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `generate_workspace_image_v2` 404 / `NotFoundError` for Gemini image models by implementing `ChatModelImageGenerator.edit()` and routing Gemini models to it in `_build_workspace_image_generator()`.

**Architecture:** `_build_workspace_image_generator()` currently always returns `LiteLLMImageGenerator`, which calls the OpenAI images API (`/openai/images/edits`) that Gemini does not support. The fix inserts a Gemini-detection branch (using the existing `is_gemini_image_model()` helper) that returns `ChatModelImageGenerator` instead, then implements the stub `edit()` method on that class via a multimodal LangChain `HumanMessage` (matching the pattern already used by `generate()`).

**Tech Stack:** Python 3.12, pytest 8.3.1, pytest-mock 3.14, LangChain (`langchain-core`), stdlib `base64`.

## Global Constraints

- No new package dependencies.
- No config file changes.
- All new test classes follow the `unittest.TestCase` + `unittest.mock` pattern used in the existing test files.
- Every commit must include the Apache 2.0 license header (already present in modified files; no action needed for files that are only edited, not created).
- Run tests with `pytest <path> -v`; full suite with `make test`.

---

### Task 1: Implement `ChatModelImageGenerator.edit()`

**Test-first: yes — `test_edit_returns_b64_from_image_url_part` must fail with `NotImplementedError` before implementation.**

**Files:**
- Modify: `tests/codemie_tools/data_management/file_system/test_generate_image_tool.py` (add tests to `TestChatModelImageGenerator`)
- Modify: `src/codemie_tools/data_management/file_system/image_generator.py:167-177` (replace stub)

**Interfaces:**
- Consumes: `ChatModelImageGenerator(model, model_id)` — already constructed; `model.invoke(messages)` returns `AIMessage`
- Produces: `ChatModelImageGenerator.edit(prompt, image, mask=None, ...) -> tuple[str|None, str|None]` — consumed by Task 2 (routing) and by `generate_image_tool_v2.py` which always calls `.edit()`

- [ ] **Step 1: Write the failing tests**

  Append a new test class at the bottom of `TestChatModelImageGenerator` in
  `tests/codemie_tools/data_management/file_system/test_generate_image_tool.py`.
  Replace the single existing `test_edit_not_supported` test with the full suite below
  (the old test expects `NotImplementedError`; it must be removed so the new tests can be
  the failing baseline, and its assertion is replaced by the positive tests):

  ```python
  # In class TestChatModelImageGenerator — replace test_edit_not_supported with:

  def test_edit_returns_b64_from_image_url_part(self):
      content = [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_B64_DATA}"}}]
      gen = ChatModelImageGenerator(self._make_model(content))
      url, b64 = gen.edit("redraw the sky", b"fake-image-bytes")
      self.assertIsNone(url)
      self.assertEqual(b64, _B64_DATA)

  def test_edit_returns_b64_from_data_part(self):
      content = [{"type": "media", "data": _B64_DATA}]
      gen = ChatModelImageGenerator(self._make_model(content))
      url, b64 = gen.edit("redraw the sky", b"fake-image-bytes")
      self.assertIsNone(url)
      self.assertEqual(b64, _B64_DATA)

  def test_edit_returns_url_when_image_url_is_external(self):
      content = [{"type": "image_url", "image_url": {"url": _IMAGE_URL}}]
      gen = ChatModelImageGenerator(self._make_model(content))
      url, b64 = gen.edit("redraw the sky", b"fake-image-bytes")
      self.assertEqual(url, _IMAGE_URL)
      self.assertIsNone(b64)

  def test_edit_returns_none_tuple_when_no_image_parts(self):
      gen = ChatModelImageGenerator(self._make_model([]))
      url, b64 = gen.edit("redraw the sky", b"fake-image-bytes")
      self.assertIsNone(url)
      self.assertIsNone(b64)

  def test_edit_sends_multimodal_message_with_image_and_text(self):
      import base64 as _b64
      image_bytes = b"raw-png-bytes"
      expected_b64 = _b64.b64encode(image_bytes).decode()
      model = self._make_model([])
      gen = ChatModelImageGenerator(model)
      gen.edit("add clouds", image_bytes)
      invoke_args = model.invoke.call_args[0][0]          # list[HumanMessage]
      content = invoke_args[0].content                    # list of dicts
      self.assertEqual(len(content), 2)
      image_part = content[0]
      text_part = content[1]
      self.assertEqual(image_part["type"], "image_url")
      self.assertEqual(image_part["image_url"]["url"], f"data:image/png;base64,{expected_b64}")
      self.assertEqual(text_part["type"], "text")
      self.assertEqual(text_part["text"], "add clouds")
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  pytest tests/codemie_tools/data_management/file_system/test_generate_image_tool.py::TestChatModelImageGenerator -v
  ```

  Expected: 5 new tests FAIL (some with `NotImplementedError`, `test_edit_sends_multimodal_message` may fail because edit() discards its args currently).

- [ ] **Step 3: Implement `ChatModelImageGenerator.edit()`**

  In `src/codemie_tools/data_management/file_system/image_generator.py`, replace lines 167–177
  (the entire `edit` method body from `def edit` to `raise NotImplementedError`):

  ```python
  def edit(
      self,
      prompt: str,
      image: bytes,
      mask: bytes | None = None,
      size: str | None = None,
      output_format: str | None = None,
      extra_body: dict[str, Any] | None = None,
  ) -> tuple[str | None, str | None]:
      import base64
      del mask, size, output_format, extra_body
      b64 = base64.b64encode(image).decode()
      content = [
          {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
          {"type": "text", "text": prompt},
      ]
      response: AIMessage = self._model.invoke([HumanMessage(content=content)])
      if isinstance(response.content, list):
          for part in response.content:
              if isinstance(part, dict):
                  if url := part.get("image_url", {}).get("url"):
                      return resolve_image_url(url)
                  if b64_data := part.get("data"):
                      return None, b64_data
      return None, None
  ```

  Note: `mask`, `size`, `output_format`, `extra_body` are accepted for `Protocol` compliance but ignored — Gemini chat completion does not support OpenAI-style mask inpainting.

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  pytest tests/codemie_tools/data_management/file_system/test_generate_image_tool.py::TestChatModelImageGenerator -v
  ```

  Expected: all tests PASS including the 5 new `edit` tests.

- [ ] **Step 5: Run full file test to check no regressions**

  ```bash
  pytest tests/codemie_tools/data_management/file_system/test_generate_image_tool.py -v
  ```

  Expected: all tests PASS.

- [ ] **Step 6: Commit**

  ```bash
  git add src/codemie_tools/data_management/file_system/image_generator.py \
          tests/codemie_tools/data_management/file_system/test_generate_image_tool.py
  git commit -m "feat(EPMCDME-13718): implement ChatModelImageGenerator.edit() via LangChain multimodal HumanMessage"
  ```

---

### Task 2: Add Gemini routing branch in `_build_workspace_image_generator()`

**Test-first: yes — `test_build_workspace_image_generator_gemini_model_returns_chat_model_generator` must fail (returning `LiteLLMImageGenerator` instead of `ChatModelImageGenerator`) before the routing change.**

**Files:**
- Modify: `tests/codemie/service/tools/test_toolkit_settings_service.py` (add new test class)
- Modify: `src/codemie/service/tools/toolkit_settings_service.py:97-127` (update imports + add branch)

**Interfaces:**
- Consumes: `ChatModelImageGenerator` (from Task 1), `is_gemini_image_model()`, `get_llm_by_credentials()` (already imported at line 26)
- Produces: `_build_workspace_image_generator()` returns `ChatModelImageGenerator` for Gemini models and `LiteLLMImageGenerator` for all other models — consumed by `get_agent_workspace_toolkit()` (line 253) and `get_workspace_image_generation_tool()` (line 266)

- [ ] **Step 1: Write the failing tests**

  Append a new class at the bottom of
  `tests/codemie/service/tools/test_toolkit_settings_service.py`:

  ```python
  class TestBuildWorkspaceImageGenerator:
      """Tests for _build_workspace_image_generator routing."""

      @patch("codemie.service.tools.toolkit_settings_service.config.LLM_PROXY_ENABLED", True)
      @patch("codemie.service.tools.toolkit_settings_service.config.LITE_LLM_URL", "https://litellm.example.com")
      @patch("codemie.service.tools.toolkit_settings_service.config.LITE_LLM_APP_KEY", "test-key")
      @patch("codemie.service.tools.toolkit_settings_service.config.OPENAI_API_VERSION", "2025-04-01-preview")
      @patch("codemie.service.tools.toolkit_settings_service.config.LLM_PROXY_TIMEOUT", "60")
      @patch("codemie.service.tools.toolkit_settings_service.get_llm_by_credentials")
      def test_build_workspace_image_generator_gemini_model_returns_chat_model_generator(
          self, mock_get_llm
      ):
          from codemie_tools.data_management.file_system.image_generator import ChatModelImageGenerator

          mock_get_llm.return_value = MagicMock()
          assistant = MagicMock()
          assistant.image_generation_model = "gemini-3.1-flash-image-preview"

          result = ToolkitSettingService._build_workspace_image_generator(assistant)

          assert isinstance(result, ChatModelImageGenerator)
          mock_get_llm.assert_called_once_with(llm_model="gemini-3.1-flash-image-preview")

      @patch("codemie.service.tools.toolkit_settings_service.config.LLM_PROXY_ENABLED", True)
      @patch("codemie.service.tools.toolkit_settings_service.config.LITE_LLM_URL", "https://litellm.example.com")
      @patch("codemie.service.tools.toolkit_settings_service.config.LITE_LLM_APP_KEY", "test-key")
      @patch("codemie.service.tools.toolkit_settings_service.config.OPENAI_API_VERSION", "2025-04-01-preview")
      @patch("codemie.service.tools.toolkit_settings_service.config.LLM_PROXY_TIMEOUT", "60")
      def test_build_workspace_image_generator_non_gemini_model_returns_litellm_generator(self):
          from codemie_tools.data_management.file_system.image_generator import LiteLLMImageGenerator

          assistant = MagicMock()
          assistant.image_generation_model = "gpt-image-2"

          result = ToolkitSettingService._build_workspace_image_generator(assistant)

          assert isinstance(result, LiteLLMImageGenerator)
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  pytest tests/codemie/service/tools/test_toolkit_settings_service.py::TestBuildWorkspaceImageGenerator -v
  ```

  Expected: `test_build_workspace_image_generator_gemini_model_returns_chat_model_generator` FAIL
  (returns `LiteLLMImageGenerator`, not `ChatModelImageGenerator`).
  `test_build_workspace_image_generator_non_gemini_model_returns_litellm_generator` should PASS (already correct behaviour).

- [ ] **Step 3: Update `_build_workspace_image_generator()` imports and add Gemini branch**

  In `src/codemie/service/tools/toolkit_settings_service.py`, replace the existing
  `_build_workspace_image_generator` method (lines 97–126) with:

  ```python
  @staticmethod
  def _build_workspace_image_generator(assistant: Optional[Assistant] = None, request: Optional[object] = None):
      from codemie_tools.data_management.file_system.image_generator import (
          ChatModelImageGenerator,
          LiteLLMImageConfig,
          LiteLLMImageGenerator,
          is_gemini_image_model,
      )

      image_generation_model = ToolkitSettingService._resolve_image_generation_model(assistant, request)

      if image_generation_model and is_gemini_image_model(image_generation_model):
          return ChatModelImageGenerator(
              model=get_llm_by_credentials(llm_model=image_generation_model),
              model_id=image_generation_model,
          )

      if config.LLM_PROXY_ENABLED and config.LITE_LLM_URL and image_generation_model:
          return LiteLLMImageGenerator(
              LiteLLMImageConfig(
                  api_base=config.LITE_LLM_URL,
                  api_key=config.LITE_LLM_APP_KEY,
                  api_version=config.OPENAI_API_VERSION,
                  model_id=image_generation_model,
                  timeout=float(config.LLM_PROXY_TIMEOUT),
              )
          )

      if config.AZURE_OPENAI_URL and config.AZURE_OPENAI_API_KEY and image_generation_model:
          return LiteLLMImageGenerator(
              LiteLLMImageConfig(
                  api_base=config.AZURE_OPENAI_URL,
                  api_key=config.AZURE_OPENAI_API_KEY,
                  api_version=config.OPENAI_API_VERSION,
                  model_id=image_generation_model,
              )
          )

      return None
  ```

  The Gemini branch is inserted **before** the LiteLLM proxy and Azure branches so that
  `is_gemini_image_model()` wins regardless of which URL env vars are set. `get_llm_by_credentials`
  is already imported at line 26 — no top-level import change needed.

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  pytest tests/codemie/service/tools/test_toolkit_settings_service.py::TestBuildWorkspaceImageGenerator -v
  ```

  Expected: both tests PASS.

- [ ] **Step 5: Run full service test file to check no regressions**

  ```bash
  pytest tests/codemie/service/tools/test_toolkit_settings_service.py -v
  ```

  Expected: all tests PASS.

- [ ] **Step 6: Run lint**

  ```bash
  make ruff
  ```

  Expected: no violations.

- [ ] **Step 7: Run full test suite**

  ```bash
  make test
  ```

  Expected: all tests PASS.

- [ ] **Step 8: Commit**

  ```bash
  git add src/codemie/service/tools/toolkit_settings_service.py \
          tests/codemie/service/tools/test_toolkit_settings_service.py
  git commit -m "fix(EPMCDME-13718): route Gemini image models through ChatModelImageGenerator in _build_workspace_image_generator"
  ```
