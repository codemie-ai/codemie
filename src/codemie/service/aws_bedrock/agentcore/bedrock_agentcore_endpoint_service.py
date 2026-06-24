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

import json
import uuid
from enum import StrEnum
from typing import Any, List, Optional

from botocore.exceptions import ClientError
from pydantic import BaseModel, ValidationError

from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.assistant import Assistant, AssistantType
from codemie.rest_api.models.guardrail import GuardrailEntity
from codemie.rest_api.models.settings import AWSCredentials, SettingsBase
from codemie.rest_api.models.vendor import ImportAgentcoreRuntime
from codemie.rest_api.security.user import User
from codemie.rest_api.utils.default_applications import ensure_application_exists
from codemie.service.aws_bedrock.agentcore.agentcore_config import AgentcoreResponseConfig
from codemie.service.aws_bedrock.exceptions import (
    AgentcoreEndpointNotFoundError,
    AgentcoreEndpointValidationError,
    EntityAccessDenied,
    EntityDeletionError,
    EntityNotFound,
    aws_service_exception_handler,
    is_resource_not_found,
)
from codemie.service.aws_bedrock.utils import (
    call_bedrock_listing_api,
    get_aws_client_for_service,
    get_setting_aws_credentials,
    get_setting_for_user,
    handle_aws_call,
)
from codemie.service.guardrail.guardrail_service import GuardrailService


EXCEPTION_IDENTIFIER = "Bedrock AgentCore runtimes"
_AWS_ENDPOINT_READY_STATUS = "READY"


class EndpointStatus(StrEnum):
    PREPARED = "PREPARED"
    NOT_PREPARED = "NOT_PREPARED"
    VERSION_DRIFT = "VERSION_DRIFT"
    DELETED_ON_AWS = "DELETED_ON_AWS"


class AgentcoreEndpointEntity(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    status: EndpointStatus = EndpointStatus.NOT_PREPARED
    description: Optional[str] = None
    liveVersion: Optional[str] = None
    targetVersion: Optional[str] = None
    createdAt: Optional[Any] = None
    updatedAt: Optional[Any] = None
    aiRunId: Optional[str] = None
    configurationJson: Optional[str] = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __contains__(self, key: str) -> bool:
        return getattr(self, key, None) is not None


class AgentcoreEndpointDetailEntity(AgentcoreEndpointEntity):
    agentRuntimeEndpointArn: Optional[str] = None
    agentRuntimeArn: Optional[str] = None
    failureReason: Optional[str] = None


class BedrockAgentCoreEndpointService:
    @staticmethod
    @aws_service_exception_handler(EXCEPTION_IDENTIFIER)
    def list_importable_entities_for_main_entity(
        user: User,
        main_entity_id: str,
        setting_id: str,
        page: int,
        per_page: int,
        next_token: Optional[str] = None,
    ) -> tuple[List[AgentcoreEndpointEntity], Optional[str]]:
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        existing_entities = Assistant.get_by_bedrock_runtime_aws_settings_id(str(setting.id))
        existing_entities_map = {
            assistant.bedrock_agentcore_runtime.runtime_endpoint_id: assistant
            for assistant in existing_entities
            if assistant.bedrock_agentcore_runtime
            and assistant.bedrock_agentcore_runtime.runtime_id == main_entity_id
            and hasattr(assistant.bedrock_agentcore_runtime, "runtime_endpoint_id")
        }

        aws_creds = get_setting_aws_credentials(setting.id)

        runtime_endpoints = []

        endpoints_information, return_next_token = BedrockAgentCoreEndpointService._bedrock_list_runtime_endpoints(
            runtime_id=main_entity_id,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
            page=page,
            per_page=per_page,
            next_token=next_token,
        )

        seen_endpoint_ids: set[str] = set()
        for endpoint_info in endpoints_information:
            endpoint_id = endpoint_info.get("id")
            assistant = existing_entities_map.get(endpoint_id)
            runtime_endpoints.append(BedrockAgentCoreEndpointService._build_endpoint_entity(endpoint_info, assistant))
            if endpoint_id:
                seen_endpoint_ids.add(endpoint_id)

        runtime_endpoints.extend(
            BedrockAgentCoreEndpointService._get_deleted_endpoint_entities(
                runtime_id=main_entity_id,
                existing_entities_map=existing_entities_map,
                seen_endpoint_ids=seen_endpoint_ids,
                aws_creds=aws_creds,
            )
        )

        return runtime_endpoints, return_next_token

    @staticmethod
    @aws_service_exception_handler(EXCEPTION_IDENTIFIER)
    def get_importable_entity_detail(
        user: User,
        main_entity_id: str,
        importable_entity_detail: str,
        setting_id: str,
    ) -> AgentcoreEndpointDetailEntity:
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        aws_creds = get_setting_aws_credentials(setting.id)

        endpoint_info = BedrockAgentCoreEndpointService._bedrock_get_runtime_endpoint(
            runtime_id=main_entity_id,
            endpoint_name=importable_entity_detail,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
        )

        if not endpoint_info:
            logger.warning(
                f"Failed to retrieve endpoint information for runtime {main_entity_id}, "
                f"endpoint: {importable_entity_detail}"
            )
            raise EntityNotFound("agentcore-endpoint", importable_entity_detail)

        assistant = None
        endpoint_id = endpoint_info.get("id")
        if endpoint_id:
            existing_entities = Assistant.get_by_bedrock_runtime_aws_settings_id(str(setting.id))
            for a in existing_entities:
                if a.bedrock_agentcore_runtime and a.bedrock_agentcore_runtime.runtime_endpoint_id == endpoint_id:
                    assistant = a
                    break

        base = BedrockAgentCoreEndpointService._build_endpoint_entity(endpoint_info, assistant)
        return AgentcoreEndpointDetailEntity(
            **base.model_dump(),
            agentRuntimeEndpointArn=endpoint_info.get("agentRuntimeEndpointArn"),
            agentRuntimeArn=endpoint_info.get("agentRuntimeArn"),
            failureReason=endpoint_info.get("failureReason"),
        )

    @staticmethod
    @aws_service_exception_handler(EXCEPTION_IDENTIFIER)
    def import_entities(user: User, import_payload: dict[str, List[ImportAgentcoreRuntime]]):
        results = []

        for setting_id, endpoint_imports in import_payload.items():
            setting: SettingsBase = get_setting_for_user(user, setting_id)

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
                    BedrockAgentCoreEndpointService._process_endpoint_import(
                        user=user,
                        setting=setting,
                        aws_creds=aws_creds,
                        existing_entities_map=existing_entities_map,
                        input_runtime_id=endpoint_import.id,
                        input_endpoint_name=endpoint_import.agentcoreRuntimeEndpointName,
                        configuration_json=endpoint_import.configuration_json,
                        assistant_name=endpoint_import.assistant_name,
                        assistant_description=endpoint_import.assistant_description,
                    )
                )

        return results

    @staticmethod
    def unimport_entity(entity_id: str, user: User) -> None:
        entity_model = Assistant.find_by_id(entity_id)
        if not entity_model:
            raise EntityNotFound("agentcore-runtime", entity_id)
        if not Ability(user).can(Action.DELETE, entity_model):
            raise EntityAccessDenied
        try:
            entity_model.delete()
            GuardrailService.remove_guardrail_assignments_for_entity(GuardrailEntity.ASSISTANT, str(entity_model.id))
        except Exception as e:
            raise EntityDeletionError("agentcore-runtime", str(e))

    @staticmethod
    def _get_deleted_endpoint_entities(
        runtime_id: str,
        existing_entities_map: dict,
        seen_endpoint_ids: set[str],
        aws_creds,
    ) -> List[AgentcoreEndpointEntity]:
        candidate_ids = set(existing_entities_map.keys()) - seen_endpoint_ids

        deleted: List[AgentcoreEndpointEntity] = []
        for endpoint_id in candidate_ids:
            assistant = existing_entities_map[endpoint_id]
            endpoint_name = assistant.bedrock_agentcore_runtime.runtime_endpoint_name
            try:
                BedrockAgentCoreEndpointService._bedrock_get_runtime_endpoint(
                    runtime_id=runtime_id,
                    endpoint_name=endpoint_name,
                    region=aws_creds.region,
                    access_key_id=aws_creds.access_key_id,
                    secret_access_key=aws_creds.secret_access_key,
                    session_token=aws_creds.session_token,
                )
            except ClientError as e:
                if is_resource_not_found(e):
                    deleted.append(BedrockAgentCoreEndpointService._build_endpoint_entity(None, assistant))
                else:
                    logger.error(f"Unexpected ClientError checking AgentCore endpoint {endpoint_name}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error checking AgentCore endpoint {endpoint_name}: {e}")

        return deleted

    @staticmethod
    def _build_endpoint_entity(
        endpoint_info: Optional[dict] = None,
        assistant: Optional[Assistant] = None,
    ) -> AgentcoreEndpointEntity:
        if endpoint_info is None:
            rt = assistant.bedrock_agentcore_runtime
            return AgentcoreEndpointEntity(
                id=rt.runtime_endpoint_id,
                name=rt.runtime_endpoint_name,
                status=EndpointStatus.DELETED_ON_AWS,
                description=rt.runtime_endpoint_description,
                liveVersion=rt.runtime_endpoint_live_version,
                aiRunId=str(assistant.id),
                configurationJson=rt.configuration_json,
            )

        if endpoint_info.get("status") != _AWS_ENDPOINT_READY_STATUS:
            status = EndpointStatus.NOT_PREPARED
        elif (
            assistant
            and assistant.bedrock_agentcore_runtime
            and assistant.bedrock_agentcore_runtime.runtime_endpoint_live_version != endpoint_info.get("liveVersion")
        ):
            status = EndpointStatus.VERSION_DRIFT
        else:
            status = EndpointStatus.PREPARED

        ai_run_id = str(assistant.id) if assistant else None
        configuration_json = (
            assistant.bedrock_agentcore_runtime.configuration_json
            if assistant and assistant.bedrock_agentcore_runtime
            else None
        )
        return AgentcoreEndpointEntity(
            id=endpoint_info.get("id"),
            name=endpoint_info.get("name"),
            status=status,
            description=endpoint_info.get("description"),
            liveVersion=endpoint_info.get("liveVersion"),
            targetVersion=endpoint_info.get("targetVersion"),
            createdAt=endpoint_info.get("createdAt"),
            updatedAt=endpoint_info.get("lastUpdatedAt"),
            aiRunId=ai_run_id,
            configurationJson=configuration_json,
        )

    @staticmethod
    def _validate_configuration_json(configuration_json: Optional[str]) -> Optional[str]:
        if not configuration_json:
            return None

        try:
            data = json.loads(configuration_json)
        except json.JSONDecodeError as e:
            return f"Invalid JSON template: {str(e)}"

        if "response" not in data:
            return "Invalid configuration: missing required 'response' key"

        try:
            AgentcoreResponseConfig.model_validate(data["response"])
            return None
        except ValidationError as e:
            error = e.errors()[0]
            loc = ".".join(str(p) for p in error["loc"])
            msg = f"{loc} {error['msg'].lower()}" if loc else error["msg"]
            return f"Invalid response configuration: {msg}"

    @staticmethod
    def _process_endpoint_import(
        user: User,
        setting: SettingsBase,
        aws_creds: AWSCredentials,
        existing_entities_map: dict,
        input_runtime_id: str,
        input_endpoint_name: str,
        configuration_json: str,
        assistant_name: Optional[str] = None,
        assistant_description: Optional[str] = None,
    ) -> dict:
        validation_error = BedrockAgentCoreEndpointService._validate_configuration_json(configuration_json)
        if validation_error:
            raise AgentcoreEndpointValidationError(validation_error)

        try:
            endpoint_info = BedrockAgentCoreEndpointService._bedrock_get_runtime_endpoint(
                runtime_id=input_runtime_id,
                endpoint_name=input_endpoint_name,
                region=aws_creds.region,
                access_key_id=aws_creds.access_key_id,
                secret_access_key=aws_creds.secret_access_key,
                session_token=aws_creds.session_token,
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if is_resource_not_found(e) or error_code == "AccessDeniedException":
                raise AgentcoreEndpointNotFoundError(
                    f"Runtime endpoint '{input_endpoint_name}' was not found",
                ) from e
            raise

        endpoint_id = endpoint_info.get("id")

        if endpoint_info.get("status") != _AWS_ENDPOINT_READY_STATUS:
            raise ExtendedHTTPException(
                code=409,
                message=f"Endpoint {input_endpoint_name} for runtime {input_runtime_id} "
                f"is not in {_AWS_ENDPOINT_READY_STATUS} status",
            )

        assistant_data = BedrockAgentCoreEndpointService._create_assistant_data(
            user=user,
            setting=setting,
            input_runtime_id=input_runtime_id,
            endpoint_info=endpoint_info,
            configuration_json=configuration_json,
            assistant_name=assistant_name,
            assistant_description=assistant_description,
        )

        created_entity_id = BedrockAgentCoreEndpointService._create_or_update_entity(
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

    @staticmethod
    def _create_assistant_data(
        user: User,
        setting: SettingsBase,
        input_runtime_id: str,
        endpoint_info: dict,
        configuration_json: str,
        assistant_name: Optional[str] = None,
        assistant_description: Optional[str] = None,
    ) -> dict:
        endpoint_name = endpoint_info.get("name", "Unknown Endpoint")
        name = assistant_name if assistant_name else f"{input_runtime_id}:{endpoint_name}"
        random_suffix = str(uuid.uuid4())[:8]
        unique_slug = f"{name}-{random_suffix}"

        return {
            "name": name,
            "description": assistant_description
            or endpoint_info.get("description", "AWS Bedrock Agentcore Runtime Endpoint"),
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
                "configuration_json": configuration_json,
            },
            "toolkits": [],
            "llm_model_type": f"AgentCore Runtime {input_runtime_id}",
            "created_by": CreatedByUser(id=user.id, username=user.username, name=user.name),
            "project": setting.project_name,
            "shared": True,
            "type": AssistantType.BEDROCK_AGENTCORE_RUNTIME,
        }

    @staticmethod
    def _create_or_update_entity(
        endpoint_id: str,
        assistant_data: dict,
        existing_entities_map: dict,
        runtime_id: str,
    ) -> str:
        if endpoint_id in existing_entities_map:
            assistant = existing_entities_map[endpoint_id]
            for key, value in assistant_data.items():
                setattr(assistant, key, value)
            assistant.save(refresh=True)
            logger.info(f"Updated Assistant for AgentCore runtime: {runtime_id} (Endpoint: {endpoint_id})")
        else:
            project_name = assistant_data.get("project")
            if project_name:
                ensure_application_exists(project_name)

            assistant = Assistant(**assistant_data)
            assistant.save(refresh=True)
            existing_entities_map[endpoint_id] = assistant
            logger.info(f"Created Assistant for AgentCore runtime: {runtime_id} (Endpoint: {endpoint_id})")

        return str(assistant.id)

    @staticmethod
    def _bedrock_list_runtime_endpoints(
        runtime_id: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
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
            session_token=session_token,
            page=page,
            per_page=per_page,
            next_token=next_token,
            agentRuntimeId=runtime_id,
        )

    @staticmethod
    def _bedrock_get_runtime_endpoint(
        runtime_id: str,
        endpoint_name: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
    ) -> dict:
        def _func(client):
            response = client.get_agent_runtime_endpoint(agentRuntimeId=runtime_id, endpointName=endpoint_name)
            return response

        client = get_aws_client_for_service(
            "bedrock-agentcore-control",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

        return handle_aws_call(_func, client)
