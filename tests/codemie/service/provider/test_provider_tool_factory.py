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

import pytest
from unittest.mock import MagicMock, patch
from urllib3.exceptions import MaxRetryError
from typing import Optional

from codemie.service.provider.provider_tool_factory import (
    ProviderToolFactory,
    ProviderToolBase,
    ProviderConnectionError,
)
from codemie.rest_api.models.provider import Provider, ProviderConfiguration, ProviderToolkit, ProviderToolArgument
from codemie.rest_api.models.index import ProviderIndexInfo
from codemie.rest_api.security.user import User, UserContext
from codemie.configs import config


@pytest.fixture
def provider_config():
    mock = MagicMock(
        spec=Provider,
        configuration=MagicMock(
            spec=ProviderConfiguration, auth_type=ProviderConfiguration.AuthType.BEARER.value, auth_secret="test_secret"
        ),
        service_location_url="http://test.com",
    )

    return mock


@pytest.fixture
def toolkit_config():
    mock = MagicMock(spec=ProviderToolkit)
    mock.name = "test"
    mock.toolkit_id = "test_id"
    return mock


@pytest.fixture
def tool_config():
    tool_config = MagicMock(spec=ProviderToolkit.Tool)
    tool_config.name = "test"
    tool_config.description = "A test tool"
    tool_config.args_schema = {
        "param1": ProviderToolArgument(arg_type="String", required=False, description=""),
        "param2": ProviderToolArgument(arg_type="Number", required=True, description=""),
    }

    return tool_config


@pytest.fixture
def tool_factory(provider_config, toolkit_config, tool_config):
    return ProviderToolFactory(provider_config, toolkit_config, tool_config)


@pytest.fixture
def tool_params():
    return {
        "user": User(id="tool_user", auth_token="123"),
        "project_id": "tool_project",
        "request_uuid": "tool_request",
    }


def test_build_creates_tool_class(tool_factory, tool_params):
    tool_class = tool_factory.build()
    instance = tool_class(**tool_params)

    assert tool_class.__name__ == "TestTool"
    assert issubclass(tool_class, ProviderToolBase)

    assert instance.name == "test"
    assert instance.description == "A test tool"
    assert hasattr(instance, "args_schema")
    assert hasattr(instance, "execute")
    assert hasattr(instance, "invoke_headers")


def test_build_creates_tool_class_incorrect_auth_type(tool_factory, tool_params, monkeypatch):
    monkeypatch.setattr(config, "ENV", "test")
    tool_factory.provider_config.configuration.auth_type = "Basic"

    with pytest.raises(ValueError):
        klass = tool_factory.build()
        klass(**tool_params).execute()


@patch("codemie.service.provider.provider_tool_factory.get_traceparent_headers", return_value={})
@patch("codemie.clients.provider.client.Configuration", return_value=MagicMock())
@patch("codemie.clients.provider.client.ApiClient", return_value=MagicMock())
@patch("codemie.clients.provider.client.ToolInvocationManagementApi.invoke_tool")
@patch("codemie.service.provider.datasource.ProviderDatasourceSchemaService.schema_for")
@patch("codemie.service.provider.util.decrypt_datasource_provider_fields", return_value={})
def test_built_execute_method(
    _mock_decrypt,
    _mock_schema_get,
    mock_invoke_tool,
    mock_api_client,
    mock_api_config,
    _mock_tp,
    tool_factory,
    tool_params,
):
    mock_result = MagicMock()
    mock_result.result = "test_result"
    mock_invoke_tool.return_value = mock_result

    tool_class = tool_factory.build()
    tool_instance = tool_class(**tool_params)

    result = tool_instance.execute(param1="test_param1", param2=42)

    assert result == "test_result"
    # invoke_headers defaults to None — no propagation, x_correlation_id falls back to request_uuid
    mock_invoke_tool.assert_called_once()
    call_kwargs = mock_invoke_tool.call_args.kwargs
    assert call_kwargs["toolkit_name"] == "test"
    assert call_kwargs["tool_name"] == "test"
    assert call_kwargs["x_correlation_id"] == "tool_request"
    assert call_kwargs["_headers"] is None
    # tool_invocation_request is a typed ToolInvocationRequest (built by ProviderToolFactory.invoke)
    invocation_request = call_kwargs["tool_invocation_request"]
    assert invocation_request.user_id == "tool_user"
    assert invocation_request.project_id == "tool_project"
    assert invocation_request.configuration.configuration_type == "tool_invocation"
    assert invocation_request.configuration.parameters == {}
    assert invocation_request.parameters == {"param1": "test_param1", "param2": 42}
    # UserContext assertions: fixture user is User(id="tool_user", auth_token="123")
    fixture_user = tool_params["user"]
    expected_user_context = UserContext.from_user(fixture_user).model_dump(exclude_none=True)
    assert invocation_request.user_context == expected_user_context
    assert invocation_request.user_context["id"] == "tool_user"
    assert "auth_token" not in invocation_request.user_context
    assert "tenant_id" not in invocation_request.user_context


@patch("codemie.clients.provider.client.Configuration", return_value=MagicMock())
@patch("codemie.clients.provider.client.ApiClient", return_value=MagicMock())
@patch("codemie.clients.provider.client.ToolInvocationManagementApi.invoke_tool")
@patch("codemie.service.provider.datasource.ProviderDatasourceSchemaService.schema_for")
@patch("codemie.service.provider.util.decrypt_datasource_provider_fields", return_value={})
def test_built_execute_method_conn_error(
    _mock_decrypt, _mock_schema_get, mock_invoke_tool, mock_api_client, mock_api_config, tool_factory, tool_params
):
    mock_invoke_tool.side_effect = MaxRetryError(url="test", pool=MagicMock())

    tool_class = tool_factory.build()
    tool_instance = tool_class(**tool_params)

    with pytest.raises(ProviderConnectionError):
        tool_instance.execute(param1="test_param1", param2=42)


def test_build_tool_class_args_schema(tool_factory, tool_params):
    tool_class = tool_factory.build()
    args_schema = tool_class(**tool_params).args_schema

    assert args_schema.__name__ == "ArgsSchema"
    assert "param1" in args_schema.__annotations__
    assert "param2" in args_schema.__annotations__
    assert args_schema.__annotations__["param1"] is Optional[str]
    assert args_schema.__annotations__["param2"] is int


def test_build_sanitizes_datasource_tool_name(provider_config, toolkit_config, tool_config):
    datasource = MagicMock(spec=ProviderIndexInfo)
    datasource.repo_name = "My.Repo.Name"
    factory = ProviderToolFactory(provider_config, toolkit_config, tool_config, datasource=datasource)

    tool_class = factory.build()

    assert tool_class.name == "my_repo_name_test"


@patch("codemie.service.provider.provider_tool_factory.get_traceparent_headers", return_value={})
@patch("codemie.clients.provider.client.Configuration", return_value=MagicMock())
@patch("codemie.clients.provider.client.ApiClient", return_value=MagicMock())
@patch("codemie.clients.provider.client.ToolInvocationManagementApi.invoke_tool")
@patch("codemie.service.provider.datasource.ProviderDatasourceSchemaService.schema_for")
@patch("codemie.service.provider.util.decrypt_datasource_provider_fields", return_value={})
def test_execute_propagates_invoke_headers(
    _mock_decrypt, _mock_schema, mock_invoke_tool, mock_api_client, mock_api_config, _mock_tp, tool_factory
):
    """Pre-built invoke_headers dict is passed as _headers; X-Correlation-ID used for x_correlation_id."""
    mock_result = MagicMock()
    mock_result.result = "ok"
    mock_invoke_tool.return_value = mock_result

    # invoke_headers is pre-built by ToolkitService._build_invoke_headers before tool instantiation
    invoke_headers = {
        "X-Tenant-ID": "tenant-abc",
        "X-Correlation-Id": "corr-xyz",
        "X-Conversation-Id": "conv-111",
        "X-Conversation-Message-Id": "5",
        "X-Assistant-Id": "asst-222",
        "X-Assistant-LLM-Model": "gpt-4o",
        "X-Assistant-Temperature": "0.7",
    }

    tool_class = tool_factory.build()
    instance = tool_class(
        user=User(id="u1", auth_token="tok"),
        project_id="proj1",
        request_uuid="req-uuid-1",
        invoke_headers=invoke_headers,
    )

    instance.execute(param1="val")

    call_kwargs = mock_invoke_tool.call_args.kwargs
    assert call_kwargs["_headers"] == invoke_headers
    # x_correlation_id comes from the X-Correlation-ID header, not the request_uuid fallback
    assert call_kwargs["x_correlation_id"] == "corr-xyz"


@patch("codemie.service.provider.provider_tool_factory.get_traceparent_headers", return_value={})
@patch("codemie.clients.provider.client.Configuration", return_value=MagicMock())
@patch("codemie.clients.provider.client.ApiClient", return_value=MagicMock())
@patch("codemie.clients.provider.client.ToolInvocationManagementApi.invoke_tool")
@patch("codemie.service.provider.datasource.ProviderDatasourceSchemaService.schema_for")
@patch("codemie.service.provider.util.decrypt_datasource_provider_fields", return_value={})
def test_execute_no_headers_when_invoke_headers_none(
    _mock_decrypt, _mock_schema, mock_invoke_tool, mock_api_client, mock_api_config, _mock_tp, tool_factory, tool_params
):
    """When invoke_headers is None, _headers=None and request_uuid used for correlation."""
    mock_result = MagicMock()
    mock_result.result = "ok"
    mock_invoke_tool.return_value = mock_result

    tool_class = tool_factory.build()
    instance = tool_class(**tool_params)  # no invoke_headers → defaults to None

    instance.execute(param1="val")

    call_kwargs = mock_invoke_tool.call_args.kwargs
    assert call_kwargs["_headers"] is None
    assert call_kwargs["x_correlation_id"] == "tool_request"  # falls back to request_uuid


# ---------------------------------------------------------------------------
# invoke() — the reusable entry point shared by the agent tool path and the
# request-hedging fast path.
# ---------------------------------------------------------------------------


@patch("codemie.service.provider.provider_tool_factory.get_traceparent_headers", return_value={})
@patch("codemie.clients.provider.client.Configuration", return_value=MagicMock())
@patch("codemie.clients.provider.client.ApiClient", return_value=MagicMock())
@patch("codemie.clients.provider.client.ToolInvocationManagementApi.invoke_tool")
def test_invoke_returns_full_response_and_builds_request(
    mock_invoke_tool, _mock_api_client, _mock_api_config, _mock_tp, tool_factory
):
    """invoke() returns the raw ToolInvocationResponse (not response.result) and forwards headers."""
    response = MagicMock()
    mock_invoke_tool.return_value = response

    headers = {"X-Correlation-Id": "corr-1", "X-Tenant-ID": "t"}
    result = tool_factory.invoke(
        user=User(id="u1", auth_token="tok"),
        project_id="proj-1",
        request_uuid="req-1",
        params={"q": "hello"},
        headers=headers,
    )

    assert result is response
    call_kwargs = mock_invoke_tool.call_args.kwargs
    assert call_kwargs["toolkit_name"] == "test"
    assert call_kwargs["tool_name"] == "test"
    # correlation id taken from the header, not the request_uuid fallback
    assert call_kwargs["x_correlation_id"] == "corr-1"
    assert call_kwargs["_headers"] == headers
    invocation_request = call_kwargs["tool_invocation_request"]
    assert invocation_request.user_id == "u1"
    assert invocation_request.project_id == "proj-1"
    assert invocation_request.parameters == {"q": "hello"}


@patch("codemie.service.provider.provider_tool_factory.get_traceparent_headers", return_value={})
@patch("codemie.clients.provider.client.Configuration", return_value=MagicMock())
@patch("codemie.clients.provider.client.ApiClient", return_value=MagicMock())
@patch("codemie.clients.provider.client.ToolInvocationManagementApi.invoke_tool")
def test_invoke_empty_headers_passed_as_none_and_uses_request_uuid(
    mock_invoke_tool, _mock_api_client, _mock_api_config, _mock_tp, tool_factory
):
    mock_invoke_tool.return_value = MagicMock()

    tool_factory.invoke(
        user=User(id="u1", auth_token="tok"),
        project_id="proj-1",
        request_uuid="req-1",
        params={},
        headers={},
    )

    call_kwargs = mock_invoke_tool.call_args.kwargs
    assert call_kwargs["_headers"] is None
    assert call_kwargs["x_correlation_id"] == "req-1"


def test_resolve_configuration_params_returns_empty_without_datasource(tool_factory):
    assert tool_factory._resolve_configuration_params(None) == {}


@patch("codemie.service.provider.provider_tool_factory.ProviderDatasourceSchemaService")
@patch(
    "codemie.service.provider.provider_tool_factory.decrypt_datasource_provider_fields",
    return_value={"decrypted": "value"},
)
def test_resolve_configuration_params_decrypts_datasource(mock_decrypt, mock_schema_service, tool_factory):
    datasource = MagicMock()
    datasource.provider_fields.base_params = {"raw": "enc"}
    schema = MagicMock()
    schema.base_schema = {"raw": {"type": "string"}}
    mock_schema_service.return_value.schema_for.return_value = schema

    result = tool_factory._resolve_configuration_params(datasource)

    assert result == {"decrypted": "value"}
    mock_schema_service.assert_called_once_with(provider=tool_factory.provider_config)
    mock_schema_service.return_value.schema_for.assert_called_once_with(toolkit_id="test_id")
    mock_decrypt.assert_called_once_with(params={"raw": "enc"}, schema={"raw": {"type": "string"}})


@patch("codemie.clients.provider.client.Configuration", return_value=MagicMock())
@patch("codemie.clients.provider.client.ApiClient", return_value=MagicMock())
@patch("codemie.clients.provider.client.ToolInvocationManagementApi.invoke_tool")
def test_invoke_uses_explicit_datasource_for_configuration_params(
    mock_invoke_tool, _mock_api_client, _mock_api_config, tool_factory
):
    """A datasource passed to invoke() overrides self.datasource for config resolution."""
    mock_invoke_tool.return_value = MagicMock()
    datasource = MagicMock(spec=ProviderIndexInfo)

    with patch.object(tool_factory, "_resolve_configuration_params", return_value={"cfg": "1"}) as mock_resolve:
        tool_factory.invoke(
            user=User(id="u1", auth_token="tok"),
            project_id="proj-1",
            request_uuid="req-1",
            params={},
            datasource=datasource,
        )

    mock_resolve.assert_called_once_with(datasource)
    invocation_request = mock_invoke_tool.call_args.kwargs["tool_invocation_request"]
    assert invocation_request.configuration.parameters == {"cfg": "1"}


@patch("codemie.clients.provider.client.Configuration", return_value=MagicMock())
@patch("codemie.clients.provider.client.ApiClient", return_value=MagicMock())
@patch("codemie.clients.provider.client.ToolInvocationManagementApi.invoke_tool")
@patch(
    "codemie.service.provider.provider_tool_factory.get_traceparent_headers",
    return_value={"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
)
def test_invoke_injects_traceparent_into_headers(
    _mock_tp, mock_invoke_tool, _mock_api_client, _mock_api_config, tool_factory
):
    """traceparent from get_traceparent_headers() is merged into the outbound _headers."""
    mock_invoke_tool.return_value = MagicMock()

    tool_factory.invoke(
        user=User(id="u1", auth_token="tok"),
        project_id="proj-1",
        request_uuid="req-1",
        params={},
        headers={"X-Correlation-Id": "corr-1"},
    )

    call_kwargs = mock_invoke_tool.call_args.kwargs
    assert call_kwargs["_headers"]["traceparent"] == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    assert call_kwargs["_headers"]["X-Correlation-Id"] == "corr-1"
    assert call_kwargs["x_correlation_id"] == "corr-1"


@patch("codemie.service.provider.provider_tool_factory.get_traceparent_headers", return_value={})
@patch("codemie.clients.provider.client.Configuration", return_value=MagicMock())
@patch("codemie.clients.provider.client.ApiClient", return_value=MagicMock())
@patch("codemie.clients.provider.client.ToolInvocationManagementApi.invoke_tool")
def test_invoke_emits_masked_user_context_debug_line_without_touching_wire(
    mock_invoke_tool, _mock_api_client, _mock_api_config, _mock_tp, tool_factory, caplog
):
    """invoke() logs a masked user_context at DEBUG; the DSP wire payload is unchanged."""
    import logging

    mock_invoke_tool.return_value = MagicMock()
    user = User(id="u1", email="john.doe@epam.com", extra_attributes={"x": "raw-secret-value"}, auth_token="tok")

    caplog.set_level(logging.DEBUG, logger="codemie")
    codemie_logger = logging.getLogger("codemie")
    codemie_logger.addHandler(caplog.handler)
    try:
        tool_factory.invoke(
            user=user,
            project_id="proj-1",
            request_uuid="req-1",
            params={},
            headers={"X-Correlation-Id": "corr-1"},
        )
    finally:
        codemie_logger.removeHandler(caplog.handler)

    debug_text = "\n".join(r.getMessage() for r in caplog.records if r.levelname == "DEBUG")
    assert "j***@epam.com" in debug_text
    assert "<redacted, 1 keys>" in debug_text
    assert "john.doe@epam.com" not in debug_text
    assert "raw-secret-value" not in debug_text

    # DSP wire still forwards the full user_context, extra_attributes included (unchanged).
    invocation_request = mock_invoke_tool.call_args.kwargs["tool_invocation_request"]
    assert invocation_request.user_context == UserContext.from_user(user).model_dump(exclude_none=True)
    assert invocation_request.user_context["extra_attributes"] == {"x": "raw-secret-value"}


@patch("codemie.service.provider.provider_tool_factory.get_traceparent_headers", return_value={})
@patch("codemie.clients.provider.client.Configuration", return_value=MagicMock())
@patch("codemie.clients.provider.client.ApiClient", return_value=MagicMock())
@patch("codemie.clients.provider.client.ToolInvocationManagementApi.invoke_tool")
def test_invoke_omits_user_context_when_allow_user_context_false(
    mock_invoke_tool, _mock_api_client, _mock_api_config, _mock_tp, tool_factory, caplog
):
    """provider_config.allow_user_context=False: no user_context kwarg on the wire request,
    and no masked debug log (EPMCDME-14984)."""
    import logging

    mock_invoke_tool.return_value = MagicMock()
    tool_factory.provider_config.allow_user_context = False
    user = User(id="u1", email="john.doe@epam.com", auth_token="tok")

    caplog.set_level(logging.DEBUG, logger="codemie")
    codemie_logger = logging.getLogger("codemie")
    codemie_logger.addHandler(caplog.handler)
    try:
        tool_factory.invoke(
            user=user,
            project_id="proj-1",
            request_uuid="req-1",
            params={},
        )
    finally:
        codemie_logger.removeHandler(caplog.handler)

    invocation_request = mock_invoke_tool.call_args.kwargs["tool_invocation_request"]
    assert invocation_request.user_context is None

    debug_text = "\n".join(r.getMessage() for r in caplog.records if r.levelname == "DEBUG")
    assert "user context" not in debug_text.lower()


@patch("codemie.service.provider.provider_tool_factory.get_traceparent_headers", return_value={})
@patch("codemie.clients.provider.client.Configuration", return_value=MagicMock())
@patch("codemie.clients.provider.client.ApiClient", return_value=MagicMock())
@patch("codemie.clients.provider.client.ToolInvocationManagementApi.invoke_tool")
def test_invoke_sends_user_context_when_allow_user_context_true(
    mock_invoke_tool, _mock_api_client, _mock_api_config, _mock_tp, tool_factory
):
    """provider_config.allow_user_context=True: user_context is sent unchanged, same shape as today."""
    mock_invoke_tool.return_value = MagicMock()
    tool_factory.provider_config.allow_user_context = True
    user = User(id="u1", email="john.doe@epam.com", auth_token="tok")

    tool_factory.invoke(
        user=user,
        project_id="proj-1",
        request_uuid="req-1",
        params={},
    )

    invocation_request = mock_invoke_tool.call_args.kwargs["tool_invocation_request"]
    assert invocation_request.user_context == UserContext.from_user(user).model_dump(exclude_none=True)


@patch("codemie.clients.provider.client.Configuration", return_value=MagicMock())
@patch("codemie.clients.provider.client.ApiClient", return_value=MagicMock())
@patch("codemie.clients.provider.client.ToolInvocationManagementApi.invoke_tool")
@patch(
    "codemie.service.provider.provider_tool_factory.get_traceparent_headers",
    return_value={},
)
def test_invoke_proceeds_without_traceparent_when_no_span(
    _mock_tp, mock_invoke_tool, _mock_api_client, _mock_api_config, tool_factory
):
    """When get_traceparent_headers() returns {}, the DSP call still succeeds."""
    mock_invoke_tool.return_value = MagicMock()

    tool_factory.invoke(
        user=User(id="u1", auth_token="tok"),
        project_id="proj-1",
        request_uuid="req-1",
        params={},
        headers={"X-Correlation-Id": "corr-1"},
    )

    call_kwargs = mock_invoke_tool.call_args.kwargs
    assert "traceparent" not in call_kwargs["_headers"]
    assert call_kwargs["_headers"]["X-Correlation-Id"] == "corr-1"
    assert call_kwargs["x_correlation_id"] == "corr-1"
