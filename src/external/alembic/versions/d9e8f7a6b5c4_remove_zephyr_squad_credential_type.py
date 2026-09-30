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

"""remove_zephyr_squad_credential_type

EPMCDME-10913: ZephyrSquad is fully removed from the platform (no deprecation path —
the product owner confirmed zero prod usage and that the ZephyrSquad service itself no
longer exists). Delete any leftover rows first, then drop the enum label.

Zero ZEPHYR_SQUAD rows are expected, but the migration does not assert it: the product owner
asked to delete such integrations unconditionally. Instead, the number of deleted rows is logged
(WARNING when non-zero) so the assumption is verifiable in the migration output of every
environment. The delete is irreversible: downgrade() restores the enum label only, not the rows.

The value lists below describe the enum as fa14587c0de1 (EPMCDME-14587) leaves it: that
revision folded GITLAB_OAUTH, JIRA_OAUTH and CONFLUENCE_OAUTH into their base types, so
listing the folded values here would recreate them. The revisions between it and
d3c838ee6ab9 do not change the enum. tests/codemie/migrations/
test_credentialtypes_enum_migrations.py checks both lists against the chain.

Revision ID: d9e8f7a6b5c4
Revises: d3c838ee6ab9
Create Date: 2026-09-03 00:00:00.000000

"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from alembic_postgresql_enum import TableReference

# revision identifiers, used by Alembic.
revision: str = 'd9e8f7a6b5c4'
down_revision: Union[str, None] = 'd3c838ee6ab9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger(__name__)

# The credentialtypes enum as fa14587c0de1 leaves it, minus ZEPHYR_SQUAD.
_ENUM_VALUES_WITHOUT_ZEPHYR_SQUAD = [
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

# The same enum with ZEPHYR_SQUAD back in its original position, for downgrade.
_ENUM_VALUES_WITH_ZEPHYR_SQUAD = [
    *_ENUM_VALUES_WITHOUT_ZEPHYR_SQUAD[:22],
    "ZEPHYR_SQUAD",
    *_ENUM_VALUES_WITHOUT_ZEPHYR_SQUAD[22:],
]

_AFFECTED_COLUMNS = [
    TableReference(table_schema='codemie', table_name='settings', column_name='credential_type'),
]


def upgrade() -> None:
    """Remove ZEPHYR_SQUAD from credentialtypes enum."""
    # Compare as text: a literal 'ZEPHYR_SQUAD' cast to the enum fails once the label is gone,
    # which would break a re-run of this migration.
    result = op.get_bind().execute(sa.text("DELETE FROM codemie.settings WHERE credential_type::text = 'ZEPHYR_SQUAD'"))
    if result.rowcount:
        logger.warning("Deleted %d ZEPHYR_SQUAD settings rows (0 expected)", result.rowcount)
    else:
        logger.info("No ZEPHYR_SQUAD settings rows to delete")
    op.sync_enum_values(
        enum_schema='codemie',
        enum_name='credentialtypes',
        new_values=_ENUM_VALUES_WITHOUT_ZEPHYR_SQUAD,
        affected_columns=_AFFECTED_COLUMNS,
        enum_values_to_rename=[],
    )


def downgrade() -> None:
    """Restore ZEPHYR_SQUAD in credentialtypes enum (rows deleted by upgrade are not restored)."""
    op.sync_enum_values(
        enum_schema='codemie',
        enum_name='credentialtypes',
        new_values=_ENUM_VALUES_WITH_ZEPHYR_SQUAD,
        affected_columns=_AFFECTED_COLUMNS,
        enum_values_to_rename=[],
    )
