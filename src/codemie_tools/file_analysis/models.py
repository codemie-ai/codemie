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

"""Configuration models for file analysis tools."""

from typing import Optional

from langchain_core.language_models import BaseChatModel
from pydantic import Field

from codemie_tools.base.models import CodeMieToolConfig, FileConfigMixin


class FileAnalysisConfig(CodeMieToolConfig, FileConfigMixin):
    """
    Unified configuration for all file analysis tools.

    This config supports file operations for PDF, DOCX, PPTX, Excel, CSV, and other file types.
    Files are provided via the input_files attribute inherited from FileConfigMixin.

    All file analysis tools (PDFTool, DocxTool, PPTXTool, XlsxTool, CSVTool, FileAnalysisTool)
    share this single configuration class.

    Attributes:
        input_files: List of FileObject instances (inherited from FileConfigMixin)
        chat_model: Optional language model for AI-powered operations (OCR, analysis, etc.)
    """

    chat_model: Optional[BaseChatModel] = Field(default=None, exclude=True)
