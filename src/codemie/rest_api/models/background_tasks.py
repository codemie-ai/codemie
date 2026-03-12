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

from codemie.core.constants import BackgroundTaskStatus
from codemie.core.models import UserEntity, AssistantDetails
from codemie.rest_api.models.base import BaseModelWithSQLSupport, PydanticType
from sqlmodel import Column, Field as SQLField


class BackgroundTasks(BaseModelWithSQLSupport, table=True):
    __tablename__ = "background_tasks"

    task: str
    status: BackgroundTaskStatus
    final_output: str = ""
    current_step: str = ""
    user: UserEntity = SQLField(sa_column=Column(PydanticType(UserEntity)))
    assistant: AssistantDetails = SQLField(sa_column=Column(PydanticType(AssistantDetails)))
