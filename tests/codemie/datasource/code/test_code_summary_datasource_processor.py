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

import unittest
from unittest.mock import MagicMock, patch

from langchain_core.documents import Document

from codemie.core.models import GitRepo
from codemie.datasource.code.code_summary_datasource_processor import (
    CodeSummaryDatasourceProcessor,
    CodeChunkSummaryDatasourceProcessor,
)
from codemie.datasource.code.code_summary_datasource_prompt import (
    CUSTOM_SUMMARY_TEMPLATE_SUFFIX,
    FILE_SUMMARY_PROMPT,
    CHUNK_SUMMARY_PROMPT,
)
from codemie.rest_api.security.user import User


class TestCodeSummaryDatasourceProcessor(unittest.TestCase):
    def test_process_chunk(self):
        mock_repo = MagicMock(spec=GitRepo, prompt=None)
        mock_document = Document("This is a test document.")
        mock_repo.name = "TestRepo"
        indexer = CodeSummaryDatasourceProcessor(repo=mock_repo, user=User(id="id", name="name", username="username"))
        with patch('codemie.datasource.code.code_summary_datasource_processor.LLMChain.predict') as mock_llm_predict:
            mock_llm_predict.return_value = "summarized content"
            mock_index = MagicMock()
            mock_index.docs_generation = False
            indexer.index = mock_index

            chunk = "This is a chunk of text."
            metadata = {"file_name": "test.py", "file_path": "/path/to/test.py", "source": "source1"}

            result_doc = indexer._process_chunk(chunk, metadata, mock_document)

            self.assertEqual(result_doc.page_content, "summarized content")
            self.assertEqual(result_doc.metadata["source"], "source1")
            mock_llm_predict.assert_called_once_with(fileName="test.py", fileContents=chunk)

    def test_get_summary_prompt(self):
        repo = MagicMock(spec=GitRepo)
        repo.prompt = 'Document template'

        with patch(
            'codemie.datasource.code.code_summary_datasource_processor.PromptTemplate.from_template'
        ) as mock_template:
            mock_template.return_value = "Mocked Prompt"
            prompt = CodeSummaryDatasourceProcessor.get_summary_prompt(repo)
            expected_prompt = 'Document template' + CUSTOM_SUMMARY_TEMPLATE_SUFFIX
            mock_template.assert_called_once_with(expected_prompt)
            assert prompt == "Mocked Prompt"

    def test_get_summary_prompt_no_prompt(self):
        repo = MagicMock(spec=GitRepo, prompt=None)

        prompt = CodeSummaryDatasourceProcessor.get_summary_prompt(repo)
        assert prompt == FILE_SUMMARY_PROMPT


class TestChunkSummaryDatasourceProcessor(unittest.TestCase):
    def test_get_summary_prompt_no_prompt(self):
        repo = MagicMock(spec=GitRepo, prompt=None)

        prompt = CodeChunkSummaryDatasourceProcessor.get_summary_prompt(repo)
        self.assertEqual(prompt, CHUNK_SUMMARY_PROMPT)

    def test_get_summary_prompt(self):
        repo = MagicMock(spec=GitRepo)
        repo.prompt = 'Document template'

        with patch(
            'codemie.datasource.code.code_summary_datasource_processor.PromptTemplate.from_template'
        ) as mock_template:
            mock_template.return_value = "Mocked Prompt"
            prompt = CodeChunkSummaryDatasourceProcessor.get_summary_prompt(repo)
            expected_prompt = repo.prompt
            mock_template.assert_called_once_with(expected_prompt)
            self.assertEqual(prompt, "Mocked Prompt")
