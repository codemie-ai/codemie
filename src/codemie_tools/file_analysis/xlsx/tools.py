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
import logging
from typing import Type, Optional, List, Dict, Any

import pandas as pd
from markitdown import MarkItDown, PRIORITY_SPECIFIC_FILE_FORMAT
from pandas import DataFrame
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.constants import (
    SOURCE_DOCUMENT_KEY,
    SOURCE_FIELD_KEY,
    FILE_CONTENT_FIELD_KEY,
)
from codemie_tools.base.file_object import FileObject
from codemie_tools.base.file_tool_mixin import FileToolMixin
from codemie_tools.file_analysis.models import FileAnalysisConfig
from codemie_tools.file_analysis.tool_vars import EXCEL_TOOL
from codemie_tools.file_analysis.xlsx.markitdown_xlsx_converter import XlsxConverter
from codemie_tools.file_analysis.xlsx.processor import XlsxProcessor

logger = logging.getLogger(__name__)


class XslxToolInput(BaseModel):
    query: str = Field(default="", description="""User initial request should be passed as a string.""")
    sheet_names: List[str] = Field(
        default_factory=list,
        description="""Sheet names of Excel file to analyze. If empty, all sheets will be processed.""",
    )
    sheet_index: Optional[int] = Field(
        default=None,
        description="""Index of the sheet to analyze (0-based). If provided, overrides sheet_names.""",
    )
    get_sheet_names: bool = Field(
        default=False,
        description="""If True, returns only the names of all sheets in the Excel file.""",
    )
    get_stats: bool = Field(
        default=False,
        description="""If True, returns statistics about the Excel file (sheet count, row counts, column counts).""",
    )
    visible_only: bool = Field(
        default=True,
        description="""If True, only visible sheets will be processed. Hidden and very hidden sheets will be ignored.""",
    )
    filter_values: Optional[List[str]] = Field(
        default=None,
        description="""List of values to search for across all cells in each row. Only rows containing ALL specified values (AND logic) will be included in the output. Each value can appear in any cell of the row. Works with pivot tables and complex Excel layouts. Example: ["Critical", "Open", "Bug"] returns only rows containing all three values.""",
    )
    filter_mode: str = Field(
        default="exact",
        description="""Filter matching mode: 'exact' (default) - exact match, 'contains' - substring match. Case-insensitive. Applies to all filter values.""",
    )


class XlsxTool(CodeMieTool, FileToolMixin):
    """Tool for working with and analyzing Excel file contents."""

    args_schema: Optional[Type[BaseModel]] = XslxToolInput
    name: str = EXCEL_TOOL.name
    label: str = EXCEL_TOOL.label
    description: str = EXCEL_TOOL.description
    config: FileAnalysisConfig
    tokens_size_limit: int = 100_000

    def __init__(self, config: FileAnalysisConfig) -> None:
        """
        Initialize the XlsxTool with configuration containing Excel files.

        Args:
            config: FileAnalysisConfig with input_files and optional chat_model
        """
        super().__init__(config=config)

    def _get_supported_mime_types(self) -> Optional[List[str]]:
        """
        Get list of supported mime types for Excel processing.

        Returns:
            List of Excel mime types
        """
        return ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'application/vnd.ms-excel']

    def _get_supported_extensions(self) -> Optional[List[str]]:
        """
        Get list of supported file extensions for Excel processing.

        Returns:
            List of Excel file extensions
        """
        return ['.xlsx', '.xls']

    @staticmethod
    def _load_excel_file(
        file_object: FileObject,
        clean_data: bool = True,
        visible_only: bool = True,
        sheet_names: List[str] = None,
        filter_values: Optional[List[str]] = None,
        filter_mode: str = "exact",
    ) -> Dict[str, pd.DataFrame]:
        """Load an Excel file and return a dictionary of DataFrames for each sheet

        Args:
            file_object: The FileObject containing the Excel file
            clean_data: If True, clean the data by removing empty rows and columns
            visible_only: If True, only visible sheets will be processed
            sheet_names: Optional list of specific sheet names to load
            filter_values: List of values to search for. Row must contain ALL values (AND logic).
            filter_mode: Filter matching mode ('exact' - exact match, 'contains' - substring match)

        Returns:
            Dictionary of DataFrames for each sheet
        """
        try:
            processor = XlsxProcessor(
                sheet_names=sheet_names,
                visible_only=visible_only,
                filter_values=filter_values,
                filter_mode=filter_mode,
            )
            return processor.load(file_object.bytes_content(), clean_data=clean_data)
        except Exception as e:
            logger.error(f"Failed to load Excel file: {str(e)}")
            raise e

    def _get_sheet_names(self, file_object: FileObject, visible_only: bool = True) -> List[str]:
        """Get the names of all sheets in an Excel file

        Args:
            file_object: The FileObject containing the Excel file
            visible_only: If True, only visible sheets will be returned

        Returns:
            List of sheet names
        """
        try:
            sheets = self._load_excel_file(file_object, visible_only=visible_only)
            return list(sheets.keys())
        except Exception as e:
            logger.error(f"Failed to get sheet names: {str(e)}")
            return [f"Error getting sheet names: {str(e)}"]

    def _get_sheet_by_index(
        self, file_object: FileObject, index: int, visible_only: bool = True
    ) -> tuple[DataFrame, str] | tuple[str, None]:
        """Get a specific sheet by its index (0-based)

        Args:
            file_object: The FileObject containing the Excel file
            index: The index of the sheet to get (0-based)
            visible_only: If True, only visible sheets will be considered

        Returns:
            DataFrame of the sheet and its name, or error message and None
        """
        try:
            sheets = self._load_excel_file(file_object, visible_only=visible_only)
            sheet_names = list(sheets.keys())
            if 0 <= index < len(sheet_names):
                sheet_name = sheet_names[index]
                return sheets[sheet_name], sheet_name
            else:
                return f"Invalid sheet index: {index}. Valid range: 0-{len(sheet_names)-1}", None
        except Exception as e:
            logger.error(f"Failed to get sheet by index: {str(e)}")
            return f"Error getting sheet by index: {str(e)}", None

    @staticmethod
    def _detect_column_data_type(column: pd.Series) -> str:
        """Detect the data type of a column

        Args:
            column: The pandas Series to analyze

        Returns:
            String representation of the data type
        """
        if pd.api.types.is_numeric_dtype(column):
            if pd.api.types.is_integer_dtype(column):
                return "integer"
            else:
                return "float"
        elif pd.api.types.is_datetime64_dtype(column):
            return "datetime"
        else:
            # Check if it's a boolean column (True/False values)
            unique_vals = set(column.astype(str).str.lower().unique())
            if unique_vals.issubset({"true", "false", "", "yes", "no", "y", "n"}):
                return "boolean"
            else:
                return "string"

    @staticmethod
    def _get_column_sample_values(column: pd.Series, max_samples: int = 5, max_length: int = 50) -> List[str]:
        """Get sample values from a column

        Args:
            column: The pandas Series to get samples from
            max_samples: Maximum number of samples to return
            max_length: Maximum length of each sample string

        Returns:
            List of sample values as strings
        """
        unique_vals = column.astype(str).unique()
        # Limit samples and truncate long values
        return [
            str(val)[:max_length] + ("..." if len(str(val)) > max_length else "") for val in unique_vals[:max_samples]
        ]

    def _get_sheet_statistics(self, dataframe: pd.DataFrame) -> Dict[str, Any]:
        """Generate statistics for a single sheet

        Args:
            dataframe: DataFrame containing the sheet data

        Returns:
            Dictionary of sheet statistics
        """
        data_types = {}
        sample_values = {}

        for col in dataframe.columns:
            data_types[col] = self._detect_column_data_type(dataframe[col])
            sample_values[col] = self._get_column_sample_values(dataframe[col])

        return {
            "columns": list(dataframe.columns),
            "data_types": data_types,
            "sample_values": sample_values,
        }

    def _get_excel_stats(self, file_object: FileObject, visible_only: bool = True) -> Dict[str, Any]:
        """Get comprehensive statistics about an Excel file, with data cleaning to avoid pollution

        Args:
            file_object: The FileObject containing the Excel file
            visible_only: If True, only visible sheets will be included in stats

        Returns:
            Dictionary of statistics about the Excel file
        """
        try:
            # Load cleaned sheets for clean stats
            raw_sheets = self._load_excel_file(file_object, visible_only=visible_only)

            stats = {"file_name": file_object.name, "sheet_count": len(raw_sheets), "sheets": {}}

            # Process each sheet
            for sheet_name, raw_df in raw_sheets.items():
                stats["sheets"][sheet_name] = self._get_sheet_statistics(raw_df)

            return stats
        except Exception as e:
            logger.error(f"Failed to get Excel stats: {str(e)}")
            return {"error": f"Failed to get Excel stats: {str(e)}"}

    def _process_excel_file(
        self, file_object: FileObject, sheet_names: List[str] = None, visible_only: bool = True
    ) -> str:
        """Process an Excel file and return its content as markdown text

        Args:
            file_object: The FileObject containing the Excel file
            sheet_names: List of sheet names to process. If None, all sheets will be processed
            visible_only: If True, only visible sheets will be processed

        Returns:
            Markdown text representation of the Excel file
        """
        try:
            chat_model = self.config.chat_model
            llm_model = (
                getattr(chat_model, "model_name", None) or getattr(chat_model, "model", None) if chat_model else None
            )
            md = MarkItDown(
                enable_builtins=True,
                llm_client=chat_model.client if chat_model and hasattr(chat_model, "client") else None,
                llm_model=llm_model,
            )
            md.register_converter(
                XlsxConverter(sheet_names=sheet_names, visible_only=visible_only),
                priority=PRIORITY_SPECIFIC_FILE_FORMAT,
            )
            # Create a file-like object from bytes content
            binary_content = io.BytesIO(file_object.bytes_content())
            result = md.convert(binary_content)
            return result.text_content
        except FileNotFoundError as e:
            # Handle the case when a file is not found
            return f"File not found: {str(e)}"
        except Exception as e:
            # Return error message for Excel processing failures
            return f"Failed to process Excel file: {str(e)}"

    @staticmethod
    def _format_dataframe_as_markdown(df: pd.DataFrame, sheet_name: str = None) -> str:
        """Format a DataFrame as a markdown table"""
        header = f"## {sheet_name}\n" if sheet_name else ""
        return header + df.to_markdown(index=False)

    @staticmethod
    def _format_stats_as_markdown(stats: Dict[str, Any]) -> str:
        """Format Excel statistics as markdown with comprehensive details"""
        if "error" in stats:
            return f"Error: {stats['error']}"

        results = [
            f"# Excel File Statistics: {stats['file_name']}",
            f"- **Total Sheets:** {stats['sheet_count']}",
        ]

        for sheet_name, sheet_stats in stats["sheets"].items():
            results.append(f"\n## Sheet: {sheet_name}")

            # Column information
            if "columns" in sheet_stats:
                results.append("\n### Columns:")
                results.append("| Column | Data Type | Sample Values |")
                results.append("| ------ | --------- | ------------- |")

                for col in sheet_stats["columns"]:
                    data_type = sheet_stats.get("data_types", {}).get(col, "unknown")
                    samples = sheet_stats.get("sample_values", {}).get(col, [])
                    sample_str = ", ".join([f"`{s}`" for s in samples[:3]])
                    if len(samples) > 3:
                        sample_str += ", ..."
                    results.append(f"| {col} | {data_type} | {sample_str} |")

        return "\n".join(results)

    def execute(self, query: str = "", **kwargs):
        """Execute the Excel analysis tool with various options.

        Args:
            query: User's initial request string
            **kwargs: Additional parameters including sheet_names, sheet_index, etc.

        Returns:
            Processed Excel content as markdown string
        """
        # Get supported Excel files from config (automatically filters by mime type)
        files = self._get_supported_files()

        if not files:
            raise ValueError(f"{self.name} requires at least one Excel file to process.")

        # Extract parameters from kwargs with defaults
        sheet_names = kwargs.get("sheet_names")
        sheet_index = kwargs.get("sheet_index")
        get_sheet_names = kwargs.get("get_sheet_names", False)
        get_stats = kwargs.get("get_stats", False)
        visible_only = kwargs.get("visible_only", True)
        filter_values = kwargs.get("filter_values")
        filter_mode = kwargs.get("filter_mode", "exact")

        return self._process_files(
            sheet_names,
            sheet_index,
            get_sheet_names,
            get_stats,
            visible_only,
            filter_values,
            filter_mode,
        )

    def _process_files(
        self,
        sheet_names: List[str],
        sheet_index: int,
        get_sheet_names: bool,
        get_stats: bool,
        visible_only: bool,
        filter_values: Optional[List[str]],
        filter_mode: str,
    ) -> str:
        """Process all files with the specified parameters

        Returns:
            Joined results from all files
        """
        files = self._get_supported_files()

        # Process multiple Excel files with LLM-friendly separators
        result = []

        for file_object in files:
            content = self._process_file_content(
                file_object,
                sheet_names,
                sheet_index,
                get_sheet_names,
                get_stats,
                visible_only,
                filter_values,
                filter_mode,
            )

            # Add file header with metadata
            result.append(f"\n{SOURCE_DOCUMENT_KEY}\n")
            result.append(f"{SOURCE_FIELD_KEY} {file_object.name}\n")
            result.append(f"{FILE_CONTENT_FIELD_KEY} \n{content}\n")

        return "\n".join(result)

    def _process_file_content(
        self,
        file_object: FileObject,
        sheet_names: List[str],
        sheet_index: int,
        get_sheet_names: bool,
        get_stats: bool,
        visible_only: bool,
        filter_values: Optional[List[str]],
        filter_mode: str,
    ) -> str:
        """Process file content based on various options

        Args:
            file_object: The FileObject containing Excel file
            sheet_names: List of sheet names
            sheet_index: Specific sheet index
            get_sheet_names: Whether to only get sheet names
            get_stats: Whether to get statistics
            visible_only: Whether to only process visible sheets
            filter_values: List of values to filter rows (AND logic)
            filter_mode: Filter mode (exact or contains)

        Returns:
            Processed content as string
        """
        # Handle special operations
        if get_sheet_names:
            return self._get_sheet_names_content(file_object, visible_only)

        if get_stats:
            return self._get_stats_content(file_object, visible_only)

        if sheet_index is not None:
            return self._get_sheet_by_index_content(file_object, sheet_index, visible_only, filter_values, filter_mode)

        # Check if we need filtering
        if filter_values:
            return self._get_filtered_content(file_object, sheet_names, visible_only, filter_values, filter_mode)

        # Default: standard processing
        return self._process_excel_file(file_object, sheet_names=sheet_names, visible_only=visible_only)

    def _get_sheet_names_content(self, file_object: FileObject, visible_only: bool) -> str:
        """Get sheet names as formatted content"""
        sheet_list = self._get_sheet_names(file_object, visible_only=visible_only)
        return f"## Sheets in {file_object.name}:\n- " + "\n- ".join(sheet_list)

    def _get_stats_content(self, file_object: FileObject, visible_only: bool) -> str:
        """Get statistics as formatted content"""
        stats = self._get_excel_stats(file_object, visible_only=visible_only)
        return self._format_stats_as_markdown(stats)

    def _get_filtered_content(
        self,
        file_object: FileObject,
        sheet_names: List[str],
        visible_only: bool,
        filter_values: List[str],
        filter_mode: str,
    ) -> str:
        """Get filtered content as markdown"""
        sheets = self._load_excel_file(
            file_object,
            sheet_names=sheet_names,
            visible_only=visible_only,
            filter_values=filter_values,
            filter_mode=filter_mode,
        )
        return self._format_sheets_as_markdown(sheets)

    def _format_sheets_as_markdown(self, sheets: Dict[str, pd.DataFrame]) -> str:
        """Format multiple sheets as markdown tables

        Args:
            sheets: Dictionary of sheet_name -> DataFrame

        Returns:
            Formatted markdown string with all sheets
        """
        content_parts = []
        for sheet_name, df in sheets.items():
            content_parts.append(self._format_dataframe_as_markdown(df, sheet_name))
        return "\n\n".join(content_parts)

    def _get_sheet_by_index_content(
        self,
        file_object: FileObject,
        sheet_index: int,
        visible_only: bool,
        filter_values: Optional[List[str]],
        filter_mode: str,
    ) -> str:
        """Get content for a specific sheet by index

        Args:
            file_object: The FileObject containing Excel file
            sheet_index: Sheet index to retrieve
            visible_only: Whether to only consider visible sheets
            filter_values: List of values to filter rows (AND logic)
            filter_mode: Filter mode (exact or contains)

        Returns:
            Sheet content as string
        """
        sheets = self._load_excel_file(
            file_object,
            visible_only=visible_only,
            filter_values=filter_values,
            filter_mode=filter_mode,
        )
        sheet_names_list = list(sheets.keys())

        if 0 <= sheet_index < len(sheet_names_list):
            sheet_name = sheet_names_list[sheet_index]
            sheet_data = sheets[sheet_name]
            return self._format_dataframe_as_markdown(sheet_data, sheet_name)

        return f"Invalid sheet index: {sheet_index}. Valid range: 0-{len(sheet_names_list)-1}"
