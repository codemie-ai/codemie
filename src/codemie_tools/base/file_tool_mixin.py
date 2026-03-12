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

"""Mixin for tools that need file support."""

import logging
from typing import Dict, Tuple, Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from codemie_tools.base.file_object import FileObject

logger = logging.getLogger(__name__)


class FileToolMixin:
    """Mixin for tools that work with files via config.input_files."""

    def _get_supported_mime_types(self) -> Optional[List[str]]:
        """
        Get list of supported mime types for this tool.

        Override this method in subclasses to specify supported mime types.
        If None is returned, all files are accepted.

        Returns:
            List of supported mime types or None to accept all files
        """
        return None

    def _get_supported_extensions(self) -> Optional[List[str]]:
        """
        Get list of supported file extensions for this tool.

        Override this method in subclasses to specify supported file extensions.
        Extensions should include the dot (e.g., '.pdf', '.docx').
        This is used as a fallback when MIME type detection fails.

        Returns:
            List of supported file extensions or None
        """
        return None

    def _is_supported_file(self, file_obj: 'FileObject') -> bool:
        """
        Check if a file is supported by this tool based on mime type or file extension.

        First checks MIME type, then falls back to file extension if MIME type doesn't match.

        Args:
            file_obj: FileObject to check

        Returns:
            True if file is supported, False otherwise
        """
        supported_types = self._get_supported_mime_types()

        # If no specific types defined, accept all files
        if supported_types is None:
            return True

        # Check if file's mime type matches any supported type
        if file_obj.mime_type in supported_types:
            return True

        # Fallback: check file extension
        supported_extensions = self._get_supported_extensions()
        if supported_extensions:
            file_name_lower = file_obj.name.lower()
            return any(file_name_lower.endswith(ext.lower()) for ext in supported_extensions)

        return False

    def _get_supported_files(self) -> List['FileObject']:
        """
        Get list of FileObject instances that are supported by this tool.

        Filters files from config.input_files based on supported mime types.

        Returns:
            List of supported FileObject instances
        """
        input_files = []
        if hasattr(self, 'config') and self.config and hasattr(self.config, 'input_files'):
            input_files = self.config.input_files or []

        logger.debug(f"Filtering files from config. Total files available: {len(input_files)}")

        supported_files = []
        for file_obj in input_files:
            if self._is_supported_file(file_obj):
                supported_files.append(file_obj)
                logger.debug(
                    f"File '{file_obj.name}' (type: {file_obj.mime_type}) is supported by {self.__class__.__name__}"
                )
            else:
                logger.debug(
                    f"File '{file_obj.name}' (type: {file_obj.mime_type}) not supported by {self.__class__.__name__}, skipping"
                )
        return supported_files

    def _resolve_files(self) -> Dict[str, Tuple[bytes, str]]:
        """
        Get files from config, filter by supported types, and return name->(content, mime_type) mapping.

        Returns:
            Dictionary mapping file names to tuples of (content, mime_type)
        """
        supported_files = self._get_supported_files()
        logger.debug(f"Resolving {len(supported_files)} supported files")

        result = {}
        for file_obj in supported_files:
            try:
                logger.debug(
                    f"Processing file: {file_obj.name}, type: {type(file_obj)}, mime_type: {file_obj.mime_type}"
                )
                content = file_obj.bytes_content()
                logger.debug(f"File '{file_obj.name}' content retrieved: {len(content) if content else 0} bytes")

                if content:
                    result[file_obj.name] = (content, file_obj.mime_type)
                else:
                    logger.warning(f"File '{file_obj.name}' has no content, excluding from processing")
            except Exception as e:
                logger.warning(
                    f"Failed to get content for file '{file_obj.name}': {e}, excluding from processing", exc_info=True
                )

        logger.debug(f"Successfully resolved {len(result)} files: {list(result.keys())}")
        return result

    def _filter_requested_files(
        self, all_files: Dict[str, Tuple[bytes, str]], params_dict: dict
    ) -> Dict[str, Tuple[bytes, str]]:
        """
        Filter files based on file names specified in params.

        Args:
            all_files: Dictionary of all available files
            params_dict: Parsed params dict that may contain file names

        Returns:
            Filtered dictionary of requested files, or all files if no specific files requested
        """
        logger.debug(
            f"Filtering requested files. Available files: {list(all_files.keys())}, params_dict: {params_dict}"
        )

        requested_file_names = []
        if "file" in params_dict:
            file_param = params_dict["file"]
            requested_file_names = file_param if isinstance(file_param, list) else [file_param]
        elif "files" in params_dict:
            files_param = params_dict["files"]
            requested_file_names = files_param if isinstance(files_param, list) else [files_param]

        logger.debug(f"Requested file names from params: {requested_file_names}")

        if not requested_file_names:
            logger.debug("No specific files requested, returning all files")
            return all_files

        filtered_files = {}
        for file_name in requested_file_names:
            if file_name in all_files:
                filtered_files[file_name] = all_files[file_name]
                logger.debug(f"Matched requested file: {file_name}")
            else:
                logger.warning(f"Requested file '{file_name}' not found in available files: {list(all_files.keys())}")

        result = filtered_files if filtered_files else all_files
        logger.debug(f"Filtered result: {len(result)} files - {list(result.keys())}")
        return result
