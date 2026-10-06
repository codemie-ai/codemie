# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

from codemie.core.constants import MermaidMimeType
import pytest
import re

from codemie.repository.agent_workspace_repository import AgentWorkspaceRepository
from codemie.rest_api.models.agent_workspace import AgentWorkspaceFile

from urllib.parse import unquote

from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI, Response, status
from fastapi.testclient import TestClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import extended_http_exception_handler
from codemie.rest_api.routers import files
from codemie.rest_api.security.user import User
from codemie.rest_api.routers.files import router
from codemie.rest_api.security.authentication import authenticate
import hashlib

app = FastAPI()
app.include_router(router)
app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)

client = TestClient(app, base_url="http://testserver")


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def authenticated_user():
    mock_user = User(id='test_user')

    def mock_authenticate():
        return mock_user

    app.dependency_overrides[authenticate] = mock_authenticate
    yield mock_user
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(authenticated_user):
    return {"user-id": authenticated_user.id}


@pytest.mark.anyio
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
    mock_fo = mocker.Mock()
    mock_fo.owner = authenticated_user.id
    mock_fo.name = "test.txt"
    mock_fo.mime_type = "text/plain"
    mocker.patch(
        "codemie.rest_api.routers.files.FileObject.from_encoded_url",
        return_value=mock_fo,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/test.txt")

    assert response.status_code == status.HTTP_200_OK
    assert response.content == mock_file_content


@pytest.mark.anyio
async def test_read_file_not_found(mocker, authenticated_user):
    mock_fs_repo = mocker.Mock()
    mock_fs_repo.read_file.side_effect = FileNotFoundError

    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository",
        return_value=mock_fs_repo,
    )
    mock_fo = mocker.Mock()
    mock_fo.owner = authenticated_user.id
    mock_fo.name = "nonexistent.txt"
    mock_fo.mime_type = "text/plain"
    mocker.patch(
        "codemie.rest_api.routers.files.FileObject.from_encoded_url",
        return_value=mock_fo,
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


@pytest.mark.anyio
async def test_write_file_success(authenticated_user, auth_headers, mocker):
    mock_file_url = "encoded_file_url"
    mock_file_object = mocker.Mock()
    mock_file_object.to_encoded_url.return_value = mock_file_url

    mock_fs_repo = mocker.Mock()
    mock_fs_repo.write_file.return_value = mock_file_object

    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository", return_value=mock_fs_repo
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/files/", files={"file": ("test.txt", b"file content", "text/plain")}, headers=auth_headers
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"file_url": mock_file_url}


@pytest.mark.anyio
async def test_write_files_bulk_success(authenticated_user, auth_headers, mocker):
    mock_file_url_1 = "encoded_file_url_1"
    mock_file_url_2 = "encoded_file_url_2"

    mock_file_object_1 = mocker.Mock()
    mock_file_object_1.to_encoded_url.return_value = mock_file_url_1

    mock_file_object_2 = mocker.Mock()
    mock_file_object_2.to_encoded_url.return_value = mock_file_url_2

    mock_fs_repo = mocker.Mock()
    mock_fs_repo.write_file.side_effect = [mock_file_object_1, mock_file_object_2]

    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository", return_value=mock_fs_repo
    )
    mocker.patch("codemie.rest_api.routers.files.MarkdownCacheService")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/files/bulk",
            files=[
                ("files", ("test1.txt", b"file content 1", "text/plain")),
                ("files", ("test2.txt", b"file content 2", "text/plain")),
            ],
            headers=auth_headers,
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "files": [{"file_url": mock_file_url_1}, {"file_url": mock_file_url_2}],
        "failed_files": None,
    }
    assert mock_fs_repo.write_file.call_count == 2


@pytest.mark.anyio
async def test_write_files_bulk_with_failures(authenticated_user, auth_headers, mocker):
    mock_file_url = "encoded_file_url"
    mock_file_object = mocker.Mock()
    mock_file_object.to_encoded_url.return_value = mock_file_url

    mock_fs_repo = mocker.Mock()
    mock_fs_repo.write_file.side_effect = [
        mock_file_object,  # First file succeeds
        Exception("Storage error"),  # Second file fails
    ]

    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository", return_value=mock_fs_repo
    )
    mocker.patch("codemie.rest_api.routers.files.MarkdownCacheService")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/files/bulk",
            files=[
                ("files", ("test1.txt", b"file content 1", "text/plain")),
                ("files", ("test2.txt", b"file content 2", "text/plain")),
            ],
            headers=auth_headers,
        )

    assert response.status_code == status.HTTP_200_OK
    response_json = response.json()
    assert len(response_json["files"]) == 1
    assert response_json["files"][0]["file_url"] == mock_file_url
    assert "test2.txt" in response_json["failed_files"]
    assert "Storage error" in response_json["failed_files"]["test2.txt"]


@pytest.mark.anyio
async def test_write_files_bulk_no_files(authenticated_user, auth_headers, mocker):
    mock_fs_repo = mocker.Mock()

    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository", return_value=mock_fs_repo
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/files/bulk",
            data={},  # Empty data
            headers=auth_headers,
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY  # FastAPI validation error


@pytest.mark.anyio
async def test_write_files_bulk_individual_size_exceeded(authenticated_user, auth_headers, mocker):
    mock_config = mocker.Mock()
    mock_config.FILES_STORAGE_MAX_UPLOAD_SIZE = 10  # Very small limit for testing
    mocker.patch("codemie.rest_api.routers.files.config", mock_config)

    mock_file_url = "encoded_file_url"
    mock_file_object = mocker.Mock()
    mock_file_object.to_encoded_url.return_value = mock_file_url

    mock_fs_repo = mocker.Mock()
    mock_fs_repo.write_file.return_value = mock_file_object
    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository", return_value=mock_fs_repo
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/files/bulk",
            files=[
                ("files", ("test1.txt", b"file content that exceeds limit", "text/plain")),
                ("files", ("test2.txt", b"small", "text/plain")),
            ],
            headers=auth_headers,
        )

    assert response.status_code == status.HTTP_200_OK
    response_json = response.json()
    # First file should fail due to size limit
    assert "test1.txt" in response_json["failed_files"]
    assert response_json["failed_files"]["test1.txt"] == "File size exceeds the maximum allowed size."
    # Second file should succeed
    assert len(response_json["files"]) == 1
    assert response_json["files"][0]["file_url"] == mock_file_url


# Mermaid diagram tests


def test_create_mermaid_diagram_success_new_file(mocker, auth_headers):
    """Test successful creation of a new Mermaid diagram."""
    mock_mermaid_code = "graph TD; A-->B; B-->C;"
    mock_svg_content = b"<svg>Mock SVG content</svg>"
    mock_file_url = "encoded_file_url"

    hash_object = hashlib.sha256(mock_mermaid_code.encode())
    code_hash = hash_object.hexdigest()
    expected_filename = f"mermaid_{code_hash}.svg"

    mock_config = mocker.Mock()
    mock_config.is_local = True
    mock_config.API_ROOT_PATH = ""
    mocker.patch("codemie.rest_api.routers.files.config", mock_config)
    mocker.patch(
        "codemie.service.file_service.mermaid_service.MermaidService.draw_mermaid",
        return_value=mock_svg_content,
    )

    mock_file_object = mocker.Mock()
    mock_file_object.to_encoded_url.return_value = mock_file_url
    mock_fs_repo = mocker.Mock()
    mock_fs_repo.read_file.side_effect = FileNotFoundError("File not found")
    mock_fs_repo.write_file.return_value = mock_file_object
    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository", return_value=mock_fs_repo
    )

    response = client.post(
        "/v1/files/diagram/mermaid",
        json={"code": mock_mermaid_code},
        headers=auth_headers,
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"file_url": f"http://testserver/v1/files/{mock_file_url}"}
    mock_fs_repo.read_file.assert_called_once_with(expected_filename, owner="test_user")
    mock_fs_repo.write_file.assert_called_once_with(
        name=expected_filename, mime_type=MermaidMimeType.SVG, owner="test_user", content=mock_svg_content
    )


def test_create_mermaid_diagram_success_existing_file(mocker, auth_headers):
    """Test successful retrieval of an existing Mermaid diagram."""
    # Mock data
    mock_mermaid_code = "graph TD; A-->B; B-->C;"
    mock_file_url = "encoded_file_url"

    hash_object = hashlib.sha256(mock_mermaid_code.encode())
    code_hash = hash_object.hexdigest()
    expected_filename = f"mermaid_{code_hash}.svg"

    mock_config = mocker.Mock()
    mock_config.is_local = True
    mock_config.API_ROOT_PATH = ""
    mocker.patch("codemie.rest_api.routers.files.config", mock_config)

    # Mock file repository with existing file
    mock_existing_file = mocker.Mock()
    mock_existing_file.to_encoded_url.return_value = mock_file_url
    mock_fs_repo = mocker.Mock()
    mock_fs_repo.read_file.return_value = mock_existing_file
    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository", return_value=mock_fs_repo
    )

    response = client.post(
        "/v1/files/diagram/mermaid",
        json={"code": mock_mermaid_code},
        headers=auth_headers,
    )

    # Assertions
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"file_url": f"http://testserver/v1/files/{mock_file_url}"}

    # Verify mocks were called correctly
    mock_fs_repo.read_file.assert_called_once_with(expected_filename, owner="test_user")
    # write_file should not be called since the file already exists
    mock_fs_repo.write_file.assert_not_called()


def test_create_mermaid_diagram_syntax_error(mocker, auth_headers):
    """Test handling of Mermaid syntax errors."""
    # Mock data
    mock_mermaid_code = "invalid mermaid syntax"

    hash_object = hashlib.sha256(mock_mermaid_code.encode())
    code_hash = hash_object.hexdigest()
    expected_filename = f"mermaid_{code_hash}.svg"

    # Mock file repository - file doesn't exist
    mock_fs_repo = mocker.Mock()
    mock_fs_repo.read_file.side_effect = FileNotFoundError("File not found")
    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository", return_value=mock_fs_repo
    )

    # Mock FileService.draw_mermaid to raise an exception
    mocker.patch(
        "codemie.service.file_service.mermaid_service.MermaidService.draw_mermaid",
        side_effect=Exception("Syntax error in Mermaid diagram"),
    )

    response = client.post(
        "/v1/files/diagram/mermaid",
        json={"code": mock_mermaid_code},
        headers=auth_headers,
    )

    # Assertions
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {
        'error': {
            'details': "Syntax error in Mermaid diagram",
            'help': 'Please check your Mermaid syntax and try again.',
            'message': 'Mermaid diagram configuration error',
        }
    }

    # Verify read_file was called with the correct parameters
    mock_fs_repo.read_file.assert_called_once_with(expected_filename, owner="test_user")


def test_create_mermaid_diagram_service_unavailable(mocker, auth_headers):
    """Test handling of Mermaid service being unavailable."""
    # Mock data
    mock_mermaid_code = "graph TD; A-->B; B-->C;"

    hash_object = hashlib.sha256(mock_mermaid_code.encode())
    code_hash = hash_object.hexdigest()
    expected_filename = f"mermaid_{code_hash}.svg"

    # Mock file repository - file doesn't exist
    mock_fs_repo = mocker.Mock()
    mock_fs_repo.read_file.side_effect = FileNotFoundError("File not found")
    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository", return_value=mock_fs_repo
    )

    # Mock FileService.draw_mermaid to return None (service unavailable)
    mocker.patch("codemie.service.file_service.mermaid_service.MermaidService.draw_mermaid", return_value=None)

    response = client.post("/v1/files/diagram/mermaid", json={"code": mock_mermaid_code}, headers=auth_headers)

    # Assertions
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {
        'error': {
            'details': "Mermaid service is not available or returned an error.",
            'help': 'Try again later or check your Mermaid syntax for errors.',
            'message': 'Unable to generate Mermaid diagram',
        }
    }

    # Verify read_file was called with the correct parameters
    mock_fs_repo.read_file.assert_called_once_with(expected_filename, owner="test_user")


def test_create_mermaid_diagram_unauthenticated():
    """Test that unauthenticated requests are rejected."""
    response = client.post("/v1/files/diagram/mermaid", json={"code": "graph TD; A-->B;"})

    # Assertions - should return 401 or 403 depending on auth implementation
    assert response.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]


def test_create_mermaid_diagram_invalid_content_type(mocker, auth_headers):
    """Test invalid content_type returns 422 due to FastAPI validation."""
    mock_fs_repo = mocker.Mock()
    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository", return_value=mock_fs_repo
    )

    response = client.post(
        "/v1/files/diagram/mermaid?content_type=pdf",
        json={"code": "graph TD; A-->B;"},
        headers=auth_headers,
    )
    assert response.status_code == 422
    # Optionally, check the validation error details
    data = response.json()
    assert data["detail"][0]["msg"] == "Input should be 'png' or 'svg'"
    assert data["detail"][0]["loc"][-1] == "content_type"


def test_create_mermaid_diagram_invalid_response_type(mocker, auth_headers):
    """Test invalid response_type returns 422."""
    mock_fs_repo = mocker.Mock()
    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository", return_value=mock_fs_repo
    )

    response = client.post(
        "/v1/files/diagram/mermaid?response_type=invalid",
        json={"code": "graph TD; A-->B;"},
        headers=auth_headers,
    )
    assert response.status_code == 422
    # Optionally, check the validation error details
    data = response.json()
    assert data["detail"][0]["msg"] == "Input should be 'file' or 'raw'"
    assert data["detail"][0]["loc"][-1] == "response_type"


def test_create_mermaid_diagram_raw_svg(mocker, auth_headers):
    """Test response_type=raw returns SVG content directly."""
    mock_mermaid_code = "graph TD; A-->B;"
    mock_svg_content = b"<svg>Raw SVG</svg>"

    mocker.patch(
        "codemie.service.file_service.mermaid_service.MermaidService.draw_mermaid",
        return_value=mock_svg_content,
    )
    mock_fs_repo = mocker.Mock()
    mock_fs_repo.read_file.side_effect = FileNotFoundError("File not found")
    mock_fs_repo.write_file.return_value = mocker.Mock()
    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository", return_value=mock_fs_repo
    )

    response = client.post(
        "/v1/files/diagram/mermaid?response_type=raw",
        json={"code": mock_mermaid_code},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(MermaidMimeType.SVG)
    assert response.content == mock_svg_content


def test_create_mermaid_diagram_raw_png(mocker, auth_headers):
    """Test response_type=raw returns PNG content directly."""
    mock_mermaid_code = "graph TD; A-->B;"
    mock_png_content = b"\x89PNG\r\n\x1a\nRawPNG"

    mocker.patch(
        "codemie.service.file_service.mermaid_service.MermaidService.draw_mermaid",
        return_value=mock_png_content,
    )
    mock_fs_repo = mocker.Mock()
    mock_fs_repo.read_file.side_effect = FileNotFoundError("File not found")
    mock_fs_repo.write_file.return_value = mocker.Mock()
    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository", return_value=mock_fs_repo
    )

    response = client.post(
        "/v1/files/diagram/mermaid?response_type=raw&content_type=png",
        json={"code": mock_mermaid_code},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content == mock_png_content


def test_safe_disposition_strips_crlf():
    disp = files._safe_disposition("evil\r\nX-Injected: header")
    assert "\r" not in disp
    assert "\n" not in disp
    assert "attachment" in disp


def test_safe_disposition_escapes_quotes():
    disp = files._safe_disposition('trick"name.svg')
    assert disp == ('attachment; filename="trick\\"name.svg"; filename*=UTF-8\'\'trick%22name.svg')


def test_safe_disposition_empty_filename():
    assert files._safe_disposition("") == "attachment"


def test_safe_disposition_empty_filename_honours_disposition_type():
    assert files._safe_disposition("", "inline") == "inline"


@pytest.mark.parametrize("name", [".env.local", ".eslintrc.json"])
def test_safe_disposition_dotfile_with_extension_keeps_leading_dot(name):
    """A dotfile that also carries an extension must keep its leading dot in the
    ASCII fallback — rpartition leaves the dot inside `base`, so the fallback
    strips spaces only, never dots.
    """
    disp = files._safe_disposition(name)

    assert f'filename="{name}"' in disp
    encoded_part = disp.split("filename*=UTF-8''", 1)[1]
    assert unquote(encoded_part) == name


def test_safe_disposition_dotfile_without_extension_uses_download_placeholder():
    """`.bashrc` has no second dot, so rpartition yields base="" / ext="bashrc"
    and the fallback becomes "download.bashrc". This is intended: the ASCII
    fallback is a suggestion only, and filename* still carries the exact name.
    Do not "fix" this by special-casing leading dots.
    """
    disp = files._safe_disposition(".bashrc")

    assert 'filename="download.bashrc"' in disp
    assert "filename*=UTF-8''.bashrc" in disp


def test_safe_disposition_strips_surrounding_spaces():
    disp = files._safe_disposition("  spaced  .txt")

    assert 'filename="spaced.txt"' in disp


def test_safe_disposition_unicode_filename_round_trips():
    """Non-ASCII filenames must not crash header encoding and must round-trip via filename*."""
    name = "звіт.xlsx"
    disp = files._safe_disposition(name)

    # Starlette encodes headers as latin-1 (response.py: v.encode("latin-1")) —
    # the header must survive that regardless of the original filename's script.
    disp.encode("latin-1")

    assert "filename*=UTF-8''" in disp
    encoded_part = disp.split("filename*=UTF-8''", 1)[1]
    assert unquote(encoded_part) == name

    match = re.search(r'filename="([^"]*)"', disp)
    assert match is not None
    assert match.group(1)  # non-empty ASCII fallback present


def test_safe_disposition_cyrillic_fallback_uses_download_placeholder():
    """When the name has no ASCII letters/digits, the fallback is 'download' plus
    the (ASCII) original extension — the full name still round-trips via filename*.
    """
    name = "звіт.xlsx"
    disp = files._safe_disposition(name)

    assert 'filename="download.xlsx"' in disp
    encoded_part = disp.split("filename*=UTF-8''", 1)[1]
    assert unquote(encoded_part) == name


def test_safe_disposition_non_ascii_no_extension_fallback_is_bare_download():
    name = "отчет"
    disp = files._safe_disposition(name)

    assert 'filename="download"' in disp
    encoded_part = disp.split("filename*=UTF-8''", 1)[1]
    assert unquote(encoded_part) == name


def test_safe_disposition_semicolon_round_trips_and_fallback_is_sanitised():
    """A `\\;` quoted-pair is not reliably unescaped by every parser, so the ASCII
    fallback replaces `;` with `_` instead of escaping it. The exact original
    name (with the real semicolon) is still preserved losslessly in filename*.
    """
    name = "weird;name.txt"
    disp = files._safe_disposition(name)

    encoded_part = disp.split("filename*=UTF-8''", 1)[1]
    assert unquote(encoded_part) == name
    assert 'filename="weird_name.txt"' in disp

    match = re.search(r'filename="([^"]*)"', disp)
    assert match is not None
    assert "\\" not in match.group(1)


def test_safe_disposition_quote_and_backslash_cannot_break_quoted_string():
    name = 'evil\\".txt'  # literal backslash followed by a quote
    disp = files._safe_disposition(name)

    # A quoted-string parser must be able to consume the whole fallback value
    # without hitting an unescaped quote before the real closing quote.
    match = re.match(r'attachment; filename="((?:[^"\\]|\\.)*)"; filename\*=', disp)
    assert match is not None, f"quoted-string was broken out of: {disp}"

    # Backslash is stripped as a path separator, so the round trip is against
    # the name with backslashes removed, not the raw original.
    encoded_part = disp.split("filename*=UTF-8''", 1)[1]
    assert unquote(encoded_part) == name.replace("\\", "")


def test_get_attachment_response_forces_download():
    content = b"<svg><script>alert(1)</script></svg>"
    resp = files.get_attachment_response(content, "evil.svg")
    assert isinstance(resp, Response)
    assert resp.media_type == "application/octet-stream"
    assert "attachment" in resp.headers.get("content-disposition", "")
    assert 'filename="evil.svg"' in resp.headers.get("content-disposition", "")


def test_get_attachment_response_no_filename():
    resp = files.get_attachment_response(b"data")
    assert resp.headers.get("content-disposition") == "attachment"


def test_get_sanitized_html_response_accepts_filename_kwarg():
    html = "<html><body>Hello</body></html>"
    resp = files.get_sanitized_html_response(html, "page.html")
    assert 'filename="page.html"' in resp.headers.get("content-disposition", "")


def test_get_plain_text_response_accepts_filename_kwarg():
    resp = files.get_plain_text_response("var x = 1;", "script.js")
    assert resp.media_type == "text/plain"


def test_get_plain_text_response_sets_content_disposition():
    resp = files.get_plain_text_response("var x = 1;", "script.js")
    assert resp.headers.get("content-disposition") == ('attachment; filename="script.js"; filename*=UTF-8\'\'script.js')


def test_get_plain_text_response_no_filename_returns_bare_attachment():
    resp = files.get_plain_text_response("data")
    assert resp.headers.get("content-disposition") == "attachment"


def test_check_and_sanitize_content_accepts_filename_kwarg():
    resp = files.check_and_sanitize_content(b"\x89PNG", "img.png")
    assert resp.media_type == "application/octet-stream"


def test_check_and_sanitize_content_js_sets_content_disposition():
    js = b"function test() { alert('XSS'); }"
    resp = files.check_and_sanitize_content(js, "evil.js")
    assert resp.media_type == "text/plain"
    assert resp.headers.get("content-disposition") == ('attachment; filename="evil.js"; filename*=UTF-8\'\'evil.js')


def test_check_and_sanitize_content_script_tag_sets_content_disposition():
    script = b"<script>alert('XSS');</script>"
    resp = files.check_and_sanitize_content(script, "page.html")
    assert resp.media_type == "text/plain"
    assert resp.headers.get("content-disposition") == ('attachment; filename="page.html"; filename*=UTF-8\'\'page.html')


def test_check_and_sanitize_content_html_sets_content_disposition():
    html = b"<html><body><h1>Hi</h1></body></html>"
    resp = files.check_and_sanitize_content(html, "report.html")
    assert resp.media_type == "text/html"
    assert resp.headers.get("content-disposition") == (
        'attachment; filename="report.html"; filename*=UTF-8\'\'report.html'
    )


def test_check_and_sanitize_content_binary_sets_content_disposition():
    binary = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00"
    resp = files.check_and_sanitize_content(binary, "image.png")
    assert resp.media_type == "application/octet-stream"
    assert resp.headers.get("content-disposition") == ('attachment; filename="image.png"; filename*=UTF-8\'\'image.png')


def test_check_and_sanitize_content_binary_no_filename_bare_attachment():
    binary = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00"
    resp = files.check_and_sanitize_content(binary)
    assert resp.media_type == "application/octet-stream"
    assert resp.headers.get("content-disposition") == "attachment"


def test_strip_uuid_prefix_removes_leading_uuid():
    name = "a1b2c3d4-e5f6-7890-abcd-ef1234567890_report.xlsx"
    assert files._strip_uuid_prefix(name) == "report.xlsx"


def test_strip_uuid_prefix_no_uuid_unchanged():
    assert files._strip_uuid_prefix("report.xlsx") == "report.xlsx"


def test_strip_uuid_prefix_uuid_in_middle_unchanged():
    name = "prefix_a1b2c3d4-e5f6-7890-abcd-ef1234567890_suffix.csv"
    assert files._strip_uuid_prefix(name) == name


def test_strip_uuid_prefix_empty_string():
    assert files._strip_uuid_prefix("") == ""


@pytest.mark.anyio
async def test_read_file_uuid_prefix_stripped_from_content_disposition(mocker, authenticated_user):
    """UUID-prefixed storage name must be stripped before appearing in Content-Disposition."""
    uuid_name = "a1b2c3d4-e5f6-7890-abcd-ef1234567890_report.xlsx"
    _setup_read_file_mock(mocker, b"PK fake xlsx bytes", "application/octet-stream", uuid_name)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(f"/v1/files/{uuid_name}")

    assert response.status_code == 200
    disposition = response.headers.get("content-disposition", "")
    assert (
        'filename="report.xlsx"' in disposition
    ), f"Expected clean filename in Content-Disposition but got: {disposition}"
    assert "a1b2c3d4" not in disposition, "UUID prefix must not appear in Content-Disposition"


@pytest.mark.anyio
async def test_read_file_plain_text_has_content_disposition(mocker, authenticated_user):
    """text/plain files must set Content-Disposition with the stripped filename."""
    uuid_name = "b2c3d4e5-f6a7-8901-bcde-f12345678901_notes.txt"
    _setup_read_file_mock(mocker, b"Hello world", "text/plain", uuid_name)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(f"/v1/files/{uuid_name}")

    assert response.status_code == 200
    disposition = response.headers.get("content-disposition", "")
    assert 'filename="notes.txt"' in disposition


@pytest.mark.anyio
async def test_read_file_uuid_only_name_keeps_raw_name(mocker, authenticated_user):
    """When stripping the UUID prefix leaves nothing, fall back to the raw stored name."""
    uuid_only_name = "a1b2c3d4-e5f6-7890-abcd-ef1234567890_"
    _setup_read_file_mock(mocker, b"data", "application/octet-stream", uuid_only_name)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(f"/v1/files/{uuid_only_name}")

    assert response.status_code == 200
    disposition = response.headers.get("content-disposition", "")
    assert f'filename="{uuid_only_name}"' in disposition


@pytest.mark.anyio
async def test_read_file_cyrillic_filename_does_not_raise(mocker, authenticated_user):
    """Non-ASCII filenames must not raise UnicodeEncodeError inside Response()."""
    cyrillic_name = "звіт.xlsx"
    _setup_read_file_mock(mocker, b"xlsx bytes", "application/octet-stream", cyrillic_name)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(f"/v1/files/{cyrillic_name}")

    assert response.status_code == 200
    disposition = response.headers.get("content-disposition", "")
    assert "filename*=UTF-8''%D0%B7" in disposition


def test_get_sanitized_html_response_forces_download():
    html = """
    <html><body><h1>Hello</h1><script>alert('XSS');</script></body></html>
    """
    resp = files.get_sanitized_html_response(html)
    assert isinstance(resp, Response)
    assert resp.media_type == "text/html"
    assert resp.headers.get("content-disposition") == "attachment"
    assert b"<h1>Hello</h1>" in resp.body
    assert b"<script>" in resp.body


def test_get_plain_text_response_bytes_and_str():
    text = "console.log('test');"
    resp = files.get_plain_text_response(text)
    assert resp.media_type == "text/plain"

    text_bytes = b"console.log('test');"
    resp2 = files.get_plain_text_response(text_bytes)
    assert resp2.media_type == "text/plain"


def test_check_and_sanitize_content_html():
    html = b"<html><body><h1>Hi</h1><script>alert('XSS');</script></body></html>"
    resp = files.check_and_sanitize_content(html)
    assert resp.media_type == "text/html"
    assert resp.headers.get("content-disposition") == "attachment"
    assert b"<h1>Hi</h1>" in resp.body
    assert b"<script>" in resp.body


def test_check_and_sanitize_content_js():
    js = b"function test() { alert('XSS'); }"
    resp = files.check_and_sanitize_content(js)
    assert resp.media_type == "text/plain"
    assert b"function test()" in resp.body


def test_check_and_sanitize_content_script_tag():
    script = b"<script>alert('XSS');</script>"
    resp = files.check_and_sanitize_content(script)
    assert resp.media_type == "text/plain"
    assert b"<script>" in resp.body


def test_check_and_sanitize_content_binary():
    binary = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00"
    resp = files.check_and_sanitize_content(binary)
    assert resp.media_type == "application/octet-stream"
    assert resp.body == binary


def test_check_and_sanitize_content_str_html():
    html = "<html><body>Test</body></html>"
    resp = files.check_and_sanitize_content(html)
    assert resp.media_type == "text/html"


def test_check_and_sanitize_content_str_js():
    js = "var x = 1;"
    resp = files.check_and_sanitize_content(js)
    assert resp.media_type == "text/plain"


def test_check_and_sanitize_content_str_binary():
    data = "\u0000\u0001\u0002"
    resp = files.check_and_sanitize_content(data)
    assert resp.media_type == "application/octet-stream"


SVG_XSS_PAYLOAD = (
    b'<?xml version="1.0" standalone="no"?>'
    b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">'
    b'<script type="application/ecmascript">'
    b'alert("XSS in " + window.location.origin + " :: cookies=" + document.cookie);'
    b'</script><rect width="200" height="200" fill="red"/></svg>'
)

XHTML_XSS_PAYLOAD = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN"'
    b' "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">'
    b'<html xmlns="http://www.w3.org/1999/xhtml">'
    b'<head><title>XSS</title></head>'
    b'<body><script type="text/javascript">'
    b'alert("XSS in " + window.location.origin + " :: cookies=" + document.cookie);'
    b'</script></body></html>'
)


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
    # Patch path is files.py because the new handler calls FileObject.from_encoded_url directly.
    mock_fo = mocker.Mock()
    mock_fo.owner = owner
    mock_fo.name = name
    mock_fo.mime_type = mime_type
    mocker.patch(
        "codemie.rest_api.routers.files.FileObject.from_encoded_url",
        return_value=mock_fo,
    )
    return mock_file_object


@pytest.mark.anyio
async def test_read_file_svg_xss_payload_is_forced_attachment(mocker, authenticated_user):
    """Reproduces the stored XSS: SVG with embedded script must be served as attachment.

    SVG keeps image/svg+xml so <img> tags can still render it (browsers ignore
    Content-Disposition for sub-resource fetches), but Content-Disposition: attachment
    and CSP sandbox block top-level document execution.
    """
    _setup_read_file_mock(mocker, SVG_XSS_PAYLOAD, "image/svg+xml", "evil.svg")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/evil.svg")

    assert response.status_code == 200
    disposition = response.headers.get("content-disposition", "")
    assert "attachment" in disposition, "XSS REPRODUCED: SVG served without Content-Disposition: attachment"
    assert response.headers.get("content-type", "").startswith(
        "image/svg+xml"
    ), "SVG should keep image/svg+xml so <img> tags render it"
    csp = response.headers.get("content-security-policy", "")
    assert (
        "default-src 'none'" in csp and "sandbox" in csp
    ), "SVG response missing CSP sandbox — defense-in-depth against script execution"


@pytest.mark.anyio
async def test_read_file_xml_forces_attachment(mocker, authenticated_user):
    _setup_read_file_mock(mocker, b"<root><item>data</item></root>", "text/xml", "data.xml")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/data.xml")

    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "")


@pytest.mark.anyio
async def test_read_file_xhtml_forces_attachment(mocker, authenticated_user):
    _setup_read_file_mock(mocker, XHTML_XSS_PAYLOAD, "application/xhtml+xml", "page.xhtml")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/page.xhtml")

    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "")


@pytest.mark.anyio
async def test_read_file_rss_forces_attachment(mocker, authenticated_user):
    _setup_read_file_mock(mocker, b"<rss version='2.0'/>", "application/rss+xml", "feed.rss")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/feed.rss")

    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "")


@pytest.mark.anyio
async def test_read_file_svg_mixed_case_mime_forces_attachment(mocker, authenticated_user):
    """Legacy blobs stored with non-canonical MIME casing must still be forced to download."""
    _setup_read_file_mock(mocker, SVG_XSS_PAYLOAD, "Image/SVG+XML", "evil.svg")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/evil.svg")

    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "")


@pytest.mark.anyio
async def test_read_file_svg_mime_with_params_forces_attachment(mocker, authenticated_user):
    """MIME with charset parameter must still be normalised and forced to download."""
    _setup_read_file_mock(mocker, SVG_XSS_PAYLOAD, "image/svg+xml; charset=utf-8", "evil.svg")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/evil.svg")

    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "")


@pytest.mark.anyio
async def test_read_file_svg_preserves_nosniff_header(mocker, authenticated_user):
    """X-Content-Type-Options: nosniff must be present even on attachment responses."""
    _setup_read_file_mock(mocker, SVG_XSS_PAYLOAD, "image/svg+xml", "evil.svg")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/evil.svg")

    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") == "nosniff"


@pytest.mark.anyio
async def test_read_file_svg_filename_in_disposition(mocker, authenticated_user):
    """Attachment Content-Disposition must include the original filename."""
    _setup_read_file_mock(mocker, SVG_XSS_PAYLOAD, "image/svg+xml", "payload.svg")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/payload.svg")

    assert response.status_code == 200
    assert 'filename="payload.svg"' in response.headers.get("content-disposition", "")


@pytest.mark.anyio
async def test_read_file_unknown_mime_is_forced_attachment(mocker, authenticated_user):
    """Unknown MIME types must never render inline — default-deny forces download."""
    _setup_read_file_mock(mocker, b"some data", "application/x-custom-format", "report.bin")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/report.bin")

    assert response.status_code == 200
    assert "attachment" in response.headers.get(
        "content-disposition", ""
    ), "Unknown MIME type served inline — default-deny fallback missing"


@pytest.mark.anyio
async def test_read_file_raster_image_serves_inline(mocker, authenticated_user):
    """Raster images (png, jpeg) must still render inline — they are safe."""
    _setup_read_file_mock(mocker, b"\x89PNG\r\n\x1a\n", "image/png", "photo.png")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/photo.png")

    assert response.status_code == 200
    disposition = response.headers.get("content-disposition", "")
    assert "attachment" not in disposition
    assert disposition.startswith("inline;")
    assert 'filename="photo.png"' in disposition
    assert "filename*=UTF-8''photo.png" in disposition
    assert response.headers.get("content-type", "").startswith("image/png")


@pytest.mark.anyio
async def test_read_file_pdf_serves_inline(mocker, authenticated_user):
    """PDF must still render inline."""
    _setup_read_file_mock(mocker, b"%PDF-1.4", "application/pdf", "doc.pdf")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/doc.pdf")

    assert response.status_code == 200
    disposition = response.headers.get("content-disposition", "")
    assert "attachment" not in disposition
    assert disposition.startswith("inline;")
    assert 'filename="doc.pdf"' in disposition
    assert "filename*=UTF-8''doc.pdf" in disposition


@pytest.mark.anyio
async def test_read_file_inline_cyrillic_filename_encodes_cleanly(mocker, authenticated_user):
    """Inline responses go through the same latin-1 header encoding as attachments —
    a non-ASCII name must not raise and must round-trip via filename*.
    """
    cyrillic_name = "світлина.png"
    _setup_read_file_mock(mocker, b"\x89PNG\r\n\x1a\n", "image/png", cyrillic_name)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(f"/v1/files/{cyrillic_name}")

    assert response.status_code == 200
    disposition = response.headers.get("content-disposition", "")
    assert disposition.startswith("inline;")
    assert 'filename="download.png"' in disposition
    encoded_part = disposition.split("filename*=UTF-8''", 1)[1]
    assert unquote(encoded_part) == cyrillic_name


@pytest.mark.anyio
async def test_write_file_derives_mime_from_extension_not_client(authenticated_user, auth_headers, mocker):
    """Upload with spoofed MIME — server must derive MIME from filename extension, ignoring client."""
    mock_file_object = mocker.Mock()
    mock_file_object.to_encoded_url.return_value = "url"

    mock_fs_repo = mocker.Mock()
    mock_fs_repo.write_file.return_value = mock_file_object

    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository",
        return_value=mock_fs_repo,
    )
    mocker.patch("codemie.rest_api.routers.files.MarkdownCacheService")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/files/",
            files={"file": ("test.svg", SVG_XSS_PAYLOAD, "image/png")},  # client lies: says PNG
            headers=auth_headers,
        )

    assert response.status_code == 200
    _, kwargs = mock_fs_repo.write_file.call_args
    assert kwargs["mime_type"] == "image/svg+xml", (
        f"Expected server-derived 'image/svg+xml' but got '{kwargs['mime_type']}' — "
        "client-supplied MIME is being trusted"
    )


@pytest.mark.anyio
async def test_write_files_bulk_derives_mime_from_extension_not_client(authenticated_user, auth_headers, mocker):
    mock_file_object = mocker.Mock()
    mock_file_object.to_encoded_url.return_value = "url"

    mock_fs_repo = mocker.Mock()
    mock_fs_repo.write_file.return_value = mock_file_object

    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository",
        return_value=mock_fs_repo,
    )
    mocker.patch("codemie.rest_api.routers.files.MarkdownCacheService")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/files/bulk",
            files=[("files", ("attack.svg", SVG_XSS_PAYLOAD, "image/png"))],
            headers=auth_headers,
        )

    assert response.status_code == 200
    _, kwargs = mock_fs_repo.write_file.call_args
    assert kwargs["mime_type"] == "image/svg+xml"


@pytest.mark.anyio
async def test_write_file_none_filename_falls_back_to_octet_stream(authenticated_user, auth_headers, mocker):
    """filename=None must not crash — falls back to application/octet-stream."""
    mock_file_object = mocker.Mock()
    mock_file_object.to_encoded_url.return_value = "url"

    mock_fs_repo = mocker.Mock()
    mock_fs_repo.write_file.return_value = mock_file_object

    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository",
        return_value=mock_fs_repo,
    )

    mock_cache_svc = mocker.Mock()
    mocker.patch("codemie.rest_api.routers.files.MarkdownCacheService", return_value=mock_cache_svc)

    mock_upload = mocker.Mock()
    mock_upload.filename = None
    mock_upload.size = 5
    mock_upload.file.read.return_value = b"data"
    mock_upload.content_type = "application/octet-stream"

    mock_user = mocker.Mock()
    mock_user.id = "test_user"

    from codemie.rest_api.routers.files import write_file

    write_file(file=mock_upload, user=mock_user)

    _, kwargs = mock_fs_repo.write_file.call_args
    assert kwargs["mime_type"] == "application/octet-stream"


@pytest.mark.anyio
async def test_write_file_calls_cache_invalidate(authenticated_user, auth_headers, mocker):
    """write_file invalidates the markdown cache before writing the new file."""
    mock_file_object = mocker.Mock()
    mock_file_object.to_encoded_url.return_value = "url"

    mock_fs_repo = mocker.Mock()
    mock_fs_repo.write_file.return_value = mock_file_object

    mock_cache_svc = mocker.Mock()
    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository", return_value=mock_fs_repo
    )
    mocker.patch("codemie.rest_api.routers.files.MarkdownCacheService", return_value=mock_cache_svc)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/files/", files={"file": ("report.pdf", b"%PDF-fake", "application/pdf")}, headers=auth_headers
        )

    assert response.status_code == status.HTTP_200_OK
    mock_cache_svc.invalidate.assert_called_once_with(owner="test_user", filename="report.pdf", repo=mock_fs_repo)


@pytest.mark.anyio
async def test_write_files_bulk_calls_cache_invalidate_per_file(authenticated_user, auth_headers, mocker):
    """write_files_bulk invalidates cache for each uploaded file."""
    mock_obj_1 = mocker.Mock()
    mock_obj_1.to_encoded_url.return_value = "url1"
    mock_obj_2 = mocker.Mock()
    mock_obj_2.to_encoded_url.return_value = "url2"

    mock_fs_repo = mocker.Mock()
    mock_fs_repo.write_file.side_effect = [mock_obj_1, mock_obj_2]

    mock_cache_svc = mocker.Mock()
    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository", return_value=mock_fs_repo
    )
    mocker.patch("codemie.rest_api.routers.files.MarkdownCacheService", return_value=mock_cache_svc)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/files/bulk",
            files=[
                ("files", ("a.pdf", b"data1", "application/pdf")),
                ("files", ("b.html", b"data2", "text/html")),
            ],
            headers=auth_headers,
        )

    assert response.status_code == status.HTTP_200_OK
    assert mock_cache_svc.invalidate.call_count == 2
    calls = mock_cache_svc.invalidate.call_args_list
    filenames = {c.kwargs["filename"] for c in calls}
    assert filenames == {"a.pdf", "b.html"}
    assert all(c.kwargs["repo"] is mock_fs_repo for c in calls)


@pytest.mark.anyio
async def test_write_files_bulk_too_many_files(authenticated_user, auth_headers, mocker):
    mocker.patch("codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/files/bulk",
            files=[("files", (f"file_{i}.txt", b"content", "text/plain")) for i in range(21)],
            headers=auth_headers,
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    body = response.json()
    assert "Too many files" in body["error"]["message"]


def test_find_by_blob_returns_matching_record(mocker):
    repo = AgentWorkspaceRepository()
    mock_file = mocker.Mock(spec=AgentWorkspaceFile)

    session_mock = mocker.MagicMock()
    session_mock.exec.return_value.first.return_value = mock_file

    mock_session_cls = mocker.patch("codemie.repository.agent_workspace_repository.Session")
    mock_session_cls.return_value.__enter__.return_value = session_mock
    result = repo.find_by_blob("ws-1", "owner-1", "file.txt", "text/plain")

    assert result is mock_file


def test_file_service_load_content_delegates_to_repo(mocker):
    from codemie.service.file_service.file_service import FileService
    from codemie_tools.base.file_object import FileObject

    mock_fo = mocker.Mock(spec=FileObject)
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

    mock_repo.read_file.assert_called_once_with(file_name="report.pdf", owner="user-abc", mime_type="application/pdf")
    assert result is mock_result


def test_find_by_blob_none_mime_omits_mime_condition(mocker):
    repo = AgentWorkspaceRepository()
    session_mock = mocker.MagicMock()
    session_mock.exec.return_value.first.return_value = None

    mock_session_cls = mocker.patch("codemie.repository.agent_workspace_repository.Session")
    mock_session_cls.return_value.__enter__.return_value = session_mock
    result = repo.find_by_blob("ws-1", "owner-1", "file.txt")

    assert result is None
    # Verify that the mime_type filter condition is absent from the WHERE clause when
    # mime_type=None is passed. The compiled statement includes mime_type in the SELECT
    # columns regardless, but only adds "mime_type = :mime_type_1" to WHERE when a
    # non-None mime_type is provided.
    stmt_str = str(session_mock.exec.call_args[0][0])
    assert "mime_type = :mime_type_1" not in stmt_str, (
        "Expected no mime_type filter in SQL when mime_type=None, but the WHERE clause "
        f"still contains a mime_type condition: {stmt_str}"
    )


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
        "codemie.service.share_conversation_service.ConversationMonitoringService" ".send_share_conversation_metric"
    )

    result = ShareConversationService.get_shared_conversation("tok-abc", User(id="viewer"))

    assert result["share_token"] == "tok-abc"


def _share_service_mocks(mocker, mock_conv):
    """Patch the collaborators of ShareConversationService.get_shared_conversation."""
    mock_shared = mocker.Mock()
    mock_shared.conversation_id = "conv-1"
    mock_shared.shared_by_user_name = "alice"
    mock_shared.created_at = mocker.Mock()
    mock_shared.access_count = 5
    mock_shared.increment_access_count = mocker.Mock()

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
        "codemie.service.share_conversation_service.ConversationMonitoringService" ".send_share_conversation_metric"
    )
    return mock_shared


def _shared_conversation_with_history(mocker, history):
    mock_conv = mocker.Mock()
    mock_conv.id = "conv-1"
    mock_conv.assistant_ids = []
    mock_conv.history = history
    return mock_conv


def test_get_shared_conversation_maps_file_names_to_granted_urls(mocker):
    """Recipients get a separate map of granted URLs; file_names itself stays a bare token."""
    from codemie.core.models import ChatRole
    from codemie.rest_api.models.conversation import GeneratedMessage
    from codemie.service.share_conversation_service import ShareConversationService

    message = GeneratedMessage(role=ChatRole.USER, message="see attachment", file_names=["enc-token-1"])
    mock_conv = _shared_conversation_with_history(mocker, [message])
    _share_service_mocks(mocker, mock_conv)

    result = ShareConversationService.get_shared_conversation("tok-abc", User(id="viewer"))

    assert result["shared_file_urls"] == {"enc-token-1": "enc-token-1?share_token=tok-abc"}
    # The conversation is handed back untouched — this is the contract the API and the
    # sanity harness rely on, and what _process_file_names_to_objects decodes.
    assert result["conversation"].history[0].file_names == ["enc-token-1"]
    assert message.file_names == ["enc-token-1"]


def test_get_shared_conversation_maps_inline_sandbox_urls(mocker):
    """Tokens referenced only by an inline sandbox URL are in the map too."""
    from codemie.core.models import ChatRole
    from codemie.rest_api.models.conversation import GeneratedMessage
    from codemie.service.share_conversation_service import ShareConversationService

    original_text = "here it is ![shot](sandbox:/v1/files/enc-token-2) done"
    message = GeneratedMessage(role=ChatRole.ASSISTANT, message=original_text)
    mock_conv = _shared_conversation_with_history(mocker, [message])
    _share_service_mocks(mocker, mock_conv)

    result = ShareConversationService.get_shared_conversation("tok-abc", User(id="viewer"))

    assert result["shared_file_urls"] == {"enc-token-2": "enc-token-2?share_token=tok-abc"}
    # Message text is not rewritten either.
    assert result["conversation"].history[0].message == original_text
    assert message.message == original_text


def test_get_shared_conversation_returns_empty_map_without_files(mocker):
    """A conversation with no file references produces an empty map, not a missing key."""
    from codemie.core.models import ChatRole
    from codemie.rest_api.models.conversation import GeneratedMessage
    from codemie.service.share_conversation_service import ShareConversationService

    message = GeneratedMessage(role=ChatRole.ASSISTANT, message="plain answer")
    mock_conv = _shared_conversation_with_history(mocker, [message])
    _share_service_mocks(mocker, mock_conv)

    result = ShareConversationService.get_shared_conversation("tok-abc", User(id="viewer"))

    assert result["shared_file_urls"] == {}


def test_find_by_blob_scopes_the_query_to_workspace_owner_and_name(mocker):
    """The three mandatory predicates must be in the statement: they are Rule E's whole scoping."""
    from codemie.repository.agent_workspace_repository import AgentWorkspaceRepository
    from codemie.rest_api.models.agent_workspace import AgentWorkspaceFile

    session_mock = mocker.MagicMock()
    mocker.patch(
        "codemie.repository.agent_workspace_repository.Session"
    ).return_value.__enter__.return_value = session_mock

    AgentWorkspaceRepository().find_by_blob("ws-1", "owner-1", "blob-1", "image/png")

    statement = str(session_mock.exec.call_args.args[0])
    for column in ("workspace_id", "blob_owner", "blob_name", "mime_type"):
        assert f"{AgentWorkspaceFile.__tablename__}.{column}" in statement, column


# Test #16: 404 — a name that escapes the owner's directory is never served
@pytest.mark.anyio
async def test_read_file_rejects_name_escaping_the_owner_directory(mocker, authenticated_user):
    """Rule A keys on owner, but the filesystem backend joins owner and name — deny the traversal."""
    _setup_read_file_mock(
        mocker, b"secret", "application/pdf", "../victim-user/secret.pdf", owner=authenticated_user.id
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_token")
    assert response.status_code == status.HTTP_404_NOT_FOUND


# Test #17: 404 — the same guard applies to a traversal hidden in the owner field
@pytest.mark.anyio
async def test_read_file_rejects_owner_escaping_the_storage_root(mocker, authenticated_user):
    _setup_read_file_mock(mocker, b"secret", "text/plain", "secret.txt", owner="../etc")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_token")
    assert response.status_code == status.HTTP_404_NOT_FOUND


# Test #18: 404 — an authorization backend failure denies instead of surfacing a 500
@pytest.mark.anyio
async def test_read_file_authorization_backend_failure_denies(mocker, authenticated_user):
    """A share lookup blowing up must not become a 500: that would signal the blob exists."""
    _setup_read_file_mock(mocker, b"data", "text/plain", "file.txt", owner="someone-else")
    mocker.patch(
        "codemie.service.file_service.download_rules.SharedConversation.get_by_fields",
        side_effect=RuntimeError("elasticsearch is down"),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_token?share_token=tok-abc")
    assert response.status_code == status.HTTP_404_NOT_FOUND


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


def _setup_workflow_schema_mock(mocker, bucket_name, *, workflow_found=True, can_read=True):
    """Arrange a workflows/{id}.svg blob owned by the storage bucket plus the Ability outcome."""
    from codemie.configs import config as _config

    mocker.patch.object(_config, "CODEMIE_STORAGE_BUCKET_NAME", bucket_name)
    _setup_read_file_mock(mocker, b"<svg/>", "image/svg+xml", "workflows/abc-123.svg", owner=bucket_name)
    mocker.patch(
        "codemie.service.file_service.download_rules.WorkflowConfig.find_by_id",
        return_value=mocker.Mock() if workflow_found else None,
    )
    mocker.patch("codemie.service.file_service.download_rules.Ability.can", return_value=can_read)


# Test #5: 200 — workflow schema readable by a user who may read the workflow (Rule B)
@pytest.mark.anyio
async def test_read_file_workflow_svg_allowed_when_user_can_read_workflow(mocker, authenticated_user):
    """A workflow schema SVG is served when Ability grants READ on the owning workflow (Rule B)."""
    _setup_workflow_schema_mock(mocker, "test-bucket")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_token")
    assert response.status_code == status.HTTP_200_OK


# Test #5a: 404 — workflow schema denied when the user may not read the workflow (Rule B)
@pytest.mark.anyio
async def test_read_file_workflow_svg_denied_when_user_cannot_read_workflow(mocker, authenticated_user):
    """Rule B no longer grants every authenticated user: Ability must allow READ on the workflow."""
    _setup_workflow_schema_mock(mocker, "test-bucket", can_read=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_token")
    assert response.status_code == status.HTTP_404_NOT_FOUND


# Test #5b: 404 — workflow schema for a workflow that no longer exists (Rule B fails closed)
@pytest.mark.anyio
async def test_read_file_workflow_svg_denied_when_workflow_missing(mocker, authenticated_user):
    """A schema blob whose workflow row is gone is denied rather than served."""
    _setup_workflow_schema_mock(mocker, "test-bucket", workflow_found=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_token")
    assert response.status_code == status.HTTP_404_NOT_FOUND


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


# Test #7: 200 — legacy MCP image for any authenticated user (Rule C)
@pytest.mark.anyio
async def test_read_file_mcp_image_allowed_for_authenticated_user(mocker, authenticated_user):
    """Legacy screenshots under the shared MCP namespace stay readable so old chats keep rendering.

    New screenshots are written under the invoking user's id and go through Rule A instead.
    """
    from codemie.repository.repository_factory import MCP_IMAGES_SUBDIR

    _setup_read_file_mock(mocker, b"\x89PNG\r\n", "image/png", "abc123.png", owner=MCP_IMAGES_SUBDIR)
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
        mocker,
        b"generated code",
        "text/plain",
        "output.py",
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
        "codemie.service.file_service.download_rules.SharedConversation.get_by_fields",
        return_value=mock_shared,
    )
    mocker.patch(
        "codemie.service.file_service.download_rules.Conversation.find_by_id",
        return_value=mock_conv,
    )

    mock_file_result = mocker.Mock()
    mock_file_result.content = b"pdf bytes"
    mock_file_result.mime_type = "application/pdf"
    mock_file_result.name = "attachment.pdf"
    mock_repo = mocker.Mock()
    mock_repo.read_file.return_value = mock_file_result
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


# Test #10: 200 — share grant via find_by_blob (Rule E), AI-generated file
@pytest.mark.anyio
async def test_read_file_share_grant_via_find_by_blob_reference_written(mocker, authenticated_user):
    """Share recipient can access AI-generated file registered by reference (blob_owner = sharer user id)."""
    mock_fo = mocker.Mock()
    mock_fo.owner = "alice_user_id"
    mock_fo.name = "diagram.png"
    mock_fo.mime_type = "image/png"
    mocker.patch(
        "codemie.rest_api.routers.files.FileObject.from_encoded_url",
        return_value=mock_fo,
    )

    mock_conv = mocker.Mock()
    mock_conv.history = []  # no file_names match -> falls through to find_by_blob

    mock_shared = mocker.Mock()
    mock_shared.conversation_id = "conv-1"
    mock_shared.shared_by_user_id = "alice_user_id"

    mock_workspace = mocker.Mock()
    mock_workspace.id = "ws-1"

    mocker.patch(
        "codemie.service.file_service.download_rules.SharedConversation.get_by_fields",
        return_value=mock_shared,
    )
    mocker.patch(
        "codemie.service.file_service.download_rules.Conversation.find_by_id",
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

    mock_file_result = mocker.Mock()
    mock_file_result.content = b"png bytes"
    mock_file_result.mime_type = "image/png"
    mock_file_result.name = "diagram.png"
    mock_repo = mocker.Mock()
    mock_repo.read_file.return_value = mock_file_result
    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository",
        return_value=mock_repo,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/some_token?share_token=my-share-tok")
    assert response.status_code == status.HTTP_200_OK


# Test #11: 200 — workspace blob where Rule D misses but Rule E hits
@pytest.mark.anyio
async def test_read_file_workspace_blob_rule_d_miss_rule_e_hit(mocker, authenticated_user):
    """Non-owner of workspace can still access workspace blob if they hold a share token (Rule D miss -> Rule E hit)."""
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
        "codemie.service.file_service.download_rules.SharedConversation.get_by_fields",
        return_value=mock_shared,
    )
    mocker.patch(
        "codemie.service.file_service.download_rules.Conversation.find_by_id",
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

    mock_file_result = mocker.Mock()
    mock_file_result.content = b"code"
    mock_file_result.mime_type = "text/x-python"
    mock_file_result.name = "output.py"
    mock_repo = mocker.Mock()
    mock_repo.read_file.return_value = mock_file_result
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
