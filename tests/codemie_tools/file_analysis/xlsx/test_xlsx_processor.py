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

import io
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from codemie_tools.file_analysis.xlsx.processor import XlsxProcessor


@pytest.fixture
def mock_excel_bytes():
    """Create mock Excel file bytes for testing"""
    return b"mock excel content"


@pytest.fixture
def mock_excel_file():
    """Create a mock file-like object for testing"""
    file_obj = io.BytesIO(b"mock excel content")
    return file_obj


@pytest.fixture
def processor():
    """Create an XlsxProcessor instance for testing"""
    return XlsxProcessor()


@patch('pandas.read_excel')
def test_processor_load_with_bytes(mock_read_excel, processor, mock_excel_bytes):
    """Test loading Excel file from bytes using the processor"""
    # Setup mock return value
    df1 = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
    df2 = pd.DataFrame({"X": [5, 6], "Y": [7, 8]})
    mock_read_excel.return_value = {"Sheet1": df1, "Sheet2": df2}

    # Call the function
    result = processor.load(mock_excel_bytes)

    # Verify the result
    assert "Sheet1" in result
    assert "Sheet2" in result
    assert isinstance(result["Sheet1"], pd.DataFrame)
    assert isinstance(result["Sheet2"], pd.DataFrame)

    # Verify pandas.read_excel was called correctly
    mock_read_excel.assert_called_once()


@patch('pandas.read_excel')
def test_processor_load_with_file_object(mock_read_excel, processor, mock_excel_file):
    """Test loading Excel file from file-like object using the processor"""
    # Setup mock return value
    df1 = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
    mock_read_excel.return_value = {"Sheet1": df1}

    # Call the function
    result = processor.load(mock_excel_file)

    # Verify the result
    assert "Sheet1" in result
    assert isinstance(result["Sheet1"], pd.DataFrame)

    # Verify pandas.read_excel was called correctly
    mock_read_excel.assert_called_once()
    # Verify file position was reset
    assert mock_excel_file.tell() == 0


@patch('openpyxl.load_workbook')
@patch('pandas.read_excel')
def test_processor_load_with_visible_only(mock_read_excel, mock_load_workbook, mock_excel_bytes):
    """Test loading Excel file with visible_only=True using the processor"""
    # Create mock workbook with visible and hidden sheets
    mock_wb = MagicMock()
    mock_sheet1 = MagicMock()
    mock_sheet1.title = "VisibleSheet1"
    mock_sheet1.sheet_state = 'visible'

    mock_sheet2 = MagicMock()
    mock_sheet2.title = "HiddenSheet"
    mock_sheet2.sheet_state = 'hidden'

    mock_sheet3 = MagicMock()
    mock_sheet3.title = "VisibleSheet2"
    mock_sheet3.sheet_state = 'visible'

    mock_wb.worksheets = [mock_sheet1, mock_sheet2, mock_sheet3]
    mock_wb.sheetnames = ["VisibleSheet1", "HiddenSheet", "VisibleSheet2"]
    mock_load_workbook.return_value = mock_wb

    # Setup pandas read_excel mock
    visible_df1 = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
    visible_df2 = pd.DataFrame({"X": [5, 6], "Y": [7, 8]})
    mock_read_excel.return_value = {"VisibleSheet1": visible_df1, "VisibleSheet2": visible_df2}
    processor = XlsxProcessor(visible_only=True)

    # Call the function
    result = processor.load(mock_excel_bytes)

    # Verify only visible sheets are included
    assert len(result) == 2
    assert "VisibleSheet1" in result
    assert "VisibleSheet2" in result
    assert "HiddenSheet" not in result

    # Verify openpyxl was called to check visibility (we don't check the count anymore)
    assert mock_load_workbook.called

    # Verify pandas read_excel was called with the list of visible sheets
    mock_read_excel.assert_called_once()
    args, kwargs = mock_read_excel.call_args
    assert kwargs['sheet_name'] == ['VisibleSheet1', 'VisibleSheet2']


@patch('pandas.read_excel')
def test_processor_load_with_sheet_names(mock_read_excel, mock_excel_bytes):
    """Test loading Excel file with specific sheet_names using the processor"""
    # Setup mock return value
    df1 = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
    df2 = pd.DataFrame({"X": [5, 6], "Y": [7, 8]})
    mock_read_excel.return_value = {"Sheet1": df1, "Sheet2": df2}

    # Create processor with specific sheet_names
    processor = XlsxProcessor(sheet_names=["Sheet1"])

    # Call the function
    processor.load(mock_excel_bytes)

    # Verify pandas.read_excel was called with the correct sheet_names
    mock_read_excel.assert_called_once()
    args, kwargs = mock_read_excel.call_args
    assert kwargs['sheet_name'] == ["Sheet1"]


def test_processor_convert():
    """Test converting DataFrames to markdown"""
    # Create test DataFrames
    df1 = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
    df2 = pd.DataFrame({"X": [5, 6], "Y": [7, 8]})
    sheets = {"Sheet1": df1, "Sheet2": df2}

    # Create processor
    processor = XlsxProcessor()

    # Call the convert method
    result = processor.convert(sheets)

    # Verify the result contains markdown for both sheets
    assert "## Sheet1" in result
    assert "## Sheet2" in result
    assert "|" in result  # Markdown table separator

    # Basic check for table content
    assert "A" in result
    assert "B" in result
    assert "X" in result
    assert "Y" in result


@patch('pandas.read_excel')
def test_processor_load_with_filter_contains(mock_read_excel, mock_excel_bytes):
    """Test loading Excel file with filter_value using contains mode"""
    # Setup mock return value - simulating a project tracking sheet
    df = pd.DataFrame(
        {
            "Project": ["NATI-5DME", "NATI-5DC", "NATI-4OXO", "NATI-5DME", "OTHER-123"],
            "Employee": ["John Doe", "Jane Smith", "Bob Wilson", "Alice Brown", "Charlie Davis"],
            "Hours": [40, 35, 20, 30, 25],
        }
    )
    mock_read_excel.return_value = {"Sheet1": df}

    # Create processor with filter
    processor = XlsxProcessor(filter_values=["NATI-5DME"], filter_mode="contains")

    # Call the function
    result = processor.load(mock_excel_bytes)

    # Verify the result contains only filtered rows
    assert "Sheet1" in result
    filtered_df = result["Sheet1"]
    assert len(filtered_df) == 2  # Should have 2 rows with "NATI-5DME"
    assert all("NATI-5DME" in str(row).upper() for _, row in filtered_df.iterrows())


@patch('pandas.read_excel')
def test_processor_load_with_filter_exact(mock_read_excel, mock_excel_bytes):
    """Test loading Excel file with filter_value using exact mode"""
    # Setup mock return value
    df = pd.DataFrame(
        {
            "Status": ["Complete", "Completed", "In Progress", "Complete", "Pending"],
            "Task": ["Task 1", "Task 2", "Task 3", "Task 4", "Task 5"],
            "Priority": ["High", "Low", "Medium", "High", "Low"],
        }
    )
    mock_read_excel.return_value = {"Sheet1": df}

    # Create processor with exact filter
    processor = XlsxProcessor(filter_values=["Complete"], filter_mode="exact")

    # Call the function
    result = processor.load(mock_excel_bytes)

    # Verify the result contains only exact matches
    assert "Sheet1" in result
    filtered_df = result["Sheet1"]
    assert len(filtered_df) == 2  # Should have 2 rows with exactly "Complete"
    # Verify "Completed" is not included
    for _, row in filtered_df.iterrows():
        row_str = " ".join(str(val).lower() for val in row)
        assert "complete" in row_str
        assert "completed" not in row_str or "complete" in row_str


@patch('pandas.read_excel')
def test_processor_load_with_filter_no_matches(mock_read_excel, mock_excel_bytes):
    """Test loading Excel file with filter_value that has no matches"""
    # Setup mock return value
    df = pd.DataFrame(
        {"Project": ["NATI-5DME", "NATI-5DC", "NATI-4OXO"], "Employee": ["John", "Jane", "Bob"], "Hours": [40, 35, 20]}
    )
    mock_read_excel.return_value = {"Sheet1": df}

    # Create processor with filter that won't match anything
    processor = XlsxProcessor(filter_values=["NONEXISTENT-PROJECT"], filter_mode="contains")

    # Call the function
    result = processor.load(mock_excel_bytes)

    # Verify the result is empty
    assert "Sheet1" in result
    filtered_df = result["Sheet1"]
    assert len(filtered_df) == 0  # Should have no rows


@patch('pandas.read_excel')
def test_processor_load_with_filter_case_insensitive(mock_read_excel, mock_excel_bytes):
    """Test that filtering is case-insensitive"""
    # Setup mock return value
    df = pd.DataFrame(
        {
            "Project": ["NATI-5DME", "nati-5dc", "NaTi-4OXO"],
            "Employee": ["John", "Jane", "Bob"],
            "Status": ["COMPLETE", "complete", "Complete"],
        }
    )
    mock_read_excel.return_value = {"Sheet1": df}

    # Create processor with lowercase filter
    processor = XlsxProcessor(filter_values=["nati"], filter_mode="contains")

    # Call the function
    result = processor.load(mock_excel_bytes)

    # Verify all rows with "nati" (any case) are included
    assert "Sheet1" in result
    filtered_df = result["Sheet1"]
    assert len(filtered_df) == 3  # All rows contain "nati" in some case


@patch('pandas.read_excel')
def test_processor_load_without_filter(mock_read_excel, mock_excel_bytes):
    """Test loading Excel file without filter (default behavior)"""
    # Setup mock return value
    df = pd.DataFrame({"A": [1, 2, 3], "B": [4, 5, 6]})
    mock_read_excel.return_value = {"Sheet1": df}

    # Create processor without filter
    processor = XlsxProcessor()

    # Call the function
    result = processor.load(mock_excel_bytes)

    # Verify all rows are included
    assert "Sheet1" in result
    unfiltered_df = result["Sheet1"]
    assert len(unfiltered_df) == 3  # All rows should be present


@patch('pandas.read_excel')
def test_processor_load_with_filter_and_cleaning(mock_read_excel, mock_excel_bytes):
    """Test that filtering works correctly with data cleaning"""
    # Setup mock return value with empty rows
    df = pd.DataFrame(
        {
            "Project": ["NATI-5DME", "", "NATI-5DC", "", "OTHER-123"],
            "Employee": ["John", "", "Jane", "", "Charlie"],
            "Hours": [40, "", 35, "", 25],
        }
    )
    mock_read_excel.return_value = {"Sheet1": df}

    # Create processor with filter and cleaning enabled
    processor = XlsxProcessor(filter_values=["NATI"], filter_mode="contains")

    # Call the function
    result = processor.load(mock_excel_bytes, clean_data=True)

    # Verify empty rows were cleaned and then filtering was applied
    assert "Sheet1" in result
    filtered_df = result["Sheet1"]
    # Should have rows with "NATI" after cleaning empty rows
    assert len(filtered_df) == 2
    for _, row in filtered_df.iterrows():
        row_str = " ".join(str(val).lower() for val in row)
        assert "nati" in row_str


@patch('pandas.read_excel')
def test_processor_load_with_filter_multiple_sheets(mock_read_excel, mock_excel_bytes):
    """Test filtering across multiple sheets"""
    # Setup mock return value with multiple sheets
    df1 = pd.DataFrame({"Project": ["NATI-5DME", "NATI-5DC", "OTHER-123"], "Hours": [40, 35, 25]})
    df2 = pd.DataFrame({"Code": ["NATI-4OXO", "PROJ-100", "NATI-5DME"], "Budget": [12000, 8000, 10000]})
    mock_read_excel.return_value = {"Sheet1": df1, "Sheet2": df2}

    # Create processor with filter
    processor = XlsxProcessor(filter_values=["NATI-5DME"], filter_mode="contains")

    # Call the function
    result = processor.load(mock_excel_bytes)

    # Verify filtering is applied to both sheets
    assert "Sheet1" in result
    assert "Sheet2" in result
    assert len(result["Sheet1"]) == 1  # Only 1 row with "NATI-5DME" in Sheet1
    assert len(result["Sheet2"]) == 1  # Only 1 row with "NATI-5DME" in Sheet2


@patch('pandas.read_excel')
def test_processor_load_with_default_exact_mode(mock_read_excel, mock_excel_bytes):
    """Test that default filter_mode is 'exact'"""
    # Setup mock return value
    df = pd.DataFrame(
        {
            "Status": ["Complete", "In Progress", "Complete", "Completed"],
            "Task": ["Task 1", "Task 2", "Task 3", "Task 4"],
        }
    )
    mock_read_excel.return_value = {"Sheet1": df}

    # Create processor with filter but no mode specified (should default to 'exact')
    processor = XlsxProcessor(filter_values=["Complete"])

    # Call the function
    result = processor.load(mock_excel_bytes)

    # Verify exact matching (should not match "Completed")
    assert "Sheet1" in result
    filtered_df = result["Sheet1"]
    assert len(filtered_df) == 2  # Should match only exact "Complete", not "Completed"


@patch('pandas.read_excel')
def test_processor_load_with_multiple_filter_values_and_logic(mock_read_excel, mock_excel_bytes):
    """Test filtering with multiple filter values using AND logic"""
    # Setup mock return value - simulating a bug tracking sheet
    df = pd.DataFrame(
        {
            "ID": ["BUG-001", "BUG-002", "BUG-003", "BUG-004", "BUG-005"],
            "Status": ["Open", "Closed", "Open", "Open", "Closed"],
            "Priority": ["Critical", "Low", "Critical", "Medium", "Critical"],
            "Type": ["Bug", "Feature", "Bug", "Bug", "Feature"],
        }
    )
    mock_read_excel.return_value = {"Sheet1": df}

    # Create processor with multiple filter values (AND logic)
    # Should return only rows containing ALL three values: "Open" AND "Critical" AND "Bug"
    processor = XlsxProcessor(filter_values=["Open", "Critical", "Bug"], filter_mode="exact")

    # Call the function
    result = processor.load(mock_excel_bytes)

    # Verify the result contains only rows with ALL filter values
    assert "Sheet1" in result
    filtered_df = result["Sheet1"]

    # Only BUG-001 and BUG-003 should match (have Open, Critical, and Bug)
    assert len(filtered_df) == 2

    # Verify each filtered row contains all three values
    for _, row in filtered_df.iterrows():
        row_str = " ".join(str(val).lower() for val in row)
        assert "open" in row_str
        assert "critical" in row_str
        assert "bug" in row_str


@patch('pandas.read_excel')
def test_processor_load_with_multiple_filter_values_contains_mode(mock_read_excel, mock_excel_bytes):
    """Test filtering with multiple filter values using contains mode and AND logic"""
    # Setup mock return value - simulating a project tracking sheet
    df = pd.DataFrame(
        {
            "Project": ["NATI-5DME-001", "NATI-5DC-002", "PROJ-100", "NATI-5DME-003", "OTHER-200"],
            "Owner": ["John Smith", "Jane Doe", "Bob Wilson", "Alice Johnson", "Charlie Brown"],
            "Status": ["Active", "Inactive", "Active", "Active", "Active"],
            "Team": ["Backend", "Frontend", "Backend", "Backend", "QA"],
        }
    )
    mock_read_excel.return_value = {"Sheet1": df}

    # Create processor with multiple filter values in contains mode
    # Should return only rows containing ALL three: "NATI-5DME", "Active", and "Backend"
    processor = XlsxProcessor(filter_values=["NATI-5DME", "Active", "Backend"], filter_mode="contains")

    # Call the function
    result = processor.load(mock_excel_bytes)

    # Verify the result
    assert "Sheet1" in result
    filtered_df = result["Sheet1"]

    # Only NATI-5DME-001 and NATI-5DME-003 should match
    assert len(filtered_df) == 2

    # Verify each row contains all filter values
    for _, row in filtered_df.iterrows():
        row_str = " ".join(str(val).lower() for val in row)
        assert "nati-5dme" in row_str
        assert "active" in row_str
        assert "backend" in row_str


@patch('pandas.read_excel')
def test_processor_load_with_multiple_filter_values_no_matches(mock_read_excel, mock_excel_bytes):
    """Test that AND logic returns empty when no rows match all filter values"""
    # Setup mock return value
    df = pd.DataFrame(
        {
            "Status": ["Open", "Closed", "In Progress"],
            "Priority": ["High", "Low", "Medium"],
            "Type": ["Bug", "Feature", "Task"],
        }
    )
    mock_read_excel.return_value = {"Sheet1": df}

    # Create processor with filter values that can't all be in the same row
    # No row has both "Open" AND "Closed" at the same time
    processor = XlsxProcessor(filter_values=["Open", "Closed"], filter_mode="exact")

    # Call the function
    result = processor.load(mock_excel_bytes)

    # Verify the result is empty
    assert "Sheet1" in result
    filtered_df = result["Sheet1"]
    assert len(filtered_df) == 0
