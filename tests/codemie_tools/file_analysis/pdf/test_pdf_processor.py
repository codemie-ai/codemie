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

from unittest.mock import Mock, patch

import pytest
import pdfplumber

from codemie_tools.base.file_object import FileObject
from codemie_tools.file_analysis.pdf.processor import PdfProcessor
from codemie_tools.utils.image_processor import ImageProcessor


def test_init_with_chat_model(pdf_processor):
    """Test initialization with a chat model."""
    assert pdf_processor.image_processor is not None
    assert isinstance(pdf_processor.image_processor, ImageProcessor)


def test_init_without_chat_model():
    """Test initialization without a chat model."""
    from codemie_tools.file_analysis.pdf.processor import PdfProcessor

    processor = PdfProcessor()
    assert processor.image_processor is None


@patch('pdfplumber.open')
def test_open_pdf_document(mock_open, pdf_processor):
    """Test opening a PDF document from file content."""
    # Set up mock
    mock_doc = Mock(spec=pdfplumber.PDF)
    mock_open.return_value = mock_doc

    # Test
    result = pdf_processor.open_pdf_document(b"pdf_content")

    # Assertions
    assert result == mock_doc
    assert mock_open.called


def test_extract_text_as_markdown_all_pages(pdf_processor):
    """Test extracting text as markdown from all pages."""
    # Set up mock PDF with pages
    mock_doc = Mock(spec=pdfplumber.PDF)
    mock_page1 = Mock()
    mock_page1.extract_text.return_value = "Page 1 text"
    mock_page1.extract_tables.return_value = []

    mock_page2 = Mock()
    mock_page2.extract_text.return_value = "Page 2 text"
    mock_page2.extract_tables.return_value = []

    mock_doc.pages = [mock_page1, mock_page2]

    # Call method
    result = pdf_processor.extract_text_as_markdown(mock_doc)

    # Assertions
    assert "Page 1 text" in result
    assert "Page 2 text" in result


def test_extract_text_as_markdown_specific_pages(pdf_processor):
    """Test extracting text as markdown from specific pages."""
    # Set up mock PDF with pages
    mock_doc = Mock(spec=pdfplumber.PDF)
    mock_page1 = Mock()
    mock_page1.extract_text.return_value = "Page 1 text"
    mock_page1.extract_tables.return_value = []

    mock_page2 = Mock()
    mock_page2.extract_text.return_value = "Page 2 text"
    mock_page2.extract_tables.return_value = []

    mock_doc.pages = [mock_page1, mock_page2]

    # Call method with specific pages (1-based index)
    result = pdf_processor.extract_text_as_markdown(mock_doc, pages=[2])

    # Assertions - should only contain page 2
    assert "Page 2 text" in result
    assert "Page 1 text" not in result


@patch.object(PdfProcessor, 'open_pdf_document')
@patch.object(PdfProcessor, 'extract_text_as_markdown')
def test_extract_text_as_markdown_from_files(mock_extract, mock_open, pdf_processor):
    """Test extracting text as markdown from multiple files."""
    # Set up mocks
    mock_doc1 = Mock(spec=pdfplumber.PDF)
    mock_doc1.close = Mock()
    mock_doc2 = Mock(spec=pdfplumber.PDF)
    mock_doc2.close = Mock()
    mock_open.side_effect = [mock_doc1, mock_doc2]
    mock_extract.side_effect = ["Content from file 1", "Content from file 2"]

    # Create file objects
    files = [
        FileObject(name="test1.pdf", content=b"content1", mime_type="application/pdf", owner="test"),
        FileObject(name="test2.pdf", content=b"content2", mime_type="application/pdf", owner="test"),
    ]

    # Call method
    result = pdf_processor.extract_text_as_markdown_from_files(files, pages=[1], page_chunks=True)

    # Assertions
    assert "###SOURCE DOCUMENT###" in result
    assert "**Source:** test1.pdf" in result
    assert "**File Content:**" in result
    assert "Content from file 1" in result
    assert "Content from file 2" in result

    # Verify calls
    mock_open.assert_any_call(b"content1")
    mock_open.assert_any_call(b"content2")
    mock_extract.assert_any_call(mock_doc1, [1], True)
    mock_extract.assert_any_call(mock_doc2, [1], True)


def test_extract_text_as_markdown_null_document(pdf_processor):
    """Test behavior when null PDF document is provided."""
    with pytest.raises(ValueError):
        pdf_processor.extract_text_as_markdown(None)


def test_get_total_pages_success(pdf_processor):
    """Test getting the total number of pages successfully."""
    mock_doc = Mock(spec=pdfplumber.PDF)
    mock_doc.pages = [Mock()] * 10  # 10 pages

    result = pdf_processor.get_total_pages(mock_doc)
    assert result == "10"


def test_get_total_pages_null_document(pdf_processor):
    """Test behavior when null PDF document is provided."""
    with pytest.raises(ValueError):
        pdf_processor.get_total_pages(None)


@patch.object(PdfProcessor, 'open_pdf_document')
def test_get_total_pages_from_files(mock_open, pdf_processor):
    """Test getting total pages from multiple files."""
    # Set up mocks
    mock_doc1 = Mock(spec=pdfplumber.PDF)
    mock_doc1.pages = [Mock()] * 5
    mock_doc1.close = Mock()

    mock_doc2 = Mock(spec=pdfplumber.PDF)
    mock_doc2.pages = [Mock()] * 10
    mock_doc2.close = Mock()

    mock_open.side_effect = [mock_doc1, mock_doc2]

    # Create file objects
    files = [
        FileObject(name="test1.pdf", content=b"content1", mime_type="application/pdf", owner="test"),
        FileObject(name="test2.pdf", content=b"content2", mime_type="application/pdf", owner="test"),
    ]

    # Call method
    result = pdf_processor.get_total_pages_from_files(files)

    # Assertions
    assert "PDF PAGE COUNT SUMMARY" in result
    assert "**Total pages across all files:** 15" in result
    assert "**Breakdown by file:**" in result
    assert "test1.pdf: 5 pages" in result
    assert "test2.pdf: 10 pages" in result


@patch.object(PdfProcessor, '_process_pdf_document')
def test_process_pdf_success(mock_process, pdf_processor):
    """Test successful PDF processing."""
    # Set up mock
    mock_doc = Mock(spec=pdfplumber.PDF)
    expected_content = "PDF content with images"
    mock_process.return_value = expected_content

    # Call method
    result = pdf_processor.process_pdf(mock_doc)

    # Assertions
    assert result == expected_content
    mock_process.assert_called_once_with(mock_doc, None)


@patch.object(PdfProcessor, 'open_pdf_document')
@patch.object(PdfProcessor, '_process_pdf_document')
def test_process_pdf_files(mock_process, mock_open, pdf_processor):
    """Test processing multiple PDF files."""
    # Set up mocks
    mock_doc1 = Mock(spec=pdfplumber.PDF)
    mock_doc1.close = Mock()
    mock_doc2 = Mock(spec=pdfplumber.PDF)
    mock_doc2.close = Mock()

    mock_open.side_effect = [mock_doc1, mock_doc2]
    mock_process.side_effect = ["Content from file 1", "Content from file 2"]

    # Create file objects
    files = [
        FileObject(name="test1.pdf", content=b"content1", mime_type="application/pdf", owner="test"),
        FileObject(name="test2.pdf", content=b"content2", mime_type="application/pdf", owner="test"),
    ]

    # Call method
    result = pdf_processor.process_pdf_files(files, pages=[1, 2])

    # Assertions
    assert "###SOURCE DOCUMENT###" in result
    assert "**Source:** test1.pdf" in result
    assert "**File Content:**" in result
    assert "Content from file 1" in result
    assert "Content from file 2" in result

    # Verify calls
    mock_open.assert_any_call(b"content1")
    mock_open.assert_any_call(b"content2")
    mock_process.assert_any_call(mock_doc1, [1, 2])
    mock_process.assert_any_call(mock_doc2, [1, 2])


def test_process_pdf_null_document(pdf_processor):
    """Test behavior when null PDF document is provided."""
    with pytest.raises(ValueError):
        pdf_processor.process_pdf(None)


@patch.object(PdfProcessor, '_process_page_images')
def test_process_pdf_document(mock_process_images, pdf_processor):
    """Test processing a PDF document with both text and images."""
    # Set up mock document and pages
    mock_doc = Mock(spec=pdfplumber.PDF)

    mock_page1 = Mock()
    mock_page1.extract_text.return_value = "Page 1 text"

    mock_page2 = Mock()
    mock_page2.extract_text.return_value = "Page 2 text"

    mock_doc.pages = [mock_page1, mock_page2]

    # Configure the mock image processor to return image text
    mock_process_images.side_effect = ["Page 1 image text", "Page 2 image text"]

    # Call the method
    result = pdf_processor._process_pdf_document(mock_doc)

    # Assertions
    assert "Page 1 PDF Text" in result
    assert "Page 1 text" in result
    assert "Page 2 PDF Text" in result
    assert "Page 2 text" in result
    assert "Page 1 image text" in result
    assert "Page 2 image text" in result

    # Verify _process_page_images calls
    assert mock_process_images.call_count == 2


def test_process_page_images_no_images(pdf_processor):
    """Test behavior when page has no images."""
    # Set up mocks
    mock_page = Mock()
    mock_page.images = []

    # Call method
    result = pdf_processor._process_page_images(mock_page, 0)

    # Assertion
    assert result is None


@patch.object(ImageProcessor, 'extract_text_from_image_bytes')
def test_process_page_images_with_images(mock_extract_text, pdf_processor):
    """Test processing page images when images are present."""
    # Set up mocks
    mock_page = Mock()
    mock_page.images = [
        {"x0": 0, "top": 0, "x1": 100, "bottom": 100},
        {"x0": 100, "top": 100, "x1": 200, "bottom": 200},
    ]

    # Mock the page rendering
    mock_page_image = Mock()
    mock_pil_image1 = Mock()
    mock_pil_image2 = Mock()
    mock_page_image.original.crop.side_effect = [mock_pil_image1, mock_pil_image2]
    mock_page.to_image.return_value = mock_page_image

    # Mock PIL Image save to return bytes
    def mock_save(byte_arr, format):
        byte_arr.write(b"fake_image_bytes")

    mock_pil_image1.save = Mock(side_effect=mock_save)
    mock_pil_image2.save = Mock(side_effect=mock_save)

    # Mock text extraction from images
    mock_extract_text.side_effect = ["Text from image 1", "Text from image 2"]

    # Call the method
    result = pdf_processor._process_page_images(mock_page, 0)

    # Assertions
    assert "Page 1 Image 1 Text" in result
    assert "Text from image 1" in result
    assert "Page 1 Image 2 Text" in result
    assert "Text from image 2" in result

    # Verify extract_text_from_image_bytes calls
    assert mock_extract_text.call_count == 2


@patch.object(ImageProcessor, 'extract_text_from_image_bytes')
def test_process_page_images_extraction_error(mock_extract_text, pdf_processor):
    """Test handling errors during image extraction."""
    # Set up mocks
    mock_page = Mock()
    mock_page.images = [
        {"x0": 0, "top": 0, "x1": 100, "bottom": 100},
    ]

    # Make to_image raise an exception
    mock_page.to_image.side_effect = Exception("Extract image error")

    # Call the method
    result = pdf_processor._process_page_images(mock_page, 0)

    # Assertions
    assert result is None
    mock_extract_text.assert_not_called()


@patch.object(ImageProcessor, 'extract_text_from_image_bytes')
def test_process_page_images_empty_text(mock_extract_text, pdf_processor):
    """Test behavior when extracted image text is empty."""
    # Set up mocks
    mock_page = Mock()
    mock_page.images = [
        {"x0": 0, "top": 0, "x1": 100, "bottom": 100},
    ]

    # Mock the page rendering
    mock_page_image = Mock()
    mock_pil_image = Mock()
    mock_page_image.original.crop.return_value = mock_pil_image
    mock_page.to_image.return_value = mock_page_image

    # Mock PIL Image save
    def mock_save(byte_arr, format):
        byte_arr.write(b"fake_image_bytes")

    mock_pil_image.save = Mock(side_effect=mock_save)

    # Mock text extraction returning empty string
    mock_extract_text.return_value = "   "  # Just whitespace

    # Call the method
    result = pdf_processor._process_page_images(mock_page, 0)

    # Assertions
    assert result is None  # Should return None when no text is found
    mock_extract_text.assert_called_once()
