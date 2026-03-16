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

from concurrent.futures import ThreadPoolExecutor
import re
from time import time
from typing import List, Optional, TypedDict
import uuid
from langchain_core.messages import HumanMessage
from botocore.exceptions import ClientError

from codemie.configs import logger
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.assistant import Assistant, AssistantType
from codemie.rest_api.models.guardrail import GuardrailEntity
from codemie.rest_api.models.settings import AWSCredentials, SettingsBase
from codemie.rest_api.models.vendor import ImportAgent
from codemie.rest_api.security.user import User
from codemie.rest_api.utils.default_applications import ensure_application_exists
from codemie.service.aws_bedrock.base_bedrock_service import ALL_SETTINGS_OVERVIEW_ENTITY_COUNT, BaseBedrockService
from codemie.service.aws_bedrock.exceptions import aws_service_exception_handler
from codemie.service.aws_bedrock.utils import (
    CONFIGURATION_INVALID_EXCEPTIONS,
    call_bedrock_listing_api,
    get_all_settings_for_user,
    get_aws_client_for_service,
    get_setting_for_user,
    handle_aws_call,
    get_setting_aws_credentials,
)
from codemie.service.guardrail.guardrail_service import GuardrailService


class InvokeAgentResponse(TypedDict):
    output: str
    time_elapsed: float


class BedrockAgentService(BaseBedrockService):
    @staticmethod
    @aws_service_exception_handler("Bedrock agents")
    def get_all_settings_overview(user: User, page: int, per_page: int):
        settings = get_all_settings_for_user(user)

        start = page * per_page
        end = start + per_page
        paged_settings = settings[start:end]

        total = len(settings)
        results = {
            "data": [],
            "pagination": {
                "total": total,
                "pages": (total + per_page - 1) // per_page,
                "page": page,
                "per_page": per_page,
            },
        }

        if not paged_settings:
            return results

        # Parallelize the AWS API calls
        with ThreadPoolExecutor(max_workers=min(len(paged_settings), 10)) as executor:
            # Submit all tasks, preserving submission order
            futures = [
                executor.submit(BedrockAgentService._fetch_main_entity_names_for_setting, setting)
                for setting in paged_settings
            ]

            # Collect results in submission order to keep stable pagination order
            for setting, future in zip(paged_settings, futures, strict=False):
                try:
                    agent_names = future.result()

                    results["data"].append(
                        {
                            "setting_id": str(setting.id),
                            "setting_name": setting.alias,
                            "project": setting.project_name,
                            "entities": agent_names if agent_names is not None else [],
                        }
                    )
                except Exception as e:
                    logger.error(f"Error processing setting {setting.id}: {e}")

                    is_config_error = any(error in str(e) for error in CONFIGURATION_INVALID_EXCEPTIONS)

                    results["data"].append(
                        {
                            "setting_id": str(setting.id),
                            "setting_name": setting.alias,
                            "project": setting.project_name,
                            "entities": [],
                            "invalid": is_config_error,
                            "error": str(e),
                        }
                    )
                    continue

        return results

    @staticmethod
    @aws_service_exception_handler("Bedrock agents")
    def list_main_entities(
        user: User,
        setting_id: str,
        page: int,
        per_page: int,
        next_token: Optional[str] = None,
    ) -> tuple[List[dict], Optional[str]]:
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        aws_creds = get_setting_aws_credentials(setting.id)

        all_agents, return_next_token = BedrockAgentService._bedrock_list_all_agents(
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
            page=page,
            per_page=per_page,
            next_token=next_token,
        )

        agent_data = []
        for agent_info in all_agents:
            agent_status = agent_info.get("agentStatus")
            if agent_status != "PREPARED":
                agent_status = "NOT_PREPARED"

            agent_data.append(
                {
                    "id": agent_info.get("agentId"),
                    "name": agent_info.get("agentName"),
                    "status": agent_status,
                    "description": agent_info.get("description"),
                    "updatedAt": agent_info.get("updatedAt"),
                }
            )

        return agent_data, return_next_token

    @staticmethod
    @aws_service_exception_handler("Bedrock agents")
    def get_main_entity_detail(
        user: User,
        main_entity_id: str,
        setting_id: str,
    ) -> dict:
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        aws_creds = get_setting_aws_credentials(setting.id)

        agent_info = BedrockAgentService._bedrock_get_agent(
            agent_id=main_entity_id,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
        )

        agent_status = agent_info.get("agentStatus")
        if agent_status != "PREPARED":
            agent_status = "NOT_PREPARED"

        return {
            "id": agent_info.get("agentId"),
            "name": agent_info.get("agentName"),
            "status": agent_status,
            "description": agent_info.get("description"),
            "updatedAt": agent_info.get("updatedAt"),
        }

    @staticmethod
    @aws_service_exception_handler("Bedrock agents")
    def list_importable_entities_for_main_entity(
        user: User,
        main_entity_id: str,
        setting_id: str,
        page: int,
        per_page: int,
        next_token: Optional[str] = None,
    ) -> tuple[List[dict], Optional[str]]:
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        existing_entities = Assistant.get_by_bedrock_aws_settings_id(str(setting.id))
        existing_entities_map = {
            assistant.bedrock.bedrock_agent_alias_id: assistant for assistant in existing_entities if assistant.bedrock
        }

        aws_creds = get_setting_aws_credentials(setting.id)

        agent_aliases = []

        agent_aliases_information, return_next_token = BedrockAgentService._bedrock_list_agent_aliases(
            main_entity_id,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
            page=page,
            per_page=per_page,
            next_token=next_token,
        )

        for alias_info in agent_aliases_information:
            alias_status = alias_info.get("agentAliasStatus")
            alias_invocation_state = alias_info.get("aliasInvocationState")
            if alias_status != "PREPARED" or (
                alias_invocation_state and alias_invocation_state != "ACCEPT_INVOCATIONS"
            ):
                alias_status = "NOT_PREPARED"

            alias_id = alias_info["agentAliasId"]

            if not alias_info.get("routingConfiguration"):
                logger.warning(f"No routing configuration found for alias {alias_id} of agent {main_entity_id}.")
                version = None
            else:
                # There can be a DRAFT version, which is not a valid version for our usecase
                version = alias_info["routingConfiguration"][0]["agentVersion"]

                if not re.match(r"^\d{1,5}$", version):
                    alias_status = "NOT_PREPARED"

            alias_dict = {
                "id": alias_id,
                "name": alias_info["agentAliasName"],
                "status": alias_status,
                "description": alias_info.get("description"),
                "version": version,
                "createdAt": alias_info["createdAt"],
                "updatedAt": alias_info["updatedAt"],
            }

            if alias_id in existing_entities_map:
                alias_dict["aiRunId"] = existing_entities_map[alias_id].id

            agent_aliases.append(alias_dict)

        return agent_aliases, return_next_token

    @staticmethod
    @aws_service_exception_handler("Bedrock agents")
    def get_importable_entity_detail(
        user: User,
        main_entity_id: str,
        importable_entity_detail: str,
        setting_id: str,
    ):
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        aws_creds = get_setting_aws_credentials(setting.id)

        version_info = BedrockAgentService._bedrock_get_agent_version(
            agent_id=main_entity_id,
            agent_version=importable_entity_detail,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
        )

        if not version_info:
            logger.warning(
                f"Failed to retrieve version information for agent {main_entity_id}, "
                f"version: {importable_entity_detail}"
            )
            return {}

        status = version_info.get("agentStatus")
        if status != "PREPARED":
            status = "NOT_PREPARED"

        return {
            "id": version_info.get("agentId"),
            "name": version_info.get("agentName"),
            "status": status,
            "version": version_info.get("version"),
            "instruction": version_info.get("instruction"),
            "foundationModel": version_info.get("foundationModel"),
            "description": version_info.get("description"),
            "createdAt": version_info.get("createdAt"),
            "updatedAt": version_info.get("updatedAt"),
        }

    @staticmethod
    @aws_service_exception_handler("Bedrock agents")
    def import_entities(user: User, import_payload: dict[str, List[ImportAgent]]):
        results = []

        for setting_id, agent_alias_ids in import_payload.items():
            setting: SettingsBase = get_setting_for_user(user, setting_id)

            # Retrieve all existing assistants for this settings
            existing_entities = Assistant.get_by_bedrock_aws_settings_id(str(setting.id))
            existing_entities_map = {
                assistant.bedrock.bedrock_agent_alias_id: assistant
                for assistant in existing_entities
                if assistant.bedrock
            }

            aws_creds = get_setting_aws_credentials(setting.id)
            agent_version_info_cache = {}

            for agent_alias_id in agent_alias_ids:
                results.append(
                    BedrockAgentService._process_alias_import(
                        user=user,
                        setting=setting,
                        aws_creds=aws_creds,
                        existing_entities_map=existing_entities_map,
                        input_agent_id=agent_alias_id.id,
                        input_agent_alias_id=agent_alias_id.agentAliasId,
                        agent_version_info_cache=agent_version_info_cache,
                    )
                )

        return results

    @staticmethod
    def delete_entities(setting_id: str):
        existing_entities = Assistant.get_by_bedrock_aws_settings_id(setting_id)
        for assistant in existing_entities:
            assistant.delete()
            GuardrailService.remove_guardrail_assignments_for_entity(GuardrailEntity.ASSISTANT, str(assistant.id))

    @staticmethod
    def validate_remote_entity_exists_and_cleanup(entity: Assistant):
        if (
            entity.type != AssistantType.BEDROCK_AGENT
            or not entity.bedrock
            or not entity.bedrock.bedrock_agent_id
            or not entity.bedrock.bedrock_agent_alias_id
            or not entity.bedrock.bedrock_aws_settings_id
        ):
            return None  # not a bedrock entity

        try:
            aws_creds = get_setting_aws_credentials(entity.bedrock.bedrock_aws_settings_id)

            BedrockAgentService._bedrock_get_agent_alias(
                agent_id=entity.bedrock.bedrock_agent_id,
                agent_alias_id=entity.bedrock.bedrock_agent_alias_id,
                region=aws_creds.region,
                access_key_id=aws_creds.access_key_id,
                secret_access_key=aws_creds.secret_access_key,
                session_token=aws_creds.session_token,
            )

            return None

        except ClientError as e:
            error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            if error_code.strip().lower() == "resourcenotfoundexception":
                entity.delete()
                GuardrailService.remove_guardrail_assignments_for_entity(GuardrailEntity.ASSISTANT, str(entity.id))

                return entity.name
            else:
                # any other issue (like configuration) is just ignored at this point
                logger.error(f"Unexpected ClientError validating Bedrock agent for assistant {entity.name}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error validating Bedrock agent for assistant {entity.name}: {e}")

    @staticmethod
    def validate_remote_entity_exists_and_cleanup_with_subassistants(entity: Assistant):
        deleted_assistants_names = []
        deleted_subassistant_ids = []

        # Process subassistants
        for subassistant_id in entity.assistant_ids:
            subassistant = Assistant.find_by_id(subassistant_id)
            if subassistant:
                deleted_name = BedrockAgentService.validate_remote_entity_exists_and_cleanup(subassistant)
                if deleted_name:
                    deleted_assistants_names.append(deleted_name)
                    deleted_subassistant_ids.append(subassistant_id)
            else:
                # Subassistant no longer exists in database - remove from parent
                deleted_subassistant_ids.append(subassistant_id)

        # Check main entity
        deleted_name = BedrockAgentService.validate_remote_entity_exists_and_cleanup(entity)
        if deleted_name:
            deleted_assistants_names.append(deleted_name)
            # Entity deleted - no need to update assistant_ids
        elif deleted_assistants_names:  # Only update if entity survives AND subassistants were deleted
            entity.assistant_ids = [aid for aid in entity.assistant_ids if aid not in deleted_subassistant_ids]
            entity.save()

        return deleted_assistants_names

    @staticmethod
    def invoke_agent(
        assistant: Assistant,
        input_text: str,
        conversation_id: str,
        chat_history: Optional[List] = None,
    ) -> InvokeAgentResponse:
        """
        Invokes a Bedrock agent with the provided input text and session state.

        This method retrieves AWS credentials, prepares the session state, and calls the Bedrock agent
        using the AWS Bedrock API. It measures the time taken for the invocation and returns the response.

        Args:
            assistant (Assistant): The assistant object containing Bedrock agent details.
            input_text (str): The input text to send to the Bedrock agent.
            conversation_id (str): The unique identifier for the conversation session.
            chat_history (Optional[List]): The chat history to include in the session state.

        Returns:
            InvokeAgentResponse: A dictionary containing the agent's output and the time elapsed.
        """
        start_time = time()

        if (
            not assistant.bedrock
            or not assistant.bedrock.bedrock_agent_id
            or not assistant.bedrock.bedrock_agent_alias_id
            or not assistant.bedrock.bedrock_aws_settings_id
        ):
            raise ValueError("Trying to AWS invoke non-bedrock assistant.")

        try:
            aws_creds = get_setting_aws_credentials(assistant.bedrock.bedrock_aws_settings_id)

            input_text, session_state = BedrockAgentService._prepare_session_state(input_text, chat_history)

            response = BedrockAgentService._bedrock_invoke_agent(
                agent_id=assistant.bedrock.bedrock_agent_id,
                agent_alias=assistant.bedrock.bedrock_agent_alias_id,
                input_text=input_text,
                session_id=conversation_id,
                region=aws_creds.region,
                access_key_id=aws_creds.access_key_id,
                secret_access_key=aws_creds.secret_access_key,
                session_token=aws_creds.session_token,
                session_state=session_state,
            )

            return {
                "output": response,
                "time_elapsed": time() - start_time,
            }
        except ClientError as e:
            error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            if error_code.strip().lower() == "resourcenotfoundexception":
                logger.warning(f"Bedrock agent not found on remote {assistant.bedrock.bedrock_agent_id}: {e}")

                BedrockAgentService.validate_remote_entity_exists_and_cleanup_with_subassistants(
                    assistant
                )  # if the resource was deleted in the meantime, delete the local record
            else:
                logger.error(f"AWS ClientError invoking Bedrock agent {assistant.bedrock.bedrock_agent_id}: {e}")

            return {
                "output": str(e),
                "time_elapsed": time() - start_time,
            }
        except Exception as e:
            logger.error(f"Unexpected error invoking Bedrock agent {assistant.bedrock.bedrock_agent_id}: {e}")
            return {
                "output": str(e),
                "time_elapsed": time() - start_time,
            }

    @staticmethod
    def _fetch_main_entity_names_for_setting(setting) -> List[str] | None:
        """
        Fetch agent names for a single setting.
        """
        aws_creds = get_setting_aws_credentials(setting.id)

        all_agents, _ = BedrockAgentService._bedrock_list_all_agents(
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
            page=0,
            per_page=ALL_SETTINGS_OVERVIEW_ENTITY_COUNT,
            max_retry_attempts=1,  # only 1 attempt to avoid incorrect setting config timeouts
        )

        agent_names = []
        for agent_count, agent_info in enumerate(all_agents):
            if agent_count >= ALL_SETTINGS_OVERVIEW_ENTITY_COUNT:
                break
            agent_names.append(agent_info["agentName"])

        return agent_names

    @staticmethod
    def _prepare_session_state(input_text: str, chat_history: Optional[List]) -> tuple[str, dict]:
        """
        Prepares the session state for a Bedrock agent invocation.

        This method formats the input text and chat history into a session state dictionary
        compatible with the Bedrock API.

        Args:
            input_text (str): The input text to send to the agent.
            chat_history (Optional[List]): The chat history to include in the session state.

        Returns:
            tuple[str, dict]: A tuple containing the updated input text and the session state dictionary.
        """

        def extract_text(content):
            if isinstance(content, str):
                return content
            if isinstance(content, list) and content and isinstance(content[0], dict):
                return content[0].get("text", "")
            return str(content)

        session_state = {}
        if chat_history and isinstance(chat_history[-1], HumanMessage):
            last_msg = chat_history.pop()
            input_text = f"{input_text}\n\n{last_msg.content}" if input_text else last_msg.content

        if chat_history:
            session_state = {"conversationHistory": {"messages": []}}
            for message in chat_history:
                session_state["conversationHistory"]["messages"].append(
                    {
                        "content": [{"text": extract_text(message.content)}],
                        "role": "user" if isinstance(message, HumanMessage) else "assistant",
                    }
                )

        return input_text, session_state

    @staticmethod
    def _process_alias_import(
        user: User,
        setting: SettingsBase,
        aws_creds: AWSCredentials,
        existing_entities_map: dict,
        input_agent_id: str,
        input_agent_alias_id: str,
        agent_version_info_cache: dict,
    ) -> dict:
        try:
            alias_info = BedrockAgentService._bedrock_get_agent_alias(
                agent_id=input_agent_id,
                agent_alias_id=input_agent_alias_id,
                region=aws_creds.region,
                access_key_id=aws_creds.access_key_id,
                secret_access_key=aws_creds.secret_access_key,
                session_token=aws_creds.session_token,
            )

            alias_id = alias_info.get("agentAliasId")
            agent_id = alias_info.get("agentId")
            if not alias_id or not agent_id:
                return {
                    "agentId": agent_id,
                    "agentAliasId": alias_id,
                    "error": {
                        "statusCode": "404",
                        "message": f"Agent with {agent_id} and {alias_id} was not found",
                    },
                }

            if alias_info.get("agentAliasStatus") != "PREPARED":
                return {
                    "agentId": agent_id,
                    "agentAliasId": alias_id,
                    "error": {
                        "statusCode": "409",
                        "message": f"Agent alias {alias_id} for agent {agent_id} is not in PREPARED status",
                    },
                }

            agent_version_info = BedrockAgentService._get_agent_version_info(
                agent_id,
                alias_id,
                alias_info,
                aws_creds,
                agent_version_info_cache,
            )
            if not agent_version_info:
                return {
                    "agentId": agent_id,
                    "agentAliasId": alias_id,
                    "error": {
                        "statusCode": "404",
                        "message": (f"Agent version info not found for agent {agent_id} and alias {alias_id}"),
                    },
                }

            assistant_data = BedrockAgentService._create_assistant_data(
                user=user,
                setting=setting,
                alias_info=alias_info,
                agent_version_info=agent_version_info,
            )

            created_entity_id = BedrockAgentService._create_or_update_entity(
                alias_id=alias_id,
                assistant_data=assistant_data,
                existing_entities_map=existing_entities_map,
                agent_id=agent_id,
            )

            return {
                "agentId": agent_id,
                "agentAliasId": alias_id,
                "aiRunId": created_entity_id,
            }
        except ClientError as e:
            error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            if error_code.strip().lower() == "resourcenotfoundexception":
                return {
                    "agentId": input_agent_id,
                    "agentAliasId": input_agent_alias_id,
                    "error": {
                        "statusCode": "404",
                        "message": (
                            f"Agent with ID {input_agent_id} and alias {input_agent_alias_id} "
                            f"was not found (AWS ResourceNotFoundException)"
                        ),
                    },
                }
            return {
                "agentId": input_agent_id,
                "agentAliasId": input_agent_alias_id,
                "error": {"statusCode": "500", "message": str(e)},
            }
        except Exception as e:
            return {
                "agentId": input_agent_id,
                "agentAliasId": input_agent_alias_id,
                "error": {"statusCode": "500", "message": str(e)},
            }

    @staticmethod
    def _get_agent_version_info(
        agent_id: str,
        alias_id: str,
        alias_info: dict,
        aws_creds: AWSCredentials,
        agent_version_info_cache: dict,
    ) -> Optional[dict]:
        """
        Retrieve and cache version information for a specific AWS Bedrock agent alias.

        This method fetches the version information for a specific alias of a Bedrock agent
        and caches it for reuse. If the alias does not have a valid version, or if the API call fails,
        None is returned.

        Args:
            agent (dict): The Bedrock agent details.
            alias_id (str): The ID of the agent alias.
            alias_info (dict): The alias information dictionary.
            aws_creds (AWSCredentials): The AWS credentials to use for the API call.
            agent_version_info_cache (dict): A cache for storing version information by version number.

        Returns:
            Optional[dict]: The version information for the alias, or None if retrieval fails.
        """
        if not alias_info.get("routingConfiguration"):
            logger.warning(f"No routing configuration found for alias {alias_id} of agent {agent_id}.")
            return None

        if len(alias_info["routingConfiguration"]) > 1:
            logger.warning(f"Multiple routing configurations found for agent {agent_id}. Using the first one.")

        agent_version = alias_info["routingConfiguration"][0]["agentVersion"]

        if agent_version in agent_version_info_cache:
            return agent_version_info_cache[agent_version]

        # There can be a DRAFT version, which is not a valid version and the API call fails
        if not re.match(r"^\d{1,5}$", agent_version):
            logger.warning(
                f"Skipping alias {alias_id} for agent {agent_id} due to invalid agentVersion: {agent_version}"
            )
            return None

        # Retrieve and cache the version information
        version_info = BedrockAgentService._bedrock_get_agent_version(
            agent_id=agent_id,
            agent_version=agent_version,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
        )

        if not version_info:
            logger.warning(
                f"Failed to retrieve version information for agent {agent_id} "
                f"(Alias: {alias_id}), version: {agent_version}"
            )
            return None

        agent_version_info_cache[agent_version] = version_info

        return version_info

    @staticmethod
    def _create_assistant_data(
        user: User,
        setting: SettingsBase,
        alias_info: dict,
        agent_version_info: dict,
    ) -> dict:
        """
        Creates the data dictionary for a Bedrock assistant.

        This method generates the data required to create or update an assistant object
        based on the Bedrock agent, alias, and version information.

        Args:
            user (User): The user creating the assistant.
            setting (Settings): The AWS settings associated with the assistant.
            aws_toolkit (ToolKitDetails): The AWS toolkit details.
            agent (dict): The Bedrock agent details.
            alias_info (dict): The alias information.
            agent_version_info (dict): The version information for the alias.

        Returns:
            dict: The data dictionary for the assistant.
        """

        name = (
            f"{agent_version_info.get('agentName', 'Unknown Agent')}:{alias_info.get('agentAliasId', 'Unknown Alias')}"
        )

        # First 8 characters of UUID for unique slug
        random_suffix = str(uuid.uuid4())[:8]
        unique_slug = f"{name}-{random_suffix}"

        return {
            "name": name,
            "description": agent_version_info.get("description", "AWS Bedrock Agent"),
            "system_prompt": agent_version_info.get("instruction", "No instruction provided"),
            "slug": unique_slug,
            "bedrock": {
                "bedrock_agent_id": agent_version_info["agentId"],
                "bedrock_agent_alias_id": alias_info["agentAliasId"],
                "bedrock_agent_name": agent_version_info["agentName"],
                "bedrock_agent_description": agent_version_info.get("description"),
                "bedrock_agent_version": agent_version_info["version"],
                "bedrock_aws_settings_id": setting.id,
            },
            "toolkits": [],
            "llm_model_type": agent_version_info.get("foundationModel", "Bedrock Agent"),
            "created_by": CreatedByUser(id=user.id, username=user.username, name=user.name),
            "project": setting.project_name,
            "shared": True,  # Assistants are "project based" and shared by default
            "type": AssistantType.BEDROCK_AGENT,
        }

    @staticmethod
    def _create_or_update_entity(
        alias_id: str,
        assistant_data: dict,
        existing_entities_map: dict,
        agent_id: str,
    ) -> str:
        """
        Creates or updates an assistant object for a Bedrock agent alias.

        This method checks if an assistant already exists for the given alias and either updates
        it or creates a new one.

        Args:
            alias_id (str): The ID of the agent alias.
            assistant_data (dict): The data for the assistant.
            existing_entities_map (dict): A map of existing assistants by alias ID.
            agent_name (str): The name of the Bedrock agent.
        """
        # Check if an assistant already exists for this alias
        if alias_id in existing_entities_map:
            # Update the existing assistant
            assistant = existing_entities_map[alias_id]
            for key, value in assistant_data.items():
                setattr(assistant, key, value)
            assistant.save(refresh=True)
            logger.info(f"Updated Assistant for Bedrock agent: {agent_id} (Alias: {alias_id})")
        else:
            # Ensure Application exists for the project
            project_name = assistant_data.get("project")
            if project_name:
                ensure_application_exists(project_name)

            # Create a new assistant
            assistant = Assistant(**assistant_data)
            assistant.save(refresh=True)
            existing_entities_map[alias_id] = assistant  # Add to the map
            logger.info(f"Created Assistant for Bedrock agent: {agent_id} (Alias: {alias_id})")

        return str(assistant.id)

    @staticmethod
    def _bedrock_list_all_agents(
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
        page: int = 0,
        per_page: int = 10,
        next_token: Optional[str] = None,
        max_retry_attempts: Optional[int] = None,
    ) -> tuple[List[dict], Optional[str]]:
        return call_bedrock_listing_api(
            service_name="bedrock-agent",
            api_method_name="list_agents",
            response_key="agentSummaries",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
            page=page,
            per_page=per_page,
            next_token=next_token,
            max_retry_attempts=max_retry_attempts,
        )

    @staticmethod
    def _bedrock_list_agent_aliases(
        agent_id: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
        page: int = 0,
        per_page: int = 10,
        next_token: Optional[str] = None,
    ) -> tuple[List[dict], Optional[str]]:
        return call_bedrock_listing_api(
            service_name="bedrock-agent",
            api_method_name="list_agent_aliases",
            response_key="agentAliasSummaries",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
            page=page,
            per_page=per_page,
            next_token=next_token,
            agentId=agent_id,  # API-specific parameter
        )

    @staticmethod
    def _bedrock_get_agent(
        agent_id: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
    ) -> dict:
        def _func(client):
            response = client.get_agent(agentId=agent_id)

            return response.get("agent", {})

        client = get_aws_client_for_service(
            "bedrock-agent",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

        return handle_aws_call(_func, client)

    @staticmethod
    def _bedrock_get_agent_version(
        agent_id: str,
        agent_version: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
    ) -> dict:
        def _func(client):
            response = client.get_agent_version(agentId=agent_id, agentVersion=agent_version)

            return response.get("agentVersion", {})

        client = get_aws_client_for_service(
            "bedrock-agent",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

        return handle_aws_call(_func, client)

    @staticmethod
    def _bedrock_get_agent_alias(
        agent_id: str,
        agent_alias_id: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
    ) -> dict:
        def _func(client):
            response = client.get_agent_alias(agentId=agent_id, agentAliasId=agent_alias_id)
            return response.get("agentAlias", {})

        client = get_aws_client_for_service(
            "bedrock-agent",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

        return handle_aws_call(_func, client)

    @staticmethod
    def _bedrock_invoke_agent(
        agent_id: str,
        agent_alias: str,
        input_text: str,
        session_id: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
        session_state: Optional[dict] = None,
    ):
        def extract_text_from_chunk(chunk):
            # Handle bytes directly in the chunk
            if "bytes" in chunk and isinstance(chunk["bytes"], bytes):
                return chunk["bytes"].decode("utf-8")
            # Handle dictionary structure
            elif isinstance(chunk, dict):
                return "".join(
                    content.get("text", "")
                    for content in chunk.get("message", {}).get("content", [])
                    if content.get("type") == "text"
                )
            return ""

        def _func(client):
            response = client.invoke_agent(
                agentId=agent_id,
                agentAliasId=agent_alias,
                sessionId=session_id,
                inputText=input_text,
                sessionState=session_state or {},
            )

            completion = ""
            for event in response.get("completion", []):
                if "chunk" in event:
                    completion += extract_text_from_chunk(event["chunk"])
            return completion

        client = get_aws_client_for_service(
            "bedrock-agent-runtime",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

        return handle_aws_call(_func, client)
