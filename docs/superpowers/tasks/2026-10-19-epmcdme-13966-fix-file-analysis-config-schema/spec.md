# Spec: EPMCDME-13966 — FileAnalysisConfig dropped from GET /v1/tools/configs

Size: XS (9/36). Source research: `technical-analysis.md` in this directory.

## Problem

`FileAnalysisConfig.chat_model` (`src/codemie_tools/file_analysis/models.py:41`) is typed
`Optional[BaseChatModel]` with `Field(default=None, exclude=True)`. `exclude=True` only affects
serialization, so `FileAnalysisConfig.model_json_schema()` raises
`PydanticInvalidForJsonSchema` (CallableSchema). `_process_config_class`
(`src/codemie_tools/base/toolkit_provider.py` ~L416-446) catches it, logs ERROR, and
`get_available_tools_configs_info()` returns 29 of 30 configs. `GET /v1/tools/configs`
(`src/codemie/rest_api/routers/tool.py:46-63`) answers 200 with the config missing and an ERROR
on every call.

## Intent

Make the runtime-only `chat_model` field invisible to JSON-schema generation while keeping its
runtime and serialization behavior unchanged, so every registered config is returned and no
error is logged. Add regression tests so this class of silent omission is caught in CI.

## Approach

- Wrap the field type in pydantic's `SkipJsonSchema` (from `pydantic.json_schema`), keeping
  `default=None` and `exclude=True`. This is the ticket's suggested fix; Stage 1 verified it on a
  copy of the class under the pinned pydantic 2.13.5. It is the first `SkipJsonSchema` use in the
  repo; the existing `Field(exclude=True)` convention stays as is everywhere else.
- No change to the provider, the router or any other config class.
- The endpoint key is `fileanalysisconfig` (`config_class.__name__.lower()`); the ticket's
  `filesanalysisconfig` is a typo.

## Acceptance criteria

1. AC1: `FileAnalysisConfig.model_json_schema()` succeeds, and `chat_model` is absent from the
   schema's `properties`.
2. AC2: `get_available_tools_configs_info()` (real discovery, no mocks) includes an entry whose key
   is `fileanalysisconfig`.
3. AC3: Real discovery yields no failed config classes: the provider logs no
   `Error getting info from ...` and the summary reports `0 failed`.
4. AC4: Runtime behavior is unchanged: `FileAnalysisConfig(input_files=[], chat_model=<BaseChatModel>)`
   still validates and keeps the model on the instance (callers: `file_analysis/toolkit.py:101,121`,
   `service/tools/toolkit_service.py:1343`), and `chat_model` stays out of `model_dump()`.
5. AC5: Regression tests cover AC1 and AC4 in `tests/codemie_tools/file_analysis/`, and AC2 and AC3
   in `tests/codemie_tools/base/test_toolkit_provider.py`, using the existing autouse `lru_cache`
   clearing fixture (L37-55).

## Non-goals

- Changing `_process_config_class`'s broad `except Exception`, its log levels, or making
  `/v1/tools/configs` fail on a broken config. This is a follow-up candidate, not part of this fix.
- Migrating other `Field(exclude=True)` runtime fields to `SkipJsonSchema`.
- Changing the shape of the endpoint response, its `response_model`, or the OpenAPI baseline.
- Hiding `input_files` / `preconverted_content` from the newly returned `fileanalysisconfig`
  entry. Exposing them is the expected result of returning the config.
- Any `codemie-ui` or `codemie-sdk` change.

## Constraints

- The pydantic range `^2.9.2` already includes `SkipJsonSchema`, so no dependency change.
- Tests must not assert a hardcoded total config count ("30"). The count changes whenever a
  toolkit is added. Assert presence and zero failures instead.

## Left to the builder

- Exact test names. Whether AC3 is checked through log capture (`caplog`) or by comparing the
  number of discovered config classes to the number of returned entries.
- How the AC4 test builds a `BaseChatModel` (a langchain fake chat model or a minimal stub).
- Whether to strengthen the existing `test_integration_get_available_tools_configs_info` (L441,
  asserts only `len >= 0`) or add a new test next to it.
