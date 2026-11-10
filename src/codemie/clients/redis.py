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

from codemie.configs import config


def redis_configured() -> bool:
    """Is Redis configured at all? True when ``REDIS_HOST`` is non-empty.

    Distinct from reachability (a configured Redis can still be down at call time) —
    this answers only "is there a Redis to talk to".
    """
    return bool(config.REDIS_HOST)


def google_oauth_available() -> bool:
    """Outside Standalone, always available. In Standalone, only when the flag is on and Redis is configured."""
    return True if not config.STANDALONE else bool(config.GOOGLE_OAUTH_ENABLED and redis_configured())


def create_redis_client():
    import redis

    return redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        password=config.REDIS_PASSWORD or None,
        ssl=config.REDIS_SSL,
        ssl_cert_reqs=config.REDIS_SSL_CERT_REQS if config.REDIS_SSL else None,
        decode_responses=False,
        socket_connect_timeout=config.REDIS_CONNECT_TIMEOUT_SECONDS,
        socket_timeout=config.REDIS_TIMEOUT_SECONDS,
    )
