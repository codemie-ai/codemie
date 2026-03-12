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
from enum import Enum
from typing import Optional, Type, Any, List, Union, Dict

from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.file_tool_mixin import FileToolMixin
from codemie_tools.file_analysis.models import FileAnalysisConfig
from codemie_tools.file_analysis.pdf.processor import PdfProcessor
from codemie_tools.file_analysis.tool_vars import PDF_TOOL

# Configure logger
logger = logging.getLogger(__name__)


class QueryType(str, Enum):
    TEXT = "Text"
    TEXT_WITH_METADATA = "Text_with_Metadata"
    TEXT_WITH_OCR = "Text_with_Image"  # Kept the name for backward compatibility, now uses LLM
    TOTAL_PAGES = "Total_Pages"


class PDFToolInput(BaseModel):
    """
    Defines the schema for the arguments required by PDFTool.
    """

    pages: list[int] = Field(
        default_factory=list,
        description=(
            "List of page numbers of a PDF document to process. "
            "Must be empty to process all pages in a single request. "
            "Page numbers are 1-based."
        ),
    )
    query: QueryType = Field(
        ...,
        description=(
            "'Text' if the tool must return the text representation of the PDF pages. "
            "'Text_with_Metadata' if the tool must return the text representation of the "
            "PDF pages with metadata. "
            "'Text_with_Image' if the tool must extract text from PDF that contain images within it"
            "'Total_Pages' if the tool must return the total number of pages in the PDF "
            "document."
        ),
    )


class PDFTool(CodeMieTool, FileToolMixin):
    """
    A tool for processing PDF documents, such as extracting the text from specific pages.
    Also supports text extraction from images within PDFs using LLM-based image recognition.
    Supports multiple PDFs as input.
    """

    # The Pydantic model that describes the shape of arguments this tool takes.
    args_schema: Type[BaseModel] = PDFToolInput

    name: str = PDF_TOOL.name
    label: str = PDF_TOOL.label
    description: str = PDF_TOOL.description

    config: FileAnalysisConfig
    pdf_processor: Optional[PdfProcessor] = None

    def __init__(self, config: FileAnalysisConfig) -> None:
        """
        Initialize the PDFTool with configuration containing PDF files.

        Args:
            config: FileAnalysisConfig with input_files and optional chat_model
        """
        super().__init__(config=config)
        self.pdf_processor = PdfProcessor(chat_model=config.chat_model)

    def _get_supported_mime_types(self) -> Optional[List[str]]:
        """
        Get list of supported mime types for PDF processing.

        Returns:
            List of PDF mime types
        """
        return ['application/pdf']

    def _get_supported_extensions(self) -> Optional[List[str]]:
        """
        Get list of supported file extensions for PDF processing.

        Returns:
            List of PDF file extensions
        """
        return ['.pdf']

    def execute(self, pages: List[int], query: QueryType) -> Union[str, Dict[str, Any]]:
        """
        Process the PDF documents based on the provided query and pages.

        Args:
            pages (List[int]): A list of 1-based page numbers to process.
                               If empty, the entire document is processed.
            query (str): The query or action to perform:
                - "Total_Pages" to return the total number of pages.
                - "Text" to return the text representation of the PDF.
                - "Text_with_Metadata" to return the text along with metadata.
                - "Text_with_OCR" to extract text from PDF and images using LLM.

        Returns:
            str | dict: A string representation of the requested data or a dictionary with structured results.
        """
        # Get supported PDF files from config (automatically filters by mime type)
        files = self._get_supported_files()

        if not files:
            raise ValueError(f"{self.name} requires at least one PDF file to process.")

        logger.info(f"Processing {len(files)} PDF files with query type: {query}")

        if query == QueryType.TOTAL_PAGES:
            return self.pdf_processor.get_total_pages_from_files(files)

        elif query == QueryType.TEXT_WITH_OCR:
            return self.pdf_processor.process_pdf_files(files, pages)

        elif query.lower().startswith("text"):
            # Pass page_chunks parameter based on query type
            page_chunks = query == QueryType.TEXT_WITH_METADATA
            return self.pdf_processor.extract_text_as_markdown_from_files(
                files=files, pages=pages, page_chunks=page_chunks
            )

        else:
            error_msg = (
                f"Unknown query '{query}'. Expected one of ['Total_Pages', 'Text', "
                f"'Text_with_Metadata', 'Text_with_OCR']."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
