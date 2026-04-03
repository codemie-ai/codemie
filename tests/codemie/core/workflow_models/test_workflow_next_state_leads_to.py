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


from codemie.core.workflow_models.workflow_models import (
    WorkflowNextState,
    WorkflowStateCondition,
    WorkflowStateSwitch,
    WorkflowStateSwitchCondition,
)


def test_leads_to_state_id():
    nxt = WorkflowNextState(state_id="b")
    assert nxt.leads_to() == {"b"}


def test_leads_to_state_ids():
    nxt = WorkflowNextState(state_ids=["b", "c"])
    assert nxt.leads_to() == {"b", "c"}


def test_leads_to_condition():
    nxt = WorkflowNextState(condition=WorkflowStateCondition(expression="x > 0", then="b", otherwise="c"))
    assert nxt.leads_to() == {"b", "c"}


def test_leads_to_switch():
    nxt = WorkflowNextState(
        switch=WorkflowStateSwitch(
            cases=[
                WorkflowStateSwitchCondition(condition="x == 1", state_id="b"),
                WorkflowStateSwitchCondition(condition="x == 2", state_id="c"),
            ],
            default="d",
        )
    )
    assert nxt.leads_to() == {"b", "c", "d"}


def test_leads_to_membership():
    nxt = WorkflowNextState(state_id="b")
    nxt2 = WorkflowNextState(state_ids=["x"])
    assert "b" in nxt.leads_to()
    assert "missing" not in nxt2.leads_to()
