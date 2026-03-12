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

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from markitdown import MarkItDown

from codemie_tools.base.file_object import FileObject
from codemie_tools.file_analysis.models import FileAnalysisConfig
from codemie_tools.file_analysis.xlsx.tools import XlsxTool


@pytest.fixture
def excel_file_object():
    """Create a mock Excel file object for testing"""
    file_obj = MagicMock(spec=FileObject)
    file_obj.name = "test.xlsx"
    file_obj.mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    file_obj.bytes_content.return_value = b"mock excel content"
    return file_obj


@pytest.fixture
def excel_tool(excel_file_object):
    """Create an ExcelTool instance with a mock file object"""
    return XlsxTool(config=FileAnalysisConfig(input_files=[excel_file_object]))


def test_excel_tool_init(excel_tool):
    """Test that ExcelTool initializes correctly"""
    assert excel_tool.name == "excel_tool"
    assert len(excel_tool.config.input_files) == 1
    assert excel_tool.config.input_files[0].name == "test.xlsx"


@patch.object(MarkItDown, 'convert')
def test_process_excel_file(mock_convert, excel_tool, excel_file_object):
    """Test processing an Excel file"""
    # Setup mock return value
    mock_result = MagicMock()
    mock_result.text_content = "# Sheet1\n| Column A | Column B |\n| --- | --- |\n| Value 1 | Value 2 |"
    mock_convert.return_value = mock_result

    # Call the method
    result = excel_tool._process_excel_file(excel_file_object)

    # Verify the result
    assert "# Sheet1" in result
    assert "| Column A | Column B |" in result
    assert "| Value 1 | Value 2 |" in result

    # Verify the mock was called correctly
    mock_convert.assert_called_once()


@patch.object(XlsxTool, '_process_excel_file')
def test_execute(mock_process, excel_tool, excel_file_object):
    """Test the execute method"""
    # Setup mock return value
    mock_process.return_value = "# Sheet1\n| Column A | Column B |\n| --- | --- |\n| Value 1 | Value 2 |"

    # Call the method
    result = excel_tool.execute(query="test query", sheet_names=["Sheet1"])

    # Verify the result
    assert "# Sheet1" in result
    assert "| Column A | Column B |" in result
    assert "| Value 1 | Value 2 |" in result
    assert excel_file_object.name in result

    # Verify the mock was called correctly
    mock_process.assert_called_once_with(excel_file_object, sheet_names=["Sheet1"], visible_only=True)


def test_execute_no_files():
    """Test execute with no files"""
    tool = XlsxTool(config=FileAnalysisConfig(input_files=[]))
    with pytest.raises(ValueError, match="requires at least one Excel file"):
        tool.execute()


@patch.object(MarkItDown, 'convert')
def test_process_excel_file_error(mock_convert, excel_tool, excel_file_object):
    """Test handling errors when processing an Excel file"""
    # Setup mock to raise an exception
    mock_convert.side_effect = Exception("Test error")

    # Call the method
    result = excel_tool._process_excel_file(excel_file_object)

    # Verify the result contains the error message
    assert "Failed to process Excel file" in result
    assert "Test error" in result


@patch.object(XlsxTool, '_get_sheet_names')
def test_get_sheet_names(mock_get_sheet_names, excel_tool, excel_file_object):
    """Test getting sheet names"""
    # Setup mock return value
    mock_get_sheet_names.return_value = ["Sheet1", "Sheet2", "Sheet3"]

    # Call the method
    result = excel_tool.execute(get_sheet_names=True)

    # Verify the result
    assert "Sheets in" in result
    assert "Sheet1" in result
    assert "Sheet2" in result
    assert "Sheet3" in result

    # Verify the mock was called correctly
    mock_get_sheet_names.assert_called_once_with(excel_file_object, visible_only=True)


@patch.object(XlsxTool, '_load_excel_file')
def test_get_sheet_by_index(mock_load_excel_file, excel_tool, excel_file_object):
    """Test getting a sheet by index"""
    # Create sample DataFrames for multiple sheets
    df1 = pd.DataFrame({"Column A": ["Value 1", "Value 2"], "Column B": [1, 2]})
    df2 = pd.DataFrame({"Column X": ["Value 3", "Value 4"], "Column Y": [3, 4]})

    # Setup mock return value
    mock_load_excel_file.return_value = {"Sheet1": df1, "Sheet2": df2}

    # Call the method with sheet_index=0 (should get Sheet1)
    result = excel_tool.execute(sheet_index=0)

    # Verify the result contains Sheet1 data
    assert "Sheet1" in result
    assert "Column A" in result
    assert "Column B" in result
    assert "Value 1" in result
    assert "Value 2" in result

    # Verify the mock was called correctly
    mock_load_excel_file.assert_called_once_with(
        excel_file_object, visible_only=True, filter_values=None, filter_mode="exact"
    )


@patch.object(XlsxTool, '_load_excel_file')
def test_get_sheet_by_index_error(mock_load_excel_file, excel_tool, excel_file_object):
    """Test getting a sheet by index with an error"""
    # Setup mock return value - only 2 sheets available
    df1 = pd.DataFrame({"A": [1, 2]})
    df2 = pd.DataFrame({"B": [3, 4]})
    mock_load_excel_file.return_value = {"Sheet1": df1, "Sheet2": df2}

    # Call the method with invalid index
    result = excel_tool.execute(sheet_index=5)

    # Verify the result contains error message
    assert "Invalid sheet index: 5" in result
    assert "Valid range: 0-1" in result

    # Verify the mock was called correctly
    mock_load_excel_file.assert_called_once_with(
        excel_file_object, visible_only=True, filter_values=None, filter_mode="exact"
    )


@patch.object(XlsxTool, '_get_excel_stats')
def test_get_excel_stats(mock_get_excel_stats, excel_tool, excel_file_object):
    """Test getting Excel statistics"""
    # Setup mock return value with enhanced stats structure
    mock_stats = {
        "file_name": "test.xlsx",
        "sheet_count": 2,
        "sheets": {
            "Sheet1": {
                "raw": {"non_empty_cells": 45, "total_cells": 50, "fill_rate": 0.9},
                "clean": {"non_empty_cells": 45, "total_cells": 32, "fill_rate": 0.95},
                "columns": ["A", "B", "C", "D", "E"],
                "data_types": {"A": "string", "B": "integer", "C": "float", "D": "datetime", "E": "boolean"},
                "sample_values": {
                    "A": ["Value 1", "Value 2", "Value 3"],
                    "B": ["1", "2", "3"],
                    "C": ["1.1", "2.2", "3.3"],
                    "D": ["2023-01-01", "2023-01-02"],
                    "E": ["True", "False"],
                },
                "numeric_columns": {
                    "B": {
                        "min": 1.0,
                        "max": 10.0,
                        "mean": 5.5,
                        "median": 5.0,
                        "std": 2.5,
                        "q1": 3.0,
                        "q3": 8.0,
                        "null_count": 0,
                        "zero_count": 0,
                    },
                    "C": {
                        "min": 1.1,
                        "max": 9.9,
                        "mean": 5.0,
                        "median": 4.5,
                        "std": 2.2,
                        "q1": 2.5,
                        "q3": 7.5,
                        "null_count": 2,
                        "zero_count": 0,
                    },
                },
            },
            "Sheet2": {
                "raw": {
                    "row_count": 5,
                    "column_count": 3,
                    "empty_rows": 0,
                    "empty_columns": 0,
                    "non_empty_cells": 12,
                    "total_cells": 15,
                    "fill_rate": 0.8,
                },
                "clean": {
                    "row_count": 5,
                    "column_count": 3,
                    "non_empty_cells": 12,
                    "total_cells": 15,
                    "fill_rate": 0.8,
                },
                "columns": ["X", "Y", "Z"],
                "data_types": {"X": "string", "Y": "integer", "Z": "string"},
                "sample_values": {"X": ["X1", "X2"], "Y": ["10", "20"], "Z": ["Z1", "Z2"]},
            },
        },
    }
    mock_get_excel_stats.return_value = mock_stats

    # Call the method
    result = excel_tool.execute(get_stats=True)

    # Verify the result
    assert "Excel File Statistics" in result
    assert "test.xlsx" in result
    assert "**Total Sheets:** 2" in result
    assert "Sheet: Sheet1" in result

    # Check for column information
    assert "Columns:" in result

    # Check for column information
    assert "Column | Data Type | Sample Values" in result

    # Check for data types
    assert "Data Type" in result
    assert "Sample Values" in result

    # Check for Sheet2
    assert "Sheet: Sheet2" in result

    # Verify the mock was called correctly
    mock_get_excel_stats.assert_called_once_with(excel_file_object, visible_only=True)


@patch.object(XlsxTool, '_load_excel_file')
def test_get_sheet_names_implementation(mock_load_excel_file, excel_tool, excel_file_object):
    """Test the implementation of _get_sheet_names"""
    # Setup mock return value
    mock_load_excel_file.return_value = {"Sheet1": pd.DataFrame(), "Sheet2": pd.DataFrame()}

    # Call the method
    result = excel_tool._get_sheet_names(excel_file_object)

    # Verify the result
    assert result == ["Sheet1", "Sheet2"]
    mock_load_excel_file.assert_called_once_with(excel_file_object, visible_only=True)


@patch.object(XlsxTool, '_load_excel_file')
def test_get_sheet_by_index_implementation(mock_load_excel_file, excel_tool, excel_file_object):
    """Test the implementation of _get_sheet_by_index"""
    # Setup mock return value
    df1 = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
    df2 = pd.DataFrame({"X": [5, 6], "Y": [7, 8]})
    mock_load_excel_file.return_value = {"Sheet1": df1, "Sheet2": df2}

    # Call the method with valid index
    result_df, result_name = excel_tool._get_sheet_by_index(excel_file_object, 1)

    # Verify the result
    assert result_name == "Sheet2"
    pd.testing.assert_frame_equal(result_df, df2)

    # Call the method with invalid index
    result_error, result_none = excel_tool._get_sheet_by_index(excel_file_object, 2)

    # Verify the error result
    assert "Invalid sheet index: 2" in result_error
    assert result_none is None


@patch.object(pd, 'read_excel')
def test_load_excel_file_with_cleaning(mock_read_excel, excel_tool, excel_file_object):
    """Test loading Excel file with data cleaning"""
    # Create test DataFrames with empty rows and columns
    df1 = pd.DataFrame({"A": [1, 2, "", ""], "B": [3, 4, "", ""], "C": ["", "", "", ""]})

    # Setup mock return value
    mock_read_excel.return_value = {"Sheet1": df1}

    # Test without cleaning
    sheets = excel_tool._load_excel_file(excel_file_object, clean_data=False)
    assert "Sheet1" in sheets
    assert sheets["Sheet1"].shape == (4, 3)  # Original shape with empty rows/columns

    # Test with cleaning
    sheets_clean = excel_tool._load_excel_file(excel_file_object, clean_data=True)
    assert "Sheet1" in sheets_clean
    assert sheets_clean["Sheet1"].shape == (2, 2)  # Should remove empty rows and columns

    # Verify the mock was called correctly
    assert mock_read_excel.call_count == 2


@patch.object(pd, 'read_excel')
def test_unnamed_columns_renaming(mock_read_excel, excel_tool, excel_file_object):
    """Test renaming of 'Unnamed: X' columns to 'ColX'"""
    # Create test DataFrame with unnamed columns
    df = pd.DataFrame()
    df['Normal Column'] = [1, 2, 3]
    df['Unnamed: 0'] = [4, 5, 6]
    df['Unnamed: 1'] = [7, 8, 9]
    df['Another Column'] = [10, 11, 12]
    df['Unnamed: 42'] = [13, 14, 15]

    # Setup mock return value
    mock_read_excel.return_value = {"Sheet1": df}

    # Test column renaming
    sheets = excel_tool._load_excel_file(excel_file_object, clean_data=False)

    # Verify the columns were renamed correctly
    renamed_df = sheets["Sheet1"]
    assert "Normal Column" in renamed_df.columns
    assert "Another Column" in renamed_df.columns
    assert "Unnamed: 0" not in renamed_df.columns
    assert "Unnamed: 1" not in renamed_df.columns
    assert "Unnamed: 42" not in renamed_df.columns
    assert "Col0" in renamed_df.columns
    assert "Col1" in renamed_df.columns
    assert "Col42" in renamed_df.columns

    # Verify the data is preserved
    assert renamed_df["Col0"].tolist() == [4, 5, 6]
    assert renamed_df["Col1"].tolist() == [7, 8, 9]
    assert renamed_df["Col42"].tolist() == [13, 14, 15]


@patch('openpyxl.load_workbook')
@patch.object(pd, 'read_excel')
def test_visible_only_sheets(mock_read_excel, mock_load_workbook, excel_tool, excel_file_object):
    """Test loading only visible sheets"""
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

    mock_sheet4 = MagicMock()
    mock_sheet4.title = "VeryHiddenSheet"
    mock_sheet4.sheet_state = 'veryHidden'

    mock_wb.worksheets = [mock_sheet1, mock_sheet2, mock_sheet3, mock_sheet4]
    mock_load_workbook.return_value = mock_wb

    # Setup pandas read_excel mock
    visible_df1 = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
    visible_df2 = pd.DataFrame({"X": [5, 6], "Y": [7, 8]})
    mock_read_excel.return_value = {"VisibleSheet1": visible_df1, "VisibleSheet2": visible_df2}

    # Test with visible_only=True
    sheets = excel_tool._load_excel_file(excel_file_object, visible_only=True)

    # Verify only visible sheets are included
    assert len(sheets) == 2
    assert "VisibleSheet1" in sheets
    assert "VisibleSheet2" in sheets
    assert "HiddenSheet" not in sheets
    assert "VeryHiddenSheet" not in sheets

    # Verify openpyxl was called at least once to check visibility
    mock_load_workbook.assert_called()
    # First call should be for checking visibility
    first_call_args = mock_load_workbook.call_args_list[0]
    assert first_call_args[1].get('read_only') is True

    # Verify pandas read_excel was called with the list of visible sheets
    mock_read_excel.assert_called_once()
    args, kwargs = mock_read_excel.call_args
    assert kwargs['sheet_name'] == ['VisibleSheet1', 'VisibleSheet2']


@patch.object(XlsxTool, '_process_excel_file')
def test_execute_with_visible_only_parameter(mock_process, excel_tool, excel_file_object):
    """Test the execute method with visible_only parameter"""
    # Setup mock return value
    mock_process.return_value = "# Sheet1\n| Column A | Column B |\n| --- | --- |\n| Value 1 | Value 2 |"

    # Call the method with visible_only=False
    excel_tool.execute(query="test query", sheet_names=["Sheet1"], visible_only=False)

    # Verify the mock was called with visible_only=False
    mock_process.assert_called_once_with(excel_file_object, sheet_names=["Sheet1"], visible_only=False)

    # Reset mock
    mock_process.reset_mock()

    # Call the method with default visible_only (True)
    excel_tool.execute(query="test query", sheet_names=["Sheet1"])

    # Verify the mock was called with visible_only=True
    mock_process.assert_called_once_with(excel_file_object, sheet_names=["Sheet1"], visible_only=True)


@patch.object(XlsxTool, '_load_excel_file')
def test_execute_with_filter_value(mock_load_excel_file, excel_tool, excel_file_object):
    """Test execute method with filter_value parameter"""
    # Setup mock return value
    filtered_df = pd.DataFrame(
        {"Project": ["NATI-5DME", "NATI-5DME"], "Employee": ["John", "Alice"], "Hours": [40, 30]}
    )
    mock_load_excel_file.return_value = {"Sheet1": filtered_df}

    # Call the method with filter_value
    result = excel_tool.execute(filter_values=["NATI-5DME"], filter_mode="contains")

    # Verify the result
    assert "NATI-5DME" in result
    assert "John" in result
    assert "Alice" in result
    assert excel_file_object.name in result

    # Verify the mock was called with filter parameters
    mock_load_excel_file.assert_called_once()
    call_kwargs = mock_load_excel_file.call_args[1]
    assert call_kwargs['filter_values'] == ["NATI-5DME"]
    assert call_kwargs['filter_mode'] == "contains"


@patch.object(XlsxTool, '_load_excel_file')
def test_execute_with_filter_exact_mode(mock_load_excel_file, excel_tool, excel_file_object):
    """Test execute method with filter in exact mode"""
    # Setup mock return value
    filtered_df = pd.DataFrame({"Status": ["Complete", "Complete"], "Task": ["Task 1", "Task 4"]})
    mock_load_excel_file.return_value = {"Sheet1": filtered_df}

    # Call the method with exact filter
    result = excel_tool.execute(filter_values=["Complete"], filter_mode="exact")

    # Verify the result
    assert "Complete" in result
    assert "Task 1" in result
    assert "Task 4" in result

    # Verify the mock was called with exact mode
    call_kwargs = mock_load_excel_file.call_args[1]
    assert call_kwargs['filter_values'] == ["Complete"]
    assert call_kwargs['filter_mode'] == "exact"


@patch.object(XlsxTool, '_load_excel_file')
def test_execute_with_filter_and_sheet_index(mock_load_excel_file, excel_tool, excel_file_object):
    """Test execute method with both filter_value and sheet_index"""
    # Setup mock return value with multiple sheets
    filtered_df1 = pd.DataFrame({"Project": ["NATI-5DME"], "Employee": ["John"], "Hours": [40]})
    filtered_df2 = pd.DataFrame({"Code": ["NATI-5DME"], "Budget": [10000]})
    mock_load_excel_file.return_value = {"Sheet1": filtered_df1, "Sheet2": filtered_df2}

    # Call the method with filter and sheet_index
    result = excel_tool.execute(filter_values=["NATI-5DME"], sheet_index=1)

    # Verify the result contains data from Sheet2 only
    assert "Sheet2" in result
    assert "Budget" in result
    assert "10000" in result

    # Verify the mock was called with filter parameters
    call_kwargs = mock_load_excel_file.call_args[1]
    assert call_kwargs['filter_values'] == ["NATI-5DME"]


@patch.object(XlsxTool, '_load_excel_file')
def test_execute_with_filter_no_matches(mock_load_excel_file, excel_tool, excel_file_object):
    """Test execute method with filter that has no matches"""
    # Setup mock return value with empty DataFrame
    empty_df = pd.DataFrame()
    mock_load_excel_file.return_value = {"Sheet1": empty_df}

    # Call the method with filter that has no matches
    result = excel_tool.execute(filter_values=["NONEXISTENT"])

    # Verify the result still contains file metadata
    assert excel_file_object.name in result


@patch.object(XlsxTool, '_load_excel_file')
def test_execute_with_filter_multiple_sheets(mock_load_excel_file, excel_tool, excel_file_object):
    """Test execute method with filter across multiple sheets"""
    # Setup mock return value with filtered data from multiple sheets
    filtered_df1 = pd.DataFrame({"Project": ["NATI-5DME"], "Hours": [40]})
    filtered_df2 = pd.DataFrame({"Code": ["NATI-5DME"], "Budget": [10000]})
    mock_load_excel_file.return_value = {"Sheet1": filtered_df1, "Sheet2": filtered_df2}

    # Call the method with filter
    result = excel_tool.execute(filter_values=["NATI-5DME"])

    # Verify both sheets are in the result
    assert "Sheet1" in result
    assert "Sheet2" in result
    assert "NATI-5DME" in result
    assert "40" in result
    assert "10000" in result


@patch.object(XlsxTool, '_process_excel_file')
def test_execute_without_filter_uses_markitdown(mock_process, excel_tool, excel_file_object):
    """Test that execute without filter uses markitdown processor"""
    # Setup mock return value
    mock_process.return_value = "# Sheet1\n| Column A | Column B |\n| --- | --- |\n| Value 1 | Value 2 |"

    # Call the method without filter
    result = excel_tool.execute()

    # Verify markitdown processor was used
    mock_process.assert_called_once()
    assert "# Sheet1" in result


@patch.object(XlsxTool, '_load_excel_file')
def test_execute_with_filter_and_sheet_names(mock_load_excel_file, excel_tool, excel_file_object):
    """Test execute method with filter and specific sheet names"""
    # Setup mock return value
    filtered_df = pd.DataFrame({"Project": ["NATI-5DME"], "Employee": ["John"]})
    mock_load_excel_file.return_value = {"SpecificSheet": filtered_df}

    # Call the method with filter and sheet_names
    result = excel_tool.execute(filter_values=["NATI-5DME"], sheet_names=["SpecificSheet"])

    # Verify the result
    assert "SpecificSheet" in result
    assert "NATI-5DME" in result

    # Verify the mock was called with both parameters
    call_kwargs = mock_load_excel_file.call_args[1]
    assert call_kwargs['filter_values'] == ["NATI-5DME"]
    assert call_kwargs['sheet_names'] == ["SpecificSheet"]


@patch.object(XlsxTool, '_load_excel_file')
def test_load_excel_file_passes_filter_parameters(mock_load_excel_file, excel_tool, excel_file_object):
    """Test that _load_excel_file is called with correct filter parameters"""
    # Setup mock return value
    df = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
    mock_load_excel_file.return_value = {"Sheet1": df}

    # Call execute with various filter parameters
    excel_tool.execute(filter_values=["test_value"], filter_mode="contains")

    # Verify _load_excel_file was called with correct parameters
    call_kwargs = mock_load_excel_file.call_args[1]
    assert call_kwargs['filter_values'] == ["test_value"]
    assert call_kwargs['filter_mode'] == "contains"
