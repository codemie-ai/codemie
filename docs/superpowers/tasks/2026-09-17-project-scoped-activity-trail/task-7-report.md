DONE

## Commit

`077963f77` — `EPMCDME-14325: Emit project_name/actor snapshot on user management and auth events (task 7)`

## Changed files (confirmed via `git show --stat HEAD`)

- `src/codemie/rest_api/routers/local_auth_router.py`
- `src/codemie/service/user/authentication_service.py`
- `src/codemie/service/user/registration_service.py`
- `src/codemie/service/user/user_management_service.py`
- `tests/codemie/rest_api/routers/test_local_auth_router.py`
- `tests/codemie/service/user/test_authentication_service.py`
- `tests/codemie/service/user/test_registration_service.py`
- `tests/codemie/service/user/test_user_management_service.py`

Exactly these 8 files — no other file touched (task 3-6 files and `activity_models.py`/migration untouched).

## Per-site changes

### `src/codemie/service/user/user_management_service.py` (3 sites)
- `create_user` (line ~132, `USER_CREATED`): added `project_name=None, actor_email=None, actor_display_name=None` — no actor object in scope.
- `update_user` (line ~243, `USER_UPDATED`): same 3 kwargs added — `actor_user_id` is a bare string, no `.email`/`.name` object.
- `deactivate_user` (line ~293, `USER_DEACTIVATED`): same 3 kwargs added.

### `src/codemie/service/user/registration_service.py` (1 site)
- `register_user` (line ~104, `USER_CREATED`, self-registration, `actor_id=None`): added `project_name=None, actor_email=None, actor_display_name=None`.

### `src/codemie/service/user/authentication_service.py` (4 sites)
- `create_user_from_idp` (line ~216, `USER_CREATED`, `actor_id=None`): added the 3 kwargs, all `None`.
- `authenticate_dev_header` new-user creation (line ~671, `USER_CREATED`, `actor_id=None`): added the 3 kwargs, all `None`.
- `authenticate_dev_header` login (line ~686, `USER_LOGIN`, `actor_id=db_user.id`): added `project_name=None, actor_email=db_user.email, actor_display_name=db_user.name`.
- `authenticate_and_login` local login (line ~761, `USER_LOGIN`, `actor_id=user.id`): added `project_name=None, actor_email=user.email, actor_display_name=user.name`.

### `src/codemie/rest_api/routers/local_auth_router.py` (1 site)
- `/logout` endpoint (line ~243, `USER_LOGOUT`): added `project_name=None, actor_email=_user.email, actor_display_name=_user.name`.

## TDD process

For the 7 sites where `actor_id=None`/no actor object is in scope, the new fields default to `None` on `ActivityEventCreate` already, so the assertions `project_name is None` / `actor_email is None` / `actor_display_name is None` pass trivially even before the production change (verified by running the extended tests against the pre-change code — they passed without any code modification, confirming the DTO defaults). The production change for these 7 sites is made for explicitness/consistency per the plan rather than to fix an observable test failure.

For the 3 meaningful sites with a real actor object in scope — dev-header login (`authenticate_dev_header`), local login (`authenticate_and_login`), and `/logout` — I explicitly reproduced RED by temporarily reverting just the `actor_email`/`actor_display_name` kwargs and re-running the corresponding tests:
- `test_authenticate_dev_header_new_user` failed: `actor_email` was `None` instead of `db_user.email`.
- `test_authenticate_and_login_emits_login_event` failed: `actor_email` was `None` instead of `mock_user.email`.
- `test_logout_clears_cookie` failed: `actor_email` was `None` instead of `mock_user.email`.

All three failed for the expected reason. I then restored the production kwargs and re-ran to confirm GREEN.

Tests added/extended (one assertion group per site, per instructions):
- `test_user_management_service.py`: extended `test_create_local_user_emits_user_created_event`, `test_update_user_emits_user_updated_event`, `test_deactivate_user_emits_user_deactivated_event`.
- `test_registration_service.py`: added `test_register_user_emits_user_created_event_with_no_project_or_actor`.
- `test_authentication_service.py`: added `test_create_user_from_idp_emits_event_with_no_project_or_actor`; extended `test_authenticate_dev_header_new_user` (covers both the dev_header `USER_CREATED` and `USER_LOGIN` sites in one test); extended `test_authenticate_and_login_emits_login_event`.
- `test_local_auth_router.py`: extended `test_logout_clears_cookie`.

## Verification

Command:
```
poetry run pytest tests/codemie/service/user/ tests/codemie/rest_api/routers/test_local_auth_router.py -q
```
Result: `EXIT=1` — but the single failure, `TestLoadUserForAuth::test_load_user_for_auth_found` (`assert result.is_admin is False` failing with `True`), is a pre-existing, unrelated failure. I confirmed this by `git stash`-ing all my changes and re-running that single test on the unmodified branch state: it fails identically (same `is_admin` mismatch), proving it predates this task and is unrelated to the `project_name`/actor snapshot work. All other 305 tests pass, including every new/extended assertion for the 10 call sites.

Lint/format:
```
poetry run ruff check <8 files>          -> All checks passed!
poetry run ruff format --check <8 files> -> 8 files already formatted
```

## Concerns

- The pre-existing failure in `TestLoadUserForAuth::test_load_user_for_auth_found` is out of scope for this task (unrelated to `authenticate_dev_header`/`authenticate_and_login`/`create_user_from_idp`/logout code paths touched here) but is present on the branch and should be flagged to whoever owns overall branch health before merge.
- For the 7 "no actor / no project context" sites, TDD RED could not be meaningfully demonstrated per-site because `ActivityEventCreate`'s new fields already default to `None` (added in an earlier task). This was documented above rather than silently glossed over; the added assertions still serve as regression guards against a future default change.
