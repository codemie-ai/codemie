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

from __future__ import annotations

import json

import pytest

from codemie.core import tool_step_states as states

STEP = {'id': 's', 'message': 'Confirm?', 'children': [{'id': 'c'}], 'metadata': {'k': 1}, 'in_progress': True}


def _fields(step):
    return step['interrupted'], step['aborted'], step['in_progress'], step['message']


@pytest.mark.parametrize(
    'builder, expected',
    [
        (states.waiting, (True, False, False, states.WAITING_MESSAGE)),
        (states.allowed, (False, False, True, '')),
        (states.denied, (False, True, False, states.DENIED_MESSAGE)),
        (states.cancelled, (False, True, False, states.CANCELLED_MESSAGE)),
    ],
)
def test_builder_yields_spec_table_fields(builder, expected):
    assert _fields(builder({**STEP, 'interrupted': False, 'aborted': False})) == expected


@pytest.mark.parametrize('builder', [states.waiting, states.allowed, states.denied, states.cancelled])
def test_builder_returns_new_dict_and_keeps_other_fields(builder):
    result = builder(STEP)
    assert result is not STEP
    assert STEP['message'] == 'Confirm?' and 'interrupted' not in STEP
    assert result['id'] == 's' and result['children'] == [{'id': 'c'}] and result['metadata'] == {'k': 1}


def test_messages():
    assert states.DENIED_MESSAGE == 'Tool call was denied by the user.'
    assert states.CANCELLED_MESSAGE == 'Tool call was cancelled without a decision.'


def test_waiting_chunk_is_the_waiting_step():
    from types import SimpleNamespace

    from codemie.agents.tool_confirmation.models import DecisionItem
    from codemie.agents.tool_confirmation.tool_call_confirmation_mixin import ToolCallConfirmationMixin

    item = DecisionItem.model_construct(
        thought_id='t1',
        event=SimpleNamespace(tool_name='tool', tool_args={'a': 1}, location=None),
    )
    mixin = SimpleNamespace(_find_thought=lambda _id: None)

    thought = json.loads(ToolCallConfirmationMixin._waiting_chunk(mixin, item))['thought']

    assert (thought['interrupted'], thought['in_progress'], thought['message']) == (
        True,
        False,
        states.WAITING_MESSAGE,
    )
