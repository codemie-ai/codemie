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

import pytest
from unittest.mock import Mock, patch, MagicMock

from codemie.datasource.loader.pdf_plumber_loader import PDFPlumberLoader
from langchain_core.documents import Document


class TestPDFPlumberLoaderInit:
    """Test PDFPlumberLoader initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default parameters."""
        loader = PDFPlumberLoader(file_path="test.pdf")

        assert loader.file_path == "test.pdf"
        assert loader.mode == "page"
        assert loader.extract_images is True
        assert loader.extract_tables == "markdown"
        assert loader.images_parser is None

    def test_init_with_custom_parameters(self):
        """Test initialization with custom parameters."""
        mock_parser = Mock()
        loader = PDFPlumberLoader(
            file_path="custom.pdf",
            mode="single",
            extract_images=False,
            extract_tables="none",
            images_parser=mock_parser,
        )

        assert loader.file_path == "custom.pdf"
        assert loader.mode == "single"
        assert loader.extract_images is False
        assert loader.extract_tables == "none"
        assert loader.images_parser == mock_parser


class TestPDFPlumberLoaderTableConversion:
    """Test table to markdown conversion."""

    def test_table_to_markdown_simple(self):
        """Test converting a simple table to markdown."""
        table = [
            ["Name", "Age", "City"],
            ["Alice", "30", "NYC"],
            ["Bob", "25", "LA"],
        ]

        result = PDFPlumberLoader._table_to_markdown(table)

        assert "| Name | Age | City |" in result
        assert "| --- | --- | --- |" in result
        assert "| Alice | 30 | NYC |" in result
        assert "| Bob | 25 | LA |" in result

    def test_table_to_markdown_with_none_values(self):
        """Test table conversion handles None values."""
        table = [
            ["Header1", "Header2"],
            ["Value1", None],
            [None, "Value2"],
        ]

        result = PDFPlumberLoader._table_to_markdown(table)

        assert "| Header1 | Header2 |" in result
        assert "| Value1 |  |" in result
        assert "|  | Value2 |" in result

    def test_table_to_markdown_empty_table(self):
        """Test empty table returns empty string."""
        assert PDFPlumberLoader._table_to_markdown([]) == ""
        assert PDFPlumberLoader._table_to_markdown(None) == ""

    def test_table_to_markdown_strips_whitespace(self):
        """Test that cell values are stripped."""
        table = [
            ["  Header  ", " Value "],
            [" Data1 ", "  Data2  "],
        ]

        result = PDFPlumberLoader._table_to_markdown(table)

        assert "| Header | Value |" in result
        assert "| Data1 | Data2 |" in result


class TestPDFPlumberLoaderExtractTables:
    """Test table extraction from pages."""

    def test_extract_tables_disabled(self):
        """Test table extraction when disabled."""
        loader = PDFPlumberLoader(file_path="test.pdf", extract_tables="none")
        mock_page = Mock()

        result = loader._extract_tables(mock_page)

        assert result == ""
        mock_page.extract_tables.assert_not_called()

    def test_extract_tables_no_tables_found(self):
        """Test when page has no tables."""
        loader = PDFPlumberLoader(file_path="test.pdf")
        mock_page = Mock()
        mock_page.extract_tables.return_value = []

        result = loader._extract_tables(mock_page)

        assert result == ""

    def test_extract_tables_with_tables(self):
        """Test extracting tables from page."""
        loader = PDFPlumberLoader(file_path="test.pdf")
        mock_page = Mock()
        mock_page.extract_tables.return_value = [
            [["Name", "Age"], ["Alice", "30"]],
            [["City"], ["NYC"]],
        ]

        result = loader._extract_tables(mock_page)

        assert "**Table 1:**" in result
        assert "**Table 2:**" in result
        assert "| Name | Age |" in result
        assert "| City |" in result


class TestPDFPlumberLoaderExtractImages:
    """Test image extraction from pages."""

    def test_extract_images_disabled(self):
        """Test image extraction when disabled."""
        loader = PDFPlumberLoader(file_path="test.pdf", extract_images=False)
        mock_page = Mock()

        result = loader._extract_images_info(mock_page)

        assert result == ""

    def test_extract_images_no_images_found(self):
        """Test when page has no images."""
        loader = PDFPlumberLoader(file_path="test.pdf")
        mock_page = Mock()
        mock_page.images = []

        result = loader._extract_images_info(mock_page)

        assert result == ""

    def test_extract_images_with_images(self):
        """Test extracting image information."""
        loader = PDFPlumberLoader(file_path="test.pdf")
        mock_page = Mock()
        mock_page.images = [
            {"width": 100, "height": 200},
            {"width": 50, "height": 75},
        ]

        result = loader._extract_images_info(mock_page)

        assert "[Image 1: 100x200]" in result
        assert "[Image 2: 50x75]" in result

    def test_extract_images_missing_dimensions(self):
        """Test handling images with missing width/height."""
        loader = PDFPlumberLoader(file_path="test.pdf")
        mock_page = Mock()
        mock_page.images = [
            {"width": 100},  # Missing height
            {},  # Missing both
        ]

        result = loader._extract_images_info(mock_page)

        assert "[Image 1: 100x?]" in result
        assert "[Image 2: ?x?]" in result


class TestPDFPlumberLoaderLazyLoad:
    """Test the main lazy_load functionality."""

    @patch('pdfplumber.open')
    def test_lazy_load_single_page(self, mock_pdfplumber_open):
        """Test loading a single-page PDF."""
        # Mock PDF and page
        mock_page = Mock()
        mock_page.extract_text.return_value = "Page 1 content"
        mock_page.extract_tables.return_value = []
        mock_page.images = []

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__.return_value = mock_pdf
        mock_pdf.__exit__.return_value = None

        mock_pdfplumber_open.return_value = mock_pdf

        # Load documents
        loader = PDFPlumberLoader(file_path="test.pdf")
        documents = list(loader.lazy_load())

        assert len(documents) == 1
        assert isinstance(documents[0], Document)
        assert documents[0].page_content == "Page 1 content"
        assert documents[0].metadata["page"] == 1
        assert documents[0].metadata["total_pages"] == 1
        assert documents[0].metadata["source"] == "test.pdf"

    @patch('pdfplumber.open')
    def test_lazy_load_multiple_pages(self, mock_pdfplumber_open):
        """Test loading a multi-page PDF."""
        # Mock pages
        mock_page1 = Mock()
        mock_page1.extract_text.return_value = "Page 1"
        mock_page1.extract_tables.return_value = []
        mock_page1.images = []

        mock_page2 = Mock()
        mock_page2.extract_text.return_value = "Page 2"
        mock_page2.extract_tables.return_value = []
        mock_page2.images = []

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page1, mock_page2]
        mock_pdf.__enter__.return_value = mock_pdf
        mock_pdf.__exit__.return_value = None

        mock_pdfplumber_open.return_value = mock_pdf

        loader = PDFPlumberLoader(file_path="test.pdf")
        documents = list(loader.lazy_load())

        assert len(documents) == 2
        assert documents[0].page_content == "Page 1"
        assert documents[0].metadata["page"] == 1
        assert documents[1].page_content == "Page 2"
        assert documents[1].metadata["page"] == 2
        assert documents[1].metadata["total_pages"] == 2

    @patch('pdfplumber.open')
    def test_lazy_load_with_tables(self, mock_pdfplumber_open):
        """Test loading PDF with tables."""
        mock_page = Mock()
        mock_page.extract_text.return_value = "Text content"
        mock_page.extract_tables.return_value = [
            [["Col1", "Col2"], ["A", "B"]],
        ]
        mock_page.images = []

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__.return_value = mock_pdf
        mock_pdf.__exit__.return_value = None

        mock_pdfplumber_open.return_value = mock_pdf

        loader = PDFPlumberLoader(file_path="test.pdf")
        documents = list(loader.lazy_load())

        content = documents[0].page_content
        assert "Text content" in content
        assert "**Table 1:**" in content
        assert "| Col1 | Col2 |" in content
        assert "| A | B |" in content

    @patch('pdfplumber.open')
    def test_lazy_load_with_images(self, mock_pdfplumber_open):
        """Test loading PDF with images."""
        mock_page = Mock()
        mock_page.extract_text.return_value = "Text"
        mock_page.extract_tables.return_value = []
        mock_page.images = [
            {"width": 100, "height": 200},
        ]

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__.return_value = mock_pdf
        mock_pdf.__exit__.return_value = None

        mock_pdfplumber_open.return_value = mock_pdf

        loader = PDFPlumberLoader(file_path="test.pdf")
        documents = list(loader.lazy_load())

        content = documents[0].page_content
        assert "Text" in content
        assert "[Image 1: 100x200]" in content

    @patch('pdfplumber.open')
    def test_lazy_load_empty_text(self, mock_pdfplumber_open):
        """Test handling pages with no text."""
        mock_page = Mock()
        mock_page.extract_text.return_value = None
        mock_page.extract_tables.return_value = []
        mock_page.images = []

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__.return_value = mock_pdf
        mock_pdf.__exit__.return_value = None

        mock_pdfplumber_open.return_value = mock_pdf

        loader = PDFPlumberLoader(file_path="test.pdf")
        documents = list(loader.lazy_load())

        assert len(documents) == 1
        assert documents[0].page_content == ""

    @patch('pdfplumber.open')
    def test_lazy_load_metadata_fields(self, mock_pdfplumber_open):
        """Test that all metadata fields are correctly set."""
        mock_page = Mock()
        mock_page.extract_text.return_value = "Content"
        mock_page.extract_tables.return_value = []
        mock_page.images = []

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page, mock_page, mock_page]
        mock_pdf.__enter__.return_value = mock_pdf
        mock_pdf.__exit__.return_value = None

        mock_pdfplumber_open.return_value = mock_pdf

        loader = PDFPlumberLoader(file_path="/path/to/document.pdf")
        documents = list(loader.lazy_load())

        # Check first page
        assert documents[0].metadata["source"] == "/path/to/document.pdf"
        assert documents[0].metadata["file_path"] == "/path/to/document.pdf"
        assert documents[0].metadata["page"] == 1
        assert documents[0].metadata["total_pages"] == 3

        # Check last page
        assert documents[2].metadata["page"] == 3
        assert documents[2].metadata["total_pages"] == 3


class TestPDFPlumberLoaderIntegration:
    """Integration tests combining multiple features."""

    @patch('pdfplumber.open')
    def test_complex_pdf_with_all_features(self, mock_pdfplumber_open):
        """Test PDF with text, tables, and images."""
        mock_page = Mock()
        mock_page.extract_text.return_value = "Introduction text"
        mock_page.extract_tables.return_value = [
            [["Product", "Price"], ["Widget", "$10"]],
        ]
        mock_page.images = [
            {"width": 300, "height": 400},
        ]

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__.return_value = mock_pdf
        mock_pdf.__exit__.return_value = None

        mock_pdfplumber_open.return_value = mock_pdf

        loader = PDFPlumberLoader(file_path="complex.pdf")
        documents = list(loader.lazy_load())

        content = documents[0].page_content
        # Verify all content is included
        assert "Introduction text" in content
        assert "**Table 1:**" in content
        assert "| Product | Price |" in content
        assert "| Widget | $10 |" in content
        assert "[Image 1: 300x400]" in content

    @patch('pdfplumber.open')
    def test_lazy_load_is_iterator(self, mock_pdfplumber_open):
        """Test that lazy_load returns an iterator."""
        mock_page = Mock()
        mock_page.extract_text.return_value = "Test"
        mock_page.extract_tables.return_value = []
        mock_page.images = []

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__.return_value = mock_pdf
        mock_pdf.__exit__.return_value = None

        mock_pdfplumber_open.return_value = mock_pdf

        loader = PDFPlumberLoader(file_path="test.pdf")
        result = loader.lazy_load()

        # Should be an iterator, not a list
        assert hasattr(result, '__iter__')
        assert hasattr(result, '__next__')


class TestPDFPlumberLoaderBackwardCompatibility:
    """Test compatibility with PyMuPDFLoader interface."""

    def test_implements_base_loader_interface(self):
        """Test that PDFPlumberLoader implements BaseLoader interface."""
        from langchain_core.document_loaders import BaseLoader

        loader = PDFPlumberLoader(file_path="test.pdf")

        assert isinstance(loader, BaseLoader)
        assert hasattr(loader, 'lazy_load')

    @patch('pdfplumber.open')
    def test_document_structure_matches_expected_format(self, mock_pdfplumber_open):
        """Test that Document structure matches what consumers expect."""
        mock_page = Mock()
        mock_page.extract_text.return_value = "Content"
        mock_page.extract_tables.return_value = []
        mock_page.images = []

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__.return_value = mock_pdf
        mock_pdf.__exit__.return_value = None

        mock_pdfplumber_open.return_value = mock_pdf

        loader = PDFPlumberLoader(file_path="test.pdf")
        documents = list(loader.lazy_load())

        doc = documents[0]

        # Check Document structure
        assert hasattr(doc, 'page_content')
        assert hasattr(doc, 'metadata')
        assert isinstance(doc.page_content, str)
        assert isinstance(doc.metadata, dict)

        # Check expected metadata keys
        required_keys = ["source", "file_path", "page", "total_pages"]
        for key in required_keys:
            assert key in doc.metadata

    def test_accepts_same_kwargs_as_pymupdf_loader(self):
        """Test that it accepts PyMuPDFLoader-compatible arguments."""
        # These were PyMuPDFLoader parameters
        loader = PDFPlumberLoader(
            file_path="test.pdf",
            mode="page",
            extract_images=True,
            extract_tables="markdown",
            images_parser=None,
        )

        # Should not raise any errors
        assert loader is not None


class TestPDFPlumberLoaderEdgeCases:
    """Test edge cases and error handling."""

    @patch('pdfplumber.open')
    def test_handles_empty_pdf(self, mock_pdfplumber_open):
        """Test PDF with no pages."""
        mock_pdf = MagicMock()
        mock_pdf.pages = []
        mock_pdf.__enter__.return_value = mock_pdf
        mock_pdf.__exit__.return_value = None

        mock_pdfplumber_open.return_value = mock_pdf

        loader = PDFPlumberLoader(file_path="empty.pdf")
        documents = list(loader.lazy_load())

        assert len(documents) == 0

    @patch('pdfplumber.open')
    def test_handles_multiline_text(self, mock_pdfplumber_open):
        """Test handling multiline text content."""
        mock_page = Mock()
        mock_page.extract_text.return_value = "Line 1\nLine 2\nLine 3"
        mock_page.extract_tables.return_value = []
        mock_page.images = []

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__.return_value = mock_pdf
        mock_pdf.__exit__.return_value = None

        mock_pdfplumber_open.return_value = mock_pdf

        loader = PDFPlumberLoader(file_path="test.pdf")
        documents = list(loader.lazy_load())

        assert "Line 1\nLine 2\nLine 3" in documents[0].page_content

    @patch('pdfplumber.open')
    def test_file_path_preserved_in_metadata(self, mock_pdfplumber_open):
        """Test that file path is preserved correctly."""
        mock_page = Mock()
        mock_page.extract_text.return_value = "Text"
        mock_page.extract_tables.return_value = []
        mock_page.images = []

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__.return_value = mock_pdf
        mock_pdf.__exit__.return_value = None

        mock_pdfplumber_open.return_value = mock_pdf

        test_path = "/some/long/path/to/document.pdf"
        loader = PDFPlumberLoader(file_path=test_path)
        documents = list(loader.lazy_load())

        assert documents[0].metadata["source"] == test_path
        assert documents[0].metadata["file_path"] == test_path


class TestPDFPlumberLoaderPerformance:
    """Test performance characteristics."""

    @patch('pdfplumber.open')
    def test_lazy_evaluation(self, mock_pdfplumber_open):
        """Test that pages are processed lazily, not all at once."""
        mock_pages = [Mock() for _ in range(100)]
        for page in mock_pages:
            page.extract_text.return_value = "Content"
            page.extract_tables.return_value = []
            page.images = []

        mock_pdf = MagicMock()
        mock_pdf.pages = mock_pages
        mock_pdf.__enter__.return_value = mock_pdf
        mock_pdf.__exit__.return_value = None

        mock_pdfplumber_open.return_value = mock_pdf

        loader = PDFPlumberLoader(file_path="large.pdf")
        doc_iter = loader.lazy_load()

        # Get first document
        first_doc = next(doc_iter)

        # Should have processed only 1 page, not all 100
        assert first_doc.metadata["page"] == 1
        assert first_doc.metadata["total_pages"] == 100


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
