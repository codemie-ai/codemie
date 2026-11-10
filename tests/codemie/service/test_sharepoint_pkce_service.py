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

"""Unit tests for SharePointPKCEService.initiate()'s Redis-failure message."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service import sharepoint_pkce_service as sharepoint_pkce_service_module
from codemie.service.sharepoint_pkce_service import SharePointPKCEService


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _service_with_failing_redis():
    redis = MagicMock()
    redis.set.side_effect = Exception("redis down")
    encryption = MagicMock()
    encryption.encrypt.side_effect = lambda value: value.encode() if isinstance(value, str) else value
    return SharePointPKCEService(redis_client=redis, encryption_service=encryption)


@pytest.mark.anyio
async def test_initiate_raises_502_on_redis_failure_outside_standalone():
    """Non-Standalone: unchanged generic 502."""
    service = _service_with_failing_redis()

    with patch.object(sharepoint_pkce_service_module.config, "STANDALONE", False):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await service.initiate("user-1", "client-id", "tenant-id")

    assert exc_info.value.code == 502
    assert exc_info.value.message == "Failed to initiate authentication"


@pytest.mark.anyio
async def test_initiate_raises_503_on_redis_failure_in_standalone():
    """Standalone: same 503 message as its sibling handle_callback."""
    service = _service_with_failing_redis()

    with patch.object(sharepoint_pkce_service_module.config, "STANDALONE", True):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await service.initiate("user-1", "client-id", "tenant-id")

    assert exc_info.value.code == 503
    assert exc_info.value.message == "Authentication service temporarily unavailable. Please try again."
