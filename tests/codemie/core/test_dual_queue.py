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
Unit tests for DualQueue implementation.

Tests that DualQueue correctly sends messages to both streaming and persistence queues.
"""

import pytest

from codemie.core.dual_queue import DualQueue
from codemie.core.thread import ThreadedGenerator
from codemie.core.thought_queue import ThoughtQueue, ThoughtQueueItem
from codemie.chains.base import StreamedGenerationResult, Thought, ThoughtAuthorType


class TestDualQueue:
    """Test DualQueue functionality"""

    @pytest.fixture
    def streaming_queue(self):
        """Create mock streaming queue"""
        return ThreadedGenerator(
            request_uuid="test-request",
            user_id="test-user",
            conversation_id="test-conversation",
        )

    @pytest.fixture
    def persistence_queue(self):
        """Create mock persistence queue"""
        queue = ThoughtQueue()
        queue.set_context('user_id', 'test-user')
        return queue

    @pytest.fixture
    def dual_queue(self, streaming_queue, persistence_queue):
        """Create DualQueue instance"""
        return DualQueue(
            streaming_queue=streaming_queue,
            persistence_queue=persistence_queue,
        )

    def test_send_to_both_queues(self, dual_queue, streaming_queue, persistence_queue):
        """Test that send() sends to both queues"""
        # Create a thought message
        thought = Thought(
            id="test-thought-id",
            message="Test message",
            author_name="Test Agent",
            author_type=ThoughtAuthorType.Agent.value,
            in_progress=False,
        )
        result = StreamedGenerationResult(
            thought=thought,
            context={'execution_state_id': 'test-state-id'},
        )
        message = result.model_dump_json()

        # Send to dual queue
        dual_queue.send(message)

        # Verify streaming queue received the message
        assert not streaming_queue.queue.empty()
        streaming_message = streaming_queue.queue.get()
        assert streaming_message == message

        # Verify persistence queue received the message as ThoughtQueueItem
        assert not persistence_queue.queue.empty()
        persistence_item = persistence_queue.queue.get()
        assert isinstance(persistence_item, ThoughtQueueItem)
        assert persistence_item.data.id == "test-thought-id"
        assert persistence_item.data.message == "Test message"
        assert persistence_item.context.execution_state_id == "test-state-id"

    def test_close_both_queues(self, dual_queue, streaming_queue, persistence_queue):
        """Test that close() closes both queues"""
        dual_queue.close()

        # Verify both queues are closed
        assert streaming_queue.is_closed()
        assert persistence_queue.is_closed()

        # Verify StopIteration was sent to both
        streaming_item = streaming_queue.queue.get()
        assert streaming_item is StopIteration

        persistence_item = persistence_queue.queue.get()
        assert persistence_item is StopIteration

    def test_is_closed_reflects_streaming_queue(self, dual_queue, streaming_queue):
        """Test that is_closed() reflects streaming queue state"""
        assert not dual_queue.is_closed()

        streaming_queue.close()

        assert dual_queue.is_closed()

    def test_iteration_uses_streaming_queue(self, dual_queue, streaming_queue):
        """Test that iteration iterates over streaming queue"""
        test_messages = ["message1", "message2", "message3"]

        for msg in test_messages:
            streaming_queue.queue.put(msg)

        streaming_queue.queue.put(StopIteration)

        # Iterate over dual queue
        collected_messages = []
        try:
            for message in dual_queue:
                collected_messages.append(message)  # noqa PERF402
        except StopIteration:
            pass

        assert collected_messages == test_messages

    def test_set_context_on_both_queues(self, dual_queue):
        """Test that set_context() sets context on both queues"""
        dual_queue.set_context('execution_state_id', 'test-state-123')

        # Verify context is set on both queues
        assert dual_queue.streaming_queue.context.execution_state_id == 'test-state-123'
        assert dual_queue.persistence_queue.context.execution_state_id == 'test-state-123'

    def test_get_from_context_uses_persistence_queue(self, dual_queue, persistence_queue):
        """Test that get_from_context() uses persistence queue"""
        persistence_queue.set_context('user_id', 'test-user-456')

        value = dual_queue.get_from_context('user_id')

        assert value == 'test-user-456'

    def test_multiple_thoughts_sent_correctly(self, dual_queue, persistence_queue):
        """Test sending multiple thoughts in sequence"""
        thoughts_data = [
            ("thought-1", "Message 1", False),
            ("thought-2", "Message 2", True),  # in_progress
            ("thought-3", "Message 3", False),
        ]

        for thought_id, message, in_progress in thoughts_data:
            thought = Thought(
                id=thought_id,
                message=message,
                author_name="Agent",
                author_type=ThoughtAuthorType.Agent.value,
                in_progress=in_progress,
            )
            result = StreamedGenerationResult(
                thought=thought,
                context={'execution_state_id': 'state-1'},
            )
            dual_queue.send(result.model_dump_json())

        # Verify all thoughts reached persistence queue
        thought_items = []
        while not persistence_queue.queue.empty():
            item = persistence_queue.queue.get()
            if isinstance(item, ThoughtQueueItem):
                thought_items.append(item)

        assert len(thought_items) == 3
        assert thought_items[0].data.id == "thought-1"
        assert thought_items[1].data.id == "thought-2"
        assert thought_items[1].data.in_progress is True
        assert thought_items[2].data.id == "thought-3"

    def test_workflow_state_events_handled_correctly(self, dual_queue, streaming_queue, persistence_queue):
        """Test that workflow state events are sent to streaming but skipped by persistence queue"""
        from codemie.chains.base import WorkflowStateEvent

        # Send a workflow state event (no thought, should be skipped by persistence queue)
        state_event = WorkflowStateEvent(
            id="state-1",
            name="Test State",
            status="in_progress",
            event_type="state_start",
        )
        result = StreamedGenerationResult(workflow_state=state_event)
        message = result.model_dump_json()

        dual_queue.send(message)

        # Verify streaming queue received the message
        assert not streaming_queue.queue.empty()
        streaming_message = streaming_queue.queue.get()
        assert streaming_message == message

        # Verify persistence queue is empty (ThoughtQueue.send() skips non-thought messages)
        assert persistence_queue.queue.empty()

    def test_client_disconnect_scenario(self, dual_queue, streaming_queue, persistence_queue):
        """
        Test client disconnect scenario:
        - Streaming queue closes (simulating disconnect)
        - Persistence queue continues processing
        """
        # Send a thought
        thought = Thought(
            id="thought-before-disconnect",
            message="Before disconnect",
            author_name="Agent",
            author_type=ThoughtAuthorType.Agent.value,
            in_progress=False,
        )
        result = StreamedGenerationResult(
            thought=thought,
            context={'execution_state_id': 'state-1'},
        )
        dual_queue.send(result.model_dump_json())

        # Simulate client disconnect - close streaming queue only
        streaming_queue.close()

        # Dual queue should report as closed
        assert dual_queue.is_closed()

        # But persistence queue should still be open
        assert not persistence_queue.is_closed()

        # And it should have received the thought
        persistence_item = persistence_queue.queue.get()
        assert isinstance(persistence_item, ThoughtQueueItem)
        assert persistence_item.data.id == "thought-before-disconnect"

        # Persistence queue can continue receiving thoughts even after streaming closes
        # (This would happen from the workflow execution thread)
        thought2 = Thought(
            id="thought-after-disconnect",
            message="After disconnect",
            author_name="Agent",
            author_type=ThoughtAuthorType.Agent.value,
            in_progress=False,
        )
        result2 = StreamedGenerationResult(
            thought=thought2,
            context={'execution_state_id': 'state-1'},
        )
        # Directly send to persistence queue (simulating workflow continuing after disconnect)
        persistence_queue.send(result2.model_dump_json())

        persistence_item2 = persistence_queue.queue.get()
        assert isinstance(persistence_item2, ThoughtQueueItem)
        assert persistence_item2.data.id == "thought-after-disconnect"
