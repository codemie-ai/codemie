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

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from codemie_tools.base.file_object import FileObject
from codemie_tools.file_analysis.models import FileAnalysisConfig
from codemie_tools.file_analysis.pdf.tools import PDFTool, PDFToolInput, QueryType, PdfProcessor


# Test for PDFToolInput
def test_pdf_tool_input_validation():
    # Valid input
    query = "Text"
    valid_input = {"pages": [1, 2, 3], "query": query}
    input_data = PDFToolInput(**valid_input)
    assert input_data.pages == [1, 2, 3]
    assert input_data.query == query

    # Invalid input (missing required fields)
    with pytest.raises(ValidationError):
        PDFToolInput(pages=[1, 2, 3])


# Test for PDFTool
@patch.object(PdfProcessor, '__init__')
def test_pdf_tool_initialization(mock_processor_init):
    # Set up
    mock_processor_init.return_value = None
    empty_pdf_bytes = bytearray(
        b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >> endobj\nxref\n0 4\n0000000000 65535 f\n0000000015 00000 n\n0000000060 00000 n\n0000000114 00000 n\ntrailer << /Size 4 /Root 1 0 R >>\nstartxref\n178\n%%EOF"
    )
    file_obj = FileObject(name="test.pdf", content=empty_pdf_bytes, mime_type="application/pdf", owner="test")

    # Test
    pdf_tool = PDFTool(config=FileAnalysisConfig(input_files=[file_obj]))

    # Assertions
    assert pdf_tool.pdf_processor is not None
    assert pdf_tool.config.input_files == [file_obj]
    mock_processor_init.assert_called_once()


@patch.object(PdfProcessor, 'extract_text_as_markdown_from_files')
def test_pdf_tool_execute_text(mock_extract, sample_pdf_path):
    # Set up
    expected_result = "Extracted text"
    mock_extract.return_value = expected_result

    with open(sample_pdf_path, "rb") as f:
        pdf_bytes = f.read()
    file_obj = FileObject(name="sample.pdf", content=pdf_bytes, mime_type="application/pdf", owner="test")
    pdf_tool = PDFTool(config=FileAnalysisConfig(input_files=[file_obj]))

    # Test
    result = pdf_tool.execute(pages=[1], query=QueryType.TEXT)

    # Assertions
    assert result == expected_result
    mock_extract.assert_called_once_with(files=[file_obj], pages=[1], page_chunks=False)


@patch.object(PdfProcessor, 'extract_text_as_markdown_from_files')
def test_pdf_tool_execute_text_with_metadata(mock_extract, sample_pdf_path):
    # Set up
    expected_result = "Extracted text with metadata"
    mock_extract.return_value = expected_result

    with open(sample_pdf_path, "rb") as f:
        pdf_bytes = f.read()
    file_obj = FileObject(name="sample.pdf", content=pdf_bytes, mime_type="application/pdf", owner="test")
    pdf_tool = PDFTool(config=FileAnalysisConfig(input_files=[file_obj]))

    # Test
    result = pdf_tool.execute(pages=[1], query=QueryType.TEXT_WITH_METADATA)

    # Assertions
    assert result == expected_result
    mock_extract.assert_called_once_with(files=[file_obj], pages=[1], page_chunks=True)


@patch.object(PdfProcessor, 'get_total_pages_from_files')
def test_pdf_tool_execute_pages(mock_get_pages, sample_pdf_path):
    # Set up
    expected_result = "Total pages: 10\nfile1.pdf: 5 pages\nfile2.pdf: 5 pages"
    mock_get_pages.return_value = expected_result

    with open(sample_pdf_path, "rb") as f:
        pdf_bytes = f.read()
    file_obj = FileObject(name="sample.pdf", content=pdf_bytes, mime_type="application/pdf", owner="test")
    pdf_tool = PDFTool(config=FileAnalysisConfig(input_files=[file_obj]))

    # Test
    result = pdf_tool.execute(pages=[], query=QueryType.TOTAL_PAGES)

    # Assertions
    assert result == expected_result
    mock_get_pages.assert_called_once_with([file_obj])


@patch.object(PdfProcessor, 'process_pdf_files')
def test_pdf_tool_execute_text_with_ocr(mock_process, sample_pdf_path):
    # Set up
    expected_result = "Extracted text with OCR"
    mock_process.return_value = expected_result

    with open(sample_pdf_path, "rb") as f:
        pdf_bytes = f.read()
    file_obj = FileObject(name="sample.pdf", content=pdf_bytes, mime_type="application/pdf", owner="test")
    pdf_tool = PDFTool(config=FileAnalysisConfig(input_files=[file_obj]))

    # Test
    result = pdf_tool.execute(pages=[1], query=QueryType.TEXT_WITH_OCR)

    # Assertions
    assert result == expected_result
    mock_process.assert_called_once_with([file_obj], [1])
