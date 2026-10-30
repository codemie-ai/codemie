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
"""Dispatch to the Exchange transport selected by ``config.auth_type``."""

from __future__ import annotations

from codemie_tools.ms_exchange.client_base import ExchangeClient
from codemie_tools.ms_exchange.errors import ExchangeError, InternalError, map_write_open_error
from codemie_tools.ms_exchange.ews_client import EwsExchangeClient
from codemie_tools.ms_exchange.graph_client import GraphExchangeClient
from codemie_tools.ms_exchange.models import ExchangeAuthType, ExchangeToolConfig

_CLIENTS = {
    ExchangeAuthType.OAUTH_AZURE: GraphExchangeClient,
    ExchangeAuthType.NTLM: EwsExchangeClient,
}


def get_client(config: ExchangeToolConfig) -> ExchangeClient:
    client_cls = _CLIENTS.get(config.auth_type)
    if client_cls is None:
        raise InternalError
    try:
        return client_cls(config)
    except ExchangeError:
        raise
    except Exception:  # noqa: BLE001 - provider text (URLs, accounts) must not surface
        raise InternalError from None


def get_write_client(config: ExchangeToolConfig) -> ExchangeClient:
    """Construct the client for a write tool: a construction failure here means nothing was sent.

    Graph builds its credential inside ``__init__`` itself, before ``get_client`` ever returns. EWS does no
    such work there: its read account is built on first read access, and a write checks the endpoint (resolve
    and DNS check) when its write session opens, where a failure is already reported as Not applied.
    ``get_client`` reports a construction failure as a read error (no ``OUTCOME``, retryable), which is correct
    for the read tools that also call it but wrong for a write: nothing was submitted, so it must be Not
    applied, never silently retryable.
    """
    try:
        return get_client(config)
    except ExchangeError as exc:
        raise map_write_open_error(exc) from None
