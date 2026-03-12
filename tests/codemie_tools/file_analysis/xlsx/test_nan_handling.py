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
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from codemie_tools.file_analysis.xlsx.processor import XlsxProcessor


@pytest.fixture
def processor():
    """Create an XlsxProcessor instance for testing"""
    return XlsxProcessor()


@patch('pandas.read_excel')
def test_nan_replacement(mock_read_excel, processor):
    """Test that NaN values are replaced with empty strings"""
    # Create a DataFrame with NaN values
    df = pd.DataFrame({"A": [1, np.nan, 3], "B": [np.nan, 4, 5], "C": ["text", np.nan, "more text"]})

    # Setup mock return value
    mock_read_excel.return_value = {"Sheet1": df}

    # Call the function
    result = processor.load(io.BytesIO(b"mock excel content"))

    # Verify NaN values were replaced with empty strings
    assert not pd.isna(result["Sheet1"].loc[1, "A"])
    assert result["Sheet1"].loc[1, "A"] == ""
    assert not pd.isna(result["Sheet1"].loc[0, "B"])
    assert result["Sheet1"].loc[0, "B"] == ""
    assert not pd.isna(result["Sheet1"].loc[1, "C"])
    assert result["Sheet1"].loc[1, "C"] == ""


@patch('pandas.read_excel')
def test_nan_replacement_with_percentages(mock_read_excel, processor):
    """Test that NaN values are replaced with empty strings when processing percentages"""
    # Create a DataFrame with NaN values and percentage values
    df = pd.DataFrame(
        {
            "A": [0.01, np.nan, -0.05],  # 1%, NaN, -5%
            "B": [np.nan, 0.25, 1.5],  # NaN, 25%, 150%
        }
    )

    # Setup mock return value
    mock_read_excel.return_value = {"Sheet1": df}

    # Call the function
    result = processor.load(io.BytesIO(b"mock excel content"))

    # Verify NaN values were replaced with empty strings
    assert not pd.isna(result["Sheet1"].loc[1, "A"])
    assert result["Sheet1"].loc[1, "A"] == ""
    assert not pd.isna(result["Sheet1"].loc[0, "B"])
    assert result["Sheet1"].loc[0, "B"] == ""
