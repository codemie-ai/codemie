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

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from codemie.configs import logger
from codemie.triggers.bindings.webhook import WebhookService
from codemie.triggers.trigger_exceptions import NotImplementedDatasource

router = APIRouter(
    tags=["Webhooks"],
    prefix="/v1",
    dependencies=[],
)


@router.post("/webhooks/{webhook_id}")
async def invoke_webhook(
    request: Request,
    webhook_id: str,
    background_tasks: BackgroundTasks,
):
    try:
        return await WebhookService.invoke_webhook_logic(request, webhook_id, background_tasks)
    except NotImplementedDatasource as e:
        logger.error(f"Not implemented webhook {webhook_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except Exception as e:
        logger.error(f"Error processing webhook {webhook_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Webhook processing failed")
