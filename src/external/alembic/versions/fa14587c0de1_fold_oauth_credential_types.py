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

"""fold_oauth_credential_types

EPMCDME-14586/14587: OAuth becomes an authentication method within the existing integration types
rather than a standalone credential type. Existing GITLAB_OAUTH / JIRA_OAUTH / CONFLUENCE_OAUTH
settings rows are rewritten into their base type (GIT / JIRA / CONFLUENCE) carrying an
``auth_type=oauth`` marker in ``credential_values``; the three OAuth values are then removed from the
``credentialtypes`` enum. GOOGLE_OAUTH is a separate feature and is kept. The enum stores member
names, so rows and enum values are matched by name (e.g. ``JIRA_OAUTH`` -> ``JIRA``).

Both steps are idempotent: the marker is only appended when absent, and rows are only rewritten while
the OAuth values still exist.

Revision ID: fa14587c0de1
Revises: 67b7c43d6090
Create Date: 2026-09-03 12:45:00.000000

"""

from typing import Sequence, Union

from alembic import op
from alembic_postgresql_enum import TableReference

revision: str = "fa14587c0de1"
down_revision: Union[str, Sequence[str], None] = "67b7c43d6090"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The credentialtypes enum after the three folded OAuth values are removed (GOOGLE_OAUTH kept).
_ENUM_VALUES_FOLDED = [
    "JIRA",
    "CONFLUENCE",
    "GIT",
    "SVN",
    "KUBERNETES",
    "AWS",
    "GCP",
    "KEYCLOAK",
    "AZURE",
    "ELASTIC",
    "OPEN_API",
    "PLUGIN",
    "FILE_SYSTEM",
    "SCHEDULER",
    "WEBHOOK",
    "EMAIL",
    "AZURE_DEVOPS",
    "SONAR",
    "SQL",
    "TELEGRAM",
    "ZEPHYR_SCALE",
    "_ZEPHYR_CLOUD",
    "ZEPHYR_SQUAD",
    "XRAY",
    "SERVICENOW",
    "REPORT_PORTAL",
    "ENVIRONMENT_VARS",
    "AUTH_TOKEN",
    "A2A",
    "LITE_LLM",
    "SHAREPOINT",
    "XWIKI",
    "GOOGLE_OAUTH",
    "DIAL",
    "MS_TEAMS",
]

# The same enum with the three folded OAuth values restored, for downgrade.
_ENUM_VALUES_WITH_OAUTH = _ENUM_VALUES_FOLDED + ["GITLAB_OAUTH", "JIRA_OAUTH", "CONFLUENCE_OAUTH"]

_AFFECTED_COLUMNS = [
    TableReference(table_schema="codemie", table_name="settings", column_name="credential_type"),
]

# Folded OAuth credential type name -> base credential type name.
_FOLD = {
    "JIRA_OAUTH": "JIRA",
    "CONFLUENCE_OAUTH": "CONFLUENCE",
    "GITLAB_OAUTH": "GIT",
}

_OAUTH_MARKER = '[{"key": "auth_type", "value": "oauth"}]'


def upgrade() -> None:
    # 1. Stamp the auth_type=oauth marker onto every OAuth row that does not already carry it.
    op.execute(
        f"""
        UPDATE codemie.settings
        SET credential_values = credential_values || '{_OAUTH_MARKER}'::jsonb
        WHERE credential_type IN ('GITLAB_OAUTH', 'JIRA_OAUTH', 'CONFLUENCE_OAUTH')
          AND NOT (credential_values @> '{_OAUTH_MARKER}'::jsonb)
        """
    )
    # 2. Rewrite the credential type to its base type (base values already exist in the enum).
    for oauth_name, base_name in _FOLD.items():
        op.execute(
            f"UPDATE codemie.settings SET credential_type = '{base_name}' WHERE credential_type = '{oauth_name}'"
        )
    # 3. Drop the now-unused OAuth values from the enum.
    op.sync_enum_values(
        enum_schema="codemie",
        enum_name="credentialtypes",
        new_values=_ENUM_VALUES_FOLDED,
        affected_columns=_AFFECTED_COLUMNS,
        enum_values_to_rename=[],
    )


def downgrade() -> None:
    # 1. Restore the OAuth enum values so rows can be typed back.
    op.sync_enum_values(
        enum_schema="codemie",
        enum_name="credentialtypes",
        new_values=_ENUM_VALUES_WITH_OAUTH,
        affected_columns=_AFFECTED_COLUMNS,
        enum_values_to_rename=[],
    )
    # 2. Re-split OAuth rows (base type + auth_type=oauth marker) back into the standalone OAuth type.
    for oauth_name, base_name in _FOLD.items():
        op.execute(
            f"""
            UPDATE codemie.settings
            SET credential_type = '{oauth_name}'
            WHERE credential_type = '{base_name}'
              AND credential_values @> '{_OAUTH_MARKER}'::jsonb
            """
        )
    # 3. Remove the marker (it is implied by the standalone OAuth type again) by rebuilding the
    #    array without the auth_type=oauth element.
    op.execute(
        f"""
        UPDATE codemie.settings
        SET credential_values = (
            SELECT COALESCE(jsonb_agg(elem), '[]'::jsonb)
            FROM jsonb_array_elements(credential_values) AS elem
            WHERE NOT (elem = '{{"key": "auth_type", "value": "oauth"}}'::jsonb)
        )
        WHERE credential_type IN ('GITLAB_OAUTH', 'JIRA_OAUTH', 'CONFLUENCE_OAUTH')
          AND credential_values @> '{_OAUTH_MARKER}'::jsonb
        """
    )
