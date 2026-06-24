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
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import StrEnum
from time import time
from typing import Any, List, Optional, TypedDict

from botocore.exceptions import ClientError
from pydantic import BaseModel

from codemie.chains.base import StreamedGenerationResult
from codemie.configs import logger
from codemie.core.models import ChatMessage
from codemie.rest_api.models.assistant import Assistant, AssistantType
from codemie.rest_api.models.guardrail import GuardrailEntity
from codemie.rest_api.models.settings import SettingsBase
from codemie.rest_api.models.vendor import ImportAgentcoreRuntime
from codemie.rest_api.security.user import User
from codemie.service.aws_bedrock.agentcore.agentcore_config import AgentcoreRequestConfig, AgentcoreResponseConfig
from codemie.service.aws_bedrock.agentcore.agentcore_request_builder import AgentcoreRequestBuilder
from codemie.service.aws_bedrock.agentcore.agentcore_response_parser import AgentcoreResponseParser
from codemie.service.aws_bedrock.agentcore.bedrock_agentcore_endpoint_service import (
    EXCEPTION_IDENTIFIER,
    AgentcoreEndpointDetailEntity,
    AgentcoreEndpointEntity,
    BedrockAgentCoreEndpointService,
)
from codemie.service.aws_bedrock.base_bedrock_service import ALL_SETTINGS_OVERVIEW_ENTITY_COUNT, BaseBedrockService
from codemie.service.aws_bedrock.exceptions import aws_service_exception_handler, is_resource_not_found
from codemie.service.aws_bedrock.utils import (
    CONFIGURATION_INVALID_EXCEPTIONS,
    call_bedrock_listing_api,
    get_all_settings_for_user,
    get_aws_client_for_service,
    get_setting_aws_credentials,
    get_setting_for_user,
    handle_aws_call,
)
from codemie.service.guardrail.guardrail_service import GuardrailService


_AWS_RUNTIME_READY_STATUS = "READY"


class AgentcoreContentType(StrEnum):
    SSE = "text/event-stream"
    JSON = "application/json"


_agentcore_response_parser = AgentcoreResponseParser()


class RuntimeStatus(StrEnum):
    PREPARED = "PREPARED"
    NOT_PREPARED = "NOT_PREPARED"
    DELETED_ON_AWS = "DELETED_ON_AWS"


class AgentcoreRuntimeEntity(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    status: RuntimeStatus = RuntimeStatus.NOT_PREPARED
    description: Optional[str] = None
    version: Optional[str] = None
    updatedAt: Optional[Any] = None


class InvokeAgentCoreRuntimeResponse(TypedDict):
    output: str
    thoughts: list  # list of Thought.model_dump() dicts — new field
    time_elapsed: float


class BedrockAgentCoreRuntimeService(BaseBedrockService):
    # --- BaseBedrockService interface: runtime-level ---

    @staticmethod
    @aws_service_exception_handler(EXCEPTION_IDENTIFIER)
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

        with ThreadPoolExecutor(max_workers=min(len(paged_settings), 10)) as executor:
            future_to_setting = {
                executor.submit(BedrockAgentCoreRuntimeService._fetch_main_entity_names_for_setting, setting): setting
                for setting in paged_settings
            }

            for future in as_completed(future_to_setting):
                setting = future_to_setting[future]
                try:
                    runtime_names, has_deleted = future.result()

                    entry = {
                        "setting_id": str(setting.id),
                        "setting_name": setting.alias,
                        "project": setting.project_name,
                        "entities": runtime_names if runtime_names is not None else [],
                    }
                    if has_deleted:
                        entry["invalid"] = True
                    results["data"].append(entry)
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
    @aws_service_exception_handler(EXCEPTION_IDENTIFIER)
    def list_main_entities(
        user: User,
        setting_id: str,
        page: int,
        per_page: int,
        next_token: Optional[str] = None,
    ) -> tuple[List[AgentcoreRuntimeEntity], Optional[str]]:
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        aws_creds = get_setting_aws_credentials(setting.id)

        all_runtimes, return_next_token = BedrockAgentCoreRuntimeService._bedrock_list_agent_runtimes(
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
            page=page,
            per_page=per_page,
            next_token=next_token,
        )

        runtime_data: List[AgentcoreRuntimeEntity] = []
        seen_runtime_ids: set[str] = set()
        for runtime_info in all_runtimes:
            runtime_id = runtime_info.get("agentRuntimeId")
            runtime_status = (
                RuntimeStatus.PREPARED
                if runtime_info.get("status") == _AWS_RUNTIME_READY_STATUS
                else RuntimeStatus.NOT_PREPARED
            )
            runtime_data.append(
                AgentcoreRuntimeEntity(
                    id=runtime_id,
                    name=runtime_info.get("agentRuntimeName"),
                    status=runtime_status,
                    description=runtime_info.get("description"),
                    version=runtime_info.get("agentRuntimeVersion"),
                    updatedAt=runtime_info.get("lastUpdatedAt"),
                )
            )
            if runtime_id:
                seen_runtime_ids.add(runtime_id)

        runtime_data.extend(
            BedrockAgentCoreRuntimeService._get_deleted_runtime_entities(
                setting_id=str(setting.id),
                seen_runtime_ids=seen_runtime_ids,
                aws_creds=aws_creds,
            )
        )

        return runtime_data, return_next_token

    @staticmethod
    @aws_service_exception_handler(EXCEPTION_IDENTIFIER)
    def get_main_entity_detail(
        user: User,
        main_entity_id: str,
        setting_id: str,
    ) -> AgentcoreRuntimeEntity:
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        aws_creds = get_setting_aws_credentials(setting.id)

        try:
            runtime_info = BedrockAgentCoreRuntimeService._bedrock_get_agent_runtime(
                runtime_id=main_entity_id,
                region=aws_creds.region,
                access_key_id=aws_creds.access_key_id,
                secret_access_key=aws_creds.secret_access_key,
                session_token=aws_creds.session_token,
            )
        except ClientError as e:
            if is_resource_not_found(e):
                existing_assistants = Assistant.get_by_bedrock_runtime_aws_settings_id(str(setting.id))
                has_imported_endpoints = any(
                    a.bedrock_agentcore_runtime and a.bedrock_agentcore_runtime.runtime_id == main_entity_id
                    for a in existing_assistants
                )
                if has_imported_endpoints:
                    return AgentcoreRuntimeEntity(id=main_entity_id, status=RuntimeStatus.DELETED_ON_AWS)
            raise

        runtime_status = (
            RuntimeStatus.PREPARED
            if runtime_info.get("status") == _AWS_RUNTIME_READY_STATUS
            else RuntimeStatus.NOT_PREPARED
        )

        return AgentcoreRuntimeEntity(
            id=runtime_info.get("agentRuntimeId"),
            name=runtime_info.get("agentRuntimeName"),
            status=runtime_status,
            description=runtime_info.get("description"),
            version=runtime_info.get("agentRuntimeVersion"),
            updatedAt=runtime_info.get("lastUpdatedAt"),
        )

    @staticmethod
    def list_importable_entities_for_main_entity(
        user: User,
        main_entity_id: str,
        setting_id: str,
        page: int,
        per_page: int,
        next_token: Optional[str] = None,
    ) -> tuple[List[AgentcoreEndpointEntity], Optional[str]]:
        return BedrockAgentCoreEndpointService.list_importable_entities_for_main_entity(
            user=user,
            main_entity_id=main_entity_id,
            setting_id=setting_id,
            page=page,
            per_page=per_page,
            next_token=next_token,
        )

    @staticmethod
    def get_importable_entity_detail(
        user: User,
        main_entity_id: str,
        importable_entity_detail: str,
        setting_id: str,
    ) -> AgentcoreEndpointDetailEntity:
        return BedrockAgentCoreEndpointService.get_importable_entity_detail(
            user=user,
            main_entity_id=main_entity_id,
            importable_entity_detail=importable_entity_detail,
            setting_id=setting_id,
        )

    @staticmethod
    def import_entities(user: User, import_payload: dict[str, List[ImportAgentcoreRuntime]]):
        return BedrockAgentCoreEndpointService.import_entities(
            user=user,
            import_payload=import_payload,
        )

    @staticmethod
    def unimport_entity(entity_id: str, user: User) -> None:
        return BedrockAgentCoreEndpointService.unimport_entity(
            entity_id=entity_id,
            user=user,
        )

    # --- Runtime lifecycle ---

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
            return None

        try:
            aws_creds = get_setting_aws_credentials(entity.bedrock_agentcore_runtime.aws_settings_id)

            BedrockAgentCoreEndpointService._bedrock_get_runtime_endpoint(
                runtime_id=entity.bedrock_agentcore_runtime.runtime_id,
                endpoint_name=entity.bedrock_agentcore_runtime.runtime_endpoint_name,
                region=aws_creds.region,
                access_key_id=aws_creds.access_key_id,
                secret_access_key=aws_creds.secret_access_key,
                session_token=aws_creds.session_token,
            )

            return None

        except ClientError as e:
            if is_resource_not_found(e):
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

        for subassistant_id in entity.assistant_ids:
            subassistant = Assistant.find_by_id(subassistant_id)
            if subassistant:
                deleted_name = BedrockAgentCoreRuntimeService.validate_remote_entity_exists_and_cleanup(subassistant)
                if deleted_name:
                    deleted_assistants_names.append(deleted_name)
                    deleted_subassistant_ids.append(subassistant_id)
            else:
                deleted_subassistant_ids.append(subassistant_id)

        deleted_name = BedrockAgentCoreRuntimeService.validate_remote_entity_exists_and_cleanup(entity)
        if deleted_name:
            deleted_assistants_names.append(deleted_name)
        elif deleted_assistants_names:
            entity.assistant_ids = [aid for aid in entity.assistant_ids if aid not in deleted_subassistant_ids]
            entity.save()

        return deleted_assistants_names

    @staticmethod
    def invoke_agentcore_runtime(
        assistant: Assistant,
        input_text: str,
        conversation_id: str,
        history: Optional[List[ChatMessage]] = None,
        thread_generator: Optional[Any] = None,
    ) -> InvokeAgentCoreRuntimeResponse:
        start_time = time()

        if (
            not assistant.bedrock_agentcore_runtime
            or not assistant.bedrock_agentcore_runtime.runtime_arn
            or not assistant.bedrock_agentcore_runtime.aws_settings_id
        ):
            raise ValueError("Trying to invoke non-AgentCore runtime assistant.")

        try:
            aws_creds = get_setting_aws_credentials(assistant.bedrock_agentcore_runtime.aws_settings_id)

            configuration_json = assistant.bedrock_agentcore_runtime.configuration_json
            response_config = AgentcoreResponseConfig.parse_json(configuration_json)
            logger.debug(
                "[AgentCore] invoke: history_turns=%s configuration_json=%s",
                len(history) if history else 0,
                configuration_json,
            )
            payload, accept = BedrockAgentCoreRuntimeService._build_agentcore_request(
                configuration_json, input_text, history, response_config
            )

            logger.debug("AgentCore request: accept=%s payload=%s", accept, payload.decode("utf-8", errors="replace"))

            raw_response = BedrockAgentCoreRuntimeService._bedrock_invoke_runtime(
                runtime_arn=assistant.bedrock_agentcore_runtime.runtime_arn,
                qualifier=assistant.bedrock_agentcore_runtime.runtime_endpoint_name,
                payload=payload,
                accept=accept,
                region=aws_creds.region,
                access_key_id=aws_creds.access_key_id,
                secret_access_key=aws_creds.secret_access_key,
                session_token=aws_creds.session_token,
            )

            output, thoughts = BedrockAgentCoreRuntimeService._parse_agentcore_response(
                raw_response, response_config, thread_generator
            )

            logger.debug("AgentCore response: output=%r thoughts_count=%d", output, len(thoughts))

            return {
                "output": output,
                "thoughts": [t.model_dump() for t in thoughts],
                "time_elapsed": time() - start_time,
            }
        except ClientError as e:
            if is_resource_not_found(e):
                logger.warning(f"AgentCore runtime not found on remote: {e}")

                BedrockAgentCoreRuntimeService.validate_remote_entity_exists_and_cleanup_with_subassistants(assistant)
            else:
                logger.error(f"AWS ClientError invoking AgentCore runtime: {e}")

            return {
                "output": str(e),
                "thoughts": [],
                "time_elapsed": time() - start_time,
            }
        except Exception as e:
            logger.error(f"Unexpected error invoking AgentCore runtime: {e}")
            return {
                "output": str(e),
                "thoughts": [],
                "time_elapsed": time() - start_time,
            }

    # --- Private AWS call methods ---

    @staticmethod
    def _get_deleted_runtime_entities(
        setting_id: str,
        seen_runtime_ids: set[str],
        aws_creds,
    ) -> List[AgentcoreRuntimeEntity]:
        existing_assistants = Assistant.get_by_bedrock_runtime_aws_settings_id(setting_id)
        candidate_ids = {
            a.bedrock_agentcore_runtime.runtime_id
            for a in existing_assistants
            if a.bedrock_agentcore_runtime and a.bedrock_agentcore_runtime.runtime_id
        } - seen_runtime_ids

        deleted: List[AgentcoreRuntimeEntity] = []
        for runtime_id in candidate_ids:
            try:
                BedrockAgentCoreRuntimeService._bedrock_get_agent_runtime(
                    runtime_id=runtime_id,
                    region=aws_creds.region,
                    access_key_id=aws_creds.access_key_id,
                    secret_access_key=aws_creds.secret_access_key,
                    session_token=aws_creds.session_token,
                )
            except ClientError as e:
                if is_resource_not_found(e):
                    deleted.append(AgentcoreRuntimeEntity(id=runtime_id, status=RuntimeStatus.DELETED_ON_AWS))
                else:
                    logger.error(f"Unexpected ClientError checking AgentCore runtime {runtime_id}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error checking AgentCore runtime {runtime_id}: {e}")

        return deleted

    @staticmethod
    def _fetch_main_entity_names_for_setting(setting) -> tuple[List[str] | None, bool]:
        aws_creds = get_setting_aws_credentials(setting.id)

        all_runtimes, _ = BedrockAgentCoreRuntimeService._bedrock_list_agent_runtimes(
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
            page=0,
            per_page=ALL_SETTINGS_OVERVIEW_ENTITY_COUNT,
            max_retry_attempts=1,
        )

        seen_runtime_ids: set[str] = set()
        runtime_names = []
        for runtime_info in all_runtimes:
            runtime_id = runtime_info.get("agentRuntimeId")
            if len(runtime_names) < ALL_SETTINGS_OVERVIEW_ENTITY_COUNT:
                runtime_names.append(runtime_info.get("agentRuntimeName", "Unknown Runtime"))
            if runtime_id:
                seen_runtime_ids.add(runtime_id)

        deleted_entities = BedrockAgentCoreRuntimeService._get_deleted_runtime_entities(
            setting_id=str(setting.id),
            seen_runtime_ids=seen_runtime_ids,
            aws_creds=aws_creds,
        )
        for entity in deleted_entities:
            if len(runtime_names) >= ALL_SETTINGS_OVERVIEW_ENTITY_COUNT:
                break
            runtime_names.append(entity.id)

        return runtime_names, bool(deleted_entities)

    @staticmethod
    def _build_agentcore_request(
        configuration_json: Optional[str],
        input_text: str,
        history: Optional[List[ChatMessage]] = None,
        response_config: Optional[AgentcoreResponseConfig] = None,
    ) -> tuple[bytes, str]:
        if response_config is not None:
            request_config = AgentcoreRequestConfig.from_json(configuration_json)
            return AgentcoreRequestBuilder(request_config).build(input_text, history), (
                AgentcoreContentType.SSE if response_config.streaming else AgentcoreContentType.JSON
            )
        return json.dumps({"message": input_text}).encode("utf-8"), AgentcoreContentType.SSE

    @staticmethod
    def _parse_agentcore_response(
        raw_response, response_config: AgentcoreResponseConfig, thread_generator: Optional[Any] = None
    ) -> tuple[str, list]:
        """Route to the streaming or JSON parser based on response_config."""
        logger.debug(
            "AgentCore response: streaming=%s thread_generator=%s config=%s",
            response_config.streaming,
            thread_generator is not None,
            response_config.model_dump_json(),
        )

        if response_config.streaming:
            return BedrockAgentCoreRuntimeService._parse_agentcore_streaming_response(
                raw_response, response_config, thread_generator
            )

        return BedrockAgentCoreRuntimeService._parse_agentcore_json_response(
            raw_response, response_config, thread_generator
        )

    @staticmethod
    def _parse_agentcore_streaming_response(
        raw_response, response_config: AgentcoreResponseConfig, thread_generator: Optional[Any] = None
    ) -> tuple[str, list]:
        """Parse an SSE stream from AgentCore.

        With thread_generator: forwards text chunks and thoughts as SSE events and returns (full_text, []).
        Without thread_generator: accumulates and returns (full_text, thoughts).
        """
        if thread_generator is None:
            return _agentcore_response_parser.parse_streaming(raw_response, response_config)

        content_parts: list[str] = []
        chunk_count = 0

        for text_chunk, emitted_thoughts in _agentcore_response_parser.parse_streaming(
            raw_response, response_config, emit_stream=True
        ):
            for thought in emitted_thoughts:
                thread_generator.send(StreamedGenerationResult(thought=thought).model_dump_json())

            if text_chunk is not None:
                thread_generator.send(StreamedGenerationResult(generated_chunk=text_chunk).model_dump_json())
                content_parts.append(text_chunk)
                chunk_count += 1
        full_text = "".join(content_parts)
        logger.debug("AgentCore streaming complete: %d text chunks sent", chunk_count)
        thread_generator.send(StreamedGenerationResult(last=True, generated_chunk="").model_dump_json())

        return full_text, []

    @staticmethod
    def _parse_agentcore_json_response(
        raw_response, response_config: AgentcoreResponseConfig, thread_generator: Optional[Any] = None
    ) -> tuple[str, list]:
        """Parse a JSON response from AgentCore.

        With thread_generator: forwards thoughts as SSE events and returns (text, []).
        Without thread_generator: returns (text, thoughts) for the caller to consume.
        """
        text, thoughts = _agentcore_response_parser.parse_json(raw_response, response_config)

        if thread_generator is not None:
            for thought in thoughts:
                thread_generator.send(StreamedGenerationResult(thought=thought).model_dump_json())
            return text, []

        return text, thoughts

    @staticmethod
    def _bedrock_list_agent_runtimes(
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
            service_name="bedrock-agentcore-control",
            api_method_name="list_agent_runtimes",
            response_key="agentRuntimes",
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
    def _bedrock_get_agent_runtime(
        runtime_id: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
    ) -> dict:
        def _func(client):
            response = client.get_agent_runtime(agentRuntimeId=runtime_id)
            return response

        client = get_aws_client_for_service(
            "bedrock-agentcore-control",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
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
        session_token: Optional[str] = None,
        accept: str = AgentcoreContentType.SSE,
    ):
        def _func(client):
            response = client.invoke_agent_runtime(
                agentRuntimeArn=runtime_arn,
                qualifier=qualifier,
                payload=payload,
                contentType=AgentcoreContentType.JSON,
                accept=accept,
            )

            if accept == AgentcoreContentType.JSON:
                body = response.get("response") or response.get("Body")
                if hasattr(body, "read"):
                    body = body.read()
                return body
            # Return raw stream body so the caller can process it (structured or legacy)
            return response.get("response")

        client = get_aws_client_for_service(
            "bedrock-agentcore",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

        return handle_aws_call(_func, client)

    @staticmethod
    def _parse_response_by_content_type(response: dict, content_type: str) -> str:
        if AgentcoreContentType.SSE in content_type:
            return BedrockAgentCoreRuntimeService._parse_streaming_response(response)
        elif content_type == AgentcoreContentType.JSON:
            return BedrockAgentCoreRuntimeService._parse_json_response(response)
        else:
            return str(response)

    @staticmethod
    def _parse_streaming_response(response: dict) -> str:
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
