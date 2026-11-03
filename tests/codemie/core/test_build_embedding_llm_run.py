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

"""Tests for build_embedding_llm_run helper (Cycle 3)."""

import pytest
from unittest.mock import MagicMock


class _NoProxyEmbeddings:
    """Stand-in for a non-proxy embeddings instance (no consume_last_usage)."""


class TestBuildEmbeddingLLMRun:
    def test_proxy_path_uses_real_tokens_and_cost(self, mocker):
        from codemie.core.utils import build_embedding_llm_run
        from codemie.service.request_summary_manager import LLMRun

        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.cost = 0.000005

        mock_embeddings = MagicMock()
        mock_embeddings.consume_last_usage.return_value = mock_usage

        result = build_embedding_llm_run(mock_embeddings, "test query", "ada-002")

        assert isinstance(result, LLMRun)
        assert result.input_tokens == 100
        assert result.money_spent == pytest.approx(0.000005)
        assert result.output_tokens == 0
        assert result.llm_model == "ada-002"
        mock_embeddings.consume_last_usage.assert_called_once()

    def test_fallback_path_uses_calculated_tokens(self, mocker):
        """Off the LiteLLM path the local price table *is* the ledger, so the estimate stands."""
        from codemie.core.utils import build_embedding_llm_run
        from codemie.service.request_summary_manager import LLMRun

        mock_embeddings = _NoProxyEmbeddings()
        mocker.patch("codemie.core.utils.uses_litellm", return_value=False)

        mocker.patch("codemie.core.utils.llm_service.get_embeddings_model_cost").return_value = MagicMock(input=0.0002)
        mocker.patch("codemie.core.utils.calculate_tokens", return_value=50)

        result = build_embedding_llm_run(mock_embeddings, "test query", "ada-002")

        assert isinstance(result, LLMRun)
        assert result.input_tokens == 50
        assert result.money_spent == pytest.approx(50 * 0.0002)
        assert result.llm_model == "ada-002"

    def test_proxy_usage_none_falls_back_to_calculated(self, mocker):
        from codemie.core.utils import build_embedding_llm_run

        mock_embeddings = MagicMock()
        mock_embeddings.consume_last_usage.return_value = None
        mocker.patch("codemie.core.utils.uses_litellm", return_value=False)

        mocker.patch("codemie.core.utils.llm_service.get_embeddings_model_cost").return_value = MagicMock(input=0.0002)
        mocker.patch("codemie.core.utils.calculate_tokens", return_value=30)

        result = build_embedding_llm_run(mock_embeddings, "test query", "ada-002")

        assert result.input_tokens == 30
        assert result.money_spent == pytest.approx(30 * 0.0002)

    def test_llm_run_has_zero_output_tokens(self, mocker):
        from codemie.core.utils import build_embedding_llm_run

        mock_usage = MagicMock()
        mock_usage.input_tokens = 10
        mock_usage.cost = 0.000001
        mock_embeddings = MagicMock()
        mock_embeddings.consume_last_usage.return_value = mock_usage

        result = build_embedding_llm_run(mock_embeddings, "x", "model")

        assert result.output_tokens == 0

    def test_litellm_embeddings_without_proxy_usage_are_unsettled_not_zero(self, mocker):
        """AD-28: on the LiteLLM path the real cost is in the ledger, so the money is absent.

        The embedding succeeded and was billed, so a zero would be a lie; and LiteLLM already
        priced it, so a local estimate would be a second, disagreeing number (AD-4).
        """
        from codemie.core.utils import build_embedding_llm_run

        mock_embeddings = _NoProxyEmbeddings()
        mocker.patch("codemie.core.utils.uses_litellm", return_value=True)
        mock_model_cost = mocker.patch("codemie.core.utils.llm_service.get_embeddings_model_cost")
        mocker.patch("codemie.core.utils.calculate_tokens", return_value=50)

        result = build_embedding_llm_run(mock_embeddings, "test query", "ada-002")

        assert result.money_spent is None
        assert not isinstance(result.money_spent, float)
        # The price table is never even consulted on this path.
        mock_model_cost.assert_not_called()
        # The token count is real and stays.
        assert result.input_tokens == 50

    def test_uses_litellm_is_asked_about_embeddings_not_chat(self, mocker):
        """AD-2: embeddings route on four terms, chat on three - the class must be explicit."""
        from codemie.core.utils import build_embedding_llm_run
        from codemie.service.spend.routing import LLMCallClass

        mock_uses_litellm = mocker.patch("codemie.core.utils.uses_litellm", return_value=True)
        mocker.patch("codemie.core.utils.calculate_tokens", return_value=1)

        build_embedding_llm_run(_NoProxyEmbeddings(), "q", "ada-002")

        mock_uses_litellm.assert_called_once_with(LLMCallClass.EMBEDDINGS)

    def test_each_call_produces_unique_run_id(self, mocker):
        from codemie.core.utils import build_embedding_llm_run

        mock_usage = MagicMock()
        mock_usage.input_tokens = 10
        mock_usage.cost = 0.000001
        mock_embeddings = MagicMock()
        mock_embeddings.consume_last_usage.return_value = mock_usage

        r1 = build_embedding_llm_run(mock_embeddings, "x", "model")
        r2 = build_embedding_llm_run(mock_embeddings, "x", "model")

        assert r1.run_id != r2.run_id

    def test_run_id_is_the_one_the_embedding_call_carried(self):
        from codemie.core.utils import build_embedding_llm_run

        mock_usage = MagicMock()
        mock_usage.input_tokens = 10
        mock_usage.cost = 0.000001
        mock_embeddings = MagicMock()
        mock_embeddings.consume_last_usage.return_value = mock_usage
        mock_embeddings.codemie_run_id = "run-from-litellm-headers"

        assert build_embedding_llm_run(mock_embeddings, "x", "model").run_id == "run-from-litellm-headers"
