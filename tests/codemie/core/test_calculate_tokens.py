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

"""calculate_tokens treats tiktoken special-token text as plain text."""

from codemie_tools.base.utils import get_encoding

from codemie.core.utils import calculate_tokens
from codemie.service.llm_service.llm_service import llm_service

MARKER = "<|endoftext|>"


def test_calculate_tokens_counts_endoftext_marker():
    assert calculate_tokens(MARKER) > 0


def test_calculate_tokens_counts_marker_inside_text():
    assert calculate_tokens(f"a {MARKER} b") > calculate_tokens("a  b")


def test_calculate_tokens_ordinary_text_matches_encoder():
    text = "hello world, this is ordinary text"
    expected = len(get_encoding(llm_service.default_llm_model).encode(text))

    assert calculate_tokens(text) == expected
