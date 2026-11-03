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

"""Registration of a repository remote to the shared project that receives its CLI analytics."""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class RepositoryProjectRegistration(SQLModel, table=True):
    """One normalized remote maps to at most one project, so routing by repository is never ambiguous."""

    __tablename__ = "repository_project_registrations"
    __table_args__ = {"schema": "codemie"}

    normalized_remote: str = Field(primary_key=True, unique=True)
    project_name: str = Field(index=True)
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
