# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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
import queue as queue_module
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.conversation import GeneratedMessage
from codemie.rest_api.routers.utils import (
    _serve_workflow_stream,
    raise_access_denied,
    raise_not_found,
    raise_unprocessable_entity,
)
from codemie.utils.datetime_utils import get_timestamp_bounds


def test_raise_access_denied():
    action = "delete"

    with pytest.raises(ExtendedHTTPException) as exc_info:
        raise_access_denied(action)

    exception = exc_info.value
    assert exception.code == status.HTTP_401_UNAUTHORIZED
    assert exception.message == "Access denied"
    assert exception.details == "You do not have the necessary permissions to delete this entity."
    assert "Please ensure you have the correct role or permissions" in exception.help
    assert "contact your system administrator" in exception.help


def test_raise_unprocessable_entity():
    action = "create"
    resource = "user"
    original_exception = ValueError("Invalid email format")

    with pytest.raises(ExtendedHTTPException) as exc_info:
        raise_unprocessable_entity(action, resource, original_exception)

    exception = exc_info.value
    assert exception.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert exception.message == "Failed to create a user"
    assert exception.details == "An error occurred while trying to create a user: Invalid email format"
    assert "Please check your request format" in exception.help
    assert "contact support" in exception.help
    assert exc_info.value.__cause__ == original_exception


def test_raise_not_found():
    resource_id = "user123"
    resource_type = "User"

    with pytest.raises(ExtendedHTTPException) as exc_info:
        raise_not_found(resource_id, resource_type)

    exception = exc_info.value
    assert exception.code == status.HTTP_404_NOT_FOUND
    assert exception.message == "User not found"
    assert exception.details == "The User with ID [user123] could not be found in the system."
    assert exception.help == "Please ensure the specified ID is correct"


def test_get_timestamp_bounds_empty():
    assert get_timestamp_bounds([]) == (None, None)
    assert get_timestamp_bounds(None) == (None, None)


def test_get_timestamp_bounds_single_message():
    dt = datetime(2023, 1, 1, 12, 0, 0)
    dt_utc = dt.replace(tzinfo=timezone.utc)
    history = [GeneratedMessage(message="msg", role="User", date=dt)]
    assert get_timestamp_bounds(history) == (dt_utc, dt_utc)


def test_get_timestamp_bounds_multiple_messages():
    dt1 = datetime(2023, 1, 1, 12, 0, 0)
    dt2 = datetime(2023, 1, 1, 12, 1, 0)
    dt3 = datetime(2023, 1, 1, 12, 5, 0)
    history = [
        GeneratedMessage(message="msg1", role="User", date=dt1),
        GeneratedMessage(message="msg2", role="Assistant", date=dt2),
        GeneratedMessage(message="msg3", role="User", date=dt3),
    ]
    assert get_timestamp_bounds(history) == (
        dt1.replace(tzinfo=timezone.utc),
        dt3.replace(tzinfo=timezone.utc),
    )


def test_get_timestamp_bounds_messages_without_date():
    dt = datetime(2023, 1, 1, 12, 0, 0)
    history = [
        GeneratedMessage(message="msg1", role="User", date=None),
        GeneratedMessage(message="msg2", role="Assistant", date=dt),
        GeneratedMessage(message="msg3", role="User"),
    ]
    assert get_timestamp_bounds(history) == (dt.replace(tzinfo=timezone.utc), dt.replace(tzinfo=timezone.utc))


def test_get_timestamp_bounds_mixed_naive_and_aware_utc():
    dt_naive = datetime(2023, 1, 1, 10, 0, 0)
    dt_aware = datetime(2023, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
    history = [
        GeneratedMessage(message="a", role="User", date=dt_naive),
        GeneratedMessage(message="b", role="Assistant", date=dt_aware),
    ]
    assert get_timestamp_bounds(history) == (
        dt_naive.replace(tzinfo=timezone.utc),
        dt_aware,
    )


def test_get_timestamp_bounds_all_messages_without_date():
    history = [
        GeneratedMessage(message="msg1", role="User", date=None),
        GeneratedMessage(message="msg3", role="User"),
    ]
    assert get_timestamp_bounds(history) == (None, None)


def test_get_timestamp_bounds_dict_messages():
    """Dict-based messages (raw JSONB-decoded) are handled via dict key access, not getattr."""
    dt1 = datetime(2023, 6, 1, 8, 0, 0, tzinfo=timezone.utc)
    dt2 = datetime(2023, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
    dt3 = datetime(2023, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    history = [
        {"role": "User", "message": "m1", "date": dt2},
        {"role": "Assistant", "message": "m2", "date": dt1},
        {"role": "User", "message": "m3", "date": dt3},
        {"role": "User", "message": "m4"},  # no "date" key — should be skipped
    ]
    assert get_timestamp_bounds(history) == (dt1, dt3)


def test_get_timestamp_bounds_dict_messages_no_dates():
    """Dict-based messages with no date values return (None, None)."""
    history = [
        {"role": "User", "message": "m1"},
        {"role": "Assistant", "message": "m2", "date": None},
    ]
    assert get_timestamp_bounds(history) == (None, None)


# ---------------------------------------------------------------------------
# _serve_workflow_stream tests
# ---------------------------------------------------------------------------


def _make_thought_json(message: str) -> str:
    """Build a minimal thought JSON line matching what the workflow puts on the queue."""
    return json.dumps({"thought": {"id": "t1", "message": message, "author_type": "Agent"}})


def _run_stream(workflow, generator_queue, producer_fn):
    """
    Helper: patches threading.Thread so that thread.start() runs producer_fn
    synchronously (puts items on the queue before the generator loop runs),
    then drains the generator and returns all yielded lines.
    """
    with patch("codemie.rest_api.routers.utils.threading.Thread") as mock_thread_cls:
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread
        mock_thread.start.side_effect = lambda: producer_fn()
        return list(_serve_workflow_stream(workflow, generator_queue))


class _FakeGeneratorQueue:
    """Minimal stand-in for ThreadedGenerator — only exposes the queue attribute used by _serve_workflow_stream."""

    def __init__(self):
        self.queue = queue_module.Queue()

    def close(self):
        self.queue.put(StopIteration)


@pytest.fixture
def generator_queue():
    return _FakeGeneratorQueue()


@pytest.fixture
def mock_workflow():
    return MagicMock()


def test_serve_workflow_stream_passes_through_ndjson_chunks(generator_queue, mock_workflow):
    """Each thought message received from the queue is yielded as a raw NDJSON line."""
    msg1 = _make_thought_json("Hello")
    msg2 = _make_thought_json("World")

    def _produce():
        generator_queue.queue.put(msg1)
        generator_queue.queue.put(msg2)
        generator_queue.close()

    lines = _run_stream(mock_workflow, generator_queue, _produce)

    assert lines[0] == f"{msg1}\n"
    assert lines[1] == f"{msg2}\n"


def test_serve_workflow_stream_final_chunk_has_last_true(generator_queue, mock_workflow):
    """The last yielded line always contains last=True."""

    def _produce():
        generator_queue.queue.put(_make_thought_json("some output"))
        generator_queue.close()

    lines = _run_stream(mock_workflow, generator_queue, _produce)

    last_chunk = json.loads(lines[-1])
    assert last_chunk["last"] is True


def test_serve_workflow_stream_final_chunk_generated_matches_last_thought(generator_queue, mock_workflow):
    """The final chunk's generated field contains the last thought's message text."""
    expected_text = "The answer is 42"

    def _produce():
        generator_queue.queue.put(_make_thought_json("intermediate"))
        generator_queue.queue.put(_make_thought_json(expected_text))
        generator_queue.close()

    lines = _run_stream(mock_workflow, generator_queue, _produce)

    last_chunk = json.loads(lines[-1])
    assert last_chunk["generated"] == expected_text


def test_serve_workflow_stream_zero_thoughts_final_chunk_generated_empty(generator_queue, mock_workflow):
    """When no thoughts are emitted before StopIteration (e.g. early failure),
    last=True is still sent and generated is an empty string — no UnboundLocalError."""

    def _produce():
        generator_queue.close()  # StopIteration is the very first item

    lines = _run_stream(mock_workflow, generator_queue, _produce)

    assert len(lines) == 1
    last_chunk = json.loads(lines[0])
    assert last_chunk["last"] is True
    assert last_chunk["generated"] == ""


def test_serve_workflow_stream_starts_and_joins_thread(generator_queue, mock_workflow):
    """Background thread is created with stream_to_client as target and joined with timeout=1."""

    def _produce():
        generator_queue.close()

    with patch("codemie.rest_api.routers.utils.threading.Thread") as mock_thread_cls:
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread
        mock_thread.start.side_effect = lambda: _produce()

        list(_serve_workflow_stream(mock_workflow, generator_queue))

    mock_thread_cls.assert_called_once_with(target=mock_workflow.stream_to_client)
    mock_thread.start.assert_called_once()
    mock_thread.join.assert_called_once_with(timeout=1)
