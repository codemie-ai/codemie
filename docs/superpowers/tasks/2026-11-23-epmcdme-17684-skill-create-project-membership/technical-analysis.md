# Technical Research

**Task**: skills project membership create
**Generated**: 2026-11-23
**Research path**: codegraph

---

## 1. Original Context

task_context (verbatim ticket EPMCDME-17684, https://jiraeu.epam.com/browse/EPMCDME-17684, fetched earlier from Jira):

Summary: [G0.17] L16: a user makes a skill in a project with no membership

Problem: Skill creation (POST /v1/skills) does not check that the caller is a member of the target project. A user can create a skill in a project of which they are not a member, so they can put content into that project. Project members can then read the planted skill, and the Project Admin gets write and delete abilities on it.

Required change: Skill creation checks the membership of the caller in the target project. A caller who is not a member is rejected.

Acceptance Criteria:
1. Given a project of which the caller is not a member, when the caller creates a skill in that project, then the request fails and no skill is created.
2. Given a project of which the caller is a member, when the caller creates a skill, then the skill is created as today.
3. A regression test covers these cases, and the test passes in CI.

Implementation note: Analysis of the defect reports that the skill import endpoint (POST /v1/skills/import) has the same gap. During implementation, check whether the import path must receive the same membership check, and say so in the merge request.

Context already established by a live local reproduction (do not redo it): SkillService.create_skill in src/codemie/service/skill_service.py only checks duplicate name and never consults user.applications / applications_admin / is_admin. SkillService.import_skill ends by calling create_skill, so a check there covers import too. SkillService._validate_project_change (same file) already implements the membership rule for moving a skill to another project on update (admins and project admins of the target pass; otherwise project must be in user.applications; 403 SkillErrors.PERMISSION_DENIED). Non-member POST /v1/skills and /v1/skills/import both returned 201 locally. Please research: other places that gate project membership for creating assistants/datasources/workflows etc. (is there a shared helper to reuse?), how personal projects (user's email/username as project) are treated, how skills are created from other paths that call create_skill or SkillRepository.create (e.g. generate, marketplace, internal callers), existing tests for create_skill/import_skill and the router, and the error codes/messages conventions (SkillErrors).

---

## 2. Codebase Findings

### Existing Implementations

Purpose A: "Only a member of a project may place content in it" (gate on create).
- Skill create: none. `SkillService.create_skill` (`src/codemie/service/skill_service.py:529`) checks duplicate name per author+project (409), `_validate_companion_payloads`, sanitizes MCP servers, then `SkillRepository.create` (:575). No project/membership reference. `request.project` is a free `str` (`SkillCreateRequest.project`, `src/codemie/rest_api/models/skill.py:236`); no Application-exists check either.
- Skill move (update): `SkillService._validate_project_change` (`skill_service.py:604-627`). Rule: no-op if project unchanged/None; pass if `user.is_admin` or project in `user.applications_admin` (via `hasattr` guard) or project in `user.applications`; else `ExtendedHTTPException` 403, message `SkillErrors.PERMISSION_DENIED` ("Permission denied"), details `"You don't have access to project '<p>'"`, help "Only admins or project members can move skills to a project". Used only by update.
- Shared helper `project_access_check(user, project_name)` (`src/codemie/rest_api/security/authentication.py:398`): raises 403 `ACCESS_DENIED_MESSAGE`, details "You do not have permission to access the project '<p>'", logs `access_denied_project`. Backed by `User.has_access_to_application(app)` (`src/codemie/rest_api/security/user.py:232`) = `is_admin_or_maintainer` OR in `project_names` OR in `admin_project_names`. Used at the router layer by: assistant create `rest_api/routers/assistant/management.py:313` (and :508), workflow create `routers/workflow.py:707` (and :764, :847), guardrail `routers/guardrail.py:165`, user settings `routers/user_settings.py:188,231`. Skill router does not import it.
- Datasource project move: `FileDatasourceService.validate_project_change` (`service/datasource/file_datasource_service.py:141`) and `routers/index/_common.py:165` use `has_access_to_application` (404 if Application missing, 403 `_ACCESS_DENIED_MESSAGE`, 409 on duplicate index).
- Hosted apps: `require_project_access` (`service/hosted_applications/catalog.py:85`) uses `has_access_to_application`, also rejects `EXTERNAL_USER_TYPE`; own error type `HostedApplicationError`.
- Differences in rules: `_validate_project_change` and `has_access_to_application` grant the same set (admin, project admin, member; maintainers get `is_admin=True` in `User.resolve_is_admin`, `user.py:175`). Assistant/workflow also call `ensure_application_exists(project)` after the check (`management.py:340`, `workflow_service.py:221`); skill create does not.

Purpose B: "What a project member or admin can do with a skill in the project" (the impact).
- `Skill.is_shared_with` (`models/skill.py:643`): PROJECT visibility = project in `user.project_names`; `Skill.is_managed_by` (:639): project in `user.admin_project_names` (this is the Project Admin write/delete path). Skill ability checks go through `Ability(user)` (`skill_service.py:136-152`).

Purpose C: all entry points that create a skill row.
- `POST /v1/skills` -> router `create_skill` (`rest_api/routers/skill.py:433`) -> `SkillService.create_skill`. Router has no project logic.
- `POST /v1/skills/import` -> router `import_skill` (`skill.py:494`) -> `SkillService.import_skill` (`skill_service.py:847`) -> parses frontmatter, validates name/content, then calls `SkillService.create_skill` (:901) with `project` from `SkillImportRequest`. No project check before `create_skill`; decode/parse/validation 400s happen first.
- `POST /v1/skills/import-bundle-preview` (`skill.py:531`): preview only, no persistence.
- `POST /v1/skills/generate` (`skill.py:772`) / `refine` / `instructions/generate`: LLM generation, return details, do not persist (SkillGeneratorService, `service/skill_generator_service.py`).
- Marketplace publish/unpublish: change visibility of an existing skill, not creation.
- Deployment seed: `src/external/deployment_scripts/preconfigured_skills.py:266` calls `SkillRepository.create` directly with `SYSTEM_USER`; bypasses the service (no User, no membership).
- No other callers of `create_skill` / `SkillRepository.create` in `src/` (grep). 16 "callers" reported by codegraph are tests.

Purpose D: personal project handling.
- Personal project is an `Application` of `project_type=personal` named after the user's email; membership is a `user_projects` row (`PersonalProjectService`, `service/project/personal_project_service.py:164-201`; ensured at login). So in user-management mode it appears in `user.project_names` and passes a `project in user.applications` check. Name match is case-insensitive elsewhere (`same_project_name`, `user.py:31`) because the IdP email may differ in casing; membership lists carry the application's stored name.
- IDP-claims mode (`ENABLE_USER_MANAGEMENT=False`): no stored default; `derive_idp_default_project` (`user.py:78`) picks email-named project, else username workspace (`idp_workspace_name`) from `project_names`. Both come from the claims list, so exact `in project_names` works if the project is in the claims.
- `User.project_names` defaults to `['demo']` (`user.py:144`).
- Nothing in the skill code special-cases personal projects.

Other touched code
- `SkillErrors` (`skill_service.py:86-120`): constants `PERMISSION_DENIED = "Permission denied"`, `PERMISSION_DENIED_SKILL`, `PERMISSION_DENIED_ASSISTANT`; no constant for project access. The project-change 403 builds its details inline (`:624`), not via a constant.

### Decisions Already in the Code
- Skill-level authorization lives in the service layer (`SkillService._raise_if_no_access`, `_validate_project_change`), router is thin. Assistants/workflows put `project_access_check` in the router. Both conventions exist.
- Skill errors use `ExtendedHTTPException(code, message, details, help)` with `SkillErrors` constants for messages (`skill_service.py:147-152, 159-164, 621-626`).
- `ENV=local` makes every `User` admin (`user.py:168`); unit tests in `test_skill_service.py` neutralize it with an autouse fixture setting `config.ENV="dev"` (:62).
- Duplicate-name uniqueness is per (name, author id, project) (`SkillRepository.get_by_name_author_project`, unique index `uix_skill_name_author_project`, `models/skill.py:624`).
- Companion-file validation runs before persistence "so a rejected request leaves no skill behind" (`skill_service.py:551`); the same ordering rationale applies to AC 1.
- Rollback precedent: `_rollback_created_skill` (:516) on companion upload failure.

### Task Statements Checked Against the Code
- "create_skill only checks duplicate name and never consults applications/applications_admin/is_admin": holds (`skill_service.py:529-601`).
- "import_skill ends by calling create_skill, so a check there covers import too": holds (`skill_service.py:901`); `import_skill` has no other persistence call. Import's earlier 400 validations (encoding, frontmatter, name, content length) run before `create_skill`, so a non-member sending an invalid file gets 400, not 403.
- "_validate_project_change implements the membership rule ... 403 SkillErrors.PERMISSION_DENIED": holds (`skill_service.py:604-627`); it takes a `SkillUpdateRequest` and `Skill`, so it is not callable from create as written.
- "Project members can read the planted skill, Project Admin gets write/delete": holds via `is_shared_with` (PROJECT visibility, `models/skill.py:654`) and `is_managed_by` (:641).
- "Non-member POST returned 201 locally": consistent, but note `ENV=local` forces `is_admin=True` (`user.py:168`), so a local repro with an admin-bypass would still pass for any caller; unverified how the repro user was constructed.
- "Is there a shared helper": `project_access_check` / `User.has_access_to_application` exist (see Purpose A); the skill code does not use them.

### Consumers of Changed Contracts
- `SkillService.create_skill`: routers `skill.py:443`, `SkillService.import_skill` (:901); tests `tests/codemie/service/test_skill_service.py` (TestCreateSkill :519, TestImportSkill :900, TestSkillMcpGovernance :759, TestSkillToolkits :1831, TestCreateSkillWithCompanionFiles :2389, TestCreateSkillNoOrphan :2499, TestBundleWarningInResponses :2746) and `tests/codemie/rest_api/routers/test_skill_router.py:460` (mocks the service). All create requests in these tests use `project="project-a"`; `owner_user` has `project_names=["project-a","project-b"]`, so they would still be members. `TestImportSkill` passes "project-a" with `owner_user`.
- `SkillRepository.create` direct caller: `preconfigured_skills.py:266` (not through the service).
- Frontend-facing contract: 201 `SkillDetailResponse`; a new rejection would be a 403 `ExtendedHTTPException` (same shape as update's move rejection).

### Architecture and Layers Affected
- REST router (`rest_api/routers/skill.py`) -> service (`service/skill_service.py`) -> repository (`repository/skill_repository.py`) -> `Skill` SQLModel; auth principal `User` (`rest_api/security/user.py`).

### Integration Points
- `User.project_names` / `admin_project_names` populated from DB `user_projects` (user-management mode) or IdP claims; `SkillMonitoringService.send_skill_management_metric` is called on success only in `create_skill` (:590).

### Patterns and Conventions
- Service static methods, `ExtendedHTTPException`, logger warnings on access denials in `project_access_check`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/` exists; grep of the guides found no skill-specific or project-membership create guidance. Relevant: `development/security-patterns.md`, `development/error-handling.md`, `api/endpoint-conventions.md`, `testing/testing-service-patterns.md`.

### Architectural Decisions
- None found for skill project gating.

### Derived Conventions
- Authorization 403s carry message + details + help; skills keep them in `SkillService`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/test_skill_service.py` (create, duplicate, import happy path and 400 cases, companion rollback/no-orphan, access control).
- `tests/codemie/rest_api/routers/test_skill_router.py`: calls router functions directly with `SkillService.*` patched (`test_create_skill_success` :460); no import-endpoint test (codegraph: no tests within 3 hops of router `import_skill`).
- `tests/codemie/repository/test_skill_repository.py`.
- Existing non-member/update-move coverage: not located in a dedicated test class; `_validate_project_change` tests not confirmed by search.

### Testing Framework and Patterns
- pytest, `MagicMock(spec=SkillRepository)` with `patch.object(SkillRepository, "create"/"get_by_name_author_project", ...)`; users built as `User(id=..., project_names=[...], admin_project_names=[...])`; autouse `_non_local_env` sets ENV=dev; fixtures `owner_user`, `other_user` (project_names b,c), `project_manager_user` (admin of project-a), `sample_skill`. Mock-based, no real Postgres.

### Coverage Gaps
- No test for create/import by a non-member; no test for create by project admin or global admin; no router-level import test.

---

## 5. Configuration and Environment

### Environment Variables
- `ENV` (`config.ENV`; `local` forces `is_admin=True`), `ENABLE_USER_MANAGEMENT` (source of `project_names`; admin resolution), `ADMIN_USER_ID`, `ADMIN_ROLE_NAME` (legacy admin).

### Configuration Files
- None skill-specific.

### Feature Flags and Deployment Concerns
- No flag found for skill creation gating. Existing skills created into non-member projects are already persisted; nothing found that cleans them.

---

## 6. Risk Indicators

- Existing mechanism for the same purpose kept in place: `_validate_project_change` (`skill_service.py:604`, update path only) duplicates the rule that `User.has_access_to_application` / `project_access_check` already express; three copies of the rule would exist (see Existing Implementations, Purpose A).
- Local reproduction risk: `ENV=local` makes every user admin (`user.py:168`), so a rejection cannot be observed locally without a non-local ENV; tests need the existing `_non_local_env` pattern.
- Import ordering: import validations run before `create_skill`; a membership 403 for a non-member only appears after those pass.
- Create bypass via direct repository callers: `preconfigured_skills.py:266` skips the service (system seeding; intentional by design, not verified).
- Personal-project naming: email case differences (`same_project_name`) vs exact `in project_names`; skills in a personal project with a case-different name are untested.
- `project` is a free string: no Application-existence check on create; a nonexistent project name from an admin passes any membership rule that grants admins.
- Speculative: the error shape (router `project_access_check` 403 vs service-level `SkillErrors.PERMISSION_DENIED` 403) is a convention choice both of which exist; frontend handling of the message text is unverified.
- Speculative: existing tests keep passing only if the check uses `user.project_names`; tests constructing users without `project-a` were not found in `test_skill_service.py` create paths, but other suites that call `create_skill` indirectly were not exhaustively checked.
- Existing skills already planted in non-member projects are not addressed by the ticket.

---

## 7. Summary for Complexity Assessment

The change surface is the skill service (create path, shared by import) with the router unchanged in structure. Layers: REST router -> `SkillService.create_skill` -> `SkillRepository.create`. The membership rule already exists in three forms (`SkillService._validate_project_change`, `User.has_access_to_application`, `project_access_check`), used by assistant, workflow, guardrail, datasource and hosted-app flows; skills use the first one only on update. Import is a thin wrapper over create, so no separate persistence path exists; the only bypass is the deployment seed script that writes through the repository with a system user.

Technical novelty is low: the pattern is established and the error shape (`ExtendedHTTPException` 403 with `SkillErrors` messages) is in the same file. Test posture is mock-based and strong on create/import happy paths and companion rollback, with an autouse non-local ENV fixture and ready-made member / non-member / project-admin users; there is no existing test of non-member create or of import at the router layer.

Key risk factors: three copies of the same rule, ENV=local making all users admin, personal-project name casing, and import's pre-validation order. No migrations, config or new dependencies were found to be involved.

---

## 8. External References

None named by the task (the Jira URL is the ticket source; its content was supplied in task_context).
