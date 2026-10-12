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

"""
Saving a workflow must not require the author to own every integration.

A slot the author left to the consumer — not pinned, automatic lookup on — is resolved per user at
execution time: the person running the workflow either picks their own integration in "Your
Integration Settings" or gets one through automatic lookup. Blocking the save because the workflow
author happens to have no such integration would make that whole flow unusable.
"""

from unittest.mock import MagicMock, patch

from codemie.workflows.validation.resources import _collect_missing_for_referenced_assistant


def _assistant_with(tool_name: str, *, pinned: bool, auto: bool):
    tool = MagicMock()
    tool.name = tool_name
    tool.settings = MagicMock() if pinned else None
    tool.auto_credentials_lookup = auto
    toolkit = MagicMock()
    toolkit.tools = [tool]
    toolkit.settings = None
    toolkit.auto_credentials_lookup = True
    db_assistant = MagicMock()
    db_assistant.id = "assistant-1"
    db_assistant.toolkits = [toolkit]
    return db_assistant


def _missing(tool_name: str):
    missing = MagicMock()
    missing.tool = tool_name
    missing.credential_type = "Confluence"
    missing.toolkit = "Project Management"
    return missing


def _workflow_ref():
    workflow_config = MagicMock()
    workflow_config.project = "project-1"
    assistant_ref = MagicMock()
    assistant_ref.id = "assistant_1"
    assistant_ref.assistant_id = "assistant-1"
    return workflow_config, assistant_ref


def _collect(db_assistant, missing_list):
    workflow_config, assistant_ref = _workflow_ref()
    with (
        patch(
            "codemie.workflows.validation.resources._load_referenced_assistant",
            return_value=db_assistant,
        ),
        patch(
            "codemie.workflows.validation.resources._safely_collect_missing_integrations",
            return_value=missing_list,
        ),
        patch(
            "codemie.workflows.validation.resources._find_states_referencing_assistant",
            return_value=["state-1"],
        ),
    ):
        return _collect_missing_for_referenced_assistant(workflow_config, MagicMock(), assistant_ref)


def test_slot_left_to_the_user_does_not_block_saving():
    db_assistant = _assistant_with("generic_confluence_tool", pinned=False, auto=True)

    assert _collect(db_assistant, [_missing("generic_confluence_tool")]) == []


def test_slot_with_lookup_disabled_does_not_block_saving_either():
    # Automatic lookup being off does not make the slot the author's problem: it is still offered in
    # "Your Integration Settings", so the person running the workflow can pick their own integration.
    db_assistant = _assistant_with("generic_confluence_tool", pinned=False, auto=False)

    assert _collect(db_assistant, [_missing("generic_confluence_tool")]) == []


def test_slot_with_lookup_disabled_is_reported_as_a_warning():
    from codemie.workflows.validation.resources import collect_consumer_slot_integration_warnings

    workflow_config, assistant_ref = _workflow_ref()
    workflow_config.assistants = [assistant_ref]
    db_assistant = _assistant_with("generic_confluence_tool", pinned=False, auto=False)

    with (
        patch(
            "codemie.workflows.validation.resources._load_referenced_assistant",
            return_value=db_assistant,
        ),
        patch(
            "codemie.workflows.validation.resources._safely_collect_missing_integrations",
            return_value=[_missing("generic_confluence_tool")],
        ),
    ):
        warnings = collect_consumer_slot_integration_warnings(workflow_config, MagicMock())

    assert [w["tool_name"] for w in warnings] == ["generic_confluence_tool"]


def test_slot_inside_a_sub_assistant_does_not_block_saving():
    # Missing integrations are collected for the orchestrator AND its sub-assistants, so a slot left
    # to the consumer must be recognised as such wherever it lives — otherwise the save is still
    # blocked for exactly the setup this behaviour exists to allow.
    orchestrator = _assistant_with("generic_jira_tool", pinned=False, auto=True)
    orchestrator.assistant_ids = ["sub-1"]
    sub_assistant = _assistant_with("generic_confluence_tool", pinned=False, auto=True)
    sub_assistant.id = "sub-1"
    sub_assistant.assistant_ids = []

    workflow_config, assistant_ref = _workflow_ref()
    with (
        patch(
            "codemie.workflows.validation.resources._load_referenced_assistant",
            side_effect=lambda _user, assistant_id: sub_assistant if assistant_id == "sub-1" else orchestrator,
        ),
        patch(
            "codemie.workflows.validation.resources._safely_collect_missing_integrations",
            return_value=[_missing("generic_confluence_tool")],
        ),
        patch(
            "codemie.workflows.validation.resources._find_states_referencing_assistant",
            return_value=["state-1"],
        ),
    ):
        reported = _collect_missing_for_referenced_assistant(workflow_config, MagicMock(), assistant_ref)

    assert reported == []


def test_unknown_tool_is_still_reported():
    # A tool that is not among the assistant's slots cannot be "left to the user".
    db_assistant = _assistant_with("generic_jira_tool", pinned=False, auto=True)

    reported = _collect(db_assistant, [_missing("generic_confluence_tool")])

    assert [entry[0] for entry in reported] == ["generic_confluence_tool"]


def test_consumer_slots_are_reported_as_warnings():
    # Not an error — the save goes through — but the author should still learn that these slots have
    # no integration of their own and depend on each user's setup.
    from codemie.workflows.validation.resources import collect_consumer_slot_integration_warnings

    workflow_config, assistant_ref = _workflow_ref()
    workflow_config.assistants = [assistant_ref]
    db_assistant = _assistant_with("generic_confluence_tool", pinned=False, auto=True)

    with (
        patch(
            "codemie.workflows.validation.resources._load_referenced_assistant",
            return_value=db_assistant,
        ),
        patch(
            "codemie.workflows.validation.resources._safely_collect_missing_integrations",
            return_value=[_missing("generic_confluence_tool")],
        ),
    ):
        warnings = collect_consumer_slot_integration_warnings(workflow_config, MagicMock())

    assert len(warnings) == 1
    assert warnings[0]["tool_name"] == "generic_confluence_tool"
    assert warnings[0]["credential_type"] == "Confluence"
    assert warnings[0]["assistant_ref"] == "assistant_1"


def test_no_warning_when_the_author_pinned_the_slot():
    from codemie.workflows.validation.resources import collect_consumer_slot_integration_warnings

    workflow_config, assistant_ref = _workflow_ref()
    workflow_config.assistants = [assistant_ref]
    db_assistant = _assistant_with("generic_confluence_tool", pinned=True, auto=True)

    with (
        patch(
            "codemie.workflows.validation.resources._load_referenced_assistant",
            return_value=db_assistant,
        ),
        patch(
            "codemie.workflows.validation.resources._safely_collect_missing_integrations",
            return_value=[_missing("generic_confluence_tool")],
        ),
    ):
        assert collect_consumer_slot_integration_warnings(workflow_config, MagicMock()) == []


def test_consumer_slot_warning_carries_kind_and_keeps_its_shape():
    from codemie.workflows.validation.resources import collect_consumer_slot_integration_warnings

    workflow_config, assistant_ref = _workflow_ref()
    workflow_config.assistants = [assistant_ref]
    db_assistant = _assistant_with("generic_confluence_tool", pinned=False, auto=True)

    with (
        patch("codemie.workflows.validation.resources._load_referenced_assistant", return_value=db_assistant),
        patch(
            "codemie.workflows.validation.resources._safely_collect_missing_integrations",
            return_value=[_missing("generic_confluence_tool")],
        ),
    ):
        warnings = collect_consumer_slot_integration_warnings(workflow_config, MagicMock())

    assert warnings == [
        {
            "kind": "consumer_slot",
            "assistant_ref": "assistant_1",
            "tool_name": "generic_confluence_tool",
            "credential_type": "Confluence",
        }
    ]


def _script_workflow(access):
    from codemie.core.workflow_models.workflow_models import WorkflowAssistantTool

    tool = MagicMock()
    tool.id = "script_tool"
    tool.tool = "execute_workspace_script"
    tool.tool_access = [WorkflowAssistantTool(name=n, integration_alias=a) for n, a in access]
    state = MagicMock()
    state.id = "step-1"
    state.tool_id = "script_tool"
    workflow_config = MagicMock()
    workflow_config.tools = [tool]
    workflow_config.states = [state]
    workflow_config.is_global = False
    return workflow_config


def test_tool_access_owner_integration_warning_for_proposed_global():
    from codemie.workflows.validation.tool_access import collect_tool_access_owner_integration_warnings

    config = _script_workflow([("jira_tool", "work"), ("git_tool", None)])

    warnings = collect_tool_access_owner_integration_warnings(config, treat_as_global=True)

    assert len(warnings) == 1
    item = warnings[0]
    assert item["kind"] == "tool_access_owner_integration"
    assert item["step_id"] == "step-1"
    assert item["tool_name"] == "jira_tool"
    assert item["integration_alias"] == "work"
    assert "author" in item["message"]


def test_tool_access_owner_integration_warning_absent_when_not_global():
    from codemie.workflows.validation.tool_access import collect_tool_access_owner_integration_warnings

    config = _script_workflow([("jira_tool", "work")])

    assert collect_tool_access_owner_integration_warnings(config, treat_as_global=False) == []


def test_tool_access_owner_integration_warning_absent_without_aliases():
    from codemie.workflows.validation.tool_access import collect_tool_access_owner_integration_warnings

    config = _script_workflow([("jira_tool", None)])

    assert collect_tool_access_owner_integration_warnings(config, treat_as_global=True) == []
