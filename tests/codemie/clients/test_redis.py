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

"""Unit tests for the Redis-configured predicate."""

from unittest.mock import patch

from codemie.clients import redis as redis_client


def test_redis_configured_true_when_host_set():
    with patch.object(redis_client.config, "REDIS_HOST", "localhost"):
        assert redis_client.redis_configured() is True


def test_redis_configured_false_when_host_empty():
    with patch.object(redis_client.config, "REDIS_HOST", ""):
        assert redis_client.redis_configured() is False
