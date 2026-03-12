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
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel
from sqlmodel import Field as SQLField

from codemie.rest_api.models.base import BaseModelWithSQLSupport


class ConfigValueType(str, Enum):
    """Value types supported for dynamic configuration"""

    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"


class DynamicConfig(BaseModelWithSQLSupport, table=True):
    """
    Dynamic configuration table for runtime-updatable settings.

    Stores configuration as key-value pairs with type metadata.
    All values stored as strings in DB. Type conversion happens in the service layer
    (DynamicConfigService.get()) when programmatically accessing configs, not at the
    REST API layer (endpoints return raw string values).
    Inherits id, date (created_at), update_date from BaseModelWithSQLSupport.
    """

    __tablename__ = "dynamic_config"

    # Core fields
    key: str = SQLField(unique=True, index=True, nullable=False, max_length=255)
    value: str = SQLField(nullable=False, max_length=10000)
    value_type: ConfigValueType = SQLField(nullable=False)
    description: Optional[str] = SQLField(default=None, max_length=500)

    # Audit field (date and update_date inherited from base)
    updated_by: str = SQLField(nullable=False)


# Pydantic models for API request/response


class DynamicConfigCreateRequest(BaseModel):
    """Request model for creating a new dynamic configuration"""

    key: str
    value: str
    value_type: ConfigValueType
    description: Optional[str] = None


class DynamicConfigUpdateRequest(BaseModel):
    """Request model for updating an existing dynamic configuration"""

    value: str
    value_type: Optional[ConfigValueType] = None
    description: Optional[str] = None


class DynamicConfigResponse(BaseModel):
    """Response model for dynamic configuration"""

    id: str
    key: str
    value: str
    value_type: ConfigValueType
    description: Optional[str]
    created_at: datetime
    updated_at: datetime
    updated_by: str

    class Config:
        from_attributes = True
