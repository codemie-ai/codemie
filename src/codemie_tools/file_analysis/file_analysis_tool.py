# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the “License”);
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
from typing import Type, Optional, Set

from pydantic import BaseModel, Field

from codemie.datasource.loader.file_processor_pool import maybe_pool_submit
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.constants import SOURCE_DOCUMENT_KEY, SOURCE_FIELD_KEY, FILE_CONTENT_FIELD_KEY
from codemie_tools.base.file_object import FileObject
from codemie_tools.base.file_tool_mixin import FileToolMixin
from codemie_tools.file_analysis.models import FileAnalysisConfig
from codemie_tools.file_analysis.tool_vars import FILE_ANALYSIS_TOOL
from codemie_tools.file_analysis.workers.markdown_workers import convert_file_to_markdown

logger = logging.getLogger(__name__)


class FileAnalysisToolInput(BaseModel):
    query: str = Field(default="", description="""User initial request should be passed as a string.""")


class FileAnalysisTool(CodeMieTool, FileToolMixin):
    """Tool for working with and analyzing file contents."""

    args_schema: Optional[Type[BaseModel]] = FileAnalysisToolInput
    name: str = FILE_ANALYSIS_TOOL.name
    label: str = FILE_ANALYSIS_TOOL.label
    description: str = FILE_ANALYSIS_TOOL.description
    config: FileAnalysisConfig
    tokens_size_limit: int = 100_000

    def __init__(self, config: FileAnalysisConfig) -> None:
        """
        Initialize the FileAnalysisTool with configuration containing files.

        Args:
            config: FileAnalysisConfig with input_files and optional chat_model
        """
        super().__init__(config=config)

    @staticmethod
    def _get_specialized_tools_signatures() -> tuple[Set[str], Set[str]]:
        """
        Collect MIME types and extensions from all specialized file tools.

        Calls class methods directly without instantiating tools.

        Uses the centralized SPECIALIZED_TOOL_CLASSES list from toolkit module.
        When a new specialized tool is added to that list, FileAnalysisTool
        automatically excludes its supported file types.

        Returns:
            Tuple of (mime_types_set, extensions_set) from specialized tools
        """
        from codemie_tools.file_analysis.toolkit import SPECIALIZED_TOOL_CLASSES

        specialized_mime_types = set()
        specialized_extensions = set()

        try:
            for tool_class in SPECIALIZED_TOOL_CLASSES:
                try:
                    # Call class methods directly - no need to instantiate!
                    # These methods don't need instance state
                    mime_types = tool_class._get_supported_mime_types(None)
                    if mime_types:
                        specialized_mime_types.update(mime_types)

                    extensions = tool_class._get_supported_extensions(None)
                    if extensions:
                        specialized_extensions.update(ext.lower() for ext in extensions)

                except Exception as e:
                    logger.warning(f"Failed to query {tool_class.__name__} capabilities: {e}")

        except ImportError as e:
            logger.error(f"Failed to import SPECIALIZED_TOOL_CLASSES: {e}")

        logger.debug(
            f"Collected specialized tool signatures: {len(specialized_mime_types)} MIME types, {len(specialized_extensions)} extensions"
        )

        return specialized_mime_types, specialized_extensions

    def _is_supported_file(self, file_obj: FileObject) -> bool:
        """
        Check if file should be processed by FileAnalysisTool.

        Only accepts files that are NOT supported by specialized tools.

        Args:
            file_obj: FileObject to check

        Returns:
            True if file is not supported by specialized tools, False otherwise
        """
        specialized_mime_types, specialized_extensions = self._get_specialized_tools_signatures()

        # Check if MIME type is handled by specialized tool
        if file_obj.mime_type in specialized_mime_types:
            logger.debug(
                f"File '{file_obj.name}' (type: {file_obj.mime_type}) "
                f"is supported by specialized tools, skipping in FileAnalysisTool"
            )
            return False

        # Check if file extension is handled by specialized tool
        file_name_lower = file_obj.name.lower()
        for ext in specialized_extensions:
            if file_name_lower.endswith(ext):
                return False

        # File is not supported by any specialized tool
        logger.debug(
            f"File '{file_obj.name}' (type: {file_obj.mime_type}) "
            f"not supported by specialized tools, will be handled by FileAnalysisTool"
        )
        return True

    @staticmethod
    def _fallback_decode_text_file(file_object: FileObject, original_exception: Exception = None) -> str:
        """
        Private fallback method to decode text files when markitdown fails
        :param file_object: The FileObject to process
        :param original_exception: The original exception from markitdown (if any)

        :return: file content as string or error message
        """
        if file_object.is_text_based():
            try:
                return file_object.string_content()
            except Exception as inner_e:
                return f"Failed to decode file: {str(inner_e)}"

        error_msg = "File type not supported for direct decoding"
        if original_exception:
            error_msg += f". Original error: {str(original_exception)}"
        return error_msg

    def _process_single_file(self, file_object: FileObject) -> str:
        """Process a single file and return its content as markdown text"""
        if file_object.name in self.config.preconverted_content:
            return self.config.preconverted_content[file_object.name]
        try:
            # Use process pool if enabled, otherwise process inline
            return maybe_pool_submit(
                convert_file_to_markdown,
                file_object.bytes_content(),
                file_object.name,
            )
        except FileNotFoundError as e:
            return f"File not found: {str(e)}"
        except Exception as e:
            return self._fallback_decode_text_file(file_object, original_exception=e)

    def execute(self, query: str = ""):
        # Get supported files from config (automatically filters by mime type)
        files = self._get_supported_files()

        if not files:
            raise ValueError(f"{self.name} requires at least one file to process.")

        # Process multiple files with LLM-friendly separators
        result = []
        for file_object in files:
            file_content = self._process_single_file(file_object)
            # Add file header with metadata
            logger.debug(file_object)
            result.append(f"\n{SOURCE_DOCUMENT_KEY}\n")
            result.append(f"{SOURCE_FIELD_KEY} {file_object.name}\n")
            result.append(f"{FILE_CONTENT_FIELD_KEY}\n{file_content}\n")

        return "\n".join(result)
