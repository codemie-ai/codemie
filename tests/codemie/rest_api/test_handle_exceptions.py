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

import logging
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codemie.rest_api.main import configure_logging, handle_exceptions

_REQUEST_ID = "test-uuid-deadbeef"
_HEADERS = {"X-Request-ID": _REQUEST_ID}


def _build_app() -> FastAPI:
    app = FastAPI()

    # Same order as production: registered first -> inner; registered last -> outermost.
    app.middleware("http")(configure_logging)
    app.middleware("http")(handle_exceptions)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret: /srv/app/codemie/secret.py line 42")

    return app


_app = _build_app()
_client = TestClient(_app, raise_server_exceptions=False)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def _get_non_local():
    with patch("codemie.rest_api.main.config") as mock_cfg:
        mock_cfg.is_local = False
        return _client.get("/boom", headers=_HEADERS)


def test_non_local_hides_traceback():
    response = _get_non_local()

    assert response.status_code == 500
    body = response.text
    # None of these must appear in any deployed-environment 500 response
    assert "Traceback" not in body
    assert "File " not in body
    assert "RuntimeError" not in body
    assert "secret" not in body
    assert "/srv" not in body


def test_non_local_includes_request_id():
    assert _REQUEST_ID in _get_non_local().json()["error"]["details"]


def test_non_local_preserves_error_shape():
    data = _get_non_local().json()
    assert "error" in data
    assert "message" in data["error"]
    assert "details" in data["error"]
    assert "help" in data["error"]


def test_logged_exception_carries_returned_request_id():
    from codemie.rest_api.main import logger

    handler = _Capture()
    logger.addHandler(handler)
    try:
        response = _get_non_local()
    finally:
        logger.removeHandler(handler)

    # record_factory renders the traceback into the message, so match on its text
    errors = [r for r in handler.records if r.levelno == logging.ERROR and "Traceback" in r.getMessage()]
    assert errors, "exception was not logged with a traceback"
    # The id the client sees must be the id on the log record holding the traceback
    assert errors[-1].uuid == _REQUEST_ID
    assert _REQUEST_ID in response.json()["error"]["details"]


def test_local_reraises():
    with patch("codemie.rest_api.main.config") as mock_cfg:
        mock_cfg.is_local = True
        local_client = TestClient(_app, raise_server_exceptions=True)
        with pytest.raises(RuntimeError):
            local_client.get("/boom", headers=_HEADERS)
