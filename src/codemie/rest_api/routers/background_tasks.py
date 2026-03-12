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

from fastapi import APIRouter, Depends, status
from fastapi import Request

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import BackgroundTaskEntity
from codemie.service.background_tasks_service import BackgroundTasksService
from codemie.rest_api.security.authentication import authenticate, User

router = APIRouter(
    tags=["BackgroundTasks"],
    prefix="/v1",
    dependencies=[],
)

service = BackgroundTasksService()


@router.get(
    "/tasks/{id}",
    status_code=status.HTTP_200_OK,
    response_model=BackgroundTaskEntity,
    response_model_by_alias=True,
)
async def get_task(request: Request, id: str, user: User = Depends(authenticate)):
    """
    Returns background task by id
    """
    try:
        task = service.get_task(id)
    except Exception:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Task not found",
            details=f"The task with ID [{id}] could not be found in the system.",
            help="Please verify the task ID and try again. If you believe this is an error, contact support.",
        )

    return task
