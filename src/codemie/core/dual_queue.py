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
Dual queue implementation for workflow execution.

This module provides a DualQueue class that wraps both ThreadedGenerator (for streaming)
and ThoughtQueue (for database persistence), allowing both to process the same thought
messages in parallel during streaming mode.
"""

from typing import Any

from codemie.core.thread import ThreadedGenerator
from codemie.core.thought_queue import ThoughtQueue


class DualQueue:
    """
    Wrapper that sends messages to both streaming and persistence queues.

    This allows workflow streaming mode to simultaneously:
    - Stream thoughts to the client via ThreadedGenerator
    - Save thoughts to the database via ThoughtQueue + ThoughtConsumer

    The two queues operate independently, so if the streaming connection is lost,
    database persistence continues unaffected.
    """

    def __init__(self, streaming_queue: ThreadedGenerator, persistence_queue: ThoughtQueue):
        """
        Initialize dual queue with both streaming and persistence queues.

        Args:
            streaming_queue: ThreadedGenerator for streaming thoughts to client
            persistence_queue: ThoughtQueue for saving thoughts to database
        """
        self.streaming_queue = streaming_queue
        self.persistence_queue = persistence_queue

    def send(self, data: Any) -> None:
        """
        Send message to both queues for parallel processing.

        Args:
            data: Message data (typically JSON string with thought/state info)
        """
        self.streaming_queue.send(data)  # For client streaming
        self.persistence_queue.send(data)  # For database saving

    def close(self) -> None:
        """Close both queues."""
        self.streaming_queue.close()
        self.persistence_queue.close()

    def is_closed(self) -> bool:
        """
        Check if queues are closed.

        Returns True if streaming queue is closed (indicates client disconnect).
        The persistence queue may still be processing.
        """
        return self.streaming_queue.is_closed()

    def __iter__(self):
        """
        Iterate over streaming queue messages.

        Only the streaming queue is iterated since it's used for the HTTP response.
        The persistence queue is consumed by ThoughtConsumer in a background thread.
        """
        return self.streaming_queue.__iter__()

    def __next__(self):
        """Get next message from streaming queue."""
        return self.streaming_queue.__next__()

    def set_context(self, field: str, value: Any) -> None:
        """
        Set context field on both queues.

        Args:
            field: Context field name (e.g., 'user_id', 'execution_state_id')
            value: Context field value
        """
        # Both queues need context for proper thought association
        if hasattr(self.streaming_queue, 'context'):
            setattr(self.streaming_queue.context, field, value)
        if hasattr(self.persistence_queue, 'context'):
            setattr(self.persistence_queue.context, field, value)

    def get_from_context(self, field: str) -> Any:
        """
        Get context field value.

        Args:
            field: Context field name

        Returns:
            Context field value from persistence queue
        """
        if hasattr(self.persistence_queue, 'get_from_context'):
            return self.persistence_queue.get_from_context(field)
        return None

    @property
    def context(self):
        """
        Get context object.

        Returns context from persistence queue as it has the proper ThoughtContext structure.
        """
        return self.persistence_queue.context

    @property
    def queue(self):
        """
        Get the underlying queue for ThoughtConsumer.

        Returns the persistence queue's internal queue so ThoughtConsumer can consume
        ThoughtQueueItem objects directly.
        """
        return self.persistence_queue.queue
