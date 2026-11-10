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

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import (
    INTERNAL_ERROR_DETAILS,
    INTERNAL_ERROR_MESSAGE,
    elastic_exception_handler,
    extended_http_exception_handler,
    handle_exceptions,
)

LEAK_MARKERS = ("Stacktrace", "Traceback", "secret internal detail", "site-packages", "sqlalchemy", "psycopg")


def _request(uuid: str | None = "req-123") -> MagicMock:
    request = MagicMock()
    request.state = SimpleNamespace(uuid=uuid) if uuid else SimpleNamespace()
    return request


def _assert_no_leak(body: str) -> None:
    for marker in LEAK_MARKERS:
        assert marker not in body


@pytest.mark.asyncio
async def test_handle_exceptions_does_not_leak_internals():
    call_next = AsyncMock(side_effect=RuntimeError("secret internal detail"))

    with patch("codemie.rest_api.main.config") as mock_config, patch("codemie.rest_api.main.logger") as mock_logger:
        mock_config.is_local = False
        response = await handle_exceptions(_request(), call_next)

    body = response.body.decode()
    assert response.status_code == 500
    _assert_no_leak(body)
    assert json.loads(body)["error"]["message"] == INTERNAL_ERROR_MESSAGE
    assert mock_logger.error.call_args.kwargs["exc_info"] is True


@pytest.mark.asyncio
async def test_handle_exceptions_includes_request_id_in_details():
    call_next = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("codemie.rest_api.main.config") as mock_config:
        mock_config.is_local = False
        response = await handle_exceptions(_request("req-123"), call_next)

    assert json.loads(response.body)["error"]["details"] == f"{INTERNAL_ERROR_DETAILS} Request ID: req-123"


@pytest.mark.asyncio
async def test_handle_exceptions_without_request_id_uses_generic_details():
    call_next = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("codemie.rest_api.main.config") as mock_config:
        mock_config.is_local = False
        response = await handle_exceptions(_request(None), call_next)

    assert json.loads(response.body)["error"]["details"] == INTERNAL_ERROR_DETAILS


@pytest.mark.asyncio
async def test_handle_exceptions_reraises_when_local():
    call_next = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("codemie.rest_api.main.config") as mock_config:
        mock_config.is_local = True
        with pytest.raises(RuntimeError):
            await handle_exceptions(_request(), call_next)


@pytest.mark.asyncio
async def test_extended_handler_hides_details_of_500():
    exc = ExtendedHTTPException(
        code=500,
        message="Failed to retrieve tools information",
        details="(psycopg.OperationalError) connection to server at sqlalchemy secret internal detail",
        help="Try again",
    )

    with patch("codemie.rest_api.main.logger") as mock_logger:
        response = await extended_http_exception_handler(_request(), exc)

    body = response.body.decode()
    error = json.loads(body)["error"]
    assert response.status_code == 500
    _assert_no_leak(body)
    assert error["message"] == "Failed to retrieve tools information"
    assert error["details"] == f"{INTERNAL_ERROR_DETAILS} Request ID: req-123"
    assert error["help"] == "Try again"
    assert "secret internal detail" in mock_logger.error.call_args.args[0]


@pytest.mark.asyncio
async def test_extended_handler_keeps_details_of_503():
    exc = ExtendedHTTPException(code=503, message="Service unavailable", details="Elastic is down", help="Retry")

    response = await extended_http_exception_handler(_request(), exc)

    assert json.loads(response.body)["error"]["details"] == "Elastic is down"


@pytest.mark.asyncio
async def test_extended_handler_logs_expected_503_as_warning_without_traceback():
    exc = ExtendedHTTPException(code=503, message="Not configured", details="Set X", help="Help", expected=True)

    with patch("codemie.rest_api.main.logger") as mock_logger:
        response = await extended_http_exception_handler(_request(), exc)

    assert response.status_code == 503
    assert json.loads(response.body)["error"]["details"] == "Set X"
    mock_logger.error.assert_not_called()
    mock_logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_extended_handler_logs_unexpected_503_with_traceback():
    exc = ExtendedHTTPException(code=503, message="Unavailable", details="Elastic is down", help="Retry")

    with patch("codemie.rest_api.main.logger") as mock_logger:
        await extended_http_exception_handler(_request(), exc)

    assert mock_logger.error.call_args.kwargs["exc_info"] is True
    mock_logger.warning.assert_not_called()


@pytest.mark.asyncio
async def test_extended_handler_keeps_details_of_4xx():
    exc = ExtendedHTTPException(code=400, message="Bad input", details="field x is invalid", help="Fix it")

    response = await extended_http_exception_handler(_request(), exc)

    assert json.loads(response.body)["error"]["details"] == "field x is invalid"


@pytest.mark.asyncio
async def test_elastic_handler_hides_exception_text():
    with patch("codemie.rest_api.main.logger") as mock_logger:
        response = await elastic_exception_handler(_request(), RuntimeError("secret internal detail elasticsearch"))

    body = response.body.decode()
    assert response.status_code == 503
    assert "secret internal detail" not in body
    assert json.loads(body)["error"]["details"] == f"{INTERNAL_ERROR_DETAILS} Request ID: req-123"
    assert mock_logger.error.call_args.kwargs["exc_info"] is True
