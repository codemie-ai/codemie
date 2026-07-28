# Git Datasource Optional Integration Design

**Ticket**: EPMCDME-13690  
**Branch**: `EPMCDME-13690_git-datasource-optional-integration`  
**Date**: 2026-07-24

---

## Summary

Allow users to create a Git datasource for public repositories without selecting an integration. Integration selection remains required for private repositories. The system validates public-URL accessibility at creation time using `git ls-remote` so users receive a clear error immediately if the repository requires credentials.

---

## Architecture

This feature touches three layers:

| Layer | Component | Change |
|---|---|---|
| Datasource Loader | `git_loader.py` | Add `test_public_access` and `test_connection` static methods |
| API / Router | `routers/index.py` → `_validate_git_credentials` | Replace early-return skip with public-accessibility probe |
| Service | `datasource_health_check_service.py` | Add `health_check_git` + `DatasourceTypes.GIT` case |
| API / Models | `models/index.py` → `DatasourceHealthCheckRequest` | Add `git_url` field |

No model migrations, no new dependencies, no deployment changes. `setting_id` is already `Optional[str]` at every layer.

---

## Components

### `GitBatchLoader.test_public_access(url, timeout=3)`

`@staticmethod` in `src/codemie/datasource/loader/git_loader.py`.

Runs `git ls-remote --exit-code --quiet <url> HEAD` via `git.cmd.Git().execute(..., kill_after_timeout=timeout)`. On any failure (`GitCommandError`, timeout, non-zero exit) raises `ConnectionException("Repository not publicly accessible")`. On success returns `None`.

Used by `_validate_git_credentials` when `setting_id is None` and by `health_check_git` for the no-integration path.

### `GitBatchLoader.test_connection(url, creds, timeout=3)`

`@staticmethod` in `src/codemie/datasource/loader/git_loader.py`.

Builds an authenticated URL with `_build_clone_url(creds, SimpleNamespace(link=url))`, then runs `git ls-remote --exit-code --quiet <auth_url> HEAD` via `git.cmd.Git().execute(..., kill_after_timeout=timeout)`. Raises `ConnectionException` on failure.

Used by `health_check_git` for the authenticated path.

### `_validate_git_credentials` change

Replace:
```python
if not setting_id:
    return
```

With:
```python
if not setting_id:
    try:
        GitBatchLoader.test_public_access(repo_link)
    except ConnectionException:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Repository Not Publicly Accessible",
            details="The repository URL is not accessible without authentication. "
                    "Please select a Git integration to provide credentials.",
            help="Go to Settings > Integrations and add a Git integration, "
                 "then select it when creating the datasource.",
        )
    return
```

This applies to both `create_index_application` and `update_index_application` since both call `_validate_git_credentials`. Public repos re-probe on reindex (3 s max); this gives a clear error if the repo becomes private after creation rather than silently hanging.

### `DatasourceHealthCheckRequest` model

Add one field to `src/codemie/rest_api/models/index.py`:
```python
git_url: Optional[str] = None
```

Non-breaking — existing callers omitting this field continue to work.

### `IndexHealthCheckService.health_check_git`

New `@classmethod` in `src/codemie/service/index/datasource_health_check_service.py`:

```
if request.git_url is missing:
    → return DatasourceHealthCheckResponse(error=ErrorMessage(field_error="git_url"))

if request.setting_id is None:
    → GitBatchLoader.test_public_access(request.git_url)
    → return DatasourceHealthCheckResponse(documents_count=0)

else:
    → SettingsService.get_git_creds(user_id, project_name, git_url, setting_id)
    → GitBatchLoader.test_connection(request.git_url, creds)
    → return DatasourceHealthCheckResponse(documents_count=0)
```

`health_check_datasource` gets a new case:
```python
case DatasourceTypes.GIT:
    return cls.health_check_git(request, user_id)
```

`ConnectionException` and `MissingIntegrationException` are already caught by the outer try/except in `health_check_datasource` and mapped to error responses.

---

## Data Flow

**Creation (public repo, no integration):**
```
POST /index → create_index_application
  → _validate_git_credentials(setting_id=None)
    → GitBatchLoader.test_public_access(url)   ← new
      → git ls-remote --exit-code url HEAD (timeout 3s)
      → success → return
      → failure → ConnectionException → ExtendedHTTPException 422
  → index_code_datasource_in_background(...)   ← unchanged
```

**Health check (public repo):**
```
POST /index/health {index_type: "git", git_url: "...", setting_id: null}
  → health_check_datasource → health_check_git   ← new
    → GitBatchLoader.test_public_access(git_url)
    → DatasourceHealthCheckResponse(documents_count=0)
```

**Health check (authenticated repo):**
```
POST /index/health {index_type: "git", git_url: "...", setting_id: "abc"}
  → health_check_datasource → health_check_git   ← new
    → get_git_creds(setting_id="abc")
    → GitBatchLoader.test_connection(git_url, creds)
    → DatasourceHealthCheckResponse(documents_count=0)
```

---

## Error Handling

| Scenario | Error |
|---|---|
| Public repo URL not accessible at creation | `ExtendedHTTPException` 422 — "Repository Not Publicly Accessible" with help text directing to Integrations |
| Public repo URL not accessible at reindex | Same 422 — raised from `_validate_git_credentials` in `update_index_application` |
| Health check missing `git_url` | `DatasourceHealthCheckResponse(error=ErrorMessage(field_error="git_url"))` |
| Health check — repo inaccessible | `ConnectionException` caught by `health_check_datasource` outer handler → error response |
| Health check — bad integration | `MissingIntegrationException` caught by `health_check_datasource` outer handler → error response |
| Existing authenticated creation — no change | All existing `_validate_git_credentials` paths for `setting_id` present are unchanged |

---

## Testing

### `tests/codemie/rest_api/routers/test_index_git_validation.py`

- Update `test_create_datasource_without_setting_id_skips_validation` — mock `GitBatchLoader.test_public_access` to succeed; assert creation succeeds
- Add `test_create_datasource_public_repo_inaccessible` — mock `test_public_access` to raise `ConnectionException`; assert `ExtendedHTTPException` 422 with "Repository Not Publicly Accessible"
- Add `test_reindex_public_repo_probes_accessibility` — mock `test_public_access` on the reindex path; assert probe is called for `setting_id=None` stored datasource

### `tests/codemie/datasource/loader/test_git_loader.py`

- `test_test_public_access_success` — mock `git.cmd.Git().execute` to return normally; assert no exception raised
- `test_test_public_access_git_command_error` — mock `execute` to raise `GitCommandError`; assert `ConnectionException` raised
- `test_test_public_access_timeout` — mock `execute` to raise timeout exception; assert `ConnectionException` raised
- `test_test_connection_builds_auth_url` — mock `execute`; assert called with the auth-embedded URL, not the plain one

### `tests/codemie/service/index/test_datasource_health_check_service.py`

- `test_health_check_git_missing_url` — assert `field_error="git_url"` response
- `test_health_check_git_public_success` — mock `test_public_access` to succeed; assert `documents_count=0` response
- `test_health_check_git_public_inaccessible` — mock `test_public_access` to raise `ConnectionException`; assert error response
- `test_health_check_git_authenticated_success` — mock `get_git_creds` and `test_connection`; assert success response
- `test_health_check_datasource_routes_git` — assert `DatasourceTypes.GIT` routes to `health_check_git`

All tests mock `git.cmd.Git` — no live network calls.
