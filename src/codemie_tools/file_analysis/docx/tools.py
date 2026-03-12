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

import logging
from typing import Optional, Type, Any, List, Union, Dict

from pydantic import BaseModel

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.file_tool_mixin import FileToolMixin
from codemie_tools.file_analysis.models import FileAnalysisConfig
from codemie_tools.file_analysis.docx.exceptions import (
    DocxProcessingError,
    InvalidPageSelectionError,
)
from codemie_tools.file_analysis.docx.models import DocxToolInput, QueryType
from codemie_tools.file_analysis.docx.processor import DocxProcessor
from codemie_tools.file_analysis.tool_vars import DOCX_TOOL

# Configure logger
logger = logging.getLogger(__name__)


class DocxTool(CodeMieTool, FileToolMixin):
    """
    A tool for processing DOCX documents.

    Provides comprehensive capabilities for extracting text and analyzing content.
    """

    # Registration metadata
    name: str = DOCX_TOOL.name
    label: str = DOCX_TOOL.label
    description: str = DOCX_TOOL.description

    # Pydantic model for tool arguments
    args_schema: Type[BaseModel] = DocxToolInput

    # Dependencies
    config: FileAnalysisConfig
    docx_processor: Optional[DocxProcessor] = None

    def __init__(self, config: FileAnalysisConfig) -> None:
        """
        Initialize the DocxTool with configuration containing DOCX files.

        Args:
            config: FileAnalysisConfig with input_files and optional chat_model
        """
        super().__init__(config=config)
        self.docx_processor = DocxProcessor(ocr_enabled=True, chat_model=config.chat_model)

    def _get_supported_mime_types(self) -> Optional[List[str]]:
        """
        Get list of supported mime types for DOCX processing.

        Returns:
            List of DOCX mime types
        """
        return ['application/vnd.openxmlformats-officedocument.wordprocessingml.document']

    def _get_supported_extensions(self) -> Optional[List[str]]:
        """
        Get list of supported file extensions for DOCX processing.

        Returns:
            List of DOCX file extensions
        """
        return ['.docx']

    def _handle_direct_processor_queries(
        self,
        query: QueryType,
        pages: Optional[str],
        instructions: Optional[str],
    ) -> Union[str, List[Dict[str, Any]]]:
        """Handle queries that can be processed directly by the processor."""
        method = None
        if query == QueryType.TEXT:
            method = "read"
        elif query == QueryType.IMAGE_EXTRACTION:
            method = "extract_images"
        elif query == QueryType.TABLE_EXTRACTION:
            method = "extract_tables"
        elif query == QueryType.SUMMARY:
            method = "summary"

        files = self._get_supported_files()
        return self.docx_processor.process_multiple_files(
            files,
            method,
            pages=pages,
            instructions=instructions,
        )

    def _handle_metadata_queries(self, query: QueryType, pages: Optional[str]) -> List[Dict[str, Any]]:
        """Handle queries that need metadata from the document."""
        result = []
        files = self._get_supported_files()

        for file_obj in files:
            content = self.docx_processor.read_document_from_bytes(file_obj.content, file_obj.name, query, pages)
            # Use Any type for dictionary values to allow different types
            file_result: Dict[str, Any] = {"file_name": file_obj.name, "text": content.text}

            if query == QueryType.TEXT_WITH_METADATA:
                file_result["metadata"] = content.metadata
            elif query == QueryType.TEXT_WITH_IMAGES:
                image_texts = [img.text_content for img in content.images if img.text_content]
                file_result["image_texts"] = image_texts
            elif query == QueryType.STRUCTURE_ONLY:
                structure_dict = self._structure_to_dict(content.structure) if content.structure else {}
                file_result["structure"] = structure_dict

            result.append(file_result)

        return result

    def _handle_analyze_query(self, pages: Optional[str], instructions: Optional[str]) -> List[Dict[str, Any]]:
        """Handle analytical queries that require the chat model."""
        if not instructions:
            instructions = "Provide a comprehensive analysis"

        analysis_results = []
        files = self._get_supported_files()

        for file_obj in files:
            content = self.docx_processor.read_document_from_bytes(
                file_obj.content, file_obj.name, QueryType.ANALYZE, pages
            )
            analysis = self.docx_processor.analyze_content(content, instructions=instructions)
            analysis_results.append(
                {
                    "file_name": file_obj.name,
                    "summary": analysis.summary,
                    "key_topics": analysis.key_topics,
                    "sentiment": analysis.sentiment,
                    "language": analysis.language,
                    "readability_score": analysis.readability_score,
                    "pages_analyzed": content.metadata.get("filtered_pages", "all"),
                }
            )
        return analysis_results

    def execute(
        self,
        query: QueryType,
        instructions: Optional[str] = None,
        pages: Optional[str] = None,
    ) -> Union[str, Dict[str, Any], List[Dict[str, Any]], bytes]:
        """
        Process DOCX documents based on the provided query and parameters.

        Args:
            query: The type of operation to perform:
                - "text": Extract plain text content
                - "text_with_metadata": Extract text with metadata
                - "text_with_images": Extract text including OCR from images
                - "structure_only": Extract only document structure
                - "image_extraction": Extract images from the document
                - "table_extraction": Extract tables from the document
                - "summary": Generate a document summary
                - "analyze": Perform comprehensive document analysis
            instructions: Natural language instructions for document operations
            pages: Page selection string (e.g., "1,3,5-8") to process specific pages

        Returns:
            Operation result (varies by query type)

        Raises:
            ValueError: If required parameters are missing
            DocxProcessingError: If document processing fails
        """
        files = self.config.input_files or []

        if not files:
            raise ValueError(f"{self.name} requires at least one file to process.")

        logger.info(f"Processing {len(files)} DOCX files with query type: {query}")

        # Normalize 'all' to None for consistent handling
        if pages and pages.strip().lower() == "all":
            pages = None

        if pages:
            logger.info(f"Processing specific pages: {pages}")

        try:
            # Handle queries using direct processor method calls
            if query in [
                QueryType.TEXT,
                QueryType.IMAGE_EXTRACTION,
                QueryType.TABLE_EXTRACTION,
                QueryType.SUMMARY,
            ]:
                return self._handle_direct_processor_queries(query, pages, instructions)

            # Handle metadata and structure queries
            elif query in [
                QueryType.TEXT_WITH_METADATA,
                QueryType.TEXT_WITH_IMAGES,
                QueryType.STRUCTURE_ONLY,
            ]:
                return self._handle_metadata_queries(query, pages)

            # Handle analysis query
            elif query == QueryType.ANALYZE:
                return self._handle_analyze_query(pages, instructions)

            # Handle unknown query type
            raise ValueError(f"Unknown query type: {query}")

        except ValueError as e:
            # Pass through value errors for input validation
            logger.error(f"Input validation error: {str(e)}")
            raise

        except InvalidPageSelectionError as e:
            # Pass through page selection errors
            logger.error(f"Page selection error: {str(e)}")
            raise

        except Exception as e:
            # Convert other exceptions to DocxProcessingError
            logger.error(f"Error processing DOCX: {str(e)}")
            raise DocxProcessingError(f"Failed to process DOCX document(s): {str(e)}") from e

    @staticmethod
    def _structure_to_dict(structure) -> Dict[str, Any]:
        """
        Convert document structure to a dictionary representation.

        Args:
            structure: DocumentStructure object

        Returns:
            Dictionary representation of the structure
        """
        # Convert headers
        headers = []
        if hasattr(structure, "headers"):
            for header in structure.headers:
                headers.append(
                    {
                        "level": header.level,
                        "text": header.text,
                        "position": {
                            "page": header.position.page,
                            "x": header.position.x,
                            "y": header.position.y,
                        },
                    }
                )

        # Convert sections
        sections = []
        if hasattr(structure, "sections"):
            for section in structure.sections:
                sections.append(
                    {
                        "title": section.title,
                        "level": section.level,
                        "content_length": len(section.content),
                    }
                )

        return {
            "headers": headers,
            "sections": sections,
            "paragraph_count": len(structure.paragraphs) if hasattr(structure, "paragraphs") else 0,
            "style_count": len(structure.styles) if hasattr(structure, "styles") else 0,
        }
