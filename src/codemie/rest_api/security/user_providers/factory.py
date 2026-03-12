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

from codemie.configs import config

from codemie.rest_api.security.user_providers.base import UserProvider


def get_user_provider() -> UserProvider:
    """Get appropriate user provider based on feature flag

    Returns:
        LegacyJwtUserProvider if ENABLE_USER_MANAGEMENT=False
        PersistentUserProvider if ENABLE_USER_MANAGEMENT=True
    """
    if config.ENABLE_USER_MANAGEMENT:
        from codemie.rest_api.security.user_providers.persistent import PersistentUserProvider

        return PersistentUserProvider()
    else:
        from codemie.rest_api.security.user_providers.legacy_jwt import LegacyJwtUserProvider

        return LegacyJwtUserProvider()
