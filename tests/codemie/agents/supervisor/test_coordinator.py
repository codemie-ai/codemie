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

from uuid import uuid4

from codemie.agents.supervisor.coordinator import SupervisorCoordinator, _SupervisorHandoffTracker


def _make_coordinator() -> SupervisorCoordinator:
    return SupervisorCoordinator(
        handoff_tool_prefix="transfer_to_",
        extract_agent_name_from_tool=lambda name: name,
        tool_call_id_to_uuid=lambda call_id: uuid4(),
        resolve_display_name=lambda name, a, b: name,
        emit_handoff=lambda *a, **k: None,
        emit_subassistant_back=lambda *a, **k: None,
        set_thread_context=lambda *a, **k: None,
    )


def test_tracker_reset_clears_all_state():
    tracker = _SupervisorHandoffTracker()
    handoff = (uuid4(), "sub_agent", "do a thing", None)
    tracker.queue_pending("sub_agent", handoff)
    tracker.activate("sub_agent", "sub_agent:1", handoff)

    tracker.reset()

    assert tracker.active == {}
    assert tracker.pending == {}
    assert tracker.run_bindings == {}


def test_coordinator_reset_clears_dangling_active_handoff():
    coordinator = _make_coordinator()
    handoff = (uuid4(), "sub_agent", "do a thing", None)
    coordinator.tracker.activate("sub_agent", "sub_agent:1", handoff)
    assert coordinator.binding_for("sub_agent:1") is not None

    coordinator.reset()

    assert coordinator.binding_for("sub_agent:1") is None
