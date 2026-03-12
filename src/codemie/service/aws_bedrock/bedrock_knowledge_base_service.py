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
from typing import List, Optional
from botocore.exceptions import ClientError

from codemie.configs import logger
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.guardrail import GuardrailEntity
from codemie.rest_api.models.index import IndexInfo, IndexInfoType
from codemie.rest_api.models.settings import AWSCredentials, Settings, SettingsBase
from codemie.rest_api.models.vendor import ImportKnowledgeBase
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


class BedrockKnowledgeBaseService(BaseBedrockService):
    @staticmethod
    @aws_service_exception_handler("Bedrock knowledge bases")
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
                executor.submit(BedrockKnowledgeBaseService._fetch_main_entity_names_for_setting, setting): setting
                for setting in paged_settings
            }

            # Collect results as they complete
            for future in as_completed(future_to_setting):
                setting = future_to_setting[future]
                try:
                    kb_names = future.result()
                    if kb_names is not None:  # None indicates an error occurred
                        results["data"].append(
                            {
                                "setting_id": str(setting.id),
                                "setting_name": setting.alias,
                                "project": setting.project_name,
                                "entities": kb_names,
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
    @aws_service_exception_handler("Bedrock knowledge bases")
    def list_main_entities(
        user: User,
        setting_id: str,
        page: int,
        per_page: int,
        next_token: Optional[str] = None,
    ) -> tuple[List[dict], Optional[str]]:
        """
        Retrieve a paginated list of AWS Bedrock knowledge bases.

        Args:
            user (User): The user requesting the entities.
            setting_id (str): The identifier for the AWS settings to use.
            page (int): The page number (0-based) for pagination.
            per_page (int): The number of items per page.

        Returns:
            List: A list of knowledge base summaries.
        """
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        existing_entities = IndexInfo.get_by_bedrock_aws_settings_id(str(setting.id))
        existing_entities_map = {
            entity.bedrock.bedrock_knowledge_base_id: entity for entity in existing_entities if entity.bedrock
        }

        aws_creds = get_setting_aws_credentials(setting.id)

        all_knowledge_bases, return_next_token = BedrockKnowledgeBaseService._bedrock_list_knowledge_bases(
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
            page=page,
            per_page=per_page,
            next_token=next_token,
        )

        result = []
        for kb_info in all_knowledge_bases:
            status = "PREPARED" if kb_info.get("status") == "ACTIVE" else "NOT_PREPARED"

            kb_id = kb_info.get("knowledgeBaseId")

            data = {
                "id": kb_id,
                "name": kb_info.get("name"),
                "status": status,
                "description": kb_info.get("description"),
                "updatedAt": kb_info.get("updatedAt"),
            }

            if kb_id in existing_entities_map:
                data["aiRunId"] = existing_entities_map[kb_id].id

            result.append(data)

        return result, return_next_token

    @staticmethod
    @aws_service_exception_handler("Bedrock knowledge bases")
    def get_main_entity_detail(
        user: User,
        main_entity_id: str,
        setting_id: str,
    ) -> dict:
        setting: SettingsBase = get_setting_for_user(user, setting_id)

        existing_entities = IndexInfo.get_by_bedrock_aws_settings_id(str(setting.id))
        existing_entities_map = {
            entity.bedrock.bedrock_knowledge_base_id: entity for entity in existing_entities if entity.bedrock
        }

        aws_creds = get_setting_aws_credentials(setting.id)

        knowledge_base_detail = BedrockKnowledgeBaseService._bedrock_get_knowledge_base(
            knowledge_base_id=main_entity_id,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
        )

        kb_configuration = knowledge_base_detail.get("knowledgeBaseConfiguration", {})
        kb_type = kb_configuration.get("type")

        embedding_model_arn = kb_configuration.get("vectorKnowledgeBaseConfiguration", {}).get("embeddingModelArn")
        embedding_model = embedding_model_arn.split("/")[-1] if embedding_model_arn else None

        kendra_index_arn = kb_configuration.get("kendraKnowledgeBaseConfiguration", {}).get("kendraIndexArn")

        if kb_type not in ["VECTOR", "KENDRA"]:
            status = "NOT_PREPARED"
        else:
            status = "PREPARED" if knowledge_base_detail.get("status") == "ACTIVE" else "NOT_PREPARED"

        kb_dict = {
            "id": knowledge_base_detail.get("knowledgeBaseId"),
            "name": knowledge_base_detail.get("name"),
            "description": knowledge_base_detail.get("description"),
            "type": kb_type,
            "status": status,
            "embeddingModel": embedding_model,
            "kendraIndexArn": kendra_index_arn,
            "createdAt": knowledge_base_detail.get("createdAt"),
            "updatedAt": knowledge_base_detail.get("updatedAt"),
        }

        if main_entity_id in existing_entities_map:
            kb_dict["aiRunId"] = existing_entities_map[main_entity_id].id

        return kb_dict

    @staticmethod
    @aws_service_exception_handler("Bedrock knowledge bases")
    def list_importable_entities_for_main_entity(
        user: User,
        main_entity_id: str,
        setting_id: str,
        page: int,
        per_page: int,
        next_token: Optional[str] = None,
    ) -> tuple[List[dict], Optional[str]]:
        # For knowledge bases, the main entity is the importable entity
        # So we just return the detailed information about this specific knowledge base
        return [BedrockKnowledgeBaseService.get_main_entity_detail(user, main_entity_id, setting_id)], None

    @staticmethod
    @aws_service_exception_handler("Bedrock knowledge bases")
    def get_importable_entity_detail(
        user: User,
        main_entity_id: str,
        importable_entity_detail: str,
        setting_id: str,
    ):
        # For knowledge bases, we don't need the importable_entity_detail parameter
        # as the knowledge base itself is the importable entity
        setting: SettingsBase = get_setting_for_user(user, setting_id)
        aws_creds = get_setting_aws_credentials(setting.id)

        kb_detail = BedrockKnowledgeBaseService._bedrock_get_knowledge_base(
            knowledge_base_id=main_entity_id,
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
        )

        if not kb_detail:
            logger.warning(f"Failed to retrieve knowledge base information for {main_entity_id}")
            return {}

        return {
            "id": kb_detail.get("knowledgeBaseId"),
            "name": kb_detail.get("name"),
            "description": kb_detail.get("description"),
            "arn": kb_detail.get("knowledgeBaseArn"),
            "roleArn": kb_detail.get("roleArn"),
            "knowledgeBaseConfiguration": kb_detail.get("knowledgeBaseConfiguration"),
            "storageConfiguration": kb_detail.get("storageConfiguration"),
            "status": kb_detail.get("status"),
            "createdAt": kb_detail.get("createdAt"),
            "updatedAt": kb_detail.get("updatedAt"),
            "failureReasons": kb_detail.get("failureReasons"),
        }

    @staticmethod
    @aws_service_exception_handler("Bedrock knowledge bases")
    def import_entities(user: User, import_payload: dict[str, List[ImportKnowledgeBase]]):
        """
        Import Bedrock knowledge bases for the given user and payload.

        Args:
            user (User): The user performing the import.
            import_payload (dict): Mapping of setting_id to list of ImportKnowledgeBase.

        Returns:
            list: List of imported knowledge base info.
        """
        results = []

        for setting_id, kb_imports in import_payload.items():
            setting: SettingsBase = get_setting_for_user(user, setting_id)

            # Retrieve all existing entities for this settings
            existing_entities = IndexInfo.get_by_bedrock_aws_settings_id(str(setting.id))
            existing_entities_map = {
                entity.bedrock.bedrock_knowledge_base_id: entity for entity in existing_entities if entity.bedrock
            }

            aws_creds = get_setting_aws_credentials(setting.id)

            for kb_import in kb_imports:
                results.append(
                    BedrockKnowledgeBaseService._process_entity_import(
                        user=user,
                        setting=setting,
                        aws_creds=aws_creds,
                        existing_entities_map=existing_entities_map,
                        knowledge_base_id=kb_import.id,
                    )
                )

        return results

    @staticmethod
    def delete_entities(setting_id: str):
        """
        Delete all imported knowledge bases for a given setting_id.
        """
        existing_entities = IndexInfo.get_by_bedrock_aws_settings_id(setting_id)
        for entity in existing_entities:
            entity.delete()
            GuardrailService.remove_guardrail_assignments_for_entity(GuardrailEntity.KNOWLEDGEBASE, str(entity.id))

    @staticmethod
    def validate_remote_entity_exists_and_cleanup(entity: IndexInfo):
        if not entity.bedrock or not entity.bedrock.bedrock_knowledge_base_id:
            return None  # not a bedrock entity

        try:
            setting: SettingsBase = Settings.get_by_id(entity.bedrock.bedrock_aws_settings_id)  # type: ignore
            if not setting:
                raise ValueError(f"Setting with id {entity.bedrock.bedrock_aws_settings_id} not found")

            aws_creds = get_setting_aws_credentials(setting.id)

            BedrockKnowledgeBaseService._bedrock_get_knowledge_base(
                knowledge_base_id=entity.bedrock.bedrock_knowledge_base_id,
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
                GuardrailService.remove_guardrail_assignments_for_entity(GuardrailEntity.KNOWLEDGEBASE, str(entity.id))

                return entity.full_name
            else:
                # any other issue (like configuration) is just ignored at this point
                logger.error(f"Unexpected ClientError validating Bedrock knowledge base: {e}")
        except Exception as e:
            logger.error(f"Unexpected error validating Bedrock knowledge base: {e}")

    @staticmethod
    def invoke_knowledge_base(query: str, bedrock_index_info_id: str):
        bedrock_index: IndexInfo = IndexInfo.get_by_id(bedrock_index_info_id)  # type: ignore

        setting: SettingsBase = Settings.get_by_id(bedrock_index.setting_id or "")  # type: ignore
        if not setting:
            logger.error(f"Setting with ID {bedrock_index.setting_id} not found")
            raise ValueError("Missing setting. Check and fix integration and retry.")

        if not bedrock_index.created_by:
            logger.error(f"Bedrock index {bedrock_index.id} does not have a created_by user")
            raise ValueError("Missing created_by user. Check and fix integration and retry.")

        aws_creds = get_setting_aws_credentials(setting.id)
        if not aws_creds:
            raise ValueError("Missing AWS credentials. Check and fix integration and retry.")

        try:
            kb_id = bedrock_index.bedrock.bedrock_knowledge_base_id if bedrock_index.bedrock else None
            if not kb_id:
                logger.error(f"Bedrock index {bedrock_index.id} does not have a bedrock_knowledge_base_id")
                raise ValueError("Missing bedrock_knowledge_base_id. Check and fix integration and retry.")

            response = BedrockKnowledgeBaseService._bedrock_retrieve_knowledge_base(
                input_text=query,
                bedrock_knowledge_base_id=kb_id,
                region=aws_creds.region,
                access_key_id=aws_creds.access_key_id,
                secret_access_key=aws_creds.secret_access_key,
                session_token=aws_creds.session_token,
            )

            return response
        except ClientError as e:
            error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            if error_code.strip().lower() == "resourcenotfoundexception":
                logger.warning(f"Bedrock knowledge base not found on remote: {e}")

                bedrock_index.delete()  # if the resource was deleted in the meantime, delete the local record
                GuardrailService.remove_guardrail_assignments_for_entity(
                    GuardrailEntity.KNOWLEDGEBASE, str(bedrock_index.id)
                )
            else:
                logger.error(f"AWS ClientError listing Bedrock knowledge bases: {e}")

            raise
        except Exception as e:
            logger.error(f"Unexpected error listing Bedrock knowledge bases: {e}")
            raise

    @staticmethod
    def _fetch_main_entity_names_for_setting(setting) -> List[str] | None:
        """
        Fetch knowledge base names for a single setting.
        """
        aws_creds = get_setting_aws_credentials(setting.id)

        all_knowledge_bases, _ = BedrockKnowledgeBaseService._bedrock_list_knowledge_bases(
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            session_token=aws_creds.session_token,
            page=0,
            per_page=ALL_SETTINGS_OVERVIEW_ENTITY_COUNT,
            max_retry_attempts=1,  # only 1 attempt to avoid incorrect setting config timeouts
        )

        kb_names = []
        for kb_count, kb_info in enumerate(all_knowledge_bases):
            if kb_count >= ALL_SETTINGS_OVERVIEW_ENTITY_COUNT:
                break
            kb_names.append(kb_info["name"])

        return kb_names

    @staticmethod
    def _process_entity_import(
        user: User,
        setting: SettingsBase,
        aws_creds: AWSCredentials,
        existing_entities_map: dict,
        knowledge_base_id: str,
    ) -> dict:
        try:
            kb_detail = BedrockKnowledgeBaseService._bedrock_get_knowledge_base(
                knowledge_base_id=knowledge_base_id,
                region=aws_creds.region,
                access_key_id=aws_creds.access_key_id,
                secret_access_key=aws_creds.secret_access_key,
                session_token=aws_creds.session_token,
            )

            kb_id = kb_detail.get("knowledgeBaseId")
            if not kb_id:
                return {
                    "knowledgeBaseId": knowledge_base_id,
                    "error": {
                        "statusCode": "404",
                        "message": f"Knowledge base with ID {knowledge_base_id} was not found",
                    },
                }

            if kb_detail.get("status") != "ACTIVE":
                return {
                    "knowledgeBaseId": kb_id,
                    "error": {
                        "statusCode": "409",
                        "message": f"Knowledge base is not active. Status: {kb_detail.get('status')}",
                    },
                }

            kb_type = kb_detail.get("knowledgeBaseConfiguration", {}).get("type")

            # Only allow VECTOR and KENDRA types for now
            if kb_type not in ["VECTOR", "KENDRA"]:
                return {
                    "knowledgeBaseId": kb_id,
                    "error": {
                        "statusCode": "400",
                        "message": f"Only VECTOR and KENDRA type knowledge bases are supported. Found type: {kb_type}",
                    },
                }

            kb_configuration = kb_detail.get("knowledgeBaseConfiguration", {})
            model_arn = kb_configuration.get("vectorKnowledgeBaseConfiguration", {}).get("embeddingModelArn")
            kendra_index_arn = kb_configuration.get("kendraKnowledgeBaseConfiguration", {}).get("kendraIndexArn")

            index_data = BedrockKnowledgeBaseService._create_entity_data(
                user=user,
                setting=setting,
                kb_detail=kb_detail,
                kb_type=kb_type,
                model_arn=model_arn,
                kendra_index_arn=kendra_index_arn,
            )

            created_entity_id = BedrockKnowledgeBaseService._create_or_update_entity(
                knowledge_base_id=kb_id,
                index_data=index_data,
                existing_entities_map=existing_entities_map,
            )

            return {
                "knowledgeBaseId": kb_id,
                "aiRunId": created_entity_id,
            }
        except ClientError as e:
            error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            if error_code.strip().lower() == "resourcenotfoundexception":
                return {
                    "knowledgeBaseId": knowledge_base_id,
                    "error": {
                        "statusCode": "404",
                        "message": (
                            f"Knowledge base with ID {knowledge_base_id} was not found (AWS ResourceNotFoundException)"
                        ),
                    },
                }
            return {
                "knowledgeBaseId": knowledge_base_id,
                "error": {"statusCode": "500", "message": str(e)},
            }
        except Exception as e:
            return {
                "knowledgeBaseId": knowledge_base_id,
                "error": {"statusCode": "500", "message": str(e)},
            }

    @staticmethod
    def _create_entity_data(
        user: User,
        setting: SettingsBase,
        kb_detail: dict,
        kb_type: str,
        model_arn: Optional[str] = None,
        kendra_index_arn: Optional[str] = None,
    ) -> dict:
        """
        Creates the data dictionary for a Bedrock knowledge base index.

        Args:
            user (User): The user creating the index.
            setting (SettingsBase): The AWS settings associated with the index.
            kb_detail (dict): The Bedrock knowledge base details.
            kb_type (str): The type of the knowledge base.
            model_arn (Optional[str]): The ARN of the embedding model (for VECTOR type).
            kendra_index_arn (Optional[str]): The ARN of the Kendra index (for KENDRA type).

        Returns:
            dict: The data dictionary for the index.
        """
        return {
            "project_name": setting.project_name,
            "repo_name": f"{setting.id}-{kb_detail['name']}",
            "index_type": IndexInfoType.KB_BEDROCK.value,
            "description": kb_detail.get("description", ""),
            "bedrock": {
                "bedrock_knowledge_base_id": kb_detail["knowledgeBaseId"],
                "bedrock_name": kb_detail["name"],
                "bedrock_model_arn": model_arn,
                "bedrock_kendra_index_arn": kendra_index_arn,
                "bedrock_status": kb_detail["status"],
                "bedrock_type": kb_type,
                "bedrock_created_at": kb_detail["createdAt"],
                "bedrock_updated_at": kb_detail.get("updatedAt"),
                "bedrock_storage_type": kb_detail.get("storageConfiguration", {}).get("type"),
                "bedrock_aws_settings_id": str(setting.id),
            },
            "created_by": CreatedByUser(id=user.id, username=user.username, name=user.name),
            "setting_id": getattr(setting, "id", None),
            "completed": True,  # we are just considering these completed as codemie is concerned
        }

    @staticmethod
    def _create_or_update_entity(
        knowledge_base_id: str,
        index_data: dict,
        existing_entities_map: dict,
    ):
        """
        Creates or updates an IndexInfo object for a Bedrock knowledge base.

        Args:
            knowledge_base_id (str): The ID of the knowledge base.
            index_data (dict): The data for the index.
            existing_entities_map (dict): A map of existing indexes by knowledge base ID.
        """
        if knowledge_base_id in existing_entities_map:
            # Update the existing index
            index = existing_entities_map[knowledge_base_id]
            for key, value in index_data.items():
                setattr(index, key, value)
            index.save(refresh=True)
            logger.info(f"Updated IndexInfo for Bedrock knowledge base: {knowledge_base_id}")
        else:
            # Ensure Application exists for the project_name
            project_name = index_data.get("project_name")
            if project_name:
                ensure_application_exists(project_name)

            # Create a new index
            index = IndexInfo(**index_data)
            index.save(refresh=True)
            existing_entities_map[knowledge_base_id] = index
            logger.info(f"Created IndexInfo for Bedrock knowledge base: {knowledge_base_id}")

        return str(index.id)

    @staticmethod
    def _bedrock_list_knowledge_bases(
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
            api_method_name="list_knowledge_bases",
            response_key="knowledgeBaseSummaries",
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
    def _bedrock_get_knowledge_base(
        knowledge_base_id: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
    ) -> dict:
        """
        Retrieve details for a specific AWS Bedrock Knowledge Base.

        This method fetches the details for a given knowledge base by interacting with the AWS Bedrock service.

        Args:
            knowledge_base_id (str): The Knowledge Base ID.
            region (str): The AWS region for the request.
            access_key_id (str): The AWS access key ID.
            secret_access_key (str): The AWS secret access key.
            session_token (Optional[str]): The AWS session token, if applicable.

        Returns:
            dict: The knowledge base details as a dictionary.
        """

        def _func(client):
            response = client.get_knowledge_base(knowledgeBaseId=knowledge_base_id)

            return response.get("knowledgeBase", {})

        client = get_aws_client_for_service(
            "bedrock-agent",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

        return handle_aws_call(_func, client)

    @staticmethod
    def _bedrock_retrieve_knowledge_base(
        input_text: str,
        bedrock_knowledge_base_id: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
    ):
        """
        Retrieves relevant documents from a Bedrock knowledge base (no generation).

        Args:
            input_text (str): The query text to retrieve relevant documents for.
            bedrock_knowledge_base_id (str): The ID of the Bedrock knowledge base.
            region (Optional[str]): AWS region.
            access_key_id (Optional[str]): AWS access key ID.
            secret_access_key (Optional[str]): AWS secret access key.
            session_token (Optional[str]): AWS session token, if applicable.

        Returns:
            list: List of retrieved references/documents.
        """

        def _func(client):
            response = client.retrieve(knowledgeBaseId=bedrock_knowledge_base_id, retrievalQuery={'text': input_text})

            return response.get("retrievalResults", [])

        client = get_aws_client_for_service(
            "bedrock-agent-runtime",
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

        return handle_aws_call(_func, client)
