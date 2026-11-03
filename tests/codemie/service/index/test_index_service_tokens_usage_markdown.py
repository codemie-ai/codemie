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

from unittest.mock import MagicMock

from codemie.core.models import TokensUsage
from codemie.service.index.index_service import IndexStatusService


def _datasource_with(tokens_usage: TokensUsage) -> MagicMock:
    datasource = MagicMock()
    datasource.tokens_usage = tokens_usage
    return datasource


def test_get_tokens_usage_markdown_settled_renders_plain_figure():
    datasource = _datasource_with(TokensUsage(input_tokens=10, output_tokens=20, money_spent=1.23))

    result = IndexStatusService.get_tokens_usage_markdown(datasource)

    assert "#### Money spent: $1.23\n" in result


def test_get_tokens_usage_markdown_pending_says_awaiting_final_cost():
    datasource = _datasource_with(
        TokensUsage(
            input_tokens=10,
            output_tokens=20,
            money_spent=0.0,
            money_spent_pending=True,
        )
    )

    result = IndexStatusService.get_tokens_usage_markdown(datasource)

    assert "#### Money spent: awaiting final cost\n" in result
    assert "$0.0" not in result


def test_get_tokens_usage_markdown_unavailable_says_final_cost_unavailable():
    datasource = _datasource_with(
        TokensUsage(
            input_tokens=10,
            output_tokens=20,
            money_spent=0.0,
            money_spent_unavailable=True,
        )
    )

    result = IndexStatusService.get_tokens_usage_markdown(datasource)

    assert "#### Money spent: final cost unavailable\n" in result
    assert "$0.0" not in result
