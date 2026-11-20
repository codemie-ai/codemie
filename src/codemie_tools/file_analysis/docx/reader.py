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
import os
import tempfile
from typing import Optional, List, Dict, Any, TypedDict

import docx2txt
from docx import Document
from langchain_core.language_models import BaseChatModel

from codemie.configs import config
from codemie.datasource.exceptions import FileParsingTimeoutError
from codemie.datasource.loader.file_processor_pool import maybe_pool_submit
from codemie_tools.file_analysis.docx.exceptions import (
    DocumentReadError,
    CorruptedDocumentError,
    UnsupportedFormatError,
    ImageExtractionError,
    TableExtractionError,
)
from codemie_tools.file_analysis.docx.models import (
    DocumentContent,
    Position,
    ImageData,
    QueryType,
    DocxReaderExtractFlags,
)
from codemie_tools.utils.image_processor import ImageProcessor
from codemie_tools.file_analysis.workers import extract_docx_content, extract_docx_text

logger = logging.getLogger(__name__)


class DocxReader:
    """
    Reader for DOCX documents.

    Provides methods for extracting content, structure, and embedded elements from DOCX files.
    """

    def __init__(self, ocr_enabled: bool = True, chat_model: Optional[BaseChatModel] = None):
        """
        Initialize the DocxReader with configuration and dependencies.

        Args:
            ocr_enabled: Whether OCR is enabled for image processing
            chat_model: LangChain chat model for image text extraction
        """
        self.ocr_enabled = ocr_enabled
        self.image_processor = ImageProcessor(chat_model=chat_model) if chat_model else None

    def _resolve_extract_flags(self, query: QueryType) -> DocxReaderExtractFlags:
        extract_text = query in [
            QueryType.TEXT,
            QueryType.TEXT_WITH_METADATA,
            QueryType.TEXT_WITH_IMAGES,
            QueryType.SUMMARY,
            QueryType.ANALYZE,
        ]
        extract_structure = query in [
            QueryType.TEXT,
            QueryType.TEXT_WITH_METADATA,
            QueryType.TEXT_WITH_IMAGES,
            QueryType.STRUCTURE_ONLY,
            QueryType.SUMMARY,
            QueryType.ANALYZE,
        ]
        extract_formatting = query in [
            QueryType.STRUCTURE_ONLY,
            QueryType.SUMMARY,
            QueryType.ANALYZE,
        ]
        extract_metadata = query in [
            QueryType.TEXT_WITH_METADATA,
            QueryType.SUMMARY,
            QueryType.ANALYZE,
        ]
        extract_tables = query in [
            QueryType.TABLE_EXTRACTION,
            QueryType.SUMMARY,
            QueryType.ANALYZE,
        ]
        needs_images = query in [
            QueryType.TEXT_WITH_IMAGES,
            QueryType.IMAGE_EXTRACTION,
        ]
        return {
            "extract_text": extract_text,
            "extract_structure": extract_structure,
            "extract_formatting": extract_formatting,
            "extract_metadata": extract_metadata,
            "extract_tables": extract_tables,
            "needs_images": needs_images,
        }

    def read_with_markitdown(
        self,
        file_path: str,
        query: QueryType,
    ) -> DocumentContent:
        """
        Read a DOCX document using MarkItDown for comprehensive reading.

        Args:
            file_path: Path to the DOCX file
            query: Query type to control what content is extracted (QueryType enum).

        Returns:
            DocumentContent object with document data

        Raises:
            DocumentReadError: If the document cannot be read
            CorruptedDocumentError: If the document is corrupted
            UnsupportedFormatError: If the format is not supported
        """
        logger.info(f"Reading document with MarkItDown: {file_path}, Query: {query}, OCR: {self.ocr_enabled}")

        extract_params = self._resolve_extract_flags(query)
        try:
            with open(file_path, 'rb') as f:
                docx_bytes = f.read()
            # Fast path: offload all non-image extraction to process pool
            document = maybe_pool_submit(extract_docx_content, docx_bytes, **extract_params)

            # Standard path
            images = []
            if extract_params.get("needs_images"):
                images = self._extract_images(file_path, include_ocr=self.ocr_enabled)

            return DocumentContent(
                text=document.text,
                structure=document.structure,
                formatting=document.formatting,
                metadata=document.metadata,
                tables=document.tables,
                images=images,
            )

        except FileParsingTimeoutError:
            raise
        except ValueError as e:
            logger.error(f"Document format error: {str(e)}")
            raise UnsupportedFormatError(f"Unsupported document format: {str(e)}") from e
        except IOError as e:
            logger.error(f"Document read error: {str(e)}")
            raise DocumentReadError(f"Error reading document: {str(e)}") from e
        except Exception as e:
            logger.error(f"Document processing error: {str(e)}")
            raise CorruptedDocumentError(f"Document might be corrupted: {str(e)}") from e

    def read_from_bytes(
        self,
        content: bytes,
        file_name: str,
        query: QueryType,
    ) -> DocumentContent:
        """
        Read a DOCX document from bytes.

        Args:
            content: DOCX content as bytes
            file_name: Name of the file (for reference)
            query: Query type to control what content is extracted (QueryType enum)

        Returns:
            DocumentContent object with document data

        Raises:
            DocumentReadError: If the document cannot be read
            CorruptedDocumentError: If the document is corrupted
            UnsupportedFormatError: If the format is not supported
        """
        logger.info(f"Reading document from bytes: {file_name}, Query: {query}, OCR: {self.ocr_enabled}")

        try:
            extract_flags = self._resolve_extract_flags(query)

            # Fast path: offload all non-image extraction to process pool
            if not extract_flags.get("needs_images"):
                return maybe_pool_submit(
                    extract_docx_content,
                    content,
                    *extract_flags.values(),
                )

            # Standard path: create temporary file (needed for image extraction)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as temp_file:
                temp_path = temp_file.name
                temp_file.write(content)

            try:
                # Process the temporary file
                return self.read_with_markitdown(temp_path, query=query)
            finally:
                # Clean up the temporary file
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

        except FileParsingTimeoutError:
            raise
        except Exception as e:
            logger.error(f"Error reading document from bytes: {str(e)}")
            raise DocumentReadError(f"Failed to process document content: {str(e)}") from e

    def extract_images(self, file_path: str, include_ocr: bool = True) -> List[ImageData]:
        """
        Extract all images from a DOCX document.

        Args:
            file_path: Path to the DOCX file
            include_ocr: Whether to perform OCR on images

        Returns:
            List of ImageData objects

        Raises:
            ImageExtractionError: If image extraction fails
        """
        logger.info(f"Extracting images from document: {file_path}")

        try:
            return self._extract_images(file_path, include_ocr)
        except Exception as e:
            logger.error(f"Error extracting images: {str(e)}")
            raise ImageExtractionError(f"Failed to extract images: {str(e)}") from e

    def _extract_images(self, file_path: str, include_ocr: bool = True) -> List[ImageData]:
        """
        Extract images from a document.

        Args:
            file_path: Original document path (for reference)
            include_ocr: Whether to perform OCR on images

        Returns:
            List of ImageData objects

        Raises:
            ImageExtractionError: If image extraction fails
        """
        images = []

        try:
            # Create a temporary directory to extract images
            with tempfile.TemporaryDirectory() as temp_dir:
                # Use docx2txt to extract images to temp directory
                docx2txt.process(file_path, temp_dir)

                # Process all extracted images
                for i, img_file in enumerate(sorted(os.listdir(temp_dir))):
                    if not img_file.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp")):
                        continue

                    img_path = os.path.join(temp_dir, img_file)

                    # Read image data
                    with open(img_path, "rb") as f:
                        img_data = f.read()

                    # Get image format
                    img_format = os.path.splitext(img_file)[1].lstrip(".")

                    # Create position (approximate)
                    position = Position(page=1, x=0.0, y=0.0)

                    # Extract text using OCR if enabled
                    text_content = None
                    if include_ocr and self.ocr_enabled and self.image_processor:
                        try:
                            text_content = self.image_processor.extract_text_from_image_bytes(img_data)
                            logger.debug(f"OCR extracted {len(text_content)} characters from image {i}")
                        except Exception as ocr_e:
                            logger.warning(f"OCR failed for image {i}: {str(ocr_e)}")

                    images.append(
                        ImageData(
                            content=img_data,
                            format=img_format,
                            text_content=text_content,
                            position=position,
                            metadata={"image_index": i},
                        )
                    )

        except Exception as e:
            logger.error(f"Error extracting images: {str(e)}")
            raise ImageExtractionError(f"Failed to extract images: {str(e)}") from e

        return images
