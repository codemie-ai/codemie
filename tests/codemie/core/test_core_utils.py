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

import markdown
from pygments import highlight
from pygments.lexers import JsonLexer
from pygments.formatters import HtmlFormatter

import io
import json
import zipfile
from typing import Dict

from codemie.core.utils import (
    calculate_token_cost,
    extract_text_from_llm_output,
    format_json_content,
    format_markdown_content,
    generate_zip,
    sanitize_string,
    unpack_json_strings,
)
from codemie.configs.llm_config import CostConfig


@pytest.mark.parametrize(
    "input_str, expected",
    [
        ("password: secret123", "password: ***"),
        ("pwd=mypass", "pwd=***"),
        ("pass : 1234", "pass : ***"),
        ("password:abc password:def", "password:*** password:***"),
    ],
)
def test_password_sanitization(input_str, expected):
    assert sanitize_string(input_str) == expected


@pytest.mark.parametrize(
    "input_str, expected",
    [
        ("username: admin", "username: ***"),
        ("user=john_doe", "user=***"),
        ("uname : alice", "uname : ***"),
    ],
)
def test_username_sanitization(input_str, expected):
    assert sanitize_string(input_str) == expected


@pytest.mark.parametrize(
    "input_str, expected",
    [
        ("IP: 192.168.1.1", "IP: [IP_ADDRESS]"),
        ("Server at 10.0.0.1 is down", "Server at [IP_ADDRESS] is down"),
    ],
)
def test_ip_address_sanitization(input_str, expected):
    assert sanitize_string(input_str) == expected


@pytest.mark.parametrize(
    "input_str, expected",
    [
        ("Email: user@example.com", "Email: [EMAIL]"),
        ("Contact: admin@test.co.uk", "Contact: [EMAIL]"),
        ("Invalid email: user@.com", "Invalid email: user@.com"),  # Should not match
    ],
)
def test_email_sanitization(input_str, expected):
    assert sanitize_string(input_str) == expected


@pytest.mark.parametrize(
    "input_str, expected",
    [
        ("api_key: abcd1234", "api_key: [API_KEY]"),
        ("access_token=xyz789", "access_token=[API_KEY]"),
    ],
)
def test_api_key_sanitization(input_str, expected):
    assert sanitize_string(input_str) == expected


@pytest.mark.parametrize(
    "input_str, expected",
    [
        ("CC: 1234 5678 9012 3456", "CC: [CREDIT_CARD]"),
        ("Card: 1234-5678-9012-3456", "Card: [CREDIT_CARD]"),
        ("Invalid: 123 456 789", "Invalid: 123 456 789"),  # Should not match
    ],
)
def test_credit_card_sanitization(input_str, expected):
    assert sanitize_string(input_str) == expected


def test_multiple_patterns():
    input_str = (
        "User: admin Pass: secret IP: 192.168.1.1 Email: user@example.com api_key: key123 CC: 1234-5678-9012-3456"
    )
    expected = "User: *** Pass: *** IP: [IP_ADDRESS] Email: [EMAIL] api_key: [API_KEY] CC: [CREDIT_CARD]"
    assert sanitize_string(input_str) == expected


@pytest.mark.parametrize(
    "input_str, expected",
    [
        ("PASSWORD: Secret123", "PASSWORD: ***"),
        ("Email: USER@EXAMPLE.COM", "Email: [EMAIL]"),
    ],
)
def test_case_insensitivity(input_str, expected):
    assert sanitize_string(input_str) == expected


def test_no_sensitive_data():
    input_str = "This is a normal string without any sensitive information."
    assert sanitize_string(input_str) == input_str


def test_string_input():
    """Test when input is a simple string."""
    assert extract_text_from_llm_output("hello") == "hello"
    assert extract_text_from_llm_output("") == ""


def test_list_of_dicts():
    """Test when input is a list containing dictionaries."""
    # Test with valid dictionary containing 'text' key
    assert extract_text_from_llm_output([{"text": "hello"}]) == "hello"

    # Test with dictionary missing 'text' key
    assert extract_text_from_llm_output([{"other": "value"}]) == ""

    # Test with multiple dictionaries - should only use first one
    assert extract_text_from_llm_output([{"text": "first"}, {"text": "second"}]) == "first"


def test_empty_list():
    """Test when input is an empty list."""
    assert extract_text_from_llm_output([]) == ""


def test_other_types():
    """Test when input is of other types."""
    # Test with number
    assert extract_text_from_llm_output(123) == "123"

    # Test with None
    assert extract_text_from_llm_output(None) == "None"

    # Test with boolean
    assert extract_text_from_llm_output(True) == "True"

    # Test with list of non-dictionaries
    assert extract_text_from_llm_output([1, 2, 3]) == "[1, 2, 3]"


def test_nested_structures():
    """Test when input has nested structures."""
    nested_dict = {"a": {"b": "c"}}
    assert extract_text_from_llm_output(nested_dict) == "{'a': {'b': 'c'}}"


def test_mixed_list():
    """Test when input is a list with mixed types."""
    mixed_list = [1, "two", {"three": 3}]
    assert extract_text_from_llm_output(mixed_list) == "[1, 'two', {'three': 3}]"


def test_generate_zip() -> None:
    files: Dict[str, str] = {
        "file1.txt": "This is the content of file 1.",
        "file2.txt": "This is the content of file 2.",
        "file3.txt": "This is the content of file 3.",
    }

    zip_content = b"".join(generate_zip(files))

    with zipfile.ZipFile(io.BytesIO(zip_content), 'r') as zip_file:
        assert set(zip_file.namelist()) == set(files.keys())

        for filename, expected_content in files.items():
            with zip_file.open(filename) as file:
                assert file.read().decode() == expected_content


def test_format_json_content():
    json_content = {"key": "value"}
    highlighted_json = format_json_content(json_content)

    assert isinstance(highlighted_json, str)
    assert highlight(json.dumps(json_content, indent=4), JsonLexer(), HtmlFormatter()) == highlighted_json


def test_format_markdown_content():
    markdown_text = "# Heading\n\nThis is a paragraph."
    html_content = format_markdown_content(markdown_text)

    assert isinstance(html_content, str)
    assert markdown.markdown(markdown_text) == html_content


@pytest.mark.parametrize(
    "input_string, expected_output",
    [
        # Valid JSON objects should be parsed
        ('{"nested": {"inner": "value"}}', {"nested": {"inner": "value"}}),
        ('{"empty": {}}', {"empty": {}}),
        # Valid JSON arrays should be parsed
        ('[[1, 2], [3, 4]]', [[1, 2], [3, 4]]),
        ('[{"id": 1}, {"id": 2}]', [{"id": 1}, {"id": 2}]),
        ('[]', []),
        # Primitive JSON values should remain as strings (not parsed)
        ('42', '42'),
        ('null', 'null'),
        # Non-string, non-dict, non-list values should pass through unchanged
        (3.14, 3.14),
        (True, True),
        (None, None),
        # Non-JSON strings should remain unchanged
        ('not json', 'not json'),
        ('{invalid json}', '{invalid json}'),
        ('"[incomplete', '"[incomplete'),
        ('', ''),
        # Malformed JSON should remain as strings
        ('{"key": }', '{"key": }'),
        ('{{"nested": "broken"', '{{"nested": "broken"'),
    ],
)
def test_unpack_json_strings_string_input(input_string, expected_output):
    """Test unpack_json_strings with various string inputs."""
    result = unpack_json_strings(input_string)
    assert result == expected_output
    assert type(result) is type(expected_output)


@pytest.mark.parametrize(
    "input_dict, expected_dict",
    [
        # Mixed dictionary with JSON and non-JSON strings
        ({"json": '["hello"]', "text": "not json", "num": 42}, {"json": ["hello"], "text": "not json", "num": 42}),
        # Dictionary with empty and malformed JSON
        ({"empty": "", "broken": "{invalid}", "valid": '[]'}, {"empty": "", "broken": "{invalid}", "valid": []}),
        # Nested dictionary processing
        ({"outer": '{"inner": "[1, 2]"}'}, {"outer": {"inner": [1, 2]}}),
        # Dictionary with non-string values
        (
            {"string": '[1]', "number": 42, "boolean": True, "none": None},
            {"string": [1], "number": 42, "boolean": True, "none": None},
        ),
        # Empty dictionary
        ({}, {}),
        # Mixed list with JSON and non-JSON strings
        (['["hello"]', 'not json', 42, True], [['hello'], 'not json', 42, True]),
        # List with malformed JSON
        (['[]', '{broken}', 'text'], [[], '{broken}', 'text']),
        # Empty list
        ([], []),
        # Nested list processing
        (['["[1, 2]"]'], [[[1, 2]]]),
        # List with various data types
        (['["json string"]', None, 42, True, "regular string"], [["json string"], None, 42, True, "regular string"]),
    ],
)
def test_unpack_json_strings_dict_input(input_dict, expected_dict):
    """Test unpack_json_strings with dictionary inputs."""
    result = unpack_json_strings(input_dict)
    assert result == expected_dict


@pytest.mark.parametrize(
    "input_structure, expected_structure",
    [
        # Complex nested structure from the docstring example
        ({'a': '[1, 2, 3]', 'b': '{"c": "d"}', 'e': 'not json'}, {'a': [1, 2, 3], 'b': {'c': 'd'}, 'e': 'not json'}),
        # Mixed complex structure
        (
            {
                'users': '[{"name": "Alice", "data": "{\\"age\\": 25}"}, {"name": "Bob"}]',
                'meta': '{"count": "2", "active": "true"}',
                'config': 'not json data',
            },
            {
                'users': [{'name': 'Alice', 'data': {'age': 25}}, {'name': 'Bob'}],
                'meta': {'count': '2', 'active': 'true'},
                'config': 'not json data',
            },
        ),
        # List containing dictionaries with JSON strings
        (
            [
                {'data': '["item1", "item2"]', 'meta': '{"type": "list"}'},
                'plain string',
                {'nested': '{"deep": "{\\"value\\": 42}"}'},
            ],
            [
                {'data': ['item1', 'item2'], 'meta': {'type': 'list'}},
                'plain string',
                {'nested': {'deep': {'value': 42}}},
            ],
        ),
    ],
)
def test_unpack_json_strings_complex_structures(input_structure, expected_structure):
    """Test unpack_json_strings with complex nested structures."""
    result = unpack_json_strings(input_structure)
    assert result == expected_structure


def test_unpack_json_strings_edge_cases():
    """Test edge cases and special scenarios for unpack_json_strings."""

    # Test with whitespace in JSON
    result = unpack_json_strings({'data': ' { "key" : "value" } '})
    assert result == {'data': {'key': 'value'}}

    # Test with unicode characters
    result = unpack_json_strings({'unicode': '{"message": "Hello 🌍"}'})
    assert result == {'unicode': {'message': 'Hello 🌍'}}

    # Test JSON string containing escaped quotes
    result = unpack_json_strings({'escaped': '{"quote": "He said \\"Hello\\""}'})
    assert result == {'escaped': {'quote': 'He said "Hello"'}}


@pytest.mark.parametrize(
    "input_tokens, output_tokens, cached_tokens, input_cost, output_cost, cache_cost, expected_total, expected_cached",
    [
        # No caching
        (1000, 500, 0, 0.000003, 0.000015, 0.0000003, 0.0105, 0.0),
        # With caching - Claude 3.7 rates
        (1000, 500, 800, 0.000003, 0.000015, 0.0000003, 0.00834, 0.00024),
        # All tokens cached
        (1000, 0, 1000, 0.000003, 0.000015, 0.0000003, 0.0003, 0.0003),
        # No cache cost configured (legacy model) - prompt: (1000-100)*0.000003=0.0027, output: 500*0.000015=0.0075
        (1000, 500, 100, 0.000003, 0.000015, None, 0.0102, 0.0),
        # GPT-4.1 rates with caching - prompt: (2000-500)*0.0000005=0.00075, output: 1000*0.0000015=0.0015, cache: 500*0.00000025=0.000125
        (2000, 1000, 500, 0.0000005, 0.0000015, 0.00000025, 0.002375, 0.000125),
    ],
)
def test_calculate_token_cost_with_caching(
    input_tokens, output_tokens, cached_tokens, input_cost, output_cost, cache_cost, expected_total, expected_cached
):
    """Test calculate_token_cost returns correct tuple (total_cost, cached_cost, cache_creation_cost)."""
    cost_config = CostConfig(
        input=input_cost,
        output=output_cost,
        cache_read_input_token_cost=cache_cost,
    )

    total_cost, cached_cost, cache_creation_cost = calculate_token_cost(
        llm_model="test-model",
        cost_config=cost_config,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
    )

    assert abs(total_cost - expected_total) < 0.000001, f"Expected total {expected_total}, got {total_cost}"
    assert abs(cached_cost - expected_cached) < 0.000001, f"Expected cached {expected_cached}, got {cached_cost}"
    assert cache_creation_cost == 0.0, f"Expected cache_creation_cost 0.0, got {cache_creation_cost}"


def test_calculate_token_cost_with_batch_costs():
    """Test calculate_token_cost includes batch costs in total but not in cached cost."""
    cost_config = CostConfig(
        input=0.000003,
        output=0.000015,
        input_cost_per_token_batches=0.0000001,
        output_cost_per_token_batches=0.0000002,
        cache_read_input_token_cost=0.0000003,
    )

    total_cost, cached_cost, cache_creation_cost = calculate_token_cost(
        llm_model="claude-3-7",
        cost_config=cost_config,
        input_tokens=1000,
        output_tokens=500,
        cached_tokens=800,
    )

    # Prompt tokens = 1000 - 800 = 200
    # Prompt cost = 200 * (0.000003 + 0.0000001) = 0.00062
    # Output cost = 500 * (0.000015 + 0.0000002) = 0.0076
    # Cached cost = 800 * 0.0000003 = 0.00024
    # Total = 0.00062 + 0.0076 + 0.00024 = 0.00846
    expected_total = 0.00846
    expected_cached = 0.00024

    assert abs(total_cost - expected_total) < 0.000001
    assert abs(cached_cost - expected_cached) < 0.000001


def test_calculate_token_cost_zero_tokens():
    """Test calculate_token_cost with zero tokens."""
    cost_config = CostConfig(
        input=0.000003,
        output=0.000015,
        cache_read_input_token_cost=0.0000003,
    )

    total_cost, cached_cost, cache_creation_cost = calculate_token_cost(
        llm_model="test-model",
        cost_config=cost_config,
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
    )

    assert total_cost == 0.0
    assert cached_cost == 0.0
    assert cache_creation_cost == 0.0


def test_calculate_token_cost_returns_tuple():
    """Test that calculate_token_cost returns a tuple, not a single value."""
    cost_config = CostConfig(
        input=0.000003,
        output=0.000015,
        cache_read_input_token_cost=0.0000003,
    )

    result = calculate_token_cost(
        llm_model="test-model",
        cost_config=cost_config,
        input_tokens=100,
        output_tokens=50,
        cached_tokens=10,
    )

    assert isinstance(result, tuple)
    assert len(result) == 3
    assert isinstance(result[0], float)  # total_cost
    assert isinstance(result[1], float)  # cached_tokens_cost
    assert isinstance(result[2], float)  # cache_creation_tokens_cost
