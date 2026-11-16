DONE

## Task 5: Status-aware narrowing in the four `get_allowed_*` methods

### What was implemented

1. `src/codemie/service/llm_service/llm_service.py::_filter_models_by_visibility` (around line 527-553):
   extended the list comprehension in the `include_all=False` branch to also drop models where
   `model.status == ModelStatus.DISABLED`, alongside the existing `forbidden_for_web is not True`
   check. The `include_all=True` early return is unchanged, so `include_all=True` continues to
   bypass status narrowing exactly as it already bypasses `forbidden_for_web` narrowing.

2. `src/codemie/service/llm_service/llm_service.py::get_allowed_router_options` (around line 118-155):
   moved `capable`/`efficient` resolution (`by_name.get(...)`) up so they are available immediately
   after the existing `router.forbidden_for_web` check, and added a new check right after them:
   ```python
   if not include_all and (
       (capable is not None and capable.status == ModelStatus.DISABLED)
       or (efficient is not None and efficient.status == ModelStatus.DISABLED)
   ):
       continue
   ```
   No other logic in the method (the `_compute_router_capabilities` call, the `LlmRouterOption`
   construction, etc.) was touched.

### Ruling confirmation

`get_allowed_image_generation_models` was left completely untouched, per the ruling in the task
brief. It still uses its own explicit `supports_image_generation` filter and ignores
`include_all`/status entirely; no lines in that method were edited, and the pre-existing test
`test_get_allowed_image_generation_models_ignores_forbidden_for_web` still passes unchanged.

### Test-first (TDD) process

Extended `tests/codemie/service/test_llm_service.py` (the real, already-existing file — the
plan's stated path `tests/codemie/service/llm_service/test_llm_service.py` does not exist and was
not created, per the correction in the brief).

Added to `TestModelVisibilityFiltering`:
- `test_filter_models_status_enabled_included` — enabled=True, status=ENABLED, include_all=False → listed.
- `test_filter_models_status_disabled_excluded` — enabled=True, status=DISABLED, include_all=False → absent.
- `test_filter_models_status_deprecated_included` — enabled=True, status=DEPRECATED, include_all=False → listed, `status == ModelStatus.DEPRECATED`.
- `test_filter_models_status_disabled_included_when_include_all_true` — enabled=True, status=DISABLED, include_all=True → listed (include_all bypass).
- `test_get_allowed_chat_models_provider_disabled_wins_over_deprecated_status` — enabled=False, status=DEPRECATED, include_all=False → absent, exercised end-to-end via `get_allowed_chat_models` (provider `enabled=False` is already filtered upstream by `get_model_info`/`get_all_llm_model_info`, before `_filter_models_by_visibility` ever sees the model — confirmed this is the real call chain, so the test targets that chain rather than forcing a redundant `enabled` check into the private method).

Added new class `TestGetAllowedRouterOptionsStatusNarrowing` (constructs an `LLMRouter` with a
`SwitchyardConfig` directly and mocks `get_all_llm_model_info`/`get_llm_routers` on the service
instance, avoiding YAML/`SWITCHYARD_ENABLED` config wiring):
- `test_router_skipped_when_capable_model_disabled_and_include_all_false`
- `test_router_skipped_when_efficient_model_disabled_and_include_all_false`
- `test_router_included_when_disabled_status_and_include_all_true`
- `test_router_included_when_both_statuses_enabled` (sanity/regression check)

RED confirmed: ran the new tests before implementation — 3 failed for the expected reason (new
behavior not yet implemented: `test_filter_models_status_disabled_excluded`,
`test_router_skipped_when_capable_model_disabled_and_include_all_false`,
`test_router_skipped_when_efficient_model_disabled_and_include_all_false`); the other new tests
passed trivially because they didn't yet require the new narrowing (asserted pre-existing/no-op
behavior), which is expected and not a smell — the failures for the 3 cases above confirmed the
tests exercise the missing logic.

GREEN confirmed after implementing both edits: all 28 tests in the file pass, including the
pre-existing Task 4 tests (`TestInitializeDefaultLiteLLMModelsMergesPersistedStatus`) and all other
pre-existing tests (`TestLLMService`, `TestModelVisibilityFiltering`, `TestApplyPremiumFlags`).

### Exact test command and result

```
poetry run pytest tests/codemie/service/test_llm_service.py -o addopts="--import-mode=importlib" > /Users/Aliaksei_Hurynovich/Developer/_codemie/codemie/docs/superpowers/tasks/2026-09-21-status-persistence-api/.test.log 2>&1; echo "EXIT=$?"; tail -25 /Users/Aliaksei_Hurynovich/Developer/_codemie/codemie/docs/superpowers/tasks/2026-09-21-status-persistence-api/.test.log
```

Result: `EXIT=0`, `28 passed, 1 warning in 1.09s` (one pre-existing unrelated `FutureWarning` from
`google.auth.transport.grpc` about grpcio/PQC deprecation — not related to this change).

### Commit

SHA: `b35fb0598653cfe463f89adebf316fe7818fef26`
Message: `EPMCDME-15080: Add status-aware narrowing to get_allowed_* methods`

`git show --stat HEAD`:
```
 src/codemie/service/llm_service/llm_service.py |  14 ++-
 tests/codemie/service/test_llm_service.py      | 155 +++++++++++++++++++++++++
 2 files changed, 168 insertions(+), 1 deletion(-)
```
Only these two files were staged and committed (verified via `git status` before commit). Pre-existing
dirty files (`config/customer/customer-config.yaml`, `.ai-run/sdlc-factory/doctor.json`,
`.codemie-repos/`) were left untouched and unstaged.

### Self-review notes

- No changes to `initialize_default_litellm_models` / `_merge_persisted_status` (Task 4's code) —
  confirmed via diff, only the two named methods were touched.
- No new method (`assert_model_selectable`) was added — that's explicitly out of scope for this
  task (reserved for Task 6).
- `ModelStatus` was already imported in `llm_service.py` from Task 4; no new import needed.
- The router-options reorder only moved `capable`/`efficient` resolution earlier in the loop body;
  the `forbidden_for_web` check's position relative to `router.enabled` is unchanged, and
  `_compute_router_capabilities` is called exactly as before once both new checks pass.
- Confirmed `get_allowed_image_generation_models` diff is empty (untouched) and its dedicated test
  still passes.
