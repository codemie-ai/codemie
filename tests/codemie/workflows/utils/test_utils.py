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

"""Tests for retrieval-backend gating in codemie.workflows.utils.utils."""

from unittest.mock import Mock, patch

import pytest

from codemie.workflows.utils.utils import get_documents_tree_by_datasource_id


@patch("codemie.workflows.utils.utils.ElasticSearchClient")
@patch("codemie.workflows.utils.utils.IndexInfo")
def test_get_documents_tree_by_datasource_id_raises_when_retrieval_unavailable(mock_index_info, mock_es_client):
    mock_es_client.is_configured.return_value = False

    with pytest.raises(ValueError):
        get_documents_tree_by_datasource_id("ds-id")

    mock_index_info.find_by_id.assert_not_called()
    mock_es_client.get_client.assert_not_called()


@patch("codemie.workflows.utils.utils.ElasticSearchClient")
@patch("codemie.workflows.utils.utils.IndexInfo")
def test_get_documents_tree_by_datasource_id_returns_documents_when_retrieval_available(
    mock_index_info, mock_es_client
):
    mock_es_client.is_configured.return_value = True

    datasource = Mock()
    datasource.get_index_identifier.return_value = "repo-1"
    datasource.is_code_index.return_value = False
    datasource.is_google_doc_index.return_value = False
    mock_index_info.find_by_id.return_value = datasource

    mock_es_client.get_client.return_value.search.return_value = {
        "hits": {
            "hits": [
                {"_source": {"metadata": {"source": "doc1"}}},
            ]
        }
    }

    result = get_documents_tree_by_datasource_id("ds-id")

    assert result == [{"source": "doc1"}]
    mock_index_info.find_by_id.assert_called_once_with("ds-id")


class TestInitializeAssistantHeadroomLayers:
    """initialize_assistant forwards the node and workflow Headroom overrides as one grouped argument."""

    def test_forwards_node_and_workflow_layers(self):
        from codemie.core.workflow_models import WorkflowAssistant
        from codemie.service.headroom.scope import WorkflowHeadroomLayers
        from codemie.service.headroom.models import HeadroomOverride
        from codemie.workflows.utils.utils import AssistantExecutionOptions, initialize_assistant

        node = HeadroomOverride(enabled=True)
        workflow = HeadroomOverride(strategy="aggressive")

        with patch("codemie.workflows.utils.utils.AssistantService.build_agent_for_workflow") as build:
            initialize_assistant(
                user_input="hi",
                user=Mock(),
                workflow_assistant=WorkflowAssistant(id="a", headroom=node),
                options=AssistantExecutionOptions(headroom_workflow=workflow),
            )

        assert build.call_args.kwargs["headroom_layers"] == WorkflowHeadroomLayers(node=node, workflow=workflow)

    def test_defaults_give_empty_layers(self):
        from codemie.core.workflow_models import WorkflowAssistant
        from codemie.service.headroom.scope import WorkflowHeadroomLayers
        from codemie.workflows.utils.utils import initialize_assistant

        with patch("codemie.workflows.utils.utils.AssistantService.build_agent_for_workflow") as build:
            initialize_assistant(user_input="hi", user=Mock(), workflow_assistant=WorkflowAssistant(id="a"))

        assert build.call_args.kwargs["headroom_layers"] == WorkflowHeadroomLayers()

    def test_mock_workflow_assistant_without_headroom_attribute(self):
        from codemie.core.workflow_models import WorkflowAssistant
        from codemie.service.headroom.scope import WorkflowHeadroomLayers
        from codemie.workflows.utils.utils import initialize_assistant

        with patch("codemie.workflows.utils.utils.AssistantService.build_agent_for_workflow") as build:
            initialize_assistant(user_input="hi", user=Mock(), workflow_assistant=Mock(spec=WorkflowAssistant))

        assert build.call_args.kwargs["headroom_layers"] == WorkflowHeadroomLayers()


class TestWorkflowHeadroomOverride:
    def test_returns_override_only_for_headroom_override(self):
        from codemie.core.workflow_models import WorkflowConfig
        from codemie.service.headroom.models import HeadroomOverride
        from codemie.workflows.utils.utils import workflow_headroom_override

        override = HeadroomOverride(enabled=True)

        assert (
            workflow_headroom_override(WorkflowConfig(name="n", description="d", headroom_config=override)) == override
        )
        assert workflow_headroom_override(WorkflowConfig(name="n", description="d")) is None
        assert workflow_headroom_override(Mock()) is None
        assert workflow_headroom_override(Mock(spec=WorkflowConfig)) is None
        assert workflow_headroom_override(None) is None
