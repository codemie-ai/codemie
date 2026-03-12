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

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from codemie.rest_api.models.settings import LiteLLMCredentials


def get_litellm_credentials_for_user(user_id: str, user_applications: list[str]) -> Optional["LiteLLMCredentials"]:
    """
    Get LiteLLM credentials for user from core SettingsService.

    This function stays in core because it directly imports and uses
    core SettingsService which cannot be accessed from enterprise package.

    Checks user-level settings first, then application-level settings.

    Args:
        user_id: User ID
        user_applications: List of applications user has access to

    Returns:
        LiteLLMCredentials or None if not found

    Usage:
        from codemie.enterprise.litellm import get_litellm_credentials_for_user

        creds = get_litellm_credentials_for_user(user.id, user.project_names)
        if creds:
            api_key = creds.api_key
    """
    from codemie.configs import logger
    from codemie.core.exceptions import ExtendedHTTPException
    from codemie.service.settings.settings import SettingsService

    # Try user-level credentials first
    try:
        creds = SettingsService.get_litellm_creds(project_name=None, user_id=user_id)
        if creds:
            logger.debug(f"Found user-level LiteLLM credentials for {user_id}")
            return creds
    except (ExtendedHTTPException, ValueError, KeyError) as e:
        logger.debug(f"No user-level LiteLLM credentials for {user_id}: {e}")
    except Exception as e:
        logger.warning(f"Unexpected error retrieving user-level LiteLLM credentials for {user_id}: {e}")

    # Try application-level credentials
    if user_applications:
        for app in user_applications:
            try:
                creds = SettingsService.get_litellm_creds(project_name=app, user_id=user_id)
                if creds:
                    logger.debug(f"Found app-level LiteLLM credentials for {user_id} in {app}")
                    return creds
            except (ExtendedHTTPException, ValueError, KeyError) as e:
                logger.debug(f"No LiteLLM credentials for {user_id} in {app}: {e}")
                continue
            except Exception as e:
                logger.warning(f"Unexpected error retrieving LiteLLM credentials for {user_id} in {app}: {e}")
                continue

    return None
