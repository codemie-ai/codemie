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
import io
from typing import Optional, List, Union

import pdfplumber
from langchain_core.language_models import BaseChatModel

from codemie_tools.base.constants import SOURCE_DOCUMENT_KEY, SOURCE_FIELD_KEY, FILE_CONTENT_FIELD_KEY
from codemie_tools.base.file_object import FileObject
from codemie_tools.utils.image_processor import ImageProcessor

# Configure logger
logger = logging.getLogger(__name__)

# Constants for error messages
ERROR_NO_PDF_LOADED = "No PDF document is loaded"
ERROR_NO_PDF_LOADED_DETAIL = "No PDF document is loaded. Please provide a valid PDF."


class PdfProcessor:
    """
    A utility class for processing PDFs and extracting text using OCR capabilities.
    Uses ImageProcessor for image-based text extraction and pdfplumber for PDF processing.
    """

    def __init__(self, chat_model: Optional[BaseChatModel] = None):
        """
        Initialize the OCR processor.

        Args:
            chat_model: Optional LangChain chat model for image text extraction
        """
        self.image_processor = ImageProcessor(chat_model=chat_model) if chat_model else None

    @staticmethod
    def open_pdf_document(file_content: bytes) -> pdfplumber.PDF:
        """
        Opens a PDF document from file content.

        Args:
            file_content: PDF file content as bytes

        Returns:
            pdfplumber.PDF: pdfplumber PDF object
        """
        try:
            return pdfplumber.open(io.BytesIO(file_content))
        except Exception as e:
            raise ValueError(f"Failed to open PDF document: {str(e)}")

    def process_pdf_files(self, files: List[FileObject], pages: List[int] = None) -> str:
        """
        Process multiple PDF files and extract text from both regular content and images.

        Args:
            files: List of PDF files to process
            pages: List of 1-based page numbers to process. If None, processes all pages.

        Returns:
            str: Combined extracted text from all PDF content and images
        """
        if not files:
            raise ValueError(ERROR_NO_PDF_LOADED_DETAIL)

        logger.info(f"Processing {len(files)} PDF files with LLM for image text recognition")

        # If there's only one file, process it directly
        if len(files) == 1:
            pdf_document = self.open_pdf_document(files[0].content)
            try:
                return self._process_pdf_document(pdf_document, pages)
            finally:
                pdf_document.close()

        # Process multiple files with LLM-friendly separators
        results = []
        for idx, file_object in enumerate(files):
            logger.info(f"Processing PDF {idx + 1}/{len(files)}: {file_object.name}")

            pdf_document = self.open_pdf_document(file_object.content)
            try:
                file_content = self._process_pdf_document(pdf_document, pages)
                results.append(f"\n{SOURCE_DOCUMENT_KEY}\n")
                results.append(f"{SOURCE_FIELD_KEY} {file_object.name}\n")
                results.append(f"{FILE_CONTENT_FIELD_KEY} \n{file_content}\n")
            finally:
                pdf_document.close()

        return "\n".join(results)

    def process_pdf(self, pdf_document: Union[pdfplumber.PDF, bytes], pages: List[int] = None) -> str:
        """
        Process a PDF document and extract text from both regular content and images.

        Args:
            pdf_document: pdfplumber PDF object or bytes content
            pages: List of 1-based page numbers to process. If None, processes all pages.

        Returns:
            str: Combined extracted text from PDF content and images
        """
        # Convert bytes to pdfplumber.PDF if needed
        if isinstance(pdf_document, bytes):
            pdf_document = self.open_pdf_document(pdf_document)
            should_close = True
        else:
            should_close = False

        if not pdf_document:
            raise ValueError(ERROR_NO_PDF_LOADED_DETAIL)

        logger.info("Processing PDF with LLM for image text recognition")

        try:
            return self._process_pdf_document(pdf_document, pages)
        finally:
            if should_close:
                pdf_document.close()

    def extract_text_as_markdown_from_files(
        self, files: List[FileObject], pages: List[int] = None, page_chunks: bool = False
    ) -> str:
        """
        Extract text from multiple PDF files and format it as markdown.

        Args:
            files: List of PDF files to process
            pages: List of 1-based page numbers to process. If None, processes all pages.
            page_chunks: Whether to include page metadata in the output.

        Returns:
            str: Markdown-formatted extracted text from all PDFs
        """
        if not files:
            raise ValueError(ERROR_NO_PDF_LOADED)

        logger.info(f"Extracting text from {len(files)} PDF files")

        # If there's only one file, process it directly
        if len(files) == 1:
            pdf_document = self.open_pdf_document(files[0].content)
            try:
                return self.extract_text_as_markdown(pdf_document, pages, page_chunks)
            finally:
                pdf_document.close()

        # Process multiple files with LLM-friendly separators
        results = []

        for idx, file_object in enumerate(files):
            logger.info(f"Processing PDF {idx + 1}/{len(files)}: {file_object.name}")

            pdf_document = self.open_pdf_document(file_object.content)
            try:
                file_content = self.extract_text_as_markdown(pdf_document, pages, page_chunks)
                results.append(f"\n{SOURCE_DOCUMENT_KEY}\n")
                results.append(f"{SOURCE_FIELD_KEY} {file_object.name}\n")
                results.append(f"{FILE_CONTENT_FIELD_KEY} \n{file_content}\n")
            finally:
                pdf_document.close()

        return "\n".join(results)

    @staticmethod
    def _ensure_pdf_object(pdf_document: Union[pdfplumber.PDF, bytes]) -> tuple:
        """Ensure we have a pdfplumber.PDF object.

        Args:
            pdf_document: PDF object or bytes

        Returns:
            Tuple of (pdf_object, should_close)
        """
        if isinstance(pdf_document, bytes):
            try:
                return pdfplumber.open(io.BytesIO(pdf_document)), True
            except Exception as e:
                raise ValueError(f"Failed to open PDF document: {str(e)}")
        return pdf_document, False

    @staticmethod
    def _get_pages_to_process(pdf_obj: pdfplumber.PDF, pages: list[int] | None = None) -> list[int] | range:
        """Get the range of pages to process.

        Args:
            pdf_obj: pdfplumber PDF object
            pages: Optional list of 1-based page numbers

        Returns:
            List of 0-based page indices or range object
        """
        if pages:
            return [p - 1 for p in pages]
        return range(len(pdf_obj.pages))

    @staticmethod
    def _process_pdf_page(page, page_idx: int, page_chunks: bool) -> List[str]:
        """Extract text and tables from a single PDF page.

        Args:
            page: pdfplumber page object
            page_idx: 0-based page index
            page_chunks: Whether to include page headers

        Returns:
            List of markdown parts for this page
        """
        parts = []

        if page_chunks:
            parts.append(f"\n## Page {page_idx + 1}\n")

        # Extract text
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text)

        # Extract tables as markdown
        tables = page.extract_tables()
        for table_idx, table in enumerate(tables):
            if table:
                table_md = PdfProcessor._table_to_markdown(table)
                parts.append(f"\n**Table {table_idx + 1}:**\n{table_md}\n")

        return parts

    @staticmethod
    def extract_text_as_markdown(
        pdf_document: Union[pdfplumber.PDF, bytes], pages: List[int] = None, page_chunks: bool = False
    ) -> str:
        """
        Extract text from a PDF document and format it as markdown.

        Args:
            pdf_document: pdfplumber PDF object or bytes content
            pages: List of 1-based page numbers to process. If None, processes all pages.
            page_chunks: Whether to include page metadata in the output.

        Returns:
            str: Markdown-formatted extracted text from the PDF
        """
        # Convert bytes to pdfplumber.PDF if needed
        pdf_obj, should_close = PdfProcessor._ensure_pdf_object(pdf_document)

        if not pdf_obj:
            raise ValueError(ERROR_NO_PDF_LOADED_DETAIL)

        logger.info(f"Extracting text from pages: {pages if pages else 'all'}")

        try:
            # Convert 1-based page indices to 0-based
            pages_to_process = PdfProcessor._get_pages_to_process(pdf_obj, pages)

            markdown_parts = []
            for page_idx in pages_to_process:
                if page_idx >= len(pdf_obj.pages):
                    continue
                page = pdf_obj.pages[page_idx]
                page_parts = PdfProcessor._process_pdf_page(page, page_idx, page_chunks)
                markdown_parts.extend(page_parts)

            markdown = "\n\n".join(markdown_parts)
            logger.debug(f"Extracted {len(markdown)} characters of text")
            return markdown
        finally:
            if should_close:
                pdf_obj.close()

    @staticmethod
    def _table_to_markdown(table):
        """Convert table array to markdown format."""
        if not table or not any(table):
            return ""

        md_lines = []
        for i, row in enumerate(table):
            if not row:
                continue
            cells = [str(cell or "").strip() for cell in row]
            md_lines.append("| " + " | ".join(cells) + " |")
            if i == 0:  # Add header separator
                md_lines.append("| " + " | ".join(["---"] * len(cells)) + " |")

        return "\n".join(md_lines)

    def get_total_pages_from_files(self, files: List[FileObject]) -> str:
        """
        Get the total number of pages across all provided PDF files.

        Args:
            files: List of PDF files

        Returns:
            str: Total number of pages as a string and breakdown by file
        """
        if not files:
            raise ValueError(ERROR_NO_PDF_LOADED)

        # If there's only one file, get pages directly
        if len(files) == 1:
            pdf_document = self.open_pdf_document(files[0].content)
            try:
                pages = len(pdf_document.pages)
                return f"Total pages: {pages}\n{files[0].name}: {pages} pages"
            finally:
                pdf_document.close()

        # For multiple files
        total_pages = 0
        file_pages = []

        for idx, file_obj in enumerate(files):
            pdf_document = self.open_pdf_document(file_obj.content)
            try:
                pages = len(pdf_document.pages)
                total_pages += pages
                file_pages.append(f"{file_obj.name}: {pages} pages")
            finally:
                pdf_document.close()

        results = [
            "### PDF PAGE COUNT SUMMARY ###\n",
            f"**Total pages across all files:** {total_pages}\n",
            "**Breakdown by file:**\n",
            "\n".join(file_pages),
        ]

        return "\n".join(results)

    @staticmethod
    def get_total_pages(pdf_document: Union[pdfplumber.PDF, bytes]) -> str:
        """
        Get the total number of pages in a PDF document.

        Args:
            pdf_document: pdfplumber PDF object or bytes content

        Returns:
            str: Total number of pages as a string
        """
        # Convert bytes to pdfplumber.PDF if needed
        if isinstance(pdf_document, bytes):
            pdf_document = pdfplumber.open(io.BytesIO(pdf_document))
            should_close = True
        else:
            should_close = False

        if not pdf_document:
            logger.error(ERROR_NO_PDF_LOADED)
            raise ValueError(ERROR_NO_PDF_LOADED_DETAIL)

        try:
            page_count = len(pdf_document.pages)
            logger.debug(f"Returning total page count: {page_count}")
            return str(page_count)
        finally:
            if should_close:
                pdf_document.close()

    def _process_pdf_document(self, pdf_document: pdfplumber.PDF, pages: List[int] = None) -> str:
        """
        Internal method to process a PDF document and extract text.

        Args:
            pdf_document: pdfplumber PDF object
            pages: List of 1-based page numbers to process. If None, processes all pages.

        Returns:
            str: Combined extracted text from PDF content and images
        """
        # Convert 1-based page indices to 0-based
        zero_based_pages = [p - 1 for p in pages] if pages else list(range(len(pdf_document.pages)))

        all_text = []

        for page_num in zero_based_pages:
            logger.info(f"Processing page {page_num + 1}")

            if page_num >= len(pdf_document.pages):
                logger.warning(f"Page {page_num + 1} does not exist, skipping")
                continue

            page = pdf_document.pages[page_num]

            # Extract text directly from PDF
            text = page.extract_text() or ""
            if text.strip():
                all_text.append(f"--- Page {page_num + 1} PDF Text ---\n{text}")

            # Process images if image processor is available
            if not self.image_processor:
                continue

            # Extract and process images
            image_text = self._process_page_images(page, page_num)
            if image_text:
                all_text.append(image_text)

        # Combine all extracted text
        result = "\n\n".join(all_text)
        logger.info(f"Processing complete, extracted {len(result)} characters in total")
        return result

    def _process_page_images(self, page: pdfplumber.page.Page, page_num: int) -> Optional[str]:
        """
        Process all images on a single PDF page.

        Args:
            page: pdfplumber page object
            page_num: Zero-based page number

        Returns:
            Optional[str]: Extracted text from images, if any
        """
        try:
            # Get images from the page
            image_list = page.images
            if not image_list:
                logger.debug(f"No images found on page {page_num + 1}")
                return None

            logger.info(f"Found {len(image_list)} images on page {page_num + 1}")
            page_image_texts = []

            # Render the page once for all images
            page_image = page.to_image(resolution=150)

            for img_idx, img_info in enumerate(image_list):
                try:
                    # Get image bounding box
                    x0, top, x1, bottom = (
                        img_info.get("x0"),
                        img_info.get("top"),
                        img_info.get("x1"),
                        img_info.get("bottom"),
                    )

                    if None in (x0, top, x1, bottom):
                        logger.warning(f"Image {img_idx} on page {page_num + 1} missing bbox, skipping")
                        continue

                    # Crop the image region from the rendered page
                    cropped_img = page_image.original.crop((x0, top, x1, bottom))

                    # Convert PIL Image to bytes
                    img_byte_arr = io.BytesIO()
                    cropped_img.save(img_byte_arr, format='PNG')
                    image_bytes = img_byte_arr.getvalue()

                    # Use the image processor to extract text
                    image_text = self.image_processor.extract_text_from_image_bytes(image_bytes)

                    if image_text.strip():
                        page_image_texts.append(f"--- Page {page_num + 1} Image {img_idx + 1} Text ---\n{image_text}")
                        logger.debug(f"Extracted {len(image_text)} characters from image {img_idx}")
                    else:
                        logger.debug(f"No text found in image {img_idx} on page {page_num + 1}")

                except Exception as e:
                    logger.error(f"Error processing image {img_idx} on page {page_num + 1}: {str(e)}")

            return "\n\n".join(page_image_texts) if page_image_texts else None

        except Exception as e:
            logger.error(f"Error rendering page {page_num + 1} for image extraction: {str(e)}")
            return None
