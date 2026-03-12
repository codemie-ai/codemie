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

from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.security import APIKeyHeader

from codemie.configs import logger
from codemie.rest_api.models.index import IndexInfo

router = APIRouter(
    tags=["Callbacks"],
    prefix="/v1",
    dependencies=[],
)

otp_header = APIKeyHeader(
    name="X-Callback-OTP", auto_error=False, description="One-time password for callback authentication"
)


@router.post("/callbacks/index/{index_id}")
def datasource_callback(index_id: str, payload: dict = Body(), otp_value: str = Depends(otp_header)):
    """
    Callback endpoint for provider datasource operations.

    This endpoint is called by provider service after processing a datasource.
    """

    logger.info(f"Received callback for datasource {index_id}: {payload}")

    index = IndexInfo.find_by_id(index_id)

    if not index or not index.provider_fields:
        raise HTTPException(status_code=404, detail=f"External provider data source with id {index_id} not found")

    if otp_value != index.provider_fields.otp:
        raise HTTPException(status_code=403, detail="Invalid or expired OTP")

    status = payload.get("status")

    if not status:
        raise HTTPException(status_code=400, detail="Missing status in callback payload")

    if status == "Cancelled":
        error = "Process was canceled by external provider"
        index.set_error(error)
    elif status == "Error":
        error = payload.get("errors", "Unknown external provider error")
        index.set_error(error)
    elif status == "Completed":
        index.complete_progress()
    else:
        raise HTTPException(status_code=400, detail=f"Invalid status '{status}' in callback payload")

    index.reset_otp()

    return {"status": "success"}
