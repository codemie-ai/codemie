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

"""Tests for the Headroom sidecar compressor."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from codemie.service.headroom.compressors.base import CompressOutcome, HeadroomCompressionError
from codemie.service.headroom.models import HeadroomStrategy, ResolvedHeadroomConfig
from codemie.service.headroom.compressors.sidecar_compressor import SidecarCompressor
from codemie.service.headroom.compressors.strategies import sidecar_config

POST = "codemie.service.headroom.compressors.sidecar_compressor.httpx.post"
SECRET_TOKEN = "s3cr3t-token-value"
SECRET_TEXT = "confidential message body"
MESSAGES = [{"role": "user", "content": SECRET_TEXT}]
MODEL = "gpt-4o"


@pytest.fixture(autouse=True)
def sidecar_settings():
    with patch("codemie.service.headroom.compressors.sidecar_compressor.config") as mock_config:
        mock_config.HEADROOM_SIDECAR_URL = "http://headroom:8787/"
        mock_config.HEADROOM_PROXY_TOKEN = SECRET_TOKEN
        mock_config.HEADROOM_TIMEOUT_SECONDS = 7
        yield mock_config


def _cfg(strategy: HeadroomStrategy = HeadroomStrategy.BALANCED, user: bool = True) -> ResolvedHeadroomConfig:
    return ResolvedHeadroomConfig(compress_user_messages=user, compress_tool_outputs=True, strategy=strategy)


def _response(status: int = 200, json_body=None, json_error: Exception | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    if json_error is not None:
        response.json.side_effect = json_error
    else:
        response.json.return_value = json_body
    return response


def _ok_body() -> dict:
    return {"messages": [{"role": "user", "content": "short"}], "tokens_before": 100, "tokens_after": 40}


def test_compress_posts_expected_request_and_parses_outcome():
    with patch(POST, return_value=_response(200, _ok_body())) as post:
        outcome = SidecarCompressor().compress(MESSAGES, MODEL, _cfg())

    assert SidecarCompressor.mode == "sidecar"
    assert outcome == CompressOutcome(
        messages=[{"role": "user", "content": "short"}], tokens_before=100, tokens_after=40
    )
    args, kwargs = post.call_args
    assert args == ("http://headroom:8787/v1/compress",)
    assert kwargs["headers"] == {"Authorization": f"Bearer {SECRET_TOKEN}"}
    assert kwargs["timeout"] == 7
    assert kwargs["json"] == {
        "messages": MESSAGES,
        "model": MODEL,
        "config": sidecar_config(HeadroomStrategy.BALANCED, True),
    }


@pytest.mark.parametrize("strategy", list(HeadroomStrategy))
@pytest.mark.parametrize("user", [True, False])
def test_compress_sends_strategy_config(strategy, user):
    with patch(POST, return_value=_response(200, _ok_body())) as post:
        SidecarCompressor().compress(MESSAGES, MODEL, _cfg(strategy, user))

    assert post.call_args.kwargs["json"]["config"] == sidecar_config(strategy, user)


def test_compress_omits_authorization_header_when_token_empty(sidecar_settings):
    sidecar_settings.HEADROOM_PROXY_TOKEN = ""
    with patch(POST, return_value=_response(200, _ok_body())) as post:
        SidecarCompressor().compress(MESSAGES, MODEL, _cfg())

    assert "Authorization" not in (post.call_args.kwargs.get("headers") or {})


def test_compress_returns_fail_open_variant_as_unchanged_outcome():
    body = {
        "messages": MESSAGES,
        "tokens_before": 0,
        "tokens_after": 0,
        "compression_skipped": True,
        "skip_reason": "error",
    }
    with patch(POST, return_value=_response(200, body)):
        outcome = SidecarCompressor().compress(MESSAGES, MODEL, _cfg())

    assert outcome == CompressOutcome(messages=MESSAGES, tokens_before=0, tokens_after=0)


@pytest.mark.parametrize(
    "side_effect",
    [
        httpx.ReadTimeout(f"timeout {SECRET_TOKEN} {SECRET_TEXT}"),
        httpx.ConnectError(f"refused {SECRET_TOKEN} {SECRET_TEXT}"),
    ],
    ids=["timeout", "connection_error"],
)
def test_compress_wraps_transport_errors(side_effect):
    with patch(POST, side_effect=side_effect):
        with pytest.raises(HeadroomCompressionError) as exc_info:
            SidecarCompressor().compress(MESSAGES, MODEL, _cfg())

    assert isinstance(exc_info.value.__cause__, httpx.HTTPError)
    assert SECRET_TOKEN not in str(exc_info.value)
    assert SECRET_TEXT not in str(exc_info.value)


@pytest.mark.parametrize("status", [400, 401, 500, 503])
def test_compress_raises_on_non_2xx_status(status):
    body = {"detail": f"{SECRET_TOKEN} {SECRET_TEXT}"}
    with patch(POST, return_value=_response(status, body)):
        with pytest.raises(HeadroomCompressionError) as exc_info:
            SidecarCompressor().compress(MESSAGES, MODEL, _cfg())

    assert str(status) in str(exc_info.value)
    assert SECRET_TOKEN not in str(exc_info.value)
    assert SECRET_TEXT not in str(exc_info.value)


def test_compress_raises_on_non_json_body():
    with patch(POST, return_value=_response(200, json_error=ValueError(SECRET_TEXT))):
        with pytest.raises(HeadroomCompressionError) as exc_info:
            SidecarCompressor().compress(MESSAGES, MODEL, _cfg())

    assert SECRET_TEXT not in str(exc_info.value)


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"tokens_before": 1, "tokens_after": 1},
        {"messages": "nope", "tokens_before": 1, "tokens_after": 1},
        {"messages": ["not-a-dict"], "tokens_before": 1, "tokens_after": 1},
        {"messages": [], "tokens_after": 1},
        {"messages": [], "tokens_before": 1},
        {"messages": [], "tokens_before": "1", "tokens_after": 1},
        {"messages": [], "tokens_before": 1, "tokens_after": 1.5},
        {"messages": [], "tokens_before": True, "tokens_after": 1},
    ],
)
def test_compress_raises_on_malformed_body(body):
    with patch(POST, return_value=_response(200, body)):
        with pytest.raises(HeadroomCompressionError) as exc_info:
            SidecarCompressor().compress(MESSAGES, MODEL, _cfg())

    assert SECRET_TOKEN not in str(exc_info.value)
    assert SECRET_TEXT not in str(exc_info.value)
