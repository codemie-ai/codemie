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
File upload service for sandbox execution.

This module provides services for uploading files to sandbox environments
with parallel processing capabilities for improved performance.
"""

import logging
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Any, Optional

from langchain_core.tools import ToolException
from llm_sandbox import SandboxSession

from codemie_tools.base.file_object import FileObject

logger = logging.getLogger(__name__)


class FileUploadService:
    """
    Service for uploading files to sandbox environments.

    Handles file downloads, parallel uploads, and error management.
    """

    def __init__(self, file_repository: Optional[Any] = None):
        """
        Initialize file upload service.

        Args:
            file_repository: Repository for reading file contents
        """
        self.file_repository = file_repository

    def upload_files_to_sandbox(self, session: SandboxSession, file_objects: List[FileObject], workdir: str) -> None:
        """
        Upload files from file repository to the sandbox environment.

        Files are uploaded to the user's working directory in the sandbox,
        making them available for code execution by their original filenames.

        Performance optimization: Files are uploaded in parallel (max 3 concurrent uploads)
        to reduce total upload time.

        Args:
            session: Active sandbox session
            file_objects: List of FileObject instances to upload
            workdir: Working directory in the sandbox

        Raises:
            ToolException: If file upload fails or file repository is not available
        """
        if not self.file_repository:
            raise ToolException("Cannot upload files: file_repository not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            self._download_files_to_temp(file_objects, temp_dir)
            self._upload_files_parallel(session, file_objects, temp_dir, workdir)

    def _download_files_to_temp(self, file_objects: List[FileObject], temp_dir: str) -> None:
        """
        Download all files to temporary directory.

        Args:
            file_objects: List of files to download
            temp_dir: Temporary directory path

        Raises:
            ToolException: If download fails
        """
        for file_obj in file_objects:
            try:
                temp_file_path = os.path.join(temp_dir, file_obj.name)
                with open(temp_file_path, "wb") as f:
                    f.write(
                        self.file_repository.read_file(
                            file_name=file_obj.name,
                            owner=file_obj.owner,
                            mime_type=file_obj.mime_type,
                        ).bytes_content()
                    )

            except Exception as e:
                logger.error(f"Failed to download file {file_obj.name}: {e}")
                raise ToolException(f"Failed to download file {file_obj.name}: {str(e)}")

    def _upload_files_parallel(
        self, session: SandboxSession, file_objects: List[FileObject], temp_dir: str, workdir: str
    ) -> None:
        """
        Upload files to sandbox in parallel for improved performance.

        Uses ThreadPoolExecutor to upload multiple files concurrently (max 3 workers).

        Args:
            session: Active sandbox session
            file_objects: List of FileObject instances to upload
            temp_dir: Temporary directory containing downloaded files
            workdir: Working directory in the sandbox

        Raises:
            ToolException: If any file upload fails
        """
        start_time = time.time()
        max_workers = min(3, len(file_objects))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._upload_single_file, session, file_obj, temp_dir, workdir): file_obj
                for file_obj in file_objects
            }

            failed_uploads = []
            for future in as_completed(futures):
                _, success, error_msg = future.result()
                if not success:
                    failed_uploads.append(error_msg)

            if failed_uploads:
                error_details = "\n".join(failed_uploads)
                raise ToolException(f"Failed to upload {len(failed_uploads)} file(s):\n{error_details}")

        total_elapsed = time.time() - start_time
        file_names = [f.name for f in file_objects]
        logger.debug(f"Uploaded {len(file_objects)} file(s) in {total_elapsed:.2f}s: {file_names}")

    @staticmethod
    def _upload_single_file(session: SandboxSession, file_obj: FileObject, temp_dir: str, workdir: str) -> tuple:
        """
        Upload a single file to the sandbox.

        Args:
            session: Active sandbox session
            file_obj: File object to upload
            temp_dir: Temporary directory with file
            workdir: Working directory in sandbox

        Returns:
            Tuple of (filename, success, error_message)
        """
        try:
            temp_file_path = os.path.join(temp_dir, file_obj.name)
            dest_file_path = f"{workdir}/{file_obj.name}"

            session.copy_to_runtime(temp_file_path, dest_file_path)

            return (file_obj.name, True, None)

        except Exception as e:
            error_msg = f"Failed to upload {file_obj.name}: {str(e)}"
            logger.error(error_msg)
            return (file_obj.name, False, error_msg)
