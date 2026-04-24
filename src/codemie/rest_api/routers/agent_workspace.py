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

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from starlette import status

from codemie.core.exceptions import ExtendedHTTPException, ValidationException
from codemie.rest_api.models.agent_workspace import (
    AgentWorkspaceResponse,
    CreateAgentWorkspaceRequest,
    ExecuteWorkspaceScriptRequest,
    ExecuteWorkspaceScriptResponse,
    UpsertWorkspaceFileRequest,
    WorkspaceDeleteFileResponse,
    WorkspaceEditFileRequest,
    WorkspaceEditFileResponse,
    WorkspaceFileContentResponse,
    WorkspaceFileItemResponse,
    WorkspaceGrepResponse,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService

router = APIRouter(
    tags=["Agent Workspace"],
    prefix="/v1",
    dependencies=[],
)

workspace_service = AgentWorkspaceService()


def _as_http_error(exception: Exception) -> ExtendedHTTPException:
    if isinstance(exception, ValidationException):
        return ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid workspace request",
            details=str(exception),
            help="Please verify workspace identifiers and file paths, then try again.",
        )

    return ExtendedHTTPException(
        code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        message="Workspace request failed",
        details=str(exception),
        help="Please try again later. If the problem persists, contact the administrator.",
    )


@router.post(
    "/workspaces",
    response_model=AgentWorkspaceResponse,
    dependencies=[Depends(authenticate)],
)
def create_workspace(
    request: CreateAgentWorkspaceRequest, user: User = Depends(authenticate)
) -> AgentWorkspaceResponse:
    try:
        return workspace_service.create_workspace(request, user)
    except Exception as exception:
        raise _as_http_error(exception) from exception


@router.get(
    "/workspaces",
    response_model=list[AgentWorkspaceResponse],
    dependencies=[Depends(authenticate)],
)
def list_workspaces(user: User = Depends(authenticate)) -> list[AgentWorkspaceResponse]:
    try:
        return workspace_service.list_workspaces(user)
    except Exception as exception:
        raise _as_http_error(exception) from exception


@router.get(
    "/workspaces/conversations/{conversation_id}",
    response_model=AgentWorkspaceResponse,
    dependencies=[Depends(authenticate)],
)
def get_workspace_by_conversation(conversation_id: str, user: User = Depends(authenticate)) -> AgentWorkspaceResponse:
    try:
        return workspace_service.get_workspace_by_conversation(conversation_id, user)
    except Exception as exception:
        raise _as_http_error(exception) from exception


@router.get(
    "/workspaces/{workspace_id}/files",
    response_model=list[WorkspaceFileItemResponse],
    dependencies=[Depends(authenticate)],
)
def list_workspace_files(
    workspace_id: str,
    prefix: Optional[str] = Query(default=None),
    recursive: bool = Query(default=True),
    user: User = Depends(authenticate),
) -> list[WorkspaceFileItemResponse]:
    try:
        return workspace_service.list_files(workspace_id, user, prefix=prefix, recursive=recursive)
    except Exception as exception:
        raise _as_http_error(exception) from exception


@router.put(
    "/workspaces/{workspace_id}/files",
    response_model=WorkspaceFileItemResponse,
    dependencies=[Depends(authenticate)],
)
def upsert_workspace_file(
    workspace_id: str,
    request: UpsertWorkspaceFileRequest,
    user: User = Depends(authenticate),
) -> WorkspaceFileItemResponse:
    try:
        return workspace_service.upsert_text_file(workspace_id, request.file_path, request.content, user)
    except Exception as exception:
        raise _as_http_error(exception) from exception


@router.get(
    "/workspaces/{workspace_id}/files/content",
    response_model=WorkspaceFileContentResponse,
    dependencies=[Depends(authenticate)],
)
def get_workspace_file_content(
    workspace_id: str,
    file_path: str = Query(...),
    user: User = Depends(authenticate),
) -> WorkspaceFileContentResponse:
    try:
        return workspace_service.get_file_content(workspace_id, file_path, user)
    except Exception as exception:
        raise _as_http_error(exception) from exception


@router.get("/workspaces/{workspace_id}/files/download", dependencies=[Depends(authenticate)])
def download_workspace_file(
    workspace_id: str,
    file_path: str = Query(...),
    user: User = Depends(authenticate),
):
    try:
        file_object = workspace_service.download_file(workspace_id, file_path, user)
        download_name = PurePosixPath(file_path).name
        return Response(
            content=file_object.content,
            media_type=file_object.mime_type,
            headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
        )
    except Exception as exception:
        raise _as_http_error(exception) from exception


@router.post(
    "/workspaces/{workspace_id}/files/edit",
    response_model=WorkspaceEditFileResponse,
    dependencies=[Depends(authenticate)],
)
def edit_workspace_file(
    workspace_id: str,
    request: WorkspaceEditFileRequest,
    user: User = Depends(authenticate),
) -> WorkspaceEditFileResponse:
    try:
        return workspace_service.edit_file(
            workspace_id=workspace_id,
            file_path=request.file_path,
            old_string=request.old_string,
            new_string=request.new_string,
            replace_all=request.replace_all,
            user=user,
        )
    except Exception as exception:
        raise _as_http_error(exception) from exception


@router.delete(
    "/workspaces/{workspace_id}/files",
    response_model=WorkspaceDeleteFileResponse,
    dependencies=[Depends(authenticate)],
)
def delete_workspace_file(
    workspace_id: str,
    file_path: str = Query(...),
    user: User = Depends(authenticate),
) -> WorkspaceDeleteFileResponse:
    try:
        return workspace_service.delete_file(workspace_id, file_path, user)
    except Exception as exception:
        raise _as_http_error(exception) from exception


@router.get(
    "/workspaces/{workspace_id}/files/grep",
    response_model=WorkspaceGrepResponse,
    dependencies=[Depends(authenticate)],
)
def grep_workspace_files(
    workspace_id: str,
    query: str = Query(...),
    prefix: Optional[str] = Query(default=None),
    recursive: bool = Query(default=True),
    user: User = Depends(authenticate),
) -> WorkspaceGrepResponse:
    try:
        matches = workspace_service.grep_files(workspace_id, query, user, prefix=prefix, recursive=recursive)
        return WorkspaceGrepResponse(matches=matches)
    except Exception as exception:
        raise _as_http_error(exception) from exception


@router.post(
    "/workspaces/{workspace_id}/execute",
    response_model=ExecuteWorkspaceScriptResponse,
    dependencies=[Depends(authenticate)],
)
def execute_workspace_script(
    workspace_id: str,
    request: ExecuteWorkspaceScriptRequest,
    user: User = Depends(authenticate),
) -> ExecuteWorkspaceScriptResponse:
    try:
        return workspace_service.execute_workspace_script(
            workspace_id=workspace_id,
            script_path=request.script_path,
            user=user,
            export_files=request.export_files,
        )
    except Exception as exception:
        raise _as_http_error(exception) from exception
