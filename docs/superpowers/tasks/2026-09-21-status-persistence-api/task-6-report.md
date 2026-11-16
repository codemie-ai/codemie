DONE

## Summary

Implemented Task 6: `ModelNotSelectableError` exception + `LLMService.assert_model_selectable()` guard, exactly as specified in the plan. This is the guard Tasks 7, 8, 9 will call at write-path call sites (create/update assistant, update_conversation, create/update workflow) to block selecting a deprecated or unavailable model.

## What was implemented

1. `src/codemie/core/exceptions.py` — added `ModelNotSelectableError(Exception)` right after `WorkflowGenerationError` (before `TokenLimitExceededException`), matching the exact code block from the plan verbatim (`__init__(self, base_name: str, reason: str)`, stores `base_name`/`reason`, message `f"Model '{base_name}' is not selectable: {reason}"`).

2. `src/codemie/service/llm_service/llm_service.py`:
   - Added `from codemie.core.exceptions import ModelNotSelectableError` import alongside other imports.
   - Added `assert_model_selectable(self, base_name: str) -> None` method, placed after `get_allowed_embedding_models`, right before the module-level `llm_service = LLMService(llm_config)` instantiation. Logic matches the plan exactly:
     - Looks up model via `next((m for m in self.get_all_llm_model_info() if m.base_name == base_name), None)`.
     - Returns silently if no match (unknown base_name — caller's own validation handles this elsewhere).
     - Raises `ModelNotSelectableError(base_name, "model is deprecated")` if `status == ModelStatus.DEPRECATED`.
     - Raises `ModelNotSelectableError(base_name, "model is not available")` if `not model.enabled or status == ModelStatus.DISABLED` (defense-in-depth).
   - Did not touch `_merge_persisted_status`, `initialize_default_litellm_models`, `_filter_models_by_visibility`, or `get_allowed_router_options` (owned by earlier tasks).

3. `tests/codemie/service/test_llm_service.py` — added new `TestAssertModelSelectable` class with 4 test cases (test-first, verified RED then GREEN):
   - `test_raises_when_status_deprecated`
   - `test_raises_when_enabled_false_defense_in_depth`
   - `test_does_not_raise_when_enabled_and_status_enabled`
   - `test_does_not_raise_for_unknown_base_name`
   
   Also added top-level import `from codemie.core.exceptions import ModelNotSelectableError`.

Extended the existing (already Task-4/5-modified) file at `tests/codemie/service/test_llm_service.py`, per the pre-made ruling — did NOT create anything under `tests/codemie/service/llm_service/`.

## TDD verification

- RED: ran `poetry run pytest tests/codemie/service/test_llm_service.py -o addopts="--import-mode=importlib" -k TestAssertModelSelectable` before implementing — failed with `ImportError: cannot import name 'ModelNotSelectableError' from 'codemie.core.exceptions'` (expected: import doesn't exist yet).
- GREEN: implemented exception + method, re-ran the full file.

## Test command run

```
poetry run pytest tests/codemie/service/test_llm_service.py -o addopts="--import-mode=importlib" > /Users/Aliaksei_Hurynovich/Developer/_codemie/codemie/docs/superpowers/tasks/2026-09-21-status-persistence-api/.test.log 2>&1; echo "EXIT=$?"; tail -25 <logfile>
```

Result: `EXIT=0`, `32 passed, 1 warning in 1.13s` (all pre-existing tests from Tasks 1-5 plus the 4 new ones pass; no regressions).

## Commit

- SHA: `b953993f788ca07ac25eda2f61955965c1bf2bad`
- Message: `EPMCDME-15080: Add ModelNotSelectableError and assert_model_selectable guard`
- Files changed (verified via `git show --stat HEAD`): exactly
  - `src/codemie/core/exceptions.py` (+9)
  - `src/codemie/service/llm_service/llm_service.py` (+16)
  - `tests/codemie/service/test_llm_service.py` (+43)
- Confirmed pre-existing dirty files (`config/customer/customer-config.yaml`, `.ai-run/sdlc-factory/doctor.json`, `.codemie-repos/`) were NOT staged — verified with `git status` before `git add`.

## Self-review notes

- Exception name, constructor signature, and method name/signature match the plan's verbatim code blocks exactly — no renaming or restructuring, as required for the three downstream implementers (Tasks 7, 8, 9).
- `assert_model_selectable` uses `get_all_llm_model_info()`, which already reflects the Task 4/5 persisted-status merge in LiteLLM-proxy mode, or YAML defaults (`ModelStatus.ENABLED`) otherwise — no new merge logic added, consistent with "guard, not source of truth" design.
- Did not modify any of the methods owned by earlier tasks (`_merge_persisted_status`, `initialize_default_litellm_models`, `_filter_models_by_visibility`, `get_allowed_router_options`).
- No `git add -A`/`-a` used; explicit path-based `git add` for the three intended files only.
