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

from typing import Optional, Literal

from pydantic import BaseModel

from codemie.rest_api.models.base import BaseModelWithSQLSupport
from sqlmodel import Field as SQLField, String


class UserDataResponse(BaseModel):
    sidebar_view: Literal["flat", "folders"] = "flat"


class UserDataChangeRequest(BaseModel):
    sidebar_view: Literal["flat", "folders"] = "flat"


class UserData(BaseModelWithSQLSupport, table=True):
    __tablename__ = "user_data"

    user_id: Optional[str] = SQLField(default=None, index=True)
    sidebar_view: Literal["flat", "folders"] = SQLField(default="flat", sa_type=String)
