DONE

## What was implemented

- `src/codemie/rest_api/models/llm_model_status.py`: `LLMModelStatus(SQLModel, table=True)`, `__tablename__ = "llm_model_status"`, columns exactly as specified in the plan (`id`, `base_name` unique+indexed, `status`, `updated_by_id`, `updated_by_name`, `created_at`/`updated_at` as `TIMESTAMP(timezone=True)` with `server_default=func.now()` / `onupdate=func.now()`).
- `src/codemie/repository/llm_model_status_repository.py`: `LLMModelStatusRepository` with static methods `get_all(session)`, `get_by_base_name(session, base_name)`, `upsert(session, base_name, status, admin_id, admin_name)`, mirroring `UserPreferencesRepository`'s get-then-update-or-create pattern (session.exec + select, since `LLMModelStatus` has a surrogate UUID primary key rather than `base_name` as PK, so lookups go through `select(...).where(...)` rather than `session.get`). Added a module-level singleton `llm_model_status_repository = LLMModelStatusRepository()` for consistency, methods remain `@staticmethod` so both `LLMModelStatusRepository.upsert(...)` (class-level) and instance calls work.
- `tests/codemie/repository/test_llm_model_status_repository.py`: real in-memory SQLite session fixture (`LLMModelStatus.__table__.create(bind=engine)`, `StaticPool`), covering:
  - upsert then get_by_base_name returns row with status + admin fields
  - second upsert for same base_name updates in place (asserted via get_all() length == 1 and updated status/admin fields)
  - get_by_base_name for unknown name returns None
  - get_all returns all rows (two distinct base_names)

TDD was followed: wrote the test file first, confirmed RED (ModuleNotFoundError for `codemie.repository.llm_model_status_repository`), then implemented model + repository, confirmed GREEN (4 passed).

## Commit

`586bf3768` — "EPMCDME-15080: Add llm_model_status table and repository"

`git show --stat HEAD` confirms only the three intended files changed:
```
 src/codemie/repository/llm_model_status_repository.py      |  68 +++++++++++++
 src/codemie/rest_api/models/llm_model_status.py             |  40 ++++++++
 tests/codemie/repository/test_llm_model_status_repository.py| 109 +++++++++++++++++++++
 3 files changed, 217 insertions(+)
```

## Test command and result

```
poetry run pytest tests/codemie/repository/test_llm_model_status_repository.py -o addopts="--import-mode=importlib"
```
EXIT=0, 4 passed in 1.62s.

RED confirmation run (before implementation) also EXIT=0 for the shell command itself, but pytest reported `1 error during collection` (`ModuleNotFoundError: No module named 'codemie.repository.llm_model_status_repository'`) — the expected RED failure.

## Self-review notes

- No SQLite incompatibility was hit with `TIMESTAMP(timezone=True)` / `func.now()` — SQLAlchemy handled it transparently under SQLite, so no deviation from the plan's exact column spec was needed.
- Lookups in the repository use `session.exec(select(LLMModelStatus).where(...))` rather than `session.get(LLMModelStatus, base_name)` because the primary key is the generated `id` UUID, not `base_name` (per the plan's model spec, `base_name` is a unique+indexed non-PK column). This is a necessary deviation from `UserPreferencesRepository`'s literal `session.get(Model, pk)` call, but the method signatures (`get_by_base_name(session, base_name)`, `upsert(session, base_name, status, admin_id, admin_name)`) match the plan exactly, and `get_all()`/`get_by_base_name()` return `LLMModelStatus` objects with `.base_name` and `.status` attributes as Task 4 will expect.
- Did not touch Task 1's files, `src/external/alembic/`, or any of the pre-existing dirty files (`config/customer/customer-config.yaml`, `.codemie-repos/`, `.ai-run/sdlc-factory/doctor.json`) — verified via `git status` before staging and confirmed via `git show --stat HEAD` after commit.
- Did not merge, push, or open a PR/MR.
