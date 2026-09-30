# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""MarkItDown worker for multiprocessing."""

import io
import logging
import os

from markitdown import MarkItDown, StreamInfo

from codemie_tools.file_analysis.workers.utf8_safe_plain_text_converter import Utf8SafePlainTextConverter
from codemie_tools.file_analysis.workers.xlsx_workers import process_xlsx_to_markdown

logger = logging.getLogger(__name__)


def convert_file_to_markdown(file_bytes: bytes, file_name: str, llm_client=None, llm_model=None) -> str:
    """
    Convert file to markdown using MarkItDown.

    Args:
        file_bytes: File content as bytes
        file_name: Name of file (for logging/errors)
        llm_client: Optional LLM client for MarkItDown
        llm_model: Optional LLM model name

    Returns:
        Markdown text content
    """
    try:
        ext = os.path.splitext(file_name or "")[1].lower()
        if ext == ".xlsb":
            return process_xlsx_to_markdown(file_bytes, sheet_names=None, visible_only=False, file_ext=".xlsb")

        md = MarkItDown(
            enable_builtins=True,
            llm_client=llm_client,
            # llm_model=llm_model,
        )
        # Register before the built-in PlainTextConverter (priority 10) so UTF-8 Cyrillic
        # content is handled correctly when charset detection reads only the first 4 KiB
        # and incorrectly returns 'ascii' for files with non-ASCII bytes past that boundary.
        md.register_converter(Utf8SafePlainTextConverter(), priority=9)
        binary_content = io.BytesIO(file_bytes)

        result = md.convert(binary_content, stream_info=StreamInfo(filename=file_name, extension=ext or None))
        return result.text_content
    except Exception as e:
        logger.error(f"MarkItDown conversion failed for {file_name}: {e}")
        raise
