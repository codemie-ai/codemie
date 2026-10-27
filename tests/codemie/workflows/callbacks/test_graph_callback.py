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

"""EPMCDME-14145: LanggraphNodeCallback streams truncated on WorkflowState thoughts."""

import json
from unittest.mock import MagicMock

from codemie.workflows.callbacks.graph_callback import LanggraphNodeCallback


def test_on_node_end_sets_truncation_signal_on_streamed_thought():
    gen = MagicMock()
    callback = LanggraphNodeCallback(gen=gen)
    callback.set_current_thought("state-1", "agent_name")

    callback.on_node_end(
        output="cut off output",
        execution_state_id="state-1",
        execution_context={},
        truncated=True,
    )

    sent_payload = json.loads(gen.send.call_args_list[-1].args[0])
    assert sent_payload["thought"]["truncated"] is True


def test_on_node_end_defaults_truncation_signal_when_absent():
    gen = MagicMock()
    callback = LanggraphNodeCallback(gen=gen)
    callback.set_current_thought("state-1", "agent_name")

    callback.on_node_end(output="ok", execution_state_id="state-1", execution_context={})

    sent_payload = json.loads(gen.send.call_args_list[-1].args[0])
    assert sent_payload["thought"]["truncated"] is False
