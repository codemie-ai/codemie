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

"""Workflow REST API actor."""

from typing import Optional
import requests
from requests.exceptions import RequestException

from codemie.configs import logger
from codemie.core.workflow_models import CreateWorkflowExecutionRequest
from codemie.rest_api.security.authentication import BIND_KEY_HEADER, get_bind_key
from codemie.rest_api.security.user import USER_ID_HEADER
from codemie.triggers.config import BASE_API_URL


def invoke_workflow(
    workflow_id: str, user_id: str, job_id: str, task: Optional[str] = "Do it", url: str = BASE_API_URL
):
    """Invoke workflow"""
    try:
        headers = {
            'Content-Type': 'application/json',
            USER_ID_HEADER: user_id,
            BIND_KEY_HEADER: get_bind_key(),
        }
        full_url = f'{url.rstrip("/")}/v1/workflows/{workflow_id}/executions'

        # What about stream boolean that we always having on routers?
        # Maybe we wanted to have it disabled for troggered by webhook workflows?
        data = CreateWorkflowExecutionRequest(user_input=task).dict()
        logger.info(
            'Invoking triggered actor "invoke_workflow". job_id: %s, workflow_id: %s, url: %s',
            job_id,
            workflow_id,
            full_url,
        )
        response = requests.post(url=full_url, headers=headers, json=data, timeout=600)
        response.raise_for_status()
        logger.info('Workflow invoked successfully. job_id: %s, workflow_id: %s', job_id, workflow_id)
    except RequestException as e:
        logger.error('Failed to invoke workflow. job_id: %s, workflow_id: %s, error: %s', job_id, workflow_id, str(e))
