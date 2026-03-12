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

from typing import Iterator
import pdfplumber
from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document


class PDFPlumberLoader(BaseLoader):
    """
    Load PDF files using pdfplumber with full table and image extraction.

    Args:
        file_path: Path to the PDF file
        mode: Loading mode ("page" for per-page documents)
        extract_images: Whether to note images in the content
        extract_tables: Format for table extraction ("markdown" for markdown tables)
        images_parser: Optional parser for image content (for OCR)
        **kwargs: Additional arguments for compatibility
    """

    def __init__(
        self,
        file_path: str,
        mode: str = "page",
        extract_images: bool = True,
        extract_tables: str = "markdown",
        images_parser=None,
        **kwargs,
    ):
        self.file_path = file_path
        self.mode = mode
        self.extract_images = extract_images
        self.extract_tables = extract_tables
        self.images_parser = images_parser
        self.kwargs = kwargs

    def _extract_tables(self, page) -> str:
        """Extract tables from a page and convert to markdown.

        Args:
            page: pdfplumber page object

        Returns:
            Markdown-formatted tables
        """
        if self.extract_tables != "markdown":
            return ""

        tables = page.extract_tables()
        if not tables:
            return ""

        table_parts = []
        for table_idx, table in enumerate(tables, start=1):
            if table:
                table_md = self._table_to_markdown(table)
                table_parts.append(f"\n\n**Table {table_idx}:**\n{table_md}\n")

        return "".join(table_parts)

    def _extract_images_info(self, page) -> str:
        """Extract image information from a page.

        Args:
            page: pdfplumber page object

        Returns:
            Image metadata as text
        """
        if not self.extract_images:
            return ""

        images = page.images
        if not images:
            return ""

        image_parts = []
        for img_idx, img in enumerate(images, start=1):
            img_info = f"[Image {img_idx}: {img.get('width', '?')}x{img.get('height', '?')}]"
            image_parts.append(f"\n\n{img_info}\n")

        return "".join(image_parts)

    def lazy_load(self) -> Iterator[Document]:
        """
        Lazy load PDF pages as Documents.

        Yields:
            Document objects with page content and metadata
        """
        with pdfplumber.open(self.file_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                # Extract text
                text = page.extract_text() or ""

                # Add tables
                text += self._extract_tables(page)

                # Add image info
                text += self._extract_images_info(page)

                yield Document(
                    page_content=text,
                    metadata={
                        "source": self.file_path,
                        "file_path": self.file_path,
                        "page": page_num,
                        "total_pages": len(pdf.pages),
                    },
                )

    @staticmethod
    def _table_to_markdown(table):
        """
        Convert table array to markdown format.

        Args:
            table: List of lists representing table rows

        Returns:
            Markdown-formatted table string
        """
        if not table or not any(table):
            return ""

        md_lines = []
        for i, row in enumerate(table):
            if not row:
                continue
            # Convert None to empty string, handle various cell types
            cells = [str(cell or "").strip() for cell in row]
            md_lines.append("| " + " | ".join(cells) + " |")

            # Add header separator after first row
            if i == 0:
                md_lines.append("| " + " | ".join(["---"] * len(cells)) + " |")

        return "\n".join(md_lines)
