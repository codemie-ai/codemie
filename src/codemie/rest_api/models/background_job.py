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

from __future__ import annotations

from typing import Optional

from sqlalchemy import BigInteger, Column, Integer, String
from sqlmodel import Field as SQLField, SQLModel


class BackgroundJob(SQLModel, table=True):
    """Background job record written by external services."""

    __tablename__ = "background_jobs"
    __table_args__ = {"schema": "codemie"}

    id: Optional[int] = SQLField(default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True))
    job_type: str = SQLField(sa_column=Column(String(50), nullable=False))
    timestamp: int = SQLField(sa_column=Column(BigInteger, nullable=False))
    status: str = SQLField(sa_column=Column(String(20), nullable=False))
    error: Optional[str] = SQLField(default=None)
