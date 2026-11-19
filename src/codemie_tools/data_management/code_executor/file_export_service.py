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

from __future__ import annotations

import logging
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import List, Optional, Any

from llm_sandbox import SandboxSession

from codemie_tools.base.artifact_publishing import ArtifactPublisher, PublishContext
from codemie_tools.base.artifact_storage import publish_artifact
from codemie_tools.base.file_object import FileObject

logger = logging.getLogger(__name__)


class FileExportService:
    """
    Service for exporting files from sandbox environments.

    Handles file downloads from sandbox and publishing through the artifact publisher.
    """

    def __init__(
        self,
        user_id: str = "",
        publisher: ArtifactPublisher | None = None,
        publish_context: PublishContext | None = None,
    ):
        """
        Initialize file export service.

        Args:
            user_id: User ID for file ownership of collected files
            publisher: `ArtifactPublisher` that stores exported files
            publish_context: `PublishContext` the exported files are published under
        """
        self.user_id = user_id
        self.publisher = publisher
        self.publish_context = publish_context

    def export_files_from_execution(
        self, session: SandboxSession, file_paths: Optional[List[str]], workdir: str
    ) -> List[str]:
        """
        Export files from the execution environment and publish them through `publisher`.

        Args:
            session: The active execution session
            file_paths: List of paths to export from the execution environment
            workdir: The user-specific working directory

        Returns:
            List of URLs for the stored files
        """
        if file_paths is None:
            return []

        urls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            for i, src_path in enumerate(file_paths, 1):
                url = self._export_single_file(session, src_path, workdir, temp_dir, i)
                if url:
                    urls.append(url)

        return urls

    def collect_files_from_execution(
        self, session: SandboxSession, file_paths: Optional[List[str]], workdir: str
    ) -> List[FileObject]:
        """Collect files from the execution environment into in-memory FileObjects."""
        if file_paths is None:
            return []

        collected_files: List[FileObject] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            for i, src_path in enumerate(file_paths, 1):
                file_object = self._collect_single_file(session, src_path, workdir, temp_dir, i)
                if file_object:
                    collected_files.append(file_object)

        return collected_files

    def store_exported_bytes(self, filename: str, content: bytes) -> Optional[str]:
        """Publish already-pulled bytes and return the result line for the model.

        Shared kernel for any execution backend that delivers file bytes itself
        (e.g. sandbox-jobs exec-tar), bypassing the SandboxSession copy_from_runtime path.
        A refused file returns its reason, so it reaches the user instead of vanishing into the log.
        """
        return self._publish_line(filename, content=content)

    def _publish_line(self, filename: str, **payload: Any) -> str | None:
        result = publish_artifact(self.publisher, self.publish_context, filename, **payload)
        if not result.ok:
            logger.error(f"Failed to store exported file {filename}: {result.reason}")
            return f" File '{result.name}' was not stored: {result.reason}"
        return f" File '{result.name}', URL `{result.link}`"

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

            session.copy_from_runtime(f"{workdir}/{src_path}", temp_file_path)

            # Published by path: the publisher checks the on-disk size before reading the file.
            return self._publish_line(filename, path=temp_file_path)

        except Exception as e:
            logger.error(f"Failed to export file {src_path}: {e}")
            return None

    def _collect_single_file(
        self,
        session: SandboxSession,
        src_path: str,
        workdir: str,
        temp_dir: str,
        index: int,
    ) -> FileObject | None:
        if not self.user_id:
            logger.warning("Cannot collect file %s: no owner to record it under", src_path)
            return None
        try:
            normalized_path = Path(src_path).as_posix()
            filename = os.path.basename(normalized_path) or f"file_{index}"
            temp_file_path = os.path.join(temp_dir, f"{index}_{filename}")

            session.copy_from_runtime(f"{workdir}/{normalized_path}", temp_file_path)

            extension = Path(normalized_path).suffix.lower().lstrip(".")
            mime_type = self._determine_mime_type(extension)

            with open(temp_file_path, "rb") as f:
                content = f.read()

            return FileObject(
                name=normalized_path,
                path=normalized_path,
                mime_type=mime_type,
                owner=self.user_id,
                content=content,
            )
        except Exception as e:
            logger.error(f"Failed to collect file {src_path}: {e}")
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
