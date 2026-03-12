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

"""Tests for DocxTool class."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from codemie_tools.base.file_object import FileObject
from codemie_tools.file_analysis.docx.models import (
    DocumentContent,
    DocumentStructure,
    FormattingInfo,
    ImageData,
    Position,
    QueryType,
)
from codemie_tools.file_analysis.docx.tools import DocxTool


@pytest.fixture
def mock_file_object():
    """Create mock FileObject for testing."""
    file_obj = Mock(spec=FileObject)
    file_obj.name = "test.docx"
    file_obj.mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    file_obj.content = b"fake_docx_content"
    return file_obj


@pytest.fixture
def mock_document_content():
    """Create mock DocumentContent for testing."""
    return DocumentContent(
        text="Sample text content with important information.",
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
        tables=[],
    )


@pytest.fixture
def docx_tool(mock_file_object):
    """Create DocxTool instance for testing."""
    from codemie_tools.file_analysis.models import FileAnalysisConfig

    config = FileAnalysisConfig(input_files=[mock_file_object])
    return DocxTool(config=config)


class TestPagesParameterHandling:
    """Tests for pages parameter handling in DocxTool."""

    def test_pages_none_processes_all(self, docx_tool):
        """Test that pages=None processes all pages."""
        with patch.object(docx_tool.docx_processor, "process_multiple_files") as mock_process:
            mock_process.return_value = "Sample text"

            docx_tool.execute(query=QueryType.TEXT, pages=None)

            # Verify pages parameter is passed as None
            mock_process.assert_called_once()
            call_kwargs = mock_process.call_args[1]
            assert call_kwargs["pages"] is None

    def test_pages_all_normalized_to_none(self, docx_tool):
        """Test that pages='all' is normalized to None."""
        with patch.object(docx_tool.docx_processor, "process_multiple_files") as mock_process:
            mock_process.return_value = "Sample text"

            docx_tool.execute(query=QueryType.TEXT, pages="all")

            # Verify pages='all' is normalized to None
            mock_process.assert_called_once()
            call_kwargs = mock_process.call_args[1]
            assert call_kwargs["pages"] is None

    def test_pages_all_case_insensitive(self, docx_tool):
        """Test that pages='ALL', 'All', 'aLl' are all normalized to None."""
        test_cases = ["all", "ALL", "All", "aLl", "  all  ", "  ALL  "]

        for pages_value in test_cases:
            with patch.object(docx_tool.docx_processor, "process_multiple_files") as mock_process:
                mock_process.return_value = "Sample text"

                docx_tool.execute(query=QueryType.TEXT, pages=pages_value)

                # Verify each variation is normalized to None
                mock_process.assert_called_once()
                call_kwargs = mock_process.call_args[1]
                assert call_kwargs["pages"] is None, f"Failed for pages='{pages_value}'"

    def test_pages_specific_number(self, docx_tool):
        """Test that specific page numbers are passed through."""
        with patch.object(docx_tool.docx_processor, "process_multiple_files") as mock_process:
            mock_process.return_value = "Sample text"

            docx_tool.execute(query=QueryType.TEXT, pages="1")

            # Verify specific page number is passed through
            mock_process.assert_called_once()
            call_kwargs = mock_process.call_args[1]
            assert call_kwargs["pages"] == "1"

    def test_pages_comma_separated(self, docx_tool):
        """Test that comma-separated pages are passed through."""
        with patch.object(docx_tool.docx_processor, "process_multiple_files") as mock_process:
            mock_process.return_value = "Sample text"

            docx_tool.execute(query=QueryType.TEXT, pages="1,3,5")

            # Verify comma-separated pages are passed through
            mock_process.assert_called_once()
            call_kwargs = mock_process.call_args[1]
            assert call_kwargs["pages"] == "1,3,5"

    def test_pages_range(self, docx_tool):
        """Test that page ranges are passed through."""
        with patch.object(docx_tool.docx_processor, "process_multiple_files") as mock_process:
            mock_process.return_value = "Sample text"

            docx_tool.execute(query=QueryType.TEXT, pages="1-4")

            # Verify page range is passed through
            mock_process.assert_called_once()
            call_kwargs = mock_process.call_args[1]
            assert call_kwargs["pages"] == "1-4"

    def test_pages_with_text_with_images_query(self, docx_tool, mock_document_content):
        """Test pages parameter with TEXT_WITH_IMAGES query."""
        with patch.object(docx_tool.docx_processor, "read_document_from_bytes") as mock_read:
            mock_read.return_value = mock_document_content

            docx_tool.execute(query=QueryType.TEXT_WITH_IMAGES, pages="1,2")

            # Verify pages parameter is passed
            mock_read.assert_called_once()
            # The pages parameter is the 4th positional argument (index 3)
            # call_args[0] = (content, file_name, query, pages)
            assert mock_read.call_args[0][3] == "1,2"


class TestToolEdgeCases:
    """Tests for edge cases and error handling in DocxTool."""

    def test_tool_requires_files(self):
        """Test that tool requires at least one file."""
        from codemie_tools.file_analysis.models import FileAnalysisConfig

        config = FileAnalysisConfig(input_files=[])
        tool = DocxTool(config=config)

        with pytest.raises(ValueError) as exc_info:
            tool.execute(query=QueryType.TEXT)

        assert "requires at least one file" in str(exc_info.value)

    def test_unknown_query_type_raises_error(self, docx_tool):
        """Test that unknown query type raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            docx_tool.execute(query="invalid_query_type")

        assert "Unknown query type" in str(exc_info.value)

    def test_execute_with_instructions(self, docx_tool, mock_document_content):
        """Test execute with custom instructions."""
        with (
            patch.object(docx_tool.docx_processor, "read_document_from_bytes") as mock_read,
            patch.object(docx_tool.docx_processor, "analyze_content") as mock_analyze,
        ):
            mock_read.return_value = mock_document_content
            mock_analysis = MagicMock()
            mock_analysis.summary = "Custom analysis"
            mock_analysis.key_topics = []
            mock_analysis.sentiment = "neutral"
            mock_analysis.language = "en"
            mock_analysis.readability_score = 7.0
            mock_analyze.return_value = mock_analysis

            instructions = "Focus on financial data"
            docx_tool.execute(query=QueryType.ANALYZE, instructions=instructions)

            # Verify instructions are passed to analyze_content
            mock_analyze.assert_called_once()
            call_kwargs = mock_analyze.call_args[1]
            assert call_kwargs["instructions"] == instructions

    def test_table_extraction_query(self, docx_tool, mock_document_content):
        """Test TABLE_EXTRACTION query."""
        with patch.object(docx_tool.docx_processor, "process_multiple_files") as mock_process:
            mock_process.return_value = [{"rows": [["A", "B"], ["C", "D"]]}]

            result = docx_tool.execute(query=QueryType.TABLE_EXTRACTION)

            # Verify tables are extracted
            assert isinstance(result, list)
            assert len(result) == 1
            assert "rows" in result[0]

    def test_structure_only_query(self, docx_tool, mock_document_content):
        """Test STRUCTURE_ONLY query."""
        with patch.object(docx_tool.docx_processor, "read_document_from_bytes") as mock_read:
            mock_read.return_value = mock_document_content

            result = docx_tool.execute(query=QueryType.STRUCTURE_ONLY)

            # Verify structure is extracted
            assert isinstance(result, list)
            assert len(result) == 1
            assert "structure" in result[0]
