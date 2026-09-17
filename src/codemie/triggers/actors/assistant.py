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

import uuid
from typing import Optional

import httpx

from codemie.configs import logger
from codemie.rest_api.security.authentication import sign_internal_request
from codemie.triggers.actors.conversation import create_conversation, save_error_history_turn
from codemie.triggers.config import BASE_API_URL


async def invoke_assistant(
    assistant_id: str,
    user_id: str,
    job_id: str,
    task: Optional[str] = "Do it",
    url: str = BASE_API_URL,
    trigger_source: str = "Webhook",
):
    """Invoke assistant."""
    headers = {
        'Content-Type': 'application/json',
        **sign_internal_request(user_id),
    }
    created_conversation_id = await create_conversation(
        assistant_id=assistant_id,
        conversation_name=f"{trigger_source}: {assistant_id}",
        user_id=user_id,
        job_id=job_id,
        url=url,
    )
    conversation_id = created_conversation_id or str(uuid.uuid4())
    data = {
        'conversation_id': conversation_id,
        'text': task,
        'content_raw': f'<p>{task}</p>',
        'stream': False,
    }

    logger.info('Invoking triggered actor "invoke_assistant". job_id: %s, assistant: %s', job_id, assistant_id)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url=f'{url.rstrip("/")}/v1/assistants/{assistant_id}/model',
                headers=headers,
                json=data,
                timeout=600,
            )
            response.raise_for_status()
            logger.info('Successfully invoked assistant: %s, job_id: %s', assistant_id, job_id)
            return {"conversation_id": conversation_id}
    except httpx.RequestError as e:
        logger.error(
            'Failed to invoke assistant %s for job_id %s: %s',
            assistant_id,
            job_id,
            type(e).__name__,
        )
        if created_conversation_id:
            await save_error_history_turn(
                conversation_id=created_conversation_id,
                assistant_id=assistant_id,
                user_id=user_id,
                job_id=job_id,
                error_message=(
                    f'Failed to invoke assistant: {type(e).__name__}. '
                    'Refresh or fix the MCP token/configuration, then re-run or '
                    'wait for the next webhook/scheduler execution.'
                ),
                url=url,
            )
    except httpx.HTTPStatusError as e:
        logger.error(
            'Failed to invoke assistant %s for job_id %s: HTTP %s %s',
            assistant_id,
            job_id,
            e.response.status_code,
            type(e).__name__,
        )
