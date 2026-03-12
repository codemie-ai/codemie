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
import queue
from typing import Any, Optional

from pydantic import BaseModel

from codemie.chains.base import StreamedGenerationResult, Thought


class ThoughtContext(BaseModel):
    request_uuid: Optional[str] = None
    user_id: Optional[str] = None
    execution_state_id: Optional[str] = None


class ThoughtQueueItem(BaseModel):
    data: Thought
    context: ThoughtContext


class ThoughtQueue:
    def __init__(self):
        self.queue = queue.Queue()
        self.closed = False
        self.context = ThoughtContext()

    def __iter__(self):
        return self

    def __next__(self):
        item = self.queue.get()

        if item is StopIteration:
            raise item
        return item

    def set_context(self, field: str, value: Any):
        setattr(self.context, field, value)

    def get_from_context(self, field: str) -> Any:
        return getattr(self.context, field)

    def send(self, data: str):
        json_data = json.loads(data)
        result = StreamedGenerationResult.model_validate(json_data)

        # Skip messages that don't have thought data (e.g., workflow state events)
        # ThoughtQueue only handles thought messages, not workflow state events
        if result.thought is None:
            return

        self.queue.put(
            ThoughtQueueItem(
                data=result.thought,
                context=ThoughtContext(
                    user_id=self.context.user_id,
                    request_uuid=self.context.request_uuid,
                    execution_state_id=result.context.get('execution_state_id', None) if result.context else None,
                ),
            )
        )

    def close(self):
        self.closed = True
        self.queue.put(StopIteration)

    def is_closed(self):
        return self.closed
