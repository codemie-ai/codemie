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

from typing import Iterator
from unittest.mock import Mock, patch

import pytest
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.runnables import Runnable

from codemie.chains import PureChatChain
from codemie.core.models import AssistantChatRequest
from codemie.core.thread import ThreadedGenerator
from codemie.service.llm_service.llm_service import LLMService


class TestPureChatChainStream:
    @pytest.fixture
    def mock_thread_generator(self):
        return Mock(spec=ThreadedGenerator)

    @pytest.fixture
    def base_request(self):
        return AssistantChatRequest(text="Test question", history=[], file_name=None, system_prompt="")

    @pytest.fixture
    def mock_runnable(self):
        mock = Mock(spec=Runnable)
        # Setup the mock to return a streaming mock by default
        stream_mock = Mock()
        mock.with_config.return_value = stream_mock
        return mock

    @pytest.fixture
    def chat_chain(self, mock_thread_generator, base_request, mock_runnable):
        with patch('codemie.chains.pure_chat_chain.PureChatChain._build_chain') as mock_build:
            mock_build.return_value = mock_runnable
            from codemie.chains.pure_chat_chain import PureChatChain

            chain = PureChatChain(
                request=base_request,
                system_prompt="You are a helpful assistant",
                llm_model=LLMService.BASE_NAME_GPT_41_MINI,
                llm=Mock(spec=Runnable),
                thread_generator=mock_thread_generator,
            )
            return chain

    def test_successful_stream(self, base_request, mock_thread_generator, mock_runnable):
        # Setup mock stream response
        chunks = ["Hello", " World", "!"]
        with patch('codemie.chains.pure_chat_chain.PureChatChain._build_chain') as mock_build:
            mock_build.stream.return_value = self.create_chunk_iterator(chunks)

            chain = PureChatChain(
                request=base_request,
                system_prompt="You are a helpful assistant",
                llm_model=LLMService.BASE_NAME_GPT_41,
                llm=Mock(spec=Runnable),
                thread_generator=mock_thread_generator,
            )

            # Execute the stream method
            chain.stream()

            mock_thread_generator.send.assert_called_once()
            mock_thread_generator.close.assert_called_once()

    def test_multiple_chunks_handling(self, base_request, mock_thread_generator, mock_runnable):
        chunks = ["Hello", " ", "World", "!", " ", "How", " ", "are", " ", "you", "?"]

        with patch('codemie.chains.pure_chat_chain.PureChatChain._build_chain') as mock_build:
            mock_build.stream.return_value = self.create_chunk_iterator(chunks)

            chain = PureChatChain(
                request=base_request,
                system_prompt="You are a helpful assistant",
                llm_model=LLMService.BASE_NAME_GPT_41,
                llm=Mock(spec=Runnable),
                thread_generator=mock_thread_generator,
            )

            # Execute the stream method
            chain.stream()

            mock_thread_generator.send.assert_called_once()
            mock_thread_generator.close.assert_called_once()

    def test_thread_generator_closure(self, base_request, mock_thread_generator, mock_runnable):
        chunks = ["Test"]

        with patch('codemie.chains.pure_chat_chain.PureChatChain._build_chain') as mock_build:
            mock_build.stream.return_value = self.create_chunk_iterator(chunks)

            chain = PureChatChain(
                request=base_request,
                system_prompt="You are a helpful assistant",
                llm_model=LLMService.BASE_NAME_GPT_41,
                llm=Mock(spec=Runnable),
                thread_generator=mock_thread_generator,
            )
            chain.stream()
            mock_thread_generator.close.assert_called_once()

    def create_chunk_iterator(self, texts: list[str]) -> Iterator[ChatGenerationChunk]:
        for text in texts:
            if text is not None:  # Skip None values
                yield ChatGenerationChunk(text=text)
