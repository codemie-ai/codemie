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

import copy
import json
import queue
import threading
from collections.abc import Collection
from typing import Protocol, Any

from codemie.core.constants import UniqueThoughtParentIds


def _merge_routing(target: dict, source: dict) -> None:
    """Merge ``source['routing']`` non-None fields into ``target['routing']``, in place."""
    incoming = source.get('routing')
    if not incoming:
        return
    existing = target.get('routing') or {}
    target['routing'] = {**existing, **{k: v for k, v in incoming.items() if v is not None}}


def _find_thought(thoughts: list[dict[str, Any]], thought_id: str) -> dict[str, Any] | None:
    """Latest thought with this id at any depth of the step tree (depth-first, later siblings win)."""
    for thought in reversed(thoughts):
        nested = _find_thought(thought.get('children') or [], thought_id)
        if nested is not None:
            return nested
        if thought.get('id') == thought_id:
            return thought
    return None


def finalize_thoughts(thoughts: list[dict]) -> list[dict]:
    """Force in_progress=False on every thought and, recursively, every
    nested children entry. Call only on a turn's raw accumulated thought
    list once the turn has completed successfully; no other field is
    touched."""
    for thought in thoughts:
        thought['in_progress'] = False
        children = thought.get('children')
        if children:
            finalize_thoughts(children)
    return thoughts


class MessageQueue(Protocol):
    def __iter__(self): ...

    def __next__(self): ...

    def send(self, data: Any): ...

    def close(self, error: BaseException | None = None, *, reason: str | None = None): ...

    def is_closed(self): ...

    def mark_consumer_aborted(self) -> None: ...

    def is_consumer_aborted(self) -> bool: ...


class CancellationReason:
    """Known cancellation reasons for ThreadedGenerator.close()."""

    FAST_PATH_WON = "hedging_fast_path_won"
    ABORTED_BY_USER = "aborted_by_user"


# Alias for backward compatibility
HedgingCancellationReason = CancellationReason


class ThreadedGenerator:
    def __init__(self, request_uuid: str = '', user_id: str = '', conversation_id: str = ''):
        from codemie.core.thought_queue import ThoughtContext

        self.queue = queue.Queue()
        self.closed = False
        self.cancellation_reason: str | None = None
        # EPMCDME-16233: set by the supervisor's _stream_graph when the chunk-consumption loop
        # exits via an exception. Lives here, not on a LangGraphAgent instance, because every
        # sub-agent in a run is its own separate LangGraphAgent built by AssistantFactory - this
        # ThreadedGenerator is the one object already shared across all of them (see is_closed()),
        # so it is what a sibling sub-agent's pre_model_hook can actually observe.
        self.consumer_aborted = False
        self.request_uuid = request_uuid
        self.user_id = user_id
        self.conversation_id = conversation_id
        # Steps seeded from a consumed pause: the first chunk for one replaces its message instead of appending.
        self.seeded_ids: set[str] = set()
        # The seeded steps the resumed run owns (allowed and carried ones), read by the pause gate.
        self.allowed_ids: set[str] = set()
        self.thoughts = []
        self.context = ThoughtContext(user_id=user_id, request_uuid=request_uuid)
        self._close_lock = threading.Lock()
        self._history_chunks: list[str] = []
        self._subscribers: list[queue.Queue] = []
        self._sub_lock = threading.Lock()

    def seed(self, thoughts: list[dict[str, Any]], replace_ids: Collection[str]) -> None:
        """Start the run from the steps of a consumed pause, in their post-consume state."""
        self.thoughts = list(thoughts)
        self.seeded_ids = set(replace_ids)
        self.allowed_ids = set(replace_ids)

    def thoughts_after(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The thought tree as it would be after these chunks, on a copy; ``self.thoughts`` is untouched."""
        target = copy.deepcopy(self.thoughts)
        seeded_ids = set(self.seeded_ids)
        for chunk in chunks:
            self._process_thought(chunk, target, seeded_ids)
        return target

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
        with self._close_lock:
            if self.is_closed():
                return
            with self._sub_lock:
                self._history_chunks.append(data)
                for sub in self._subscribers:
                    sub.put(data)
            try:
                parsed = json.loads(data)
            except Exception:
                self.queue.put(data)
                return

            thought = parsed.get('thought', {})
            self.queue.put(data)

        if thought:
            self._process_thought(thought, self.thoughts, self.seeded_ids)

    def _process_thought(self, thought, thoughts: list[dict[str, Any]], seeded_ids: set[str]):
        thought_id = thought.get('id', '')
        parent_id = thought.get('parent_id')
        is_nested = parent_id is not None
        is_nested_to_latest = thought.get('parent_id') == UniqueThoughtParentIds.LATEST.value
        message = thought.get('message') or ''
        children = thought.get('children') or []
        metadata = thought.get('metadata', {})
        output_format = thought.get('output_format')
        in_progress = thought.get('in_progress', False)

        existing_thought = _find_thought(thoughts, thought_id)

        if existing_thought:
            if thought_id in seeded_ids:
                # The first chunk for a seeded step replaces its pause-state message; later chunks append.
                seeded_ids.discard(thought_id)
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
                thoughts.append(thought_object)
            elif is_nested:
                self._nest_to_thought(thoughts, parent_id, thought_object)
            else:
                thoughts.append(thought_object)

    def get(self, timeout: float | None = None) -> Any:
        """Get the next item, re-raising any queued exception; raises queue.Empty on timeout."""
        item = self.queue.get(timeout=timeout) if timeout is not None else self.queue.get()
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self, error: BaseException | None = None, *, reason: str | None = None):
        with self._close_lock:
            if self.closed:
                return
            self.closed = True
            self.cancellation_reason = reason
            with self._sub_lock:
                for sub in self._subscribers:
                    if error is not None:
                        sub.put(error)
                    sub.put(StopIteration)
            if error is not None:
                self.queue.put(error)
            self.queue.put(StopIteration)

    def subscribe(self) -> tuple[list[str], queue.Queue, bool]:
        """Register a subscriber queue for real-time chunk multi-casting.

        Returns:
            (history_snapshot, sub_queue, is_closed)
        """
        with self._sub_lock:
            history_snapshot = list(self._history_chunks)
            if self.closed:
                return history_snapshot, queue.Queue(), True
            sub_queue = queue.Queue()
            self._subscribers.append(sub_queue)
            return history_snapshot, sub_queue, False

    def unsubscribe(self, sub: queue.Queue) -> None:
        """Unregister a subscriber queue."""
        with self._sub_lock:
            if sub in self._subscribers:
                self._subscribers.remove(sub)

    def is_closed(self):
        return self.closed

    def mark_consumer_aborted(self) -> None:
        self.consumer_aborted = True

    def is_consumer_aborted(self) -> bool:
        return self.consumer_aborted

    def _nest_to_thought(self, thoughts, parent_id, thought_object):
        parent = _find_thought(thoughts, parent_id)
        if parent is not None:
            parent['children'].append(thought_object)

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
