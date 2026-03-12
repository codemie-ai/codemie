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
import warnings
from typing import Optional, List

import pandas as pd
from langchain_core.language_models import BaseChatModel
from langchain_experimental.tools import PythonAstREPLTool

from codemie_tools.base.base_toolkit import BaseToolkit
from codemie_tools.base.file_object import FileObject
from codemie_tools.base.models import ToolKit, ToolSet, Tool
from codemie_tools.file_analysis.csv.tools import CSVTool, get_csv_delimiter
from codemie_tools.file_analysis.docx.tools import DocxTool
from codemie_tools.file_analysis.file_analysis_tool import FileAnalysisTool
from codemie_tools.file_analysis.models import FileAnalysisConfig
from codemie_tools.file_analysis.pdf.tools import PDFTool
from codemie_tools.file_analysis.pptx.tools import PPTXTool
from codemie_tools.file_analysis.tool_vars import (
    FILE_ANALYSIS_TOOL,
    PPTX_TOOL,
    PDF_TOOL,
    CSV_TOOL,
    EXCEL_TOOL,
    DOCX_TOOL,
)
from codemie_tools.file_analysis.xlsx.tools import XlsxTool
from codemie_tools.utils.common import normalize_filename

logger = logging.getLogger(__name__)

# Specialized tool classes for file analysis
# When adding a new specialized tool, add it to this list
SPECIALIZED_TOOL_CLASSES = [
    PDFTool,
    DocxTool,
    PPTXTool,
    XlsxTool,
    CSVTool,
]


class FileAnalysisToolkit(BaseToolkit):
    model_config = {"arbitrary_types_allowed": True}
    files: Optional[List[FileObject]] = None
    chat_model: Optional[BaseChatModel] = None
    warnings_length_limit: int = 30

    @classmethod
    def get_tools_ui_info(cls, *args, **kwargs):
        return ToolKit(
            toolkit=ToolSet.FILE_ANALYSIS,
            tools=[
                Tool.from_metadata(FILE_ANALYSIS_TOOL),
                Tool.from_metadata(PPTX_TOOL),
                Tool.from_metadata(PDF_TOOL),
                Tool.from_metadata(CSV_TOOL),
                Tool.from_metadata(EXCEL_TOOL),
                Tool.from_metadata(DOCX_TOOL),
            ],
        ).model_dump()

    def _process_csv_files_to_tools(self, csv_files):
        """Process CSV files and return appropriate tools.

        Args:
            csv_files: List of FileObject instances representing CSV files

        Returns:
            List of tools for CSV processing
        """
        if not csv_files:
            return []

        tools = []
        dataframes, repl_tool_description = self._pre_process_csv_files(csv_files)

        if dataframes:
            logger.debug(
                f"Initializing PythonAstREPLTool. " f"Locals: {dataframes}. " f"Description: {repl_tool_description}"
            )
            repl_tool = PythonAstREPLTool(locals=dataframes)
            repl_tool.description = repl_tool_description

            # Create config for CSV tool
            csv_config = FileAnalysisConfig(input_files=csv_files, chat_model=self.chat_model)
            tools.extend([repl_tool, CSVTool(config=csv_config)])

        return tools

    def get_tools(self):
        """Get tools for processing files.

        Each tool will automatically filter files by their supported mime types.
        Only tools with matching files will be included to optimize LLM context.

        Returns:
            List of tools for file processing
        """
        if not self.files:
            return []

        tools = []

        # Create config with all files - each tool will filter what it supports
        config = FileAnalysisConfig(input_files=self.files, chat_model=self.chat_model)

        # Use the module-level specialized tool classes + FileAnalysisTool
        tool_classes = SPECIALIZED_TOOL_CLASSES + [FileAnalysisTool]

        # Only add tools that have matching files
        for tool_class in tool_classes:
            tool = tool_class(config=config)
            supported_files = tool._get_supported_files()
            if supported_files:
                # CSV tool needs special handling (REPL tool + CSVTool)
                if tool_class == CSVTool:
                    csv_tools = self._process_csv_files_to_tools(supported_files)
                    tools.extend(csv_tools)
                else:
                    tools.append(tool)
                logger.debug(f"Added {tool_class.__name__} (has matching files)")

        return tools

    @classmethod
    def get_toolkit(cls, files: List[FileObject], chat_model: Optional[BaseChatModel] = None):
        return cls(files=files, chat_model=chat_model)

    def _pre_process_csv_files(self, csv_files):
        """Process CSV files and return dataframes and tool description.

        Args:
            csv_files: List of FileObject instances representing CSV files

        Returns:
            tuple: (dataframes dictionary, repl_tool_description, warnings_string, csv_files)
        """
        # Read all CSV files and combine them into a dictionary of dataframes
        dataframes = {}
        all_warnings = []

        for file_obj in csv_files:
            with warnings.catch_warnings(record=True) as wlist:
                data = FileObject.to_string_content(file_obj.content)
                try:
                    dataframe = pd.read_csv(
                        io.StringIO(data), delimiter=get_csv_delimiter(data, 128), on_bad_lines="warn"
                    )
                    # Store the dataframe with its filename as key (without extension)
                    file_name = file_obj.name
                    dataframes[normalize_filename(file_name)] = dataframe
                    logger.debug(f"Generating dataframe for file {file_name}. Dataframe: {dataframe}")
                    # Collect warnings
                    file_warnings = [str(warning.message) for warning in wlist[: self.warnings_length_limit]]
                    if file_warnings:
                        all_warnings.append(f"Warnings for {file_obj.name}:\n" + "\n".join(file_warnings))
                except Exception as e:
                    all_warnings.append(f"Error processing {file_obj.name}: {str(e)}")
                    continue

        # Combine all warnings
        warnings_string = "\n\n".join(all_warnings) if all_warnings else None

        # Create repl tool description if there are dataframes
        repl_tool_description = None
        if dataframes:
            repl_tool_description = self._generate_csv_prompt_for_multiple_files(dataframes, warnings_string)

        return dataframes, repl_tool_description

    @staticmethod
    def _generate_csv_prompt_for_multiple_files(dataframes: dict, warnings_string: Optional[str] = None):
        warning_section = ""
        if warnings_string:
            warning_section = (
                f"\n**ALWAYS note these warnings if they exist. Warning(s) while reading CSV file(s):**\n"
                f"{warnings_string}\n"
                "These warnings were generated while loading CSV file(s). "
                "They may indicate malformed rows, missing values, or other data issues. "
                "Please take them into account when analyzing the data.\n"
            )

        dataframe_names = ', '.join(dataframes.keys())
        return f"""CSV file(s) named '{dataframe_names}' have been uploaded by the user,
            and they have already been loaded into a Pandas DataFrame. Dataframe names correspond to filename.
            {warning_section}
             - You may ask clarifying questions if something is unclear.
             - In your explanations or final answers, refer to the CSV by its respective file name from the list
                '{dataframe_names}'.
            Remember:
            1) The DataFrame variable is correspond to file name.
            2) The file names are '{dataframe_names}'.
            """
