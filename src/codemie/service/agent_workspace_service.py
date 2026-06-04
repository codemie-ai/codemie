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

import hashlib
import mimetypes
from pathlib import PurePosixPath

from codemie.core.exceptions import ValidationException
from codemie.repository.agent_workspace_repository import AgentWorkspaceRepository
from codemie.repository.repository_factory import FileRepositoryFactory
from codemie.rest_api.models.agent_workspace import (
    AgentWorkspace,
    AgentWorkspaceFile,
    AgentWorkspaceResponse,
    CreateAgentWorkspaceRequest,
    ExecuteWorkspaceScriptResponse,
    WorkspaceDeleteFileResponse,
    WorkspaceEditFileResponse,
    WorkspaceFileContentResponse,
    WorkspaceFileItemResponse,
    WorkspaceGrepMatchResponse,
)
from codemie.rest_api.security.user import User
from codemie_tools.base.file_object import FileObject
from codemie_tools.data_management.workspace.execute_workspace_script_tool import (
    WorkspaceScriptRunner,
)

MULTIPLE_OLD_STRING_ERROR = (
    "Multiple old_string are found. Please adjust old_string to be unique or set replace_all to True "
    "if all such occurences should be replaced"
)
SANDBOX_FILE_PREFIX = "sandbox:/v1/files/"


class AgentWorkspaceService:
    def __init__(self):
        self.repository = AgentWorkspaceRepository()
        self.file_repository = FileRepositoryFactory.get_current_repository()

    def upsert_binary_file(
        self,
        workspace_id: str,
        file_path: str,
        content: bytes,
        user: User,
    ) -> WorkspaceFileItemResponse:
        workspace = self.get_workspace(workspace_id, user)
        resolved_workspace_id = workspace.id
        if resolved_workspace_id is None:
            raise ValidationException(f"Workspace '{workspace_id}' not found")
        saved_file = self._upsert_workspace_file_content(resolved_workspace_id, file_path, content)
        return WorkspaceFileItemResponse.from_model(saved_file)

    def get_file_sandbox_url(self, workspace_id: str, file_path: str, user: User) -> str:
        workspace = self.get_workspace(workspace_id, user)
        if workspace.id is None:
            raise ValidationException(f"Workspace '{workspace_id}' not found")
        workspace_file = self._get_workspace_file_or_raise(workspace, file_path)
        file_object = FileObject(
            name=workspace_file.blob_name,
            mime_type=workspace_file.mime_type,
            owner=workspace_file.blob_owner,
            path=workspace_file.path,
        )
        return f"{SANDBOX_FILE_PREFIX}{file_object.to_encoded_url()}"

    def create_workspace(self, request: CreateAgentWorkspaceRequest, user: User) -> AgentWorkspaceResponse:
        existing = self.repository.get_by_conversation_for_user(request.conversation_id, user.id)
        if existing:
            return AgentWorkspaceResponse.from_model(existing)

        workspace = AgentWorkspace(
            conversation_id=request.conversation_id,
            user_id=user.id,
            name=request.name,
        )
        self.repository.save_workspace(workspace)
        self.sync_uploaded_files(
            conversation_id=request.conversation_id,
            file_urls=self._get_conversation_uploaded_file_urls(request.conversation_id),
            user=user,
        )
        return AgentWorkspaceResponse.from_model(workspace)

    def sync_uploaded_files(
        self,
        conversation_id: str,
        file_urls: list[str],
        user: User,
    ) -> list[WorkspaceFileItemResponse]:
        """Persist workspace metadata for uploaded chat files without copying blob content."""
        if not conversation_id or not file_urls:
            return []

        workspace = self._get_or_create_workspace(conversation_id, user)
        existing_files = self.repository.list_files(workspace.id)
        known_blob_keys = {
            self._blob_identity_key(
                workspace_file.blob_owner,
                workspace_file.blob_name,
                workspace_file.mime_type,
            )
            for workspace_file in existing_files
        }
        known_paths = {workspace_file.path for workspace_file in existing_files}

        synced_files: list[WorkspaceFileItemResponse] = []
        for file_url in file_urls:
            file_object = self._decode_workspace_file_url(file_url)
            if not file_object:
                continue

            blob_key = self._blob_identity_key(file_object.owner, file_object.name, file_object.mime_type)
            workspace_path = self._normalize_uploaded_file_path(file_object)
            if blob_key in known_blob_keys or workspace_path in known_paths:
                continue

            workspace_file = self._upsert_workspace_file_reference(workspace.id, workspace_path, file_object)
            synced_files.append(WorkspaceFileItemResponse.from_model(workspace_file))
            known_blob_keys.add(blob_key)
            known_paths.add(workspace_path)

        return synced_files

    def get_workspace(self, workspace_id: str, user: User) -> AgentWorkspace:
        workspace = self.repository.get_by_id_for_user(workspace_id, user.id)
        if not workspace:
            raise ValidationException(f"Workspace '{workspace_id}' not found")
        return workspace

    def get_workspace_by_conversation(self, conversation_id: str, user: User) -> AgentWorkspaceResponse:
        workspace = self.repository.get_by_conversation_for_user(conversation_id, user.id)
        if not workspace:
            raise ValidationException(f"Workspace for conversation '{conversation_id}' not found")
        return AgentWorkspaceResponse.from_model(workspace)

    def register_generated_files(
        self,
        conversation_id: str,
        generated_file_urls: list[str],
        user: User,
        request_file_urls: list[str] | None = None,
    ) -> list[WorkspaceFileItemResponse]:
        if not conversation_id or not generated_file_urls:
            return []

        workspace = self._get_or_create_workspace(conversation_id, user)
        existing_files = self.repository.list_files(workspace.id)
        known_blob_keys = {
            self._blob_identity_key(
                workspace_file.blob_owner,
                workspace_file.blob_name,
                workspace_file.mime_type,
            )
            for workspace_file in existing_files
        }
        known_paths = {workspace_file.path for workspace_file in existing_files}
        request_blob_keys = self._extract_blob_keys(request_file_urls or [])
        conversation_blob_keys = {
            self._blob_identity_key(virtual_file.blob_owner, virtual_file.blob_name, virtual_file.mime_type)
            for virtual_file in self._get_conversation_uploaded_files(conversation_id)
        }

        registered_files: list[WorkspaceFileItemResponse] = []
        for generated_file_url in generated_file_urls:
            file_object = self._decode_workspace_file_url(generated_file_url)
            if not file_object:
                continue

            blob_key = self._blob_identity_key(file_object.owner, file_object.name, file_object.mime_type)
            workspace_relative_path = file_object.path or file_object.name
            workspace_path = self._normalize_path(workspace_relative_path)

            if blob_key in request_blob_keys or blob_key in conversation_blob_keys:
                continue
            if blob_key in known_blob_keys:
                continue

            workspace_file = self._upsert_workspace_file_reference(workspace.id, workspace_path, file_object)
            registered_files.append(WorkspaceFileItemResponse.from_model(workspace_file))
            known_blob_keys.add(blob_key)
            known_paths.add(workspace_path)

        return registered_files

    def list_workspaces(self, user: User) -> list[AgentWorkspaceResponse]:
        return [AgentWorkspaceResponse.from_model(workspace) for workspace in self.repository.list_for_user(user.id)]

    def list_files(
        self,
        workspace_id: str,
        user: User,
        prefix: str | None = None,
        recursive: bool = True,
    ) -> list[WorkspaceFileItemResponse]:
        workspace = self.get_workspace(workspace_id, user)
        normalized_prefix = self._normalize_optional_path(prefix)
        db_files = self.repository.list_files(workspace.id)

        existing_paths: set[str] = set()
        filtered_files = []
        for workspace_file in db_files:
            existing_paths.add(workspace_file.path)
            if not self._path_matches_prefix(workspace_file.path, normalized_prefix, recursive):
                continue
            filtered_files.append(WorkspaceFileItemResponse.from_model(workspace_file))

        for virtual_file in self._get_conversation_uploaded_files(workspace.conversation_id):
            if virtual_file.path in existing_paths:
                continue
            if not self._path_matches_prefix(virtual_file.path, normalized_prefix, recursive):
                continue
            filtered_files.append(WorkspaceFileItemResponse.from_model(virtual_file))

        return filtered_files

    def upsert_text_file(
        self, workspace_id: str, file_path: str, content: str, user: User
    ) -> WorkspaceFileItemResponse:
        workspace = self.get_workspace(workspace_id, user)
        saved_file = self._upsert_workspace_file_content(workspace.id, file_path, content)
        return WorkspaceFileItemResponse.from_model(saved_file)

    def get_file_content(self, workspace_id: str, file_path: str, user: User) -> WorkspaceFileContentResponse:
        workspace = self.get_workspace(workspace_id, user)
        workspace_file = self._get_workspace_file_or_raise(workspace, file_path)
        file_object = self.file_repository.read_file(
            file_name=workspace_file.blob_name,
            owner=workspace_file.blob_owner,
            mime_type=workspace_file.mime_type,
        )
        is_binary = not self._is_text_mime_type(workspace_file.mime_type)
        content = None if is_binary else self._to_text_content(file_object.content)
        checksum = workspace_file.checksum
        size = workspace_file.size
        if not checksum and file_object.content is not None:
            raw = file_object.content if isinstance(file_object.content, bytes) else file_object.content.encode("utf-8")
            checksum = hashlib.sha256(raw).hexdigest()
            size = len(raw)
        return WorkspaceFileContentResponse(
            path=workspace_file.path,
            mime_type=workspace_file.mime_type,
            checksum=checksum,
            size=size,
            version=workspace_file.version,
            is_binary=is_binary,
            content=content,
        )

    def download_file(self, workspace_id: str, file_path: str, user: User):
        workspace = self.get_workspace(workspace_id, user)
        workspace_file = self._get_workspace_file_or_raise(workspace, file_path)
        return self.file_repository.read_file(
            file_name=workspace_file.blob_name,
            owner=workspace_file.blob_owner,
            mime_type=workspace_file.mime_type,
        )

    def edit_file(
        self,
        workspace_id: str,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool,
        user: User,
    ) -> WorkspaceEditFileResponse:
        file_content = self.get_file_content(workspace_id, file_path, user)
        if file_content.is_binary:
            return WorkspaceEditFileResponse(
                message=f"Cannot edit binary file '{file_content.path}'",
                updated=False,
                occurrences_replaced=0,
            )

        occurrences = file_content.content.count(old_string) if file_content.content else 0
        if occurrences == 0:
            return WorkspaceEditFileResponse(
                message=f"old_string was not found in '{file_content.path}'",
                updated=False,
                occurrences_replaced=0,
            )

        if occurrences > 1 and not replace_all:
            return WorkspaceEditFileResponse(message=MULTIPLE_OLD_STRING_ERROR, updated=False, occurrences_replaced=0)

        updated_content = (
            file_content.content.replace(old_string, new_string)
            if replace_all
            else file_content.content.replace(old_string, new_string, 1)
        )
        file_item = self.upsert_text_file(workspace_id, file_path, updated_content, user)
        occurrences_replaced = occurrences if replace_all else 1
        return WorkspaceEditFileResponse(
            message=f"Updated '{file_item.path}' successfully",
            updated=True,
            occurrences_replaced=occurrences_replaced,
        )

    def delete_file(self, workspace_id: str, file_path: str, user: User) -> WorkspaceDeleteFileResponse:
        workspace = self.get_workspace(workspace_id, user)
        normalized_path = self._normalize_path(file_path)
        deleted = self.repository.soft_delete_file(workspace.id, normalized_path)
        if not deleted:
            return WorkspaceDeleteFileResponse(message=f"File '{normalized_path}' not found", deleted=False)
        return WorkspaceDeleteFileResponse(message=f"Deleted '{normalized_path}' successfully", deleted=True)

    def grep_files(
        self,
        workspace_id: str,
        query: str,
        user: User,
        prefix: str | None = None,
        recursive: bool = True,
    ) -> list[WorkspaceGrepMatchResponse]:
        workspace = self.get_workspace(workspace_id, user)
        normalized_prefix = self._normalize_optional_path(prefix)
        matches: list[WorkspaceGrepMatchResponse] = []

        db_files = self.repository.list_files(workspace.id)
        existing_paths: set[str] = set()
        all_files = []
        for workspace_file in db_files:
            existing_paths.add(workspace_file.path)
            all_files.append(workspace_file)
        for virtual_file in self._get_conversation_uploaded_files(workspace.conversation_id):
            if virtual_file.path not in existing_paths:
                all_files.append(virtual_file)

        for workspace_file in all_files:
            if not self._path_matches_prefix(workspace_file.path, normalized_prefix, recursive):
                continue
            if not self._is_text_mime_type(workspace_file.mime_type):
                continue

            file_object = self.file_repository.read_file(
                file_name=workspace_file.blob_name,
                owner=workspace_file.blob_owner,
                mime_type=workspace_file.mime_type,
            )
            content = self._to_text_content(file_object.content)
            for line_number, line in enumerate(content.splitlines(), start=1):
                if query.lower() in line.lower():
                    matches.append(
                        WorkspaceGrepMatchResponse(
                            file_path=workspace_file.path,
                            line_number=line_number,
                            line=line,
                        )
                    )

        return matches

    def get_workspace_input_files(self, workspace_id: str, user: User) -> list[FileObject]:
        workspace = self.get_workspace(workspace_id, user)
        input_files: list[FileObject] = []
        existing_paths: set[str] = set()

        for workspace_file in self.repository.list_files(workspace.id):
            existing_paths.add(workspace_file.path)
            file_object = self.file_repository.read_file(
                file_name=workspace_file.blob_name,
                owner=workspace_file.blob_owner,
                mime_type=workspace_file.mime_type,
            )
            input_files.append(
                FileObject(
                    name=workspace_file.path,
                    mime_type=workspace_file.mime_type,
                    owner=workspace_file.blob_owner,
                    path=workspace_file.path,
                    content=file_object.content,
                )
            )

        for virtual_file in self._get_conversation_uploaded_files(workspace.conversation_id):
            if virtual_file.path in existing_paths:
                continue
            file_object = self.file_repository.read_file(
                file_name=virtual_file.blob_name,
                owner=virtual_file.blob_owner,
                mime_type=virtual_file.mime_type,
            )
            input_files.append(
                FileObject(
                    name=virtual_file.path,
                    mime_type=virtual_file.mime_type,
                    owner=virtual_file.blob_owner,
                    path=virtual_file.path,
                    content=file_object.content,
                )
            )

        return input_files

    def execute_workspace_script(
        self,
        workspace_id: str,
        script_path: str,
        user: User,
        export_files: list[str] | None = None,
    ) -> ExecuteWorkspaceScriptResponse:
        workspace = self.get_workspace(workspace_id, user)
        input_files = self.get_workspace_input_files(workspace_id, user)
        executor = WorkspaceScriptRunner(
            file_repository=self.file_repository,
            user_id=user.id,
            input_files=input_files,
            conversation_id=workspace.conversation_id,
        )
        output = executor.execute_script(script_path=script_path, export_files=export_files)
        synced_files = self._sync_execution_files(workspace.id, executor.last_execution_files)
        return ExecuteWorkspaceScriptResponse(
            message="Workspace script executed successfully",
            output=output,
            workspace_files=synced_files,
        )

    def _sync_execution_files(
        self, workspace_id: str, execution_files: list[FileObject]
    ) -> list[WorkspaceFileItemResponse]:
        synced_files: list[WorkspaceFileItemResponse] = []

        for execution_file in execution_files:
            saved_file = self._upsert_workspace_file_content(
                workspace_id, execution_file.name, execution_file.content or b""
            )
            synced_files.append(WorkspaceFileItemResponse.from_model(saved_file))

        return synced_files

    def _upsert_workspace_file_content(
        self, workspace_id: str, file_path: str, content: str | bytes
    ) -> AgentWorkspaceFile:
        normalized_path = self._normalize_path(file_path)
        binary_content = content.encode("utf-8") if isinstance(content, str) else content
        checksum = hashlib.sha256(binary_content).hexdigest()
        mime_type = self._guess_mime_type(normalized_path)
        blob_owner = self._get_blob_owner(workspace_id)

        self.file_repository.write_file(
            name=normalized_path,
            mime_type=mime_type,
            owner=blob_owner,
            content=content,
        )

        workspace_file = self.repository.get_file(workspace_id, normalized_path, include_deleted=True)
        if workspace_file:
            workspace_file.blob_owner = blob_owner
            workspace_file.blob_name = normalized_path
            workspace_file.mime_type = mime_type
            workspace_file.checksum = checksum
            workspace_file.size = len(binary_content)
            workspace_file.version += 1
            workspace_file.deleted_at = None
        else:
            workspace_file = AgentWorkspaceFile(
                workspace_id=workspace_id,
                path=normalized_path,
                blob_owner=blob_owner,
                blob_name=normalized_path,
                mime_type=mime_type,
                checksum=checksum,
                size=len(binary_content),
            )

        return self.repository.save_file(workspace_file)

    def _upsert_workspace_file_reference(
        self, workspace_id: str, file_path: str, file_object: FileObject
    ) -> AgentWorkspaceFile:
        # Keep uploaded chat files in their original storage location and persist only
        # workspace metadata that points to that blob.
        normalized_path = self._normalize_path(file_path)
        try:
            repository_file = self.file_repository.read_file(
                file_name=file_object.name,
                owner=file_object.owner,
                mime_type=file_object.mime_type,
            )
        except FileNotFoundError:
            repository_file = None

        raw_content = repository_file.content if repository_file and repository_file.content is not None else b""
        binary_content = raw_content if isinstance(raw_content, bytes) else raw_content.encode("utf-8")
        checksum = hashlib.sha256(binary_content).hexdigest()
        size = len(binary_content)

        if size == 0:
            size = getattr(file_object, "size", 0) or 0

        workspace_file = self.repository.get_file(workspace_id, normalized_path, include_deleted=True)
        if workspace_file:
            workspace_file.blob_owner = file_object.owner
            workspace_file.blob_name = file_object.name
            workspace_file.mime_type = file_object.mime_type
            workspace_file.checksum = checksum
            workspace_file.size = size
            workspace_file.version += 1
            workspace_file.deleted_at = None
        else:
            workspace_file = AgentWorkspaceFile(
                workspace_id=workspace_id,
                path=normalized_path,
                blob_owner=file_object.owner,
                blob_name=file_object.name,
                mime_type=file_object.mime_type,
                checksum=checksum,
                size=size,
            )

        return self.repository.save_file(workspace_file)

    def _get_workspace_file_or_raise(self, workspace: AgentWorkspace, file_path: str) -> AgentWorkspaceFile:
        normalized_path = self._normalize_path(file_path)
        workspace_file = self.repository.get_file(workspace.id, normalized_path)
        if workspace_file:
            return workspace_file
        for virtual_file in self._get_conversation_uploaded_files(workspace.conversation_id):
            if virtual_file.path == normalized_path:
                return virtual_file
        raise ValidationException(f"File '{normalized_path}' not found")

    def _get_conversation_uploaded_files(self, conversation_id: str) -> list[AgentWorkspaceFile]:
        """Return virtual AgentWorkspaceFile entries for files uploaded in the conversation.

        These records are not persisted — blob_owner/blob_name point to the original
        upload blobs so reads are served directly from there without copying content.
        Workspace-native files (from the DB) always shadow these when paths collide.
        """
        seen_paths: set[str] = set()
        virtual_files: list[AgentWorkspaceFile] = []

        for encoded_url in self._get_conversation_uploaded_file_urls(conversation_id):
            try:
                file_obj = FileObject.from_encoded_url(encoded_url)
            except Exception:
                continue
            path = self._normalize_uploaded_file_path(file_obj)
            if path in seen_paths:
                continue
            seen_paths.add(path)
            virtual_files.append(
                AgentWorkspaceFile(
                    workspace_id="",
                    path=path,
                    blob_owner=file_obj.owner,
                    blob_name=file_obj.name,
                    mime_type=file_obj.mime_type,
                    checksum="",
                    size=0,
                    version=0,
                )
            )

        return virtual_files

    @staticmethod
    def _get_conversation_uploaded_file_urls(conversation_id: str) -> list[str]:
        from codemie.rest_api.models.conversation import Conversation

        conversation = Conversation.find_by_id(conversation_id)
        if not conversation:
            return []

        file_urls: list[str] = []
        for message in conversation.history or []:
            file_urls.extend(message.file_names or [])
        return file_urls

    def _get_or_create_workspace(self, conversation_id: str, user: User) -> AgentWorkspace:
        workspace = self.repository.get_by_conversation_for_user(conversation_id, user.id)
        if workspace:
            return workspace

        self.create_workspace(CreateAgentWorkspaceRequest(conversation_id=conversation_id), user)
        workspace = self.repository.get_by_conversation_for_user(conversation_id, user.id)
        if not workspace:
            raise ValidationException(f"Workspace for conversation '{conversation_id}' not found")
        return workspace

    @staticmethod
    def _normalize_file_url(file_url: str) -> str:
        if not file_url:
            return ""
        return file_url[len(SANDBOX_FILE_PREFIX) :] if file_url.startswith(SANDBOX_FILE_PREFIX) else file_url

    @classmethod
    def _decode_workspace_file_url(cls, file_url: str) -> FileObject | None:
        normalized_url = cls._normalize_file_url(file_url)
        if not normalized_url:
            return None
        try:
            return FileObject.from_encoded_url(normalized_url)
        except Exception:
            return None

    @classmethod
    def _extract_blob_keys(cls, file_urls: list[str]) -> set[tuple[str, str, str]]:
        blob_keys: set[tuple[str, str, str]] = set()
        for file_url in file_urls:
            file_object = cls._decode_workspace_file_url(file_url)
            if not file_object:
                continue
            blob_keys.add(cls._blob_identity_key(file_object.owner, file_object.name, file_object.mime_type))
        return blob_keys

    @staticmethod
    def _blob_identity_key(owner: str, name: str, mime_type: str) -> tuple[str, str, str]:
        return owner, name, mime_type

    @classmethod
    def _normalize_uploaded_file_path(cls, file_object: FileObject) -> str:
        return cls._normalize_path(PurePosixPath(file_object.name).name)

    @staticmethod
    def _normalize_path(file_path: str) -> str:
        candidate = (file_path or "").replace("\\", "/").strip()
        if not candidate:
            raise ValidationException("file_path must not be empty")

        normalized_path = PurePosixPath(candidate)
        if normalized_path.is_absolute() or ".." in normalized_path.parts:
            raise ValidationException("file_path must be a workspace-relative path")

        normalized = str(normalized_path)
        if normalized in {"", "."}:
            raise ValidationException("file_path must be a file path")
        return normalized

    def _normalize_optional_path(self, file_path: str | None) -> str | None:
        if not file_path:
            return None
        return self._normalize_path(file_path)

    @staticmethod
    def _guess_mime_type(file_path: str) -> str:
        guessed_mime_type, _ = mimetypes.guess_type(file_path)
        return guessed_mime_type or "text/plain"

    @staticmethod
    def _is_text_mime_type(mime_type: str) -> bool:
        text_like_mime_types = {
            "application/json",
            "application/javascript",
            "application/x-python-code",
            "application/x-sh",
            "application/x-yaml",
            "application/xml",
        }
        return mime_type.startswith("text/") or mime_type in text_like_mime_types

    @staticmethod
    def _path_matches_prefix(file_path: str, prefix: str | None, recursive: bool) -> bool:
        if prefix is None:
            return recursive or "/" not in file_path

        normalized_prefix = prefix.rstrip("/")
        if file_path == normalized_prefix:
            return True
        if not file_path.startswith(f"{normalized_prefix}/"):
            return False
        if recursive:
            return True
        remaining_path = file_path[len(normalized_prefix) + 1 :]
        return "/" not in remaining_path

    @staticmethod
    def _get_blob_owner(workspace_id: str) -> str:
        return f"workspace-{workspace_id}"

    @staticmethod
    def _to_text_content(content: str | bytes) -> str:
        if isinstance(content, bytes):
            return content.decode("utf-8", errors="replace")
        return content
