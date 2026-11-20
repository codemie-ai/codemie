DONE

## Commit

- SHA: `4c34567dc9fa31a4f395ab240d756abaf5205d76`
- Subject: `EPMCDME-14325: Add project-admin-or-maintainer activity trail auth dependency (task 11)`

## Changed files (`git show --stat HEAD`)

```
 src/codemie/rest_api/security/authentication.py    | 38 ++++++++++++
 .../rest_api/security/test_authentication.py       | 71 ++++++++++++++++++++++
 2 files changed, 109 insertions(+)
```

## What was done

- Discovered that `tests/codemie/rest_api/security/test_authentication.py` **already existed** (contrary to the task's assumption that no test file existed for this module). Rather than creating a duplicate/new file, I added the required tests to the existing file, following its established conventions exactly: `@pytest.mark.anyio` (not `@pytest.mark.asyncio` — this repo's convention for this file uses the `anyio` pytest plugin with a module-level `anyio_backend` fixture already defined), `@patch.object(config, 'ENV', 'dev')` / `@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)` decorators, and `MagicMock()` for `Request` with `request.state.user` / `request.path_params` set directly, matching the sibling tests for `project_admin_or_admin_user_detail_access`.
- Added `async def project_admin_or_admin_or_maintainer_access(request: Request) -> None` to `src/codemie/rest_api/security/authentication.py`, placed immediately before `application_access_check` (after `project_admin_or_admin_user_detail_access`, which ends at line 340 in the pre-edit file — matches the plan's placement intent "near" the precedent function). Implementation matches the plan's provided code exactly: function-local imports of `get_session` and `user_project_repository`, admin/maintainer fast path, `project_name` path-param lookup via `user_project_repository.is_admin(session, user.id, project_name)`, and 403 `ExtendedHTTPException` with `ACCESS_DENIED_MESSAGE` otherwise. No new top-level imports were needed.
- Added 3 tests to `tests/codemie/rest_api/security/test_authentication.py`:
  1. `test_project_admin_or_admin_or_maintainer_access_admin_passes_without_project_check` — maintainer user passes; asserts `user_project_repository.is_admin` is never called.
  2. `test_project_admin_or_admin_or_maintainer_access_project_admin_passes` — non-admin/non-maintainer user whose `project_name` path param resolves via `is_admin(...) == True` passes; asserts `is_admin` called with `(session, user_id, project_name)`.
  3. `test_project_admin_or_admin_or_maintainer_access_denied_for_non_project_admin` — same setup with `is_admin(...) == False`; asserts `ExtendedHTTPException` raised with `.code == 403` and `.message == ACCESS_DENIED_MESSAGE`.
  Also added the two new imports (`project_admin_or_admin_or_maintainer_access`, `ACCESS_DENIED_MESSAGE`) to the existing import block from `codemie.rest_api.security.authentication`.

## TDD verification

- RED: ran `pytest tests/codemie/rest_api/security/test_authentication.py -k project_admin_or_admin_or_maintainer -v` before implementing the function — failed with `ImportError: cannot import name 'project_admin_or_admin_or_maintainer_access'` (collection error), confirming the test module could not even import prior to implementation.
- GREEN: after implementing, ran the same test file in full: `poetry run pytest tests/codemie/rest_api/security/test_authentication.py -v` → `40 passed, 2 skipped` (the 2 skips are pre-existing `@pytest.mark.skip` markers unrelated to this change). Exit code 0. Full log saved to `docs/superpowers/tasks/2026-09-17-project-scoped-activity-trail/test.local.log`.
- Wider check: `poetry run pytest tests/codemie/rest_api/security/ -q` → `1 failed, 185 passed, 2 skipped`. The 1 failure, `tests/codemie/rest_api/security/idp/test_local_idp.py::test_authenticate_db_success_with_user_management_enabled` (`AssertionError: assert 'db_user' == 'db_username'`), is **pre-existing and unrelated**: confirmed by `git stash`-ing my changes and re-running that single test in isolation — it fails identically with the exact same assertion error on a clean tree.
- Lint: `poetry run ruff check src/codemie/rest_api/security/authentication.py tests/codemie/rest_api/security/test_authentication.py` → "All checks passed!". `poetry run ruff format --check` on the same two files → "2 files already formatted" (no changes needed).

## Concerns

- The task brief assumed no test file existed for this module and instructed creating a new one with `pytest.mark.asyncio`. The actual repo convention for this module is `pytest.mark.anyio` with an existing test file — I followed the actual codebase convention instead of the brief's assumption, per AGENTS.md's "trust current source" guidance. Flagging this discrepancy for visibility even though the outcome (passing tests, correct behavior) is unaffected.
- Per scope, only `src/codemie/rest_api/security/authentication.py` and `tests/codemie/rest_api/security/test_authentication.py` were touched. No router files, `user_project_repository.py`, or `user.py` were modified — those are later tasks in the plan.
