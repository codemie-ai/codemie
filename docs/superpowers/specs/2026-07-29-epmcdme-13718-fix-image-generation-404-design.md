# EPMCDME-13718 — Fix generate_workspace_image_v2 404 Error for Gemini Image Models

## Problem

`generate_workspace_image_v2` fails with HTTP 404 / `Vertex_aiException: Publisher model not found` when the configured `IMAGE_GENERATION_MODEL` is `gemini-3.1-flash-image-preview` (the default).

Root cause: `_build_workspace_image_generator()` unconditionally returns `LiteLLMImageGenerator`, which calls `AzureOpenAI.images.edit()` → `/openai/images/edits` endpoint. Gemini image models do not expose this OpenAI-compatible images endpoint; they are only accessible via the chat completion API (`generateContent`).

## Solution

Two-part fix, approach A (minimal):

1. **Implement `ChatModelImageGenerator.edit()`** — sends base image + prompt as a multimodal `HumanMessage`; parses the response the same way as `generate()`.
2. **Add Gemini routing to `_build_workspace_image_generator()`** — check `is_gemini_image_model()` before the LiteLLM/DIAL branches; route matching models to `ChatModelImageGenerator` via `get_llm_by_credentials()`.

## Architecture

No new dependencies. No new classes or config keys. Two production files change.

```
generate_workspace_image_bytes()
  └─ image_generator.edit(prompt, image, mask)
       ├─ [Gemini model]   ChatModelImageGenerator.edit()  ← NEW implementation
       └─ [Other models]   LiteLLMImageGenerator.edit()    ← unchanged
```

`_build_workspace_image_generator()` routing order (after fix):

1. `is_gemini_image_model(model)` → `ChatModelImageGenerator(get_llm_by_credentials(model))`
2. `LLM_PROXY_ENABLED + LITE_LLM_URL` → `LiteLLMImageGenerator` (unchanged)
3. `AZURE_OPENAI_URL + AZURE_OPENAI_API_KEY` → `LiteLLMImageGenerator` (unchanged)
4. `None`

## Component Details

### `image_generator.py` — `ChatModelImageGenerator.edit()`

```python
def edit(self, prompt, image, mask=None, size=None, output_format=None, extra_body=None):
    import base64
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

- `mask`, `size`, `output_format`, `extra_body` are accepted (Protocol compliance) but ignored — Gemini chat does not support traditional mask-based inpainting or these OpenAI-specific parameters.
- Base image is encoded as `data:image/png;base64,…` inline data URL.
- Response parsing mirrors `generate()` exactly.
- `base64` import is local (already available in stdlib; no new package needed).

### `toolkit_settings_service.py` — `_build_workspace_image_generator()`

Add import of `ChatModelImageGenerator` and `is_gemini_image_model` alongside existing imports. Add the Gemini branch first:

```python
if image_generation_model and is_gemini_image_model(image_generation_model):
    return ChatModelImageGenerator(
        model=get_llm_by_credentials(llm_model=image_generation_model),
        model_id=image_generation_model,
    )
```

`get_llm_by_credentials` is already imported at line 26. It routes through LiteLLM chat or native Vertex AI depending on environment config — no additional wiring needed.

## Error Handling

- If the model returns no image parts (`None, None`), `normalize_generated_image_bytes()` in the tool raises `ValueError` — existing behaviour, no change needed.
- Network/model errors propagate as LangChain exceptions and are caught by the tool's existing `ValidationException` handler.

## Testing

### `toolkit_settings_service.py` routing tests

Two parametrized cases in `test_toolkit_settings_service.py`:

| Scenario | Expected generator type |
|---|---|
| `gemini-3.1-flash-image-preview` + `LLM_PROXY_ENABLED=True` | `ChatModelImageGenerator` |
| `gpt-image-2` + `LLM_PROXY_ENABLED=True` | `LiteLLMImageGenerator` |

### `ChatModelImageGenerator.edit()` unit tests

In the existing image generator test file:

- Mock `model.invoke()` to return `AIMessage(content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}])` → assert returns `(None, "abc")`
- Mock returning `AIMessage(content=[{"data": "xyz"}])` → assert returns `(None, "xyz")`
- Assert `edit()` does NOT raise `NotImplementedError`
- Assert multimodal message contains exactly two content parts: image + text

## Files Changed

| File | Change |
|---|---|
| `src/codemie_tools/data_management/file_system/image_generator.py` | Implement `ChatModelImageGenerator.edit()` |
| `src/codemie/service/tools/toolkit_settings_service.py` | Add Gemini routing branch in `_build_workspace_image_generator()` |
| `tests/codemie/service/tools/test_toolkit_settings_service.py` | Routing unit tests |
| `tests/codemie_tools/data_management/workspace/test_generate_image_tool_v2.py` | `edit()` unit tests |

## Out of Scope

- Merging `_build_image_generator` and `_build_workspace_image_generator` (separate cleanup ticket)
- Mask-based inpainting support for Gemini
- Fixing `generate_image_tool.py` (v1) — not reported as broken
