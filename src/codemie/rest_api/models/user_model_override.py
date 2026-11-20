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

"""Database model for user-level LLM model overrides within a project."""

import uuid

from sqlalchemy import CheckConstraint, Column, ForeignKeyConstraint, String as SQLString, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlmodel import Field as SQLField

from codemie.rest_api.models.base import BaseModelWithSQLSupport


class UserModelOverride(BaseModelWithSQLSupport, table=True):
    """
    User-level override for LLM model access within a project.

    Once a row exists it is the user's whole model set; the project whitelist is not consulted.
    One override per (user_id, project_name) pair; a missing row means the user has no override.

    The row hangs off the user's membership of the project (user_projects), so it goes when the membership
    does and follows it when the membership is renamed or the user's id is remapped. Both foreign keys are
    deferrable: the user id remap (migration c6f1a2b3d4e5) moves users.id and its children in one transaction.
    """

    __tablename__ = "user_model_overrides"

    id: uuid.UUID = SQLField(default_factory=uuid.uuid4, primary_key=True)
    user_id: str = SQLField(nullable=False, index=True)
    project_name: str = SQLField(max_length=100, nullable=False, index=True)
    allowed_models: list[str] = SQLField(sa_column=Column(ARRAY(SQLString), nullable=False))
    default_model: str | None = SQLField(default=None, max_length=255)

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_model_overrides_user_id",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["user_id", "project_name"],
            ["user_projects.user_id", "user_projects.project_name"],
            name="fk_user_model_overrides_membership",
            ondelete="CASCADE",
            onupdate="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint("user_id", "project_name", name="uq_user_model_override_user_project"),
        CheckConstraint(
            "array_length(allowed_models, 1) IS NOT NULL",
            name="ck_user_model_override_has_models",
        ),
    )
