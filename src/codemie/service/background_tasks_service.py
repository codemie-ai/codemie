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

import uuid
from datetime import datetime

from pydantic import BaseModel

from codemie.core.constants import BackgroundTaskStatus
from codemie.core.models import BackgroundTaskRequest
from codemie.rest_api.models.background_tasks import BackgroundTasks


class BackgroundTasksService(BaseModel):
    def save(self, task: BackgroundTaskRequest):
        task_id = str(uuid.uuid4())
        BackgroundTasks(id=task_id, task=task.task, user=task.user, status=task.status, assistant=task.assistant).save()
        return task_id

    def update(
        self, task_id: str, status: BackgroundTaskStatus = None, current_step: str = None, final_output: str = None
    ):
        result = BackgroundTasks.get_by_id(task_id)
        if result:
            if status:
                result.status = status
            if final_output:
                result.final_output = final_output
            if current_step:
                result.current_step = current_step
            result.update_date = datetime.now()
            result.update()

    def get_task(self, task_id: str):
        return BackgroundTasks.get_by_id(task_id)
