# Git Datasource Optional Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow Git datasource creation for public repositories without an integration by adding a `git ls-remote` public-accessibility probe, and implement Git health-check support.

**Architecture:** `GitBatchLoader` gains two static methods (`test_public_access`, `test_connection`) that run `git ls-remote` via GitPython's `cmd.Git`. `_validate_git_credentials` replaces its silent early-return with a call to `test_public_access`. `IndexHealthCheckService` gets a `health_check_git` classmethod wired into the existing `health_check_datasource` match statement.

**Tech Stack:** Python, FastAPI, GitPython (`gitpython ^3.1.52`), pytest, `unittest.mock`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/codemie/datasource/loader/git_loader.py` | Modify | Add `test_public_access` + `test_connection` static methods; add `ConnectionException` + `cmd` + `SimpleNamespace` imports |
| `src/codemie/rest_api/routers/index.py` | Modify | Import `ConnectionException`; replace early-return in `_validate_git_credentials` with probe + 422 |
| `src/codemie/rest_api/models/index.py` | Modify | Add `git_url: Optional[str] = None` to `DatasourceHealthCheckRequest` |
| `src/codemie/service/index/datasource_health_check_service.py` | Modify | Import `ConnectionException` + `GitBatchLoader`; add `case DatasourceTypes.GIT:`; add `health_check_git`; add `except ConnectionException` handler |
| `tests/codemie/datasource/loader/test_git_loader.py` | Modify | Add 4 tests for `test_public_access` and `test_connection` |
| `tests/codemie/rest_api/routers/test_index_git_validation.py` | Modify | Update 1 existing test; add 2 new tests |
| `tests/codemie/service/index/test_datasource_health_check_service.py` | Create | 5 tests for `health_check_git` and `DatasourceTypes.GIT` routing |

---

## Task 1: Add `test_public_access` and `test_connection` to `GitBatchLoader`

**Files:**
- Modify: `src/codemie/datasource/loader/git_loader.py`
- Test: `tests/codemie/datasource/loader/test_git_loader.py`

- [ ] **Step 1: Write failing tests for `test_public_access`**

Add these three test methods to the `TestGitBatchLoader` class in `tests/codemie/datasource/loader/test_git_loader.py`. Also add the missing imports at the top of the file.

Add to imports (after the existing imports):
```python
from codemie.datasource.exceptions import ConnectionException
```

Add to `TestGitBatchLoader` class:
```python
@patch('codemie.datasource.loader.git_loader.git_cmd.Git')
def test_test_public_access_success(self, mock_git_cls):
    """test_public_access returns None when ls-remote succeeds."""
    mock_git_cls.return_value.execute.return_value = "abc123\tHEAD"
    # Should not raise
    GitBatchLoader.test_public_access("https://github.com/owner/public-repo")

@patch('codemie.datasource.loader.git_loader.git_cmd.Git')
def test_test_public_access_git_command_error(self, mock_git_cls):
    """test_public_access raises ConnectionException on GitCommandError."""
    from git.exc import GitCommandError
    mock_git_cls.return_value.execute.side_effect = GitCommandError("ls-remote", 128)
    with pytest.raises(ConnectionException):
        GitBatchLoader.test_public_access("https://github.com/owner/private-repo")

@patch('codemie.datasource.loader.git_loader.git_cmd.Git')
def test_test_public_access_generic_exception(self, mock_git_cls):
    """test_public_access raises ConnectionException on any other exception (e.g. timeout)."""
    mock_git_cls.return_value.execute.side_effect = Exception("timed out")
    with pytest.raises(ConnectionException):
        GitBatchLoader.test_public_access("https://github.com/owner/slow-repo")
```

- [ ] **Step 2: Run to verify they fail**

```bash
poetry run pytest tests/codemie/datasource/loader/test_git_loader.py::TestGitBatchLoader::test_test_public_access_success tests/codemie/datasource/loader/test_git_loader.py::TestGitBatchLoader::test_test_public_access_git_command_error tests/codemie/datasource/loader/test_git_loader.py::TestGitBatchLoader::test_test_public_access_generic_exception -v
```

Expected: FAIL with `AttributeError: type object 'GitBatchLoader' has no attribute 'test_public_access'`

- [ ] **Step 3: Add imports and `test_public_access` to `git_loader.py`**

In `src/codemie/datasource/loader/git_loader.py`, change the `from git import` line and add two new imports:

Change:
```python
from git import Blob, Repo, Submodule
```
To:
```python
from git import Blob, Repo, Submodule, cmd as git_cmd
```

Add after the `from git.exc import GitCommandError` line:
```python
from types import SimpleNamespace
```

Add after the `from codemie.datasource.datasources_config import CODE_CONFIG` line:
```python
from codemie.datasource.exceptions import ConnectionException
```

Then add these two static methods to `GitBatchLoader`, just before the `create_loader` classmethod (line ~170):

```python
@staticmethod
def test_public_access(url: str, timeout: int = 3) -> None:
    """Probe whether a git URL is publicly accessible without credentials.

    Raises ConnectionException if the URL requires authentication or is unreachable.
    """
    g = git_cmd.Git()
    try:
        g.execute(
            ["git", "ls-remote", "--exit-code", "--quiet", url, "HEAD"],
            kill_after_timeout=timeout,
        )
    except Exception as e:
        raise ConnectionException("git", "Repository not publicly accessible") from e

@staticmethod
def test_connection(url: str, creds: Credentials, timeout: int = 3) -> None:
    """Probe whether a git URL is accessible using the provided credentials.

    Builds an authenticated URL from creds, then runs ls-remote.
    Raises ConnectionException if the connection fails.
    """
    auth_url = _build_clone_url(creds, SimpleNamespace(link=url))
    g = git_cmd.Git()
    try:
        g.execute(
            ["git", "ls-remote", "--exit-code", "--quiet", auth_url, "HEAD"],
            kill_after_timeout=timeout,
        )
    except Exception as e:
        raise ConnectionException("git", f"Failed to connect to repository at {url}") from e
```

- [ ] **Step 4: Run `test_public_access` tests to verify they pass**

```bash
poetry run pytest tests/codemie/datasource/loader/test_git_loader.py::TestGitBatchLoader::test_test_public_access_success tests/codemie/datasource/loader/test_git_loader.py::TestGitBatchLoader::test_test_public_access_git_command_error tests/codemie/datasource/loader/test_git_loader.py::TestGitBatchLoader::test_test_public_access_generic_exception -v
```

Expected: PASS

- [ ] **Step 5: Write failing test for `test_connection`**

Add to `TestGitBatchLoader` class in `tests/codemie/datasource/loader/test_git_loader.py`:

```python
@patch('codemie.datasource.loader.git_loader.git_cmd.Git')
def test_test_connection_uses_auth_url(self, mock_git_cls):
    """test_connection builds an auth URL from creds and runs ls-remote on it."""
    mock_execute = mock_git_cls.return_value.execute
    mock_execute.return_value = "abc123\tHEAD"

    creds = Credentials(
        url="https://github.com",
        token="mytoken",
        token_name="oauth2",
        auth_type="pat",
    )
    GitBatchLoader.test_connection("https://github.com/owner/repo", creds)

    # The execute call must receive the auth-embedded URL, not the plain one
    # Command list: ["git", "ls-remote", "--exit-code", "--quiet", <url>, "HEAD"]
    # auth_url is at index 4
    cmd_list = mock_execute.call_args[0][0]  # first positional arg is the command list
    assert "mytoken" in cmd_list[4], f"Expected auth URL at index 4, got: {cmd_list}"
```

- [ ] **Step 6: Run to verify it fails**

```bash
poetry run pytest tests/codemie/datasource/loader/test_git_loader.py::TestGitBatchLoader::test_test_connection_uses_auth_url -v
```

Expected: FAIL with `AttributeError: type object 'GitBatchLoader' has no attribute 'test_connection'`

- [ ] **Step 7: Run the test again after Step 3 changes are in place**

```bash
poetry run pytest tests/codemie/datasource/loader/test_git_loader.py::TestGitBatchLoader::test_test_connection_uses_auth_url -v
```

Expected: PASS

- [ ] **Step 8: Run the full git_loader test suite to check for regressions**

```bash
poetry run pytest tests/codemie/datasource/loader/test_git_loader.py -v
```

Expected: all previously-passing tests still PASS

- [ ] **Step 9: Run ruff**

```bash
make ruff
```

Expected: exits 0 with no errors

- [ ] **Step 10: Commit**

```bash
git add src/codemie/datasource/loader/git_loader.py tests/codemie/datasource/loader/test_git_loader.py
git commit -m "EPMCDME-13690: Add GitBatchLoader.test_public_access and test_connection"
```

---

## Task 2: Replace early-return in `_validate_git_credentials` with public-accessibility probe

**Files:**
- Modify: `src/codemie/rest_api/routers/index.py`
- Test: `tests/codemie/rest_api/routers/test_index_git_validation.py`

- [ ] **Step 1: Update the existing `test_create_datasource_without_setting_id_skips_validation` test**

In `tests/codemie/rest_api/routers/test_index_git_validation.py`, the `TestCreateDatasourceValidation` class, update the existing test to mock `test_public_access` so it succeeds. Also add the new inaccessible-repo and reindex tests.

First, update the decorator stack for `test_create_datasource_without_setting_id_skips_validation` — add a `@patch` for `test_public_access`:

Replace the existing test:
```python
@patch('codemie.rest_api.routers.index.ensure_application_exists')
@patch('codemie.rest_api.routers.index.index_code_datasource_in_background')
@patch('codemie.rest_api.routers.index.Application.get_by_id')
@patch('codemie.rest_api.routers.index.IndexInfo.filter_by_project_and_repo')
@patch('codemie.rest_api.routers.index.request_summary_manager.create_request_summary')
def test_create_datasource_without_setting_id_skips_validation(
    self,
    mock_summary,
    mock_index_filter,
    mock_get_app,
    mock_index_bg,
    mock_ensure_app,
):
```

With (adding the `test_public_access` patch):
```python
@patch('codemie.rest_api.routers.index.GitBatchLoader.test_public_access')
@patch('codemie.rest_api.routers.index.ensure_application_exists')
@patch('codemie.rest_api.routers.index.index_code_datasource_in_background')
@patch('codemie.rest_api.routers.index.Application.get_by_id')
@patch('codemie.rest_api.routers.index.IndexInfo.filter_by_project_and_repo')
@patch('codemie.rest_api.routers.index.request_summary_manager.create_request_summary')
def test_create_datasource_without_setting_id_skips_validation(
    self,
    mock_summary,
    mock_index_filter,
    mock_get_app,
    mock_index_bg,
    mock_ensure_app,
    mock_test_public_access,
):
```

The `mock_test_public_access` argument goes last (innermost decorator = first argument after `self`). Adjust the argument order so it is **first after self**, matching the innermost `@patch`:

```python
@patch('codemie.rest_api.routers.index.GitBatchLoader.test_public_access')
@patch('codemie.rest_api.routers.index.ensure_application_exists')
@patch('codemie.rest_api.routers.index.index_code_datasource_in_background')
@patch('codemie.rest_api.routers.index.Application.get_by_id')
@patch('codemie.rest_api.routers.index.IndexInfo.filter_by_project_and_repo')
@patch('codemie.rest_api.routers.index.request_summary_manager.create_request_summary')
def test_create_datasource_without_setting_id_skips_validation(
    self,
    mock_summary,           # outermost @patch → last arg
    mock_index_filter,
    mock_get_app,
    mock_index_bg,
    mock_ensure_app,
    mock_test_public_access,  # innermost @patch → first arg after self
):
    """Test that creating a datasource without integration probes public accessibility."""
    from codemie.rest_api.routers.index import create_index_application

    mock_index_filter.return_value = []
    mock_app = Mock()
    mock_app.name = "test-app"
    mock_get_app.return_value = mock_app
    mock_test_public_access.return_value = None  # public access succeeds

    mock_request = Mock()
    mock_request.state.uuid = "uuid123"
    mock_request.state.user.is_demo_user = False
    mock_request.state.user.as_user_model.return_value = Mock()

    mock_user = Mock()
    mock_user.id = "user123"

    create_git_repo_request = CreateIndexRequest(
        name="test-repo",
        link="https://github.com/owner/public-repo",
        branch="main",
        setting_id=None,
        description="Test repo",
        index_type="code",
        guardrail_assignments=None,
    )

    result = create_index_application(
        app_name="test-app",
        create_git_repo_request=create_git_repo_request,
        request=mock_request,
        tasks=Mock(),
        user=mock_user,
    )

    mock_test_public_access.assert_called_once_with("https://github.com/owner/public-repo")
    assert result.message == "Indexing of datasource test-repo has been started in the background"
```

- [ ] **Step 2: Add the two new tests to `TestCreateDatasourceValidation`**

Add these two tests to the same class:

```python
@patch('codemie.rest_api.routers.index.GitBatchLoader.test_public_access')
@patch('codemie.rest_api.routers.index.ensure_application_exists')
@patch('codemie.rest_api.routers.index.Application.get_by_id')
@patch('codemie.rest_api.routers.index.IndexInfo.filter_by_project_and_repo')
@patch('codemie.rest_api.routers.index.request_summary_manager.create_request_summary')
def test_create_datasource_public_repo_inaccessible(
    self,
    mock_summary,
    mock_index_filter,
    mock_get_app,
    mock_ensure_app,
    mock_test_public_access,
):
    """Test that creating a datasource with inaccessible public URL raises 422."""
    from codemie.datasource.exceptions import ConnectionException
    from codemie.rest_api.routers.index import create_index_application

    mock_index_filter.return_value = []
    mock_app = Mock()
    mock_app.name = "test-app"
    mock_get_app.return_value = mock_app
    mock_test_public_access.side_effect = ConnectionException("git", "Repository not publicly accessible")

    mock_request = Mock()
    mock_request.state.uuid = "uuid123"
    mock_request.state.user.is_demo_user = False
    mock_request.state.user.as_user_model.return_value = Mock()

    mock_user = Mock()
    mock_user.id = "user123"

    create_git_repo_request = CreateIndexRequest(
        name="test-repo",
        link="https://github.com/owner/private-repo",
        branch="main",
        setting_id=None,
        description="Test repo",
        index_type="code",
        guardrail_assignments=None,
    )

    with pytest.raises(ExtendedHTTPException) as exc_info:
        create_index_application(
            app_name="test-app",
            create_git_repo_request=create_git_repo_request,
            request=mock_request,
            tasks=Mock(),
            user=mock_user,
        )

    assert exc_info.value.code == 422
    assert exc_info.value.message == "Repository Not Publicly Accessible"
    assert "Please select a Git integration" in exc_info.value.details
```

And:

```python
@patch('codemie.rest_api.routers.index.GitBatchLoader.test_public_access')
@patch('codemie.rest_api.routers.index.update_code_datasource_in_background')
@patch('codemie.rest_api.routers.index.GitRepo.get_by_app_id')
@patch('codemie.rest_api.routers.index.Application.get_by_id')
@patch('codemie.rest_api.routers.index.IndexInfo.filter_by_project_and_repo')
@patch('codemie.rest_api.routers.index.request_summary_manager.create_request_summary')
@patch('codemie.rest_api.routers.index.Ability')
def test_reindex_public_repo_probes_accessibility(
    self,
    mock_ability,
    mock_summary,
    mock_index_info,
    mock_get_app,
    mock_get_repos,
    mock_update_bg,
    mock_test_public_access,
):
    """Test that reindexing a datasource with setting_id=None probes public accessibility."""
    from codemie.rest_api.routers.index import update_index_application

    mock_index = Mock()
    mock_index.project_name = "test-project"
    mock_index.index_type = "git"
    mock_index_info.return_value = [mock_index]

    mock_ability_instance = Mock()
    mock_ability_instance.can.return_value = True
    mock_ability.return_value = mock_ability_instance

    mock_app = Mock()
    mock_app.name = "test-app"
    mock_get_app.return_value = mock_app

    mock_repo = Mock()
    mock_repo.name = "test-repo"
    mock_repo.link = "https://github.com/owner/public-repo"
    mock_repo.setting_id = None  # previously created without integration
    mock_get_repos.return_value = [mock_repo]

    mock_test_public_access.return_value = None

    mock_request = Mock()
    mock_request.name = None
    mock_request.model_fields_set = set()

    mock_raw_request = Mock()
    mock_raw_request.state.uuid = "uuid123"

    mock_user = Mock()
    mock_user.id = "user123"
    mock_user.as_user_model.return_value = Mock()

    update_index_application(
        app_name="test-app",
        repo_name="test-repo",
        tasks=Mock(),
        request=mock_request,
        raw_request=mock_raw_request,
        full_reindex=True,
        skip_reindex=False,
        resume_indexing=False,
        user=mock_user,
    )

    mock_test_public_access.assert_called_once_with("https://github.com/owner/public-repo")
```

Note: add this test to `TestCreateDatasourceValidation` or a new class — either works since the reindex logic also goes through `_validate_git_credentials`.

- [ ] **Step 3: Run the new tests to verify they fail**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_index_git_validation.py::TestCreateDatasourceValidation::test_create_datasource_without_setting_id_skips_validation tests/codemie/rest_api/routers/test_index_git_validation.py::TestCreateDatasourceValidation::test_create_datasource_public_repo_inaccessible tests/codemie/rest_api/routers/test_index_git_validation.py::TestCreateDatasourceValidation::test_reindex_public_repo_probes_accessibility -v
```

Expected: FAIL (probe mock not yet wired up in production code)

- [ ] **Step 4: Update `_validate_git_credentials` in `src/codemie/rest_api/routers/index.py`**

First, add the import for `ConnectionException`. Find the existing datasource exception imports (around line 43-50) and add:

```python
from codemie.datasource.exceptions import ConnectionException
```

Then find `_validate_git_credentials` (around line 2379) and replace:

```python
    if not setting_id:
        # No git integration configured, skip validation
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

- [ ] **Step 5: Run the tests to verify they pass**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_index_git_validation.py -v
```

Expected: all tests PASS (including the three updated/new ones)

- [ ] **Step 6: Run ruff**

```bash
make ruff
```

Expected: exits 0

- [ ] **Step 7: Commit**

```bash
git add src/codemie/rest_api/routers/index.py tests/codemie/rest_api/routers/test_index_git_validation.py
git commit -m "EPMCDME-13690: Probe public accessibility in _validate_git_credentials when no integration"
```

---

## Task 3: Add `git_url` field, implement `health_check_git`, wire into `health_check_datasource`

**Files:**
- Modify: `src/codemie/rest_api/models/index.py`
- Modify: `src/codemie/service/index/datasource_health_check_service.py`
- Create: `tests/codemie/service/index/test_datasource_health_check_service.py`

- [ ] **Step 1: Write failing tests**

Create `tests/codemie/service/index/test_datasource_health_check_service.py`:

```python
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pytest
from unittest.mock import Mock, patch

from codemie.core.constants import DatasourceTypes
from codemie.datasource.exceptions import ConnectionException
from codemie.rest_api.models.index import DatasourceHealthCheckRequest, DatasourceHealthCheckResponse
from codemie.service.index.datasource_health_check_service import IndexHealthCheckService


class TestHealthCheckGit:
    """Tests for IndexHealthCheckService.health_check_git."""

    def _make_request(self, git_url=None, setting_id=None, project_name="test-project"):
        return DatasourceHealthCheckRequest(
            project_name=project_name,
            index_type=DatasourceTypes.GIT,
            git_url=git_url,
            setting_id=setting_id,
        )

    def test_health_check_git_missing_url(self):
        """Returns field_error response when git_url is not provided."""
        request = self._make_request(git_url=None)
        response = IndexHealthCheckService.health_check_git(request, user_id="user1")
        assert response.error is not None
        assert response.error.field_error == "git_url"

    @patch('codemie.service.index.datasource_health_check_service.GitBatchLoader.test_public_access')
    def test_health_check_git_public_success(self, mock_test_public_access):
        """Returns documents_count=0 when public repo is accessible."""
        mock_test_public_access.return_value = None
        request = self._make_request(git_url="https://github.com/owner/public-repo", setting_id=None)
        response = IndexHealthCheckService.health_check_git(request, user_id="user1")
        assert response.error is None
        assert response.documents_count == 0
        mock_test_public_access.assert_called_once_with("https://github.com/owner/public-repo")

    @patch('codemie.service.index.datasource_health_check_service.GitBatchLoader.test_public_access')
    def test_health_check_git_public_inaccessible(self, mock_test_public_access):
        """ConnectionException from test_public_access propagates for handler mapping."""
        mock_test_public_access.side_effect = ConnectionException("git", "not accessible")
        request = self._make_request(git_url="https://github.com/owner/private-repo", setting_id=None)
        with pytest.raises(ConnectionException):
            IndexHealthCheckService.health_check_git(request, user_id="user1")

    @patch('codemie.service.index.datasource_health_check_service.GitBatchLoader.test_connection')
    @patch('codemie.service.index.datasource_health_check_service.SettingsService.get_git_creds')
    def test_health_check_git_authenticated_success(self, mock_get_creds, mock_test_connection):
        """Returns documents_count=0 when authenticated repo is accessible."""
        mock_creds = Mock()
        mock_get_creds.return_value = mock_creds
        mock_test_connection.return_value = None

        request = self._make_request(
            git_url="https://github.com/owner/private-repo",
            setting_id="setting-abc",
        )
        response = IndexHealthCheckService.health_check_git(request, user_id="user1")

        assert response.error is None
        assert response.documents_count == 0
        mock_get_creds.assert_called_once_with(
            user_id="user1",
            project_name="test-project",
            repo_link="https://github.com/owner/private-repo",
            setting_id="setting-abc",
        )
        mock_test_connection.assert_called_once_with("https://github.com/owner/private-repo", mock_creds)

    @patch('codemie.service.index.datasource_health_check_service.GitBatchLoader.test_public_access')
    def test_health_check_datasource_routes_git(self, mock_test_public_access):
        """health_check_datasource dispatches DatasourceTypes.GIT to health_check_git."""
        mock_test_public_access.return_value = None
        request = DatasourceHealthCheckRequest(
            project_name="test-project",
            index_type=DatasourceTypes.GIT,
            git_url="https://github.com/owner/public-repo",
        )
        response = IndexHealthCheckService.health_check_datasource(request, user_id="user1")
        assert response.error is None
        assert response.documents_count == 0
        mock_test_public_access.assert_called_once()
```

- [ ] **Step 2: Run to verify they fail**

```bash
poetry run pytest tests/codemie/service/index/test_datasource_health_check_service.py -v
```

Expected: FAIL — `DatasourceHealthCheckRequest` has no `git_url` field and `health_check_git` does not exist yet

- [ ] **Step 3: Add `git_url` to `DatasourceHealthCheckRequest`**

In `src/codemie/rest_api/models/index.py`, find `DatasourceHealthCheckRequest` (line ~1249):

```python
class DatasourceHealthCheckRequest(BaseModel):
    project_name: str
    index_type: str
    cql: Optional[str] = None
    jql: Optional[str] = None
    wiki_query: Optional[str] = None
    wiki_name: Optional[str] = None
    wiql_query: Optional[str] = None
    setting_id: Optional[str] = None
    svn_repo_url: Optional[str] = None
    svn_branch: Optional[str] = "trunk"
```

Add `git_url` after `svn_branch`:

```python
class DatasourceHealthCheckRequest(BaseModel):
    project_name: str
    index_type: str
    cql: Optional[str] = None
    jql: Optional[str] = None
    wiki_query: Optional[str] = None
    wiki_name: Optional[str] = None
    wiql_query: Optional[str] = None
    setting_id: Optional[str] = None
    svn_repo_url: Optional[str] = None
    svn_branch: Optional[str] = "trunk"
    git_url: Optional[str] = None
```

- [ ] **Step 4: Add imports and `health_check_git` to `datasource_health_check_service.py`**

In `src/codemie/service/index/datasource_health_check_service.py`, update the imports block. Change:

```python
from codemie.datasource.exceptions import (
    InvalidQueryException,
    MissingIntegrationException,
    UnauthorizedException,
    EmptyResultException,
)
```

To:

```python
from codemie.datasource.exceptions import (
    ConnectionException,
    InvalidQueryException,
    MissingIntegrationException,
    UnauthorizedException,
    EmptyResultException,
)
from codemie.datasource.loader.git_loader import GitBatchLoader
```

Then in `health_check_datasource`, find the `case DatasourceTypes.SVN:` branch and add the Git case right after it:

```python
                case DatasourceTypes.SVN:
                    return cls.health_check_svn(request, user_id)
                case DatasourceTypes.GIT:
                    return cls.health_check_git(request, user_id)
```

Also add a `ConnectionException` handler to the existing `except` chain in `health_check_datasource`. After the `except MissingIntegrationException` block, add:

```python
        except ConnectionException as e:
            return DatasourceHealthCheckResponse(
                error=ErrorMessage(
                    message=str(e),
                    details=f"An error occurred while checking the connection: {str(e)}",
                    help="Please verify the repository URL is correct and accessible.",
                )
            )
```

Then add the `health_check_git` classmethod after `health_check_svn`:

```python
@classmethod
def health_check_git(cls, request: DatasourceHealthCheckRequest, user_id: str):
    if not request.git_url:
        return DatasourceHealthCheckResponse(
            error=ErrorMessage(
                message="Git repository URL is required",
                details="Provide git_url in the request to test the Git connection.",
                help="Include the Git repository URL (e.g. https://github.com/owner/repo) in the request.",
                field_error="git_url",
            )
        )
    if not request.setting_id:
        GitBatchLoader.test_public_access(request.git_url)
        return DatasourceHealthCheckResponse(documents_count=0)

    git_creds = SettingsService.get_git_creds(
        user_id=user_id,
        project_name=request.project_name,
        repo_link=request.git_url,
        setting_id=request.setting_id,
    )
    GitBatchLoader.test_connection(request.git_url, git_creds)
    return DatasourceHealthCheckResponse(documents_count=0)
```

- [ ] **Step 5: Run the health check service tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/index/test_datasource_health_check_service.py -v
```

Expected: all 5 tests PASS

- [ ] **Step 6: Run the full test suite for affected areas**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_index_git_validation.py tests/codemie/datasource/loader/test_git_loader.py tests/codemie/service/index/ -v
```

Expected: all tests PASS

- [ ] **Step 7: Run ruff**

```bash
make ruff
```

Expected: exits 0

- [ ] **Step 8: Commit**

```bash
git add src/codemie/rest_api/models/index.py src/codemie/service/index/datasource_health_check_service.py tests/codemie/service/index/test_datasource_health_check_service.py
git commit -m "EPMCDME-13690: Add health_check_git and git_url field to DatasourceHealthCheckRequest"
```

---

## Final Verification

- [ ] **Run the full test suite**

```bash
make test
```

Expected: all tests pass, no regressions

- [ ] **Run ruff one final time**

```bash
make ruff
```

Expected: exits 0
