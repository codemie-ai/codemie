# Task 11 Report — Confirm `status` on GET /v1/llm_models

DONE

## Summary

The plan's premise was confirmed empirically: `LLMModel.status` (landed in Tasks 1-10) already
round-trips through the existing `response_model=List[Union[LLMModel, LlmRouterOption]]` /
`response_model_exclude_none=True` serialization in
`src/codemie/rest_api/routers/llm_models.py` with **zero production code changes**.

## What was added

New test `test_llm_models_status_serialization` in
`tests/codemie/rest_api/routers/test_llm_models.py`, mirroring the existing
`test_llm_models_is_premium_serialization` pattern:

- Mocks `llm_service.get_allowed_chat_models` to return one `LLMModel` with
  `status=ModelStatus.DEPRECATED` explicitly set, and one with no `status=` kwarg at all.
- Asserts `GET /v1/llm_models` response JSON contains `"status": "deprecated"` for the first
  model and `"status": "enabled"` (the field default) for the second.
- This test **passed on first run**, before any production file was touched — confirming the
  plan's stated expectation. No router code was added.

## Unexpected finding (in-scope fix, test-file only)

Running the full test file surfaced 3 **pre-existing** failures unrelated to my new test:
`test_get_llm_models`, `test_get_embeddings_models`, `test_get_image_generation_models`. These
tests assert exact dict equality on the full response JSON and were written before Tasks 1-10
added the `status` field to `LLMModel`. Since `status` now always serializes (it has a
non-Optional default of `ModelStatus.ENABLED`, so `response_model_exclude_none=True` never hides
it), these hard-coded expected dicts became stale and started failing — confirmed by inspecting
`git diff --stat` on my own edit (purely additive, +25 lines, nothing removed/changed), which
proves these 3 failures are not caused by my change but by earlier landed tasks.

This is squarely inside my ownership of `tests/codemie/rest_api/routers/test_llm_models.py` (not
router code), and the plan explicitly asked me to "confirm the rest of the file's existing ~10
tests still pass." I made the minimal mechanical fix: added `'status': 'enabled'` to each of the
5 expected-dict literals in those 3 tests (all use default-enabled models with no explicit
`status=`). No other production or test logic was touched.

## Files changed

- `tests/codemie/rest_api/routers/test_llm_models.py` — added 1 new test
  (`test_llm_models_status_serialization`) and updated 5 pre-existing expected-dict literals
  across 3 pre-existing tests to include `'status': 'enabled'`.
- `src/codemie/rest_api/routers/llm_models.py` — **not touched** (confirmed no gap; matches plan's
  expected outcome).

## Test command and result

```
poetry run pytest tests/codemie/rest_api/routers/test_llm_models.py -o addopts="--import-mode=importlib"
```

- First run (new test only, before fixing the 3 stale tests): `EXIT=1` — 3 failed (pre-existing,
  unrelated to new test), 13 passed (including the new test).
- After fixing the 5 stale dict literals: `EXIT=0` — **16 passed**, 3 warnings (pre-existing
  deprecation warnings, unrelated).

## Commit

- SHA: a8deee3f9ed1dcc28bd684e6ac867b5a2f3c0978
- Message: `EPMCDME-15080: Confirm model status is serialized on GET /v1/llm_models`
- Staged: `tests/codemie/rest_api/routers/test_llm_models.py` only.
- Verified via `git show --stat HEAD` that only this file changed.
