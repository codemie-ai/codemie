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

"""Tests for DocxProcessor class."""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from codemie_tools.base.file_object import FileObject
from codemie_tools.file_analysis.docx.exceptions import (
    DocumentReadError,
    InvalidPageSelectionError,
)
from codemie_tools.file_analysis.docx.models import (
    DocumentContent,
    DocumentStructure,
    FormattingInfo,
    ImageData,
    Position,
    TableData,
    QueryType,
)
from codemie_tools.file_analysis.docx.processor import DocxProcessor


@pytest.fixture
def docx_processor():
    """Provides an instance of DocxProcessor."""
    return DocxProcessor(ocr_enabled=True)


@pytest.fixture
def temp_docx_file():
    """Fixture providing path to test DOCX file."""
    return Path(__file__).parent / "test.docx"


@pytest.fixture
def mock_document_content():
    """Create mock DocumentContent for testing."""
    return DocumentContent(
        text="Sample text content",
        structure=DocumentStructure(
            headers=[],
            paragraphs=[],
            sections=[],
            styles=[],
        ),
        formatting=FormattingInfo(),
        metadata={"page_count": 3, "word_count": 100},
        images=[
            ImageData(
                content=b"fake_image_data",
                format="png",
                text_content="Image text from OCR",
                position=Position(page=1, x=0.0, y=0.0),
                metadata={},
            )
        ],
        tables=[
            TableData(
                rows=[["A", "B"], ["C", "D"]],
                headers=["A", "B"],
                position=Position(page=1, x=0.0, y=0.0),
                metadata={},
            )
        ],
    )


@pytest.fixture
def mock_file_object():
    """Create mock FileObject for testing."""
    file_obj = Mock(spec=FileObject)
    file_obj.name = "test.docx"
    file_obj.content = b"fake_docx_content"
    return file_obj


class TestPagesAllHandling:
    """Tests for pages='all' parameter handling."""

    def test_read_document_pages_none(self, docx_processor, temp_docx_file):
        """Test reading document with pages=None (all pages)."""
        with patch.object(docx_processor.reader, "read_with_markitdown") as mock_read:
            mock_content = MagicMock(spec=DocumentContent)
            mock_read.return_value = mock_content

            result = docx_processor.read_document(str(temp_docx_file), QueryType.TEXT, pages=None)

            # Should not call _filter_content_by_pages
            assert result == mock_content

    def test_read_document_pages_all_string(self, docx_processor, temp_docx_file):
        """Test reading document with pages='all' (should process all pages)."""
        with patch.object(docx_processor.reader, "read_with_markitdown") as mock_read:
            mock_content = MagicMock(spec=DocumentContent)
            mock_read.return_value = mock_content

            # pages='all' should be handled like pages=None
            result = docx_processor.read_document(str(temp_docx_file), QueryType.TEXT, pages="all")

            # Should not call _filter_content_by_pages (all pages)
            assert result == mock_content

    def test_read_document_pages_specific(self, docx_processor, temp_docx_file, mock_document_content):
        """Test reading document with specific pages."""
        with (
            patch.object(docx_processor.reader, "read_with_markitdown") as mock_read,
            patch.object(docx_processor, "_filter_content_by_pages") as mock_filter,
        ):
            mock_read.return_value = mock_document_content
            mock_filtered = MagicMock(spec=DocumentContent)
            mock_filter.return_value = mock_filtered

            result = docx_processor.read_document(str(temp_docx_file), QueryType.TEXT, pages="1,2")

            # Should call _filter_content_by_pages
            mock_filter.assert_called_once_with(mock_document_content, "1,2")
            assert result == mock_filtered

    def test_read_document_from_bytes_pages_all(self, docx_processor, mock_file_object):
        """Test reading document from bytes with pages='all'."""
        with patch.object(docx_processor.reader, "read_from_bytes") as mock_read:
            mock_content = MagicMock(spec=DocumentContent)
            mock_read.return_value = mock_content

            result = docx_processor.read_document_from_bytes(
                mock_file_object.content,
                mock_file_object.name,
                QueryType.TEXT,
                pages="all",
            )

            # Should process all pages without filtering
            assert result == mock_content

    def test_parse_page_selection_raises_error_for_all(self, docx_processor):
        """Test that _parse_page_selection raises error for 'all' string."""
        with pytest.raises(InvalidPageSelectionError) as exc_info:
            docx_processor._parse_page_selection("all")

        assert "Invalid page selection" in str(exc_info.value)
        assert "all" in str(exc_info.value).lower()

    def test_parse_page_selection_single_page(self, docx_processor):
        """Test parsing single page number."""
        result = docx_processor._parse_page_selection("1")
        assert result == {1}

    def test_parse_page_selection_comma_separated(self, docx_processor):
        """Test parsing comma-separated page numbers."""
        result = docx_processor._parse_page_selection("1,3,5")
        assert result == {1, 3, 5}

    def test_parse_page_selection_range(self, docx_processor):
        """Test parsing page range."""
        result = docx_processor._parse_page_selection("1-4")
        assert result == {1, 2, 3, 4}

    def test_parse_page_selection_mixed(self, docx_processor):
        """Test parsing mixed page selection."""
        result = docx_processor._parse_page_selection("1,3,5-8")
        assert result == {1, 3, 5, 6, 7, 8}

    def test_parse_page_selection_empty_string(self, docx_processor):
        """Test parsing empty string raises error."""
        with pytest.raises(InvalidPageSelectionError):
            docx_processor._parse_page_selection("")

    def test_parse_page_selection_invalid_page_number(self, docx_processor):
        """Test parsing invalid page number raises error."""
        with pytest.raises(InvalidPageSelectionError) as exc_info:
            docx_processor._parse_page_selection("abc")

        assert "Invalid page number" in str(exc_info.value)

    def test_parse_page_selection_zero_page(self, docx_processor):
        """Test parsing zero page number raises error."""
        with pytest.raises(InvalidPageSelectionError) as exc_info:
            docx_processor._parse_page_selection("0")

        assert "Page numbers must be >= 1" in str(exc_info.value)

    def test_parse_page_selection_negative_page(self, docx_processor):
        """Test parsing negative page number raises error."""
        with pytest.raises(InvalidPageSelectionError):
            docx_processor._parse_page_selection("-1")


class TestProcessorEdgeCases:
    """Tests for edge cases and error handling."""

    def test_read_document_error_handling(self, docx_processor):
        """Test error handling when document cannot be read."""
        with patch.object(
            docx_processor.reader,
            "read_with_markitdown",
            side_effect=Exception("Read error"),
        ):
            with pytest.raises(DocumentReadError) as exc_info:
                docx_processor.read_document("invalid.docx", QueryType.TEXT)

            assert "Failed to read document" in str(exc_info.value)

    def test_read_document_from_bytes_error_handling(self, docx_processor):
        """Test error handling when document from bytes cannot be read."""
        with patch.object(
            docx_processor.reader,
            "read_from_bytes",
            side_effect=Exception("Read error"),
        ):
            with pytest.raises(DocumentReadError) as exc_info:
                docx_processor.read_document_from_bytes(b"invalid", "test.docx", QueryType.TEXT)

            assert "Failed to read document from bytes" in str(exc_info.value)

    def test_process_multiple_files_no_files(self, docx_processor):
        """Test processing with no files raises error."""
        with pytest.raises(ValueError) as exc_info:
            docx_processor.process_multiple_files([], "read")

        assert "No files provided" in str(exc_info.value)

    def test_process_multiple_files_unknown_operation(self, docx_processor, mock_file_object, mock_document_content):
        """Test processing with unknown operation raises error."""
        with patch.object(docx_processor, "read_document_from_bytes") as mock_read:
            mock_read.return_value = mock_document_content

            with pytest.raises(ValueError) as exc_info:
                docx_processor.process_multiple_files([mock_file_object], "invalid_operation")

            assert "Unknown operation" in str(exc_info.value)

    def test_process_single_file_table_extraction(self, docx_processor, mock_file_object, mock_document_content):
        """Test processing single file for table extraction."""
        with patch.object(docx_processor, "read_document_from_bytes") as mock_read:
            mock_read.return_value = mock_document_content

            result = docx_processor._process_single_file(mock_file_object, "extract_tables")

            assert isinstance(result, list)
            assert len(result) == 1
            assert result[0]["rows"] == [["A", "B"], ["C", "D"]]
            assert result[0]["headers"] == ["A", "B"]
