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

"""EPMCDME-16625 final review AR-4: virtual assistants keep their project for credentials and are billed as asset-less
to the user's default project unless the caller names a project."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.rest_api.security.user import User
from codemie.service.llm_service.utils import _bills_as_asset_less


def _user() -> User:
    return User(id="u-1", username="carol", name="Carol", email="carol@example.com", project_names=["alpha", "carol"])


def _raw_request() -> MagicMock:
    raw = MagicMock()
    raw.state = SimpleNamespace(uuid="req-1", wait_for_disconnect=AsyncMock())
    return raw


async def _ide_assistant(project: str | None):
    from codemie.core.models import VirtualIdeChatRequest
    from codemie.rest_api.routers import ide

    request = VirtualIdeChatRequest(text="hi", virtual_assistant_id="v1", llm_model="m", project=project)
    with (
        patch.object(ide, "get_request_handler") as get_handler,
        patch.object(ide.assistant_user_interaction_service, "record_usage"),
        patch.object(ide.request_summary_manager, "create_request_summary"),
        patch.object(ide.asyncio, "to_thread", new=AsyncMock(return_value="ok")),
        patch.object(ide.asyncio, "create_task"),
    ):
        await ide.inference_virtual(_raw_request(), MagicMock(), request, _user())
    return get_handler.call_args.args[0]


@pytest.mark.asyncio
async def test_ide_without_a_project_keeps_the_credentials_project_and_bills_as_asset_less():
    assistant = await _ide_assistant(None)

    assert assistant.project == _user().current_project
    assert _bills_as_asset_less(assistant)


@pytest.mark.asyncio
async def test_ide_with_a_named_project_bills_that_project():
    assistant = await _ide_assistant("alpha")

    assert assistant.project == "alpha"
    assert not _bills_as_asset_less(assistant)


@pytest.mark.asyncio
async def test_inline_assistant_keeps_the_credentials_project_and_bills_as_asset_less():
    from codemie.rest_api.routers.assistant import execution

    # A non-empty tools_config keeps the endpoint from reading toolkits back off the unmapped model.
    request = MagicMock(tools_config=["cfg"], mcp_servers=[], llm_model_type="m")
    with (
        patch.object(execution.asyncio, "to_thread", new=AsyncMock(return_value="ok")) as to_thread,
        patch.object(execution.asyncio, "create_task"),
        patch.object(execution.MCPAccessControlService, "sanitize_for_save", return_value=[]),
    ):
        await execution.ask_virtual_assistant(_raw_request(), MagicMock(), request, _user())

    assistant = to_thread.call_args.args[1]
    # Read the field directly: a model_construct table model has no SQLAlchemy state outside the app.
    assert assistant.__dict__["project"] == _user().current_project
    assert _bills_as_asset_less(assistant)
