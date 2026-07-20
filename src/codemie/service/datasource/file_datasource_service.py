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

"""Business logic and repository communication for file-type knowledge-base datasources."""

from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass, field

from elasticsearch.exceptions import NotFoundError
from fastapi import UploadFile, status

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application, CreatedByUser
from codemie.datasource.file.file_datasource_processor import FILE_PATH_DATA_NT
from codemie.repository.repository_factory import FileRepositoryFactory
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem
from codemie.rest_api.models.index import IndexInfo, IndexKnowledgeBaseFileTypes, KnowledgeBaseIndexInfo
from codemie.rest_api.security.user import User
from codemie.service.datasource.zip_utils import ZipExtractionError, expand_zip_file

_INDEX_NOT_FOUND_MESSAGE = "Index not found"
_INDEX_NOT_FOUND_HELP = (
    "Please verify the index name and project name. If you believe this index should exist, "
    "check your project configuration or contact support."
)
_APPLICATION_NOT_FOUND_MESSAGE = "Application not found"
_APPLICATION_NOT_FOUND_HELP = (
    "Please verify the application name and ensure it exists. If you believe this is an error, contact support."
)
_ACCESS_DENIED_MESSAGE = "Access denied"
_CHECK_PERMISSIONS_MESSAGE = "Please check your user permissions or contact an administrator for assistance."
_INDEX_EXISTS_MESSAGE = "Index already exists"
_INDEX_EXISTS_HELP = "Please choose a different name for your index or use the existing index."


@dataclass
class FileChanges:
    """Result of computing file changes for a datasource update."""

    removed_files: set[str]
    has_file_changes: bool


@dataclass
class PreparedFilesResult:
    """All file data ready to be forwarded to the update processor."""

    all_files_paths: list[FILE_PATH_DATA_NT] = field(default_factory=list)
    new_files_paths: list[FILE_PATH_DATA_NT] = field(default_factory=list)
    uploaded_files: list[str] = field(default_factory=list)


class FileDatasourceService:
    """Encapsulates business logic and repository communication for file-type datasources.

    All public methods are stateless and exposed as class-level static methods to match
    the existing service pattern used across the codebase (e.g. ``FileService``,
    ``IndexStatusService``).
    """

    @staticmethod
    def find_or_raise(project_name: str, name: str) -> IndexInfo:
        """Return the ``IndexInfo`` record or raise HTTP 404."""
        kb_index = KnowledgeBaseIndexInfo.filter_by_project_and_repo(project_name=project_name, repo_name=name)

        if not kb_index:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=_INDEX_NOT_FOUND_MESSAGE,
                details=f"The index with name '{name}' in project '{project_name}' could not be found.",
                help=_INDEX_NOT_FOUND_HELP,
            )
        return kb_index[0]

    @staticmethod
    def parse_uploaded_files(uploaded_files_str: str | None, index: IndexInfo) -> list[str]:
        """Return the list of filenames to keep after the update.

        If *uploaded_files_str* is ``None``, the existing ``index.uploaded_files`` list
        is used (i.e. all files are retained).
        """
        if uploaded_files_str is not None:
            try:
                return json.loads(uploaded_files_str)
            except (json.JSONDecodeError, ValueError, TypeError):
                raise ExtendedHTTPException(
                    code=status.HTTP_400_BAD_REQUEST,
                    message="Invalid uploaded_files parameter",
                    details="Failed to parse uploaded_files.",
                    help="Ensure uploaded_files is a valid JSON array of file name strings.",
                )
        return list(index.uploaded_files or [])

    @staticmethod
    def parse_guardrail_assignments(
        guardrail_assignments_str: str | None,
    ) -> list[GuardrailAssignmentItem] | None:
        """Parse a JSON-encoded guardrail assignments string, or return ``None``."""
        if not guardrail_assignments_str:
            return None
        try:
            assignments_list = json.loads(guardrail_assignments_str)
            return [GuardrailAssignmentItem.model_validate(item) for item in assignments_list]
        except (json.JSONDecodeError, ValueError, TypeError):
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Invalid guardrail_assignments parameter",
                details="Failed to parse guardrail_assignments.",
                help="Ensure guardrail_assignments is a valid JSON array.",
            )

    @staticmethod
    def compute_file_changes(
        new_files: list[UploadFile] | None,
        uploaded_files_to_keep: list[str],
        index: IndexInfo,
    ) -> FileChanges:
        """Determine which files were removed and whether any file-related changes exist."""
        db_uploaded_files = set(index.uploaded_files or [])
        removed_files = db_uploaded_files - set(uploaded_files_to_keep)
        has_file_changes = bool(removed_files) or bool(new_files)
        return FileChanges(removed_files=removed_files, has_file_changes=has_file_changes)

    @staticmethod
    def validate_project_change(
        new_project_name: str | None,
        current_project_name: str,
        repo_name: str,
        user: User,
    ) -> None:
        """Validate that a project transfer is permitted, if one is requested."""
        if not new_project_name or new_project_name == current_project_name:
            return

        try:
            Application.get_by_id(new_project_name)
        except (NotFoundError, KeyError) as e:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=_APPLICATION_NOT_FOUND_MESSAGE,
                details=f"The application with name '{new_project_name}' could not be found in the system.",
                help=_APPLICATION_NOT_FOUND_HELP,
            ) from e

        if not user.has_access_to_application(new_project_name):
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message=_ACCESS_DENIED_MESSAGE,
                details=f"You don't have permission for the project '{new_project_name}'.",
                help=_CHECK_PERMISSIONS_MESSAGE,
            )

        existing_index = IndexInfo.filter_by_project_and_repo(project_name=new_project_name, repo_name=repo_name)
        if existing_index:
            raise ExtendedHTTPException(
                code=status.HTTP_409_CONFLICT,
                message=_INDEX_EXISTS_MESSAGE,
                details=f"An index with the name '{repo_name}' already exists in the project '{new_project_name}'.",
                help=_INDEX_EXISTS_HELP,
            )

        logger.info(
            f"FileDatasourceService. ProjectChangeRequested. "
            f"Datasource={repo_name}. "
            f"From={current_project_name}. "
            f"To={new_project_name}. "
            f"UserId={user.id}"
        )

    @staticmethod
    def update_metadata_only(
        index: IndexInfo,
        user: User,
        description: str | None,
        project_space_visible: bool | None,
        new_project_name: str | None,
        guardrail_assignments: list[GuardrailAssignmentItem] | None,
    ) -> None:
        """Apply metadata-only changes that do not require file re-indexing."""
        updated_by = CreatedByUser(id=user.id, username=user.username, name=user.name)
        index.update_index(
            user=user,
            description=description,
            project_space_visible=project_space_visible,
            project_name=new_project_name,
            guardrail_assignments=guardrail_assignments,
            updated_by=updated_by,
        )

    @staticmethod
    def _write_extracted_file(
        name: str,
        content: bytes,
        user_id: str,
        file_repo,
    ) -> tuple[FILE_PATH_DATA_NT, str]:
        if name.split(".")[-1].lower() == IndexKnowledgeBaseFileTypes.JSON.value:
            _validate_json_file(name, content)
        mime, _ = mimetypes.guess_type(name)
        file_object = file_repo.write_file(
            name=name,
            mime_type=mime or "application/octet-stream",
            owner=user_id,
            content=content,
        )
        return FILE_PATH_DATA_NT(name=file_object.name, owner=file_object.owner), file_object.name

    @staticmethod
    def _write_extracted_files_with_rollback(
        extracted: list[tuple[str, bytes]],
        user_id: str,
        file_repo,
    ) -> tuple[list[FILE_PATH_DATA_NT], list[str]]:
        written: list[FILE_PATH_DATA_NT] = []
        paths: list[FILE_PATH_DATA_NT] = []
        filenames: list[str] = []
        try:
            for extracted_name, extracted_bytes in extracted:
                path, fname = FileDatasourceService._write_extracted_file(
                    extracted_name, extracted_bytes, user_id, file_repo
                )
                written.append(path)
                paths.append(path)
                filenames.append(fname)
        except Exception:
            for stored in written:
                try:
                    file_repo.delete_file(stored.name, stored.owner)
                except Exception as del_exc:
                    logger.warning(f"Failed to roll back uploaded file '{stored.name}': {del_exc}")
            raise
        return paths, filenames

    @staticmethod
    def _process_upload_file(
        file: UploadFile,
        user_id: str,
        file_repo,
    ) -> tuple[list[FILE_PATH_DATA_NT], list[str]]:
        """Write one uploaded file to storage, expanding ZIP archives if needed.

        Returns ``(paths, filenames)`` for the resulting stored file(s).
        """
        if not file.filename:
            raise ZipExtractionError(
                message="Uploaded file is missing a filename",
                detail="The multipart part did not include a filename.",
                help_text="Ensure the file upload includes a valid filename.",
            )
        content = file.file.read()
        paths: list[FILE_PATH_DATA_NT] = []
        filenames: list[str] = []

        if file.filename.split(".")[-1].lower() == IndexKnowledgeBaseFileTypes.ZIP.value:
            extracted = expand_zip_file(content)  # raises ZipExtractionError on bad/oversized/too-many-files
            if not extracted:
                raise ZipExtractionError(
                    message="ZIP archive contained no extractable files",
                    detail="The archive was empty or contained only directories or metadata entries.",
                    help_text="Ensure the archive contains at least one supported file and try again.",
                )
            paths, filenames = FileDatasourceService._write_extracted_files_with_rollback(extracted, user_id, file_repo)
        else:
            file_object = file_repo.write_file(
                name=file.filename,
                mime_type=file.headers.get("content-type", "application/octet-stream"),
                owner=user_id,
                content=content,
            )
            if file.filename.split(".")[-1].lower() == IndexKnowledgeBaseFileTypes.JSON.value:
                _validate_json_file(file.filename, content)
            paths.append(FILE_PATH_DATA_NT(name=file_object.name, owner=file_object.owner))
            filenames.append(file_object.name)

        return paths, filenames

    @staticmethod
    def upload_and_prepare_files(
        new_files: list[UploadFile],
        user: User,
        uploaded_files_to_keep: list[str],
        index: IndexInfo,
    ) -> PreparedFilesResult:
        """Upload new files to storage and assemble path lists for the update processor.

        Returns a :class:`PreparedFilesResult` that contains:
        - ``all_files_paths``  — paths for every file the processor should index
          (kept files + newly uploaded files).
        - ``new_files_paths``  — paths for newly uploaded files only (used by the
          processor to compute incremental progress counters).
        - ``uploaded_files``   — canonical filenames for the final ``uploaded_files``
          column on the ``IndexInfo`` record.
        """
        original_owner_id = index.created_by.id if index.created_by else user.id
        kept_paths = [FILE_PATH_DATA_NT(name=fname, owner=original_owner_id) for fname in uploaded_files_to_keep]

        file_repo = FileRepositoryFactory.get_current_repository()
        new_paths: list[FILE_PATH_DATA_NT] = []
        new_filenames: list[str] = []

        for file in new_files:
            file_paths, file_names = FileDatasourceService._process_upload_file(file, user.id, file_repo)
            new_paths.extend(file_paths)
            new_filenames.extend(file_names)

        return PreparedFilesResult(
            all_files_paths=kept_paths + new_paths,
            new_files_paths=new_paths,
            uploaded_files=list(uploaded_files_to_keep) + new_filenames,
        )


def _validate_json_file(filename: str, content: bytes) -> None:
    """Validate that a JSON file matches the expected ``[{content, metadata}]`` schema."""
    try:
        for doc in json.loads(content):
            if "content" not in doc:
                raise KeyError("missing 'content' key")
            if "metadata" not in doc:
                raise KeyError("missing 'metadata' key")
    except (json.JSONDecodeError, KeyError) as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="File has incorrect format",
            details=f"An error occurred while validating file {filename} datasource: {str(e)}",
            help="Please check provided data on form or contact an administrator for assistance.",
        ) from e
