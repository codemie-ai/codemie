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

from typing import Dict

from codemie.rest_api.models.assistant import Assistant, Context
from codemie.configs import logger
from codemie.configs.customer_config import customer_config
from codemie.rest_api.models.guardrail import GuardrailEntity
from codemie.rest_api.utils.default_applications import CODEMIE_PROJECT_NAME
from codemie.rest_api.models.index import IndexInfo
from codemie.service.assistant.assistant_service import assistant_service
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.guardrail.guardrail_service import GuardrailService
from external.deployment_scripts.index_util import create_index_from_dump


# Elasticsearch field names for querying
REPO_NAME_FIELD = "repo_name.keyword"
PROJECT_NAME_FIELD = "project_name.keyword"

# slug->UUID of assistants created/updated from templates (for later use in flows)
preconfigured_assistant_ids: Dict[str, str] = {}


def get_assistant_index_name(assistant_slug: str) -> str | None:
    if not customer_config.has_assistant_config(assistant_slug):
        return None

    assistant_config = customer_config.get_assistant_config(assistant_slug)
    return assistant_config.get("index_name") if assistant_config else None


def delete_disabled_assistant(assistant_slug: str) -> bool:
    """
    Delete an assistant that is disabled in customer config.

    Args:
        assistant_slug (str): The slug of the assistant to delete.

    Returns:
        bool: True if assistant was deleted, False if it didn't exist.
    """
    existing_assistant = Assistant.get_by_fields({"slug.keyword": assistant_slug})
    if existing_assistant:
        logger.info(f"Deleting disabled assistant '{assistant_slug}'")
        if index_name := get_assistant_index_name(assistant_slug):
            delete_context(index_name)
        existing_assistant.delete()
        GuardrailService.remove_guardrail_assignments_for_entity(GuardrailEntity.ASSISTANT, str(existing_assistant.id))
        # Remove from preconfigured_assistant_ids if it exists
        if assistant_slug in preconfigured_assistant_ids:
            del preconfigured_assistant_ids[assistant_slug]
        return True
    return False


def manage_preconfigured_assistants():
    """
    Manage preconfigured assistants based on customer configuration.
    - If assistant slug is in config and enabled: create assistant
    - If assistant slug is in config and disabled: delete assistant
    - If assistant slug is not in config: create assistant (default behavior)
    """
    logger.info("Managing preconfigured assistants based on customer configuration")

    for assistant_slug in customer_config.get_all_configured_assistant_slugs():
        if customer_config.is_assistant_enabled(assistant_slug):
            logger.debug(f"Assistant '{assistant_slug}' is enabled in config, creating if not exists")
            target_project = customer_config.get_assistant_target_project(assistant_slug)
            if target_project is None:
                target_project = CODEMIE_PROJECT_NAME
            create_preconfigured_assistant(assistant_slug, target_project)
        else:
            logger.debug(f"Assistant '{assistant_slug}' is disabled in config, deleting if exists")
            delete_disabled_assistant(assistant_slug)


def get_index_info(repo_name: str) -> IndexInfo | None:
    """
    Get IndexInfo by repository name.
    """
    return IndexInfo.get_by_fields({REPO_NAME_FIELD: repo_name, PROJECT_NAME_FIELD: CODEMIE_PROJECT_NAME})


def delete_context(repo_name: str) -> None:
    """
    Delete IndexInfo for the given repository name.
    """
    if index_info := get_index_info(repo_name):
        index_info.delete()


def create_context_from_index(repo_name: str) -> Context:
    """
    Creates a context from the given repository name by searching the IndexInfo.
    If index does not exist, creates it from dump (for static indices).
    """
    index_info = get_index_info(repo_name) or create_index_from_dump(CODEMIE_PROJECT_NAME, repo_name)
    return Context(context_type=Context.index_info_type(index_info), name=index_info.repo_name)


def get_preconfigured_assistant_id_by_slug(slug: str):
    return preconfigured_assistant_ids.get(slug)


def get_all_contexts(assistant_slug: str, assistant_template: Assistant) -> list[Context]:
    """
    Get all contexts for an assistant, merging static and dynamic contexts.
    Static contexts (from customer config) take priority over template contexts.
    """
    contexts_by_name: dict[str, Context] = {}

    # Add static context first (if configured)
    if index_name := get_assistant_index_name(assistant_slug):
        static_ctx = create_context_from_index(index_name)
        contexts_by_name[static_ctx.name] = static_ctx

    # Add dynamic contexts from template (only if they don't conflict and exist on platform)
    for ctx in assistant_template.context or []:
        if ctx.name not in contexts_by_name and get_index_info(ctx.name):
            contexts_by_name[ctx.name] = ctx

    return list(contexts_by_name.values())


def update_assistant_content(
    existing_assistant: Assistant, assistant_template: Assistant, context: list[Context] | None = None
):
    fields_to_check = {
        'description': assistant_template.description,
        'system_prompt': assistant_template.system_prompt,
        'conversation_starters': assistant_template.conversation_starters,
        'toolkits': assistant_template.toolkits,
        'icon_url': assistant_template.icon_url,
        'llm_model_type': llm_service.default_llm_model,
        'categories': assistant_template.categories,
        'mcp_servers': assistant_template.mcp_servers,
    }

    updates = {
        field: new_value
        for field, new_value in fields_to_check.items()
        if getattr(existing_assistant, field) != new_value
    }

    if context is not None and existing_assistant.context != context:
        updates['context'] = context

    if updates:
        for field, value in updates.items():
            logger.info(f"Updating {field} for assistant '{existing_assistant.slug}'")
            setattr(existing_assistant, field, value)

        existing_assistant.save()
        logger.info(f"Assistant '{existing_assistant.slug}' updated successfully.")
        return True

    return False


def create_preconfigured_assistant(assistant_slug: str, project_name: str = CODEMIE_PROJECT_NAME):
    """
    Creates a preconfigured assistant if it does not already exist.
    Handles both static indices (created from dumps) and dynamic indices (existing on platform).

    Args:
        assistant_slug (str): The assistant template slug to create.
        project_name (str): The project name to create the assistant for.
    """
    # Check if the assistant already exists
    existing_assistant = Assistant.get_by_fields({"slug.keyword": assistant_slug})
    assistant_template = assistant_service.get_assistant_template_by_slug(assistant_slug)

    if not assistant_template:
        logger.warning(f"No template found for assistant slug '{assistant_slug}'")
        return

    # Get all contexts (static + dynamic, without duplicates)
    all_contexts = get_all_contexts(assistant_slug, assistant_template)

    if existing_assistant:
        logger.info(f"Assistant '{assistant_slug}' already exists.")
        preconfigured_assistant_ids[assistant_slug] = existing_assistant.id
        update_assistant_content(existing_assistant, assistant_template, all_contexts)
        return

    # Create the new preconfigured assistant using details from assistant template
    preconfigured_assistant = Assistant(
        name=assistant_template.name,
        description=assistant_template.description,
        system_prompt=assistant_template.system_prompt,
        conversation_starters=assistant_template.conversation_starters,
        is_react=assistant_template.is_react,
        toolkits=assistant_template.toolkits,
        icon_url=assistant_template.icon_url,
        slug=assistant_template.slug,
        context=all_contexts,
        project=project_name,
        llm_model_type=assistant_template.llm_model_type or llm_service.default_llm_model,
        is_global=assistant_template.is_global,
        shared=assistant_template.shared,
        temperature=assistant_template.temperature,
        categories=assistant_template.categories,
        mcp_servers=assistant_template.mcp_servers,
    )

    # Save the new assistant
    preconfigured_assistant.save(refresh=True)
    preconfigured_assistant_ids[assistant_slug] = preconfigured_assistant.id
    logger.info(f"Assistant '{assistant_slug}' created successfully.")
