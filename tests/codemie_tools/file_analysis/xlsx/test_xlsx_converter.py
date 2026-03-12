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


@patch('pandas.read_excel')
def test_load_with_bytes(mock_read_excel, mock_excel_bytes):
    """Test loading Excel file from bytes"""
    # Setup mock return value
    df1 = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
    df2 = pd.DataFrame({"X": [5, 6], "Y": [7, 8]})
    mock_read_excel.return_value = {"Sheet1": df1, "Sheet2": df2}

    # Create processor instance
    processor = XlsxProcessor()

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
def test_load_with_file_object(mock_read_excel, mock_excel_file):
    """Test loading Excel file from file-like object"""
    # Setup mock return value
    df1 = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
    mock_read_excel.return_value = {"Sheet1": df1}

    # Create processor instance
    processor = XlsxProcessor()

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
def test_load_with_visible_only(mock_read_excel, mock_load_workbook, mock_excel_bytes):
    """Test loading Excel file with visible_only=True"""
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
    # Create processor instance with visible_only=True
    processor = XlsxProcessor(visible_only=True)

    # Call the function with visible_only=True
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
def test_load_with_sheet_names(mock_read_excel, mock_excel_bytes):
    """Test loading Excel file with specific sheet_names"""
    # Setup mock return value
    df1 = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
    df2 = pd.DataFrame({"X": [5, 6], "Y": [7, 8]})
    mock_read_excel.return_value = {"Sheet1": df1, "Sheet2": df2}

    # Create processor instance with specific sheet_names
    processor = XlsxProcessor(sheet_names=["Sheet1"])

    # Call the function
    processor.load(mock_excel_bytes)

    # Verify pandas.read_excel was called with the correct sheet_names
    mock_read_excel.assert_called_once()
    args, kwargs = mock_read_excel.call_args
    assert kwargs['sheet_name'] == ["Sheet1"]


@patch('pandas.read_excel')
def test_load_with_clean_data(mock_read_excel, mock_excel_bytes):
    """Test loading Excel file with clean_data=True"""
    # Create test DataFrames with empty rows and columns
    df1 = pd.DataFrame({"A": [1, 2, "", ""], "B": [3, 4, "", ""], "C": ["", "", "", ""]})

    # Setup mock return value
    mock_read_excel.return_value = {"Sheet1": df1}

    # Create processor instance
    processor = XlsxProcessor()

    # Call the function with clean_data=True
    result = processor.load(mock_excel_bytes, clean_data=True)

    # Verify the result has cleaned data
    assert "Sheet1" in result
    assert result["Sheet1"].shape == (2, 2)  # Should remove empty rows and columns


@patch('pandas.read_excel')
def test_unnamed_columns_renaming_in_utils(mock_read_excel, mock_excel_bytes):
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

    # Create processor instance
    processor = XlsxProcessor()

    # Call the function
    result = processor.load(mock_excel_bytes)

    # Verify the columns were renamed correctly
    renamed_df = result["Sheet1"]
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
def test_load_visibility_error_handling(mock_load_workbook, mock_excel_bytes):
    """Test error handling when checking sheet visibility"""
    # Setup mock to raise an exception
    mock_load_workbook.side_effect = Exception("Test error")

    # Call the function with visible_only=True
    # Should not raise an exception, but log a warning and process all sheets
    with patch('pandas.read_excel') as mock_read_excel:
        df1 = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
        mock_read_excel.return_value = {"Sheet1": df1}

        # Create processor instance with visible_only=True
        processor = XlsxProcessor(visible_only=True)

        # Call the function
        processor.load(mock_excel_bytes)

        # Verify pandas.read_excel was called with sheet_name=None (all sheets)
        mock_read_excel.assert_called_once()
        args, kwargs = mock_read_excel.call_args
        assert kwargs['sheet_name'] is None
