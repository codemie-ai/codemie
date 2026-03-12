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
from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    SystemMessage,
    ToolMessage,
    FunctionMessage,
)

from codemie.workflows.utils.guardrail_replacement_utils import (
    extract_message_texts,
    update_message_texts,
    _collect_texts,
    _replace_texts,
    _ReplacementCursor,
)


class TestExtractMessageTexts:
    """Test suite for extract_message_texts function."""

    def test_simple_human_message(self):
        """Test extracting text from a simple HumanMessage."""
        msg = HumanMessage(content="What's the weather like in New York today?")
        texts = extract_message_texts(msg)
        assert texts == ["What's the weather like in New York today?"]

    def test_human_message_with_additional_kwargs(self):
        """Test HumanMessage with additional_kwargs containing text."""
        msg = HumanMessage(
            content="Hello",
            additional_kwargs={
                "locale": "en-US",
                "custom_metadata": {"request_id": "abc123", "message": "metadata text"},
            },
        )
        texts = extract_message_texts(msg)
        assert texts == ["Hello", "en-US", "abc123", "metadata text"]

    def test_multimodal_human_message(self):
        """Test HumanMessage with multimodal content (text + image)."""
        msg = HumanMessage(
            content=[
                {"type": "text", "text": "Can you caption this image?"},
                {"type": "image_url", "image_url": "https://example.com/cat.png"},
            ]
        )
        texts = extract_message_texts(msg)
        # ("type", "text") and ("type", "image_url") are skipped as structural metadata
        assert texts == ["Can you caption this image?", "https://example.com/cat.png"]

    def test_simple_ai_message(self):
        """Test extracting text from a simple AIMessage."""
        msg = AIMessage(
            content="The weather in New York is sunny with a high of 75°F.",
            additional_kwargs={"completion_tokens": 42, "model": "gpt-4o"},
        )
        texts = extract_message_texts(msg)
        assert texts == ["The weather in New York is sunny with a high of 75°F.", "gpt-4o"]

    def test_ai_message_with_tool_calls(self):
        """Test AIMessage with tool_calls containing text arguments."""
        msg = AIMessage(
            content="",
            tool_calls=[{"name": "get_weather", "args": {"location": "New York"}, "id": "call_1"}],
            additional_kwargs={"function_call": {"name": "get_weather", "arguments": '{"location": "New York"}'}},
        )
        texts = extract_message_texts(msg)
        # Expected order: content, tool_calls (name, location, id), additional_kwargs (name, arguments)
        assert texts == [
            "",
            "get_weather",
            "New York",
            "call_1",
            "get_weather",
            '{"location": "New York"}',
        ]

    def test_system_message(self):
        """Test extracting text from SystemMessage."""
        msg = SystemMessage(
            content="You are a travel assistant. Always reply in JSON.",
            additional_kwargs={"timestamp": "2024-07-01T12:34:56Z"},
        )
        texts = extract_message_texts(msg)
        assert texts == ["You are a travel assistant. Always reply in JSON.", "2024-07-01T12:34:56Z"]

    def test_tool_message_string_content(self):
        """Test ToolMessage with string content."""
        msg = ToolMessage(
            content='Weather data: {"temp": 74, "condition": "Sunny"}',
            name="get_weather",
            tool_call_id="call_1",
            additional_kwargs={"execution_time_ms": 128},
        )
        texts = extract_message_texts(msg)
        assert texts == ['Weather data: {"temp": 74, "condition": "Sunny"}']

    def test_function_message(self):
        """Test extracting text from FunctionMessage."""
        msg = FunctionMessage(name="lookup_user", content='{"user_id": 42, "name": "Ada Lovelace"}')
        texts = extract_message_texts(msg)
        assert texts == ['{"user_id": 42, "name": "Ada Lovelace"}']

    def test_message_with_metadata(self):
        """Test message with metadata containing text."""
        msg = HumanMessage(
            content="Test message",
            metadata={"source": "web", "user_message": "Original input", "nested": {"text": "Nested text"}},
        )
        texts = extract_message_texts(msg)
        assert texts == ["Test message", "web", "Original input", "Nested text"]

    def test_deeply_nested_structure(self):
        """Test message with deeply nested text structures."""
        msg = HumanMessage(
            content="Root content",
            additional_kwargs={
                "level1": {
                    "text": "Level 1 text",
                    "level2": {
                        "message": "Level 2 message",
                        "level3": {"value": "Level 3 value", "data": [{"text": "List item text"}]},
                    },
                }
            },
        )
        texts = extract_message_texts(msg)
        assert texts == ["Root content", "Level 1 text", "Level 2 message", "Level 3 value", "List item text"]

    def test_message_with_multiple_text_keys_in_dict(self):
        """Test dict with multiple TEXT_KEYS - should collect all."""
        msg = HumanMessage(
            content="Main",
            additional_kwargs={"block": {"text": "Text value", "message": "Message value"}},
        )
        texts = extract_message_texts(msg)
        assert texts == ["Main", "Text value", "Message value"]

    def test_empty_message(self):
        """Test message with empty content."""
        msg = HumanMessage(content="")
        texts = extract_message_texts(msg)
        assert texts == [""]

    def test_message_with_list_content(self):
        """Test message with list of mixed content types."""
        msg = HumanMessage(
            content=[
                "Plain string",
                {"type": "text", "text": "Dict text"},
                {"type": "image_url", "url": "http://example.com"},
                {"message": "Another text key"},
            ]
        )
        texts = extract_message_texts(msg)
        # ("type", "text") and ("type", "image_url") are skipped
        assert texts == [
            "Plain string",
            "Dict text",
            "http://example.com",
            "Another text key",
        ]

    def test_structural_type_audio(self):
        """Test that (type, audio) is skipped."""
        msg = HumanMessage(content=[{"type": "audio", "url": "http://example.com/audio.mp3"}])
        texts = extract_message_texts(msg)
        assert texts == ["http://example.com/audio.mp3"]

    def test_structural_type_video(self):
        """Test that (type, video) is skipped."""
        msg = HumanMessage(content=[{"type": "video", "url": "http://example.com/video.mp4"}])
        texts = extract_message_texts(msg)
        assert texts == ["http://example.com/video.mp4"]

    def test_non_structural_type_value(self):
        """Test that type with non-structural value is NOT skipped."""
        msg = HumanMessage(
            content="Main",
            additional_kwargs={"user_type": "premium", "account_type": "business"},
        )
        texts = extract_message_texts(msg)
        # "premium" and "business" should be collected since they're not in SKIP_KEY_VALUE_PAIRS
        assert texts == ["Main", "premium", "business"]


class TestUpdateMessageTexts:
    """Test suite for update_message_texts function."""

    def test_update_simple_content(self):
        """Test updating simple string content."""
        msg = HumanMessage(content="Original text")
        new_texts = ["Guardrailed text"]
        result = update_message_texts(msg, new_texts)
        assert result.content == "Guardrailed text"

    def test_update_multimodal_content(self):
        """Test updating multimodal content preserving structure."""
        msg = HumanMessage(
            content=[
                {"type": "text", "text": "Original caption"},
                {"type": "image_url", "image_url": "https://example.com/cat.png"},
            ]
        )
        # ("type", "text") and ("type", "image_url") are preserved, only actual content is replaced
        new_texts = ["Updated caption", "https://updated.example.com/cat.png"]
        result = update_message_texts(msg, new_texts)

        expected_content = [
            {"type": "text", "text": "Updated caption"},
            {"type": "image_url", "image_url": "https://updated.example.com/cat.png"},
        ]
        assert result.content == expected_content

    def test_update_with_additional_kwargs(self):
        """Test updating text in additional_kwargs."""
        msg = HumanMessage(
            content="Main content",
            additional_kwargs={"custom_metadata": {"message": "Original metadata"}},
        )
        new_texts = ["New main", "New metadata"]
        result = update_message_texts(msg, new_texts)

        assert result.content == "New main"
        assert result.additional_kwargs == {"custom_metadata": {"message": "New metadata"}}

    def test_update_tool_calls(self):
        """Test updating text in tool_calls."""
        msg = AIMessage(
            content="Calling tool",
            tool_calls=[{"name": "get_weather", "args": {"location": "New York"}, "id": "call_1"}],
        )
        new_texts = ["Updated call", "get_weather", "Los Angeles", "call_1"]
        result = update_message_texts(msg, new_texts)

        assert result.content == "Updated call"
        assert result.tool_calls == [
            {"name": "get_weather", "args": {"location": "Los Angeles"}, "id": "call_1", "type": "tool_call"}
        ]

    def test_update_metadata(self):
        """Test updating text in metadata."""
        msg = HumanMessage(content="Content", metadata={"source": "web", "user_message": "Original"})
        new_texts = ["New content", "mobile", "Updated"]
        result = update_message_texts(msg, new_texts)

        assert result.content == "New content"
        assert result.metadata == {"source": "mobile", "user_message": "Updated"}  # type: ignore

    def test_update_deeply_nested(self):
        """Test updating deeply nested text structures."""
        msg = HumanMessage(
            content="Root",
            additional_kwargs={"level1": {"text": "L1", "level2": {"message": "L2", "level3": {"value": "L3"}}}},
        )
        new_texts = ["New root", "New L1", "New L2", "New L3"]
        result = update_message_texts(msg, new_texts)

        assert result.content == "New root"
        expected_kwargs = {"level1": {"text": "New L1", "level2": {"message": "New L2", "level3": {"value": "New L3"}}}}
        assert result.additional_kwargs == expected_kwargs

    def test_update_preserves_non_text_fields(self):
        """Test that non-text fields are preserved during update."""
        msg = AIMessage(
            content="Text",
            additional_kwargs={"completion_tokens": 42, "model": "gpt-4o", "message": "Msg"},
        )
        new_texts = ["New text", "gpt-4o", "New msg"]
        result = update_message_texts(msg, new_texts)

        assert result.content == "New text"
        expected_kwargs = {"completion_tokens": 42, "model": "gpt-4o", "message": "New msg"}
        assert result.additional_kwargs == expected_kwargs

    def test_update_with_wrong_count_raises_error(self):
        """Test that providing wrong number of replacement texts raises error."""
        msg = HumanMessage(content="Text 1", additional_kwargs={"message": "Text 2"})
        new_texts = ["Only one"]  # Need 2
        with pytest.raises(ValueError, match="Not enough replacement strings supplied."):
            update_message_texts(msg, new_texts)

    def test_update_with_empty_string(self):
        """Test updating message with empty text content."""
        msg = HumanMessage(content="", additional_kwargs={"count": 42})
        new_texts = [""]
        result = update_message_texts(msg, new_texts)

        assert result.content == ""
        assert result.additional_kwargs == {"count": 42}

    def test_update_list_content_with_strings(self):
        """Test updating list content containing plain strings."""
        msg = HumanMessage(content=["String 1", "String 2", {"text": "Dict text"}])
        new_texts = ["New 1", "New 2", "New dict"]
        result = update_message_texts(msg, new_texts)

        expected_content = ["New 1", "New 2", {"text": "New dict"}]
        assert result.content == expected_content

    def test_update_preserves_structural_pairs(self):
        """Test that structural key-value pairs are preserved during update."""
        msg = HumanMessage(
            content=[
                {"type": "text", "text": "Original"},
                {"type": "image", "url": "http://old.com"},
            ]
        )
        new_texts = ["Updated", "http://new.com"]
        result = update_message_texts(msg, new_texts)

        expected_content = [
            {"type": "text", "text": "Updated"},
            {"type": "image", "url": "http://new.com"},
        ]
        assert result.content == expected_content


class TestCollectTexts:
    """Test suite for _collect_texts helper function."""

    def test_collect_from_none(self):
        """Test collecting from None returns empty."""
        out = []
        _collect_texts(None, out)
        assert out == []

    def test_collect_from_string(self):
        """Test collecting from a simple string."""
        out = []
        _collect_texts("Hello world", out)
        assert out == ["Hello world"]

    def test_collect_from_list_of_strings(self):
        """Test collecting from list of strings."""
        out = []
        _collect_texts(["Text 1", "Text 2", "Text 3"], out)
        assert out == ["Text 1", "Text 2", "Text 3"]

    def test_collect_from_dict_with_text_key(self):
        """Test collecting from dict with 'text' key."""
        out = []
        _collect_texts({"type": "text", "text": "Content here"}, out)
        # ("type", "text") is skipped, but "text": "Content here" is collected
        assert out == ["Content here"]

    def test_collect_from_dict_with_message_key(self):
        """Test collecting from dict with 'message' key."""
        out = []
        _collect_texts({"status": "ok", "message": "Success message"}, out)
        assert out == ["ok", "Success message"]

    def test_collect_from_dict_without_text_keys(self):
        """Test collecting from dict without TEXT_KEYS - should recurse into all values."""
        out = []
        _collect_texts({"metadata": {"text": "Nested text"}, "count": 42}, out)
        assert out == ["Nested text"]

    def test_collect_from_nested_lists(self):
        """Test collecting from nested lists."""
        out = []
        _collect_texts([["Level 1"], [["Level 2"]], "Top level"], out)
        assert out == ["Level 1", "Level 2", "Top level"]

    def test_collect_from_mixed_structure(self):
        """Test collecting from complex mixed structure."""
        out = []
        data = {
            "content": "Main",
            "extras": [{"text": "Extra 1"}, "String extra", {"nested": {"message": "Deep"}}],
        }
        _collect_texts(data, out)
        assert out == ["Main", "Extra 1", "String extra", "Deep"]

    def test_collect_skips_structural_pairs(self):
        """Test that structural key-value pairs are skipped."""
        out = []
        _collect_texts({"type": "text", "text": "Content", "id": "abc123"}, out)
        # ("type", "text") is skipped
        assert out == ["Content", "abc123"]

    def test_collect_multiple_text_keys_in_same_dict(self):
        """Test collecting when dict has multiple values."""
        out = []
        _collect_texts({"text": "Text value", "message": "Message value"}, out)
        assert out == ["Text value", "Message value"]

    def test_collect_non_structural_type(self):
        """Test that type with non-structural value is collected."""
        out = []
        _collect_texts({"type": "premium", "name": "John"}, out)
        # "premium" is not in SKIP_KEY_VALUE_PAIRS, so it should be collected
        assert out == ["premium", "John"]

    def test_collect_tool_call_function(self):
        """Test that (tool_call, function) is skipped."""
        out = []
        _collect_texts({"tool_call": "function", "name": "get_data"}, out)
        # ("tool_call", "function") is skipped
        assert out == ["get_data"]


class TestReplaceTexts:
    """Test suite for _replace_texts helper function."""

    def test_replace_none(self):
        """Test replacing None returns None."""
        cursor = _ReplacementCursor(["New"])
        result = _replace_texts(None, cursor)
        assert result is None

    def test_replace_string(self):
        """Test replacing a string."""
        cursor = _ReplacementCursor(["Replacement"])
        result = _replace_texts("Original", cursor)
        assert result == "Replacement"

    def test_replace_list_of_strings(self):
        """Test replacing list of strings."""
        cursor = _ReplacementCursor(["New 1", "New 2"])
        result = _replace_texts(["Old 1", "Old 2"], cursor)
        assert result == ["New 1", "New 2"]

    def test_replace_dict_with_text_key(self):
        """Test replacing values while preserving structural pairs."""
        cursor = _ReplacementCursor(["New content"])
        result = _replace_texts({"type": "text", "text": "Old content"}, cursor)
        # ("type", "text") is preserved, "text": "Old content" is replaced
        assert result == {"type": "text", "text": "New content"}

    def test_replace_dict_without_text_keys(self):
        """Test replacing dict - should recurse into all values."""
        cursor = _ReplacementCursor(["New nested"])
        result = _replace_texts({"metadata": {"text": "Old nested"}, "count": 42}, cursor)
        assert result == {"metadata": {"text": "New nested"}, "count": 42}

    def test_replace_nested_structure(self):
        """Test replacing complex nested structure."""
        cursor = _ReplacementCursor(["New 1", "New 2", "New 3"])
        data = {"level1": {"text": "Old 1"}, "list": [{"message": "Old 2"}], "direct": "Old 3"}
        result = _replace_texts(data, cursor)

        expected = {"level1": {"text": "New 1"}, "list": [{"message": "New 2"}], "direct": "New 3"}
        assert result == expected

    def test_replace_preserves_structural_pairs(self):
        """Test that structural pairs are preserved."""
        cursor = _ReplacementCursor(["http://new.com", "New text"])
        data = {"type": "image", "url": "http://old.com", "text": "Old text"}
        result = _replace_texts(data, cursor)
        # ("type", "image") is preserved
        expected = {"type": "image", "url": "http://new.com", "text": "New text"}
        assert result == expected

    def test_replace_returns_copy_of_dict(self):
        """Test that replacement returns a new dict, not modifying original."""
        cursor = _ReplacementCursor(["New"])
        original = {"text": "Original"}
        result = _replace_texts(original, cursor)

        assert result is not original
        assert original == {"text": "Original"}  # Original unchanged
        assert result == {"text": "New"}

    def test_replace_non_string_types(self):
        """Test replacing structure with non-string types (numbers, booleans)."""
        cursor = _ReplacementCursor([])
        data = {"count": 42, "active": True, "ratio": 3.14}
        result = _replace_texts(data, cursor)
        assert result == {"count": 42, "active": True, "ratio": 3.14}

    def test_replace_tool_call_function_preserved(self):
        """Test that (tool_call, function) is preserved."""
        cursor = _ReplacementCursor(["new_get_data"])
        data = {"tool_call": "function", "name": "get_data"}
        result = _replace_texts(data, cursor)
        # ("tool_call", "function") is preserved
        assert result == {"tool_call": "function", "name": "new_get_data"}


class TestReplacementCursor:
    """Test suite for _ReplacementCursor helper class."""

    def test_cursor_initialization(self):
        """Test cursor initialization."""
        cursor = _ReplacementCursor(["A", "B", "C"])
        assert cursor.total == 3
        assert cursor.used == 0

    def test_cursor_next(self):
        """Test cursor.next() returns items in order."""
        cursor = _ReplacementCursor(["First", "Second", "Third"])
        assert cursor.next() == "First"
        assert cursor.used == 1
        assert cursor.next() == "Second"
        assert cursor.used == 2
        assert cursor.next() == "Third"
        assert cursor.used == 3

    def test_cursor_exhausted_raises_error(self):
        """Test that calling next() when exhausted raises error."""
        cursor = _ReplacementCursor(["Only one"])
        cursor.next()
        with pytest.raises(ValueError, match="Not enough replacement strings"):
            cursor.next()

    def test_assert_drained_success(self):
        """Test assert_drained succeeds when all items consumed."""
        cursor = _ReplacementCursor(["A", "B"])
        cursor.next()
        cursor.next()
        cursor.assert_drained()  # Should not raise

    def test_assert_drained_fails_when_unused(self):
        """Test assert_drained fails when items remain."""
        cursor = _ReplacementCursor(["A", "B", "C"])
        cursor.next()
        with pytest.raises(ValueError, match="Replacement strings were not all used"):
            cursor.assert_drained()

    def test_cursor_with_empty_list(self):
        """Test cursor with empty replacement list."""
        cursor = _ReplacementCursor([])
        assert cursor.total == 0
        assert cursor.used == 0
        cursor.assert_drained()  # Should succeed


class TestEdgeCases:
    """Test edge cases and special scenarios."""

    def test_message_with_all_attributes(self):
        """Test message with content, tool_calls, additional_kwargs, and metadata."""
        msg = AIMessage(
            content="Main content",
            tool_calls=[{"name": "tool", "args": {"param": "Value"}, "id": "call_1"}],
            additional_kwargs={"metadata": {"message": "Extra"}},
            metadata={"source": {"text": "Origin"}},
        )
        texts = extract_message_texts(msg)
        new_texts = ["New " + t for t in texts]
        result = update_message_texts(msg, new_texts)

        assert result.content == "New Main content"
        assert result.tool_calls == [
            {"name": "New tool", "args": {"param": "New Value"}, "id": "New call_1", "type": "tool_call"}
        ]
        assert result.additional_kwargs == {"metadata": {"message": "New Extra"}}
        assert result.metadata == {"source": {"text": "New Origin"}}

    def test_unicode_and_special_characters(self):
        """Test handling of unicode and special characters."""
        msg = HumanMessage(content="Hello 世界 🌍", additional_kwargs={"message": "Emoji: 🎉, Math: ∑∫"})
        texts = extract_message_texts(msg)
        new_texts = ["Updated " + t for t in texts]
        result = update_message_texts(msg, new_texts)

        assert result.content == "Updated Hello 世界 🌍"
        assert result.additional_kwargs == {"message": "Updated Emoji: 🎉, Math: ∑∫"}

    def test_very_long_text(self):
        """Test handling of very long text content."""
        long_text = "A" * 10000
        msg = HumanMessage(content=long_text)
        texts = extract_message_texts(msg)
        assert texts == [long_text]
        new_texts = ["B" * 10000]
        result = update_message_texts(msg, new_texts)
        assert result.content == "B" * 10000

    def test_empty_strings_in_structure(self):
        """Test handling of empty strings."""
        msg = HumanMessage(content="", additional_kwargs={"text": "", "nested": {"message": ""}})
        texts = extract_message_texts(msg)
        assert texts == ["", "", ""]
        new_texts = ["Non-empty 1", "Non-empty 2", "Non-empty 3"]
        result = update_message_texts(msg, new_texts)

        assert result.content == "Non-empty 1"
        expected_kwargs = {"text": "Non-empty 2", "nested": {"message": "Non-empty 3"}}
        assert result.additional_kwargs == expected_kwargs

    def test_whitespace_only_text(self):
        """Test handling of whitespace-only text."""
        msg = HumanMessage(content="   \n\t  ", additional_kwargs={"message": "  "})
        texts = extract_message_texts(msg)
        assert texts == ["   \n\t  ", "  "]

    def test_numeric_string_values(self):
        """Test handling of numeric strings."""
        msg = HumanMessage(content="42", additional_kwargs={"value": "3.14", "message": "100"})
        texts = extract_message_texts(msg)
        assert texts == ["42", "3.14", "100"]

    def test_circular_reference_prevention(self):
        """Test that deeply nested structures don't cause infinite loops."""
        # Create a very deep nesting (but not circular, as dicts are immutable in this context)
        deep_data = {"text": "Level 0"}
        current = deep_data
        for i in range(100):
            current["nested"] = {"text": f"Level {i + 1}"}
            current = current["nested"]

        msg = HumanMessage(content="Root", additional_kwargs=deep_data)
        texts = extract_message_texts(msg)
        # Should successfully extract without hanging
        assert texts[0] == "Root"
        assert texts[1] == "Level 0"
        assert texts[-1] == "Level 100"
        assert len(texts) == 102  # Root + 101 levels

    def test_message_with_output_text_key(self):
        """Test extraction of 'output_text' key."""
        msg = HumanMessage(content="Input", additional_kwargs={"result": {"output_text": "Generated output"}})
        texts = extract_message_texts(msg)
        assert texts == ["Input", "Generated output"]

    def test_message_with_input_text_key(self):
        """Test extraction of 'input_text' key."""
        msg = AIMessage(
            content="Response",
            additional_kwargs={"request": {"input_text": "User provided input"}},
        )
        texts = extract_message_texts(msg)
        assert texts == ["Response", "User provided input"]

    def test_multiple_messages_roundtrip(self):
        """Test extract -> modify -> update roundtrip with multiple message types."""
        messages = [
            HumanMessage(content="Human input"),
            AIMessage(content="AI response", tool_calls=[{"name": "tool", "args": {"text": "Arg"}, "id": "call_1"}]),
            SystemMessage(content="System instruction"),
            ToolMessage(content="Tool output", name="tool", tool_call_id="call_1"),
        ]

        for msg in messages:
            texts = extract_message_texts(msg)
            new_texts = [f"GUARDRAILED: {t}" for t in texts]
            result = update_message_texts(msg, new_texts)

            # Verify all texts were prefixed
            result_texts = extract_message_texts(result)
            assert all(rt.startswith("GUARDRAILED: ") for rt in result_texts)
            assert len(result_texts) == len(texts)

    def test_mixed_structural_and_content_types(self):
        """Test message with both structural and content type fields."""
        msg = HumanMessage(
            content=[
                {"type": "text", "text": "Content"},  # Structural type
                {"user_type": "premium", "name": "John"},  # Non-structural type
            ]
        )
        texts = extract_message_texts(msg)
        # ("type", "text") is skipped, but "user_type": "premium" is collected
        assert texts == ["Content", "premium", "John"]

    def test_all_structural_types_skipped(self):
        """Test that all structural type values are properly skipped."""
        msg = HumanMessage(
            content=[
                {"type": "text", "data": "Text data"},
                {"type": "image", "data": "Image data"},
                {"type": "image_url", "data": "Image URL data"},
                {"type": "audio", "data": "Audio data"},
                {"type": "video", "data": "Video data"},
                {"type": "tool_call", "data": "Tool call data"},
            ]
        )
        texts = extract_message_texts(msg)
        # All ("type", <structural_value>) pairs should be skipped
        assert texts == [
            "Text data",
            "Image data",
            "Image URL data",
            "Audio data",
            "Video data",
            "Tool call data",
        ]
