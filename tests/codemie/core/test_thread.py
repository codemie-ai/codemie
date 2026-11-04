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
import threading

import pytest
import queue
from unittest.mock import patch

from codemie.core.thread import ThreadedGenerator, finalize_thoughts


@pytest.fixture
def generator():
    return ThreadedGenerator(request_uuid='request_uuid', user_id='user_id', conversation_id='conversation_id')


def test_finalize_thoughts_forces_top_level_in_progress_false():
    thoughts = [{'id': 't1', 'in_progress': True, 'children': []}]
    assert finalize_thoughts(thoughts)[0]['in_progress'] is False


def test_finalize_thoughts_forces_nested_children_in_progress_false():
    thoughts = [{'id': 't1', 'in_progress': False, 'children': [{'id': 'c1', 'in_progress': True, 'children': []}]}]
    finalize_thoughts(thoughts)
    assert thoughts[0]['children'][0]['in_progress'] is False


def test_finalize_thoughts_handles_missing_children_key():
    thoughts = [{'id': 't1', 'in_progress': True}]
    finalize_thoughts(thoughts)
    assert thoughts[0]['in_progress'] is False


def test_finalize_thoughts_leaves_other_fields_untouched():
    thoughts = [{'id': 't1', 'in_progress': True, 'error': True, 'aborted': True, 'interrupted': False}]
    finalize_thoughts(thoughts)
    assert thoughts[0] == {'id': 't1', 'in_progress': False, 'error': True, 'aborted': True, 'interrupted': False}


def test_process_thought_leaves_orphaned_thought_in_progress_true_when_closing_event_never_arrives(generator):
    # Mirrors a real streaming turn: one thought's start+end events both arrive normally;
    # a second thought (e.g. a "CodeMie Thoughts" generic LLM entry) only ever gets its
    # start event — its closing event is lost, the same LangChain callback-lifecycle
    # unreliability documented in EPMCDME-14850. This reads generator.thoughts directly,
    # pre-finalize-helper, to prove the raw accumulator itself holds the stale value.
    generator.send(json.dumps({'thought': {'id': 'resolved', 'in_progress': True, 'message': 'start'}}))
    generator.send(json.dumps({'thought': {'id': 'resolved', 'in_progress': False, 'message': ' end'}}))
    generator.send(json.dumps({'thought': {'id': 'orphaned', 'in_progress': True, 'message': 'start'}}))
    # no closing event ever sent for 'orphaned'

    resolved = next(t for t in generator.thoughts if t['id'] == 'resolved')
    orphaned = next(t for t in generator.thoughts if t['id'] == 'orphaned')
    assert resolved['in_progress'] is False
    assert orphaned['in_progress'] is True


def test_init(generator):
    assert generator.request_uuid == 'request_uuid'
    assert generator.user_id == 'user_id'
    assert generator.conversation_id == 'conversation_id'
    assert not generator.closed
    assert isinstance(generator.queue, queue.Queue)


def test_iter(generator):
    generator = ThreadedGenerator()
    assert iter(generator) == generator


def test_next(generator):
    with patch.object(generator, 'queue') as mock_queue:
        mock_queue.get.return_value = 'item'
        assert next(generator) == 'item'

        mock_queue.get.return_value = StopIteration
        with pytest.raises(StopIteration):
            next(generator)


def test_send(generator):
    with patch.object(generator, 'queue') as mock_queue:
        generator.send('data')
        mock_queue.put.assert_called_once_with('data')


def test_send_after_close_is_noop(generator):
    generator.close()
    with patch.object(generator, 'queue') as mock_queue:
        generator.send('{"thought": {"id": "t1", "message": "x"}}')
        mock_queue.put.assert_not_called()


def test_send_after_close_is_noop_for_non_json_payload(generator):
    generator.close()
    with patch.object(generator, 'queue') as mock_queue:
        generator.send('not-json')
        mock_queue.put.assert_not_called()


def test_send_after_close_does_not_process_thought(generator):
    generator.close()
    with patch.object(generator, '_process_thought') as mock_process:
        generator.send('{"thought": {"id": "t1", "message": "x"}}')
        mock_process.assert_not_called()


def test_is_closed(generator):
    assert not generator.is_closed()

    generator.closed = True
    assert generator.is_closed()


def test_close(generator):
    with patch.object(generator, 'queue'):
        generator.close()
        assert generator.closed


def test_send_and_close_are_synchronized_against_a_late_put_after_stop_iteration(generator):
    """Round-2 CR-009: send()'s is_closed() check and its queue.put() must be atomic
    with close()'s own closed=True + put(StopIteration) -- otherwise a send() that
    observes is_closed()==False right before a concurrent close() call can still
    enqueue its data AFTER the StopIteration sentinel, where no consumer will ever
    read it (the consumer loop already stopped at StopIteration)."""
    checked_closed = threading.Event()
    release_sender = threading.Event()

    original_is_closed = generator.is_closed

    def paused_is_closed():
        result = original_is_closed()
        checked_closed.set()
        release_sender.wait(timeout=2)
        return result

    generator.is_closed = paused_is_closed

    payload = '{"foo": "late-item"}'
    sender = threading.Thread(target=lambda: generator.send(payload), daemon=True)
    sender.start()
    assert checked_closed.wait(timeout=2), "sender never reached its is_closed() check"

    closer = threading.Thread(target=generator.close, daemon=True)
    closer.start()

    release_sender.set()
    sender.join(timeout=2)
    closer.join(timeout=2)

    drained = []
    try:
        while True:
            drained.append(generator.queue.get_nowait())
    except queue.Empty:
        pass

    assert drained.index(payload) < drained.index(StopIteration), (
        "the late item was enqueued after StopIteration, where a real consumer "
        "(which stops reading at StopIteration) would never see it"
    )


def test_subscribe_replays_history_and_receives_live_chunks(generator):
    generator.send('chunk_1')
    generator.send('chunk_2')

    history, sub_q, is_closed = generator.subscribe()
    assert history == ['chunk_1', 'chunk_2']
    assert not is_closed

    generator.send('chunk_3')
    assert sub_q.get_nowait() == 'chunk_3'

    generator.close()
    assert sub_q.get_nowait() is StopIteration


def test_subscribe_on_closed_generator(generator):
    generator.send('chunk_1')
    generator.close()

    history, sub_q, is_closed = generator.subscribe()
    assert history == ['chunk_1']
    assert is_closed is True


def test_unsubscribe(generator):
    history, sub_q, is_closed = generator.subscribe()
    assert sub_q in generator._subscribers

    generator.unsubscribe(sub_q)
    assert sub_q not in generator._subscribers

    generator.send('chunk_after_unsub')
    assert sub_q.empty()


def test_send_after_close_does_not_reach_subscribers(generator):
    history, sub_q, is_closed = generator.subscribe()
    generator.close()
    assert sub_q.get_nowait() is StopIteration

    generator.send('chunk_after_close')
    assert sub_q.empty()
    assert generator._history_chunks == []
