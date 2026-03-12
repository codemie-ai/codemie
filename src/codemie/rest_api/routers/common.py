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

from fastapi import APIRouter, status

from codemie.configs import config
from codemie.core.constants import APP_DESCRIPTION
from codemie.core.models import InfoResponse

router = APIRouter(
    tags=["Common"],
    prefix="/v1",
    dependencies=[],
)


@router.get("/info", status_code=status.HTTP_200_OK, response_model=InfoResponse)
def app_info():
    return InfoResponse(
        message="Codemie",
        version=config.APP_VERSION,
        description=APP_DESCRIPTION,
    )


@router.get("/healthcheck", include_in_schema=False)
def healthcheck():
    import os
    import psutil

    process = psutil.Process(os.getpid())
    return {
        "status": "healthy",
        "memory_usage_mb": process.memory_info().rss / 1024 / 1024,
        "cpu_percent": process.cpu_percent(),
        "worker_pid": os.getpid(),
    }
