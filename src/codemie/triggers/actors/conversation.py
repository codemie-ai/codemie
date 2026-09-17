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

import httpx

from codemie.configs import logger
from codemie.core.models import UpdateConversationRequest
from codemie.rest_api.security.authentication import sign_internal_request
from codemie.triggers.config import BASE_API_URL

CONTENT_TYPE_JSON = 'application/json'


async def create_conversation(
    assistant_id: str, conversation_name: str, user_id: str, job_id: str, url: str = BASE_API_URL
):
    """Create conversation."""
    headers = {
        'Content-Type': CONTENT_TYPE_JSON,
        **sign_internal_request(user_id),
    }
    data = {
        'initial_assistant_id': assistant_id,
        'folder': 'job',
    }

    logger.info('Invoking triggered actor "create_conversation", job_id: %s.', job_id)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url=f'{url.rstrip("/")}/v1/conversations', headers=headers, json=data, timeout=600
            )
            response.raise_for_status()
            conversation_id = response.json().get('id')
            if conversation_id:
                await update_conversation(
                    conversation_id, UpdateConversationRequest(name=conversation_name), user_id, job_id, url=url
                )
                return conversation_id
            else:
                logger.error('Failed to get conversation ID from response: %s', response.json())
                return None
    except httpx.HTTPError as e:
        logger.error('Failed to create conversation: %s', str(e))
        return None


async def update_conversation(
    conversation_id: str,
    update_request: UpdateConversationRequest,
    user_id: str,
    job_id: str,
    url: str = BASE_API_URL,
):
    """Update conversation."""
    headers = {
        'Content-Type': CONTENT_TYPE_JSON,
        **sign_internal_request(user_id),
    }
    data = update_request.model_dump()

    logger.info(
        'Invoking triggered actor "update_conversation", job_id: %s, conversation_id: %s', job_id, conversation_id
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(
                url=f'{url.rstrip("/")}/v1/conversations/{conversation_id}',
                headers=headers,
                json=data,
                timeout=600,
            )
            response.raise_for_status()
            logger.info('Successfully updated conversation: %s', conversation_id)
    except httpx.HTTPError as e:
        logger.error('Failed to update conversation %s: %s', conversation_id, str(e))


async def save_error_history_turn(
    conversation_id: str,
    assistant_id: str,
    user_id: str,
    job_id: str,
    error_message: str,
    url: str = BASE_API_URL,
) -> None:
    """Persist a single assistant-role error message onto an existing conversation."""
    headers = {
        'Content-Type': CONTENT_TYPE_JSON,
        **sign_internal_request(user_id),
    }
    data = {
        'assistant_id': assistant_id,
        'history': [{'role': 'assistant', 'message': error_message}],
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(
                url=f'{url.rstrip("/")}/v1/conversations/{conversation_id}/history',
                headers=headers,
                json=data,
                timeout=60,
            )
            response.raise_for_status()
            logger.info(
                'Saved error history turn for conversation %s, job_id %s',
                conversation_id,
                job_id,
            )
    except httpx.HTTPError as e:
        logger.warning(
            'Failed to save error history for conversation %s: %s',
            conversation_id,
            type(e).__name__,
        )
