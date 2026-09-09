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

"""EPMCDME-14586/14587: OAuth folded into the existing integration types.

A per-user OAuth integration is persisted under the base credential type (``Jira``/``Confluence``, or
the shared ``Git`` type for GitLab — only GitLab has OAuth on Git) and marked by an
``auth_type=oauth`` credential value, so OAuth is an authentication method within the existing type
rather than a distinct type. This module is the single source of truth that resolves the OAuth
provider for a persisted Setting or an inbound SettingRequest.
"""

from typing import Optional

from codemie_tools.base.models import CredentialTypes

AUTH_TYPE_KEY = "auth_type"
OAUTH_AUTH_TYPE = "oauth"

OAUTH_PROVIDER_JIRA = "jira"
OAUTH_PROVIDER_CONFLUENCE = "confluence"
OAUTH_PROVIDER_GITLAB = "gitlab"

# Base credential type an OAuth provider folds into.
_PROVIDER_BY_BASE_TYPE = {
    CredentialTypes.JIRA: OAUTH_PROVIDER_JIRA,
    CredentialTypes.CONFLUENCE: OAUTH_PROVIDER_CONFLUENCE,
    CredentialTypes.GIT: OAUTH_PROVIDER_GITLAB,
}


def _has_oauth_marker(obj) -> bool:
    credential_values = getattr(obj, "credential_values", None) or []
    return any(cred.key == AUTH_TYPE_KEY and cred.value == OAUTH_AUTH_TYPE for cred in credential_values)


def oauth_provider(obj) -> Optional[str]:
    """Resolve the per-user OAuth provider ('jira' | 'confluence' | 'gitlab') for a Setting or a
    SettingRequest, or None for non-OAuth (PAT). Reads the auth_type marker straight off
    credential_values (auth_type is not a sensitive field, so it is plaintext for both a saved
    Setting and an inbound request)."""
    if _has_oauth_marker(obj):
        return _PROVIDER_BY_BASE_TYPE.get(obj.credential_type)
    return None
