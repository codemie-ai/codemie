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

"""Handler wiring for sensitive prompt variables: what create, update and the detail route do with them.

The helpers behind the handlers are real here; only the encryption provider and the persistence boundary
are faked. The tests name no helper, so they stay valid wherever the helpers live.
"""

import json
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.models import Application
from codemie.rest_api.models.assistant import Assistant, AssistantRequest, PromptVariable
from codemie.rest_api.routers.assistant.catalog import get_assistant_by_id
from codemie.rest_api.routers.assistant.management import create_assistant, update_assistant
from codemie.rest_api.security.user import User

MASK = "********"
STORED_CIPHERTEXT = "enc(s3cret)"
ENCRYPTION_FACTORY = "codemie.service.encryption.encryption_factory.EncryptionFactory"
MANAGEMENT = "codemie.rest_api.routers.assistant.management"
CATALOG = "codemie.rest_api.routers.assistant.catalog"


def _variable(key: str, default_value: str, is_sensitive: bool = True) -> PromptVariable:
    return PromptVariable(key=key, default_value=default_value, is_sensitive=is_sensitive)


def _request(variables: list[PromptVariable]) -> AssistantRequest:
    return AssistantRequest(
        name="Knowledge Companion",
        description="Test Description",
        system_prompt="Test Prompt",
        project="demo",
        slug="knowledge-companion",
        llm_model_type="gpt-4o",
        skip_integration_validation=True,
        prompt_variables=variables,
    )


def _stored_assistant(variables: list[PromptVariable]) -> Assistant:
    return Assistant(
        id="assistant-123",
        name="Test Assistant",
        description="Test Description",
        project="demo",
        system_prompt="Test Prompt",
        slug="test-assistant",
        prompt_variables=variables,
    )


def _values_by_key(holder) -> dict[str, str]:
    return {var.key: var.default_value for var in holder.prompt_variables}


def _user() -> MagicMock:
    user = MagicMock(spec=User)
    user.id = "user-123"
    user.username = "testuser"
    user.name = "Test User"
    return user


@pytest.fixture
def fake_encryption():
    """A provider whose ciphertext is readable and shows how many times a value was encrypted."""
    service = SimpleNamespace(encrypt=lambda value: f"enc({value})")
    with patch(ENCRYPTION_FACTORY) as factory_cls:
        factory_cls.return_value.get_current_encryption_service.return_value = service
        yield


@pytest.fixture
def create_collaborators():
    """Patch the collaborators around create_assistant, as the slug tests in test_assistant.py do."""
    application = MagicMock(allowed_models=None, default_model=None)
    with ExitStack() as stack:
        for collaborator in (
            patch(f"{MANAGEMENT}.project_access_check"),
            patch(f"{MANAGEMENT}.ensure_application_exists"),
            patch("codemie.service.assistant.assistant_version_service.AssistantVersionService.create_initial_version"),
            patch(f"{MANAGEMENT}.GuardrailService.sync_guardrail_assignments_for_entity"),
            patch(f"{MANAGEMENT}._track_mcp_usage_on_create"),
            patch(f"{MANAGEMENT}._track_assistant_management_metric"),
            patch.object(Application, "get_by_id", return_value=application),
        ):
            stack.enter_context(collaborator)
        yield


@pytest.fixture
def update_collaborators():
    """Patch the collaborators around update_assistant; the stored assistant is set by each test."""
    with ExitStack() as stack:
        for collaborator in (
            patch(f"{MANAGEMENT}.project_access_check"),
            patch(f"{MANAGEMENT}.check_user_can_access_assistant"),
            patch(f"{MANAGEMENT}._validate_remote_entities_and_raise"),
            patch(f"{MANAGEMENT}.category_service"),
            patch(f"{MANAGEMENT}._track_mcp_usage_changes"),
            patch(f"{MANAGEMENT}.GuardrailService.sync_guardrail_assignments_for_entity"),
            patch(f"{MANAGEMENT}._track_assistant_management_metric"),
        ):
            stack.enter_context(collaborator)
        yield


@pytest.fixture
def detail_collaborators():
    """Patch the enrichment collaborators of the detail route; prompt variable masking stays real."""
    with ExitStack() as stack:
        for collaborator in (
            patch(f"{CATALOG}.Ability", return_value=MagicMock(list=MagicMock(return_value=[]))),
            patch(f"{CATALOG}.check_user_can_access_assistant"),
            patch(f"{CATALOG}._validate_remote_entities_and_raise"),
            patch(f"{CATALOG}.ToolsInfoService.get_tools_info", return_value=[]),
            patch(f"{CATALOG}.Assistant.get_by_ids", return_value=[]),
            patch(f"{CATALOG}._get_categories_data"),
            patch(f"{CATALOG}.AssistantRepository"),
            patch(f"{CATALOG}.GuardrailService.get_entity_guardrail_assignments", return_value=[]),
            patch(
                "codemie.service.llm.model_availability_service.Application.get_by_id",
                side_effect=KeyError("not found"),
            ),
            patch(
                "codemie.service.llm.model_availability_service.customer_config",
                is_feature_enabled=MagicMock(return_value=True),
            ),
        ):
            stack.enter_context(collaborator)
        yield


def test_create_saves_the_sensitive_variable_as_ciphertext_and_leaves_the_plain_one(
    fake_encryption, create_collaborators
):
    request = _request([_variable("api_key", "s3cret"), _variable("region", "eu-west", is_sensitive=False)])
    saved = {}

    def capture_saved_variables(assistant, *args, **kwargs):
        saved.update(_values_by_key(assistant))
        return MagicMock()

    with patch.object(Assistant, "save", new=capture_saved_variables):
        response = create_assistant(request, user=_user())

    assert response.message == "Specified assistant saved"
    assert saved == {"api_key": STORED_CIPHERTEXT, "region": "eu-west"}


@pytest.fixture
def request_reaching_the_repository(fake_encryption, update_collaborators):
    """Run update_assistant for a stored ciphertext, a masked echo of it and one new sensitive value."""
    stored = _stored_assistant([_variable("api_key", STORED_CIPHERTEXT)])
    request = _request([_variable("api_key", MASK), _variable("new_token", "n3w")])

    with (
        patch(f"{MANAGEMENT}.get_assistant_by_id_or_raise", return_value=stored),
        patch(f"{MANAGEMENT}.AssistantRepository") as repository_cls,
    ):
        update_assistant("assistant-123", request, background_tasks=MagicMock(), user=_user())

    update_call = repository_cls.return_value.update.call_args
    return update_call.args[1]


def test_update_keeps_the_stored_ciphertext_encrypted_once_when_the_request_sends_the_mask(
    request_reaching_the_repository,
):
    # Restore before encrypt would hand the stored ciphertext to the provider again: enc(enc(s3cret)).
    assert _values_by_key(request_reaching_the_repository)["api_key"] == STORED_CIPHERTEXT


def test_update_encrypts_a_new_sensitive_value_once(request_reaching_the_repository):
    assert _values_by_key(request_reaching_the_repository)["new_token"] == "enc(n3w)"


def test_detail_route_returns_the_mask_and_no_ciphertext(detail_collaborators):
    stored = _stored_assistant([_variable("api_key", STORED_CIPHERTEXT), _variable("region", "eu-west", False)])

    with patch(f"{CATALOG}.get_assistant_by_id_or_raise", return_value=stored):
        response = get_assistant_by_id(request=MagicMock(), assistant_id="assistant-123", user=_user())

    body = json.loads(response.body)
    returned = {variable["key"]: variable["default_value"] for variable in body["prompt_variables"]}
    assert returned == {"api_key": MASK, "region": "eu-west"}
    assert STORED_CIPHERTEXT not in response.body.decode()
