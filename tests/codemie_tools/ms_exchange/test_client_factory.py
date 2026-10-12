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
"""Tests for the auth_type client dispatcher."""

from unittest.mock import patch

import pytest

from codemie_tools.ms_exchange import client_factory as cf
from codemie_tools.ms_exchange.errors import ExchangeError
from codemie_tools.ms_exchange.models import ExchangeAuthType, ExchangeToolConfig


def _oauth():
    return ExchangeToolConfig(
        auth_type=ExchangeAuthType.OAUTH_AZURE,
        mailbox="u@example.test",
        oauth_tenant_id="t",
        oauth_client_id="c",
        oauth_client_secret="s",
    )


def _ntlm():
    return ExchangeToolConfig(
        auth_type=ExchangeAuthType.NTLM,
        mailbox="u@example.test",
        ews_url="https://ews.example.test/EWS/Exchange.asmx",
        service_account_username="svc",
        service_account_password="pw",
    )


def test_oauth_azure_dispatches_to_graph():
    with patch("codemie_tools.ms_exchange.graph_client.build_graph_client"):
        assert type(cf.get_client(_oauth())).__name__ == "GraphExchangeClient"


def test_ntlm_dispatches_to_ews():
    with patch("codemie_tools.ms_exchange.ews_client.build_ews_client"):
        assert type(cf.get_client(_ntlm())).__name__ == "EwsExchangeClient"


def test_ews_construction_failure_is_typed_and_sanitised():
    with patch(
        "codemie_tools.ms_exchange.ews_client.build_ews_client", side_effect=RuntimeError("https://secret.host")
    ):
        with pytest.raises(ExchangeError) as info:
            cf.get_client(_ntlm())
    assert "secret" not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_graph_construction_failure_is_typed_on_direct_tool_execute():
    from codemie_tools.ms_exchange.tools import ReadTool

    with patch("codemie_tools.ms_exchange.graph_client.build_graph_client", side_effect=ValueError("tenant-secret")):
        with pytest.raises(ExchangeError) as info:
            ReadTool(config=_oauth()).execute()
    assert "secret" not in str(info.value)


def test_graph_credential_failure_inside_call_is_typed():
    from codemie_tools.ms_exchange.tools import RetrieveFoldersTool

    with patch("codemie_tools.ms_exchange.graph_client.build_graph_client", side_effect=ValueError("tenant-secret")):
        with pytest.raises(ExchangeError) as info:
            RetrieveFoldersTool(config=_oauth()).execute()
    assert "secret" not in str(info.value)
