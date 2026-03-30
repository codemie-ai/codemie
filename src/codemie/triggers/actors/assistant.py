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
from typing import Optional

import requests
from requests.exceptions import RequestException

from codemie.configs import logger
from codemie.rest_api.security.authentication import BIND_KEY_HEADER, get_bind_key
from codemie.rest_api.security.user import USER_ID_HEADER
from codemie.triggers.config import BASE_API_URL


def invoke_assistant(
    assistant_id: str, user_id: str, job_id: str, task: Optional[str] = "Do it", url: str = BASE_API_URL
):
    """Invoke assistant."""
    headers = {
        'Content-Type': 'application/json',
        USER_ID_HEADER: user_id,
        BIND_KEY_HEADER: get_bind_key(),
    }
    data = {
        'conversation_id': str(uuid.uuid4()),
        'text': task,
        'content_raw': f'<p>{task}</p>',
        'stream': False,
    }

    logger.info('Invoking triggered actor "invoke_assistant". job_id: %s, assistant: %s', job_id, assistant_id)

    try:
        response = requests.post(
            url=f'{url}/v1/assistants/{assistant_id}/model', headers=headers, json=data, timeout=600
        )
        response.raise_for_status()
        logger.info('Successfully invoked assistant: %s, job_id: %s', assistant_id, job_id)
    except RequestException as e:
        logger.error('Failed to invoke assistant %s for job_id %s: %s', assistant_id, job_id, str(e))
