# EPMCDME-17684 Skill create requires project membership - Implementation Plan

**Goal:** `SkillService.create_skill` (and so `import_skill`) rejects a caller who is not a member of the target project, using the existing `User.has_access_to_application` rule rather than a third copy.

**Conventions:** Commit per task using the repository's existing convention (do not commit in this flow stage). No explanatory code comments. Run ruff/format per `.ai-run/guides/quality-gates.md`. Tests are mock-based in the style of `tests/codemie/service/test_skill_service.py`; the autouse `_non_local_env` fixture is already in place (ENV=local forces `is_admin=True`).

**Rule equivalence (checked):** `User.applications` and `applications_admin` are computed fields returning `project_names` and `admin_project_names` (`user.py:180-188`), and `has_access_to_application` (`user.py:232`) is `is_admin_or_maintainer or in project_names or is_application_admin`. That is the same set as the old inline rule (admin, project admin of target, member), so update behaviour does not change. No existing update test builds a Mock user: `test_skill_service.py` uses real `User(...)` fixtures, and the router tests patch `SkillService`. The only `.applications` uses in the test file (:170, :173) are on real users inside a test helper. No fixture needs adjusting.

## Acceptance criteria

- A non-member creating a skill in a project gets 403 `SkillErrors.PERMISSION_DENIED` and `SkillRepository.create` is not called.
- A member creating a skill in the project still succeeds as today.
- A non-member `import_skill` with a valid file gets 403; a member import still succeeds; import's own 400 validations still run first.
- A project admin of the target and a global admin can create.
- Update behaviour (`_validate_project_change`) is unchanged, including its 403 message, details and help text.
- Regression tests cover the above and pass.

## Task 1: Membership gate in `create_skill`

**Files:** modify `src/codemie/service/skill_service.py:529-627`; modify `tests/codemie/service/test_skill_service.py` (`TestCreateSkill`, :519).

Test-first: yes - non-member `create_skill` (`other_user`, project `project-a`) must raise `ExtendedHTTPException` 403 with message `SkillErrors.PERMISSION_DENIED`; today it succeeds, so the test fails.

- [ ] Add to `TestCreateSkill`, reusing `owner_user`, `other_user` and the `mock_repository` patching used by `test_create_skill_success`:
  - non-member -> 403, `create` and `get_by_name_author_project` not called;
  - member (`owner_user`) -> existing `test_create_skill_success` covers it; leave it untouched;
  - project admin of the target (`User(..., project_names=[], admin_project_names=["project-a"])`) -> creates;
  - global admin -> creates; build it exactly as `test_admin_has_full_access` (:248-251) does: `User(id=..., username=..., project_names=["project-x"], admin_project_names=[], is_admin=True)` created inside `with patch("codemie.rest_api.security.user.config.ENABLE_USER_MANAGEMENT", True):`, because with it off `resolve_is_admin` overwrites a bare `is_admin=True` and the admin would get a 403.
- [ ] Run the new tests, confirm the non-member test fails.
- [ ] In `skill_service.py`, add a static helper taking `(project: str, user: User, help_text: str)` whose body is `if not user.has_access_to_application(project): raise ExtendedHTTPException(403, SkillErrors.PERMISSION_DENIED, details=f"You don't have access to project '{project}'", help=help_text)`. No `hasattr` guard.
- [ ] Replace the inline rule at `:617-626` in `_validate_project_change` with a call to the helper, passing its current help text `"Only admins or project members can move skills to a project"`; the early return for unchanged/None project at :614 stays as is.
- [ ] Call the helper as the first statement of `create_skill` (before the duplicate-name lookup at :538) with help text `"Only admins or project members can create skills in a project"`.
- [ ] Run `tests/codemie/service/test_skill_service.py` fully (existing create, import, update and companion tests use `owner_user` in `project-a` and must stay green), then ruff/format.

## Task 2: Import regression tests

**Files:** modify `tests/codemie/service/test_skill_service.py` (`TestImportSkill`, :900).

Test-first: no - import delegates to `create_skill` (`skill_service.py:901`), so these pass once Task 1 lands; they pin the MR's answer that import needs no separate check.

- [ ] Add: non-member `import_skill` with a valid markdown file (as in `test_import_skill_valid_markdown`) -> 403 and `create` not called; member import (existing `test_import_skill_valid_markdown`) unchanged; non-member with an invalid file still gets 400 (validations run before the gate).
- [ ] Run `TestImportSkill`, then ruff/format. Record in the MR: import path needed the same check and is covered by the `create_skill` gate.
