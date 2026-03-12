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

import pytest
import queue
from unittest.mock import patch

from codemie.core.thread import ThreadedGenerator


@pytest.fixture
def generator():
    return ThreadedGenerator(request_uuid='request_uuid', user_id='user_id', conversation_id='conversation_id')


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


def test_is_closed(generator):
    assert not generator.is_closed()

    generator.closed = True
    assert generator.is_closed()


def test_close(generator):
    with patch.object(generator, 'queue'):
        generator.close()
        assert generator.closed
