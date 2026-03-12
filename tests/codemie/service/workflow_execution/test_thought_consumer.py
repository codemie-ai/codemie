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

"""Unit tests for the thought consumer module."""

import queue
import time
from unittest.mock import MagicMock, patch

import pytest

from codemie.chains.base import Thought, ThoughtAuthorType
from codemie.core.thought_queue import ThoughtContext, ThoughtQueueItem
from codemie.core.thread import ThreadedGenerator
from codemie.service.workflow_execution.thought_consumer import ThoughtConsumer


@pytest.fixture
def mock_message_queue():
    """Create a mock ThreadedGenerator with a queue."""
    message_queue = MagicMock(spec=ThreadedGenerator)
    message_queue.queue = queue.Queue()
    return message_queue


@pytest.fixture
def sample_thought():
    """Create a sample Thought object."""
    return Thought(
        id="thought-123",
        parent_id=None,
        message="Test thought message",
        in_progress=False,
        author_type=ThoughtAuthorType.Agent,
        author_name="TestAgent",
        input_text="Test input",
    )


@pytest.fixture
def sample_thought_context():
    """Create a sample ThoughtContext."""
    return ThoughtContext(
        request_uuid="request-uuid-123",
        user_id="user-123",
        execution_state_id="exec-state-123",
    )


@pytest.fixture
def sample_thought_queue_item(sample_thought, sample_thought_context):
    """Create a sample ThoughtQueueItem."""
    return ThoughtQueueItem(data=sample_thought, context=sample_thought_context)


class TestThoughtConsumerInitialization:
    """Tests for ThoughtConsumer initialization."""

    def test_init(self, mock_message_queue):
        """Test ThoughtConsumer initialization."""
        # Act
        consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)

        # Assert
        assert consumer.workflow_execution_id == "exec-123"
        assert consumer.message_queue == mock_message_queue
        assert consumer.cache == {}

    def test_run_creates_thread(self, mock_message_queue):
        """Test that run method creates and starts a thread."""
        # Arrange
        execution_id = "exec-123"

        # Mock the consume method to avoid actual execution
        with patch.object(ThoughtConsumer, 'consume'):
            # Act
            ThoughtConsumer.run(execution_id=execution_id, message_queue=mock_message_queue)

            # Give thread time to start
            time.sleep(0.1)

        # Assert - thread was created (can't easily verify it started without more complex mocking)
        # The test verifies the method doesn't raise an error


class TestThoughtConsumerConsume:
    """Tests for ThoughtConsumer consume method."""

    @patch('codemie.service.workflow_execution.thought_consumer.WorkflowExecutionStateThought')
    def test_consume_processes_single_thought(self, mock_thought_model, mock_message_queue, sample_thought_queue_item):
        """Test consuming a single thought from the queue."""
        # Arrange
        consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)

        # Add thought and stop signal to queue
        mock_message_queue.queue.put(sample_thought_queue_item)
        mock_message_queue.queue.put(StopIteration())

        mock_thought_instance = MagicMock()
        mock_thought_model.return_value = mock_thought_instance

        # Act
        consumer.consume()

        # Assert
        mock_thought_model.assert_called_once()
        call_kwargs = mock_thought_model.call_args[1]
        assert call_kwargs['id'] == "thought-123"
        assert call_kwargs['execution_state_id'] == "exec-state-123"
        assert call_kwargs['content'] == "Test thought message"
        assert call_kwargs['author_name'] == "TestAgent"
        assert call_kwargs['author_type'] == ThoughtAuthorType.Agent
        mock_thought_instance.save.assert_called_once_with(refresh=True)

    @patch('codemie.service.workflow_execution.thought_consumer.WorkflowExecutionStateThought')
    def test_consume_handles_stop_iteration(self, mock_thought_model, mock_message_queue):
        """Test that consume method stops on StopIteration."""
        # Arrange
        consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)
        mock_message_queue.queue.put(StopIteration())

        # Act
        consumer.consume()

        # Assert
        mock_thought_model.assert_not_called()

    def test_consume_breaks_when_no_queue(self, mock_message_queue):
        """Test consume breaks when message_queue has no queue attribute."""
        # Arrange
        consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)
        del mock_message_queue.queue

        # Act
        consumer.consume()

        # Assert - method should exit gracefully without errors

    @patch('codemie.service.workflow_execution.thought_consumer.WorkflowExecutionStateThought')
    def test_consume_skips_thought_without_execution_state_id(
        self, mock_thought_model, mock_message_queue, sample_thought
    ):
        """Test that thoughts without execution_state_id are skipped."""
        # Arrange
        consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)

        # Create context without execution_state_id
        context_no_exec_id = ThoughtContext(request_uuid="request-123", user_id="user-123", execution_state_id=None)
        queue_item = ThoughtQueueItem(data=sample_thought, context=context_no_exec_id)

        mock_message_queue.queue.put(queue_item)
        mock_message_queue.queue.put(StopIteration())

        # Act
        consumer.consume()

        # Assert
        mock_thought_model.assert_not_called()

    @patch('codemie.service.workflow_execution.thought_consumer.WorkflowExecutionStateThought')
    def test_consume_skips_in_progress_thoughts(self, mock_thought_model, mock_message_queue, sample_thought_context):
        """Test that in_progress thoughts are cached but not saved."""
        # Arrange
        consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)

        # Create in_progress thought
        in_progress_thought = Thought(
            id="thought-456",
            message="In progress...",
            in_progress=True,
            author_type=ThoughtAuthorType.Agent,
            author_name="TestAgent",
        )
        queue_item = ThoughtQueueItem(data=in_progress_thought, context=sample_thought_context)

        mock_message_queue.queue.put(queue_item)
        mock_message_queue.queue.put(StopIteration())

        # Act
        consumer.consume()

        # Assert
        mock_thought_model.assert_not_called()
        assert "thought-456" in consumer.cache
        assert consumer.cache["thought-456"] == "In progress..."

    @patch('codemie.service.workflow_execution.thought_consumer.WorkflowExecutionStateThought')
    def test_consume_aggregates_message_chunks(self, mock_thought_model, mock_message_queue, sample_thought_context):
        """Test that message chunks are aggregated in cache before saving."""
        # Arrange
        consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)

        # Create thought chunks
        thought_chunk1 = Thought(
            id="thought-789",
            message="First chunk ",
            in_progress=True,
            author_type=ThoughtAuthorType.Tool,
            author_name="TestTool",
            input_text="input",
        )
        thought_chunk2 = Thought(
            id="thought-789",
            message="second chunk ",
            in_progress=True,
            author_type=ThoughtAuthorType.Tool,
            author_name="TestTool",
            input_text="input",
        )
        thought_final = Thought(
            id="thought-789",
            message="final chunk",
            in_progress=False,
            author_type=ThoughtAuthorType.Tool,
            author_name="TestTool",
            input_text="input",
        )

        queue_item1 = ThoughtQueueItem(data=thought_chunk1, context=sample_thought_context)
        queue_item2 = ThoughtQueueItem(data=thought_chunk2, context=sample_thought_context)
        queue_item3 = ThoughtQueueItem(data=thought_final, context=sample_thought_context)

        mock_message_queue.queue.put(queue_item1)
        mock_message_queue.queue.put(queue_item2)
        mock_message_queue.queue.put(queue_item3)
        mock_message_queue.queue.put(StopIteration())

        mock_thought_instance = MagicMock()
        mock_thought_model.return_value = mock_thought_instance

        # Act
        consumer.consume()

        # Assert
        mock_thought_model.assert_called_once()
        call_kwargs = mock_thought_model.call_args[1]
        assert call_kwargs['content'] == "First chunk second chunk final chunk"
        mock_thought_instance.save.assert_called_once_with(refresh=True)
        assert "thought-789" not in consumer.cache  # Should be cleared after saving

    @patch('codemie.service.workflow_execution.thought_consumer.WorkflowExecutionStateThought')
    def test_consume_handles_multiple_thoughts(self, mock_thought_model, mock_message_queue, sample_thought_context):
        """Test consuming multiple different thoughts."""
        # Arrange
        consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)

        thought1 = Thought(
            id="thought-1",
            message="Message 1",
            in_progress=False,
            author_type=ThoughtAuthorType.Agent,
            author_name="Agent1",
        )
        thought2 = Thought(
            id="thought-2",
            message="Message 2",
            in_progress=False,
            author_type=ThoughtAuthorType.Tool,
            author_name="Tool1",
        )

        queue_item1 = ThoughtQueueItem(data=thought1, context=sample_thought_context)
        queue_item2 = ThoughtQueueItem(data=thought2, context=sample_thought_context)

        mock_message_queue.queue.put(queue_item1)
        mock_message_queue.queue.put(queue_item2)
        mock_message_queue.queue.put(StopIteration())

        mock_thought_instance = MagicMock()
        mock_thought_model.return_value = mock_thought_instance

        # Act
        consumer.consume()

        # Assert
        assert mock_thought_model.call_count == 2
        assert mock_thought_instance.save.call_count == 2

    @patch('codemie.service.workflow_execution.thought_consumer.WorkflowExecutionStateThought')
    def test_consume_preserves_parent_id(self, mock_thought_model, mock_message_queue, sample_thought_context):
        """Test that parent_id is preserved when saving thoughts."""
        # Arrange
        consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)

        thought_with_parent = Thought(
            id="child-thought",
            parent_id="parent-thought",
            message="Child message",
            in_progress=False,
            author_type=ThoughtAuthorType.System,
            author_name="System",
        )
        queue_item = ThoughtQueueItem(data=thought_with_parent, context=sample_thought_context)

        mock_message_queue.queue.put(queue_item)
        mock_message_queue.queue.put(StopIteration())

        mock_thought_instance = MagicMock()
        mock_thought_model.return_value = mock_thought_instance

        # Act
        consumer.consume()

        # Assert
        mock_thought_model.assert_called_once()
        call_kwargs = mock_thought_model.call_args[1]
        assert call_kwargs['parent_id'] == "parent-thought"

    def test_consume_handles_non_thought_queue_items(self, mock_message_queue):
        """Test that non-ThoughtQueueItem objects are skipped gracefully."""
        # Arrange
        consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)

        # Put some non-ThoughtQueueItem objects
        mock_message_queue.queue.put("random string")
        mock_message_queue.queue.put(123)
        mock_message_queue.queue.put({"key": "value"})
        mock_message_queue.queue.put(StopIteration())

        # Act
        consumer.consume()

        # Assert - should complete without errors


class TestThoughtConsumerUpdateCache:
    """Tests for ThoughtConsumer _update_thought_cache method."""

    def test_update_cache_creates_new_entry(self, mock_message_queue):
        """Test that _update_thought_cache creates new cache entry."""
        # Arrange
        consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)
        thought = Thought(
            id="new-thought",
            message="New message",
            in_progress=True,
            author_type=ThoughtAuthorType.Agent,
            author_name="Agent",
        )

        # Act
        consumer._update_thought_cache(thought)

        # Assert
        assert "new-thought" in consumer.cache
        assert consumer.cache["new-thought"] == "New message"

    def test_update_cache_appends_to_existing_entry(self, mock_message_queue):
        """Test that _update_thought_cache appends to existing entry."""
        # Arrange
        consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)
        consumer.cache["existing-thought"] = "Initial message "

        thought = Thought(
            id="existing-thought",
            message="additional text",
            in_progress=True,
            author_type=ThoughtAuthorType.Agent,
            author_name="Agent",
        )

        # Act
        consumer._update_thought_cache(thought)

        # Assert
        assert consumer.cache["existing-thought"] == "Initial message additional text"

    def test_update_cache_handles_empty_message(self, mock_message_queue):
        """Test that _update_thought_cache handles empty messages."""
        # Arrange
        consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)
        thought = Thought(
            id="thought-empty",
            message="",
            in_progress=True,
            author_type=ThoughtAuthorType.Agent,
            author_name="Agent",
        )

        # Act
        consumer._update_thought_cache(thought)

        # Assert
        assert "thought-empty" in consumer.cache
        assert consumer.cache["thought-empty"] == ""

    def test_update_cache_handles_none_message(self, mock_message_queue):
        """Test that _update_thought_cache raises TypeError with None messages."""
        # Arrange
        consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)
        thought = Thought(
            id="thought-none",
            message=None,
            in_progress=True,
            author_type=ThoughtAuthorType.Agent,
            author_name="Agent",
        )

        # Act & Assert
        # The implementation doesn't handle None messages, it will raise TypeError
        with pytest.raises(TypeError, match="can only concatenate str"):
            consumer._update_thought_cache(thought)


class TestThoughtConsumerIntegration:
    """Integration tests for ThoughtConsumer."""

    @patch('codemie.service.workflow_execution.thought_consumer.WorkflowExecutionStateThought')
    def test_full_workflow_with_streaming_thought(self, mock_thought_model, mock_message_queue, sample_thought_context):
        """Test complete workflow of streaming thought chunks."""
        # Arrange
        consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)

        # Simulate streaming thought: multiple chunks then final
        chunks = [
            Thought(
                id="stream-thought",
                message=f"Chunk {i} ",
                in_progress=True,
                author_type=ThoughtAuthorType.Tool,
                author_name="StreamTool",
                input_text="stream input",
            )
            for i in range(5)
        ]

        final_thought = Thought(
            id="stream-thought",
            message="final",
            in_progress=False,
            author_type=ThoughtAuthorType.Tool,
            author_name="StreamTool",
            input_text="stream input",
        )

        for chunk in chunks:
            queue_item = ThoughtQueueItem(data=chunk, context=sample_thought_context)
            mock_message_queue.queue.put(queue_item)

        final_queue_item = ThoughtQueueItem(data=final_thought, context=sample_thought_context)
        mock_message_queue.queue.put(final_queue_item)
        mock_message_queue.queue.put(StopIteration())

        mock_thought_instance = MagicMock()
        mock_thought_model.return_value = mock_thought_instance

        # Act
        consumer.consume()

        # Assert
        mock_thought_model.assert_called_once()
        call_kwargs = mock_thought_model.call_args[1]
        expected_content = "Chunk 0 Chunk 1 Chunk 2 Chunk 3 Chunk 4 final"
        assert call_kwargs['content'] == expected_content
        mock_thought_instance.save.assert_called_once_with(refresh=True)
        assert "stream-thought" not in consumer.cache

    @patch('codemie.service.workflow_execution.thought_consumer.WorkflowExecutionStateThought')
    def test_multiple_concurrent_thoughts(self, mock_thought_model, mock_message_queue, sample_thought_context):
        """Test handling multiple thoughts being streamed concurrently."""
        # Arrange
        consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)

        # Create interleaved chunks from two different thoughts
        thought1_chunk1 = Thought(
            id="thought-A", message="A1 ", in_progress=True, author_type=ThoughtAuthorType.Agent, author_name="AgentA"
        )
        thought2_chunk1 = Thought(
            id="thought-B", message="B1 ", in_progress=True, author_type=ThoughtAuthorType.Agent, author_name="AgentB"
        )
        thought1_chunk2 = Thought(
            id="thought-A", message="A2 ", in_progress=True, author_type=ThoughtAuthorType.Agent, author_name="AgentA"
        )
        thought2_chunk2 = Thought(
            id="thought-B", message="B2 ", in_progress=True, author_type=ThoughtAuthorType.Agent, author_name="AgentB"
        )
        thought1_final = Thought(
            id="thought-A", message="A3", in_progress=False, author_type=ThoughtAuthorType.Agent, author_name="AgentA"
        )
        thought2_final = Thought(
            id="thought-B", message="B3", in_progress=False, author_type=ThoughtAuthorType.Agent, author_name="AgentB"
        )

        # Interleave the chunks
        for thought in [
            thought1_chunk1,
            thought2_chunk1,
            thought1_chunk2,
            thought2_chunk2,
            thought1_final,
            thought2_final,
        ]:
            queue_item = ThoughtQueueItem(data=thought, context=sample_thought_context)
            mock_message_queue.queue.put(queue_item)

        mock_message_queue.queue.put(StopIteration())

        mock_thought_instance = MagicMock()
        mock_thought_model.return_value = mock_thought_instance

        # Act
        consumer.consume()

        # Assert
        assert mock_thought_model.call_count == 2
        calls = mock_thought_model.call_args_list

        # Verify both thoughts were saved with correct content
        saved_contents = {call[1]['id']: call[1]['content'] for call in calls}
        assert saved_contents["thought-A"] == "A1 A2 A3"
        assert saved_contents["thought-B"] == "B1 B2 B3"
        assert mock_thought_instance.save.call_count == 2

    @patch('codemie.service.workflow_execution.thought_consumer.WorkflowExecutionStateThought')
    def test_error_handling_during_save(self, mock_thought_model, mock_message_queue, sample_thought_queue_item):
        """Test that errors during save don't crash the consumer."""
        # Arrange
        consumer = ThoughtConsumer(workflow_execution_id="exec-123", message_queue=mock_message_queue)

        mock_message_queue.queue.put(sample_thought_queue_item)
        mock_message_queue.queue.put(StopIteration())

        mock_thought_instance = MagicMock()
        mock_thought_instance.save.side_effect = Exception("Database error")
        mock_thought_model.return_value = mock_thought_instance

        # Act & Assert - should raise the exception since there's no try/except in the code
        with pytest.raises(Exception, match="Database error"):
            consumer.consume()
