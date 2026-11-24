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

"""Headroom compressor that calls the Headroom sidecar over HTTP."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx

from codemie.configs import config
from codemie.service.headroom.compressors.base import CompressOutcome, HeadroomCompressionError
from codemie.service.headroom.models import ResolvedHeadroomConfig
from codemie.service.headroom.compressors.strategies import sidecar_config


class SidecarCompressor:
    """Sync client for the sidecar `/v1/compress` endpoint.

    Error messages carry only exception types or status codes: never the token or message content.
    """

    mode = "sidecar"

    def compress(self, messages: list[dict[str, Any]], model: str, cfg: ResolvedHeadroomConfig) -> CompressOutcome:
        url = f"{config.HEADROOM_SIDECAR_URL.rstrip('/')}/v1/compress"
        headers: dict[str, str] = {}
        if config.HEADROOM_PROXY_TOKEN:
            headers["Authorization"] = f"Bearer {config.HEADROOM_PROXY_TOKEN}"
        body = {
            "messages": messages,
            "model": model,
            "config": sidecar_config(cfg.strategy, cfg.compress_user_messages),
        }

        try:
            response = httpx.post(url, json=body, headers=headers, timeout=config.HEADROOM_TIMEOUT_SECONDS)
        except httpx.TimeoutException as exc:
            raise HeadroomCompressionError(f"Headroom sidecar timed out ({type(exc).__name__})") from exc
        except httpx.HTTPError as exc:
            raise HeadroomCompressionError(f"Headroom sidecar request failed ({type(exc).__name__})") from exc

        if not HTTPStatus.OK <= response.status_code < HTTPStatus.MULTIPLE_CHOICES:
            raise HeadroomCompressionError(f"Headroom sidecar returned status {response.status_code}")

        return self._parse(response)

    @staticmethod
    def _parse(response: httpx.Response) -> CompressOutcome:
        try:
            data: Any = response.json()
        except ValueError as exc:
            raise HeadroomCompressionError("Headroom sidecar returned a non-JSON response") from exc

        if not isinstance(data, dict):
            raise HeadroomCompressionError("Headroom sidecar returned a malformed response")

        messages = data.get("messages")
        tokens_before = data.get("tokens_before")
        tokens_after = data.get("tokens_after")
        if (
            not isinstance(messages, list)
            or not all(isinstance(m, dict) for m in messages)
            or not _is_int(tokens_before)
            or not _is_int(tokens_after)
        ):
            raise HeadroomCompressionError("Headroom sidecar returned a malformed response")

        return CompressOutcome(messages=messages, tokens_before=tokens_before, tokens_after=tokens_after)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
