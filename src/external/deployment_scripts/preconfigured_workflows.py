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

import re

from codemie.configs import logger, config
from codemie.core.models import UserEntity
from codemie.core.workflow_models import WorkflowConfig, WorkflowConfigTemplate
from codemie.rest_api.models.index import SearchFields
from codemie.rest_api.models.settings import CredentialValues
from codemie_tools.base.models import CredentialTypes
from codemie.service.settings.base_settings import CHANGEME_PROMPT, CHANGEME_URL
from codemie.service.settings.settings import SettingsService
from codemie.service.workflow_config import WorkflowConfigIndexService
from codemie.service.workflow_service import WorkflowService
from external.deployment_scripts.preconfigured_assistants import get_preconfigured_assistant_id_by_slug

workflow_service = WorkflowService()


def patch_template_with_real_assistant_ids(template: WorkflowConfigTemplate):
    """
    This is a (hacky) workaround to use real assistants in workflow templates.
    It replaces "PRECONFIGURED:preconfigured-assistant-slug" by real assistant-id
    in template.assistants.assistant_id - but this is not enough!
    We need also to patch the execution config (large string) by doing the same substitution there.

    If some assistants are not available - we replace the string with NOT FOUND:assistant-slug for easy debugging
    """
    for assistant_def in template.assistants:
        pattern = r"PRECONFIGURED:(\S+)"

        if not assistant_def.assistant_id:
            continue

        # Check if agent id requires substitution
        match = re.match(pattern, assistant_def.assistant_id)
        if match:
            agent_slug = match.group(1)
            assistant_id = get_preconfigured_assistant_id_by_slug(agent_slug)
            if not assistant_id:
                assistant_id = "NOT FOUND:" + agent_slug

            assistant_def.assistant_id = assistant_id
            template.yaml_config = template.yaml_config.replace("PRECONFIGURED:" + agent_slug, assistant_id)


def create_preconfigured_workflow(slug: str, name: str, project_name: str):
    """
    Creates a workflow from the template. If the template contains references to assistant ids
    in form of PRECONFIGURED:assistant-slug - and the assistant was created by preconfigured_assistants.py -
    this reference would be replaced by actual assistant UUID
    """
    query = {"name": name, SearchFields.PROJECT_NAME: project_name}
    workflows = WorkflowConfigIndexService.find_workflows_by_filters(filters=query)

    if workflows:
        logger.info(f"Workflow '{name}' already exists in '{project_name}', not updating it!")
        return

    template = workflow_service.get_prebuilt_workflow_by_slug(slug)

    if not template:
        logger.error(f"No workflow template with slug '{slug}' found!")
        return

    patch_template_with_real_assistant_ids(template)

    logger.info(f"Creating workflow '{name}' in '{project_name}' using template '{slug}'")

    workflow_config = WorkflowConfig(**template.model_dump())
    user_model = UserEntity(user_id="system", username="system", name="system")
    workflow_config.created_by = user_model
    workflow_config.save(refresh=True)


def create_amna_workflows(slug_prefix: str):
    available_workflows = workflow_service.get_prebuilt_workflows()
    for workflow in available_workflows:
        slug = workflow.slug
        name = workflow.name
        if not slug.startswith(slug_prefix):
            continue

        create_preconfigured_workflow(slug, name, "demo")


def get_amna_integration_preconfigured_credential_values(service_settings: dict):
    key_defaults = {
        SettingsService.URL: CHANGEME_URL,
        SettingsService.IS_CLOUD: False,
    }

    credential_values = []
    for key in service_settings.keys():
        credential_values.append(CredentialValues(key=key, value=key_defaults.get(key, CHANGEME_PROMPT)))
    return credential_values


def create_preconfigured_workflows():
    """
    Creates all preconfigured flows if they do not already exist.
    """
    # Put your flow creation statements below like:
    # create_preconfigured_flow("flow_template_slug", "flow_name_as_defined_by_template", "project_name")
    if config.AMNA_AIRN_PRECREATE_WORKFLOWS:
        # Create preconfigured integrations for assistants
        SettingsService.create_project_credentials_if_missing(
            project_name="codemie",
            credential_type=CredentialTypes.ENVIRONMENT_VARS,
            credential_values=[
                CredentialValues(key="AWS_REGION", value=CHANGEME_PROMPT),
                CredentialValues(key="AWS_ACCESS_KEY_ID", value=CHANGEME_PROMPT),
                CredentialValues(key="AWS_SECRET_ACCESS_KEY", value=CHANGEME_PROMPT),
                CredentialValues(key="AWS_SESSION_TOKEN", value=CHANGEME_PROMPT),
            ],
            integration_alias="amna-codemie-aws-integration",
        )

        create_amna_workflows("amna-")

        # Create preconfigured integrations for workflows
        SettingsService.create_project_credentials_if_missing(
            project_name="demo",
            credential_type=CredentialTypes.PLUGIN,
            credential_values=[CredentialValues(key=SettingsService.PLUGIN_KEY, value=CHANGEME_PROMPT)],
            integration_alias="amna-demo-plugin-integration",
        )
        SettingsService.create_project_credentials_if_missing(
            project_name="demo",
            credential_type=CredentialTypes.GIT,
            credential_values=get_amna_integration_preconfigured_credential_values(SettingsService.GIT_FIELDS),
            integration_alias="amna-demo-git-integration",
        )
        SettingsService.create_project_credentials_if_missing(
            project_name="demo",
            credential_type=CredentialTypes.JIRA,
            credential_values=get_amna_integration_preconfigured_credential_values(SettingsService.JIRA_FIELDS),
            integration_alias="amna-demo-jira-integration",
        )
        SettingsService.create_project_credentials_if_missing(
            project_name="demo",
            credential_type=CredentialTypes.CONFLUENCE,
            credential_values=get_amna_integration_preconfigured_credential_values(SettingsService.CONFLUENCE_FIELDS),
            integration_alias="amna-demo-confluence-integration",
        )
