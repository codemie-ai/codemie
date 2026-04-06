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

import contextlib
import hashlib

from fastapi import APIRouter, Response, UploadFile, Depends, Request, File
from typing import Any, List
from starlette import status

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.constants import MermaidContentType, MermaidResponseType, MermaidMimeType
from codemie.repository.repository_factory import FileRepositoryFactory
from codemie.rest_api.models.files import WriteFileResponse, MermaidRequest, BulkWriteFileResponse
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.file_service.file_service import FileService
from codemie.service.file_service.mermaid_service import MermaidService

router = APIRouter(
    tags=["File Operations"],
    prefix="/v1",
    dependencies=[],
)


def get_sanitized_html_response(content) -> Response:
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="backslashreplace")

    return Response(
        content=content,
        media_type="text/html",
        headers={"Content-Disposition": "attachment"},
    )


def get_plain_text_response(content) -> Response:
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="backslashreplace")

    return Response(content=content, media_type="text/plain")


def check_and_sanitize_content(content):
    """
    Checks binary content that has an octet-stream MIME type to see if it might
    contain executable HTML/JavaScript and handles it safely.
    """
    if isinstance(content, bytes):
        try:
            sample = content[:4000].decode("utf-8", errors="backslashreplace").lower()
        except Exception:
            # If we can't decode it, it's probably genuinely binary
            sample = ""
    else:
        # If it's already a string, just take the first 4000 chars
        sample = content[:4000].lower()

    if sample:
        js_indicators = ["javascript:", "function(", "var ", "let ", "const ", "=>", "document.", "() {"]
        html_indicators = ["<!doctype html", "<html", "<body", "<head"]
        script_indicators = ["<script", "<iframe"]

        has_js = any(indicator in sample for indicator in js_indicators)
        has_html = any(tag in sample for tag in html_indicators)

        if has_js and not has_html:
            return get_plain_text_response(content)

        if has_html:
            return get_sanitized_html_response(content)

        has_script = any(tag in sample for tag in script_indicators)
        if has_script:
            return get_plain_text_response(content)

    return Response(content=content, media_type="application/octet-stream")


READ_FILE_MIME_TYPE_HANDLERS = {
    "text/html": get_sanitized_html_response,
    "application/javascript": get_plain_text_response,
    "text/javascript": get_plain_text_response,
    "application/x-javascript": get_plain_text_response,
    "application/x-typescript": get_plain_text_response,
    "application/octet-stream": check_and_sanitize_content,
}


@router.get(
    "/files/{file_name}",
    responses={
        status.HTTP_200_OK: {"description": "File content returned successfully"},
        status.HTTP_404_NOT_FOUND: {"description": "File not found"},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)
def read_file(file_name: str) -> Any:
    """
    Reads a file from the given file name and returns its content.
    The endpoint is designed to handle images and PDF files, adjusting the response's content type accordingly.

    - file_name: str - The name of the file to be read.
    """
    try:
        file_object = FileService.get_file_object(file_name)

        handler = READ_FILE_MIME_TYPE_HANDLERS.get(file_object.mime_type)
        if handler:
            response = handler(file_object.content)
        else:
            response = Response(content=file_object.content, media_type=file_object.mime_type)

        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except FileNotFoundError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="File not found",
            details=f"The requested file '{file_name}' could not be found.",
            help="Please verify the file name and try again. If you believe this is an error, contact support.",
        ) from e
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Internal server error",
            details=f"An unexpected error occurred while trying to read the file '{file_name}': {str(e)}",
            help="Please try again later. If the problem persists, contact the system administrator.",
        ) from e


@router.post("/files/", dependencies=[Depends(authenticate)])
def write_file(file: UploadFile, user: User = Depends(authenticate)):
    """
    Writes a file to the repository.

    - file: UploadFile - The file to be written to the repository.
    """
    if config.FILES_STORAGE_MAX_UPLOAD_SIZE and file.size > config.FILES_STORAGE_MAX_UPLOAD_SIZE:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid file",
            details=f"The uploaded file '{file.filename}' is invalid.",
            help="Please verify the file and try again. If you believe this is an error, contact support.",
        ) from None

    data = file.file.read()

    fs_repo = FileRepositoryFactory().get_current_repository()

    result = fs_repo.write_file(name=file.filename, mime_type=file.content_type, owner=user.id, content=data)

    return WriteFileResponse(file_url=result.to_encoded_url())


@router.post("/files/bulk", dependencies=[Depends(authenticate)], response_model=BulkWriteFileResponse)
def write_files_bulk(files: List[UploadFile] = File(...), user: User = Depends(authenticate)):
    """
    Writes multiple files to the repository in a single request.

    - files: List[UploadFile] - The list of files to be written to the repository.
    """
    if not files:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="No files provided",
            details="No files were provided for upload.",
            help="Please provide at least one file to upload.",
        ) from None

    fs_repo = FileRepositoryFactory().get_current_repository()
    successful_files = []
    failed_files = {}

    # No total size limit check anymore, only individual file size checks are performed in the loop below

    for file in files:
        try:
            if config.FILES_STORAGE_MAX_UPLOAD_SIZE and file.size > config.FILES_STORAGE_MAX_UPLOAD_SIZE:
                failed_files[file.filename] = "File size exceeds the maximum allowed size."
                continue

            # Read file data
            data = file.file.read()

            # Write file to repository
            result = fs_repo.write_file(name=file.filename, mime_type=file.content_type, owner=user.id, content=data)

            # Add to successful files list
            successful_files.append(WriteFileResponse(file_url=result.to_encoded_url()))

        except Exception as e:
            failed_files[file.filename] = str(e)

    return BulkWriteFileResponse(files=successful_files, failed_files=failed_files if failed_files else None)


@router.post(
    "/files/diagram/mermaid",
    status_code=status.HTTP_200_OK,
    response_model=WriteFileResponse,
)
def create_mermaid_diagram(
    request: MermaidRequest,
    http_request: Request,
    content_type: MermaidContentType = MermaidContentType.SVG,
    response_type: MermaidResponseType = MermaidResponseType.FILE,
    user: User = Depends(authenticate),
):
    fs_repo = FileRepositoryFactory().get_current_repository()
    code_hash = hashlib.sha256(request.code.encode()).hexdigest()
    filename = f"mermaid_{code_hash}.{content_type.value}"

    # Try to read the file from the repository
    result = None
    mime_type = MermaidMimeType.SVG.value if content_type == MermaidContentType.SVG else MermaidMimeType.PNG.value
    with contextlib.suppress(Exception):
        result = fs_repo.read_file(filename, owner=user.id)

    if result:
        diagram = result.content
    else:
        diagram = generate_diagram_or_raise(request.code, content_type)
        result = fs_repo.write_file(name=filename, mime_type=mime_type, owner=user.id, content=diagram)

    if response_type == MermaidResponseType.RAW:
        return Response(content=diagram, media_type=mime_type)

    encoded_url = result.to_encoded_url()
    protocol = "http" if config.is_local else "https"
    host = http_request.base_url.netloc
    base_url = f"{protocol}://{host}"
    api_path = f"{config.API_ROOT_PATH.strip('/')}/" if config.API_ROOT_PATH else ""
    full_url = f"{base_url}/{api_path}v1/files/{encoded_url}"

    return WriteFileResponse(file_url=full_url)


def generate_diagram_or_raise(mermaid_code: str, content_type: MermaidContentType) -> bytes:
    """
    Generate a Mermaid diagram or raise an ExtendedHTTPException on error.
    """
    try:
        diagram = MermaidService.draw_mermaid(mermaid_code=mermaid_code, type=content_type)
    except Exception as e:
        formatted_exception = str(e).strip()
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Mermaid diagram configuration error",
            details=f"{formatted_exception}",
            help="Please check your Mermaid syntax and try again.",
        ) from e

    if diagram is None:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="Unable to generate Mermaid diagram",
            details="Mermaid service is not available or returned an error.",
            help="Try again later or check your Mermaid syntax for errors.",
        )
    return diagram
