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

"""Unit tests for LocalCodeExecutorTool."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from codemie_tools.base.file_object import FileObject
from codemie_tools.data_management.code_executor.local_code_executor_tool import (
    LocalCodeExecutorTool,
    warn_once,
)
from codemie_tools.data_management.code_executor.local_execution_engine import LocalExecutionEngine
from codemie_tools.data_management.code_executor.models import CodeExecutorConfig, ExecutionMode
from langchain_core.tools import ToolException


class TestLocalCodeExecutorTool(unittest.TestCase):
    """Test suite for LocalCodeExecutorTool."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_file_repo = MagicMock()
        self.tool = LocalCodeExecutorTool(file_repository=self.mock_file_repo, user_id="test_user")

    def test_init_forces_local_mode(self):
        """Test that LocalCodeExecutorTool forces LOCAL execution mode."""
        tool = LocalCodeExecutorTool(file_repository=self.mock_file_repo, user_id="test_user")

        assert tool.config.execution_mode == ExecutionMode.LOCAL
        assert tool.name == "python_repl_code_interpreter"

    def test_init_with_file_repository(self):
        """Test initialization with file repository."""
        tool = LocalCodeExecutorTool(file_repository=self.mock_file_repo, user_id="test_user")

        assert tool.file_repository is self.mock_file_repo
        assert tool.user_id == "test_user"

    def test_init_without_file_repository(self):
        """Test initialization without file repository."""
        tool = LocalCodeExecutorTool(user_id="test_user")

        assert tool.file_repository is None
        assert tool.user_id == "test_user"

    def test_init_default_user_id(self):
        """Test initialization with default user ID."""
        tool = LocalCodeExecutorTool()

        # Default user_id is "test" according to the signature
        assert tool.user_id == "test"

    def test_args_schema_is_python_run_code_input(self):
        """Test that args_schema is PythonRunCodeInput for local mode."""
        # The args_schema should be dynamically created by parent class
        assert self.tool.args_schema is not None
        assert hasattr(self.tool.args_schema, "model_fields")
        # Check that it has "code" field
        assert "code" in self.tool.args_schema.model_fields

    @patch('codemie_tools.data_management.code_executor.local_execution_engine.subprocess.run')
    def test_execute_calls_parent_execute_local(self, mock_subprocess_run):
        """Test that execute delegates to parent's _execute_local."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Execution result"
        mock_subprocess_run.return_value = mock_result

        result = self.tool.execute(code="print('hello')")

        mock_subprocess_run.assert_called_once()
        assert result == "Execution result"

    @patch('codemie_tools.data_management.code_executor.local_execution_engine.subprocess.run')
    def test_execute_with_timeout_error(self, mock_subprocess_run):
        """Test execute handles timeout error."""
        mock_subprocess_run.side_effect = subprocess.TimeoutExpired(cmd=["python"], timeout=30)

        with pytest.raises(ToolException) as exc_info:
            self.tool.execute(code="while True: pass")

        assert "timed out" in str(exc_info.value)

    @patch('codemie_tools.data_management.code_executor.local_execution_engine.subprocess.run')
    def test_execute_with_runtime_error(self, mock_subprocess_run):
        """Test execute handles runtime errors."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "NameError: name 'foo' is not defined"
        mock_subprocess_run.return_value = mock_result

        with pytest.raises(ToolException) as exc_info:
            self.tool.execute(code="print(foo)")

        assert "Code execution failed" in str(exc_info.value)

    def test_warn_once_function(self):
        """Test that warn_once uses lru_cache."""
        # The function is cached, so we can test that it's callable
        assert callable(warn_once)

        # Call it twice
        warn_once()
        warn_once()

        # Since it's cached, it should only log once
        # (This is a basic test; full logging test would need caplog)


class TestLocalCodeExecutorToolFileHandling(unittest.TestCase):
    """Test suite for file handling in LocalCodeExecutorTool."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_file_repo = MagicMock()
        self.tool = LocalCodeExecutorTool(file_repository=self.mock_file_repo, user_id="test_user")

    @patch('codemie_tools.data_management.code_executor.local_execution_engine.subprocess.run')
    def test_execute_with_image_output_and_file_repo(self, mock_subprocess_run):
        """Test execute with image output and file repository."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_subprocess_run.return_value = mock_result

        self.mock_file_repo.write_file.return_value.to_encoded_url.return_value = "test-url"

        # Create real temp dir first, then mock TemporaryDirectory to return it
        with tempfile.TemporaryDirectory() as img_dir:
            fake_png = Path(img_dir) / "image_0000.png"
            fake_png.write_bytes(b"\x89PNG\r\n\x1a\n")

            mock_tmpdir_instance = MagicMock()
            mock_tmpdir_instance.__enter__ = MagicMock(return_value=img_dir)
            mock_tmpdir_instance.__exit__ = MagicMock(return_value=False)

            with patch(
                'codemie_tools.data_management.code_executor.local_execution_engine.tempfile.TemporaryDirectory',
                return_value=mock_tmpdir_instance,
            ):
                result = self.tool.execute(
                    code="import matplotlib.pyplot as plt; plt.show()", export_files=[str(fake_png)]
                )

            assert "Exported files" in result
            assert "sandbox:/v1/files/" in result


class TestLocalCodeExecutorToolCodeExecution(unittest.TestCase):
    """Test suite for code execution in LocalCodeExecutorTool."""

    def setUp(self):
        """Set up test fixtures."""
        self.tool = LocalCodeExecutorTool(user_id="test_user")

    def test_execute_with_text_output(self):
        """Test execute with text output."""
        result = self.tool.execute(code="print('Hello, World!')")

        assert result == "Hello, World!"

    def test_execute_with_no_output(self):
        """Test execute with no output."""
        result = self.tool.execute(code="x = 1 + 1")

        assert "successfully" in result

    def test_execute_with_error_output(self):
        """Test execute with error output."""
        with pytest.raises(ToolException) as exc_info:
            self.tool.execute(code="print(x)")

        assert "Code execution failed" in str(exc_info.value)

    def test_execute_with_multiline_code(self):
        """Test execute with multiline code."""
        code = """
x = 5
y = 5
print(x + y)
"""
        result = self.tool.execute(code=code)

        assert result == "10"


class TestLocalCodeExecutorToolInvoke(unittest.TestCase):
    """Test suite for invoke method of LocalCodeExecutorTool."""

    def setUp(self):
        """Set up test fixtures."""
        self.tool = LocalCodeExecutorTool(user_id="test_user")

    def test_invoke_with_dict_input(self):
        """Test invoke with dictionary input."""
        result = self.tool.invoke({"code": "print(42)"})

        assert result == "42"

    def test_invoke_with_string_input(self):
        """Test invoke with string input."""
        result = self.tool.invoke("print(42)")

        assert result == "42"


class TestLocalExecutionEngineInputFiles(unittest.TestCase):
    """Test suite for input_files support in LocalExecutionEngine."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = CodeExecutorConfig()
        self.engine = LocalExecutionEngine(config=self.config, file_repository=None, user_id="test_user")

    def _make_file_object(self, name: str, content: bytes) -> FileObject:
        file_obj = MagicMock(spec=FileObject)
        file_obj.name = name
        file_obj.bytes_content.return_value = content
        return file_obj

    def test_execute_with_input_file_makes_it_accessible_in_subprocess(self):
        """Input files written to work_dir are readable by subprocess code."""
        csv_content = b"col1,col2\n1,2\n3,4"
        file_obj = self._make_file_object("data.csv", csv_content)

        result = self.engine.execute(
            code="f = open('data.csv'); print(f.read().strip()); f.close()",
            export_files=None,
            input_files=[file_obj],
        )

        assert "col1,col2" in result
        assert "1,2" in result

    def test_execute_without_input_files_runs_normally(self):
        """execute() with no input_files behaves as before."""
        result = self.engine.execute(code="print('ok')", export_files=None)
        assert result == "ok"

    def test_execute_skips_file_with_none_content_and_logs_warning(self):
        """Files with bytes_content() == None are skipped; execution still proceeds."""
        file_obj = self._make_file_object("missing.csv", None)
        file_obj.bytes_content.return_value = None

        with self.assertLogs(
            "codemie_tools.data_management.code_executor.local_execution_engine", level="WARNING"
        ) as log:
            result = self.engine.execute(code="print('ran')", export_files=None, input_files=[file_obj])

        assert result == "ran"
        assert any("missing.csv" in msg for msg in log.output)

    def test_execute_cwd_is_work_dir_not_caller_dir(self):
        """Subprocess cwd is the temp work_dir, so relative file paths resolve correctly."""
        file_obj = self._make_file_object("hello.txt", b"world")

        # If cwd were not set, open('hello.txt') would fail
        result = self.engine.execute(
            code="print(open('hello.txt').read())",
            export_files=None,
            input_files=[file_obj],
        )

        assert result == "world"

    def test_execute_sanitizes_path_traversal_name(self):
        """Path-traversal names are sanitized by Path.name: only the filename is kept.

        ``Path("../../../tmp/evil.txt").name`` returns ``"evil.txt"``, so the
        directory components are stripped silently and the file is written
        safely inside work_dir.  No warning is emitted; the code still runs.
        """
        evil_file = self._make_file_object("../../../tmp/evil.txt", b"pwned")

        # File should be accessible as plain "evil.txt" after sanitisation
        result = self.engine.execute(
            code="print(open('evil.txt').read())",
            export_files=None,
            input_files=[evil_file],
        )

        assert result == "pwned"

    def test_execute_png_glob_does_not_match_uploaded_png(self):
        """Uploaded .png input files are not mistaken for matplotlib output."""
        png_file = self._make_file_object("chart.png", b"\x89PNG\r\n\x1a\n")

        # Code produces no matplotlib output; result should be text, not "Image generated"
        result = self.engine.execute(
            code="print('done')",
            export_files=None,
            input_files=[png_file],
        )

        assert result == "done"
        assert "Image generated" not in result


class TestLocalCodeExecutorToolBackwardCompatibility(unittest.TestCase):
    """Test suite for backward compatibility with PythonREPL."""

    def test_tool_name_matches_repl(self):
        """Test that tool name matches original PythonREPL."""
        tool = LocalCodeExecutorTool(user_id="test_user")

        assert tool.name == "python_repl_code_interpreter"

    def test_execution_mode_override_ignored(self):
        """Test that execution_mode parameter is overridden to LOCAL."""
        # Even if parent class is initialized with SANDBOX mode from env,
        # LocalCodeExecutorTool should force LOCAL mode
        tool = LocalCodeExecutorTool(user_id="test_user")

        assert tool.config.execution_mode == ExecutionMode.LOCAL
