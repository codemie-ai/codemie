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

import requests
from requests.exceptions import RequestException

from codemie.configs import logger
from codemie.core.models import UpdateConversationRequest
from codemie.rest_api.security.authentication import BIND_KEY_HEADER, get_bind_key
from codemie.rest_api.security.user import USER_ID_HEADER
from codemie.triggers.config import BASE_API_URL

CONTENT_TYPE_JSON = 'application/json'


def create_conversation(assistant_id: str, conversation_name: str, user_id: str, job_id: str, url: str = BASE_API_URL):
    """Create conversation."""
    headers = {
        'Content-Type': CONTENT_TYPE_JSON,
        USER_ID_HEADER: user_id,
        BIND_KEY_HEADER: get_bind_key(),
    }
    data = {
        'initial_assistant_id': assistant_id,
        'folder': 'job',
    }

    logger.info('Invoking triggered actor "create_conversation", job_id: %s.', job_id)

    try:
        response = requests.post(url=f'{url.rstrip("/")}/v1/conversations', headers=headers, json=data, timeout=600)
        response.raise_for_status()
        conversation_id = response.json().get('id')
        if conversation_id:
            update_conversation(
                conversation_id, UpdateConversationRequest(name=conversation_name), user_id, job_id, url=url
            )
            return conversation_id
        else:
            logger.error('Failed to get conversation ID from response: %s', response.json())
            return None
    except RequestException as e:
        logger.error('Failed to create conversation: %s', str(e))
        return None


def update_conversation(
    conversation_id: str,
    update_request: UpdateConversationRequest,
    user_id: str,
    job_id: str,
    url: str = BASE_API_URL,
):
    """Update conversation."""
    headers = {
        'Content-Type': CONTENT_TYPE_JSON,
        USER_ID_HEADER: user_id,
        BIND_KEY_HEADER: get_bind_key(),
    }
    data = update_request.model_dump()

    logger.info(
        'Invoking triggered actor "update_conversation", job_id: %s, conversation_id: %s', job_id, conversation_id
    )

    try:
        response = requests.put(
            url=f'{url.rstrip("/")}/v1/conversations/{conversation_id}', headers=headers, json=data, timeout=600
        )
        response.raise_for_status()
        logger.info('Successfully updated conversation: %s', conversation_id)
    except RequestException as e:
        logger.error('Failed to update conversation %s: %s', conversation_id, str(e))


def delete_conversation(conversation_id: str, user_id: str, job_id: str, url: str = BASE_API_URL):
    """Delete conversation."""
    headers = {
        'Content-Type': CONTENT_TYPE_JSON,
        USER_ID_HEADER: user_id,
        BIND_KEY_HEADER: get_bind_key(),
    }

    logger.info(
        'Invoking triggered actor "delete_conversation", job_id: %s, conversation_id: %s', job_id, conversation_id
    )

    try:
        response = requests.delete(
            url=f'{url.rstrip("/")}/v1/conversations/{conversation_id}', headers=headers, timeout=600
        )
        response.raise_for_status()
        logger.info('Successfully deleted conversation: %s', conversation_id)
    except RequestException as e:
        logger.warning('Failed to delete orphan conversation %s: %s', conversation_id, str(e))
