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

from typing import Type, Optional, List
from typing_extensions import TypedDict

from codemie_tools.base.codemie_tool import CodeMieTool
from langchain_core.output_parsers import StrOutputParser
from pydantic import BaseModel, Field

from codemie_tools.base.constants import SOURCE_DOCUMENT_KEY, SOURCE_FIELD_KEY, FILE_CONTENT_FIELD_KEY
from codemie.agents.tools.code.tools_models import (
    ReadFilesInput,
    ReadFilesWithSummaryInput,
)
from codemie.agents.tools.code.tools_vars import READ_FILES_TOOL, READ_FILES_WITH_SUMMARY_TOOL
from codemie.agents.utils import get_repo_files_by_search_phrase_path
from codemie.configs import logger, config
from codemie.core.constants import REQUEST_ID
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.models import CodeFields
from codemie.service.llm_service.llm_service import llm_service
from codemie.templates.coding_prompts import CODE_SUMMARY_PROMPT


class FileResult(TypedDict):
    source: str
    text: str
    file_name: str


def build_chain(llm_model: str, request_id: str):
    llm = get_llm_by_credentials(llm_model=llm_model, request_id=request_id, streaming=False)
    summary_chain = CODE_SUMMARY_PROMPT | llm | StrOutputParser()
    return summary_chain


class BaseReadFileTool(CodeMieTool):
    """Base class for file reading tools with common functionality."""

    code_fields: CodeFields = Field(exclude=True)
    tokens_size_limit: int = config.MAX_CODE_TOOLS_OUTPUT_SIZE
    throw_truncated_error: bool = False

    def format_result(self, result: FileResult) -> str:
        """Format a single result into a string."""
        return (
            f"\n{SOURCE_DOCUMENT_KEY}\n"
            f"{SOURCE_FIELD_KEY}{result.get('source')}\n"
            f"{FILE_CONTENT_FIELD_KEY} \n{result.get('text')}\n"
        )

    def format_results(self, results: List[FileResult]) -> str:
        """Format multiple results into a single string."""
        return "\n".join(self.format_result(result) for result in results)


class ReadFileFromStorageTool(BaseReadFileTool):
    base_name: str = READ_FILES_TOOL.name
    name: str = READ_FILES_TOOL.name
    description: str = READ_FILES_TOOL.description
    args_schema: Optional[Type[BaseModel]] = ReadFilesInput

    def execute(self, file_path: str) -> str:
        results = get_repo_files_by_search_phrase_path(code_fields=self.code_fields, search_phrase=file_path)
        return self.format_results(results)


class ReadFileFromStorageWithSummaryTool(BaseReadFileTool):
    base_name: str = READ_FILES_WITH_SUMMARY_TOOL.name
    name: str = READ_FILES_WITH_SUMMARY_TOOL.name
    description: str = READ_FILES_WITH_SUMMARY_TOOL.description
    args_schema: Optional[Type[BaseModel]] = ReadFilesWithSummaryInput

    def execute(self, file_path: str, summarization_instructions: str, **kwargs) -> str:
        results = get_repo_files_by_search_phrase_path(code_fields=self.code_fields, search_phrase=file_path)
        total_tokens = sum(self.calculate_tokens_count(result.get("text")) for result in results)

        if total_tokens > self.tokens_size_limit:
            logger.info(f"Total tokens {total_tokens} exceed the limit. Summarizing chunks.")
            return self._handle_large_content(results, summarization_instructions)
        return self.format_results(results)

    def _handle_large_content(self, results: List[FileResult], summarization_instructions: str) -> str:
        summaries = self._get_summaries(results, summarization_instructions)
        return "\n".join(
            self.format_result(
                {
                    "source": f"Summary for: {results[0].get('file_name')}",
                    "text": summary,
                    "file_name": results[0].get('file_name'),
                }
            )
            for summary in summaries
        )

    def _get_summaries(self, results: List[FileResult], summarization_instructions: str) -> List[str]:
        sorted_results = sorted(results, key=lambda x: x.get('source', ''))
        summaries = []
        batches = self._create_batches(sorted_results, self.tokens_size_limit)

        llm_model = self.metadata.get("llm_model", llm_service.default_llm_model)
        request_id = self.metadata.get(REQUEST_ID, "")
        summary_chain = build_chain(llm_model, request_id)

        for batch in batches:
            batch_content = self.format_results(batch)
            summary = summary_chain.invoke(
                {
                    "code": batch_content,
                    "task": summarization_instructions,
                    "previous_summaries": "\n\n".join(summaries),
                }
            )
            summaries.append(summary)

        return summaries

    def _create_batches(self, results: List[FileResult], max_tokens: int) -> List[List[FileResult]]:
        batches = []
        current_batch = []
        current_batch_tokens = 0

        for result in results:
            result_tokens = self.calculate_tokens_count(result.get("text"))
            if current_batch_tokens + result_tokens > max_tokens and current_batch:
                batches.append(current_batch)
                current_batch = []
                current_batch_tokens = 0
            current_batch.append(result)
            current_batch_tokens += result_tokens

        if current_batch:  # Add the last batch if it's not empty
            batches.append(current_batch)

        return batches
