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

"""Unit tests for memory_utils module."""

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from codemie.workflows.memory_utils import _create_message_batches


class TestCreateMessageBatches:
    """Test suite for the _create_message_batches function."""

    @pytest.fixture
    def mock_calculate_tokens(self):
        """Mock the calculate_tokens function to return predictable values."""
        with patch('codemie.workflows.memory_utils.calculate_tokens') as mock:
            # Default behavior: Extract content from message string representation
            # Message strings look like: "content='Hello' additional_kwargs={} ..."
            # We want to count tokens based on content length, not full representation
            def calculate_from_content(text: str) -> int:
                # Extract content from string representation
                if "content='" in text or 'content="' in text:
                    # Find the content portion
                    import re

                    match = re.search(r"content=['\"]([^'\"]*)['\"]", text)
                    if match:
                        content = match.group(1)
                        return len(content)
                # Fallback for non-message strings
                return len(text)

            mock.side_effect = calculate_from_content
            yield mock

    # Basic Functionality Tests

    def test_empty_messages_list(self, mock_calculate_tokens):
        """Test that empty messages list returns empty batches."""
        result = _create_message_batches([], max_tokens=100)
        assert result == []

    def test_single_message_within_limit(self, mock_calculate_tokens):
        """Test single message that fits within token limit."""
        messages = [HumanMessage(content="Hello")]
        result = _create_message_batches(messages, max_tokens=100)

        assert len(result) == 1
        assert len(result[0]) == 1
        assert result[0][0].content == "Hello"

    def test_multiple_messages_single_batch(self, mock_calculate_tokens):
        """Test multiple messages that fit in a single batch."""
        messages = [
            HumanMessage(content="Hi"),
            AIMessage(content="Hello"),
            HumanMessage(content="How are you?"),
        ]
        result = _create_message_batches(messages, max_tokens=100)

        assert len(result) == 1
        assert len(result[0]) == 3
        assert all(msg in result[0] for msg in messages)

    def test_multiple_messages_multiple_batches(self, mock_calculate_tokens):
        """Test messages split across multiple batches."""
        messages = [
            HumanMessage(content="a" * 30),  # 30 tokens
            AIMessage(content="b" * 25),  # 25 tokens
            HumanMessage(content="c" * 40),  # 40 tokens
            AIMessage(content="d" * 20),  # 20 tokens
        ]
        result = _create_message_batches(messages, max_tokens=60)

        # First batch: msg1 (30) + msg2 (25) = 55 tokens
        # Second batch: msg3 (40) + msg4 (20) = 60 tokens
        assert len(result) == 2
        assert len(result[0]) == 2
        assert len(result[1]) == 2
        assert result[0][0].content == "a" * 30
        assert result[0][1].content == "b" * 25
        assert result[1][0].content == "c" * 40
        assert result[1][1].content == "d" * 20

    # Edge Cases with Token Limits

    def test_message_exactly_at_token_limit(self, mock_calculate_tokens):
        """Test message with exactly max_tokens."""
        messages = [HumanMessage(content="a" * 50)]
        result = _create_message_batches(messages, max_tokens=50)

        assert len(result) == 1
        assert len(result[0]) == 1
        assert result[0][0].content == "a" * 50

    def test_message_exceeding_token_limit(self, mock_calculate_tokens):
        """Test message larger than max_tokens gets its own batch."""
        messages = [
            HumanMessage(content="a" * 20),
            AIMessage(content="b" * 150),  # Exceeds max_tokens
            HumanMessage(content="c" * 30),
        ]
        result = _create_message_batches(messages, max_tokens=100)

        # First batch: msg1 (20 tokens)
        # Second batch: msg2 (150 tokens) - oversized, gets own batch
        # Third batch: msg3 (30 tokens)
        assert len(result) == 3
        assert len(result[0]) == 1
        assert len(result[1]) == 1
        assert len(result[2]) == 1
        assert result[0][0].content == "a" * 20
        assert result[1][0].content == "b" * 150
        assert result[2][0].content == "c" * 30

    def test_multiple_oversized_messages(self, mock_calculate_tokens):
        """Test multiple messages that individually exceed max_tokens."""
        messages = [
            HumanMessage(content="a" * 150),
            AIMessage(content="b" * 200),
            HumanMessage(content="c" * 180),
        ]
        result = _create_message_batches(messages, max_tokens=100)

        # Each oversized message gets its own batch
        assert len(result) == 3
        assert all(len(batch) == 1 for batch in result)
        assert result[0][0].content == "a" * 150
        assert result[1][0].content == "b" * 200
        assert result[2][0].content == "c" * 180

    def test_batch_boundary_exact_fit(self, mock_calculate_tokens):
        """Test messages that exactly fill batches to the limit."""
        messages = [
            HumanMessage(content="a" * 50),
            AIMessage(content="b" * 50),
            HumanMessage(content="c" * 50),
            AIMessage(content="d" * 50),
        ]
        result = _create_message_batches(messages, max_tokens=100)

        # Each pair of messages exactly fills 100 tokens
        assert len(result) == 2
        assert len(result[0]) == 2
        assert len(result[1]) == 2
        assert result[0][0].content == "a" * 50
        assert result[0][1].content == "b" * 50
        assert result[1][0].content == "c" * 50
        assert result[1][1].content == "d" * 50

    def test_batch_boundary_one_token_over(self, mock_calculate_tokens):
        """Test that adding a message that exceeds limit creates new batch."""
        messages = [
            HumanMessage(content="a" * 50),
            AIMessage(content="b" * 50),  # Total: 100 tokens
            HumanMessage(content="c"),  # Would make 101, so starts new batch
        ]
        result = _create_message_batches(messages, max_tokens=100)

        assert len(result) == 2
        assert len(result[0]) == 2
        assert len(result[1]) == 1
        assert result[1][0].content == "c"

    # Message Type Tests

    def test_different_message_types(self, mock_calculate_tokens):
        """Test batching with different LangChain message types."""
        messages = [
            HumanMessage(content="User message"),
            AIMessage(content="AI response"),
            HumanMessage(content="Follow-up"),
            AIMessage(content="Another response"),
        ]
        result = _create_message_batches(messages, max_tokens=100)

        assert len(result) == 1
        assert isinstance(result[0][0], HumanMessage)
        assert isinstance(result[0][1], AIMessage)
        assert isinstance(result[0][2], HumanMessage)
        assert isinstance(result[0][3], AIMessage)

    def test_preserves_message_metadata(self, mock_calculate_tokens):
        """Test that message metadata is preserved during batching."""
        messages = [
            HumanMessage(content="Test", additional_kwargs={"key": "value"}),
            AIMessage(content="Response", name="assistant", id="msg-123"),
        ]
        result = _create_message_batches(messages, max_tokens=100)

        assert len(result) == 1
        assert result[0][0].additional_kwargs == {"key": "value"}
        assert result[0][1].name == "assistant"
        assert result[0][1].id == "msg-123"

    # Complex Scenarios

    def test_alternating_small_and_large_messages(self, mock_calculate_tokens):
        """Test alternating pattern of small and large messages."""
        messages = [
            HumanMessage(content="a" * 10),  # Small
            AIMessage(content="b" * 90),  # Large, fills batch with previous
            HumanMessage(content="c" * 5),  # Small
            AIMessage(content="d" * 95),  # Large, fills batch with previous
            HumanMessage(content="e" * 10),  # Small
        ]
        result = _create_message_batches(messages, max_tokens=100)

        # Batch 1: 10 + 90 = 100
        # Batch 2: 5 + 95 = 100
        # Batch 3: 10
        assert len(result) == 3
        assert len(result[0]) == 2
        assert len(result[1]) == 2
        assert len(result[2]) == 1

    def test_many_small_messages(self, mock_calculate_tokens):
        """Test many small messages grouped into optimal batches."""
        # Create 50 messages of 10 tokens each
        messages = [HumanMessage(content="a" * 10) for _ in range(50)]
        result = _create_message_batches(messages, max_tokens=100)

        # Should create 5 batches of 10 messages each (10 msgs * 10 tokens = 100)
        assert len(result) == 5
        assert all(len(batch) == 10 for batch in result)

    def test_gradual_message_size_increase(self, mock_calculate_tokens):
        """Test messages with gradually increasing sizes."""
        messages = [
            HumanMessage(content="a" * 10),
            AIMessage(content="b" * 20),
            HumanMessage(content="c" * 30),
            AIMessage(content="d" * 40),
            HumanMessage(content="e" * 50),
        ]
        result = _create_message_batches(messages, max_tokens=100)

        # Batch 1: 10 + 20 + 30 + 40 = 100 (exactly)
        # Batch 2: 50
        assert len(result) == 2
        assert len(result[0]) == 4
        assert len(result[1]) == 1

    def test_oversized_message_between_normal_messages(self, mock_calculate_tokens):
        """Test oversized message in the middle of normal-sized messages."""
        messages = [
            HumanMessage(content="a" * 30),
            AIMessage(content="b" * 40),
            HumanMessage(content="c" * 150),  # Oversized
            AIMessage(content="d" * 30),
            HumanMessage(content="e" * 40),
        ]
        result = _create_message_batches(messages, max_tokens=100)

        # Batch 1: 30 + 40 = 70
        # Batch 2: 150 (oversized, own batch)
        # Batch 3: 30 + 40 = 70
        assert len(result) == 3
        assert len(result[0]) == 2
        assert len(result[1]) == 1
        assert len(result[2]) == 2
        assert result[1][0].content == "c" * 150

    # Token Calculation Tests

    def test_realistic_token_calculation(self):
        """Test with realistic token counts (not mocked)."""
        # Don't mock calculate_tokens - use actual implementation
        messages = [
            HumanMessage(content="Hello, how are you today?"),
            AIMessage(content="I'm doing well, thank you for asking!"),
            HumanMessage(content="That's great to hear."),
        ]

        # Use a reasonable max_tokens that should fit all messages
        result = _create_message_batches(messages, max_tokens=500)

        # Should fit in one batch with realistic token counts
        assert len(result) >= 1
        assert all(msg in result[0] for msg in messages) if len(result) == 1 else True

    def test_with_custom_token_calculation(self):
        """Test with custom token calculation logic."""
        with patch('codemie.workflows.memory_utils.calculate_tokens') as mock:
            # Each message gets a fixed token count regardless of content
            mock.return_value = 25

            messages = [
                HumanMessage(content="Short"),
                AIMessage(content="Also short"),
                HumanMessage(content="Still short"),
                AIMessage(content="One more"),
                HumanMessage(content="Last one"),
            ]
            result = _create_message_batches(messages, max_tokens=100)

            # Each message is 25 tokens, so 4 per batch (100/25 = 4)
            # 5 messages / 4 per batch = 2 batches
            assert len(result) == 2
            assert len(result[0]) == 4
            assert len(result[1]) == 1

    # Boundary and Special Values

    def test_max_tokens_of_one(self, mock_calculate_tokens):
        """Test with max_tokens set to 1."""
        messages = [
            HumanMessage(content="a"),
            AIMessage(content="b"),
            HumanMessage(content="c"),
        ]
        result = _create_message_batches(messages, max_tokens=1)

        # Each message gets its own batch
        assert len(result) == 3
        assert all(len(batch) == 1 for batch in result)

    def test_very_large_max_tokens(self, mock_calculate_tokens):
        """Test with very large max_tokens value."""
        messages = [
            HumanMessage(content="a" * 100),
            AIMessage(content="b" * 100),
            HumanMessage(content="c" * 100),
        ]
        result = _create_message_batches(messages, max_tokens=1_000_000)

        # All messages fit in single batch
        assert len(result) == 1
        assert len(result[0]) == 3

    def test_empty_message_content(self, mock_calculate_tokens):
        """Test handling of messages with empty content."""
        # Mock to return 0 for empty strings
        mock_calculate_tokens.side_effect = lambda text: len(text)

        messages = [
            HumanMessage(content=""),
            AIMessage(content="Valid content"),
            HumanMessage(content=""),
        ]
        result = _create_message_batches(messages, max_tokens=100)

        assert len(result) == 3
        assert len(result[0]) == 1

    def test_single_character_messages(self, mock_calculate_tokens):
        """Test batching of single-character messages."""
        messages = [HumanMessage(content="a") for _ in range(10)]
        result = _create_message_batches(messages, max_tokens=5)

        # Each message is 1 token, so 5 per batch
        assert len(result) == 2
        assert len(result[0]) == 5
        assert len(result[1]) == 5

    # Message Order Preservation

    def test_preserves_message_order_within_batch(self, mock_calculate_tokens):
        """Test that message order is preserved within each batch."""
        messages = [HumanMessage(content=f"msg_{i}") for i in range(10)]
        result = _create_message_batches(messages, max_tokens=100)

        # Collect all messages from all batches in order
        flattened = []
        for batch in result:
            flattened.extend(batch)

        # Verify order is preserved
        for i, msg in enumerate(flattened):
            assert msg.content == f"msg_{i}"

    def test_preserves_message_order_across_batches(self, mock_calculate_tokens):
        """Test that message order is preserved across batches."""
        messages = [
            HumanMessage(content="a" * 40),
            AIMessage(content="b" * 40),
            HumanMessage(content="c" * 40),
            AIMessage(content="d" * 40),
            HumanMessage(content="e" * 40),
        ]
        result = _create_message_batches(messages, max_tokens=100)

        # Flatten and verify order
        flattened = [msg for batch in result for msg in batch]
        expected_order = ["a" * 40, "b" * 40, "c" * 40, "d" * 40, "e" * 40]

        assert len(flattened) == 5
        for i, msg in enumerate(flattened):
            assert msg.content == expected_order[i]

    # Performance and Edge Cases

    def test_large_number_of_messages(self, mock_calculate_tokens):
        """Test handling of a large number of messages."""
        # Create 1000 messages
        messages = [HumanMessage(content="a" * 10) for _ in range(1000)]
        result = _create_message_batches(messages, max_tokens=100)

        # Each batch should have 10 messages (10 * 10 = 100 tokens)
        assert len(result) == 100
        assert all(len(batch) == 10 for batch in result)

    def test_all_messages_oversized(self, mock_calculate_tokens):
        """Test when all messages exceed max_tokens."""
        messages = [
            HumanMessage(content="a" * 200),
            AIMessage(content="b" * 300),
            HumanMessage(content="c" * 250),
        ]
        result = _create_message_batches(messages, max_tokens=100)

        # Each message should get its own batch
        assert len(result) == 3
        assert all(len(batch) == 1 for batch in result)

    def test_unicode_content(self, mock_calculate_tokens):
        """Test handling of Unicode characters in message content."""
        messages = [
            HumanMessage(content="Hello 世界"),
            AIMessage(content="Привет мир"),
            HumanMessage(content="مرحبا العالم"),
        ]
        result = _create_message_batches(messages, max_tokens=500)

        # Should handle Unicode without errors
        assert len(result) >= 1
        assert all(msg.content in ["Hello 世界", "Привет мир", "مرحبا العالم"] for batch in result for msg in batch)

    def test_special_characters_in_content(self, mock_calculate_tokens):
        """Test handling of special characters."""
        messages = [
            HumanMessage(content="Test\n\nwith\nnewlines"),
            AIMessage(content="Tabs\t\there"),
            HumanMessage(content="Quotes 'single' \"double\""),
        ]
        result = _create_message_batches(messages, max_tokens=500)

        # Should handle special characters without errors
        assert len(result) >= 1
        flattened = [msg for batch in result for msg in batch]
        assert len(flattened) == 3

    # Documentation Example Tests

    def test_documentation_example(self, mock_calculate_tokens):
        """Test the example scenario from the function docstring."""
        # System message should NOT be included in input (as per docstring)
        messages = [
            HumanMessage(content="a" * 30),
            AIMessage(content="b" * 30),
            HumanMessage(content="c" * 30),
            AIMessage(content="d" * 30),
        ]
        result = _create_message_batches(messages, max_tokens=80)

        # Should create batches respecting the 80 token limit
        # Batch 1: 30 + 30 = 60
        # Batch 2: 30 + 30 = 60
        assert len(result) == 2
        assert len(result[0]) == 2
        assert len(result[1]) == 2
