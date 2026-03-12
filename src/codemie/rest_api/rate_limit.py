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

"""Re-export limiter from middleware.rate_limiter

This module exists to provide a cleaner import path and avoid circular imports.
Routers can import from here instead of directly from middleware.
"""

from codemie.rest_api.middleware.rate_limiter import limiter, get_client_ip

__all__ = ["limiter", "get_client_ip"]
