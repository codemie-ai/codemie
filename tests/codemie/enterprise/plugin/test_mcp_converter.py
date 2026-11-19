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

"""The enterprise plugin path publishes MCP files the same way the core MCP path does."""

import copy
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codemie.enterprise.plugin.dependencies import convert_mcp_response, get_plugin_tools_for_assistant
from codemie.service.artifact_publisher import ArtifactPublisherService
from codemie_tools.base.artifact_publishing import PublishContext

_WIRE = json.loads(
    (Path(__file__).parents[2] / "service" / "mcp" / "fixtures" / "mcp_server_everything_results.json").read_text()
)
_CTX = PublishContext(user_id="user-1", conversation_id="conv-1", turn_id="turn-1")


@pytest.fixture
def storage():
    """Real publisher over a fake workspace: links echo the workspace path they were written to."""
    workspace = MagicMock()
    workspace.repository.get_by_conversation_for_user.return_value = None
    workspace.publish_artifact_file.side_effect = lambda conv, user, path, data, mime: f"sandbox:/v1/files/{path}"
    with (
        patch(
            "codemie.service.mcp.toolkit.ArtifactPublisherService",
            side_effect=lambda file_repository: ArtifactPublisherService(file_repository, workspace),
        ),
        patch("codemie.service.mcp.toolkit.FileRepositoryFactory.get_current_repository", return_value=MagicMock()),
    ):
        yield workspace


class _EnterpriseResponse:
    """Stands in for codemie_enterprise's pydantic response: the adapter only calls model_dump()."""

    def __init__(self, payload: dict):
        self._payload = payload

    def model_dump(self):
        return copy.deepcopy(self._payload)


def test_stub_enterprise_response_yields_link(storage):
    result = convert_mcp_response(_EnterpriseResponse(_WIRE["embedded_text"]), _CTX)
    assert "[1.txt](sandbox:/v1/files/artifacts/turn-1/1.txt)" in result
    assert storage.publish_artifact_file.call_args.args[0] == "conv-1"
    assert storage.publish_artifact_file.call_args.args[1].id == "user-1"


@pytest.mark.xfail(
    strict=True,
    reason="EPMCDME-16353: codemie-enterprise 2.3.47 ToolConsumer rebuilds items without resource, uri and name",
)
def test_plugin_raw_result_keeps_resource_fields_through_enterprise_consumer(storage):
    pytest.importorskip("codemie_enterprise.plugin")
    from codemie_enterprise.plugin.tool_consumer import ToolConsumer

    def through_plugin(case: str) -> str:
        # Production path: the plugin hands ToolConsumer a bare JSON list, not {"content": [...]}
        raw = json.dumps(_WIRE[case]["content"])
        return convert_mcp_response(ToolConsumer._convert_mcp_through_plugin_response(None, raw), _CTX)

    assert "[1.txt](sandbox:/v1/files/artifacts/turn-1/1.txt)" in through_plugin("embedded_text")
    assert "`1.txt` stays on the MCP server and was not delivered" in through_plugin("resource_link")


def _captured_converter(**kwargs):
    """Run get_plugin_tools_for_assistant against a stub enterprise package; return the converter it hands over."""
    captured = {}

    class _Toolkit:
        def __init__(self, plugin_service, plugin_key, mcp_converter_callback):
            captured["callback"] = mcp_converter_callback

        def get_tools(self, session_id=None):
            return []

    plugin_pkg = types.ModuleType("codemie_enterprise.plugin")
    plugin_pkg.PluginToolkit = _Toolkit
    models_pkg = types.ModuleType("codemie_enterprise.plugin.mcp_models")
    models_pkg.MCPToolInvocationResponse = object
    # Patches that import real modules enter first: patch.dict drops modules first imported inside it on exit
    with (
        patch("codemie.enterprise.plugin.dependencies.get_plugin_service_or_none", return_value=MagicMock()),
        patch("codemie.service.settings.settings.SettingsService.get_plugin_creds") as creds,
        patch.dict(
            sys.modules,
            {
                "codemie_enterprise": types.ModuleType("codemie_enterprise"),
                "codemie_enterprise.plugin": plugin_pkg,
                "codemie_enterprise.plugin.mcp_models": models_pkg,
            },
        ),
    ):
        creds.return_value.plugin_key = "key"
        get_plugin_tools_for_assistant(user_id="initiator", project_name="p", **kwargs)
    return captured["callback"]


def test_closure_builds_context_from_initiator_conversation_and_turn(storage):
    converter = _captured_converter(conversation_id="conv-9", request_uuid="req-9")
    assert "[1.txt](sandbox:/v1/files/artifacts/req-9/1.txt)" in converter(_EnterpriseResponse(_WIRE["embedded_text"]))
    assert storage.publish_artifact_file.call_args.args[0] == "conv-9"
    assert storage.publish_artifact_file.call_args.args[1].id == "initiator"


def test_closure_without_request_uuid_uses_fresh_turn_per_call(storage):
    converter = _captured_converter(conversation_id="conv-9")
    first = converter(_EnterpriseResponse(_WIRE["embedded_text"]))
    second = converter(_EnterpriseResponse(_WIRE["embedded_text"]))
    assert first != second
    assert "[1.txt](sandbox:/v1/files/artifacts/" in first
