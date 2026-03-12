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

import pytest
from unittest.mock import Mock

from codemie.chains.base import StreamedGenerationResult
from codemie.agents.callbacks.chain_streaming_callback import ChainStreamingCallback


@pytest.fixture
def mock_generator():
    """Create a mock generator with send method"""
    return Mock()


@pytest.fixture
def callback(mock_generator):
    """Create a ChainStreamingCallback instance with mock generator"""
    return ChainStreamingCallback(gen=mock_generator)


def test_initialization(callback, mock_generator):
    """Test proper initialization of ChainStreamingCallback"""
    assert callback.gen == mock_generator


def test_on_llm_new_token_simple(callback):
    """Test handling of simple token without special characters"""
    token = "Hello"
    callback.on_llm_new_token(token)

    expected_result = StreamedGenerationResult(generated_chunk="Hello").model_dump_json()
    callback.gen.send.assert_called_once_with(expected_result)


def test_on_llm_new_token_with_curly_braces(callback):
    """Test handling of token containing adjacent curly braces"""
    token = "}{test}{"
    callback.on_llm_new_token(token)

    expected_result = StreamedGenerationResult(generated_chunk="}_{test}_{").model_dump_json()
    callback.gen.send.assert_called_once_with(expected_result)


def test_escape_message_simple():
    """Test _escape_message method with simple text"""
    callback = ChainStreamingCallback(gen=Mock())
    result = callback._escape_message("Hello world")
    assert result == "Hello world"


def test_escape_message_with_curly_braces():
    """Test _escape_message method with text containing adjacent curly braces"""
    callback = ChainStreamingCallback(gen=Mock())
    result = callback._escape_message("test}{more}{test")
    assert result == "test}_{more}_{test"


def test_on_llm_new_token_empty(callback):
    """Test handling of empty token"""
    token = ""
    callback.on_llm_new_token(token)

    expected_result = StreamedGenerationResult(generated_chunk="").model_dump_json()
    callback.gen.send.assert_called_once_with(expected_result)


def test_on_llm_new_token_special_characters(callback):
    """Test handling of token with special characters"""
    token = "Hello\n\t\r"
    callback.on_llm_new_token(token)

    expected_result = StreamedGenerationResult(generated_chunk="Hello\n\t\r").model_dump_json()
    callback.gen.send.assert_called_once_with(expected_result)


@pytest.mark.parametrize(
    "input_token,expected_output",
    [
        ("normal text", "normal text"),
        ("}{", "}_{"),
        ("test}{test}{test", "test}_{test}_{test"),
        ("}{}{}{", "}_{}_{}_{"),
        ("", ""),
        ("test\n}{\ntest", "test\n}_{\ntest"),
    ],
)
def test_escape_message_parametrized(input_token, expected_output):
    """Test _escape_message method with various input patterns"""
    callback = ChainStreamingCallback(gen=Mock())
    result = callback._escape_message(input_token)
    assert result == expected_output


def test_multiple_tokens(callback):
    """Test handling multiple tokens in sequence"""
    tokens = ["Hello", "}{", "World"]
    expected_results = [
        StreamedGenerationResult(generated_chunk="Hello").model_dump_json(),
        StreamedGenerationResult(generated_chunk="}_{").model_dump_json(),
        StreamedGenerationResult(generated_chunk="World").model_dump_json(),
    ]

    for token, expected in zip(tokens, expected_results):
        callback.on_llm_new_token(token)
        callback.gen.send.assert_called_with(expected)

    assert callback.gen.send.call_count == 3
