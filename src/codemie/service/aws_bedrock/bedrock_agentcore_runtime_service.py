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

from concurrent.futures import ThreadPoolExecutor, as_completed
from time import time
from typing import List, Optional, TypedDict
import uuid
import json
from botocore.exceptions import ClientError

from codemie.configs import logger
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.assistant import Assistant, AssistantType
from codemie.rest_api.models.guardrail import GuardrailEntity
from codemie.rest_api.models.settings import AWSCredentials, SettingsBase
from codemie.rest_api.models.vendor import ImportAgentcoreRuntime
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


class InvokeAgentCoreRuntimeResponse(TypedDict):
    output: str
    time_elapsed: float


class BedrockAgentCoreRuntimeService(BaseBedrockService):
    QUERY_PLACEHOLDER = "__QUERY_PLACEHOLDER__"

    @staticmethod
    @aws_service_exception_handler("Bedrock AgentCore runtimes")
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
            # Submit all tasks
            future_to_setting = {
                executor.submit(BedrockAgentCoreRuntimeService._fetch_main_entity_names_for_setting, setting): setting
                for setting in paged_settings
            }

            # Collect results as they complete
            for future in as_completed(future_to_setting):
                setting = future_to_setting[future]
                try:
                    runtime_names = future.result()

                    results["data"].append(
                        {
                            "setting_id": str(setting.id),
                            "setting_name": setting.alias,
                            "project": setting.project_name,
                            "entities": runtime_names if runtime_names is not None else [],
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
    @aws_service_exception_handler("Bedrock AgentCore runtimes")
    def list_main_entities(
        user: User,
        setting_id: str,
        page: int,
        per_page: int,
        next_token: Optional[str] = None,
    ) -> tuple[List[dict], Optional[str]]:
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        aws_creds = get_setting_aws_credentials(setting.id)

        all_runtimes, return_next_token = BedrockAgentCoreRuntimeService._bedrock_list_agent_runtimes(
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            page=page,
            per_page=per_page,
            next_token=next_token,
        )

        runtime_data = []
        for runtime_info in all_runtimes:
            runtime_status = "PREPARED" if runtime_info.get("status") == "READY" else "NOT_PREPARED"

            runtime_data.append(
                {
                    "id": runtime_info.get("agentRuntimeId"),
                    "name": runtime_info.get("agentRuntimeName"),
                    "status": runtime_status,
                    "description": runtime_info.get("description"),
                    "version": runtime_info.get("agentRuntimeVersion"),
                    "updatedAt": runtime_info.get("lastUpdatedAt"),
                }
            )

        return runtime_data, return_next_token

    @staticmethod
    @aws_service_exception_handler("Bedrock AgentCore runtimes")
    def get_main_entity_detail(
        user: User,
        main_entity_id: str,
        setting_id: str,
    ) -> dict:
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        aws_creds = get_setting_aws_credentials(setting.id)

        runtime_info = BedrockAgentCoreRuntimeService._bedrock_get_agent_runtime(
            runtime_id=main_entity_id,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
        )

        runtime_status = "PREPARED" if runtime_info.get("status") == "READY" else "NOT_PREPARED"

        return {
            "id": runtime_info.get("agentRuntimeId"),
            "name": runtime_info.get("agentRuntimeName"),
            "status": runtime_status,
            "description": runtime_info.get("description"),
            "version": runtime_info.get("agentRuntimeVersion"),
            "updatedAt": runtime_info.get("lastUpdatedAt"),
        }

    @staticmethod
    @aws_service_exception_handler("Bedrock AgentCore runtimes")
    def list_importable_entities_for_main_entity(
        user: User,
        main_entity_id: str,
        setting_id: str,
        page: int,
        per_page: int,
        next_token: Optional[str] = None,
    ) -> tuple[List[dict], Optional[str]]:
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        existing_entities = Assistant.get_by_bedrock_runtime_aws_settings_id(str(setting.id))
        existing_entities_map = {
            assistant.bedrock_agentcore_runtime.runtime_endpoint_id: assistant
            for assistant in existing_entities
            if assistant.bedrock_agentcore_runtime
            and hasattr(assistant.bedrock_agentcore_runtime, "runtime_endpoint_id")
        }

        aws_creds = get_setting_aws_credentials(setting.id)

        runtime_endpoints = []

        endpoints_information, return_next_token = BedrockAgentCoreRuntimeService._bedrock_list_runtime_endpoints(
            runtime_id=main_entity_id,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            page=page,
            per_page=per_page,
            next_token=next_token,
        )

        for endpoint_info in endpoints_information:
            endpoint_status = "PREPARED" if endpoint_info.get("status") == "READY" else "NOT_PREPARED"
            endpoint_id = endpoint_info.get("id")

            endpoint_dict = {
                "id": endpoint_id,
                "name": endpoint_info.get("name"),
                "status": endpoint_status,
                "description": endpoint_info.get("description"),
                "liveVersion": endpoint_info.get("liveVersion"),
                "targetVersion": endpoint_info.get("targetVersion"),
                "createdAt": endpoint_info.get("createdAt"),
                "updatedAt": endpoint_info.get("lastUpdatedAt"),
            }

            if endpoint_id in existing_entities_map:
                endpoint_dict["aiRunId"] = existing_entities_map[endpoint_id].id

            runtime_endpoints.append(endpoint_dict)

        return runtime_endpoints, return_next_token

    @staticmethod
    @aws_service_exception_handler("Bedrock AgentCore runtimes")
    def get_importable_entity_detail(
        user: User,
        main_entity_id: str,
        importable_entity_detail: str,
        setting_id: str,
    ):
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        aws_creds = get_setting_aws_credentials(setting.id)

        endpoint_info = BedrockAgentCoreRuntimeService._bedrock_get_runtime_endpoint(
            runtime_id=main_entity_id,
            endpoint_name=importable_entity_detail,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
        )

        if not endpoint_info:
            logger.warning(
                f"Failed to retrieve endpoint information for runtime {main_entity_id}, "
                f"endpoint: {importable_entity_detail}"
            )
            return {}

        status = "PREPARED" if endpoint_info.get("status") == "READY" else "NOT_PREPARED"

        return {
            "id": endpoint_info.get("id"),
            "name": endpoint_info.get("name"),
            "status": status,
            "description": endpoint_info.get("description"),
            "liveVersion": endpoint_info.get("liveVersion"),
            "targetVersion": endpoint_info.get("targetVersion"),
            "agentRuntimeEndpointArn": endpoint_info.get("agentRuntimeEndpointArn"),
            "agentRuntimeArn": endpoint_info.get("agentRuntimeArn"),
            "failureReason": endpoint_info.get("failureReason"),
            "createdAt": endpoint_info.get("createdAt"),
            "updatedAt": endpoint_info.get("lastUpdatedAt"),
        }

    @staticmethod
    @aws_service_exception_handler("Bedrock AgentCore runtimes")
    def import_entities(user: User, import_payload: dict[str, List[ImportAgentcoreRuntime]]):
        results = []

        for setting_id, endpoint_imports in import_payload.items():
            setting: SettingsBase = get_setting_for_user(user, setting_id)

            # Retrieve all existing assistants for this settings
            existing_entities = Assistant.get_by_bedrock_runtime_aws_settings_id(str(setting.id))
            existing_entities_map = {
                assistant.bedrock_agentcore_runtime.runtime_endpoint_id: assistant
                for assistant in existing_entities
                if assistant.bedrock_agentcore_runtime
                and hasattr(assistant.bedrock_agentcore_runtime, "runtime_endpoint_id")
            }

            aws_creds = get_setting_aws_credentials(setting.id)

            for endpoint_import in endpoint_imports:
                results.append(
                    BedrockAgentCoreRuntimeService._process_endpoint_import(
                        user=user,
                        setting=setting,
                        aws_creds=aws_creds,
                        existing_entities_map=existing_entities_map,
                        input_runtime_id=endpoint_import.id,
                        input_endpoint_name=endpoint_import.agentcoreRuntimeEndpointName,
                        invocation_json=endpoint_import.invocation_json,
                    )
                )

        return results

    @staticmethod
    def delete_entities(setting_id: str):
        existing_entities = Assistant.get_by_bedrock_runtime_aws_settings_id(setting_id)
        for assistant in existing_entities:
            if assistant.type == AssistantType.BEDROCK_AGENTCORE_RUNTIME:
                assistant.delete()
                GuardrailService.remove_guardrail_assignments_for_entity(GuardrailEntity.ASSISTANT, str(assistant.id))

    @staticmethod
    def validate_remote_entity_exists_and_cleanup(entity: Assistant):
        if (
            entity.type != AssistantType.BEDROCK_AGENTCORE_RUNTIME
            or not entity.bedrock_agentcore_runtime
            or not entity.bedrock_agentcore_runtime.runtime_id
            or not entity.bedrock_agentcore_runtime.runtime_endpoint_id
            or not entity.bedrock_agentcore_runtime.aws_settings_id
        ):
            return None  # not a bedrock agentcore entity

        try:
            aws_creds = get_setting_aws_credentials(entity.bedrock_agentcore_runtime.aws_settings_id)

            BedrockAgentCoreRuntimeService._bedrock_get_runtime_endpoint(
                runtime_id=entity.bedrock_agentcore_runtime.runtime_id,
                endpoint_name=entity.bedrock_agentcore_runtime.runtime_endpoint_name,
                region=aws_creds.region,
                access_key_id=aws_creds.access_key_id,
                secret_access_key=aws_creds.secret_access_key,
            )

            return None

        except ClientError as e:
            error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            if error_code.strip().lower() == "resourcenotfoundexception":
                entity.delete()
                GuardrailService.remove_guardrail_assignments_for_entity(GuardrailEntity.ASSISTANT, str(entity.id))

                return entity.name
            else:
                logger.error(f"Unexpected ClientError validating AgentCore runtime for assistant {entity.name}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error validating AgentCore runtime for assistant {entity.name}: {e}")

    @staticmethod
    def validate_remote_entity_exists_and_cleanup_with_subassistants(entity: Assistant):
        deleted_assistants_names = []
        deleted_subassistant_ids = []

        # Process subassistants
        for subassistant_id in entity.assistant_ids:
            subassistant = Assistant.find_by_id(subassistant_id)
            if subassistant:
                deleted_name = BedrockAgentCoreRuntimeService.validate_remote_entity_exists_and_cleanup(subassistant)
                if deleted_name:
                    deleted_assistants_names.append(deleted_name)
                    deleted_subassistant_ids.append(subassistant_id)
            else:
                # Subassistant no longer exists in database - remove from parent
                deleted_subassistant_ids.append(subassistant_id)

        # Check main entity
        deleted_name = BedrockAgentCoreRuntimeService.validate_remote_entity_exists_and_cleanup(entity)
        if deleted_name:
            deleted_assistants_names.append(deleted_name)
            # Entity deleted - no need to update assistant_ids
        elif deleted_assistants_names:  # Only update if entity survives AND subassistants were deleted
            entity.assistant_ids = [aid for aid in entity.assistant_ids if aid not in deleted_subassistant_ids]
            entity.save()

        return deleted_assistants_names

    @staticmethod
    def invoke_agentcore_runtime(
        assistant: Assistant,
        input_text: str,
        conversation_id: str,
    ) -> InvokeAgentCoreRuntimeResponse:
        """
        Invokes a Bedrock AgentCore runtime with the provided input.

        Args:
            assistant (Assistant): The assistant object containing AgentCore runtime details.
            input_text (str): The input text to send to the runtime.
            conversation_id (str): The unique identifier for the conversation session.
            chat_history (Optional[List]): The chat history (currently not used for AgentCore).

        Returns:
            InvokeAgentCoreRuntimeResponse: A dictionary containing the runtime's output and time elapsed.
        """
        start_time = time()

        if (
            not assistant.bedrock_agentcore_runtime
            or not assistant.bedrock_agentcore_runtime.runtime_endpoint_arn
            or not assistant.bedrock_agentcore_runtime.aws_settings_id
        ):
            raise ValueError("Trying to invoke non-AgentCore runtime assistant.")

        try:
            aws_creds = get_setting_aws_credentials(assistant.bedrock_agentcore_runtime.aws_settings_id)

            # Prepare payload using the invocation JSON template or default structure
            payload = BedrockAgentCoreRuntimeService._prepare_invocation_payload(
                invocation_json=assistant.bedrock_agentcore_runtime.invocation_json,
                query=input_text,
                conversation_id=conversation_id,
            )

            response = BedrockAgentCoreRuntimeService._bedrock_invoke_runtime(
                runtime_arn=assistant.bedrock_agentcore_runtime.runtime_arn,
                qualifier=assistant.bedrock_agentcore_runtime.runtime_endpoint_name,
                payload=payload,
                region=aws_creds.region,
                access_key_id=aws_creds.access_key_id,
                secret_access_key=aws_creds.secret_access_key,
            )

            return {
                "output": response,
                "time_elapsed": time() - start_time,
            }
        except ClientError as e:
            error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            if error_code.strip().lower() == "resourcenotfoundexception":
                logger.warning(f"AgentCore runtime not found on remote: {e}")

                BedrockAgentCoreRuntimeService.validate_remote_entity_exists_and_cleanup_with_subassistants(assistant)
            else:
                logger.error(f"AWS ClientError invoking AgentCore runtime: {e}")

            return {
                "output": str(e),
                "time_elapsed": time() - start_time,
            }
        except Exception as e:
            logger.error(f"Unexpected error invoking AgentCore runtime: {e}")
            return {
                "output": str(e),
                "time_elapsed": time() - start_time,
            }

    @staticmethod
    def _validate_invocation_json(invocation_json: Optional[str]) -> Optional[str]:
        """
        Validate the invocation JSON template.

        The template should be valid JSON with the placeholder as a string value.
        Example: {"prompt": "__QUERY_PLACEHOLDER__", "sessionId": "123"}

        Args:
            invocation_json: JSON string template with __QUERY_PLACEHOLDER__

        Returns:
            Error message if validation fails, None if valid
        """
        if not invocation_json:
            return None

        # Check if placeholder exists
        if BedrockAgentCoreRuntimeService.QUERY_PLACEHOLDER not in invocation_json:
            return f"Invocation JSON must contain '{BedrockAgentCoreRuntimeService.QUERY_PLACEHOLDER}' placeholder"

        try:
            # Parse the JSON to validate structure
            parsed = json.loads(invocation_json)

            # Verify the placeholder exists in the parsed structure
            if not BedrockAgentCoreRuntimeService._contains_placeholder(parsed):
                return f"Invocation JSON must contain '{BedrockAgentCoreRuntimeService.QUERY_PLACEHOLDER}' as a value"

            return None
        except json.JSONDecodeError as e:
            return f"Invalid JSON template: {str(e)}"

    @staticmethod
    def _contains_placeholder(obj) -> bool:
        """
        Recursively check if the placeholder exists in the JSON structure.

        Args:
            obj: The object to search (dict, list, or primitive)

        Returns:
            True if placeholder is found, False otherwise
        """
        if isinstance(obj, str):
            return obj == BedrockAgentCoreRuntimeService.QUERY_PLACEHOLDER
        elif isinstance(obj, dict):
            return any(BedrockAgentCoreRuntimeService._contains_placeholder(v) for v in obj.values())
        elif isinstance(obj, list):
            return any(BedrockAgentCoreRuntimeService._contains_placeholder(item) for item in obj)
        return False

    @staticmethod
    def _replace_placeholder_in_structure(obj, query: str):
        """
        Recursively replace the placeholder with the actual query value.

        Args:
            obj: The object to process (dict, list, or primitive)
            query: The query string to insert

        Returns:
            The object with placeholders replaced
        """
        if isinstance(obj, str):
            return query if obj == BedrockAgentCoreRuntimeService.QUERY_PLACEHOLDER else obj
        elif isinstance(obj, dict):
            return {
                k: BedrockAgentCoreRuntimeService._replace_placeholder_in_structure(v, query) for k, v in obj.items()
            }
        elif isinstance(obj, list):
            return [BedrockAgentCoreRuntimeService._replace_placeholder_in_structure(item, query) for item in obj]
        return obj

    @staticmethod
    def _prepare_invocation_payload(invocation_json: Optional[str], query: str, conversation_id: str) -> bytes:
        """
        Prepare the invocation payload by replacing the placeholder with the actual query.

        Args:
            invocation_json: JSON template with __QUERY_PLACEHOLDER__
            query: The actual query to insert
            conversation_id: The conversation/session ID

        Returns:
            Encoded payload bytes
        """
        if invocation_json:
            try:
                # Parse the template JSON
                payload_dict = json.loads(invocation_json)

                # Replace placeholder with actual query value
                payload_dict = BedrockAgentCoreRuntimeService._replace_placeholder_in_structure(payload_dict, query)

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse invocation JSON template: {e}")
                # Fallback to default structure
                payload_dict = {
                    "message": query,
                    "sessionId": conversation_id,
                }
        else:
            # Default payload structure
            payload_dict = {
                "message": query,
                "sessionId": conversation_id,
            }

        return json.dumps(payload_dict).encode("utf-8")

    @staticmethod
    def _fetch_main_entity_names_for_setting(setting) -> List[str] | None:
        """
        Fetch runtime names for a single setting.
        """
        aws_creds = get_setting_aws_credentials(setting.id)

        all_runtimes, _ = BedrockAgentCoreRuntimeService._bedrock_list_agent_runtimes(
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            page=0,
            per_page=ALL_SETTINGS_OVERVIEW_ENTITY_COUNT,
            max_retry_attempts=1,  # only 1 attempt to avoid incorrect setting config timeouts
        )

        runtime_names = []
        for runtime_count, runtime_info in enumerate(all_runtimes):
            if runtime_count >= ALL_SETTINGS_OVERVIEW_ENTITY_COUNT:
                break
            runtime_names.append(runtime_info.get("agentRuntimeName", "Unknown Runtime"))

        return runtime_names

    @staticmethod
    def _process_endpoint_import(
        user: User,
        setting: SettingsBase,
        aws_creds: AWSCredentials,
        existing_entities_map: dict,
        input_runtime_id: str,
        input_endpoint_name: str,
        invocation_json: str,
    ) -> dict:
        try:
            validation_error = BedrockAgentCoreRuntimeService._validate_invocation_json(invocation_json)
            if validation_error:
                return {
                    "runtimeId": input_runtime_id,
                    "endpointName": input_endpoint_name,
                    "error": {
                        "statusCode": "400",
                        "message": validation_error,
                    },
                }

            endpoint_info = BedrockAgentCoreRuntimeService._bedrock_get_runtime_endpoint(
                runtime_id=input_runtime_id,
                endpoint_name=input_endpoint_name,
                region=aws_creds.region,
                access_key_id=aws_creds.access_key_id,
                secret_access_key=aws_creds.secret_access_key,
            )

            endpoint_id = endpoint_info.get("id")
            runtime_endpoint_arn = endpoint_info.get("agentRuntimeEndpointArn")

            if not endpoint_id or not runtime_endpoint_arn:
                return {
                    "runtimeId": input_runtime_id,
                    "endpointName": input_endpoint_name,
                    "error": {
                        "statusCode": "404",
                        "message": f"Runtime endpoint with name {input_endpoint_name} was not found",
                    },
                }

            if endpoint_info.get("status") != "READY":
                return {
                    "runtimeId": input_runtime_id,
                    "endpointName": input_endpoint_name,
                    "error": {
                        "statusCode": "409",
                        "message": (
                            f"Endpoint {input_endpoint_name} for runtime {input_runtime_id} is not in READY status"
                        ),
                    },
                }

            assistant_data = BedrockAgentCoreRuntimeService._create_assistant_data(
                user=user,
                setting=setting,
                input_runtime_id=input_runtime_id,
                endpoint_info=endpoint_info,
                invocation_json=invocation_json,
            )

            created_entity_id = BedrockAgentCoreRuntimeService._create_or_update_entity(
                endpoint_id=endpoint_id,
                assistant_data=assistant_data,
                existing_entities_map=existing_entities_map,
                runtime_id=input_runtime_id,
            )

            return {
                "runtimeId": input_runtime_id,
                "endpointName": input_endpoint_name,
                "aiRunId": created_entity_id,
            }
        except ClientError as e:
            error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            if error_code.strip().lower() == "resourcenotfoundexception":
                return {
                    "runtimeId": input_runtime_id,
                    "endpointName": input_endpoint_name,
                    "error": {
                        "statusCode": "404",
                        "message": (
                            f"Runtime {input_runtime_id} with endpoint {input_endpoint_name} "
                            f"was not found (AWS ResourceNotFoundException)"
                        ),
                    },
                }
            return {
                "runtimeId": input_runtime_id,
                "endpointName": input_endpoint_name,
                "error": {"statusCode": "500", "message": str(e)},
            }
        except Exception as e:
            return {
                "runtimeId": input_runtime_id,
                "endpointName": input_endpoint_name,
                "error": {"statusCode": "500", "message": str(e)},
            }

    @staticmethod
    def _create_assistant_data(
        user: User,
        setting: SettingsBase,
        input_runtime_id: str,
        endpoint_info: dict,
        invocation_json: str,
    ) -> dict:
        """
        Creates the data dictionary for a Bedrock AgentCore assistant.
        """
        endpoint_name = endpoint_info.get("name", "Unknown Endpoint")

        name = f"{input_runtime_id}:{endpoint_name}"

        # First 8 characters of UUID for unique slug
        random_suffix = str(uuid.uuid4())[:8]
        unique_slug = f"{name}-{random_suffix}"

        return {
            "name": name,
            "description": endpoint_info.get("description", "AWS Bedrock Agentcore Runtime Endpoint"),
            "system_prompt": f"AgentCore Runtime: {input_runtime_id}, Endpoint: {endpoint_name}",
            "slug": unique_slug,
            "bedrock_agentcore_runtime": {
                "runtime_id": input_runtime_id,
                "runtime_arn": endpoint_info.get("agentRuntimeArn"),
                "runtime_endpoint_id": endpoint_info.get("id"),
                "runtime_endpoint_arn": endpoint_info.get("agentRuntimeEndpointArn"),
                "runtime_endpoint_name": endpoint_name,
                "runtime_endpoint_live_version": endpoint_info.get("liveVersion"),
                "runtime_endpoint_description": endpoint_info.get(
                    "description", "AWS Bedrock Agentcore Runtime Endpoint"
                ),
                "aws_settings_id": setting.id,
                "invocation_json": invocation_json,
            },
            "toolkits": [],
            "llm_model_type": f"AgentCore Runtime {input_runtime_id}",
            "created_by": CreatedByUser(id=user.id, username=user.username, name=user.name),
            "project": setting.project_name,
            "shared": True,  # Assistants are "project based" and shared by default
            "type": AssistantType.BEDROCK_AGENTCORE_RUNTIME,
        }

    @staticmethod
    def _create_or_update_entity(
        endpoint_id: str,
        assistant_data: dict,
        existing_entities_map: dict,
        runtime_id: str,
    ) -> str:
        """
        Creates or updates an assistant object for an AgentCore runtime endpoint.
        """
        if endpoint_id in existing_entities_map:
            # Update the existing assistant
            assistant = existing_entities_map[endpoint_id]
            for key, value in assistant_data.items():
                setattr(assistant, key, value)
            assistant.save(refresh=True)
            logger.info(f"Updated Assistant for AgentCore runtime: {runtime_id} (Endpoint: {endpoint_id})")
        else:
            # Ensure Application exists for the project
            project_name = assistant_data.get("project")
            if project_name:
                ensure_application_exists(project_name)

            # Create a new assistant
            assistant = Assistant(**assistant_data)
            assistant.save(refresh=True)
            existing_entities_map[endpoint_id] = assistant
            logger.info(f"Created Assistant for AgentCore runtime: {runtime_id} (Endpoint: {endpoint_id})")

        return str(assistant.id)

    @staticmethod
    def _bedrock_list_agent_runtimes(
        region: str,
        access_key_id: str,
        secret_access_key: str,
        page: int = 0,
        per_page: int = 10,
        next_token: Optional[str] = None,
        max_retry_attempts: Optional[int] = None,
    ) -> tuple[List[dict], Optional[str]]:
        return call_bedrock_listing_api(
            service_name="bedrock-agentcore-control",
            api_method_name="list_agent_runtimes",
            response_key="agentRuntimes",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            page=page,
            per_page=per_page,
            next_token=next_token,
            max_retry_attempts=max_retry_attempts,
        )

    @staticmethod
    def _bedrock_list_runtime_endpoints(
        runtime_id: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        page: int = 0,
        per_page: int = 10,
        next_token: Optional[str] = None,
    ) -> tuple[List[dict], Optional[str]]:
        return call_bedrock_listing_api(
            service_name="bedrock-agentcore-control",
            api_method_name="list_agent_runtime_endpoints",
            response_key="runtimeEndpoints",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            page=page,
            per_page=per_page,
            next_token=next_token,
            agentRuntimeId=runtime_id,
        )

    @staticmethod
    def _bedrock_get_agent_runtime(
        runtime_id: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
    ) -> dict:
        def _func(client):
            response = client.get_agent_runtime(agentRuntimeId=runtime_id)
            # The response doesn't have a nested key, return the whole response
            return response

        client = get_aws_client_for_service(
            "bedrock-agentcore-control", region=region, access_key_id=access_key_id, secret_access_key=secret_access_key
        )

        return handle_aws_call(_func, client)

    @staticmethod
    def _bedrock_get_runtime_endpoint(
        runtime_id: str,
        endpoint_name: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
    ) -> dict:
        def _func(client):
            response = client.get_agent_runtime_endpoint(agentRuntimeId=runtime_id, endpointName=endpoint_name)
            # The response doesn't have a nested key, return the whole response
            return response

        client = get_aws_client_for_service(
            "bedrock-agentcore-control", region=region, access_key_id=access_key_id, secret_access_key=secret_access_key
        )

        return handle_aws_call(_func, client)

    @staticmethod
    def _bedrock_invoke_runtime(
        runtime_arn: str,
        qualifier: str,
        payload: bytes,
        region: str,
        access_key_id: str,
        secret_access_key: str,
    ) -> str:
        def _func(client):
            response = client.invoke_agent_runtime(
                agentRuntimeArn=runtime_arn,
                qualifier=qualifier,
                payload=payload,
                contentType="application/json",
                accept="text/event-stream",
            )

            content_type = response.get("contentType", "")
            return BedrockAgentCoreRuntimeService._parse_response_by_content_type(response, content_type)

        client = get_aws_client_for_service(
            "bedrock-agentcore", region=region, access_key_id=access_key_id, secret_access_key=secret_access_key
        )

        return handle_aws_call(_func, client)

    @staticmethod
    def _parse_response_by_content_type(response: dict, content_type: str) -> str:
        """Parse AgentCore runtime response based on content type."""
        if "text/event-stream" in content_type:
            return BedrockAgentCoreRuntimeService._parse_streaming_response(response)
        elif content_type == "application/json":
            return BedrockAgentCoreRuntimeService._parse_json_response(response)
        else:
            return str(response)

    @staticmethod
    def _parse_streaming_response(response: dict) -> str:
        """Parse streaming event response from AgentCore runtime."""
        content = []
        for line in response["response"].iter_lines(chunk_size=10):
            if line:
                line_str = line.decode("utf-8")
                if line_str.startswith("data: "):
                    line_str = line_str[6:]
                    content.append(line_str)

        return "\n".join(content)

    @staticmethod
    def _parse_json_response(response: dict) -> str:
        """Parse JSON response from AgentCore runtime."""
        response_body = response.get("response")
        if not response_body:
            return "No response body received from AgentCore runtime"

        try:
            body_bytes = response_body.read()
            body_str = body_bytes.decode("utf-8")
            response_json = json.loads(body_str)

            return response_json.get("response", body_str)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AgentCore runtime response as JSON: {e}")

            return body_str
        except Exception as e:
            logger.error(f"Error reading AgentCore runtime response: {e}")

            return "Unexpected error when reading AgentCore runtime response"
