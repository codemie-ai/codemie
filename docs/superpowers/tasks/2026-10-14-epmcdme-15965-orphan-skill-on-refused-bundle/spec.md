# EPMCDME-15965 (backend): No orphan skill when companion files are refused

Repo: `codemie`. Follow-up bug from QA on EPMCDME-15558. The frontend half (size limit mismatch, preview warning) is specified separately in `codemie-ui`.

## Problem
`SkillService.create_skill` (`src/codemie/service/skill_service.py`, ~line 496) works in two steps:
1. `SkillRepository.create(skill_data)` with `companion_files: []`, which persists the skill.
2. `_upload_companion_files(...)`, which validates and uploads the files.

Step 2 can raise 400 (`ExtendedHTTPException`):
- the path is invalid or too long (more than `MAX_SKILL_BUNDLE_PATH_LENGTH`, 943 chars; `SkillFileStorage._normalize_companion_file_path`)
- the path is the reserved `SKILL.md`
- the path is a duplicate
- the payload cannot be decoded

When it does, the already-created skill row stays behind as an empty skill. QA reproduced this live with a path over 943 chars: the API returns 400 and the dangling skill appears in the list.

Any later failure (blob storage write error, `SkillRepository.update` error) leaves the same orphan.

`update_skill` is not affected, since the skill already exists there.

## Requirements
1. A request whose companion files fail validation returns the same 400 as today and creates no skill record.
2. If anything fails after the skill record is created (upload or the follow-up update), the new record is deleted and the original exception is re-raised. A cleanup failure is logged and must not mask the original error.
3. Uploaded blobs are not deleted on failure. They are content-addressed and deliberately left in place (see `delete_skill` comment), so there is no blob cleanup.
4. The success path and the 409 duplicate-name path are unchanged. The monitoring metric is sent only on success.
5. Error codes and messages for invalid paths stay identical to today.

## Design
- Extract the validation part of `_upload_companion_files` (path normalization, reserved-name check, duplicate check, payload decode) into a static helper `_validate_companion_payloads(payloads)`. `_upload_companion_files` calls it so `update_skill` keeps identical behavior.
- `create_skill` calls the helper before `SkillRepository.create`.
- Wrap the post-create upload and update in `try/except Exception`. On failure call `SkillRepository.delete(skill.id)` guarded by its own try/except with logging, then `raise`.

Validation first is the primary fix and cleanup is the safety net, because it avoids a window where a half-created skill is visible and covers failures that cannot be pre-checked.

## Non-goals
- No change to seed-policy limits or to `skill_bundle_seed_policy.py` semantics (it governs seeding, not creation).
- No blob garbage collection.
- No frontend changes.

## Acceptance criteria
- POST create with a companion path of 944 chars returns 400 and `SkillRepository.create` is not called.
- POST create with a duplicate path or reserved `SKILL.md` path behaves the same way (400, no record).
- If `_upload_companion_files` raises a non-validation error after create, `SkillRepository.delete` is called with the new skill id and the original error propagates.
- Valid create with companion files still passes the existing two-phase tests unchanged.
