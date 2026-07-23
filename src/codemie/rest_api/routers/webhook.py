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

import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from codemie.configs import config, logger
from codemie.triggers.bindings.webhook import WebhookService
from codemie.triggers.bindings.webhook_rate_limiter import get_rate_limiter
from codemie.triggers.trigger_exceptions import NotImplementedDatasource


def _check_rate_limit(webhook_id: str) -> None:
    if not config.WEBHOOK_RATE_LIMIT_ENABLED:
        return
    try:
        allowed, retry_after = get_rate_limiter().check_and_increment(webhook_id)
    except Exception as e:
        logger.error(f"Webhook rate limiter unavailable for {webhook_id}: {e}")
        return  # fail-open: do not block the request if Redis is down
    if not allowed:
        logger.warning(
            f"Webhook rate limit exceeded for webhook_id={webhook_id}: "
            f"limit={config.WEBHOOK_RATE_LIMIT_MAX_REQUESTS} requests "
            f"per {config.WEBHOOK_RATE_LIMIT_WINDOW_SECONDS}s, retry_after={retry_after}s"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Webhook rate limit exceeded: maximum {config.WEBHOOK_RATE_LIMIT_MAX_REQUESTS} requests "
                f"per {config.WEBHOOK_RATE_LIMIT_WINDOW_SECONDS} seconds allowed."
            ),
            headers={"Retry-After": str(retry_after)},
        )


router = APIRouter(
    tags=["Webhooks"],
    prefix="/v1",
    dependencies=[],
)


@router.post("/webhooks/{webhook_id}", dependencies=[Depends(_check_rate_limit)])
async def invoke_webhook(
    request: Request,
    webhook_id: str,
    background_tasks: BackgroundTasks,
):
    try:
        raw_payload = await request.body()
        return await asyncio.to_thread(
            WebhookService.invoke_webhook_logic, request, webhook_id, background_tasks, raw_payload
        )
    except NotImplementedDatasource as e:
        logger.error(f"Not implemented webhook {webhook_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing webhook {webhook_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Webhook processing failed")
