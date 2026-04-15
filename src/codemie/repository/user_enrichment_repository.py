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

"""Repository for user enrichment data access."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.rest_api.models.user_management import UserEnrichment


class UserEnrichmentRepository:
    """Read-only repository for user enrichment records."""

    async def get_by_emails(self, session: AsyncSession, emails: list[str]) -> dict[str, UserEnrichment]:
        """Fetch enrichment records for a list of emails.

        Returns a mapping of lower-cased email → UserEnrichment.
        Missing emails are simply absent from the result dict.
        """
        if not emails:
            return {}
        normalised = [e.lower() for e in emails]
        statement = select(UserEnrichment).where(UserEnrichment.email.in_(normalised))
        result = await session.execute(statement)
        return {row.email.lower(): row for row in result.scalars().all()}


user_enrichment_repository = UserEnrichmentRepository()
