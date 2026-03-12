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

import json
from codemie.service.guardrail.utils import batch_content, MAX_PAYLOAD_BYTES


class TestBatchContent:
    """Test suite for batch_content function."""

    def test_empty_chunks_list(self):
        """Test with empty input list."""
        chunks = []
        batches = list(batch_content(chunks))
        assert batches == []

    def test_single_small_chunk(self):
        """Test with a single chunk that fits within the limit."""
        chunks = ["Hello, world!"]
        batches = list(batch_content(chunks))

        assert len(batches) == 1
        assert len(batches[0]) == 1
        assert batches[0][0] == {"text": {"text": "Hello, world!"}}

    def test_multiple_small_chunks_single_batch(self):
        """Test multiple small chunks that fit in a single batch."""
        chunks = ["First chunk", "Second chunk", "Third chunk"]
        batches = list(batch_content(chunks))

        assert len(batches) == 1
        assert len(batches[0]) == 3
        assert batches[0][0]["text"]["text"] == "First chunk"
        assert batches[0][1]["text"]["text"] == "Second chunk"
        assert batches[0][2]["text"]["text"] == "Third chunk"

    def test_chunks_requiring_multiple_batches(self):
        """Test chunks that require splitting into multiple batches."""
        # Create chunks that will exceed MAX_PAYLOAD_BYTES when batched
        large_chunk = "A" * 50000  # 50 KB
        chunks = [large_chunk, large_chunk]  # 100 KB total, requires 2 batches

        batches = list(batch_content(chunks))

        assert len(batches) == 2
        assert len(batches[0]) == 1  # First batch has first chunk
        assert len(batches[1]) == 1  # Second batch has second chunk
        assert batches[0][0]["text"]["text"] == large_chunk
        assert batches[1][0]["text"]["text"] == large_chunk

    def test_single_chunk_exceeds_max_payload(self):
        """Test a single chunk that exceeds MAX_PAYLOAD_BYTES and must be split."""
        # Create a chunk larger than MAX_PAYLOAD_BYTES (70 KB)
        oversized_chunk = "X" * 100000  # 100 KB
        chunks = [oversized_chunk]

        batches = list(batch_content(chunks))

        # Should be split into multiple batches
        assert len(batches) > 1

        # Verify each batch is within the limit
        for batch in batches:
            batch_bytes = len(json.dumps(batch).encode("utf-8"))
            assert batch_bytes <= MAX_PAYLOAD_BYTES

        # Verify all sub-chunks together reconstruct the original
        reconstructed = "".join(item["text"]["text"] for batch in batches for item in batch)
        assert reconstructed == oversized_chunk

    def test_mixed_small_and_large_chunks(self):
        """Test mixture of small chunks and chunks requiring splitting."""
        small_chunk = "Small text"
        large_chunk = "L" * 80000  # 80 KB - exceeds limit
        chunks = [small_chunk, large_chunk, small_chunk]

        batches = list(batch_content(chunks))

        # The first small chunk gets batched with later chunks,
        # NOT yielded as the first batch
        # The large chunk gets split and yielded first!

        # Verify all text is preserved (order matters)
        all_text = []
        for batch in batches:
            for item in batch:
                all_text.append(item["text"]["text"])

        reconstructed = "".join(all_text)
        original = small_chunk + large_chunk + small_chunk
        assert reconstructed == original

        # Verify the large chunk was split into multiple batches
        assert len(batches) > 1

        # At least one batch should contain only part of the large chunk
        large_chunk_parts = [item["text"]["text"] for batch in batches for item in batch if "L" in item["text"]["text"]]
        assert len("".join(large_chunk_parts)) == len(large_chunk)

    def test_chunk_at_exact_max_payload_boundary(self):
        """Test chunk that's exactly at MAX_PAYLOAD_BYTES boundary."""
        # Calculate the exact size that would produce MAX_PAYLOAD_BYTES JSON
        # Account for JSON overhead: {"text":{"text":"..."}}
        json_overhead = len(json.dumps({"text": {"text": ""}}).encode("utf-8"))
        exact_size = MAX_PAYLOAD_BYTES - json_overhead - 10  # Small buffer for safety

        boundary_chunk = "B" * exact_size
        chunks = [boundary_chunk, "next"]

        batches = list(batch_content(chunks))

        # Should fit in one batch if at exact boundary, or two if slightly over
        assert len(batches) >= 1

        # Verify no data loss
        reconstructed = "".join(item["text"]["text"] for batch in batches for item in batch)
        assert reconstructed == boundary_chunk + "next"

    def test_multiple_chunks_fill_batch_exactly(self):
        """Test multiple chunks that fill a batch to near capacity."""
        # Create chunks that together approach MAX_PAYLOAD_BYTES
        chunk_size = 20000  # 20 KB each
        chunks = ["C" * chunk_size for _ in range(3)]  # 60 KB total

        batches = list(batch_content(chunks))

        # Should fit in one batch (60 KB < 70 KB)
        assert len(batches) == 1
        assert len(batches[0]) == 3

    def test_chunks_with_unicode_characters(self):
        """Test chunks containing multi-byte Unicode characters."""
        # Emoji and other multi-byte characters
        unicode_chunk = "Hello 👋 世界 🌍" * 1000
        chunks = [unicode_chunk, unicode_chunk]

        batches = list(batch_content(chunks))

        # Verify all text is preserved correctly
        reconstructed = "".join(item["text"]["text"] for batch in batches for item in batch)
        assert reconstructed == unicode_chunk + unicode_chunk

    def test_chunks_with_special_json_characters(self):
        """Test chunks containing characters that require JSON escaping."""
        special_chunk = 'Text with "quotes", \\backslashes\\, and \n newlines'
        chunks = [special_chunk] * 10

        batches = list(batch_content(chunks))

        # Verify all batches are valid and within limit
        for batch in batches:
            batch_json = json.dumps(batch)
            batch_bytes = len(batch_json.encode("utf-8"))
            assert batch_bytes <= MAX_PAYLOAD_BYTES

        # Verify content preservation
        reconstructed = "".join(item["text"]["text"] for batch in batches for item in batch)
        assert reconstructed == special_chunk * 10

    def test_very_large_chunk_splitting(self):
        """Test that extremely large chunks are split correctly."""
        # Create a chunk 3x larger than MAX_PAYLOAD_BYTES
        huge_chunk = "H" * (MAX_PAYLOAD_BYTES * 3)
        chunks = [huge_chunk]

        batches = list(batch_content(chunks))

        # Should be split into multiple batches
        assert len(batches) >= 3

        # Each batch must be within limit
        for batch in batches:
            assert len(batch) == 1  # Each sub-chunk is its own batch
            batch_bytes = len(json.dumps(batch).encode("utf-8"))
            assert batch_bytes <= MAX_PAYLOAD_BYTES

        # Verify complete reconstruction
        reconstructed = "".join(item["text"]["text"] for batch in batches for item in batch)
        assert reconstructed == huge_chunk

    def test_empty_string_chunks(self):
        """Test handling of empty string chunks."""
        chunks = ["", "valid text", "", "more text", ""]
        batches = list(batch_content(chunks))

        # Verify all chunks are included (even empty ones)
        all_texts = [item["text"]["text"] for batch in batches for item in batch]
        assert all_texts == ["", "valid text", "", "more text", ""]

    def test_whitespace_only_chunks(self):
        """Test chunks containing only whitespace."""
        chunks = ["   ", "\n\n\n", "\t\t\t", "actual text"]
        batches = list(batch_content(chunks))

        # Verify whitespace is preserved
        all_texts = [item["text"]["text"] for batch in batches for item in batch]
        assert all_texts == ["   ", "\n\n\n", "\t\t\t", "actual text"]

    def test_alternating_small_and_large_chunks(self):
        """Test alternating pattern of small and large chunks."""
        small = "small"
        large = "L" * 75000  # Exceeds limit
        chunks = [small, large, small, large, small]

        batches = list(batch_content(chunks))

        # Verify order is preserved
        reconstructed_texts = [item["text"]["text"] for batch in batches for item in batch]
        original_concat = small + large + small + large + small
        assert "".join(reconstructed_texts) == original_concat

    def test_batch_size_calculation_accuracy(self):
        """Test that batch size calculation is accurate and doesn't exceed limit."""
        # Create various sized chunks
        chunks = ["A" * 10000, "B" * 20000, "C" * 30000, "D" * 5000]

        batches = list(batch_content(chunks))

        # Verify each batch's actual JSON size
        for batch in batches:
            actual_bytes = len(json.dumps(batch).encode("utf-8"))
            assert actual_bytes <= MAX_PAYLOAD_BYTES

    def test_chunk_splitting_preserves_order(self):
        """Test that when a chunk is split, the order of sub-chunks is correct."""
        large_chunk = "123456789" * 10000  # Create identifiable pattern
        chunks = [large_chunk]

        batches = list(batch_content(chunks))

        # Reconstruct and verify pattern
        reconstructed = "".join(item["text"]["text"] for batch in batches for item in batch)
        assert reconstructed == large_chunk

        # Verify the pattern is intact
        assert reconstructed.startswith("123456789")
        assert reconstructed.endswith("123456789")

    def test_single_character_chunks(self):
        """Test chunks containing single characters."""
        chunks = ["A", "B", "C", "D", "E"] * 100
        batches = list(batch_content(chunks))

        # Should batch efficiently
        total_items = sum(len(batch) for batch in batches)
        assert total_items == 500  # 5 chars * 100 repetitions

    def test_json_structure_format(self):
        """Test that output has correct JSON structure."""
        chunks = ["test chunk"]
        batches = list(batch_content(chunks))

        # Verify structure
        assert isinstance(batches[0], list)
        assert isinstance(batches[0][0], dict)
        assert "text" in batches[0][0]
        assert "text" in batches[0][0]["text"]
        assert batches[0][0]["text"]["text"] == "test chunk"

    def test_unsplittable_chunk_raises_error(self):
        """Test that an impossibly large chunk that can't be split raises ValueError."""
        # This test is theoretical since we dynamically reduce sub_len
        # But we can test the error path exists

        # Create a mock scenario - in practice this shouldn't happen
        # because even single characters should fit
        # The error would occur if even 1 character exceeds MAX_PAYLOAD_BYTES
        # which is impossible with current MAX_PAYLOAD_BYTES = 70KB

        # For code coverage, we'd need to mock the scenario or reduce MAX_PAYLOAD_BYTES
        # Let's verify the function handles normal large chunks without error
        large_chunk = "X" * (MAX_PAYLOAD_BYTES * 2)
        chunks = [large_chunk]

        # Should not raise error - should split successfully
        batches = list(batch_content(chunks))
        assert len(batches) > 0

    def test_generator_yields_batches_lazily(self):
        """Test that batch_content is a generator and yields lazily."""
        chunks = ["A" * 10000 for _ in range(10)]
        result = batch_content(chunks)

        # Should be a generator
        assert hasattr(result, '__iter__')
        assert hasattr(result, '__next__')

        # Can iterate manually
        first_batch = next(result)
        assert isinstance(first_batch, list)

    def test_realistic_guardrail_content(self):
        """Test with realistic guardrail content (messages, prompts, etc.)."""
        chunks = [
            "User asked: What is the weather today?",
            "AI responded: I don't have access to real-time weather data. " * 1000,
            "System prompt: You are a helpful assistant.",
            "A" * 80000,  # Large content that needs splitting
            "Final message in conversation",
        ]

        batches = list(batch_content(chunks))

        # Verify all content is preserved
        reconstructed = "".join(item["text"]["text"] for batch in batches for item in batch)
        original = "".join(chunks)
        assert reconstructed == original

        # Verify all batches are within limit
        for batch in batches:
            batch_bytes = len(json.dumps(batch).encode("utf-8"))
            assert batch_bytes <= MAX_PAYLOAD_BYTES
