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
from typing import BinaryIO, Any

import markitdown
from markitdown import StreamInfo, DocumentConverterResult

from codemie_tools.file_analysis.xlsx.processor import XlsxProcessor

logger = logging.getLogger(__name__)


class XlsxConverter(markitdown.converters.XlsxConverter):
    """
    Converts XLSX files to Markdown, with each sheet presented as a separate Markdown table.
    Customized to filter out empty rows and columns (NaN values) to reduce markdown size and improve readability.
    Can filter sheets based on visibility.
    """

    def __init__(self, sheet_names: list[str] = None, visible_only: bool = True) -> None:
        super().__init__()
        self.processor = XlsxProcessor(sheet_names=sheet_names, visible_only=visible_only)
        logger.info("Enable custom XLSX converter")

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,  # Options to pass to the converter
    ) -> DocumentConverterResult:
        """
        Converts Excel files to markdown tables, filtering out empty rows and columns to reduce output size.
        If visible_only is True, only visible sheets will be processed.
        """

        # Use the processor to load and clean Excel data
        sheets_clean = self.processor.load(file_stream, clean_data=True)

        # Use the processor to convert the sheets to markdown
        md_content = self.processor.convert(sheets_clean, **kwargs)

        return DocumentConverterResult(markdown=md_content)
