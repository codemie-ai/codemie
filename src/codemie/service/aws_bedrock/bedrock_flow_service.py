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
import re
from time import time
from typing import List, Optional

from botocore.exceptions import ClientError

from codemie.configs import logger
from codemie.core.workflow_models.workflow_config import (
    BedrockFlowData,
    WorkflowConfig,
    WorkflowConfigBase,
)
from codemie.core.workflow_models.workflow_execution import WorkflowExecution
from codemie.core.workflow_models.workflow_models import (
    CustomWorkflowNode,
    WorkflowMode,
    WorkflowNextState,
    WorkflowRetryPolicy,
    WorkflowState,
)
from codemie.rest_api.models.guardrail import GuardrailEntity
from codemie.rest_api.models.settings import AWSCredentials, Settings, SettingsBase
from codemie.rest_api.models.vendor import ImportFlow
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
from codemie.service.workflow_service import WorkflowService


workflow_service = WorkflowService()


class BedrockFlowService(BaseBedrockService):
    @staticmethod
    @aws_service_exception_handler("Bedrock flows")
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
                executor.submit(BedrockFlowService._fetch_main_entity_names_for_setting, setting): setting
                for setting in paged_settings
            }

            # Collect results as they complete
            for future in as_completed(future_to_setting):
                setting = future_to_setting[future]
                try:
                    flow_names = future.result()
                    if flow_names is not None:  # None indicates an error occurred
                        results["data"].append(
                            {
                                "setting_id": str(setting.id),
                                "setting_name": setting.alias,
                                "project": setting.project_name,
                                "entities": flow_names,
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
    @aws_service_exception_handler("Bedrock flows")
    def list_main_entities(
        user: User,
        setting_id: str,
        page: int,
        per_page: int,
        next_token: Optional[str] = None,
    ) -> tuple[List[dict], Optional[str]]:
        """
        Retrieve a paginated list of AWS Bedrock flows.

        Args:
            user (User): The user requesting the entities.
            setting_id (str): The identifier for the AWS settings to use.
            page (int): The page number (0-based) for pagination.
            per_page (int): The number of items per page.

        Returns:
            List: A list of flow summaries.
        """
        setting: SettingsBase = get_setting_for_user(user, setting_id)
        aws_creds = get_setting_aws_credentials(setting.id)

        all_flows, return_next_token = BedrockFlowService._bedrock_list_flows(
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
            page=page,
            per_page=per_page,
            next_token=next_token,
        )

        flow_data = []
        for flow_info in all_flows:
            flow_status = "PREPARED" if flow_info.get("status") == "Prepared" else "NOT_PREPARED"

            flow_data.append(
                {
                    "id": flow_info.get("id"),
                    "name": flow_info.get("name"),
                    "status": flow_status,
                    "description": flow_info.get("description"),
                    "version": flow_info.get("version"),
                    "createdAt": flow_info.get("createdAt"),
                    "updatedAt": flow_info.get("updatedAt"),
                }
            )

        return flow_data, return_next_token

    @staticmethod
    @aws_service_exception_handler("Bedrock flows")
    def get_main_entity_detail(
        user: User,
        main_entity_id: str,
        setting_id: str,
    ) -> dict:
        setting: SettingsBase = get_setting_for_user(user, setting_id)
        aws_creds = get_setting_aws_credentials(setting.id)

        flow_info = BedrockFlowService._bedrock_get_flow(
            flow_id=main_entity_id,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
        )

        flow_status = "PREPARED" if flow_info.get("status") == "Prepared" else "NOT_PREPARED"

        return {
            "id": flow_info.get("id"),
            "name": flow_info.get("name"),
            "status": flow_status,
            "description": flow_info.get("description"),
            "version": flow_info.get("version"),
            "createdAt": flow_info.get("createdAt"),
            "updatedAt": flow_info.get("updatedAt"),
        }

    @staticmethod
    @aws_service_exception_handler("Bedrock flows")
    def list_importable_entities_for_main_entity(
        user: User,
        main_entity_id: str,
        setting_id: str,
        page: int,
        per_page: int,
        next_token: Optional[str] = None,
    ) -> tuple[List[dict], Optional[str]]:
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        existing_entities = WorkflowConfig.get_by_bedrock_aws_settings_id(str(setting.id))
        existing_entities_map = {
            entity.bedrock.bedrock_flow_alias_id: entity for entity in existing_entities if entity.bedrock
        }

        aws_creds = get_setting_aws_credentials(setting.id)

        flow_aliases = []

        flow_aliases_information, return_next_token = BedrockFlowService._bedrock_list_flow_aliases(
            flow_id=main_entity_id,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
            page=page,
            per_page=per_page,
            next_token=next_token,
        )

        for alias_info in flow_aliases_information:
            alias_id = alias_info.get("id")

            if not alias_info.get("routingConfiguration"):
                logger.warning(f"No routing configuration found for alias {alias_id} of flow {main_entity_id}.")
                version = None
            else:
                # There can be a DRAFT version, which is not a valid version for our usecase
                version = alias_info["routingConfiguration"][0]["flowVersion"]

                alias_status = "NOT_PREPARED" if not re.match(r"^\d{1,5}$", version) else "PREPARED"

            alias_dict = {
                "id": alias_id,
                "name": alias_info.get("name"),
                "status": alias_status,
                "description": alias_info.get("description"),
                "version": version,
                "createdAt": alias_info.get("createdAt"),
                "updatedAt": alias_info.get("updatedAt"),
            }

            if alias_id in existing_entities_map:
                alias_dict["aiRunId"] = existing_entities_map[alias_id].id

            flow_aliases.append(alias_dict)

        return flow_aliases, return_next_token

    @staticmethod
    @aws_service_exception_handler("Bedrock flows")
    def get_importable_entity_detail(
        user: User,
        main_entity_id: str,
        importable_entity_detail: str,
        setting_id: str,
    ):
        setting: SettingsBase = get_setting_for_user(user, setting_id)
        aws_creds = get_setting_aws_credentials(setting.id)

        version_info = BedrockFlowService._bedrock_get_flow_version(
            flow_id=main_entity_id,
            version=importable_entity_detail,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
        )

        if not version_info:
            logger.warning(
                f"Failed to retrieve version information for flow {main_entity_id}, version: {importable_entity_detail}"
            )
            return {}

        status = "PREPARED" if version_info.get("status") == "Prepared" else "NOT_PREPARED"

        return {
            "id": version_info.get("id"),
            "name": version_info.get("name"),
            "version": version_info.get("version"),
            "status": status,
            "description": version_info.get("description"),
            "createdAt": version_info.get("createdAt"),
        }

    @staticmethod
    @aws_service_exception_handler("Bedrock flows")
    def import_entities(user: User, import_payload: dict[str, List[ImportFlow]]):
        """
        Import Bedrock agent aliases for the given user and payload.

        Args:
            user (User): The user performing the import.
            import_payload (dict): Mapping of setting_id to list of agentAliasIds.

        Returns:
            dict: Mapping of setting_id to list of imported agent alias info.
        """
        results = []

        for setting_id, flow_ids in import_payload.items():
            try:
                setting: SettingsBase = get_setting_for_user(user, setting_id)

                # Retrieve all existing entities for this settings
                existing_entities = WorkflowConfig.get_by_bedrock_aws_settings_id(str(setting.id))
                existing_entities_map = {
                    entity.bedrock.bedrock_flow_alias_id: entity for entity in existing_entities if entity.bedrock
                }

                aws_creds = get_setting_aws_credentials(setting.id)
                flow_version_info_cache = {}

                for flow_ids_data in flow_ids:
                    alias_info = BedrockFlowService._bedrock_get_flow_alias(
                        flow_id=flow_ids_data.id,
                        alias_id=flow_ids_data.flowAliasId,
                        region=aws_creds.region,
                        access_key_id=aws_creds.access_key_id,
                        secret_access_key=aws_creds.secret_access_key,
                        session_token=aws_creds.session_token,
                    )

                    flow_version_info = BedrockFlowService._validate_and_retrieve_alias_version(
                        flow_id=flow_ids_data.id,
                        alias_info=alias_info,
                        aws_creds=aws_creds,
                        flow_version_info_cache=flow_version_info_cache,
                    )

                    if not flow_version_info:
                        results.append(
                            {
                                "flowId": flow_ids_data.id,
                                "flowAliasId": flow_ids_data.flowAliasId,
                                "error": {
                                    "statusCode": "422",
                                    "message": (
                                        f"Valid version info not found for flow {flow_ids_data.id} "
                                        f"and alias {flow_ids_data.flowAliasId}"
                                    ),
                                },
                            }
                        )
                        continue

                    flow_definition = flow_version_info.get("definition", {})

                    input_node = next((node for node in flow_definition["nodes"] if node.get("type") == "Input"), None)
                    if not input_node:
                        results.append(
                            {
                                "flowId": flow_ids_data.id,
                                "flowAliasId": flow_ids_data.flowAliasId,
                                "error": {
                                    "statusCode": "422",
                                    "message": (
                                        f"No input node found for {flow_ids_data.id} "
                                        f"and alias {flow_ids_data.flowAliasId}"
                                    ),
                                },
                            }
                        )
                        continue

                    input_node_name = input_node["name"]
                    input_node_output_field = input_node["outputs"][0]["name"]
                    input_node_output_type = input_node["outputs"][0]["type"]

                    workflow_config = BedrockFlowService._create_workflow_object(
                        flow_id=flow_ids_data.id,
                        flow_id_alias=flow_ids_data.flowAliasId,
                        setting=setting,
                        user=user,
                        alias_info=alias_info,
                        input_node_name=input_node_name,
                        input_node_output_field=input_node_output_field,
                        input_node_output_type=input_node_output_type,
                    )

                    created_entity_id = BedrockFlowService._create_or_update_entity(
                        flow_id=flow_ids_data.id,
                        flow_id_alias=flow_ids_data.flowAliasId,
                        workflow_config=workflow_config,
                        existing_entities_map=existing_entities_map,
                    )

                    results.append(
                        {
                            "flowId": flow_ids_data.id,
                            "flowAliasId": flow_ids_data.flowAliasId,
                            "aiRunId": created_entity_id,
                        }
                    )
            except Exception as e:
                results.append(
                    {
                        "flowId": flow_ids_data.id,
                        "flowAliasId": flow_ids_data.flowAliasId,
                        "error": {"statusCode": "500", "message": str(e)},
                    }
                )

        return results

    @staticmethod
    def delete_entities(setting_id: str):
        """
        Delete all imported workflows and their executions for a given setting_id.
        """
        existing_entities = WorkflowConfig.get_by_bedrock_aws_settings_id(setting_id)  # type: ignore
        for entity in existing_entities:
            workflow_executions = WorkflowExecution.get_by_workflow_id(str(entity.id))
            for execution in workflow_executions:
                WorkflowExecution.delete(str(execution.id))

            WorkflowConfig.delete(str(entity.id))
            GuardrailService.remove_guardrail_assignments_for_entity(GuardrailEntity.WORKFLOW, str(entity.id))

    @staticmethod
    def validate_remote_entity_exists_and_cleanup(entity: WorkflowConfigBase):
        if not entity.bedrock or not entity.bedrock.bedrock_flow_id or not entity.bedrock.bedrock_flow_alias_id:
            return None  # not a bedrock entity

        try:
            setting: SettingsBase = Settings.get_by_id(id_=entity.bedrock.bedrock_aws_settings_id)  # type: ignore
            if not setting:
                raise ValueError(f"Setting with id {entity.bedrock.bedrock_aws_settings_id} not found")

            aws_creds = get_setting_aws_credentials(setting.id)

            BedrockFlowService._bedrock_get_flow_alias(
                flow_id=entity.bedrock.bedrock_flow_id,
                alias_id=entity.bedrock.bedrock_flow_alias_id,
                region=aws_creds.region,
                access_key_id=aws_creds.access_key_id,
                secret_access_key=aws_creds.secret_access_key,
                session_token=aws_creds.session_token,
            )

            return None

        except ClientError as e:
            error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            if error_code.strip().lower() == "resourcenotfoundexception":
                workflow_executions = WorkflowExecution.get_by_workflow_id(str(entity.id))
                for execution in workflow_executions:
                    WorkflowExecution.delete(str(execution.id))

                WorkflowConfig.delete(str(entity.id))
                GuardrailService.remove_guardrail_assignments_for_entity(GuardrailEntity.WORKFLOW, str(entity.id))

                return entity.name
            else:
                # any other issue (like configuration) is just ignored at this point
                logger.error(f"Unexpected ClientError validating Bedrock flow {entity.name}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error validating Bedrock flow {entity.name}: {e}")

    @staticmethod
    def invoke_flow(flow_id: str, flow_alias_id: str, user: User, setting_id: str, inputs: list):
        start_time = time()

        try:
            setting: SettingsBase = get_setting_for_user(user, setting_id)

            if not setting.user_id:
                raise ValueError("Setting does not have a user_id associated with it.")

            aws_creds = get_setting_aws_credentials(setting.id)

            response = BedrockFlowService._bedrock_invoke_flow(
                flow_id=flow_id,
                flow_alias=flow_alias_id,
                inputs=inputs,
                region=aws_creds.region,
                access_key_id=aws_creds.access_key_id,
                secret_access_key=aws_creds.secret_access_key,
                session_token=aws_creds.session_token,
            )

            return {
                "output": response,
                "time_elapsed": time() - start_time,
            }
        except ClientError as e:
            logger.error(f"AWS ClientError invoking Bedrock flow {flow_id}:{flow_alias_id}: {e}")
            return {
                "output": str(e),
                "time_elapsed": time() - start_time,
            }
        except Exception as e:
            logger.error(f"Unexpected error invoking Bedrock flow {flow_id}:{flow_alias_id}: {e}")
            return {
                "output": str(e),
                "time_elapsed": time() - start_time,
            }

    @staticmethod
    def _fetch_main_entity_names_for_setting(setting) -> List[str] | None:
        """
        Fetch flow names for a single setting.
        """
        aws_creds = get_setting_aws_credentials(setting.id)

        all_flows, _ = BedrockFlowService._bedrock_list_flows(
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
            page=0,
            per_page=ALL_SETTINGS_OVERVIEW_ENTITY_COUNT,
            max_retry_attempts=1,  # only 1 attempt to avoid incorrect setting config timeouts
        )

        flow_names = []
        for flow_count, flow_info in enumerate(all_flows):
            if flow_count >= ALL_SETTINGS_OVERVIEW_ENTITY_COUNT:
                break
            flow_names.append(flow_info["name"])

        return flow_names

    @staticmethod
    def _create_workflow_object(
        flow_id: str,
        flow_id_alias: str,
        setting: SettingsBase,
        user: User,
        alias_info: dict,
        input_node_name: str,
        input_node_output_field: str,
        input_node_output_type: str,
    ):
        bedrock_flow_state = WorkflowState(
            id="bedrock_flow_1",
            custom_node_id="bedrock_flow_node",  # This should match the node registry key for BedrockFlowNode
            task=f"Invoke Bedrock Flow {flow_id}:{flow_id_alias}",
            next=WorkflowNextState(state_id=None),  # No next state, this is a single-node workflow
            output_schema=None,
            retry_policy=WorkflowRetryPolicy(
                max_attempts=2, initial_interval=1.0, backoff_factor=2.0, max_interval=60.0
            ),
        )

        workflow_config = WorkflowConfig(
            name=f"{flow_id}:{alias_info.get('name', flow_id_alias)}",
            description=alias_info.get(
                "description",
                f"AWS Bedrock Flow {flow_id} (alias {flow_id_alias})",
            ),
            project=setting.project_name,
            created_by=user.as_user_model(),
            mode=WorkflowMode.SEQUENTIAL,
            shared=True,
            states=[bedrock_flow_state],
            custom_nodes=[
                CustomWorkflowNode(
                    id="bedrock_flow_node",
                    custom_node_id="bedrock_flow_node",
                    model=None,
                    config={
                        "flow_id": flow_id,
                        "flow_alias_id": flow_id_alias,
                        "setting_id": setting.id,
                        "input_node_name": input_node_name,
                        "input_node_output_field": input_node_output_field,
                        "input_node_output_type": input_node_output_type,
                    },
                )
            ],
            bedrock=BedrockFlowData(
                bedrock_flow_id=flow_id,
                bedrock_flow_alias_id=flow_id_alias,
                bedrock_aws_settings_id=str(setting.id),
            ),
        )

        return workflow_config

    @staticmethod
    def _create_or_update_entity(
        flow_id: str,
        flow_id_alias: str,
        workflow_config: WorkflowConfig,  # type: ignore
        existing_entities_map: dict,
    ):
        if flow_id_alias in existing_entities_map:
            # Update the existing entity
            entity = existing_entities_map[flow_id_alias]
            update_fields = [
                "name",
                "description",
                "project",
                "mode",
                "shared",
                "states",
                "custom_nodes",
                "bedrock",
            ]
            for field in update_fields:
                setattr(entity, field, getattr(workflow_config, field))

            entity.update(refresh=True)
            logger.info(f"Updated Workflow for Bedrock flow: {flow_id} (Alias: {flow_id_alias})")

            return str(entity.id)
        else:
            # Ensure Application exists for the project
            project_name = workflow_config.project
            if project_name:
                ensure_application_exists(project_name)

            # Create a new entity
            workflow_config.save(refresh=True)
            existing_entities_map[flow_id_alias] = workflow_config  # Add to the map
            logger.info(f"Created Workflow for Bedrock flow: {flow_id} (Alias: {flow_id_alias})")

            return str(workflow_config.id)

    @staticmethod
    def _validate_and_retrieve_alias_version(
        flow_id: str,
        alias_info: dict,
        aws_creds: AWSCredentials,
        flow_version_info_cache: dict,
    ) -> dict | None:
        if not alias_info.get("routingConfiguration"):
            logger.warning(f"No routing configuration found for alias {alias_info['id']} of flow {flow_id}.")
            return None

        if len(alias_info["routingConfiguration"]) > 1:
            logger.warning(f"Multiple routing configurations found for flow {flow_id}. Using the first one.")

        flow_version = alias_info["routingConfiguration"][0]["flowVersion"]

        if flow_version in flow_version_info_cache:
            flow_version_info = flow_version_info_cache[flow_version]
        else:
            # There can be a DRAFT version, which is not a valid version and the API call fails
            if not re.match(r"^\d{1,5}$", flow_version):
                logger.warning(
                    f"Skipping alias {alias_info['id']} for flow {flow_id} due to invalid flow version: {flow_version}"
                )
                return None

            flow_version_info = BedrockFlowService._get_flow_version_info(
                flow_id, flow_version, aws_creds, flow_version_info_cache
            )

        if not flow_version_info:
            return None

        if flow_version_info.get("status") != "Prepared":
            logger.warning("A flow alias with not 'Prepared' status found, skipping.")
            return None

        return flow_version_info

    @staticmethod
    def _get_flow_version_info(
        flow_id: str,
        version: str,
        aws_creds: AWSCredentials,
        flow_version_info_cache: dict,
    ) -> Optional[dict]:
        """
        Retrieve and cache version information for a specific AWS Bedrock Flow alias.

        This method fetches the version information for a specific version of a Bedrock Flow
        and caches it for reuse. If the version does not exist or the API call fails, None is returned.

        Args:
            flow (dict): The Bedrock Flow details.
            version (str): The version ID.
            aws_creds (AWSCredentials): AWS credentials for API calls.
            flow_version_info_cache (dict): Cache for storing version information.

        Returns:
            Optional[dict]: The version information for the alias, or None if retrieval fails.
        """
        if version in flow_version_info_cache:
            return flow_version_info_cache[version]

        try:
            version_info = BedrockFlowService._bedrock_get_flow_version(
                flow_id=flow_id,
                version=version,
                region=aws_creds.region,
                access_key_id=aws_creds.access_key_id,
                secret_access_key=aws_creds.secret_access_key,
                session_token=aws_creds.session_token,
            )
            if not version_info:
                logger.warning(f"Failed to retrieve version information for flow {flow_id} (Version: {version})")
                return None

            flow_version_info_cache[version] = version_info
            return version_info
        except Exception as e:
            logger.error(f"Error retrieving version info for flow {flow_id}: {e}")
            return None

    @staticmethod
    def _bedrock_list_flows(
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
            api_method_name="list_flows",
            response_key="flowSummaries",
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
    def _bedrock_list_flow_aliases(
        flow_id: str,
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
            api_method_name="list_flow_aliases",
            response_key="flowAliasSummaries",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
            page=page,
            per_page=per_page,
            next_token=next_token,
            flowIdentifier=flow_id,  # API-specific parameter
        )

    @staticmethod
    def _bedrock_get_flow(
        flow_id: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
    ) -> dict:
        def _func(client):
            return client.get_flow(flowIdentifier=flow_id)

        client = get_aws_client_for_service(
            "bedrock-agent",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

        return handle_aws_call(_func, client)

    @staticmethod
    def _bedrock_get_flow_version(
        flow_id: str,
        version: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
    ) -> dict:
        def _func(client):
            return client.get_flow_version(flowIdentifier=flow_id, flowVersion=version)

        client = get_aws_client_for_service(
            "bedrock-agent",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

        return handle_aws_call(_func, client)

    @staticmethod
    def _bedrock_get_flow_alias(
        flow_id: str,
        alias_id: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
    ) -> dict:
        def _func(client):
            return client.get_flow_alias(
                flowIdentifier=flow_id,
                aliasIdentifier=alias_id,
            )

        client = get_aws_client_for_service(
            "bedrock-agent",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

        return handle_aws_call(_func, client)

    @staticmethod
    def _bedrock_invoke_flow(
        flow_id: str,
        flow_alias: str,
        inputs: list,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
    ):
        def _func(client):
            response = client.invoke_flow(
                flowIdentifier=flow_id,
                flowAliasIdentifier=flow_alias,
                inputs=inputs,
            )

            result = {}

            for event in response.get("responseStream"):
                result.update(event)

            if result['flowCompletionEvent']['completionReason'] == 'SUCCESS':
                logger.info(
                    f"Flow invocation was successful! The output of the flow is as follows: "
                    f"{result['flowOutputEvent']['content']['document']}"
                )

            else:
                logger.warning(
                    f"The flow invocation completed because of the following reason: "
                    f"{result['flowCompletionEvent']['completionReason']}",
                )

            return result['flowOutputEvent']['content']['document']

        client = get_aws_client_for_service(
            "bedrock-agent-runtime",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

        return handle_aws_call(_func, client)
