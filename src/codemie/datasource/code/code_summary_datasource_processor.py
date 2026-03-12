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

from typing import Callable, Optional

from langchain.chains.llm import LLMChain
from langchain_core.documents import Document
from langchain_core.prompts import PromptTemplate

from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.models import GitRepo
from codemie.datasource.code.code_datasource_processor import CodeDatasourceProcessor
from codemie.datasource.code.code_summary_datasource_prompt import (
    FILE_SUMMARY_PROMPT,
    CUSTOM_SUMMARY_TEMPLATE_SUFFIX,
    CHUNK_SUMMARY_PROMPT,
    summarized_chunk_content,
)
from codemie.datasource.code.docs_generation_service import DocsGenService
from codemie.datasource.datasources_config import CODE_CONFIG
from codemie.service.llm_service.llm_service import llm_service


class CodeSummaryDatasourceProcessor(CodeDatasourceProcessor):
    docs_gen_service: Optional[DocsGenService] = None
    llm_name: Optional[str] = None

    @property
    def _processing_batch_size(self) -> int:
        return CODE_CONFIG.summarization_batch_size

    def _on_process_start(self):
        self.llm_name = llm_service.get_llm_deployment_name(self.repo.summarization_model)
        if self.index.docs_generation:
            self.docs_gen_service = DocsGenService()
        super()._on_process_start()

    def _on_process_end(self):
        super()._on_process_end()
        if self.index.docs_generation:
            self.docs_gen_service.recursively_generate_readmes(
                index=self.index, llm_name=self.llm_name, request_uuid=self.request_uuid
            )
            self.docs_gen_service.push_documentation(repo=self.repo, index=self.index)

    def _process_chunk(self, chunk: str, chunk_metadata, _document: Document) -> Document:
        prompt = self.get_summary_prompt(repo=self.repo)
        llm = get_llm_by_credentials(llm_model=self.llm_name, request_id=self.request_uuid)
        llm_chain = LLMChain(llm=llm, prompt=prompt)
        summarization_result = llm_chain.predict(fileName=chunk_metadata["file_name"], fileContents=chunk)
        processor = self._get_summary_processor()
        page_content = processor(chunk, summarization_result)
        result = Document(page_content=page_content, metadata=chunk_metadata)
        if self.index.docs_generation:
            self.docs_gen_service.generate_docs_per_file(result, self.index)
        return result

    def _get_summary_processor(self) -> Callable[[str, str], str]:
        return lambda chunk, summarization: summarization

    @classmethod
    def get_summary_prompt(cls, repo: GitRepo):
        if not repo.prompt:
            return FILE_SUMMARY_PROMPT
        docs_prompt = repo.prompt + CUSTOM_SUMMARY_TEMPLATE_SUFFIX
        return PromptTemplate.from_template(docs_prompt)


class CodeChunkSummaryDatasourceProcessor(CodeSummaryDatasourceProcessor):
    """
    A class to process and summarize code chunks from a Git repository.
    """

    def _get_summary_processor(self):
        """
        Returns a lambda function that formats the summarization result with the chunk content based on template.

        :return: A lambda function for processing the summarization result.
        """
        return lambda chunk, summarization: summarized_chunk_content.format(code=chunk, summarization=summarization)

    @classmethod
    def get_summary_prompt(cls, repo: GitRepo):
        """
        Retrieves the summary prompt template for the given repository.

        :param repo: The Git repository object.
        :return: The prompt template for summarizing code chunks.
        """
        if not repo.prompt:
            return CHUNK_SUMMARY_PROMPT
        return PromptTemplate.from_template(repo.prompt)
