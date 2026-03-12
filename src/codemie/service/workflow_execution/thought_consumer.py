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

import threading

from codemie.chains.base import Thought
from codemie.core.thread import ThreadedGenerator
from codemie.core.workflow_models import WorkflowExecutionStateThought
from codemie.configs import logger
from codemie.core.thought_queue import ThoughtQueueItem, ThoughtContext


class ThoughtConsumer:
    """Consumes thoughts from the thought queue and saves them to the database"""

    @staticmethod
    def run(execution_id: str, message_queue: ThreadedGenerator):
        instance = ThoughtConsumer(workflow_execution_id=execution_id, message_queue=message_queue)

        thread = threading.Thread(target=instance.consume)
        thread.start()

    def __init__(self, workflow_execution_id: str, message_queue: ThreadedGenerator) -> None:
        self.workflow_execution_id = workflow_execution_id
        self.message_queue = message_queue
        self.cache = {}

    def consume(self):
        while True:
            if not hasattr(self.message_queue, "queue"):
                logger.debug("ThoughtConsumer: No message queue found")
                break

            value = self.message_queue.queue.get()

            if isinstance(value, StopIteration):
                self.message_queue.queue.task_done()
                break

            if isinstance(value, ThoughtQueueItem):
                if not value.context.execution_state_id:
                    logger.debug("ThoughtConsumer: Skipping thought, no execution state id found in context")
                    continue

                thought_data: Thought = value.data
                context: ThoughtContext = value.context

                self._update_thought_cache(thought_data)

                if thought_data.in_progress:
                    continue

                thought = WorkflowExecutionStateThought(
                    id=thought_data.id,
                    execution_state_id=context.execution_state_id,
                    parent_id=thought_data.parent_id,
                    content=self.cache[thought_data.id],
                    author_name=thought_data.author_name,
                    author_type=thought_data.author_type,
                    input_text=thought_data.input_text,
                )
                thought.save(refresh=True)
                self.cache.pop(thought_data.id)

    def _update_thought_cache(self, thought_data: Thought):
        """Update the cache with the thought data"""
        if thought_data.id not in self.cache:
            self.cache[thought_data.id] = ''

        self.cache[thought_data.id] += thought_data.message
