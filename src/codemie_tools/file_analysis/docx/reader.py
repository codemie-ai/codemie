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
from typing import Optional, List, Dict, Any

import docx2txt
from docx import Document
from langchain_core.language_models import BaseChatModel

from codemie_tools.file_analysis.docx.exceptions import (
    DocumentReadError,
    CorruptedDocumentError,
    UnsupportedFormatError,
    ImageExtractionError,
    TableExtractionError,
)
from codemie_tools.file_analysis.docx.models import (
    DocumentContent,
    DocumentStructure,
    HeaderInfo,
    ParagraphInfo,
    SectionInfo,
    StyleInfo,
    FormattingInfo,
    Position,
    ImageData,
    TableData,
    QueryType,
)
from codemie_tools.utils.image_processor import ImageProcessor

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

    def read_with_markitdown(self, file_path: str, query: QueryType) -> DocumentContent:
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

        try:
            # For this implementation, we'll use python-docx directly
            document = Document(file_path)

            # Determine what content to extract based on query
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
            extract_images = query in [
                QueryType.TEXT_WITH_IMAGES,
                QueryType.IMAGE_EXTRACTION,
            ]

            # Extract text content
            text = DocxReader._extract_text(document) if extract_text else ""

            # Extract document structure
            structure = DocxReader._extract_structure(document) if extract_structure else DocumentStructure()

            # Extract formatting information
            formatting = DocxReader._extract_formatting(document) if extract_formatting else FormattingInfo()

            # Extract metadata
            metadata = self._extract_metadata(document) if extract_metadata else {}

            # Extract tables
            tables = self._extract_tables(document) if extract_tables else []

            # Extract images (OCR based on class-level ocr_enabled setting)
            images = self._extract_images(file_path, include_ocr=self.ocr_enabled) if extract_images else []

            return DocumentContent(
                text=text,
                structure=structure,
                formatting=formatting,
                metadata=metadata,
                tables=tables,
                images=images,
            )

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
            # Create temporary file
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

    @staticmethod
    def _extract_text(document) -> str:
        """
        Extract text content from a document.

        Args:
            document: python-docx Document object

        Returns:
            Document text content
        """
        text_parts = []

        for paragraph in document.paragraphs:
            text_parts.append(paragraph.text)

        for table in document.tables:
            for row in table.rows:
                row_text = []
                for cell in row.cells:
                    row_text.append(cell.text)
                text_parts.append(" | ".join(row_text))

        return "\n".join(text_parts)

    @staticmethod
    def _extract_structure(document) -> DocumentStructure:
        """
        Extract document structure information.

        Args:
            document: python-docx Document object

        Returns:
            DocumentStructure object
        """
        # Variables will be populated in the following method call

        # Process paragraphs and extract headers and styles
        paragraphs, headers, styles = DocxReader._process_paragraphs(document.paragraphs)

        # Process sections based on headers
        sections = DocxReader._create_sections(paragraphs, headers)

        return DocumentStructure(headers=headers, paragraphs=paragraphs, sections=sections, styles=styles)

    @staticmethod
    def _process_paragraphs(doc_paragraphs):
        """
        Process paragraphs to extract headers, paragraph info and styles.

        Args:
            doc_paragraphs: List of document paragraphs

        Returns:
            Tuple of (paragraphs, headers, styles) lists
        """
        headers = []
        paragraphs = []
        styles = []

        for i, paragraph in enumerate(doc_paragraphs):
            # Create a position (page number is approximate)
            position = Position(page=i // 40 + 1, x=0.0, y=float(i % 40))

            # Determine paragraph style name, defaulting to "Normal" if not found
            paragraph_style_name = paragraph.style.name if paragraph.style and paragraph.style.name else "Normal"

            # Check if it's a heading
            if paragraph_style_name.startswith("Heading"):
                level = int(paragraph_style_name.replace("Heading ", "")) if paragraph_style_name != "Heading" else 1
                headers.append(HeaderInfo(level=level, text=paragraph.text, position=position))

            # Add to paragraphs list
            paragraphs.append(ParagraphInfo(text=paragraph.text, style=paragraph_style_name, position=position))

            DocxReader._add_style_if_new(paragraph, styles, paragraph_style_name)

        return paragraphs, headers, styles

    @staticmethod
    def _add_style_if_new(paragraph, styles, default_style_name: str = "Normal"):
        """
        Add style information if not already processed.

        Args:
            paragraph: Document paragraph
            styles: List of styles to update
        """
        style_name = paragraph.style.name if paragraph.style and paragraph.style.name else default_style_name
        if style_name not in [s.name for s in styles]:
            style = StyleInfo(
                name=style_name,
                font=paragraph.style.font.name
                if hasattr(paragraph.style, "font") and paragraph.style.font
                else "Default",
                size=paragraph.style.font.size if hasattr(paragraph.style, "font") and paragraph.style.font else 12,
                bold=paragraph.bold if hasattr(paragraph, "bold") else False,
                italic=paragraph.italic if hasattr(paragraph, "italic") else False,
            )
            styles.append(style)

    @staticmethod
    def _create_sections(paragraphs, headers):
        """
        Create document sections based on headers.

        Args:
            paragraphs: List of paragraph info objects
            headers: List of header info objects

        Returns:
            List of section info objects
        """
        sections = []
        current_section = None
        section_content = []

        for para in paragraphs:
            # Check if this paragraph is a header
            header_match = next((h for h in headers if h.text == para.text), None)

            if header_match:
                DocxReader._finalize_section(current_section, section_content, sections)
                current_section = header_match
                section_content = []
            else:
                # Add to current section content
                section_content.append(para)

        # Add last section if exists
        DocxReader._finalize_section(current_section, section_content, sections)

        return sections

    @staticmethod
    def _finalize_section(current_section, section_content, sections):
        """
        Add a section to the sections list if it exists.

        Args:
            current_section: Current section header
            section_content: Content of the section
            sections: List of sections to update
        """
        if current_section:
            sections.append(
                SectionInfo(
                    title=current_section.text,
                    content=section_content.copy(),
                    level=current_section.level,
                )
            )

    @staticmethod
    def _extract_formatting(document) -> FormattingInfo:
        """
        Extract formatting information from a document.

        Args:
            document: python-docx Document object

        Returns:
            FormattingInfo object
        """
        # Create style dictionary
        style_dict = DocxReader._create_style_dict(document.styles)

        # Get page dimensions
        page_width, page_height, margins = DocxReader._extract_page_dimensions(document)

        return FormattingInfo(styles=style_dict, page_width=page_width, page_height=page_height, margins=margins)

    @staticmethod
    def _create_style_dict(styles):
        """
        Create a dictionary of styles from document style objects.

        Args:
            styles: Document style collection

        Returns:
            Dictionary of style information
        """
        style_dict = {}
        for style in styles:
            if hasattr(style, "name") and hasattr(style, "font"):
                style_dict[style.name] = StyleInfo(
                    name=style.name,
                    font=style.font.name if hasattr(style.font, "name") else "Default",
                    size=style.font.size if hasattr(style.font, "size") else 12,
                )
        return style_dict

    @staticmethod
    def _extract_page_dimensions(document):
        """
        Extract page dimensions from document.

        Args:
            document: python-docx Document object

        Returns:
            Tuple of (page_width, page_height, margins)
        """
        # Default values
        default_width = 8.5
        default_height = 11.0
        default_margins = {"top": 1.0, "right": 1.0, "bottom": 1.0, "left": 1.0}

        try:
            section = document.sections[0]
            page_width = section.page_width.inches if hasattr(section, "page_width") else default_width
            page_height = section.page_height.inches if hasattr(section, "page_height") else default_height

            margins = {
                "top": section.top_margin.inches if hasattr(section, "top_margin") else default_margins["top"],
                "right": section.right_margin.inches if hasattr(section, "right_margin") else default_margins["right"],
                "bottom": section.bottom_margin.inches
                if hasattr(section, "bottom_margin")
                else default_margins["bottom"],
                "left": section.left_margin.inches if hasattr(section, "left_margin") else default_margins["left"],
            }
            return page_width, page_height, margins
        except (AttributeError, IndexError):
            return default_width, default_height, default_margins

    @staticmethod
    def _extract_metadata(document) -> Dict[str, Any]:
        """
        Extract metadata from a document.

        Args:
            document: python-docx Document object

        Returns:
            Dictionary of metadata
        """
        metadata = {}

        # Core properties
        core_props = document.core_properties
        if hasattr(core_props, "title"):
            metadata["title"] = core_props.title
        if hasattr(core_props, "author"):
            metadata["author"] = core_props.author
        if hasattr(core_props, "created"):
            metadata["created_date"] = core_props.created.isoformat() if core_props.created else None
        if hasattr(core_props, "modified"):
            metadata["modified_date"] = core_props.modified.isoformat() if core_props.modified else None
        if hasattr(core_props, "keywords"):
            metadata["keywords"] = core_props.keywords
        if hasattr(core_props, "subject"):
            metadata["subject"] = core_props.subject
        if hasattr(core_props, "category"):
            metadata["category"] = core_props.category

        # Document statistics
        metadata["paragraph_count"] = len(document.paragraphs)
        metadata["table_count"] = len(document.tables)
        metadata["section_count"] = len(document.sections)

        # Word count (approximate)
        word_count = 0
        for paragraph in document.paragraphs:
            word_count += len(paragraph.text.split())
        metadata["word_count"] = word_count

        return metadata

    @staticmethod
    def _extract_tables(document) -> List[TableData]:
        """
        Extract tables from a document.

        Args:
            document: python-docx Document object

        Returns:
            List of TableData objects

        Raises:
            TableExtractionError: If table extraction fails
        """
        tables = []

        try:
            for i, table in enumerate(document.tables):
                rows = []
                headers = []

                # Process table rows
                for j, row in enumerate(table.rows):
                    row_data = [cell.text for cell in row.cells]

                    # First row might be headers
                    if j == 0:
                        headers = row_data

                    rows.append(row_data)

                # Create position (approximate)
                position = Position(page=i + 1, x=0.0, y=0.0)

                tables.append(TableData(rows=rows, headers=headers, position=position, metadata={"table_index": i}))

        except Exception as e:
            logger.error(f"Error extracting tables: {str(e)}")
            raise TableExtractionError(f"Failed to extract tables: {str(e)}") from e

        return tables

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
