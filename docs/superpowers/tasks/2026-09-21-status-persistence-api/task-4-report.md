DONE

## What was implemented

- Added `LLMService._merge_persisted_status(models: List[LLMModel]) -> List[LLMModel]` in
  `src/codemie/service/llm_service/llm_service.py`. It opens a session via `get_session()`,
  calls `LLMModelStatusRepository.get_all(session)`, builds a `{base_name: status}` dict, and
  sets `model.status = ModelStatus(persisted_status)` if a persisted row exists for that
  `base_name`, else `ModelStatus.ENABLED`. It mutates every model in the passed list in place
  (no filtering/dropping) and returns the list.
- Called `_merge_persisted_status` for both `user_models.chat_models` and
  `user_models.embedding_models` at the top of `initialize_default_litellm_models`, before the
  existing assignment to `self._default_litellm_models` / `self._default_litellm_embeddings`.
- Added imports: `from codemie.clients.postgres import get_session`,
  `ModelStatus` added to the existing `from codemie.configs.llm_config import (...)` block, and
  `from codemie.repository.llm_model_status_repository import LLMModelStatusRepository`.

## Commit

- SHA: `d6cb6e1b9909f11d149899101d7935593eaccc0e`
- Message: `EPMCDME-15080: Merge persisted model status into catalog on initialize`
- `git show --stat HEAD` confirms exactly two files changed:
  - `src/codemie/service/llm_service/llm_service.py` (+17)
  - `tests/codemie/service/test_llm_service.py` (+73/-1)

## Tests

Added `TestInitializeDefaultLiteLLMModelsMergesPersistedStatus` to the existing
`tests/codemie/service/test_llm_service.py` (I did NOT create a new file at the plan's stated
path `tests/codemie/service/llm_service/test_llm_service.py` — that path does not exist and per
the ruling given, the existing `tests/codemie/service/test_llm_service.py` was extended instead).

Three test cases, mocking `codemie.service.llm_service.llm_service.get_session` and
`codemie.service.llm_service.llm_service.LLMModelStatusRepository` (same idiom as
`test_user_preferences_service.py`'s `@patch(".get_session")` / `@patch(".<repo>")` pattern):

1. `test_persisted_status_is_applied_to_matching_chat_model` — chat model `base_name="m1"` with
   a persisted row `status="deprecated"` ends up with `status == ModelStatus.DEPRECATED`.
2. `test_model_without_persisted_row_defaults_to_enabled` — chat model `base_name="m2"` with no
   matching persisted row ends up with `status == ModelStatus.ENABLED`.
3. `test_embedding_models_also_get_persisted_status` — embedding model `base_name="e1"` with a
   persisted row `status="deprecated"` ends up with `status == ModelStatus.DEPRECATED`, proving
   embedding models get the same treatment as chat models.

### RED (before implementation)

Command:
```
poetry run pytest tests/codemie/service/test_llm_service.py -o addopts="--import-mode=importlib"
```
Result: `3 failed, 16 passed` — all three new tests failed with
`AttributeError: <module 'codemie.service.llm_service.llm_service' ...> does not have the
attribute 'get_session'` (expected: the module didn't import `get_session`/
`LLMModelStatusRepository` yet, so `@patch` on those targets failed correctly — confirming the
tests exercise code that doesn't exist yet, not a typo).

### GREEN (after implementation)

Command:
```
poetry run pytest tests/codemie/service/test_llm_service.py -o addopts="--import-mode=importlib"
```
EXIT=0
Result: `19 passed, 1 warning` — all 3 new tests plus all 16 pre-existing tests in the file pass.

## Self-review notes

- Only touched `initialize_default_litellm_models` and added the new private helper
  `_merge_persisted_status`; did not touch `_filter_models_by_visibility`,
  `get_allowed_router_options`, or `assert_model_selectable` (reserved for Tasks 5/6).
- Did not touch Tasks 1-3 files (`llm_config.py`, `llm_model_status.py`,
  `llm_model_status_repository.py`, migrations).
- Only `src/codemie/service/llm_service/llm_service.py` and
  `tests/codemie/service/test_llm_service.py` were staged and committed. Verified via
  `git status` before commit and `git show --stat HEAD` after — pre-existing dirty files
  (`config/customer/customer-config.yaml`, `.ai-run/sdlc-factory/doctor.json`,
  `.codemie-repos/`) were left untouched/unstaged.
- `_merge_persisted_status` mutates the input list's model objects in place and also returns the
  list; `initialize_default_litellm_models` doesn't need the return value since the same list
  objects (`user_models.chat_models` / `user_models.embedding_models`) are assigned right after,
  but the helper still returns them per the plan's signature.
