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

from enum import Enum
from typing import Optional, Type, Any, Union, Dict, List

from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.file_tool_mixin import FileToolMixin
from codemie_tools.file_analysis.models import FileAnalysisConfig
from codemie_tools.file_analysis.pptx.processor import PptxProcessor
from codemie_tools.file_analysis.tool_vars import PPTX_TOOL


class QueryType(str, Enum):
    TEXT = "Text"
    TEXT_WITH_METADATA = "Text_with_Metadata"
    TOTAL_SLIDES = "Total_Slides"


class PPTXToolInput(BaseModel):
    """
    Defines the schema for the arguments required by PPTXTool.
    """

    slides: list[int] = Field(
        description=(
            "List of slide numbers of a PPTX document to process. "
            "Must be empty to process all slides in a single request. "
            "Slide numbers are 1-based."
        ),
    )
    query: QueryType = Field(
        ...,
        description=(
            "'Text' if the tool must return the Markdown representation of the PPTX slides. "
            "'Text_with_Metadata' if the tool must return the JSON representation of the "
            "PPTX slides with metadata. Preferred if detailed information is needed. "
            "'Total_Slides' if the tool must return the total number of slides in the PPTX "
            "document."
        ),
    )


class PPTXTool(CodeMieTool, FileToolMixin):
    args_schema: Type[BaseModel] = PPTXToolInput

    name: str = PPTX_TOOL.name
    label: str = PPTX_TOOL.label
    description: str = PPTX_TOOL.description

    config: FileAnalysisConfig
    pptx_processor: Optional[PptxProcessor] = None

    def __init__(self, config: FileAnalysisConfig) -> None:
        """
        Initialize the PPTXTool with configuration containing PPTX files.

        Args:
            config: FileAnalysisConfig with input_files and optional chat_model
        """
        super().__init__(config=config)
        self.pptx_processor = PptxProcessor(chat_model=config.chat_model)

    def _get_supported_mime_types(self) -> Optional[List[str]]:
        """
        Get list of supported mime types for PPTX processing.

        Returns:
            List of PPTX mime types
        """
        return ['application/vnd.openxmlformats-officedocument.presentationml.presentation']

    def _get_supported_extensions(self) -> Optional[List[str]]:
        """
        Get list of supported file extensions for PPTX processing.

        Returns:
            List of PPTX file extensions
        """
        return ['.pptx']

    def execute(self, slides: List[int], query: QueryType) -> Union[str, Dict[str, Any]]:
        """
        Process the PPTX documents based on the provided query and slides.

        Args:
            slides (List[int]): A list of 1-based slide numbers to process.
                               If empty, the entire document is processed.
            query (str): The query or action to perform:
                - "Total_Slides" to return the total number of slides.
                - "Text" to return the text representation of the PPTX as markdown.
                - "Text_with_Metadata" to return the PPTX data as structured JSON.

        Returns:
            str | dict: A string representation of the requested data or a dictionary with structured results.
        """
        # Get supported PPTX files from config (automatically filters by mime type)
        files = self._get_supported_files()

        if not files:
            raise ValueError(f"{self.name} requires at least one file to process.")

        if query == QueryType.TOTAL_SLIDES:
            return self.pptx_processor.get_total_slides_from_files(files)
        elif query == QueryType.TEXT:
            return self.pptx_processor.process_pptx_files(files, slides)
        elif query == QueryType.TEXT_WITH_METADATA:
            # For metadata, we need to get the structured dictionary data
            pptx_document = self.pptx_processor.open_pptx_document(files[0].content)
            return self.pptx_processor.extract_text_as_json(pptx_document, slides)
