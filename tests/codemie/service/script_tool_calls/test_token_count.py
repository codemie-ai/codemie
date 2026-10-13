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

"""Token counting of the text a script run exchanges."""

from __future__ import annotations

import pytest

from codemie.core.utils import calculate_tokens
from codemie.service.script_tool_calls.token_count import count_tokens


def test_empty_text_has_no_tokens() -> None:
    assert count_tokens("") == 0


def test_it_counts_the_same_as_the_platform_does_for_a_direct_call() -> None:
    text = '{"result": {"items": [1, 2, 3], "note": "привет"}}'

    assert count_tokens(text, "gpt-4.1-mini") == calculate_tokens(text, "gpt-4.1-mini")


@pytest.mark.parametrize("model", [None, "", "no-such-model"])
def test_a_missing_or_unknown_model_still_counts(model: str | None) -> None:
    assert count_tokens("hello world", model) > 0


def test_special_token_text_is_counted_not_rejected() -> None:
    assert count_tokens("<|endoftext|>") > 0
