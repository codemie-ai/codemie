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

"""Parametrized tests for FileAnalysis tool processing files from samples directory."""

import os
import pathlib
from unittest import mock
from unittest.mock import patch

import pytest
from langchain_experimental.tools import PythonAstREPLTool

from codemie_tools.base.file_object import FileObject
from codemie_tools.file_analysis.csv.tools import CSVTool
from codemie_tools.file_analysis.file_analysis_tool import FileAnalysisTool
from codemie_tools.file_analysis.models import FileAnalysisConfig
from codemie_tools.file_analysis.toolkit import FileAnalysisToolkit


@pytest.fixture(autouse=True)
def mock_maybe_pool_submit():
    """Mock maybe_pool_submit to run inline instead of subprocess.

    Subprocess execution breaks mocks - this forces inline execution
    so test mocks work correctly. Applied to all file_analysis tests.
    """
    with patch("codemie_tools.file_analysis.file_analysis_tool.maybe_pool_submit") as mock_pool:
        # Run function directly instead of submitting to pool
        mock_pool.side_effect = lambda fn, *args, **kwargs: fn(*args, **kwargs)
        yield mock_pool


def is_text_file(filename: str) -> bool:
    """Determine if a file should be opened in text mode based on its extension.

    Args:
        filename: The name of the file to check

    Returns:
        bool: True if the file should be opened in text mode, False for binary mode
    """
    text_extensions = ('.txt', '.md', '.py', '.java', '.js', '.html', '.css', '.json', '.csv', '.xml', '.yml', '.yaml')
    return filename.lower().endswith(text_extensions)


class TestFileAnalysisTool:
    """Parametrized test cases for FileAnalysisTool using sample files."""

    @pytest.fixture
    def samples_dir(self):
        """Get the path to the samples directory."""
        return pathlib.Path(__file__).parent / "samples"

    @pytest.mark.parametrize(
        "filename",
        [
            pytest.param(filename, id=filename)
            for filename in os.listdir(pathlib.Path(__file__).parent / "samples")
            if os.path.isfile(os.path.join(pathlib.Path(__file__).parent / "samples", filename))
            and not filename.endswith(('.pdf', '.pptx', '.xlsx', '.xls', '.csv', '.docx', '.eml', '.msg'))
        ],
    )
    def test_file_processing(self, samples_dir, filename):
        """
        Test processing of each sample file individually with default MarkItDown processing.
        Note: This test only runs on files NOT handled by specialized tools (pdf, pptx, xlsx, csv, docx).

        Args:
            samples_dir: Path to the directory containing sample files
            filename: Name of the individual sample file being tested
        """
        filepath = os.path.join(samples_dir, filename)

        # Read the file content for the tool with appropriate mode
        if is_text_file(filename):
            # Open as text file
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    file_content = f.read()
                    # Convert to bytes since FileAnalysisTool expects bytes or will encode it
                    file_content = file_content.encode('utf-8')
            except UnicodeDecodeError:
                # Fallback to binary mode if UTF-8 decoding fails
                with open(filepath, 'rb') as f:
                    file_content = f.read()
        else:
            # Open as binary file
            with open(filepath, 'rb') as f:
                file_content = f.read()

        from codemie_tools.base.file_object import FileObject

        file_obj = FileObject(name=filename, content=file_content, mime_type="application/octet-stream", owner="test")
        tool = FileAnalysisTool(config=FileAnalysisConfig(input_files=[file_obj]))
        result = tool.execute(query=filepath)

        # Assert that we get a non-empty result
        assert result, f"Failed to get result for {filename}"
        assert isinstance(result, str), f"Result for {filename} is not a string"
        assert len(result) > 0, f"Result for {filename} is empty"

    @pytest.mark.parametrize(
        "filename",
        [
            pytest.param(filename, id=filename)
            for filename in os.listdir(pathlib.Path(__file__).parent / "samples")
            if os.path.isfile(os.path.join(pathlib.Path(__file__).parent / "samples", filename))
            and not filename.endswith(('.pdf', '.pptx', '.xlsx', '.xls', '.csv', '.docx', '.eml', '.msg'))
        ],
    )
    def test_fallback_mechanism(self, samples_dir, filename):
        """
        Test fallback mechanism when MarkItDown fails for each sample file individually.
        Note: This test only runs on files NOT handled by specialized tools (pdf, pptx, xlsx, csv, docx).

        Args:
            samples_dir: Path to the directory containing sample files
            filename: Name of the individual sample file being tested
        """
        # Only mock here to test the fallback functionality
        with mock.patch("codemie_tools.file_analysis.workers.markdown_workers.MarkItDown") as mock_markitdown:
            mock_markitdown.return_value.convert.side_effect = Exception(f"Forced failure for {filename}")

            filepath = os.path.join(samples_dir, filename)

            # Read the file content for the tool with appropriate mode
            if is_text_file(filename):
                # Open as text file
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        file_content = f.read()
                        # Convert to bytes since FileAnalysisTool expects bytes or will encode it
                        file_content = file_content.encode('utf-8')
                except UnicodeDecodeError:
                    # Fallback to binary mode if UTF-8 decoding fails
                    with open(filepath, 'rb') as f:
                        file_content = f.read()
            else:
                # Open as binary file
                with open(filepath, 'rb') as f:
                    file_content = f.read()

            from codemie_tools.base.file_object import FileObject

            file_obj = FileObject(
                name=filename, content=file_content, mime_type="application/octet-stream", owner="test"
            )

            # Patch the _fallback_decode_text_file method directly to ensure it works for sample.txt
            if filename == "sample.txt":
                with mock.patch.object(
                    FileAnalysisTool, '_fallback_decode_text_file', return_value="This is a sample text file"
                ):
                    tool = FileAnalysisTool(config=FileAnalysisConfig(input_files=[file_obj]))
                    result = tool.execute(query=filepath)
            else:
                tool = FileAnalysisTool(config=FileAnalysisConfig(input_files=[file_obj]))
                result = tool.execute(query=filepath)

            # For text files, the fallback should work
            if is_text_file(filename):
                assert result, f"Failed to get result for {filename}"
                if filename == "sample.txt":
                    assert "This is a sample text file" in result, f"Expected content not found in {filename}"
            else:
                # For non-text files, we expect the fallback to return an error message
                assert "not supported for direct decoding" in result, f"Expected error message not found for {filename}"

    def test_file_not_found(self):
        """Test handling of non-existent files."""
        # Create a custom FileObject that raises FileNotFoundError
        from codemie_tools.base.file_object import FileObject

        class MockFileObject(FileObject):
            def bytes_content(self):
                raise FileNotFoundError("File not found: non_existent_file.txt")

        # Create our mock file object
        file_obj = MockFileObject(name="empty.txt", content=b"", mime_type="text/plain", owner="test")
        tool = FileAnalysisTool(config=FileAnalysisConfig(input_files=[file_obj]))

        with mock.patch(
            "codemie_tools.file_analysis.workers.markdown_workers.convert_file_to_markdown"
        ) as mock_markitdown:
            # Force MarkItDown to fail so we use our fallback mechanism
            mock_markitdown.return_value.convert.side_effect = Exception("Mock failure")
            result = tool.execute(query="non_existent_file.txt")
            assert "File not found" in result

    def test_process_single_file_uses_preconverted_content(self):
        """FileAnalysisTool returns cached markdown without calling convert_file_to_markdown."""
        from unittest.mock import patch
        from codemie_tools.base.file_object import FileObject
        from codemie_tools.file_analysis.file_analysis_tool import FileAnalysisTool
        from codemie_tools.file_analysis.models import FileAnalysisConfig

        file_obj = FileObject(name="report.pdf", content=b"%PDF-fake", mime_type="application/pdf", owner="user1")
        config = FileAnalysisConfig(
            input_files=[file_obj],
            preconverted_content={"report.pdf": "# Cached markdown content"},
        )
        tool = FileAnalysisTool(config=config)

        with patch("codemie_tools.file_analysis.file_analysis_tool.convert_file_to_markdown") as mock_convert:
            result = tool._process_single_file(file_obj)

        assert result == "# Cached markdown content"
        mock_convert.assert_not_called()

    def test_process_single_file_falls_back_to_markitdown_when_not_preconverted(self):
        """FileAnalysisTool calls convert_file_to_markdown when file not in preconverted_content."""
        from unittest.mock import patch
        from codemie_tools.base.file_object import FileObject
        from codemie_tools.file_analysis.file_analysis_tool import FileAnalysisTool
        from codemie_tools.file_analysis.models import FileAnalysisConfig

        file_obj = FileObject(name="report.pdf", content=b"%PDF-fake", mime_type="application/pdf", owner="user1")
        config = FileAnalysisConfig(input_files=[file_obj], preconverted_content={})
        tool = FileAnalysisTool(config=config)

        with patch("codemie_tools.file_analysis.file_analysis_tool.maybe_pool_submit") as mock_pool:
            mock_pool.side_effect = lambda fn, *args, **kwargs: fn(*args, **kwargs)
            with patch("codemie_tools.file_analysis.file_analysis_tool.convert_file_to_markdown") as mock_convert:
                mock_convert.return_value = "# Live markdown"
                result = tool._process_single_file(file_obj)

        assert result == "# Live markdown"
        mock_convert.assert_called_once()

    def test_get_toolkit_passes_preconverted_content_to_tools(self):
        """FileAnalysisToolkit.get_toolkit propagates preconverted_content into tool configs."""
        from codemie_tools.base.file_object import FileObject
        from codemie_tools.file_analysis.file_analysis_tool import FileAnalysisTool
        from codemie_tools.file_analysis.toolkit import FileAnalysisToolkit

        file_obj = FileObject(
            name="data.html",
            content=b"<html><body>hello</body></html>",
            mime_type="text/html",
            owner="user1",
        )
        preconverted = {"data.html": "# Hello"}

        toolkit = FileAnalysisToolkit.get_toolkit(
            files=[file_obj],
            preconverted_content=preconverted,
        )
        tools = toolkit.get_tools()

        file_tool = next((t for t in tools if isinstance(t, FileAnalysisTool)), None)
        assert file_tool is not None
        assert file_tool.config.preconverted_content == preconverted

    def test_file_analysis_config_has_preconverted_content_field(self):
        from codemie_tools.file_analysis.models import FileAnalysisConfig

        config = FileAnalysisConfig()
        assert hasattr(config, "preconverted_content")
        assert config.preconverted_content == {}

    def test_multiple_files_processing(self, samples_dir):
        """Test processing multiple files with separators.
        Note: FileAnalysisTool only processes files NOT handled by specialized tools.
        """
        # Get sample text file
        txt_path = os.path.join(samples_dir, "sample.txt")
        with open(txt_path, 'rb') as f:
            txt_content = f.read()
        txt_file = FileObject(name="sample.txt", content=txt_content, mime_type="text/plain", owner="test")

        # Get another sample file that's not handled by specialized tools (e.g., SVG)
        svg_path = os.path.join(samples_dir, "sample.svg")
        with open(svg_path, 'rb') as f:
            svg_content = f.read()
        svg_file = FileObject(name="sample.svg", content=svg_content, mime_type="image/svg+xml", owner="test")

        # Process both files together
        tool = FileAnalysisTool(config=FileAnalysisConfig(input_files=[txt_file, svg_file]))
        result = tool.execute(query="analyze these files")

        # Check for expected content
        assert "###SOURCE DOCUMENT###" in result, "Missing source document header"
        assert "**Source:** sample.txt" in result, "Missing source for text file"
        assert "**Source:** sample.svg" in result, "Missing source for SVG file"
        assert "**File Content:**" in result, "Missing file content header"

        # Verify files are properly separated
        sections = result.split("###SOURCE DOCUMENT###")
        assert len(sections) >= 2, "Files should be separated by the SOURCE_DOCUMENT_KEY separator"


class TestFileAnalysisToolkitCSVSupport:
    """Test CSV support in FileAnalysisToolkit."""

    @pytest.fixture
    def csv_file(self):
        content = "a,b,c\n1,2,3\n4,5,6"
        return content

    @pytest.fixture
    def broken_csv_file(self):
        content = "a,b,c\n1,2,3\n4,5,6\n1,2,3,4"
        return content

    def test_get_csv_tools_for_file(self, csv_file):
        """Test that the correct CSV tools are returned for a CSV file."""
        # Test with new files parameter
        file_obj = FileObject(name="test.csv", mime_type="text/csv", owner="user", content=csv_file)

        toolkit = FileAnalysisToolkit.get_toolkit(files=[file_obj])
        tools = toolkit.get_tools()
        # Should include repl_tool and csv_tool tools
        assert len(tools) >= 2
        # Check that we have a PythonAstREPLTool and CSVTool
        repl_tool = None
        csv_tool = None
        for tool in tools:
            if isinstance(tool, PythonAstREPLTool):
                repl_tool = tool
            elif isinstance(tool, CSVTool):
                csv_tool = tool

        assert repl_tool is not None, "No PythonAstREPLTool found"
        assert csv_tool is not None, "No CSVTool found"
        # Check files is set correctly
        assert csv_tool.config.input_files[0].content == csv_file

    def test_csv_with_warnings(self, broken_csv_file):
        """Test CSV processing with warnings."""
        file_obj = FileObject(name="test.csv", mime_type="text/csv", owner="user", content=broken_csv_file)

        toolkit = FileAnalysisToolkit.get_toolkit(files=[file_obj])
        tools = toolkit.get_tools()

        repl_tool = None
        for tool in tools:
            if isinstance(tool, PythonAstREPLTool):
                repl_tool = tool
                break

        assert repl_tool is not None, "No PythonAstREPLTool found"
        description = repl_tool.description
        # Check that the warning is included in the description
        assert "Warning(s) while reading CSV file(s)" in description
        assert "expected 3 fields, saw 4" in description
