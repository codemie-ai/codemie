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

from unittest.mock import MagicMock, patch

import pytest
from codemie.chains.kb_sources_selector_chain import KBSourcesSelectorChain
from codemie.service.llm_service.llm_service import llm_service


class TestKBSourcesSelectorChain:
    @pytest.fixture
    def chain_instance(self):
        return KBSourcesSelectorChain(
            query="What is the capital of France?",
            sources=["Wikipedia", "Britannica"],
            llm_model=llm_service.default_llm_model,
            request_id="123",
        )

    def test_initialization(self, chain_instance):
        assert isinstance(chain_instance, KBSourcesSelectorChain)
        assert chain_instance.query == "What is the capital of France?"
        assert chain_instance.sources == ["Wikipedia", "Britannica"]
        assert chain_instance.llm_model == llm_service.default_llm_model
        assert chain_instance.request_id == "123"

    @patch('codemie.chains.kb_sources_selector_chain.KBSourcesSelectorChain._chain')
    def test_generate(self, chain, chain_instance):
        chain_instance._chain = MagicMock()
        chain_instance._chain.return_value = MagicMock(invoke=MagicMock(return_value=" Wikipedia,   Britannica "))

        assert chain_instance.generate() == {"Wikipedia", "Britannica"}
