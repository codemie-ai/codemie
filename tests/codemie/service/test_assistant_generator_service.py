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

"""
Tests for AssistantGeneratorService._is_recommendation_same_as_original method.
"""

import pytest

from codemie.service.assistant_generator_service import AssistantGeneratorService


@pytest.mark.parametrize(
    "original,recommended,expected",
    [
        # Both None - should be equal
        (None, None, True),
        # One is None, other is not - should not be equal
        (None, "value", False),
        ("value", None, False),
        # String comparisons
        ("same value", "same value", True),
        ("different", "values", False),
        ("", "", True),
        ("", "value", False),
        # List comparisons - order independent
        (["a", "b", "c"], ["a", "b", "c"], True),
        (["a", "b", "c"], ["c", "b", "a"], True),  # Different order, same content
        (["a", "b", "c"], ["a", "b"], False),  # Different length
        (["a", "b"], ["a", "b", "c"], False),  # Different length
        (["a", "b", "c"], ["a", "b", "d"], False),  # Different content
        ([], [], True),
        ([], ["a"], False),
        # List with duplicates
        (["a", "a", "b"], ["a", "b", "a"], True),  # Same elements with duplicates
        (["a", "a", "b"], ["a", "b"], False),  # Different count of duplicates
        # Integer comparisons
        (42, 42, True),
        (42, 43, False),
        (0, 0, True),
        # Boolean comparisons
        (True, True, True),
        (False, False, True),
        (True, False, False),
        # Mixed types - should not be equal
        ("123", 123, False),
        (["1", "2"], "1,2", False),
        # Empty list vs None
        ([], None, False),
        (None, [], False),
        # List of integers
        ([1, 2, 3], [3, 2, 1], True),
        ([1, 2, 3], [1, 2, 4], False),
        # List of mixed types (int and str) - not sortable, falls back to direct comparison
        ([1, "a", 2], [1, "a", 2], True),  # Same order
        ([1, "a", 2], [2, "a", 1], False),  # Different order - not sortable, so not equal
        # Complex nested structures (should compare as-is if not sortable)
        ([{"a": 1}, {"b": 2}], [{"a": 1}, {"b": 2}], True),
        ([{"a": 1}, {"b": 2}], [{"b": 2}, {"a": 1}], False),  # Dicts are not sortable by default
    ],
)
def test_is_recommendation_same_as_original(original, recommended, expected):
    """
    Test _is_recommendation_same_as_original with various input combinations.

    Args:
        original: Original value from user's assistant draft
        recommended: Recommended value from LLM
        expected: Expected boolean result
    """
    result = AssistantGeneratorService._is_recommendation_same_as_original(original, recommended)
    assert result == expected, f"Failed for original={original}, recommended={recommended}"


def test_is_recommendation_same_as_original_with_non_sortable_list():
    """
    Test that non-sortable lists fall back to direct comparison.
    This tests the TypeError exception handling.
    """

    # Create lists with non-comparable custom objects
    class CustomObj:
        def __init__(self, value):
            self.value = value

        def __eq__(self, other):
            return isinstance(other, CustomObj) and self.value == other.value

    obj1 = CustomObj(1)
    obj2 = CustomObj(2)

    # Same order should be equal
    original = [obj1, obj2]
    recommended = [obj1, obj2]
    assert AssistantGeneratorService._is_recommendation_same_as_original(original, recommended) is True

    # Different order should not be equal (since sorting fails and direct comparison is used)
    original = [obj1, obj2]
    recommended = [obj2, obj1]
    assert AssistantGeneratorService._is_recommendation_same_as_original(original, recommended) is False


def test_is_recommendation_same_as_original_string_with_whitespace():
    """Test that string comparison is exact and does not strip whitespace."""
    assert AssistantGeneratorService._is_recommendation_same_as_original("  value  ", "  value  ") is True
    assert AssistantGeneratorService._is_recommendation_same_as_original("  value  ", "value") is False
    assert AssistantGeneratorService._is_recommendation_same_as_original("value", "value ") is False


def test_is_recommendation_same_as_original_list_of_strings_case_sensitive():
    """Test that list comparison is case-sensitive for strings."""
    assert AssistantGeneratorService._is_recommendation_same_as_original(["ABC", "def"], ["def", "ABC"]) is True
    assert AssistantGeneratorService._is_recommendation_same_as_original(["ABC", "def"], ["abc", "def"]) is False
