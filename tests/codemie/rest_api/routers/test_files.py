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

from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI, Response, status
from fastapi.testclient import TestClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import extended_http_exception_handler
from codemie.rest_api.routers import files
from codemie.rest_api.security.user import User
from codemie.rest_api.routers.files import router
import hashlib

app = FastAPI()
app.include_router(router)
app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)

client = TestClient(app, base_url="http://testserver")


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def authenticated_user(monkeypatch):
    def mock_authenticate():
        return User(id='test_user')

    monkeypatch.setattr('codemie.rest_api.security.authentication.authenticate', mock_authenticate)
    return mock_authenticate


@pytest.fixture
def auth_headers(authenticated_user):
    return {"user-id": authenticated_user().id}


@pytest.mark.anyio
async def test_read_file_success(mocker):
    mock_file_content = b"file content"
    mock_file_object = mocker.Mock()
    mock_file_object.content = mock_file_content
    mock_file_object.mime_type = "text/plain"

    mock_fs_repo = mocker.Mock()
    mock_fs_repo.read_file.return_value = mock_file_object

    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository", return_value=mock_fs_repo
    )
    mocker.patch(
        "codemie.repository.base_file_repository.FileObject.from_encoded_url",
        return_value=mocker.Mock(name="test.txt", owner="user"),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as ac:
        response = await ac.get("/v1/files/test.txt")

    assert response.status_code == status.HTTP_200_OK
    assert response.content == mock_file_content


@pytest.mark.anyio
async def test_read_file_not_found(mocker):
    mock_fs_repo = mocker.Mock()
    mock_fs_repo.read_file.side_effect = FileNotFoundError

    mocker.patch(
        "codemie.rest_api.routers.files.FileRepositoryFactory.get_current_repository", return_value=mock_fs_repo
    )
    mocker.patch(
        "codemie.repository.base_file_repository.FileObject.from_encoded_url",
        return_value=mocker.Mock(name="nonexistent.txt", owner="user"),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/files/nonexistent.txt")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {
        'error': {
            'details': "The requested file 'nonexistent.txt' could not be found.",
            'help': 'Please verify the file name and try again. If you believe this is an error, contact support.',
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


def test_get_sanitized_html_response_removes_script():
    html = """
    <html><body><h1>Hello</h1><script>alert('XSS');</script></body></html>
    """
    resp = files.get_sanitized_html_response(html)
    assert isinstance(resp, Response)
    assert resp.media_type == "text/html"


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
    assert b"<script>" not in resp.body


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
