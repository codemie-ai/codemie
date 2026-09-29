# IDOR File Download Auth — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the IDOR vulnerability in `GET /v1/files/{file_name}` by adding authentication and a 5-rule ownership check, while preserving access for workspace owners and authenticated share recipients.

**Architecture:** `read_file` gains `Depends(authenticate)` + `share_token` param. A new `_authorize_file_access` helper implements 5 rules (owner match, workflow SVG allow-list, MCP screenshot allow-all, workspace ownership, share grant). New `find_by_blob` repository method enables the share-grant workspace-blob lookup. `share_token` is surfaced in `get_shared_conversation` for frontend URL construction. No token format change, no migration, no new config keys.

**Tech Stack:** FastAPI (sync handler), SQLModel, Elasticsearch (`SharedConversation`/`Conversation` via `get_by_fields`/`find_by_id`), pytest-anyio, pytest-mock.

## Global Constraints

- Python 3.11+: use `X | Y` union syntax, not `Optional[X]`
- `_authorize_file_access` cyclomatic complexity ≤ 16 (ruff `max-complexity`, `pyproject.toml:237`); current 5 rules fit under the limit
- `B904` and `G004` are in ruff ignore list — bare `raise` inside `except` and f-string logging both allowed
- `read_file` must stay synchronous (D-6 — FastAPI runs sync endpoints in threadpool)
- No new DB columns, no migration, no new config keys
- Commit prefix: `EPMCDME-12708:` per git workflow guide
- Approved spec is canonical: `docs/superpowers/tasks/2026-08-20-epmcdme-12708-idor-file-download-auth/design-section-2-final.md`

---

## File Map

| File | Change |
|---|---|
| `src/codemie/repository/agent_workspace_repository.py` | Add `find_by_blob` method |
| `src/codemie/service/file_service/file_service.py` | Add `load_content` class method |
| `src/codemie/service/share_conversation_service.py` | Add `"share_token"` key to `get_shared_conversation` return dict |
| `src/codemie/rest_api/routers/files.py` | Add imports, `_workspace_repo`, `_raise_file_not_found`, `_authorize_file_access`; rewrite `read_file` handler |
| `tests/codemie/rest_api/routers/test_files.py` | Retrofit 18 existing GET tests; add 17 new test functions |

---

### Task 1: `find_by_blob` in `AgentWorkspaceRepository`

**Files:**
- Modify: `src/codemie/repository/agent_workspace_repository.py` (after line 63, after `get_file`)
- Test: `tests/codemie/rest_api/routers/test_files.py`

**Interfaces:**
- Consumes: `AgentWorkspaceFile`, `Session`, `select` (all already imported in the file)
- Produces: `AgentWorkspaceRepository.find_by_blob(workspace_id: str, blob_owner: str, blob_name: str, mime_type: str | None = None) -> Optional[AgentWorkspaceFile]`

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/rest_api/routers/test_files.py`, below the existing imports:

```python
from codemie.repository.agent_workspace_repository import AgentWorkspaceRepository
from codemie.rest_api.models.agent_workspace import AgentWorkspaceFile


def test_find_by_blob_returns_matching_record(mocker):
    repo = AgentWorkspaceRepository()
    mock_file = mocker.Mock(spec=AgentWorkspaceFile)

    session_mock = mocker.MagicMock()
    session_mock.exec.return_value.first.return_value = mock_file

    with mocker.patch("codemie.repository.agent_workspace_repository.Session") as mock_session_cls:
        mock_session_cls.return_value.__enter__.return_value = session_mock
        result = repo.find_by_blob("ws-1", "owner-1", "file.txt", "text/plain")

    assert result is mock_file


def test_find_by_blob_none_mime_omits_mime_condition(mocker):
    repo = AgentWorkspaceRepository()
    session_mock = mocker.MagicMock()
    session_mock.exec.return_value.first.return_value = None

    with mocker.patch("codemie.repository.agent_workspace_repository.Session") as mock_session_cls:
        mock_session_cls.return_value.__enter__.return_value = session_mock
        result = repo.find_by_blob("ws-1", "owner-1", "file.txt")

    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/codemie/rest_api/routers/test_files.py::test_find_by_blob_returns_matching_record -v
```

Expected: FAIL — `AttributeError: 'AgentWorkspaceRepository' object has no attribute 'find_by_blob'`

- [ ] **Step 3: Implement `find_by_blob`**

In `src/codemie/repository/agent_workspace_repository.py`, add after line 63 (after the `get_file` method body):

```python
    def find_by_blob(
        self,
        workspace_id: str,
        blob_owner: str,
        blob_name: str,
        mime_type: str | None = None,
    ) -> Optional[AgentWorkspaceFile]:
        with Session(AgentWorkspaceFile.get_engine()) as session:
            conditions = [
                AgentWorkspaceFile.workspace_id == workspace_id,
                AgentWorkspaceFile.blob_owner == blob_owner,
                AgentWorkspaceFile.blob_name == blob_name,
                # Deliberately no deleted_at filter — see D-4.
            ]
            if mime_type is not None:
                conditions.append(AgentWorkspaceFile.mime_type == mime_type)
            return session.exec(select(AgentWorkspaceFile).where(*conditions)).first()
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/codemie/rest_api/routers/test_files.py::test_find_by_blob_returns_matching_record tests/codemie/rest_api/routers/test_files.py::test_find_by_blob_none_mime_omits_mime_condition -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/repository/agent_workspace_repository.py tests/codemie/rest_api/routers/test_files.py
git commit -m "EPMCDME-12708: Add find_by_blob to AgentWorkspaceRepository (D-4: no deleted_at filter)"
```

---

### Task 2: `FileService.load_content`

**Files:**
- Modify: `src/codemie/service/file_service/file_service.py` (after line 34, after `get_file_object`)
- Test: `tests/codemie/rest_api/routers/test_files.py`

**Interfaces:**
- Consumes: `FileObject` (already imported at `file_service.py:15`), `FileRepositoryFactory` (`:17`)
- Produces: `FileService.load_content(file_object: FileObject) -> FileObject` — takes already-decoded FileObject, returns it with content loaded

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/rest_api/routers/test_files.py`:

```python
def test_file_service_load_content_delegates_to_repo(mocker):
    from codemie.service.file_service.file_service import FileService
    from codemie_tools.base.file_object import FileObject as FO

    mock_fo = mocker.Mock(spec=FO)
    mock_fo.name = "report.pdf"
    mock_fo.owner = "user-abc"
    mock_fo.mime_type = "application/pdf"

    mock_result = mocker.Mock()
    mock_repo = mocker.Mock()
    mock_repo.read_file.return_value = mock_result

    mocker.patch(
        "codemie.service.file_service.file_service.FileRepositoryFactory.get_current_repository",
        return_value=mock_repo,
    )

    result = FileService.load_content(mock_fo)

    mock_repo.read_file.assert_called_once_with(
        file_name="report.pdf", owner="user-abc", mime_type="application/pdf"
    )
    assert result is mock_result
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/codemie/rest_api/routers/test_files.py::test_file_service_load_content_delegates_to_repo -v
```

Expected: FAIL — `AttributeError: type object 'FileService' has no attribute 'load_content'`

- [ ] **Step 3: Implement `load_content`**

In `src/codemie/service/file_service/file_service.py`, add after line 34:

```python
    @classmethod
    def load_content(cls, file_object: FileObject) -> FileObject:
        file_repo = FileRepositoryFactory().get_current_repository()
        return file_repo.read_file(
            file_name=file_object.name,
            owner=file_object.owner,
            mime_type=file_object.mime_type,
        )
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/codemie/rest_api/routers/test_files.py::test_file_service_load_content_delegates_to_repo -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/file_service/file_service.py tests/codemie/rest_api/routers/test_files.py
git commit -m "EPMCDME-12708: Add FileService.load_content for pre-decoded FileObject"
```

---

### Task 3: Expose `share_token` in `get_shared_conversation`

**Files:**
- Modify: `src/codemie/service/share_conversation_service.py:126-131`
- Test: `tests/codemie/rest_api/routers/test_files.py`

**Interfaces:**
- Consumes: `ShareConversationService.get_shared_conversation(token: str, user: User)` — signature unchanged
- Produces: same `dict` + `"share_token": token` key (frontend appends `?share_token={token}` to file URLs in shared conversations)

- [ ] **Step 1: Write the failing test (test #15 from spec)**

Add to `tests/codemie/rest_api/routers/test_files.py`:

```python
def test_get_shared_conversation_exposes_share_token(mocker):
    from codemie.service.share_conversation_service import ShareConversationService

    mock_shared = mocker.Mock()
    mock_shared.conversation_id = "conv-1"
    mock_shared.shared_by_user_name = "alice"
    mock_shared.created_at = mocker.Mock()
    mock_shared.access_count = 5
    mock_shared.increment_access_count = mocker.Mock()
    mock_conv = mocker.Mock()
    mock_conv.id = "conv-1"
    mock_conv.assistant_ids = []

    mocker.patch(
        "codemie.service.share_conversation_service.SharedConversation.get_by_fields",
        return_value=mock_shared,
    )
    mocker.patch(
        "codemie.service.share_conversation_service.Conversation.find_by_id",
        return_value=mock_conv,
    )
    mocker.patch(
        "codemie.service.share_conversation_service.Assistant.get_by_ids",
        return_value=[],
    )
    mocker.patch(
        "codemie.service.share_conversation_service.ConversationMonitoringService"
        ".send_share_conversation_metric"
    )

    result = ShareConversationService.get_shared_conversation("tok-abc", User(id="viewer"))

    assert result["share_token"] == "tok-abc"


def test_get_shared_conversation_does_not_mutate_file_names(mocker):
    """file_names entries must remain bare encoded tokens after get_shared_conversation (spec test #15)."""
    from codemie.service.share_conversation_service import ShareConversationService

    original_file_names = ["enc-token-1", "enc-token-2"]
    mock_shared = mocker.Mock()
    mock_shared.conversation_id = "conv-1"
    mock_shared.shared_by_user_name = "alice"
    mock_shared.created_at = mocker.Mock()
    mock_shared.access_count = 5
    mock_shared.increment_access_count = mocker.Mock()
    mock_conv = mocker.Mock()
    mock_conv.id = "conv-1"
    mock_conv.assistant_ids = []
    mock_conv.file_names = original_file_names[:]

    mocker.patch(
        "codemie.service.share_conversation_service.SharedConversation.get_by_fields",
        return_value=mock_shared,
    )
    mocker.patch(
        "codemie.service.share_conversation_service.Conversation.find_by_id",
        return_value=mock_conv,
    )
    mocker.patch(
        "codemie.service.share_conversation_service.Assistant.get_by_ids",
        return_value=[],
    )
    mocker.patch(
        "codemie.service.share_conversation_service.ConversationMonitoringService"
        ".send_share_conversation_metric"
    )

    result = ShareConversationService.get_shared_conversation("tok-abc", User(id="viewer"))

    assert result["conversation"].file_names == original_file_names
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/codemie/rest_api/routers/test_files.py::test_get_shared_conversation_exposes_share_token -v
```

Expected: FAIL — `KeyError: 'share_token'`

- [ ] **Step 3: Add `share_token` to the return dict**

In `src/codemie/service/share_conversation_service.py`, replace lines 126-131:

Old:
```python
        return {
            "conversation": conversation,
            "shared_by": shared.shared_by_user_name,
            "created_at": shared.created_at,
            "access_count": shared.access_count,
        }
```

New:
```python
        return {
            "conversation": conversation,
            "shared_by": shared.shared_by_user_name,
            "created_at": shared.created_at,
            "access_count": shared.access_count,
            "share_token": token,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/codemie/rest_api/routers/test_files.py::test_get_shared_conversation_exposes_share_token tests/codemie/rest_api/routers/test_files.py::test_get_shared_conversation_does_not_mutate_file_names -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/share_conversation_service.py tests/codemie/rest_api/routers/test_files.py
git commit -m "EPMCDME-12708: Expose share_token in get_shared_conversation for frontend URL construction"
```

---

### Task 4: Rewrite `read_file` handler in `files.py`

This task rewrites `GET /files/{file_name}` to enforce authentication and ownership. Tests #1–4 are written first (RED), then the implementation makes them GREEN.

**Files:**
- Modify: `src/codemie/rest_api/routers/files.py`
- Test: `tests/codemie/rest_api/routers/test_files.py`

**Interfaces:**
- Consumes: `FileService.load_content` (Task 2), `AgentWorkspaceRepository.find_by_blob` (Task 1), `SharedConversation`, `Conversation`, `MCP_IMAGES_SUBDIR`, `config.CODEMIE_STORAGE_BUCKET_NAME`
- Produces:
  - `_raise_file_not_found() -> NoReturn`
  - `_authorize_file_access(file_object, user, share_token, file_name_param, workspace_repo) -> None`
  - Updated `read_file(file_name, share_token, user)` handler with `Depends(authenticate)`, 400/401/404 responses

- [ ] **Step 1: Write 4 failing tests (#1–#4 from spec)**

Add to `tests/codemie/rest_api/routers/test_files.py`:

```python
# Test #1: 401 — no authentication
@pytest.mark.anyio
async def test_read_file_requires_authentication():
    """Unauthenticated request must be rejected with 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_encoded_token")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# Test #2: 400 — malformed token
@pytest.mark.anyio
async def test_read_file_malformed_token_returns_400(mocker, authenticated_user):
    """Non-decodable token must return 400, not 500."""
    mocker.patch(
        "codemie.rest_api.routers.files.FileObject.from_encoded_url",
        side_effect=ValueError("bad token"),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/not_valid_base64_token")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()
    assert body["error"]["message"] == "Invalid file token"
    assert body["error"]["details"] == "The file token could not be decoded."


# Test #3: 404 — wrong owner, no share_token
@pytest.mark.anyio
async def test_read_file_wrong_owner_returns_404(mocker, authenticated_user):
    """Forged token with another user's owner must return 404, not 200."""
    mock_fo = mocker.Mock()
    mock_fo.owner = "other_user_id"
    mock_fo.name = "secret.txt"
    mock_fo.mime_type = "text/plain"
    mocker.patch(
        "codemie.rest_api.routers.files.FileObject.from_encoded_url",
        return_value=mock_fo,
    )
    mocker.patch(
        "codemie.rest_api.routers.files._workspace_repo.get_by_id_for_user",
        return_value=None,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_token")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    body = response.json()
    assert body["error"]["details"] == "The requested file could not be found."
    # 404 body must NOT include the file name (existence oracle risk)
    assert "other_user_id" not in response.text
    assert "secret.txt" not in response.text


# Test #4: 200 — own file (Rule A)
@pytest.mark.anyio
async def test_read_file_own_file_succeeds(mocker, authenticated_user):
    """Owner's own file must be served (Rule A)."""
    _setup_read_file_mock(mocker, b"hello", "text/plain", "readme.txt", owner=authenticated_user.id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_encoded_token")
    assert response.status_code == status.HTTP_200_OK
    assert response.content == b"hello"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/codemie/rest_api/routers/test_files.py::test_read_file_requires_authentication tests/codemie/rest_api/routers/test_files.py::test_read_file_malformed_token_returns_400 tests/codemie/rest_api/routers/test_files.py::test_read_file_wrong_owner_returns_404 tests/codemie/rest_api/routers/test_files.py::test_read_file_own_file_succeeds -v
```

Expected: ALL FAIL — `test_requires_authentication` returns 200 (no auth); `test_malformed` returns 500 (no 400 branch); `test_wrong_owner` returns 200 (no auth check); `test_own_file` returns 401 (no auth override yet without retrofit of `_setup_read_file_mock`)

- [ ] **Step 3: Update `_setup_read_file_mock` to accept `owner` parameter**

In `tests/codemie/rest_api/routers/test_files.py`, locate `_setup_read_file_mock` at line ~902. Change:

```python
def _setup_read_file_mock(mocker, content, mime_type, name="test.svg"):
    mock_file_object = mocker.Mock()
    mock_file_object.content = content
    mock_file_object.mime_type = mime_type
    mock_file_object.name = name

    mock_fs_repo = mocker.Mock()
    mock_fs_repo.read_file.return_value = mock_file_object

    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository",
        return_value=mock_fs_repo,
    )
    mocker.patch(
        "codemie.repository.base_file_repository.FileObject.from_encoded_url",
        return_value=mocker.Mock(name=name, owner="user"),
    )
    return mock_file_object
```

To:

```python
def _setup_read_file_mock(mocker, content, mime_type, name="test.svg", owner="test_user"):
    mock_file_object = mocker.Mock()
    mock_file_object.content = content
    mock_file_object.mime_type = mime_type
    mock_file_object.name = name

    mock_fs_repo = mocker.Mock()
    mock_fs_repo.read_file.return_value = mock_file_object

    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository",
        return_value=mock_fs_repo,
    )
    # Use files.py patch path (not base_file_repository) because the new handler calls
    # FileObject.from_encoded_url directly in files.py after the router rewrite.
    mocker.patch(
        "codemie.rest_api.routers.files.FileObject.from_encoded_url",
        return_value=mocker.Mock(spec=["owner", "name", "mime_type"], owner=owner, name=name, mime_type=mime_type),
    )
    return mock_file_object
```

**Note:** The patch path changes from `codemie.repository.base_file_repository.FileObject.from_encoded_url` to `codemie.rest_api.routers.files.FileObject.from_encoded_url` because the new handler calls it directly in `files.py`.

- [ ] **Step 4: Update imports in `files.py`**

In `src/codemie/rest_api/routers/files.py`, make the following import changes:

Line 22 — add `Query`:
```python
from fastapi import APIRouter, Response, UploadFile, Depends, Request, File, Query
```

Line 23 — add `NoReturn`:
```python
from typing import Any, List, NoReturn
```

After line 26 (`from codemie.configs import config`) — add logger:
```python
from codemie.configs.logger import logger
```

Line 27 — add `FileObject`:
```python
from codemie_tools.base.file_object import FileObject, normalise_mime
```

Line 31 — add `MCP_IMAGES_SUBDIR`:
```python
from codemie.repository.repository_factory import FileRepositoryFactory, MCP_IMAGES_SUBDIR
```

After line 37 (`from codemie.service.file_service.mermaid_service import MermaidService`) — add 3 new imports:
```python
from codemie.repository.agent_workspace_repository import AgentWorkspaceRepository
from codemie.rest_api.models.conversation import Conversation
from codemie.rest_api.models.share.shared_conversation import SharedConversation
```

- [ ] **Step 5: Add `_workspace_repo`, `_raise_file_not_found`, and `_authorize_file_access` to `files.py`**

Insert the following block in `src/codemie/rest_api/routers/files.py` immediately before the `@router.get("/files/{file_name}", ...)` decorator (currently at line 195). The block goes after the `READ_FILE_MIME_TYPE_HANDLERS` dict and before the `@router.get` decorator:

```python
_workspace_repo = AgentWorkspaceRepository()  # module-level; tests patch
                                              # codemie.rest_api.routers.files._workspace_repo


def _raise_file_not_found() -> NoReturn:
    raise ExtendedHTTPException(
        code=status.HTTP_404_NOT_FOUND,
        message="File not found",
        details="The requested file could not be found.",
        help="Please verify the file name and try again.",
    )


def _authorize_file_access(
    file_object: FileObject,
    user: User,
    share_token: str | None,
    file_name_param: str,
    workspace_repo: AgentWorkspaceRepository,
) -> None:
    owner = file_object.owner

    # Rule A: requester owns the file directly.
    if owner == user.id:
        return

    # Rule B: workflow schema only. Scoped to the workflows/ prefix because the same
    # owner namespace also holds memory-profiling snapshots (see D-3).
    bucket = config.CODEMIE_STORAGE_BUCKET_NAME
    if bucket and owner == bucket and file_object.name.startswith("workflows/"):
        return

    # Rule C: MCP screenshot — any authenticated user. Recorded residual risk, see D-1.
    if owner == MCP_IMAGES_SUBDIR:
        return

    # Rule D: workspace-prefixed blob, requester owns the workspace.
    # Falls through on miss so Rule E can still grant share recipients.
    if owner.startswith("workspace-"):
        workspace_id = owner.removeprefix("workspace-")
        if workspace_repo.get_by_id_for_user(workspace_id, user.id):
            return

    # Rule E: share grant.
    if share_token:
        shared = SharedConversation.get_by_fields({"share_token.keyword": share_token})
        if shared:
            conversation = Conversation.find_by_id(shared.conversation_id)
            if conversation:
                in_file_names = any(
                    file_name_param == fn
                    for msg in (conversation.history or [])
                    for fn in (getattr(msg, "file_names", None) or [])
                )
                if in_file_names:
                    return
                # Blob identity check: covers both workspace write paths —
                # content-written (blob_owner = workspace-{id}) and reference-written
                # (blob_owner = the original owner, e.g. the sharer's user id).
                ws = workspace_repo.get_by_conversation_for_user(
                    shared.conversation_id, shared.shared_by_user_id
                )
                if ws and workspace_repo.find_by_blob(
                    ws.id, owner, file_object.name, file_object.mime_type
                ):
                    return

    _raise_file_not_found()
```

- [ ] **Step 6: Replace the `read_file` handler (lines 195–243)**

Replace the entire current `read_file` function in `files.py`:

```python
@router.get(
    "/files/{file_name}",
    dependencies=[Depends(authenticate)],
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Malformed file token"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentication required"},
        status.HTTP_404_NOT_FOUND: {"description": "File not found"},
    },
)
def read_file(
    file_name: str,
    share_token: str | None = Query(default=None),
    user: User = Depends(authenticate),
) -> Any:
    try:
        file_object = FileObject.from_encoded_url(file_name)
    except ValueError:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid file token",
            details="The file token could not be decoded.",
            help="Verify the file URL is complete and unmodified.",
        )

    # Authorization runs BEFORE the storage try block. ExtendedHTTPException subclasses
    # Exception; inside the try, the bare except would swallow 404 denials as 500.
    _authorize_file_access(file_object, user, share_token, file_name, _workspace_repo)

    try:
        file_object = FileService.load_content(file_object)

        display_name = _strip_uuid_prefix(file_object.name) or file_object.name
        normalised = normalise_mime(file_object.mime_type)
        handler = READ_FILE_MIME_TYPE_HANDLERS.get(normalised)

        if handler:
            response = handler(file_object.content, display_name)
        elif normalised.startswith(INLINE_SAFE_MIME_PREFIXES):
            response = Response(
                content=file_object.content,
                media_type=normalised,
                headers={"Content-Disposition": _safe_disposition(display_name, "inline")},
            )
        else:
            response = get_attachment_response(file_object.content, display_name)

        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except FileNotFoundError:
        _raise_file_not_found()
    except Exception:
        logger.exception(f"Unexpected error reading file: file_name={file_name!r}, user_id={user.id}")
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Internal server error",
            details="An unexpected error occurred while processing the request.",
            help="Please try again later. If the problem persists, contact support.",
        )
```

- [ ] **Step 7: Run tests #1–#4 to verify they pass**

```
pytest tests/codemie/rest_api/routers/test_files.py::test_read_file_requires_authentication tests/codemie/rest_api/routers/test_files.py::test_read_file_malformed_token_returns_400 tests/codemie/rest_api/routers/test_files.py::test_read_file_wrong_owner_returns_404 tests/codemie/rest_api/routers/test_files.py::test_read_file_own_file_succeeds -v
```

Expected: ALL PASS

- [ ] **Step 8: Run full router test suite to understand current failures scope**

```
pytest tests/codemie/rest_api/routers/test_files.py -v 2>&1 | tail -40
```

Expected: many existing GET tests fail (401 or owner mismatch) — that's expected; Task 5 fixes them.

- [ ] **Step 9: Commit**

```bash
git add src/codemie/rest_api/routers/files.py tests/codemie/rest_api/routers/test_files.py
git commit -m "EPMCDME-12708: Add auth + 5-rule authorization to GET /v1/files/{file_name}"
```

---

### Task 5: Test retrofit + authorization rule tests (#5–#14) + test #1 (401 already done in Task 4)

This task retrofits the 18 existing unauthenticated GET tests and adds tests #5–#14 covering each authorization rule.

**Files:**
- Modify: `tests/codemie/rest_api/routers/test_files.py`

**Interfaces:**
- Consumes: `authenticated_user` fixture, updated `_setup_read_file_mock` (Task 4 step 3)
- Produces: all existing GET tests pass; tests #5–#14 added and passing

#### Sub-task 5a: Retrofit 18 existing unauthenticated GET tests

The 18 tests at lines 86, 107, 757, 775, 790, 805, 934, 954, 966, 978, 991, 1004, 1017, 1030, 1043, 1058, 1076, 1096 currently make unauthenticated GET requests. After Task 4, they return 401.

**Retrofit pattern for tests using `_setup_read_file_mock`** (lines 757–1096, 16 tests):

For each test function that calls `_setup_read_file_mock`, add `authenticated_user` as a parameter (it sets `app.dependency_overrides[authenticate]`). No other change needed because `_setup_read_file_mock` now defaults to `owner="test_user"` which matches `User(id='test_user')`.

Example — `test_read_file_uuid_prefix_stripped_from_content_disposition` (line 750):

Change:
```python
async def test_read_file_uuid_prefix_stripped_from_content_disposition(mocker):
```
To:
```python
async def test_read_file_uuid_prefix_stripped_from_content_disposition(mocker, authenticated_user):
```

Apply the same `mocker, authenticated_user` parameter addition to all 16 tests that use `_setup_read_file_mock`:
- `test_read_file_uuid_prefix_stripped_from_content_disposition` (line ~750)
- `test_read_file_plain_text_has_content_disposition` (line ~768)
- `test_read_file_uuid_only_name_keeps_raw_name` (line ~783)
- `test_read_file_cyrillic_filename_does_not_raise` (line ~798)
- `test_read_file_svg_xss_payload_is_forced_attachment` (line ~922)
- `test_read_file_xml_forces_attachment` (line ~948)
- `test_read_file_xhtml_forces_attachment` (line ~960)
- `test_read_file_rss_forces_attachment` (line ~972)
- `test_read_file_svg_mixed_case_mime_forces_attachment` (line ~984)
- `test_read_file_svg_mime_with_params_forces_attachment` (line ~997)
- `test_read_file_svg_preserves_nosniff_header` (line ~1010)
- `test_read_file_svg_filename_in_disposition` (line ~1023)
- `test_read_file_unknown_mime_is_forced_attachment` (line ~1036)
- `test_read_file_raster_image_serves_inline` (line ~1051)
- `test_read_file_pdf_serves_inline` (line ~1069)
- `test_read_file_inline_cyrillic_filename_encodes_cleanly` (line ~1086)

**Retrofit for `test_read_file_success` (line 63)**:

Change:
```python
async def test_read_file_success(mocker):
    ...
    mocker.patch(
        "codemie.repository.base_file_repository.FileObject.from_encoded_url",
        return_value=mocker.Mock(name="test.txt", owner="user"),
    )
    ...
    response = await ac.get("/v1/files/test.txt")

    assert response.status_code == status.HTTP_200_OK
    assert response.content == mock_file_content
```

To:
```python
async def test_read_file_success(mocker, authenticated_user):
    mock_file_content = b"file content"
    mock_file_object = mocker.Mock()
    mock_file_object.content = mock_file_content
    mock_file_object.mime_type = "text/plain"
    mock_file_object.name = "test.txt"

    mock_fs_repo = mocker.Mock()
    mock_fs_repo.read_file.return_value = mock_file_object

    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository",
        return_value=mock_fs_repo,
    )
    mocker.patch(
        "codemie.rest_api.routers.files.FileObject.from_encoded_url",
        return_value=mocker.Mock(owner=authenticated_user.id, name="test.txt", mime_type="text/plain"),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/test.txt")

    assert response.status_code == status.HTTP_200_OK
    assert response.content == mock_file_content
```

**Retrofit for `test_read_file_not_found` (line 93)**:

Change:
```python
async def test_read_file_not_found(mocker):
    ...
    mocker.patch(
        "codemie.repository.base_file_repository.FileObject.from_encoded_url",
        return_value=mocker.Mock(name="nonexistent.txt", owner="user"),
    )
    ...
    assert response.json() == {
        'error': {
            'details': "The requested file 'nonexistent.txt' could not be found.",
            'help': 'Please verify the file name and try again. If you believe this is an error, contact support.',
            'message': 'File not found',
        }
    }
```

To:
```python
async def test_read_file_not_found(mocker, authenticated_user):
    mock_fs_repo = mocker.Mock()
    mock_fs_repo.read_file.side_effect = FileNotFoundError

    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository",
        return_value=mock_fs_repo,
    )
    mocker.patch(
        "codemie.rest_api.routers.files.FileObject.from_encoded_url",
        return_value=mocker.Mock(owner=authenticated_user.id, name="nonexistent.txt", mime_type="text/plain"),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/nonexistent.txt")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {
        'error': {
            'details': "The requested file could not be found.",
            'help': 'Please verify the file name and try again.',
            'message': 'File not found',
        }
    }
```

- [ ] **Step 1: Apply all 18 retrofits**

Apply the changes described above to all 18 tests.

- [ ] **Step 2: Run retrofitted tests to verify they pass**

```
pytest tests/codemie/rest_api/routers/test_files.py -k "test_read_file_success or test_read_file_not_found or test_read_file_uuid or test_read_file_plain or test_read_file_cyrillic or test_read_file_svg or test_read_file_xml or test_read_file_xhtml or test_read_file_rss or test_read_file_unknown or test_read_file_raster or test_read_file_pdf or test_read_file_inline" -v
```

Expected: ALL PASS

#### Sub-task 5b: Add authorization rule tests (#5–#14 from spec)

- [ ] **Step 3: Write tests #5–#14**

Add to `tests/codemie/rest_api/routers/test_files.py`:

```python
# Test #5: 200 — workflow schema (Rule B)
@pytest.mark.anyio
async def test_read_file_workflow_svg_allowed_for_any_authenticated_user(mocker, authenticated_user):
    """workflow SVGs stored under the bucket owner are served to any authenticated user (Rule B)."""
    from codemie.configs import config as _config

    bucket_name = "test-bucket"
    mocker.patch.object(_config, "CODEMIE_STORAGE_BUCKET_NAME", bucket_name)
    _setup_read_file_mock(
        mocker, b"<svg/>", "image/svg+xml", "workflows/abc-123.svg", owner=bucket_name
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_token")
    assert response.status_code == status.HTTP_200_OK


# Test #6: 404 — memory_snapshots are never accessible (pins D-3)
@pytest.mark.anyio
async def test_read_file_memory_snapshot_denied_for_any_user(mocker, authenticated_user):
    """memory_snapshots/ blobs under the bucket owner must never be served (D-3)."""
    from codemie.configs import config as _config

    bucket_name = "test-bucket"
    mocker.patch.object(_config, "CODEMIE_STORAGE_BUCKET_NAME", bucket_name)

    mock_fo = mocker.Mock()
    mock_fo.owner = bucket_name
    mock_fo.name = "memory_snapshots/snapshot_20260101_pod_deadbeef.json.gz"
    mock_fo.mime_type = "application/gzip"
    mocker.patch(
        "codemie.rest_api.routers.files.FileObject.from_encoded_url",
        return_value=mock_fo,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_token")
    assert response.status_code == status.HTTP_404_NOT_FOUND


# Test #7: 200 — MCP image for any authenticated user (Rule C, D-1 accepted trade-off)
@pytest.mark.anyio
async def test_read_file_mcp_image_allowed_for_authenticated_user(mocker, authenticated_user):
    """MCP screenshots are served to any authenticated user (Rule C).
    D-1 accepted trade-off: ownership record unreliable on default LangGraphAgent path.
    """
    from codemie.repository.repository_factory import MCP_IMAGES_SUBDIR as _mcp_dir

    _setup_read_file_mock(mocker, b"\x89PNG\r\n", "image/png", "abc123.png", owner=_mcp_dir)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_token")
    assert response.status_code == status.HTTP_200_OK


# Test #8: 200 — workspace file, requester owns the workspace (Rule D)
@pytest.mark.anyio
async def test_read_file_workspace_owner_succeeds(mocker, authenticated_user):
    """Requester who owns the workspace can access workspace blobs (Rule D)."""
    workspace_id = "ws-abc-123"
    mock_workspace = mocker.Mock()
    _setup_read_file_mock(
        mocker, b"generated code", "text/plain", "output.py",
        owner=f"workspace-{workspace_id}",
    )
    mocker.patch(
        "codemie.rest_api.routers.files._workspace_repo.get_by_id_for_user",
        return_value=mock_workspace,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_token")
    assert response.status_code == status.HTTP_200_OK


# Test #9: 200 — share grant via in_file_names (Rule E), user-uploaded attachment
@pytest.mark.anyio
async def test_read_file_share_grant_via_file_names(mocker, authenticated_user):
    """Share recipient can access file listed in conversation.history[].file_names (Rule E)."""
    file_token = "encoded_file_token_xyz"
    mock_fo = mocker.Mock()
    mock_fo.owner = "alice_user_id"
    mock_fo.name = "attachment.pdf"
    mock_fo.mime_type = "application/pdf"
    mocker.patch(
        "codemie.rest_api.routers.files.FileObject.from_encoded_url",
        return_value=mock_fo,
    )

    mock_msg = mocker.Mock()
    mock_msg.file_names = [file_token]
    mock_conv = mocker.Mock()
    mock_conv.history = [mock_msg]

    mock_shared = mocker.Mock()
    mock_shared.conversation_id = "conv-1"
    mock_shared.shared_by_user_id = "alice_user_id"

    mocker.patch(
        "codemie.rest_api.routers.files.SharedConversation.get_by_fields",
        return_value=mock_shared,
    )
    mocker.patch(
        "codemie.rest_api.routers.files.Conversation.find_by_id",
        return_value=mock_conv,
    )

    mock_repo = mocker.Mock()
    mock_repo.read_file.return_value = mocker.Mock(
        content=b"pdf bytes", mime_type="application/pdf", name="attachment.pdf"
    )
    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository",
        return_value=mock_repo,
    )
    mocker.patch(
        "codemie.rest_api.routers.files._workspace_repo.get_by_id_for_user",
        return_value=None,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(f"/v1/files/{file_token}?share_token=my-share-tok")
    assert response.status_code == status.HTTP_200_OK


# Test #10: 200 — share grant via find_by_blob, reference-written file (blob_owner = sharer user id)
@pytest.mark.anyio
async def test_read_file_share_grant_via_find_by_blob_reference_written(mocker, authenticated_user):
    """Share recipient can access AI-generated file registered by reference (blob_owner = sharer user id)."""
    mock_fo = mocker.Mock()
    mock_fo.owner = "alice_user_id"  # reference-written: blob_owner is the original owner
    mock_fo.name = "diagram.png"
    mock_fo.mime_type = "image/png"
    mocker.patch(
        "codemie.rest_api.routers.files.FileObject.from_encoded_url",
        return_value=mock_fo,
    )

    mock_conv = mocker.Mock()
    mock_conv.history = []  # no file_names match → falls through to find_by_blob

    mock_shared = mocker.Mock()
    mock_shared.conversation_id = "conv-1"
    mock_shared.shared_by_user_id = "alice_user_id"

    mock_workspace = mocker.Mock()
    mock_workspace.id = "ws-1"

    mocker.patch(
        "codemie.rest_api.routers.files.SharedConversation.get_by_fields",
        return_value=mock_shared,
    )
    mocker.patch(
        "codemie.rest_api.routers.files.Conversation.find_by_id",
        return_value=mock_conv,
    )
    mocker.patch(
        "codemie.rest_api.routers.files._workspace_repo.get_by_id_for_user",
        return_value=None,
    )
    mocker.patch(
        "codemie.rest_api.routers.files._workspace_repo.get_by_conversation_for_user",
        return_value=mock_workspace,
    )
    mocker.patch(
        "codemie.rest_api.routers.files._workspace_repo.find_by_blob",
        return_value=mocker.Mock(),
    )

    mock_repo = mocker.Mock()
    mock_repo.read_file.return_value = mocker.Mock(
        content=b"png bytes", mime_type="image/png", name="diagram.png"
    )
    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository",
        return_value=mock_repo,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_token?share_token=my-share-tok")
    assert response.status_code == status.HTTP_200_OK


# Test #11: 200 — workspace blob where Rule D misses but Rule E hits (non-owner share recipient)
@pytest.mark.anyio
async def test_read_file_workspace_blob_rule_d_miss_rule_e_hit(mocker, authenticated_user):
    """Non-owner of workspace can still access workspace blob if they hold a share token (Rule D miss → Rule E hit)."""
    workspace_id = "ws-xyz"
    mock_fo = mocker.Mock()
    mock_fo.owner = f"workspace-{workspace_id}"
    mock_fo.name = "output.py"
    mock_fo.mime_type = "text/x-python"
    mocker.patch(
        "codemie.rest_api.routers.files.FileObject.from_encoded_url",
        return_value=mock_fo,
    )

    mock_conv = mocker.Mock()
    mock_conv.history = []

    mock_shared = mocker.Mock()
    mock_shared.conversation_id = "conv-1"
    mock_shared.shared_by_user_id = "alice_user_id"

    mock_workspace = mocker.Mock()
    mock_workspace.id = workspace_id

    mocker.patch(
        "codemie.rest_api.routers.files._workspace_repo.get_by_id_for_user",
        return_value=None,  # Rule D miss
    )
    mocker.patch(
        "codemie.rest_api.routers.files.SharedConversation.get_by_fields",
        return_value=mock_shared,
    )
    mocker.patch(
        "codemie.rest_api.routers.files.Conversation.find_by_id",
        return_value=mock_conv,
    )
    mocker.patch(
        "codemie.rest_api.routers.files._workspace_repo.get_by_conversation_for_user",
        return_value=mock_workspace,
    )
    mocker.patch(
        "codemie.rest_api.routers.files._workspace_repo.find_by_blob",
        return_value=mocker.Mock(),
    )

    mock_repo = mocker.Mock()
    mock_repo.read_file.return_value = mocker.Mock(
        content=b"code", mime_type="text/x-python", name="output.py"
    )
    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository",
        return_value=mock_repo,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_token?share_token=my-share-tok")
    assert response.status_code == status.HTTP_200_OK


# Test #12: 404 — workspace blob, non-owner, no share_token
@pytest.mark.anyio
async def test_read_file_workspace_blob_non_owner_no_share_token_returns_404(mocker, authenticated_user):
    """Workspace blob with no matching workspace ownership and no share_token returns 404."""
    mock_fo = mocker.Mock()
    mock_fo.owner = "workspace-ws-abc"
    mock_fo.name = "secret.py"
    mock_fo.mime_type = "text/x-python"
    mocker.patch(
        "codemie.rest_api.routers.files.FileObject.from_encoded_url",
        return_value=mock_fo,
    )
    mocker.patch(
        "codemie.rest_api.routers.files._workspace_repo.get_by_id_for_user",
        return_value=None,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_token")
    assert response.status_code == status.HTTP_404_NOT_FOUND


# Test #13: 404-parity — denial and genuine-missing return byte-identical JSON bodies
@pytest.mark.anyio
async def test_read_file_404_parity_denial_equals_genuine_missing(mocker, authenticated_user):
    """Authorization 404 and storage FileNotFoundError 404 must have identical JSON bodies."""
    # Denial 404 (wrong owner)
    mock_fo_wrong = mocker.Mock()
    mock_fo_wrong.owner = "other_user"
    mock_fo_wrong.name = "file.txt"
    mock_fo_wrong.mime_type = "text/plain"
    mocker.patch(
        "codemie.rest_api.routers.files.FileObject.from_encoded_url",
        return_value=mock_fo_wrong,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        denial_resp = await ac.get("/v1/files/some_token")

    # Storage FileNotFoundError 404 (correct owner)
    mock_fo_own = mocker.Mock()
    mock_fo_own.owner = authenticated_user.id
    mock_fo_own.name = "missing.txt"
    mock_fo_own.mime_type = "text/plain"
    mocker.patch(
        "codemie.rest_api.routers.files.FileObject.from_encoded_url",
        return_value=mock_fo_own,
    )
    mock_repo = mocker.Mock()
    mock_repo.read_file.side_effect = FileNotFoundError
    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository",
        return_value=mock_repo,
    )
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        genuine_resp = await ac.get("/v1/files/another_token")

    assert denial_resp.status_code == genuine_resp.status_code == status.HTTP_404_NOT_FOUND
    assert denial_resp.json() == genuine_resp.json()


# Test #14: 404 not 500 — proves authorization runs OUTSIDE the storage try block
@pytest.mark.anyio
async def test_read_file_auth_outside_try_block_returns_404_not_500(mocker, authenticated_user):
    """Authorization denial must return 404, not 500, proving it runs before the storage try block."""
    mock_fo = mocker.Mock()
    mock_fo.owner = "wrong_owner_id"
    mock_fo.name = "file.txt"
    mock_fo.mime_type = "text/plain"
    mocker.patch(
        "codemie.rest_api.routers.files.FileObject.from_encoded_url",
        return_value=mock_fo,
    )
    # Even if storage would raise Exception, auth runs first and returns 404
    mock_repo = mocker.Mock()
    mock_repo.read_file.side_effect = RuntimeError("should never be reached")
    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository",
        return_value=mock_repo,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_token")
    assert response.status_code == status.HTTP_404_NOT_FOUND
```

- [ ] **Step 4: Run tests #5–#14 to verify they fail**

```
pytest tests/codemie/rest_api/routers/test_files.py -k "test_read_file_workflow_svg or test_read_file_memory_snapshot or test_read_file_mcp_image or test_read_file_workspace_owner or test_read_file_share_grant or test_read_file_workspace_blob or test_read_file_404_parity or test_read_file_auth_outside" -v
```

Expected: ALL FAIL (Task 4 must be done first for these to pass; if Task 4 is complete, re-run will show PASS)

- [ ] **Step 5: Run the full test suite to confirm all tests pass**

```
pytest tests/codemie/rest_api/routers/test_files.py -v
```

Expected: ALL PASS

- [ ] **Step 6: Run ruff on changed files**

```
make ruff
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add tests/codemie/rest_api/routers/test_files.py
git commit -m "EPMCDME-12708: Retrofit unauthenticated tests + add 14 authorization rule tests"
```

---

## Final verification

- [ ] **Run full test suite once more**

```
pytest tests/codemie/rest_api/routers/test_files.py -v --tb=short 2>&1 | tail -20
```

Expected: all pass, zero failures.

- [ ] **Run quality gates**

```
make ruff
```

Expected: clean.

---

## Open pre-conditions (document answers before deploy — not blockers for coding)

| ID | Question | Where to look |
|---|---|---|
| PRE-1 | What are the actual ingress annotations on `/code-assistant-api/(.*)`? oauth2-proxy injecting `Authorization` matters for `<img src>` auth. | `deploy-templates/values.yaml` overlays; ops team |
| PRE-2 | Are chat files rendered via `<img src>` (can't set custom header) or fetch + blob URL (can set `Authorization`)? | Frontend team; `codemie_access_token` cookie (`config.py:245`) is an alternative |
| PRE-3 | Does the frontend URL-encode `?` in share_token when building the `?share_token=` query param? | Frontend team; determines query string vs `X-Share-Token` header transport |
