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
from typing import Optional, List, Dict, Any, Union, Set

from langchain_core.language_models import BaseChatModel

from codemie_tools.base.file_object import FileObject
from codemie_tools.file_analysis.docx.analyzer import DocxAnalyzer
from codemie_tools.file_analysis.docx.exceptions import (
    DocumentReadError,
    ExtractionError,
    InvalidPageSelectionError,
)
from codemie_tools.file_analysis.docx.models import (
    DocumentContent,
    AnalysisResult,
    AnalysisType,
    QueryType,
)
from codemie_tools.file_analysis.docx.reader import DocxReader

logger = logging.getLogger(__name__)


class DocxProcessor:
    """
    Core processor for DOCX documents.

    Orchestrates the reading and analysis of DOCX files.
    """

    def __init__(self, ocr_enabled: bool = True, chat_model: Optional[BaseChatModel] = None):
        """
        Initialize the DocxProcessor with dependencies.

        Args:
            ocr_enabled: Whether OCR is enabled for image processing
            chat_model: LangChain chat model for AI-powered operations
        """
        self.ocr_enabled = ocr_enabled
        self.chat_model = chat_model

        # Initialize components
        self.reader = DocxReader(ocr_enabled=self.ocr_enabled, chat_model=self.chat_model)
        self.analyzer = DocxAnalyzer(chat_model=self.chat_model)

        logger.debug(f"Initialized DocxProcessor with OCR enabled: {self.ocr_enabled}")

    def read_document(self, file_path: str, query: QueryType, pages: Optional[str] = None) -> DocumentContent:
        """
        Read and parse a DOCX file with complete content extraction.

        Args:
            file_path: Path to the DOCX file
            query: Query type to control what content is extracted (QueryType enum)
            pages: Specific pages to extract. Format: "1" or "1,3,5" or "1-4" or "all"

        Returns:
            DocumentContent object with structured document data

        Raises:
            DocumentReadError: If the document cannot be read
            InvalidPageSelectionError: If the page selection format is invalid
        """
        try:
            logger.info(f"Reading document: {file_path}, Query: {query}")
            content = self.reader.read_with_markitdown(file_path, query=query)

            if pages and pages.lower() != "all":
                return self._filter_content_by_pages(content, pages)
            return content
        except InvalidPageSelectionError as e:
            # Re-raise without wrapping
            raise e
        except Exception as e:
            logger.error(f"Error reading document {file_path}: {str(e)}")
            raise DocumentReadError(f"Failed to read document: {str(e)}") from e

    def read_document_from_bytes(
        self,
        content: bytes,
        file_name: str,
        query: QueryType,
        pages: Optional[str] = None,
    ) -> DocumentContent:
        """
        Read and parse a DOCX file from bytes content.

        Args:
            content: DOCX file content as bytes
            file_name: Name of the file (for reference)
            query: Query type to control what content is extracted (QueryType enum)
            pages: Specific pages to extract. Format: "1" or "1,3,5" or "1-4" or "all"

        Returns:
            DocumentContent object with structured document data

        Raises:
            DocumentReadError: If the document cannot be read
            InvalidPageSelectionError: If the page selection format is invalid
        """
        try:
            logger.info(f"Reading document from bytes: {file_name}, Query: {query}")
            doc_content = self.reader.read_from_bytes(content, file_name, query=query)

            if pages and pages.lower() != "all":
                return self._filter_content_by_pages(doc_content, pages)
            return doc_content
        except InvalidPageSelectionError as e:
            # Re-raise without wrapping
            raise e
        except Exception as e:
            logger.error(f"Error reading document from bytes {file_name}: {str(e)}")
            raise DocumentReadError(f"Failed to read document from bytes: {str(e)}") from e

    def analyze_content(
        self,
        content: DocumentContent,
        analysis_type: str = "full",
        instructions: Optional[str] = None,
    ) -> AnalysisResult:
        """
        Perform AI-powered content analysis on document content.

        Args:
            content: DocumentContent object to analyze
            analysis_type: Type of analysis to perform ("text", "images", "structure", "full")
            instructions: Optional specific instructions to guide the analysis (e.g., "Focus on financial data" or "Identify key arguments")

        Returns:
            AnalysisResult with insights and metadata

        Raises:
            AnalysisError: If the analysis fails
        """
        logger.info(f"Analyzing document content with type: {analysis_type}, instructions: {instructions}")

        if analysis_type == AnalysisType.TEXT:
            return self.analyzer.analyze_text(content.text, instructions=instructions)
        elif analysis_type == AnalysisType.IMAGES:
            return self.analyzer.analyze_images(content.images, instructions=instructions)
        elif analysis_type == AnalysisType.STRUCTURE:
            return self.analyzer.analyze_structure(content.structure, instructions=instructions)
        else:  # Full analysis
            return self.analyzer.analyze_content(content, instructions=instructions)

    def extract_images(self, file_path: str, include_ocr: bool = True) -> List[Dict[str, Any]]:
        """
        Extract all images from a document with optional OCR processing.

        Args:
            file_path: Path to the DOCX file
            include_ocr: Whether to perform OCR on images

        Returns:
            List of extracted image data

        Raises:
            ExtractionError: If image extraction fails
        """
        try:
            logger.info(f"Extracting images from document: {file_path}")
            images = self.reader.extract_images(file_path, include_ocr)
            return [self._image_data_to_dict(img) for img in images]
        except Exception as e:
            logger.error(f"Error extracting images: {str(e)}")
            raise ExtractionError(f"Failed to extract images: {str(e)}") from e

    def process_multiple_files(
        self, files: List[FileObject], operation: str, **kwargs
    ) -> Union[str, Dict[str, Any], List[Dict[str, Any]], bytes]:
        """
        Process multiple DOCX files with the specified operation.

        Args:
            files: List of FileObject instances containing DOCX content
            operation: Operation to perform on the files
            **kwargs: Additional operation parameters

        Returns:
            Operation result (varies by operation type)
        """
        if not files:
            raise ValueError("No files provided for processing")

        logger.info(f"Processing {len(files)} files with operation: {operation}")

        # If only one file, process it directly
        if len(files) == 1:
            return self._process_single_file(files[0], operation, **kwargs)

        # Process multiple files
        results = []
        for idx, file_obj in enumerate(files):
            logger.info(f"Processing file {idx + 1}/{len(files)}: {file_obj.name}")
            result = self._process_single_file(file_obj, operation, **kwargs)

            # Wrap result with file information
            if isinstance(result, str):
                results.append({"file_name": file_obj.name, "content": result})
            else:
                results.append({"file_name": file_obj.name, "result": result})

        return results

    def _process_single_file(
        self, file_obj: FileObject, operation: str, **kwargs
    ) -> list[dict[str, Any]] | str | AnalysisResult:
        """
        Process a single DOCX file with the specified operation.

        Args:
            file_obj: FileObject containing DOCX content
            operation: Operation to perform on the file
            **kwargs: Additional operation parameters

        Returns:
            Operation result (varies by operation type)
        """
        pages = kwargs.get("pages", None)

        # Determine query type based on operation
        query_map = {
            "read": QueryType.TEXT,
            "analyze": QueryType.ANALYZE,
            "extract_images": QueryType.IMAGE_EXTRACTION,
            "extract_tables": QueryType.TABLE_EXTRACTION,
            "summary": QueryType.SUMMARY,
        }
        query = query_map.get(operation, QueryType.TEXT)

        content = self.read_document_from_bytes(file_obj.content, file_obj.name, query=query, pages=pages)

        if operation == "read":
            return content.text
        elif operation == "analyze":
            analysis_type = kwargs.get("analysis_type", "full")
            instructions = kwargs.get("instructions", None)
            return self.analyze_content(content, analysis_type, instructions=instructions)
        elif operation == "extract_images":
            return [self._image_data_to_dict(img) for img in content.images]
        elif operation == "extract_tables":
            return [self._table_data_to_dict(table) for table in content.tables]
        elif operation == "summary":
            instructions = kwargs.get("instructions", None)
            analysis = self.analyze_content(content, "text", instructions=instructions)
            return analysis.summary
        else:
            raise ValueError(f"Unknown operation: {operation}")

    @staticmethod
    def _image_data_to_dict(image_data) -> Dict[str, Any]:
        """Convert ImageData to dictionary representation."""
        position = None
        if image_data.position:
            position = {
                "page": image_data.position.page,
                "x": image_data.position.x,
                "y": image_data.position.y,
            }

        return {
            "format": image_data.format,
            "text_content": image_data.text_content,
            "position": position,
            "metadata": image_data.metadata,
        }

    @staticmethod
    def _table_data_to_dict(table_data) -> Dict[str, Any]:
        """Convert TableData to dictionary representation."""
        position = None
        if table_data.position:
            position = {
                "page": table_data.position.page,
                "x": table_data.position.x,
                "y": table_data.position.y,
            }

        return {
            "rows": table_data.rows,
            "headers": table_data.headers,
            "position": position,
            "metadata": table_data.metadata,
        }

    def _parse_page_selection(self, page_str: str) -> Set[int]:
        """
        Parse a page selection string and return a set of page numbers.

        Supports formats:
        - Single page: "1"
        - Comma-separated: "1,3,5"
        - Range: "1-4"
        - Mixed: "1,3,5-8"
        - Special: "all" (returns empty set to indicate all pages)

        Args:
            page_str: Page selection string

        Returns:
            Set of page numbers (empty set if 'all' is specified)

        Raises:
            InvalidPageSelectionError: If the page selection format is invalid
        """
        if not page_str or not page_str.strip():
            raise InvalidPageSelectionError("Page selection cannot be empty")

        # Defensive check: 'all' should be normalized to None before reaching this method
        if page_str.strip().lower() == "all":
            raise InvalidPageSelectionError(
                "Invalid page selection: 'all' is not a valid page number. "
                "To process all pages, omit the pages parameter or pass None."
            )

        page_numbers = set()
        page_parts = page_str.split(",")

        for part in page_parts:
            part = part.strip()
            # Check if it's a range (e.g., "1-4")
            if "-" in part:
                self._add_page_range(part, page_numbers)
            else:
                self._add_single_page(part, page_numbers)

        return page_numbers

    @staticmethod
    def _add_page_range(range_str: str, page_numbers: Set[int]) -> None:
        """Add a range of page numbers to the set."""
        try:
            start, end = map(int, range_str.split("-"))
            if start < 1 or end < start:
                raise InvalidPageSelectionError(
                    f"Invalid page range: {range_str}. Start must be >= 1 and end must be >= start."
                )
            page_numbers.update(range(start, end + 1))
        except ValueError:
            raise InvalidPageSelectionError(f"Invalid page range format: {range_str}")

    @staticmethod
    def _add_single_page(page_str: str, page_numbers: Set[int]) -> None:
        """Add a single page number to the set."""
        try:
            page_num = int(page_str)
            if page_num < 1:
                raise InvalidPageSelectionError(f"Page numbers must be >= 1, got: {page_num}")
            page_numbers.add(page_num)
        except ValueError:
            raise InvalidPageSelectionError(f"Invalid page number: {page_str}")

    def _filter_content_by_pages(self, content: DocumentContent, pages: str) -> DocumentContent:
        """
        Filter document content to include only the specified pages.

        Args:
            content: Original document content
            pages: Page selection string

        Returns:
            Filtered document content

        Raises:
            InvalidPageSelectionError: If the page selection format is invalid
        """
        try:
            page_numbers = self._parse_page_selection(pages)
            logger.info(f"Filtering content to include pages: {page_numbers}")

            # Create a new DocumentContent with filtered elements
            filtered_content = DocumentContent(
                text="",  # Will be populated based on paragraphs
                metadata=content.metadata.copy(),
                formatting=content.formatting,
            )

            # Filter content elements
            filtered_content = self._filter_content_elements(content, filtered_content, page_numbers)

            # Reconstruct text from filtered paragraphs
            filtered_content.text = "\n\n".join([p.text for p in filtered_content.structure.paragraphs])

            # Update metadata to indicate filtering
            filtered_content.metadata["filtered_pages"] = list(page_numbers)
            filtered_content.metadata["original_page_count"] = content.metadata.get("page_count", 0)

            return filtered_content

        except InvalidPageSelectionError:
            raise
        except Exception as e:
            logger.error(f"Error filtering content by pages: {str(e)}")
            raise InvalidPageSelectionError(f"Failed to filter content by pages: {str(e)}") from e

    def _filter_content_elements(
        self, content: DocumentContent, filtered_content: DocumentContent, page_numbers: Set[int]
    ) -> DocumentContent:
        """
        Filter document elements to include only those from specified pages.

        Args:
            content: Original document content
            filtered_content: Target filtered content object
            page_numbers: Set of page numbers to include

        Returns:
            Updated filtered content object
        """
        # Filter paragraphs by page
        filtered_content.structure.paragraphs = self._filter_elements_by_page(
            content.structure.paragraphs, page_numbers
        )

        # Filter headers by page
        filtered_content.structure.headers = self._filter_elements_by_page(content.structure.headers, page_numbers)

        # Filter images by page
        filtered_content.images = self._filter_elements_by_page(content.images, page_numbers)

        # Filter tables by page
        filtered_content.tables = self._filter_elements_by_page(content.tables, page_numbers)

        return filtered_content

    @staticmethod
    def _filter_elements_by_page(elements, page_numbers: Set[int]) -> List:
        """
        Filter a list of elements by page number.

        Args:
            elements: List of elements with position attribute
            page_numbers: Set of page numbers to include

        Returns:
            Filtered list of elements
        """
        return [element for element in elements if element.position and element.position.page in page_numbers]
