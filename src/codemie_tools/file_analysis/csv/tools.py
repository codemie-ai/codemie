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

import logging
from io import StringIO
from typing import Type, Optional, Dict, Any, Tuple, Union, List

import clevercsv
import pandas as pd
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.constants import SOURCE_DOCUMENT_KEY, SOURCE_FIELD_KEY, FILE_CONTENT_FIELD_KEY
from codemie_tools.base.file_object import FileObject
from codemie_tools.base.file_tool_mixin import FileToolMixin
from codemie_tools.file_analysis.models import FileAnalysisConfig
from codemie_tools.file_analysis.tool_vars import CSV_TOOL

logger = logging.getLogger(__name__)


def get_csv_delimiter(data: str, length_to_sniff: int) -> str:
    """Get the delimiter of the CSV file."""
    dialect = clevercsv.Sniffer().sniff(data[:length_to_sniff])
    return dialect.delimiter


class Input(BaseModel):
    method_name: str = Field(description="Method to be called on the pandas dataframe object generated from the file")
    method_args: dict = Field(description="Pandas dataframe arguments to be passed to the method", default={})
    column: Optional[str] = Field(description="Column to be used for the operation", default=None)


class CSVTool(CodeMieTool, FileToolMixin):
    """Tool for working with data from CSV files."""

    args_schema: Type[BaseModel] = Input
    name: str = CSV_TOOL.name
    label: str = CSV_TOOL.label
    description: str = CSV_TOOL.description
    config: FileAnalysisConfig

    def __init__(self, config: FileAnalysisConfig) -> None:
        """
        Initialize the CSVTool with configuration containing CSV files.

        Args:
            config: FileAnalysisConfig with input_files
        """
        super().__init__(config=config)

    def _get_supported_mime_types(self) -> Optional[List[str]]:
        """
        Get list of supported mime types for CSV processing.

        Returns:
            List of CSV mime types
        """
        return ['text/csv']

    def _get_supported_extensions(self) -> Optional[List[str]]:
        """
        Get list of supported file extensions for CSV processing.

        Returns:
            List of CSV file extensions
        """
        return ['.csv']

    @staticmethod
    def _process_single_csv(
        file_object: FileObject, method_name: str, method_args: Dict[str, Any], column: Optional[str] = None
    ) -> Tuple[str, Union[str, pd.DataFrame]]:
        """Process a single CSV file and return its processed content"""
        try:
            data = file_object.string_content()
            df = pd.read_csv(StringIO(data), sep=get_csv_delimiter(data, 128), on_bad_lines='skip')
            logger.debug(
                f"Processing CSV file '{file_object.name}'. "
                f"MethodName: {method_name}, "
                f"MethodArgs:{method_args}. "
                f"Column: {column}. "
                f"DataFrame: {df}"
            )
            if column:
                if column not in df.columns:
                    return file_object.name, f"Error: Column '{column}' not found in file {file_object.name}"
                col = df[column]
                result = getattr(col, method_name)
            else:
                result = getattr(df, method_name)

            result = result(**method_args) if len(method_args) else result()

            result = str(result)
            logger.debug(f"Processing CSV '{file_object.name}' completed. Result: {result}")
            return file_object.name, result
        except Exception as e:
            return file_object.name, f"Error processing {file_object.name}: {str(e)}"

    def execute(self, method_name: str, method_args=None, column: Optional[str] = None):
        if method_args is None:
            method_args = {}

        # Get supported CSV files from config (automatically filters by mime type)
        files = self._get_supported_files()

        if not files:
            raise ValueError(f"{self.name} requires at least one file to process.")

        # Process multiple files with formatted output for each
        result = []
        for file_object in files:
            _, file_content = self._process_single_csv(file_object, method_name, method_args, column)
            result.append(f"\n{SOURCE_DOCUMENT_KEY}\n")
            result.append(f"{SOURCE_FIELD_KEY} {file_object.name}\n")
            result.append(f"{FILE_CONTENT_FIELD_KEY} \n{file_content}\n")

        return "\n\n".join(result)
