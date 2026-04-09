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

"""
Additional error handling tests for PDF processor with pdfplumber.

These tests cover error scenarios that were handled differently in pymupdf.
pdfplumber doesn't have an 'is_closed' attribute, so we test different error conditions.
"""

from unittest.mock import Mock, patch
import pytest
import pdfplumber

from codemie_tools.file_analysis.pdf.processor import PdfProcessor


@pytest.fixture
def pdf_processor():
    """Create a PDF processor instance for testing."""
    mock_chat_model = Mock()
    return PdfProcessor(chat_model=mock_chat_model)


# NOTE: The old pymupdf tests for 'is_closed' attribute were removed because:
# 1. pdfplumber doesn't have an 'is_closed' attribute (uses context managers)
# 2. Accessing closed documents naturally raises errors when trying to use .pages
# 3. The error handling is implicit - Python raises AttributeError/TypeError naturally
#
# This is INTENTIONAL and SAFE - error handling happens at a different level.


def test_extract_text_with_empty_pages_list(pdf_processor):
    """Test extracting text when pages list is empty (graceful handling)."""
    mock_pdf = Mock(spec=pdfplumber.PDF)
    mock_pdf.pages = []  # Empty pages list

    # Should handle gracefully and return empty result
    result = pdf_processor.extract_text_as_markdown(mock_pdf)
    assert result == ""  # Empty markdown for no pages


def test_get_total_pages_with_empty_pages_list(pdf_processor):
    """Test getting page count when pages list is empty."""
    mock_pdf = Mock(spec=pdfplumber.PDF)
    mock_pdf.pages = []  # Empty pages list

    result = pdf_processor.get_total_pages(mock_pdf)
    assert result == "0"  # Zero pages


def test_process_pdf_with_corrupted_bytes(pdf_processor):
    """Test processing corrupted PDF bytes."""
    corrupted_bytes = b"This is not a valid PDF"

    # This should raise ValueError when trying to open (see open_pdf_document method)
    with pytest.raises(ValueError):
        pdf_processor.process_pdf(corrupted_bytes)


def test_extract_text_from_corrupted_bytes(pdf_processor):
    """Test extracting text from corrupted PDF bytes."""
    corrupted_bytes = b"Not a PDF"

    # This should raise ValueError when pdfplumber fails to open the bytes
    with pytest.raises(ValueError):
        pdf_processor.extract_text_as_markdown(corrupted_bytes)


def test_process_pdf_with_invalid_page_numbers(pdf_processor):
    """Test processing with page numbers that don't exist."""
    mock_pdf = Mock(spec=pdfplumber.PDF)
    mock_page = Mock()
    mock_page.extract_text.return_value = "Test text"
    mock_page.images = []

    # Only 2 pages available
    mock_pdf.pages = [mock_page, mock_page]

    # Try to access page 10 (doesn't exist) - should be handled gracefully
    result = pdf_processor._process_pdf_document(mock_pdf, pages=[10])

    # Should handle gracefully - either skip or return empty
    assert isinstance(result, str)


def test_process_pdf_with_page_extraction_error(pdf_processor):
    """Test handling errors during page text extraction."""
    mock_pdf = Mock(spec=pdfplumber.PDF)
    mock_page = Mock()
    mock_page.extract_text.side_effect = RuntimeError("Extraction failed")
    mock_page.images = []

    mock_pdf.pages = [mock_page]

    # Should handle extraction errors gracefully
    with pytest.raises(RuntimeError):
        pdf_processor._process_pdf_document(mock_pdf, pages=None)


def test_open_pdf_document_with_invalid_bytes(pdf_processor):
    """Test opening PDF with completely invalid data."""
    invalid_data = b""  # Empty bytes

    # open_pdf_document catches all exceptions and raises ValueError
    with pytest.raises(ValueError):
        pdf_processor.open_pdf_document(invalid_data)


def test_process_pdf_with_none_pages_attribute(pdf_processor):
    """Test processing when PDF pages attribute is None (closed scenario)."""
    mock_pdf = Mock(spec=pdfplumber.PDF)
    mock_pdf.pages = None

    # Should raise an error or handle gracefully
    with pytest.raises((TypeError, AttributeError)):
        pdf_processor._process_pdf_document(mock_pdf)


def test_table_extraction_with_invalid_data():
    """Test table to markdown conversion with invalid data."""
    # Test with None
    result = PdfProcessor._table_to_markdown(None)
    assert result == ""

    # Test with empty list
    result = PdfProcessor._table_to_markdown([])
    assert result == ""

    # Test with list of Nones
    result = PdfProcessor._table_to_markdown([None, None])
    assert result == ""

    # Test with empty rows
    result = PdfProcessor._table_to_markdown([[], []])
    assert result == ""


def test_process_pdf_auto_close_behavior(pdf_processor):
    """Test that PDF is properly closed when opened from bytes."""
    mock_pdf = Mock(spec=pdfplumber.PDF)
    mock_page = Mock()
    mock_page.extract_text.return_value = "Test"
    mock_page.images = []
    mock_pdf.pages = [mock_page]
    mock_pdf.close = Mock()

    # Mock open_pdf_document to return our mock
    with patch.object(pdf_processor, 'open_pdf_document', return_value=mock_pdf):
        # Process with bytes (should auto-close)
        result = pdf_processor.process_pdf(b"fake pdf bytes")

        # Verify close was called
        mock_pdf.close.assert_called_once()
        assert "Test" in result


def test_process_pdf_no_auto_close_for_objects(pdf_processor):
    """Test that PDF is NOT closed when passed as object."""
    mock_pdf = Mock(spec=pdfplumber.PDF)
    mock_page = Mock()
    mock_page.extract_text.return_value = "Test"
    mock_page.images = []
    mock_pdf.pages = [mock_page]
    mock_pdf.close = Mock()

    # Process with PDF object (should NOT auto-close)
    result = pdf_processor._process_pdf_document(mock_pdf)

    # Verify close was NOT called (caller is responsible)
    mock_pdf.close.assert_not_called()
    assert "Test" in result


def test_extract_text_as_markdown_closes_pdf_obj_not_bytes():
    """Regression: extract_text_as_markdown must close the opened pdf_obj, not the
    original bytes parameter.  Previously it called pdf_document.close() which
    raised 'bytes' object has no attribute 'close'."""
    mock_page = Mock()
    mock_page.extract_text.return_value = "Page text"
    mock_page.extract_tables.return_value = []

    mock_pdf_obj = Mock(spec=pdfplumber.PDF)
    mock_pdf_obj.pages = [mock_page]
    mock_pdf_obj.close = Mock()

    raw_bytes = b"fake-pdf-content"

    with patch("pdfplumber.open", return_value=mock_pdf_obj):
        result = PdfProcessor.extract_text_as_markdown(raw_bytes)

    # Should succeed without AttributeError
    assert "Page text" in result
    # The opened pdf object must be closed — not the raw bytes
    mock_pdf_obj.close.assert_called_once()
