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
import mimetypes
import re

from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter, Response, UploadFile, Depends, Request, File, Query
from typing import Any, List, NoReturn
from starlette import status

from codemie.configs import config
from codemie.configs.logger import logger
from codemie_tools.base.file_object import FileObject, normalise_mime
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import AssistantChatRequest
from codemie.core.constants import MermaidContentType, MermaidResponseType, MermaidMimeType
from codemie.repository.repository_factory import FileRepositoryFactory, MCP_IMAGES_SUBDIR
from codemie.rest_api.models.files import WriteFileResponse, MermaidRequest, BulkWriteFileResponse
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.file_service.file_service import FileService
from codemie.service.file_service.markdown_cache_service import MarkdownCacheService
from codemie.service.file_service.mermaid_service import MermaidService
from codemie.repository.agent_workspace_repository import AgentWorkspaceRepository
from codemie.rest_api.models.conversation import Conversation
from codemie.rest_api.models.share.shared_conversation import SharedConversation
from codemie.service.share_conversation_service import collect_message_file_tokens
from codemie.core.ability import Ability, Action
from codemie.core.workflow_models.workflow_config import WorkflowConfig

router = APIRouter(
    tags=["File Operations"],
    prefix="/v1",
    dependencies=[],
)


_OCTET_STREAM = "application/octet-stream"


def _safe_disposition(filename: str, disposition: str = "attachment") -> str:
    """
    Build a Content-Disposition header with both an ASCII `filename` fallback
    (RFC 6266) and a `filename*` UTF-8 parameter (RFC 5987), since Starlette
    encodes headers as latin-1 and any non-ASCII filename would otherwise raise
    UnicodeEncodeError inside Response(). Backslash and forward slash are
    removed as path separators, quotes are escaped as quoted-pairs, and
    semicolons are replaced with underscores in the ASCII fallback only — the
    exact original name is preserved losslessly in filename*.

    `disposition` selects the disposition type ("attachment" by default,
    "inline" for content that must keep rendering in place).
    """
    if not filename:
        return disposition

    cleaned = "".join(ch for ch in filename if ch.isprintable() and ch not in ("/", "\\"))
    if not cleaned:
        return disposition

    if "." in cleaned:
        base, _, ext = cleaned.rpartition(".")
    else:
        base, ext = cleaned, ""

    ascii_base = base.encode("ascii", "ignore").decode("ascii").strip(" ")
    ascii_ext = ext if ext.isascii() else ""

    name_part = ascii_base if any(ch.isalnum() for ch in ascii_base) else "download"
    ascii_fallback = f"{name_part}.{ascii_ext}" if ascii_ext else name_part
    ascii_fallback = ascii_fallback.replace('"', '\\"').replace(";", "_")

    encoded = quote(cleaned, safe="")
    return f'{disposition}; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


_UUID_PREFIX_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_',
    re.IGNORECASE,
)


def _strip_uuid_prefix(filename: str) -> str:
    return _UUID_PREFIX_RE.sub('', filename)


def get_attachment_response(content, filename: str = "") -> Response:
    return Response(
        content=content,
        media_type=_OCTET_STREAM,
        headers={"Content-Disposition": _safe_disposition(filename)},
    )


def get_safe_svg_response(content, filename: str = "") -> Response:
    # Keep image/svg+xml so <img> tags still render the SVG (browsers ignore
    # Content-Disposition for sub-resource fetches). Content-Disposition: attachment
    # prevents the file from executing as a top-level document. CSP sandbox is a
    # second layer that disables scripts even if a browser somehow renders it inline.
    return Response(
        content=content,
        media_type="image/svg+xml",
        headers={
            "Content-Disposition": _safe_disposition(filename),
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


def get_sanitized_html_response(content, filename: str = "") -> Response:
    disposition = _safe_disposition(filename)
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="backslashreplace")

    return Response(
        content=content,
        media_type="text/html",
        headers={"Content-Disposition": disposition},
    )


def get_plain_text_response(content, filename: str = "") -> Response:
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="backslashreplace")

    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": _safe_disposition(filename)},
    )


def check_and_sanitize_content(content, filename: str = ""):
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
            return get_plain_text_response(content, filename)

        if has_html:
            return get_sanitized_html_response(content, filename)

        has_script = any(tag in sample for tag in script_indicators)
        if has_script:
            return get_plain_text_response(content, filename)

    return get_attachment_response(content, filename)


INLINE_SAFE_MIME_PREFIXES = ("image/", "application/pdf")

READ_FILE_MIME_TYPE_HANDLERS = {
    "text/html": get_sanitized_html_response,
    "image/svg+xml": get_safe_svg_response,
    "text/xml": get_attachment_response,
    "application/xml": get_attachment_response,
    "application/xhtml+xml": get_attachment_response,
    "application/rss+xml": get_attachment_response,
    "application/atom+xml": get_attachment_response,
    "application/javascript": get_plain_text_response,
    "text/javascript": get_plain_text_response,
    "application/x-javascript": get_plain_text_response,
    "application/x-typescript": get_plain_text_response,
    _OCTET_STREAM: check_and_sanitize_content,
}


_workspace_repo = AgentWorkspaceRepository()


def _raise_file_not_found() -> NoReturn:
    raise ExtendedHTTPException(
        code=status.HTTP_404_NOT_FOUND,
        message="File not found",
        details="The requested file could not be found.",
        help="Please verify the file name and try again.",
    )


_WORKFLOW_SCHEMA_PREFIX = "workflows/"
_WORKFLOW_SCHEMA_SUFFIX = ".svg"


def _can_read_workflow_schema(name: str, user: User) -> bool:
    """Rule B helper: a workflow schema SVG is readable only by users who may read the workflow.

    Schema blobs are written as ``workflows/{workflow_id}.svg`` (workflow_service.save_workflow_schema)
    under the shared CODEMIE_STORAGE_BUCKET_NAME owner. That same owner namespace also holds
    memory-profiling snapshots, so the name shape is checked first and everything else is denied.
    Fails closed: any lookup error denies access rather than surfacing a 500 from the auth path.
    """
    if not name.startswith(_WORKFLOW_SCHEMA_PREFIX) or not name.endswith(_WORKFLOW_SCHEMA_SUFFIX):
        return False

    workflow_id = name[len(_WORKFLOW_SCHEMA_PREFIX) : -len(_WORKFLOW_SCHEMA_SUFFIX)]
    if not workflow_id:
        return False

    try:
        workflow_config = WorkflowConfig.find_by_id(workflow_id)
        return workflow_config is not None and Ability(user).can(Action.READ, workflow_config)
    except Exception:
        logger.warning(f"Workflow schema authorization lookup failed for workflow_id={workflow_id!r}")
        return False


_WORKSPACE_OWNER_PREFIX = "workspace-"


def _points_outside_owner(file_object: FileObject) -> bool:
    """True when the decoded token could resolve outside its own owner's storage directory.

    Authorization keys on the token's ``owner``, but the filesystem backend joins owner and name,
    so a name like ``../<victim>/secret.pdf`` under the requester's own owner would pass the
    ownership rule and still read someone else's file. The storage layer refuses such a path as
    well; denying here keeps the answer identical to every other denial instead of surfacing the
    storage error as a 500.
    """
    for value in (file_object.owner, file_object.name):
        if not value or value.startswith("/") or ".." in PurePosixPath(value).parts:
            return True
    return False


def _is_readable_workflow_schema(file_object: FileObject, user: User) -> bool:
    """Rule B: a workflow schema SVG, and only for a workflow this user may read.

    The same owner namespace also holds memory-profiling snapshots, so the name shape is
    checked before anything else and everything outside `workflows/` is denied.
    """
    bucket = config.CODEMIE_STORAGE_BUCKET_NAME
    return bool(bucket) and file_object.owner == bucket and _can_read_workflow_schema(file_object.name, user)


def _owns_workspace_blob(owner: str, user: User, workspace_repo: AgentWorkspaceRepository) -> bool:
    """Rule D: a workspace-prefixed blob whose workspace belongs to the requester."""
    if not owner.startswith(_WORKSPACE_OWNER_PREFIX):
        return False

    workspace_id = owner.removeprefix(_WORKSPACE_OWNER_PREFIX)
    return workspace_repo.get_by_id_for_user(workspace_id, user.id) is not None


def _conversation_refers_to_file(conversation: Conversation, file_name_param: str) -> bool:
    """True when any message of the conversation references this exact encoded token.

    Uses the same collector the share endpoint issues grants from, so the set this authorizes and
    the set the recipient was handed URLs for cannot drift apart. That covers both carriers:
    `file_names` entries and inline `sandbox:/v1/files/<token>` references in message text.
    """
    return any(file_name_param in collect_message_file_tokens(message) for message in (conversation.history or []))


def _has_share_grant(
    file_object: FileObject,
    share_token: str | None,
    file_name_param: str,
    workspace_repo: AgentWorkspaceRepository,
) -> bool:
    """Rule E: the file belongs to a conversation shared with the holder of this token.

    Two carriers are accepted: the token appears in a message's `file_names`, or the blob is
    registered in the sharer's workspace. The second covers files referenced only by an inline
    `sandbox:/v1/files/...` URL, including blobs registered by reference under their original
    owner rather than under the workspace.
    """
    if not share_token:
        return False

    shared = SharedConversation.get_by_fields({"share_token.keyword": share_token})
    if not shared:
        return False

    conversation = Conversation.find_by_id(shared.conversation_id)
    if not conversation:
        return False

    if _conversation_refers_to_file(conversation, file_name_param):
        return True

    workspace = workspace_repo.get_by_conversation_for_user(shared.conversation_id, shared.shared_by_user_id)
    if not workspace:
        return False

    return (
        workspace_repo.find_by_blob(workspace.id, file_object.owner, file_object.name, file_object.mime_type)
        is not None
    )


def _authorize_file_access(
    file_object: FileObject,
    user: User,
    share_token: str | None,
    file_name_param: str,
    workspace_repo: AgentWorkspaceRepository,
) -> None:
    """Grant a file download, or raise the shared 404. Rules are evaluated in order."""
    owner = file_object.owner

    # Before any rule: a token whose owner or name escapes the owner's directory is never served,
    # however the rules below would classify it.
    if _points_outside_owner(file_object):
        _raise_file_not_found()

    # Rule A: requester owns the file directly.
    if owner == user.id:
        return

    # Rule B: workflow schema, and only for a workflow this user may read.
    if _is_readable_workflow_schema(file_object, user):
        return

    # Rule C: MCP screenshot stored under the shared namespace. Screenshots are normally written
    # under the invoking user's id (see _save_screenshot_to_storage) and so are covered by Rule A;
    # this rule covers blobs written before that change, plus any written when no invoking user was
    # known. Such blobs are readable by any authenticated user who has the name — accepted residual
    # risk: names are uuid4().hex, and denying them would break images in existing conversations.
    # The unattributed-write path is logged, so whether it still occurs is observable rather than
    # assumed; the rule can be tightened once that log stays silent and history has aged out.
    if owner == MCP_IMAGES_SUBDIR:
        return

    # Rule D: workspace blob the requester owns. A miss falls through to Rule E, which still
    # grants a share recipient viewing the sharer's workspace files.
    if _owns_workspace_blob(owner, user, workspace_repo):
        return

    # Rule E: share grant.
    if _has_share_grant(file_object, share_token, file_name_param, workspace_repo):
        return

    _raise_file_not_found()


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
    # It also fails closed: a backend hiccup during a permission or share lookup must deny
    # rather than surface a 500, which would otherwise tell a requester that the blob exists.
    try:
        _authorize_file_access(file_object, user, share_token, file_name, _workspace_repo)
    except ExtendedHTTPException:
        raise
    except Exception:
        logger.exception(f"File authorization failed to evaluate: user_id={user.id}")
        _raise_file_not_found()

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

    server_mime = mimetypes.guess_type(file.filename or "")[0] or _OCTET_STREAM
    MarkdownCacheService().invalidate(owner=user.id, filename=file.filename, repo=fs_repo)
    result = fs_repo.write_file(name=file.filename, mime_type=server_mime, owner=user.id, content=data)

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

    if len(files) > AssistantChatRequest.MAX_FILE_COUNT:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=f"Too many files. Maximum count is {AssistantChatRequest.MAX_FILE_COUNT}",
            details=f"Received {len(files)} files but the maximum allowed is {AssistantChatRequest.MAX_FILE_COUNT}.",
            help="Please split your upload into multiple requests.",
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
            server_mime = mimetypes.guess_type(file.filename or "")[0] or _OCTET_STREAM
            MarkdownCacheService().invalidate(owner=user.id, filename=file.filename, repo=fs_repo)
            result = fs_repo.write_file(name=file.filename, mime_type=server_mime, owner=user.id, content=data)

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
