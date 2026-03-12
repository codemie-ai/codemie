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

"""
File export service for sandbox execution.

This module provides services for exporting files from sandbox environments
back to the file repository with proper MIME type detection.
"""

import logging
import mimetypes
import os
import tempfile
import uuid
from pathlib import Path
from typing import List, Optional, Any

from llm_sandbox import SandboxSession

logger = logging.getLogger(__name__)


class FileExportService:
    """
    Service for exporting files from sandbox environments.

    Handles file downloads from sandbox and storage in file repository.
    """

    def __init__(self, file_repository: Optional[Any] = None, user_id: str = ""):
        """
        Initialize file export service.

        Args:
            file_repository: Repository for storing exported files
            user_id: User ID for file ownership
        """
        self.file_repository = file_repository
        self.user_id = user_id

    def export_files_from_execution(
        self, session: SandboxSession, file_paths: Optional[List[str]], workdir: str
    ) -> List[str]:
        """
        Export files from the execution environment and store them using file_repository.

        Args:
            session: The active execution session
            file_paths: List of paths to export from the execution environment
            workdir: The user-specific working directory

        Returns:
            List of URLs for the stored files
        """
        if not self.file_repository:
            logger.warning("Cannot export files: file_repository not available")
            return []

        if file_paths is None:
            return []

        urls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            for i, src_path in enumerate(file_paths, 1):
                url = self._export_single_file(session, src_path, workdir, temp_dir, i)
                if url:
                    urls.append(url)

        return urls

    def _export_single_file(
        self, session: SandboxSession, src_path: str, workdir: str, temp_dir: str, index: int
    ) -> Optional[str]:
        """
        Export a single file from sandbox.

        Args:
            session: Active sandbox session
            src_path: Source path in sandbox
            workdir: Working directory in sandbox
            temp_dir: Temporary directory on host
            index: File index for default naming

        Returns:
            URL string if successful, None if failed
        """
        try:
            filename = os.path.basename(src_path) or f"file_{index}"
            temp_file_path = os.path.join(temp_dir, filename)

            # Copy file from sandbox to host
            session.copy_from_runtime(f"{workdir}/{src_path}", temp_file_path)

            # Determine MIME type and read content
            extension = Path(src_path).suffix.lower().lstrip(".")
            mime_type = self._determine_mime_type(extension)

            with open(temp_file_path, "rb") as f:
                content = f.read()

            # Store file in repository
            unique_filename = f"{uuid.uuid4()}_{filename}"
            stored_file = self.file_repository.write_file(
                name=unique_filename,
                mime_type=mime_type,
                content=content,
                owner=self.user_id,
            )

            url = f" File '{filename}', URL `sandbox:/v1/files/{stored_file.to_encoded_url()}`"
            return url

        except Exception as e:
            logger.error(f"Failed to export file {src_path}: {e}")
            return None

    @staticmethod
    def _determine_mime_type(extension: str) -> str:
        """
        Determine the MIME type based on the file extension.

        Args:
            extension: The file extension (without leading dot)

        Returns:
            The MIME type string, defaults to 'application/octet-stream' if unknown
        """
        filename = f"file.{extension}" if not extension.startswith(".") else f"file{extension}"
        mime_type, _ = mimetypes.guess_type(filename)
        return mime_type or "application/octet-stream"
