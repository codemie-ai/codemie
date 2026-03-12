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

"""Tests for CSVTool class with multiple file support."""

import pytest

from codemie_tools.base.file_object import FileObject
from codemie_tools.file_analysis.csv.tools import CSVTool
from codemie_tools.file_analysis.models import FileAnalysisConfig


class TestCSVTool:
    """Test cases for CSVTool with focus on multiple file support."""

    @pytest.fixture
    def sample_csv_1(self):
        """Simple CSV data with header."""
        return "name,age,city\nAlice,30,New York\nBob,25,Boston\nCharlie,35,San Francisco"

    @pytest.fixture
    def sample_csv_2(self):
        """Another CSV data with header and different columns."""
        return "product,price,quantity\napple,1.99,100\nbanana,0.99,150\norange,2.49,75"

    def test_single_file_processing(self, sample_csv_1):
        """Test that a single file is processed correctly."""
        # Create file object
        file_obj = FileObject(name="people.csv", mime_type="text/csv", owner="user", content=sample_csv_1)

        # Create tool instance
        tool = CSVTool(config=FileAnalysisConfig(input_files=[file_obj]))

        # Test head method
        result = tool.execute(method_name="head")
        assert "Alice" in result
        assert "name" in result

        # Test with column
        result = tool.execute(method_name="mean", column="age")
        # The mean of [30, 25, 35] is 30
        assert "30.0" in result

    def test_multiple_file_processing(self, sample_csv_1, sample_csv_2):
        """Test that multiple files are processed correctly."""
        # Create file objects
        file_obj1 = FileObject(name="people.csv", mime_type="text/csv", owner="user", content=sample_csv_1)

        file_obj2 = FileObject(name="products.csv", mime_type="text/csv", owner="user", content=sample_csv_2)

        # Create tool instance with multiple files
        tool = CSVTool(config=FileAnalysisConfig(input_files=[file_obj1, file_obj2]))

        # Test head method on multiple files
        result = tool.execute(method_name="head")

        # Check that output contains data from both files
        assert "###SOURCE DOCUMENT###" in result
        assert "**Source:** people.csv" in result
        assert "**Source:** products.csv" in result
        assert "Alice" in result
        assert "apple" in result

        # Test with different method
        result = tool.execute(method_name="describe")
        # Check that output contains summary statistics for both files
        assert "###SOURCE DOCUMENT###" in result
        assert "**Source:** people.csv" in result
        assert "**Source:** products.csv" in result
        # Should contain statistical data
        assert "count" in result
        assert "mean" in result

    def test_error_handling(self, sample_csv_1, sample_csv_2):
        """Test error handling with invalid column or method."""
        # Create file objects
        file_obj1 = FileObject(name="people.csv", mime_type="text/csv", owner="user", content=sample_csv_1)

        file_obj2 = FileObject(name="products.csv", mime_type="text/csv", owner="user", content=sample_csv_2)

        # Create tool instance with multiple files
        tool = CSVTool(config=FileAnalysisConfig(input_files=[file_obj1, file_obj2]))

        # Test with invalid column
        result = tool.execute(method_name="mean", column="invalid_column")
        assert "Error: Column 'invalid_column' not found" in result

        # Test with invalid method
        result = tool.execute(method_name="invalid_method")
        assert "Error processing" in result
        assert "invalid_method" in result or "has no attribute" in result

    def test_empty_files_list(self):
        """Test that an error is raised when no files are provided."""
        tool = CSVTool(config=FileAnalysisConfig(input_files=[]))
        with pytest.raises(ValueError) as excinfo:
            tool.execute(method_name="head")
        assert "requires at least one file to process" in str(excinfo.value)
