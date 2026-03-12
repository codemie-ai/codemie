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
from typing import List, Literal, Optional
import re
from botocore.exceptions import ClientError

from codemie.configs import logger
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.guardrail import Guardrail
from codemie.rest_api.models.settings import AWSCredentials, Settings, SettingsBase
from codemie.rest_api.models.vendor import ImportGuardrail
from codemie.rest_api.security.user import User
from codemie.rest_api.utils.default_applications import ensure_application_exists
from codemie.service.aws_bedrock.base_bedrock_service import ALL_SETTINGS_OVERVIEW_ENTITY_COUNT, BaseBedrockService
from codemie.service.aws_bedrock.exceptions import aws_service_exception_handler
from codemie.service.aws_bedrock.utils import (
    CONFIGURATION_INVALID_EXCEPTIONS,
    call_bedrock_listing_api,
    get_all_settings_for_user,
    get_setting_for_user,
    handle_aws_call,
    get_aws_client_for_service,
    get_setting_aws_credentials,
)
from codemie.service.guardrail.guardrail_service import GuardrailService


class BedrockGuardrailService(BaseBedrockService):
    @staticmethod
    @aws_service_exception_handler("Bedrock guardrails")
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
                executor.submit(BedrockGuardrailService._fetch_main_entity_names_for_setting, setting): setting
                for setting in paged_settings
            }

            # Collect results as they complete
            for future in as_completed(future_to_setting):
                setting = future_to_setting[future]
                try:
                    guardrail_names = future.result()
                    if guardrail_names is not None:  # None indicates an error occurred
                        results["data"].append(
                            {
                                "setting_id": str(setting.id),
                                "setting_name": setting.alias,
                                "project": setting.project_name,
                                "entities": guardrail_names,
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
    @aws_service_exception_handler("Bedrock guardrails")
    def list_main_entities(
        user: User,
        setting_id: str,
        page: int,
        per_page: int,
        next_token: Optional[str] = None,
    ) -> tuple[List[dict], Optional[str]]:
        """
        Retrieve a paginated list of AWS Bedrock guardrails.

        Args:
            user (User): The user requesting the entities.
            setting_id (str): The identifier for the AWS settings to use.
            page (int): The page number (0-based) for pagination.
            per_page (int): The number of items per page.

        Returns:
            List: A list of guardrail summaries.
        """
        setting: SettingsBase = get_setting_for_user(user, setting_id)
        aws_creds = get_setting_aws_credentials(setting.id)

        all_guardrails, return_next_token = BedrockGuardrailService._bedrock_list_guardrails(
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
            page=page,
            per_page=per_page,
            next_token=next_token,
        )

        guardrail_data = []
        for guardrail_info in all_guardrails:
            guardrail_status = "PREPARED" if guardrail_info.get("status") == "READY" else "NOT_PREPARED"

            guardrail_data.append(
                {
                    "id": guardrail_info.get("id"),
                    "name": guardrail_info.get("name"),
                    "status": guardrail_status,
                    "description": guardrail_info.get("description"),
                    "version": guardrail_info.get("version"),
                    "createdAt": guardrail_info.get("createdAt"),
                    "updatedAt": guardrail_info.get("updatedAt"),
                }
            )

        return guardrail_data, return_next_token

    @staticmethod
    @aws_service_exception_handler("Bedrock guardrails")
    def get_main_entity_detail(
        user: User,
        main_entity_id: str,
        setting_id: str,
    ) -> dict:
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        aws_creds = get_setting_aws_credentials(setting.id)

        guardrail_info = BedrockGuardrailService._bedrock_get_guardrail(
            guardrail_id=main_entity_id,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
        )

        guardrail_status = "PREPARED" if guardrail_info.get("status") == "READY" else "NOT_PREPARED"

        return {
            "id": guardrail_info.get("guardrailId"),
            "name": guardrail_info.get("name"),
            "status": guardrail_status,
            "description": guardrail_info.get("description"),
            "version": guardrail_info.get("version"),
            "createdAt": guardrail_info.get("createdAt"),
            "updatedAt": guardrail_info.get("updatedAt"),
        }

    @staticmethod
    @aws_service_exception_handler("Bedrock guardrails")
    def list_importable_entities_for_main_entity(
        user: User,
        main_entity_id: str,
        setting_id: str,
        page: int,
        per_page: int,
        next_token: Optional[str] = None,
    ) -> tuple[List[dict], Optional[str]]:
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        existing_entities = Guardrail.get_by_bedrock_aws_settings_id(str(setting.id))
        existing_entities_map = {
            f"{entity.bedrock.bedrock_guardrail_id}-{entity.bedrock.bedrock_version}": entity
            for entity in existing_entities
            if entity.bedrock
        }

        aws_creds = get_setting_aws_credentials(setting.id)

        guardrail_versions = []

        # Get all versions for the specific guardrail
        versions_data, return_next_token = BedrockGuardrailService._bedrock_list_guardrails(
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
            guardrail_id=main_entity_id,
            page=page,
            per_page=per_page,
            next_token=next_token,
        )

        for version_data in versions_data:
            version_status = "PREPARED" if version_data.get("status", "") == "READY" else "NOT_PREPARED"

            version = version_data.get("version", "")
            if not re.match(r"^\d{1,5}$", version):
                version_status = "NOT_PREPARED"

            version_dict = {
                "id": version_data.get("id"),
                "version": version,
                "name": version_data.get("name"),
                "status": version_status,
                "description": version_data.get("description"),
                "createdAt": version_data.get("createdAt"),
                "updatedAt": version_data.get("updatedAt"),
            }

            entity_key = f"{version_data.get('id')}-{version}"
            if entity_key in existing_entities_map:
                version_dict["aiRunId"] = existing_entities_map[entity_key].id

            guardrail_versions.append(version_dict)

        return guardrail_versions, return_next_token

    @staticmethod
    @aws_service_exception_handler("Bedrock guardrails")
    def get_importable_entity_detail(
        user: User,
        main_entity_id: str,
        importable_entity_detail: str,
        setting_id: str,
    ):
        setting: SettingsBase = get_setting_for_user(user, setting_id)
        aws_creds = get_setting_aws_credentials(setting.id)

        version_info = BedrockGuardrailService._bedrock_get_guardrail(
            guardrail_id=main_entity_id,
            guardrail_version=importable_entity_detail,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
        )

        if not version_info:
            logger.warning(
                f"Failed to retrieve version information for guardrail {main_entity_id}, "
                f"version: {importable_entity_detail}"
            )
            return {}

        status = "PREPARED" if version_info.get("status") == "READY" else "NOT_PREPARED"

        return {
            "id": version_info.get("guardrailId"),
            "name": version_info.get("name"),
            "description": version_info.get("description"),
            "version": version_info.get("version"),
            "status": status,
            "blockedInputMessaging": version_info.get("blockedInputMessaging"),
            "blockedOutputsMessaging": version_info.get("blockedOutputsMessaging"),
            "createdAt": version_info.get("createdAt"),
            "updatedAt": version_info.get("updatedAt"),
        }

    @staticmethod
    @aws_service_exception_handler("Bedrock guardrails")
    def import_entities(user: User, import_payload: dict[str, List[ImportGuardrail]]):
        """
        Import Bedrock guardrails for the given user and payload.

        Args:
            user (User): The user performing the import.
            import_payload (dict): Mapping of setting_id to list of ImportGuardrail.

        Returns:
            list: List of imported guardrail info.
        """
        results = []

        for setting_id, entity_imports in import_payload.items():
            setting: SettingsBase = get_setting_for_user(user, setting_id)

            # Retrieve all existing entities for this settings
            existing_entities = Guardrail.get_by_bedrock_aws_settings_id(str(setting.id))
            existing_entities_map = {
                f"{entity.bedrock.bedrock_guardrail_id}-{entity.bedrock.bedrock_version}": entity
                for entity in existing_entities
                if entity.bedrock
            }

            aws_creds = get_setting_aws_credentials(setting.id)

            for entity_import in entity_imports:
                results.append(
                    BedrockGuardrailService._process_entity_import(
                        user=user,
                        setting=setting,
                        aws_creds=aws_creds,
                        existing_entities_map=existing_entities_map,
                        guardrail_input_id=entity_import.id,
                        guardrail_input_version=entity_import.version,
                    )
                )

        return results

    @staticmethod
    def delete_entities(setting_id: str):
        """
        Delete all imported guardrails for a given setting_id.
        """
        existing_entities = Guardrail.get_by_bedrock_aws_settings_id(setting_id)
        for entity in existing_entities:
            entity.delete()
            GuardrailService.remove_guardrail_assignments_for_guardrail(str(entity.id))

    @staticmethod
    def validate_remote_entity_exists_and_cleanup(entity: Guardrail):
        if not entity.bedrock or not entity.bedrock.bedrock_guardrail_id or not entity.bedrock.bedrock_version:
            return None  # not a bedrock entity

        try:
            setting: SettingsBase = Settings.get_by_id(entity.bedrock.bedrock_aws_settings_id)  # type: ignore
            if not setting:
                raise ValueError(f"Setting with id {entity.bedrock.bedrock_aws_settings_id} not found")

            aws_creds = get_setting_aws_credentials(setting.id)

            BedrockGuardrailService._bedrock_get_guardrail(
                guardrail_id=entity.bedrock.bedrock_guardrail_id,
                guardrail_version=entity.bedrock.bedrock_version,
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
                GuardrailService.remove_guardrail_assignments_for_guardrail(str(entity.id))
            else:
                # any other issue (like configuration) is just ignored at this point
                logger.error(f"Unexpected ClientError validating Bedrock guardrail: {e}")
        except Exception as e:
            logger.error(f"Unexpected error validating Bedrock guardrail: {e}")

    @staticmethod
    def apply_guardrail(
        guardrail: Guardrail,
        content: list[dict],
        source: Literal["INPUT", "OUTPUT"] = "INPUT",
        output_scope: Literal["INTERVENTIONS", "FULL"] = "INTERVENTIONS",
    ) -> dict:
        """
        Applies a Bedrock guardrail to the provided content.

        Args:
            guardrail (Guardrail): The Guardrail entity to apply.
            content (list): List of content dicts to check (see AWS docs).
            source (Literal["INPUT", "OUTPUT"]): 'INPUT' or 'OUTPUT'.
            output_scope (Literal["INTERVENTIONS", "FULL"]): 'INTERVENTIONS' or 'FULL'.

        Returns:
            dict: The response from AWS Bedrock apply_guardrail.
        """
        if not guardrail.bedrock:
            raise ValueError("Guardrail does not have a valid Bedrock information.")

        setting: SettingsBase = Settings.get_by_id(guardrail.bedrock.bedrock_aws_settings_id)  # type: ignore
        if not setting:
            raise ValueError(f"Setting with id {guardrail.bedrock.bedrock_aws_settings_id} not found")

        aws_creds = get_setting_aws_credentials(setting.id)
        if not aws_creds:
            raise ValueError("AWS credentials not found for guardrail invocation.")

        return BedrockGuardrailService._bedrock_apply_guardrail(
            guardrail_identifier=guardrail.bedrock.bedrock_guardrail_id,
            guardrail_version=guardrail.bedrock.bedrock_version,
            content=content,
            source=source,
            output_scope=output_scope,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
        )

    @staticmethod
    def _fetch_main_entity_names_for_setting(setting) -> List[str] | None:
        """
        Fetch guardrail names for a single setting.
        """
        aws_creds = get_setting_aws_credentials(setting.id)

        all_guardrails, _ = BedrockGuardrailService._bedrock_list_guardrails(
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
            page=0,
            per_page=ALL_SETTINGS_OVERVIEW_ENTITY_COUNT,
            max_retry_attempts=1,  # only 1 attempt to avoid incorrect setting config timeouts
        )

        guardrail_names = []
        for guardrail_count, guardrail_info in enumerate(all_guardrails):
            if guardrail_count >= ALL_SETTINGS_OVERVIEW_ENTITY_COUNT:
                break
            guardrail_names.append(guardrail_info["name"])

        return guardrail_names

    @staticmethod
    def _process_entity_import(
        user: User,
        setting: SettingsBase,
        aws_creds: AWSCredentials,
        existing_entities_map: dict,
        guardrail_input_id: str,
        guardrail_input_version: str,
    ) -> dict:
        try:
            guardrail_detail = BedrockGuardrailService._bedrock_get_guardrail(
                guardrail_id=guardrail_input_id,
                guardrail_version=guardrail_input_version,
                region=aws_creds.region,
                access_key_id=aws_creds.access_key_id,
                secret_access_key=aws_creds.secret_access_key,
                session_token=aws_creds.session_token,
            )

            guardrail_id = guardrail_detail.get("guardrailId")
            if not guardrail_id:
                return {
                    "guardrailId": guardrail_input_id,
                    "version": guardrail_input_version,
                    "error": {
                        "statusCode": "404",
                        "message": f"Guardrail with ID {guardrail_id} was not found",
                    },
                }

            version = guardrail_detail.get("version", "")
            if not re.match(r"^\d{1,5}$", version):
                return {
                    "guardrailId": guardrail_id,
                    "version": version,
                    "error": {
                        "statusCode": "409",
                        "message": f"Guardrail version is not supported. Version: {version}",
                    },
                }

            if guardrail_detail.get("status", "") != "READY":
                return {
                    "guardrailId": guardrail_id,
                    "version": version,
                    "error": {
                        "statusCode": "409",
                        "message": f"Guardrail is not ready. Status: {guardrail_detail.get('status')}",
                    },
                }

            guardrail_data = BedrockGuardrailService._create_entity_data(
                user=user,
                setting=setting,
                guardrail_detail=guardrail_detail,
            )

            created_entity_id = BedrockGuardrailService._create_or_update_entity(
                guardrail_id=guardrail_id,
                guardrail_version=version,
                guardrail_data=guardrail_data,
                existing_entities_map=existing_entities_map,
            )

            return {
                "guardrailId": guardrail_id,
                "version": version,
                "aiRunId": created_entity_id,
            }
        except ClientError as e:
            error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            if error_code.strip().lower() == "resourcenotfoundexception":
                return {
                    "guardrailId": guardrail_input_id,
                    "version": guardrail_input_version,
                    "error": {
                        "statusCode": "404",
                        "message": (
                            f"Guardrail with ID {guardrail_input_id} was not found (AWS ResourceNotFoundException)"
                        ),
                    },
                }
            return {
                "guardrailId": guardrail_input_id,
                "version": guardrail_input_version,
                "error": {"statusCode": "500", "message": str(e)},
            }
        except Exception as e:
            return {
                "guardrailId": guardrail_input_id,
                "version": guardrail_input_version,
                "error": {"statusCode": "500", "message": str(e)},
            }

    @staticmethod
    def _create_entity_data(
        user: User,
        setting: SettingsBase,
        guardrail_detail: dict,
    ) -> dict:
        """
        Creates the data dictionary for a Bedrock guardrail, matching the Guardrail model structure.

        Args:
            user (User): The user creating the guardrail.
            setting (SettingsBase): The AWS settings associated with the guardrail.
            guardrail_detail (dict): The Bedrock guardrail details.

        Returns:
            dict: The data dictionary for the guardrail.
        """
        return {
            "project_name": setting.project_name,
            "description": guardrail_detail.get("description", ""),
            "created_by": CreatedByUser(id=user.id, username=user.username, name=user.name),
            "bedrock": {
                "bedrock_guardrail_id": guardrail_detail["guardrailId"],
                "bedrock_version": guardrail_detail["version"],
                "bedrock_name": guardrail_detail["name"],
                "bedrock_status": guardrail_detail["status"],
                "bedrock_created_at": guardrail_detail["createdAt"],
                "bedrock_updated_at": guardrail_detail.get("updatedAt"),
                "bedrock_aws_settings_id": str(setting.id),
            },
        }

    @staticmethod
    def _create_or_update_entity(
        guardrail_id: str,
        guardrail_version: str,
        guardrail_data: dict,
        existing_entities_map: dict,
    ):
        if f"{guardrail_id}-{guardrail_version}" in existing_entities_map:
            # Update the existing guardrail
            guardrail = existing_entities_map[f"{guardrail_id}-{guardrail_version}"]
            for key, value in guardrail_data.items():
                setattr(guardrail, key, value)
            guardrail.save(refresh=True)
            logger.info(f"Updated Guardrail for Bedrock guardrail: {guardrail_id}")
        else:
            # Ensure Application exists for the project
            project_name = guardrail_data.get("project_name")
            if project_name:
                ensure_application_exists(project_name)

            # Create a new guardrail
            guardrail = Guardrail(**guardrail_data)
            guardrail.save(refresh=True)
            existing_entities_map[guardrail_id] = guardrail
            logger.info(f"Created Guardrail for Bedrock guardrail: {guardrail_id}")

        return str(guardrail.id)

    @staticmethod
    def _bedrock_list_guardrails(
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
        guardrail_id: str | None = None,
        page: int = 0,
        per_page: int = 10,
        next_token: Optional[str] = None,
        max_retry_attempts: Optional[int] = None,
    ) -> tuple[List[dict], Optional[str]]:
        # Build API-specific parameters
        api_params = {}
        if guardrail_id:
            api_params["guardrailIdentifier"] = guardrail_id

        return call_bedrock_listing_api(
            service_name="bedrock",
            api_method_name="list_guardrails",
            response_key="guardrails",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
            page=page,
            per_page=per_page,
            next_token=next_token,
            max_retry_attempts=max_retry_attempts,
            **api_params,
        )

    @staticmethod
    def _bedrock_get_guardrail(
        guardrail_id: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
        guardrail_version: Optional[str] = None,
    ) -> dict:
        """
        Retrieve details for a specific AWS Bedrock Guardrail.

        Args:
            guardrail_id (str): The Guardrail Identifier.
            guardrail_version (str): The Guardrail Version.
            region (str): The AWS region for the request.
            access_key_id (str): The AWS access key ID.
            secret_access_key (str): The AWS secret access key.
            session_token: (Optional[str]): The AWS session token.

        Returns:
            dict: The guardrail details as a dictionary.
        """

        def _func(client):
            params = {"guardrailIdentifier": guardrail_id}
            if guardrail_version is not None:
                params["guardrailVersion"] = guardrail_version

            return client.get_guardrail(**params)

        client = get_aws_client_for_service(
            "bedrock",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

        return handle_aws_call(_func, client)

    @staticmethod
    def _bedrock_apply_guardrail(
        guardrail_identifier: str,
        guardrail_version: str,
        source: Literal["INPUT", "OUTPUT"],
        content: List[dict],
        output_scope: Literal["INTERVENTIONS", "FULL"],
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
    ) -> dict:
        """
        Calls the AWS Bedrock apply_guardrail API.

        Args:
            guardrail_identifier (str): The guardrail identifier.
            guardrail_version (str): The guardrail version.
            source (Literal["INPUT", "OUTPUT"]): 'INPUT' or 'OUTPUT'.
            content (List[dict]): List of content dicts to check.
            output_scope (Literal["INTERVENTIONS", "FULL"]): 'INTERVENTIONS' or 'FULL'.
            region (str): AWS region.
            access_key_id (str): AWS access key ID.
            secret_access_key (str): AWS secret access key.
            session_token: Optional[str]: AWS session token.

        Returns:
            dict: The response from AWS Bedrock.
        """

        def _func(client):
            response = client.apply_guardrail(
                guardrailIdentifier=guardrail_identifier,
                guardrailVersion=guardrail_version,
                source=source,
                content=content,
                outputScope=output_scope,
            )
            return response

        client = get_aws_client_for_service(
            "bedrock-runtime",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

        return handle_aws_call(_func, client)
