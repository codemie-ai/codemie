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
from typing import Protocol, Any

from codemie.core.constants import UniqueThoughtParentIds
from codemie.core.thought_queue import ThoughtContext


def _merge_routing(target: dict, source: dict) -> None:
    """Merge ``source['routing']`` non-None fields into ``target['routing']``, in place."""
    incoming = source.get('routing')
    if not incoming:
        return
    existing = target.get('routing') or {}
    target['routing'] = {**existing, **{k: v for k, v in incoming.items() if v is not None}}


class MessageQueue(Protocol):
    def __iter__(self): ...

    def __next__(self): ...

    def send(self, data: Any): ...

    def close(self, error: BaseException | None = None, *, reason: str | None = None): ...

    def is_closed(self): ...


class HedgingCancellationReason:
    """Known cancellation reasons for ThreadedGenerator.close()."""

    FAST_PATH_WON = "hedging_fast_path_won"


class ThreadedGenerator:
    def __init__(self, request_uuid: str = '', user_id: str = '', conversation_id: str = ''):
        self.queue = queue.Queue()
        self.closed = False
        self.cancellation_reason: str | None = None
        self.request_uuid = request_uuid
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.thoughts = []
        self.context = ThoughtContext(user_id=user_id, request_uuid=request_uuid)

    def __iter__(self):
        return self

    def __next__(self):
        item = self.queue.get()
        if item is StopIteration:
            raise item
        if isinstance(item, BaseException):
            raise item
        return item

    def send(self, data):
        try:
            parsed = json.loads(data)
        except Exception:
            self.queue.put(data)
            return

        thought = parsed.get('thought', {})
        self.queue.put(data)

        if thought:
            self._process_thought(thought)

    def _process_thought(self, thought):
        thought_id = thought.get('id', '')
        parent_id = thought.get('parent_id')
        is_nested = parent_id is not None
        is_nested_to_latest = thought.get('parent_id') == UniqueThoughtParentIds.LATEST.value
        message = thought.get('message') or ''
        children = thought.get('children') or []
        metadata = thought.get('metadata', {})
        output_format = thought.get('output_format')
        in_progress = thought.get('in_progress', False)

        existing_thought = next((item for item in self.thoughts if item['id'] == thought_id), None)

        if existing_thought:
            if existing_thought.get('interrupted'):
                existing_thought['message'] = message
            else:
                existing_thought['message'] += message

            existing_thought['children'] += children
            existing_thought['error'] = thought.get('error', False)
            existing_thought['aborted'] = thought.get('aborted', False)
            existing_thought['interrupted'] = thought.get('interrupted', False)
            existing_thought['metadata'] = {**existing_thought.get('metadata', {}), **metadata}
            existing_thought['output_format'] = output_format
            existing_thought['in_progress'] = in_progress
            _merge_routing(existing_thought, thought)
        else:
            thought_object = {
                'id': thought_id,
                'message': message,
                'author_name': thought.get('author_name', ''),
                'children': children,
                'author_type': thought.get('author_type', None),
                'parent_id': thought.get('parent_id', None),
                'input_text': thought.get('input_text', ''),
                'error': thought.get('error', False),
                'aborted': thought.get('aborted', False),
                'interrupted': thought.get('interrupted', False),
                'metadata': metadata,
                'output_format': output_format,
                'in_progress': in_progress,
                'routing': thought.get('routing'),
            }

            if is_nested_to_latest:
                self.thoughts.append(thought_object)
            elif is_nested:
                self._nest_to_thought(parent_id, thought_object)
            else:
                self.thoughts.append(thought_object)

    def get(self, timeout: float | None = None) -> Any:
        """Get the next item, re-raising any queued exception; raises queue.Empty on timeout."""
        item = self.queue.get(timeout=timeout) if timeout is not None else self.queue.get()
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self, error: BaseException | None = None, *, reason: str | None = None):
        if self.closed:
            return
        self.closed = True
        self.cancellation_reason = reason
        if error is not None:
            self.queue.put(error)
        self.queue.put(StopIteration)

    def is_closed(self):
        return self.closed

    def _nest_to_thought(self, parent_id, thought_object):
        existing_thougt = next(filter(lambda th: th['id'] == parent_id, self.thoughts[::-1]), None)
        if existing_thougt:
            existing_child_thought = next(
                (item for item in existing_thougt['children'] if item['id'] == thought_object['id']),
                None,
            )
            if existing_child_thought:
                existing_child_thought['message'] += thought_object['message']
                existing_child_thought['children'] += thought_object['children']
                existing_child_thought['error'] = thought_object.get('error', False)
                existing_child_thought['aborted'] = thought_object.get('aborted', False)
                existing_child_thought['metadata'] = {
                    **existing_child_thought.get('metadata', {}),
                    **thought_object.get('metadata', {}),
                }
                existing_child_thought['output_format'] = thought_object.get('output_format')
                existing_child_thought['in_progress'] = thought_object.get('in_progress', False)
                _merge_routing(existing_child_thought, thought_object)
            else:
                existing_thougt['children'].append(thought_object)

    def _nest_to_latest_thought(self, thought_object):
        latest_thought = self.thoughts[-1] if self.thoughts else None
        if latest_thought:
            existing_child_thought = next(
                (item for item in latest_thought['children'] if item['id'] == thought_object['id']),
                None,
            )
            if existing_child_thought:
                existing_child_thought['message'] += thought_object['message']
                existing_child_thought['children'] += thought_object['children']
                existing_child_thought['error'] = thought_object.get('error', False)
                existing_child_thought['aborted'] = thought_object.get('aborted', False)
                existing_child_thought['metadata'] = {
                    **existing_child_thought.get('metadata', {}),
                    **thought_object.get('metadata', {}),
                }
                existing_child_thought['output_format'] = thought_object.get('output_format')
                existing_child_thought['in_progress'] = thought_object.get('in_progress', False)
                _merge_routing(existing_child_thought, thought_object)
            else:
                latest_thought['children'].append(thought_object)
