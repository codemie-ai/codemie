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
from codemie.rest_api.models.logs import LogEntry, LogRetrieveRequest
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.logs import LogService
from codemie.core.exceptions import ExtendedHTTPException

router = APIRouter(tags=["Logs"], prefix="/v1", dependencies=[Depends(authenticate)])


@router.post(
    "/logs",
    status_code=status.HTTP_200_OK,
    response_model=list[LogEntry],
    response_model_by_alias=True,
)
def get_logs_by_target_field(target_field: LogRetrieveRequest, user: User = Depends(authenticate)):
    """
    Retrieve log entries by field and value from the Elasticsearch index.

    Args:
        target_field (LogRetrieveRequest): The field and value to filter log entries. Currently supported field values:
        - conversation_id
        - execution_id
        - request_uuid

    Returns:
        list[LogEntry]: A list of matched log entries.
    """
    try:
        return LogService.get_logs_by_target_field(target_field)
    except Exception as e:
        raise ExtendedHTTPException(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=str(e))
