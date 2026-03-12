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

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter


def get_client_ip(request: Request) -> str:
    """Extract client IP with proxy awareness

    Order of precedence:
    1. X-Forwarded-For header (first IP if comma-separated)
    2. X-Real-IP header
    3. request.client.host (fallback for direct connections)

    SECURITY: Only use X-Forwarded-For if behind trusted proxy/ingress.
    Misconfigured proxies can allow IP spoofing to bypass rate limits.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # X-Forwarded-For can be comma-separated (client, proxy1, proxy2)
        # Use first IP (original client)
        return forwarded.split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip

    # Fallback to direct connection IP
    return request.client.host if request.client else "unknown"


# Initialize limiter with proxy-aware IP extraction
# Exported for use in routers
limiter = Limiter(key_func=get_client_ip)
