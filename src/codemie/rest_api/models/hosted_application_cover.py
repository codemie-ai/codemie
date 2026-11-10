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

"""Owner-uploaded card cover of a static hosted application (EPMCDME-16001, revision e3b7c1a95d42).

``data`` holds up to 1 MiB of image bytes: catalog and list queries select only keys from this table;
the image itself is read by the thumbnail path alone.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, LargeBinary, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlmodel import Field, SQLModel


class HostedApplicationCovers(SQLModel, table=True):
    __tablename__ = 'hosted_application_covers'
    __table_args__ = (
        CheckConstraint("media_type IN ('image/png', 'image/jpeg', 'image/webp')"),
        CheckConstraint('octet_length(sha256) = 32'),
        CheckConstraint('byte_size BETWEEN 1 AND 1048576'),
        CheckConstraint('width BETWEEN 1 AND 2048'),
        CheckConstraint('height BETWEEN 1 AND 2048'),
        ForeignKeyConstraint(['app_id'], ['hosted_applications.id'], ondelete='CASCADE'),
        CheckConstraint('octet_length(data) = byte_size'),
        CheckConstraint('width * height <= 4194304'),
    )

    app_id: UUID = Field(sa_column=Column(PGUUID(as_uuid=True), nullable=False, primary_key=True))
    media_type: str = Field(sa_column=Column(Text, nullable=False))
    sha256: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    byte_size: int = Field(sa_column=Column(Integer, nullable=False))
    width: int = Field(sa_column=Column(Integer, nullable=False))
    height: int = Field(sa_column=Column(Integer, nullable=False))
    data: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    updated_by: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
