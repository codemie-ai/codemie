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

from unittest.mock import Mock, patch

import pytest

from codemie.configs import config

pytestmark = pytest.mark.skipif(
    not config.WORKFLOW_GENERATION_ENABLED,
    reason="WORKFLOW_GENERATION_ENABLED is False",
)


class TestWorkflowRefinerGraph:
    def test_instantiates_with_llm_model(self):
        from codemie.workflows.workflow_generator.workflow_refiner import WorkflowRefinerGraph

        graph = WorkflowRefinerGraph(llm_model="gpt-4o", request_id=None)
        assert graph.llm_model == "gpt-4o"
        assert graph.request_id is None

    def test_run_invokes_compiled_graph(self):
        from codemie.workflows.workflow_generator.workflow_refiner import WorkflowRefinerGraph

        expected_state = {"result": Mock(), "error": None, "validation_errors": []}
        graph = WorkflowRefinerGraph(llm_model="gpt-4o", request_id="req-1")
        with patch.object(graph, "graph") as mock_compiled:
            mock_compiled.invoke.return_value = expected_state
            initial_state = {
                "existing_yaml_config": "name: test\n",
                "refine_prompt": "improve it",
                "user": Mock(),
                "project": "demo",
                "available_tools": [],
                "nl_query": "",
                "intent": None,
                "step_plans": None,
                "current_node_index": 0,
                "previous_node": None,
                "node_plan": None,
                "generated_config": None,
                "validation_errors": [],
                "validation_attempts": 0,
                "failed_step_ids": [],
                "result": None,
                "error": None,
            }
            result = graph.run(initial_state)

        mock_compiled.invoke.assert_called_once_with(initial_state)
        assert result is expected_state

    def test_does_not_import_workflow_generator_graph(self):
        """WorkflowRefinerGraph must not depend on WorkflowGeneratorGraph."""
        import ast
        import pathlib

        source = pathlib.Path("src/codemie/workflows/workflow_generator/workflow_refiner.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in getattr(node, "names", [])]
                assert (
                    "WorkflowGeneratorGraph" not in names
                ), "workflow_refiner.py must not import WorkflowGeneratorGraph"
