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

import pdfplumber

from codemie_tools.base.file_object import FileObject
from codemie_tools.file_analysis.pdf.processor import PdfProcessor


@patch.object(PdfProcessor, 'open_pdf_document')
@patch.object(PdfProcessor, '_process_pdf_document')
def test_process_pdf_files_single_file(mock_process, mock_open, pdf_processor):
    """Test processing a single PDF file."""
    # Set up mocks
    mock_doc = Mock(spec=pdfplumber.PDF)
    mock_doc.close = Mock()
    mock_open.return_value = mock_doc
    mock_process.return_value = "Content from single file"

    # Create file objects
    files = [FileObject(name="test.pdf", content=b"content", mime_type="application/pdf", owner="test")]

    # Call method
    result = pdf_processor.process_pdf_files(files, pages=[1, 2])

    # Assertions - for single file we return the content directly without headers
    assert result == "Content from single file"

    # Verify calls
    mock_open.assert_called_once_with(b"content")
    mock_process.assert_called_once_with(mock_doc, [1, 2])


@patch.object(PdfProcessor, 'open_pdf_document')
@patch.object(PdfProcessor, 'extract_text_as_markdown')
def test_extract_text_as_markdown_from_files_single_file(mock_extract, mock_open, pdf_processor):
    """Test extracting text as markdown from a single file."""
    # Set up mocks
    mock_doc = Mock(spec=pdfplumber.PDF)
    mock_doc.close = Mock()
    mock_open.return_value = mock_doc
    mock_extract.return_value = "Content from single file"

    # Create file objects
    files = [FileObject(name="test.pdf", content=b"content", mime_type="application/pdf", owner="test")]

    # Call method
    result = pdf_processor.extract_text_as_markdown_from_files(files, pages=[1], page_chunks=True)

    # Assertions - for single file we return the content directly without headers
    assert result == "Content from single file"

    # Verify calls
    mock_open.assert_called_once_with(b"content")
    mock_extract.assert_called_once_with(mock_doc, [1], True)


@patch.object(PdfProcessor, 'open_pdf_document')
def test_get_total_pages_from_files_single_file(mock_open, pdf_processor):
    """Test getting total pages from a single file."""
    # Set up mocks
    mock_doc = Mock(spec=pdfplumber.PDF)
    mock_doc.pages = [Mock()] * 5
    mock_doc.close = Mock()
    mock_open.return_value = mock_doc

    # Create file objects
    files = [FileObject(name="test.pdf", content=b"content", mime_type="application/pdf", owner="test")]

    # Call method
    result = pdf_processor.get_total_pages_from_files(files)

    # Assertions
    assert "Total pages: 5" in result
    assert "test.pdf: 5 pages" in result
