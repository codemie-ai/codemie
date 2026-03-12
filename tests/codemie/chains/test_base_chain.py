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

from langchain_core.messages import HumanMessage, AIMessage

from codemie.chains.base import BaseChain, StreamingChain
from codemie.core.models import ChatMessage
from codemie.core.constants import ChatRole


class TestBaseChain:
    def test_generate(self):
        chain = BaseChain()
        assert chain.generate() is None

    def test_transform_history(self):
        messages = [
            ChatMessage(role=ChatRole.USER, message="Hello"),
            ChatMessage(role=ChatRole.ASSISTANT, message="Hello"),
        ]

        result = BaseChain._transform_history(messages)

        assert isinstance(result[0], HumanMessage)
        assert isinstance(result[1], AIMessage)


class TestStreamingChain:
    def test_stream(self):
        chain = StreamingChain()
        assert chain.stream() is None
