# Technical Research

**Task**: skill service companion files bundle
**Generated**: 2026-10-14
**Research path**: filesystem (codegraph MCP tool not available; research done directly with Bash/grep/Read)

---

## 1. Original Context

EPMCDME-15965 (backend). SkillService.create_skill (src/codemie/service/skill_service.py ~line 496) first persists the skill via SkillRepository.create (companion_files: []), then calls _upload_companion_files, which can raise 400 (invalid/too long path >943 chars via SkillFileStorage._normalize_companion_file_path, reserved SKILL.md, duplicate path, undecodable payload). The skill row is left orphaned. Any later failure (blob write, SkillRepository.update) also leaves an orphan. Fix: extract validation from _upload_companion_files into static helper _validate_companion_payloads(payloads), call it in create_skill before SkillRepository.create; wrap post-create upload+update in try/except Exception, delete the new record via SkillRepository.delete(skill.id) guarded by its own try/except with logging, then re-raise. Blobs are not cleaned up (content-addressed). Success path, 409 duplicate-name path, error codes/messages, and metric-on-success-only unchanged. update_skill must keep identical behavior. Full spec: docs/superpowers/tasks/2026-10-14-epmcdme-15965-orphan-skill-on-refused-bundle/spec.md

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/service/skill_service.py`
  - `_upload_companion_files(skill_id, payloads, file_repo, existing_records=None)` (static, ~L166-225): single loop per payload does (1) `SkillFileStorage._normalize_companion_file_path` (raises 400 for invalid/too long), (2) reserved `SKILL.md` check (case-insensitive, 400 "Invalid companion file path"), (3) duplicate-path check via `seen_paths` (400 "Duplicate companion file path"), (4) `SkillFileStorage._decode_companion_file_payload` (undecodable -> 400), (5) `content_addressed_key`, reuse of existing record if key+checksum match, else `storage.write_file(...)` and record build. Validation and writing are interleaved per file, so the extracted helper must replicate steps 1-4 (including the decode) for the whole list up front.
  - `create_skill` (~L496-565): 409 duplicate-name check -> build `skill_data` (`companion_files: []`) -> `MCPAccessControlService.sanitize_for_save` -> `SkillRepository.create` (L539) -> `if request.companion_files:` get `FileRepositoryFactory.get_current_repository()`, `_upload_companion_files`, `SkillRepository.update(skill.id, {"companion_files": ...})` (L544-545) -> `SkillMonitoringService.send_skill_management_metric(... success=True)` -> `to_detail_response`. The pre-persist validation call belongs after the 409 check (or before; spec decides) and before L539.
  - `update_skill` (~L634-690): calls `_upload_companion_files(..., existing_records=...)` at ~L677 then `SkillRepository.update` at ~L684. Must stay behavior-identical; if `_upload_companion_files` internally calls the new helper, error order/messages must not change.
  - Other `_normalize_companion_file_path` users: L235 (bundle preview), L427 (get_companion_file) - unaffected.
- `src/codemie/repository/skill_repository.py`: `SkillRepository.delete(skill_id) -> bool` (L414): opens Session, returns False if missing, bulk-deletes `SkillUserInteraction` rows, deletes skill, commits. Safe to call for rollback of a fresh row (no interactions yet).
- `SkillFileStorage` (`SkillFileStorage._normalize_companion_file_path`, `_decode_companion_file_payload`, `write_file`) and `content_addressed_key` provide path/decoding/key logic.

### Architecture and Layers Affected
- Service layer only: `SkillService` (static-method style). Repository (`SkillRepository`) is consumed, not changed. Storage helper (`SkillFileStorage`) consumed. No API/router changes (router passes through `ExtendedHTTPException`).

### Integration Points
- `SkillRepository` (create/update/delete, PostgreSQL via SQLModel), `FileRepositoryFactory`/`FileRepository` (blob store), `SkillMonitoringService.send_skill_management_metric` (must fire on success only), `MCPAccessControlService.sanitize_for_save`, `ExtendedHTTPException` (400 errors).

### Patterns and Conventions
- Static methods on `SkillService`; typed `ExtendedHTTPException(code, message, details, help)` for client errors; module `logger` with f-string messages (see `logger.info(f"Created skill ...")`). Logging guide: `.ai-run/guides/development/logging-patterns.md`; error handling guide: `.ai-run/guides/development/error-handling.md`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/architecture/service-layer-patterns.md`, `.ai-run/guides/data/repository-patterns.md`, `.ai-run/guides/development/error-handling.md`, `.ai-run/guides/development/logging-patterns.md`, `.ai-run/guides/testing/testing-service-patterns.md`. Task spec: `docs/superpowers/tasks/2026-10-14-epmcdme-15965-orphan-skill-on-refused-bundle/spec.md` (not analyzed here).

### Architectural Decisions
- Blobs are content-addressed (`content_addressed_key`), never overwritten, may be referenced by agent workspaces -> deliberately not cleaned up on rollback (per spec and docstring).

### Derived Conventions
- Create-then-enrich in two steps because the skill ID is needed for the storage key; compensation (delete) is the chosen strategy instead of a DB transaction spanning blob store.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/test_skill_service.py`: `create_skill` tests (L513-609, L765-800, L1832-1873) patch `SkillRepository.create/get_by_name_author_project/count_assistants_using_skill`; a `_upload_companion_files` test class (~L2041-2200) covers reserved path, duplicates, content-addressed reuse, existing_records, tampered checksum, legacy records. Router tests in `tests/codemie/rest_api/routers/test_skill_router.py`.
- Other consumers: `test_skill_workspace_preparation.py`, `test_external_entities_collector.py`.

### Testing Framework and Patterns
- pytest with `unittest.mock` (`patch.object(SkillRepository, ...)`, `MagicMock` file repo via `_make_file_repo()`), fixtures `owner_user`, `sample_skill`.

### Coverage Gaps
- No test that `create_skill` with invalid companion files avoids `SkillRepository.create`; none for rollback on upload/update failure (`SkillRepository.delete` called, delete-failure swallowed+logged, original exception re-raised); no direct tests for the new `_validate_companion_payloads` (path too long >943, undecodable). Existing `_upload_companion_files` tests must still pass unchanged (update path regression guard).

---

## 5. Configuration and Environment

### Environment Variables
- None relevant.

### Configuration Files
- None relevant; file repo chosen via `FileRepositoryFactory.get_current_repository()`.

### Feature Flags and Deployment Concerns
- None. No migration, no schema change.

---

## 6. Risk Indicators

- Validation/write interleaving in `_upload_companion_files`: extraction must preserve exact error codes/messages and first-error ordering for `update_skill`; decoding is part of validation, so payloads are decoded twice on create (validate + upload) - a minor cost, acceptable but note memory for large bundles.
- Compensation is not atomic: a crash between create and delete (or delete failing) still leaves an orphan; mitigated only by logging.
- Blob orphans remain by design after failed create (content-addressed, storage cost only).
- `except Exception` re-raise must not swallow `ExtendedHTTPException` or mask the original error if delete itself raises.
- Metric must not be emitted on failure; `SkillRepository.update` returning None/failing path should be covered.
- Existing `create_skill` tests mock `SkillRepository.create`; adding a pre-validation call with real `SkillFileStorage` code is harmless for tests with no companion files.
- Spec.md was not independently verified by this analysis.

---

## 7. Summary for Complexity Assessment

The change is confined to the service layer: a single file (`src/codemie/service/skill_service.py`) with two methods touched (`create_skill`, `_upload_companion_files`) plus one new static helper, and test additions in `tests/codemie/service/test_skill_service.py`. No API, DB schema, migration, config, or repository changes; `SkillRepository.delete` already exists and cascades interactions. Estimated change surface: 1 source file (~40-60 lines) and 1 test file.

Technical novelty is low: it follows established static-method service patterns and typed exceptions; the only subtlety is a compensating-delete (saga-like) pattern around a non-transactional blob + DB sequence, and refactoring validation out of an interleaved loop without altering `update_skill` behavior.

Test posture is mixed-good: `_upload_companion_files` is well covered (a regression safety net for the refactor) and `create_skill` has success/409 tests, but there is no coverage of failure/rollback paths, which the task must add. Overall risk: low-to-moderate, driven by preserving error semantics and robust exception handling in the cleanup branch.
