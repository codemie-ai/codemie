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

from datetime import datetime, UTC
from typing import Any, List, Optional, Dict, Type, TypeVar, Union
from urllib.parse import urlparse

from codemie_tools.access_management.keycloak.models import KeycloakConfig
from codemie_tools.cloud.aws.models import AWSConfig
from codemie_tools.cloud.azure.models import AzureConfig
from codemie_tools.cloud.gcp.models import GCPConfig
from codemie_tools.cloud.kubernetes.models import KubernetesConfig
from codemie_tools.code.models import SonarConfig
from codemie_tools.core.project_management.confluence.models import ConfluenceConfig
from codemie_tools.core.project_management.jira.models import JiraConfig
from codemie_tools.core.vcs.azure_devops_git.models import AzureDevOpsGitConfig
from codemie_tools.core.vcs.github.models import GithubConfig
from codemie_tools.core.vcs.gitlab.models import GitlabConfig
from codemie_tools.azure_devops.wiki.models import AzureDevOpsWikiConfig
from codemie_tools.azure_devops.work_item.models import AzureDevOpsWorkItemConfig
from codemie_tools.azure_devops.test_plan.models import AzureDevOpsTestPlanConfig
from codemie_tools.data_management.elastic.models import ElasticConfig
from codemie_tools.data_management.sql.models import SQLConfig
from codemie_tools.itsm.servicenow.models import ServiceNowConfig
from codemie_tools.notification.email.models import EmailToolConfig
from codemie_tools.notification.telegram.models import TelegramConfig
from codemie_tools.open_api.models import OpenApiConfig
from codemie_tools.qa.zephyr.models import ZephyrConfig
from codemie_tools.qa.zephyr_squad.models import ZephyrSquadConfig
from codemie_tools.qa.xray.models import XrayConfig
from codemie_tools.report_portal.models import ReportPortalConfig
from sqlalchemy.orm import attributes

from codemie.configs import logger
from codemie.core.models import ToolConfig
from codemie.core.utils import get_url_domain, hash_string
from codemie.rest_api.security.user import User
from codemie.rest_api.models.settings import (
    UserSetting,
    ProjectSetting,
    SettingRequest,
    CredentialTypes,
    Credentials,
    AWSCredentials,
    SettingType,
    CredentialValues,
    PluginCredentials,
    Settings,
    DialCredentials,
    FileSystemConfig,
    AzureDevOpsCredentials,
    SharePointCredentials,
    SonarCredentials,
    AbilitySetting,
    LiteLLMCredentials,
)
from codemie.rest_api.utils.default_applications import ensure_application_exists
from codemie.service.settings.base_settings import BaseSettingsService, SearchFields
from codemie.service.settings.settings_handler import build_settings_handlers

# Type variable for generic credential return types
T = TypeVar('T')


class SettingsService(BaseSettingsService):
    URL: str = "url"
    TOKEN: str = "token"
    NAME: str = "name"
    PROJECT: str = "project"
    REALM: str = "realm"
    CLIENT_ID: str = "client_id"
    CLIENT_SECRET: str = "client_secret"
    TENANT_ID: str = "tenant_id"
    ACTIVATE_COMMAND: str = "activate_command"
    ELASTIC_API_KEY_ID: str = "elastic_api_key_id"
    ELASTIC_API_KEY: str = "elastic_api_key"
    PLUGIN_KEY: str = "plugin_key"
    ROOT_DIRECTORY: str = "root_directory"
    USERNAME: str = "username"
    DIAL_API_KEY: str = "api_key"
    ORGANIZATION: str = "organization"
    SONAR_PROJECT_NAME: str = "sonar_project_name"
    DATABASE_NAME: str = "database_name"
    DATABASE_HOST: str = "host"
    DATABASE_PORT: str = "port"
    DATABASE_DIALECT: str = "dialect"
    DATABASE_INFLUXDB_BUCKET: str = "bucket"
    DATABASE_INFLUXDB_TOKEN: str = "token"
    DATABASE_INFLUXDB_VERIFY_SSL: str = "verify_ssl"
    DATABASE_INFLUXDB_ORG: str = "org"
    PASSWORD: str = "password"
    SECURE_HEADER_VALUE: str = "secure_header_value"
    WEBHOOK_ID: str = "webhook_id"
    ZEPHYR_SQUAD_ACCOUNT_ID: str = "account_id"
    ZEPHYR_SQUAD_ACCESS_KEY: str = "access_key"
    ZEPHYR_SQUAD_SECRET_KEY: str = "secret_key"
    XRAY_BASE_URL: str = "base_url"
    XRAY_CLIENT_ID: str = "client_id"
    XRAY_CLIENT_SECRET: str = "client_secret"
    XRAY_LIMIT: str = "limit"
    API_KEY = "api_key"
    ENV_VARS = "env_vars"
    INTERNAL_PREFIX = "__internal__"
    IDE_PREFIX = INTERNAL_PREFIX + "IDE_"
    IDE_PROJECT_NAME = IDE_PREFIX + "virtual"
    AUTH_VALUE = "auth_value"
    IS_CLOUD = "is_cloud"

    LIST_OF_SENSITIVE_FIELDS: List[str] = [
        TOKEN,
        "kubernetes_token",
        "aws_secret_access_key",
        "aws_access_key_id",
        "aws_session_token",
        "gcp_api_key",
        "client_secret",
        "azure_client_secret",
        ELASTIC_API_KEY_ID,
        ELASTIC_API_KEY,
        DIAL_API_KEY,
        PLUGIN_KEY,
        "smtp_password",
        "oauth_client_secret",
        "openapi_api_key",
        PASSWORD,
        SECURE_HEADER_VALUE,
        ZEPHYR_SQUAD_ACCESS_KEY,
        ZEPHYR_SQUAD_SECRET_KEY,
        ENV_VARS,
        AUTH_VALUE,
        "private_key",  # GitHub App private key
        "access_token",  # SharePoint OAuth delegated token
        "refresh_token",  # SharePoint OAuth refresh token
    ]

    # Define field mappings for each credential type
    ELASTIC_FIELDS = {URL: "url", ELASTIC_API_KEY_ID: "api_key_id", ELASTIC_API_KEY: "api_key"}
    PLUGIN_FIELDS = {PLUGIN_KEY: "plugin_key"}
    FILE_SYSTEM_FIELDS = {ROOT_DIRECTORY: "root_directory", ACTIVATE_COMMAND: "activate_command"}
    SONAR_FIELDS = {URL: "url", SONAR_PROJECT_NAME: "sonar_project_name", TOKEN: "token"}
    SQL_FIELDS = {
        URL: "host",
        DATABASE_PORT: "port",
        DATABASE_NAME: "database_name",
        USERNAME: "username",
        PASSWORD: "password",
        DATABASE_DIALECT: "dialect",
        DATABASE_INFLUXDB_TOKEN: "token",
        DATABASE_INFLUXDB_ORG: "org",
        DATABASE_INFLUXDB_BUCKET: "bucket",
        DATABASE_INFLUXDB_VERIFY_SSL: "verify_ssl",
    }
    ZEPHYR_FIELDS = {URL: "url", TOKEN: "token"}
    ZEPHYR_SQUAD_FIELDS = {
        ZEPHYR_SQUAD_ACCOUNT_ID: "account_id",
        ZEPHYR_SQUAD_ACCESS_KEY: "access_key",
        ZEPHYR_SQUAD_SECRET_KEY: "secret_key",
    }
    XRAY_FIELDS = {
        XRAY_BASE_URL: "base_url",
        XRAY_CLIENT_ID: "client_id",
        XRAY_CLIENT_SECRET: "client_secret",
        XRAY_LIMIT: "limit",
    }
    AZURE_DEVOPS_FIELDS = {URL: "base_url", PROJECT: "project", ORGANIZATION: "organization", TOKEN: "access_token"}
    SHAREPOINT_FIELDS = {
        URL: "url",
        TENANT_ID: "tenant_id",
        CLIENT_ID: "client_id",
        CLIENT_SECRET: "client_secret",
        "auth_type": "auth_type",
        "access_token": "access_token",
        "refresh_token": "refresh_token",
        "expires_at": "expires_at",
        USERNAME: "username",
    }
    DIAL_FIELDS = {"api_version": "api_version", "api_key": "api_key", "url": "url"}
    LITELLM_FIELDS = {"api_key": "api_key", "url": "url"}
    JIRA_FIELDS = {URL: "url", TOKEN: "token", USERNAME: "username", IS_CLOUD: "cloud"}
    GIT_FIELDS = {
        URL: "url",
        TOKEN: "token",
        NAME: "token_name",
        "auth_type": "auth_type",
        "app_id": "app_id",
        "private_key": "private_key",
        "installation_id": "installation_id",
    }
    CONFLUENCE_FIELDS = {URL: "url", TOKEN: "token", USERNAME: "username", IS_CLOUD: "cloud"}

    __CREDENTIAL_CONFIG_TO_TYPE = {
        JiraConfig: CredentialTypes.JIRA,
        ConfluenceConfig: CredentialTypes.CONFLUENCE,
        KubernetesConfig: CredentialTypes.KUBERNETES,
        AWSConfig: CredentialTypes.AWS,
        AWSCredentials: CredentialTypes.AWS,
        GCPConfig: CredentialTypes.GCP,
        AzureConfig: CredentialTypes.AZURE,
        AzureDevOpsGitConfig: CredentialTypes.AZURE_DEVOPS,
        AzureDevOpsWikiConfig: CredentialTypes.AZURE_DEVOPS,
        AzureDevOpsWorkItemConfig: CredentialTypes.AZURE_DEVOPS,
        AzureDevOpsTestPlanConfig: CredentialTypes.AZURE_DEVOPS,
        GithubConfig: CredentialTypes.GIT,
        GitlabConfig: CredentialTypes.GIT,
        KeycloakConfig: CredentialTypes.KEYCLOAK,
        ServiceNowConfig: CredentialTypes.SERVICENOW,
        Credentials: CredentialTypes.GIT,
        TelegramConfig: CredentialTypes.TELEGRAM,
        EmailToolConfig: CredentialTypes.EMAIL,
        OpenApiConfig: CredentialTypes.OPEN_API,
        ReportPortalConfig: CredentialTypes.REPORT_PORTAL,
        ZephyrConfig: CredentialTypes.ZEPHYR_SCALE,
        ZephyrSquadConfig: CredentialTypes.ZEPHYR_SQUAD,
        XrayConfig: CredentialTypes.XRAY,
        ElasticConfig: CredentialTypes.ELASTIC,
        SQLConfig: CredentialTypes.SQL,
        SonarConfig: CredentialTypes.SONAR,
    }

    @classmethod
    def get_all_settings(
        cls,
        settings_type: Optional[SettingType] = None,
        credential_type: Optional[CredentialTypes] = None,
    ):
        settings = Settings.get_all(setting_type=settings_type, credential_type=credential_type)

        return [
            cls.hide_sensitive_fields(
                data=setting, force_all=setting.credential_type == CredentialTypes.ENVIRONMENT_VARS
            )
            for setting in settings
        ]

    @classmethod
    def get_settings(
        cls,
        user_id: Optional[str] = None,
        project_names: Optional[str] | Optional[List[str]] = None,
        settings_type: Optional[SettingType] = None,
        credential_type: Optional[CredentialTypes] = None,
    ):
        if settings_type == SettingType.PROJECT:
            settings = Settings.get_by_project_names(project_names, credential_type=credential_type)
        else:
            settings = Settings.get_by_user_id(user_id, credential_type=credential_type)

        settings = [s for s in settings if not (s.alias and s.alias.startswith(cls.INTERNAL_PREFIX))]

        return [
            cls.hide_sensitive_fields(
                data=setting, force_all=setting.credential_type == CredentialTypes.ENVIRONMENT_VARS
            )
            for setting in settings
        ]

    @classmethod
    def get_setting_ability(
        cls,
        credential_id: str,
        settings_type: SettingType = SettingType.USER,
    ) -> AbilitySetting:
        setting = Settings.get_by_id(credential_id)
        if not setting:
            raise ValueError("Not found setting")

        setting = cls.hide_sensitive_fields(
            data=setting, force_all=setting.credential_type == CredentialTypes.ENVIRONMENT_VARS
        )

        return ProjectSetting(setting) if settings_type == SettingType.PROJECT else UserSetting(setting)

    @classmethod
    def create_project_credentials_if_missing(
        cls,
        project_name: str,
        integration_alias: str,
        credential_type: CredentialTypes,
        credential_values: List[CredentialValues],
    ):
        """
        Pre-create missing settings (integrations) on a project level
        """
        existing_settings = cls.get_settings(
            project_names=[project_name], settings_type=SettingType.PROJECT, credential_type=credential_type
        )
        if any(s.alias == integration_alias for s in existing_settings):
            return

        sr = SettingRequest(
            project_name=project_name,
            alias=integration_alias,
            credential_type=credential_type,
            credential_values=credential_values,
        )
        cls.create_setting("system", sr, SettingType.PROJECT)

    @classmethod
    def create_setting(
        cls,
        user_id: str,
        request: SettingRequest,
        settings_type: Optional[SettingType] = None,
        user: Optional[User] = None,
    ):
        Settings.check_alias_unique(
            project_name=request.project_name, alias=request.alias, user_id=user_id, setting_type=settings_type
        )

        cls.check_webhook_unique(request)

        prepared_creds = cls._prepare_cred_values(request.credential_type, request.credential_values)

        # --- Set setting_hash for PLUGIN ---
        setting_hash = None
        if request.credential_type == CredentialTypes.PLUGIN:
            plugin_key_value = next((cred.value for cred in prepared_creds if cred.key == cls.PLUGIN_KEY), None)
            if plugin_key_value:
                setting_hash = hash_string(str(plugin_key_value))
        # -----------------------------------

        encrypted_creds = cls._encrypt_fields(
            prepared_creds, force_all=(request.credential_type == CredentialTypes.ENVIRONMENT_VARS)
        )

        if not settings_type:
            settings_type = SettingType.USER

        # Ensure Application exists for the project
        if request.project_name:
            ensure_application_exists(request.project_name)

        # Prepare created_by field from user object
        created_by = None
        if user:
            from codemie.core.models import CreatedByUser

            created_by = CreatedByUser(
                id=user.id,
                username=user.username,
                name=user.name,
            )

        new_user_setting = Settings(
            project_name=request.project_name,
            alias=request.alias,
            credential_type=request.credential_type,
            credential_values=encrypted_creds,
            user_id=user_id,
            created_by=created_by,
            setting_type=settings_type,
            is_global=request.is_global,
            setting_hash=setting_hash,  # Pass the hash here
        )

        return new_user_setting.save()

    @classmethod
    def update_settings(
        cls,
        credential_id: str,
        request: SettingRequest,
        settings_type: Optional[SettingType] = None,
        user_id: Optional[str] = None,
    ):
        Settings.check_alias_unique(
            project_name=request.project_name,
            alias=request.alias,
            setting_id=credential_id,
            user_id=user_id,
            setting_type=settings_type,
        )

        cls.check_webhook_unique(request=request, setting_id=credential_id)

        user_setting = Settings.get_by_id(id_=credential_id)

        prepared_creds = cls._prepare_cred_values(request.credential_type, request.credential_values)

        # Remove credentials that are no longer in the prepared credentials
        prepared_cred_keys = [cred.key for cred in prepared_creds]
        user_setting.credential_values = [
            cred for cred in user_setting.credential_values if cred.key in prepared_cred_keys
        ]

        # Create a dictionary for existing credentials to quickly find and update keys
        existing_creds_dict = {cred.key: cred for cred in user_setting.credential_values}

        force_all = request.credential_type == CredentialTypes.ENVIRONMENT_VARS
        # --- Set setting_hash for PLUGIN ---
        if request.credential_type == CredentialTypes.PLUGIN:
            plugin_key_value = next((cred.value for cred in prepared_creds if cred.key == cls.PLUGIN_KEY), None)
            if plugin_key_value != cls.MASKED_VALUE:
                user_setting.setting_hash = hash_string(str(plugin_key_value))
        # -----------------------------------
        cls._handle_new_creds(existing_creds_dict, force_all, prepared_creds, user_setting)
        if settings_type == SettingType.PROJECT:
            user_setting.setting_type = SettingType.PROJECT
        else:
            user_setting.setting_type = SettingType.USER

        user_setting.alias = request.alias
        user_setting.is_global = request.is_global
        user_setting.update_date = datetime.now(UTC)
        user_setting.update()

    @classmethod
    def _handle_new_creds(cls, existing_creds_dict, force_all, prepared_creds, user_setting):
        for new_cred in prepared_creds:
            if new_cred.key in existing_creds_dict:
                if new_cred.value != cls.MASKED_VALUE:
                    existing_cred = existing_creds_dict[new_cred.key]
                    existing_cred.value = cls._encrypt_fields([new_cred], force_all=force_all)[0].value

                    # SQLModel tracks list structure changes but not modifications to existing objects within the list.
                    # When changing object properties (not references), we need to explicitly flag the change.
                    attributes.flag_modified(user_setting, 'credential_values')
            else:
                # Add new credentials that don't exist in the current settings
                if new_cred.value != cls.MASKED_VALUE:
                    encrypted_cred = cls._encrypt_fields([new_cred], force_all=force_all)[0]
                    user_setting.credential_values.append(encrypted_cred)

    @classmethod
    def _prepare_cred_values(cls, cred_type: CredentialTypes, cred_values: List[CredentialValues]):
        for cred_pair in cred_values:
            if (
                cred_pair.key == cls.URL
                and cred_type in (CredentialTypes.GIT, CredentialTypes.JIRA)
                and not cred_pair.value.endswith(".git")
            ):
                base_url = get_url_domain(cred_pair.value)
                cred_pair.value = base_url
        return cred_values

    @classmethod
    def _convert_to_appropriate_type(cls, value, field_type):
        """
        Convert a value to the appropriate type based on the field's type annotation.
        Handles typing.Optional types by extracting the inner type.

        Args:
            value: The value to convert
            field_type: The expected type for the field

        Returns:
            The converted value
        """

        # Check if field_type is an Optional type (from typing module)
        if hasattr(field_type, "__origin__") and field_type.__origin__ is Union:
            # Extract the actual type from Optional (which is Union[type, NoneType])
            types = field_type.__args__
            # Filter out NoneType
            actual_types = [t for t in types if t is not type(None)]
            if actual_types:
                # Use the first non-None type
                field_type = actual_types[0]

        # Handle boolean conversion
        if field_type is bool:
            if isinstance(value, str):
                return value.lower() in ('true', 'yes', '1', 'y')
            else:
                return bool(value)
        return value

    @classmethod
    def _extract_credential_value(cls, creds_source, key, attr, credential_class=None):
        """
        Extract a credential value from a source and convert it to the appropriate type.
        Handles typing.Optional fields by correctly processing the inner type.

        Args:
            creds_source: The source of credentials (settings object or dictionary)
            key: The key to look up in the source
            attr: The attribute name in the result object
            credential_class: Optional class to determine the expected type

        Returns:
            The extracted and converted value
        """
        # Get the value from the source
        if isinstance(creds_source, dict):
            value = creds_source.get(key, "")
        else:  # Assume it's a settings object
            value = creds_source.credential(key) if creds_source and creds_source.credential(key) else ""

        # Convert to appropriate type if credential_class is provided
        if credential_class and hasattr(credential_class, '__annotations__'):
            field_type = credential_class.__annotations__.get(attr, str)
            value = cls._convert_to_appropriate_type(value, field_type)

        return value

    @classmethod
    def _build_credential_result(cls, creds_source, required_fields, credential_class=None):
        """
        Build a credential result object from a source using the required fields.
        Properly handles Optional fields by only including non-None values in the result.

        Args:
            creds_source: The source of credentials (settings object or dictionary)
            required_fields: Dictionary mapping credential keys to attribute names
            credential_class: Optional class to instantiate

        Returns:
            An instance of credential_class or a dictionary with the extracted values
        """
        result = {}
        for key, attr in required_fields.items():
            value = cls._extract_credential_value(creds_source, key, attr, credential_class)
            # Only include non-None values in the result
            # This allows credential_class to use its default values for Optional fields
            if value is not None:
                result[attr] = value

        # Return as appropriate type
        if credential_class:
            return credential_class(**result)
        return result

    @classmethod
    def _handle_missing_config(
        cls,
        config_class: Type[T],
        user_id: str,
        credential_type: CredentialTypes,
    ) -> Optional[T]:
        """
        Handle missing configuration for specific tools.
        """
        from codemie.configs import config

        # Special handling for Elasticsearch: allow admins to use default config
        if credential_type == CredentialTypes.ELASTIC:
            if config.ELASTIC_URL:
                logger.info(f"Admin user '{user_id}': Using default Elasticsearch URL (no stored credentials found)")
                return ElasticConfig(url=config.ELASTIC_URL)
            else:
                logger.warning(
                    f"Admin user '{user_id}': Cannot provide default Elasticsearch config - "
                    f"config.ELASTIC_URL is not configured"
                )

        return None

    @classmethod
    def get_config(
        cls,
        config_class: Type[T],
        user_id: str = None,
        project_name: str = None,
        assistant_id: Optional[str] = None,
        integration_id: Optional[str] = None,
        tool_config: Optional[ToolConfig] = None,
        is_admin: bool = False,
        **kwargs,
    ):
        credential_type = cls.__CREDENTIAL_CONFIG_TO_TYPE.get(config_class)
        if not credential_type:
            raise ValueError(
                f"ConfigClass is not supported. "
                f"Class: {config_class}, "
                f"Supported types: {list(cls.__CREDENTIAL_CONFIG_TO_TYPE.keys())}"
            )
        # Use tool_config if provided
        if tool_config:
            if tool_config.tool_creds:
                return config_class(**tool_config.tool_creds)
            elif tool_config.integration_id:
                # If integration_id is provided, use it to look up credentials
                integration_id = tool_config.integration_id

        def _get_config(repo_link: str = None) -> T:
            # Otherwise retrieve from settings
            search_fields_dict = {
                SearchFields.CREDENTIAL_TYPE: credential_type,
                SearchFields.PROJECT_NAME: project_name,
            }
            if user_id:
                search_fields_dict[SearchFields.USER_ID] = user_id
            else:  # If user_id is not provided, means getting project setting type
                search_fields_dict[SearchFields.SETTING_TYPE] = SettingType.PROJECT.value

            if repo_link:  # Handle git credentials
                search_fields_dict[SearchFields.CREDENTIAL_VALUES_KEY] = cls.URL
                search_fields_dict[SearchFields.CREDENTIAL_VALUES_VALUE] = repo_link
                logger.debug(f"Retrieve git creds for {repo_link}. Search fields: {search_fields_dict}")
            return cls.retrieve_setting(search_fields_dict, assistant_id, integration_id)

        setting = _get_config(**kwargs)
        if not setting:
            if is_admin:
                return cls._handle_missing_config(
                    config_class=config_class,
                    user_id=user_id,
                    credential_type=credential_type,
                )
            # Handle git creds by root url match if repo_link is passed
            if (repo_base_url := kwargs.get("repo_link")) and repo_base_url.count("/") > 2:
                repo_base_url = get_url_domain(repo_base_url)
                setting = _get_config(repo_link=repo_base_url)
                if not setting:
                    return None
            else:
                return None
        return config_class(**setting.normalize_values())

    @classmethod
    def get_credentials(
        cls,
        credential_type: CredentialTypes,
        user_id: str = None,
        project_name: str = None,
        assistant_id: Optional[str] = None,
        integration_id: Optional[str] = None,
        tool_config: Optional[ToolConfig] = None,
        required_fields: Dict[str, str] = None,
        credential_class: Optional[Type[T]] = None,
        special_handling: Optional[callable] = None,
    ) -> Union[Dict[str, Any], T]:
        """
        Generic method to retrieve credentials of any type.

        Args:
            credential_type: Type of credentials to retrieve
            user_id: User ID
            project_name: Project name
            assistant_id: Optional assistant ID
            integration_id: Optional setting ID
            tool_config: Optional tool configuration
            required_fields: Dictionary mapping credential keys to attribute names in the result object
            credential_class: Class to instantiate with the retrieved credentials
            special_handling: Optional function for special handling of credentials

        Returns:
            An instance of credential_class with the retrieved credentials, or a dictionary if no class is provided
        """
        # Use tool_config if provided
        if tool_config:
            if tool_config.tool_creds:
                # If direct credentials are provided in tool_creds, use them
                return cls._build_credential_result(tool_config.tool_creds, required_fields, credential_class)
            elif tool_config.integration_id:
                # If integration_id is provided, use it to look up credentials
                integration_id = tool_config.integration_id

        # Otherwise retrieve from settings
        search_fields = {
            SearchFields.CREDENTIAL_TYPE: credential_type,
            SearchFields.PROJECT_NAME: project_name,
        }
        if user_id:
            search_fields[SearchFields.USER_ID] = user_id
        else:  # If user_id is not provided, means getting project setting type
            search_fields[SearchFields.SETTING_TYPE] = SettingType.PROJECT.value

        setting = cls.retrieve_setting(search_fields, assistant_id, integration_id)

        if not setting:
            return None
        # Apply special handling if provided
        if special_handling and setting:
            return special_handling(setting, required_fields, credential_class)

        # Extract credentials using the helper method
        return cls._build_credential_result(setting, required_fields, credential_class)

    @classmethod
    def get_dial_creds(cls, project_name: str) -> Optional[DialCredentials]:
        """
        Retrieves DIAL credentials for a project.

        Args:
            project_name: The name of the project
            user_id: The user ID

        Returns:
            LiteLLMCredentials object if credentials exist, None otherwise
        """
        # We use a dummy user_id here since DIAL credentials are project-level settings
        return cls.get_credentials(
            credential_type=CredentialTypes.DIAL,
            project_name=project_name,
            required_fields=cls.DIAL_FIELDS,
            credential_class=DialCredentials,
        )

    @classmethod
    def get_litellm_creds(cls, project_name: str, user_id: str) -> Optional[LiteLLMCredentials]:
        """
        Retrieves LiteLLM credentials for a user/project.

        Args:
            project_name: The name of the project

        Returns:
            LiteLLMCredentials object if credentials exist, None otherwise
        """
        return cls.get_credentials(
            credential_type=CredentialTypes.LITE_LLM,
            project_name=project_name,
            user_id=user_id,
            required_fields=cls.LITELLM_FIELDS,
            credential_class=LiteLLMCredentials,
        )

    @classmethod
    def _extract_api_keys_from_settings(
        cls,
        settings: list[Settings],
        seen_ids: set[str],
    ) -> list[str]:
        """
        Extract API keys from settings with deduplication.

        Args:
            settings: List of Settings objects to process
            seen_ids: Set of already processed setting IDs (modified in-place)

        Returns:
            List of decrypted API keys
        """

        api_keys = []

        for setting in settings:
            if setting.id in seen_ids:
                continue
            seen_ids.add(setting.id)

            # Decrypt and extract credentials
            cls._decrypt_credentials(setting)
            cred = cls._build_credential_result(setting, cls.LITELLM_FIELDS, LiteLLMCredentials)

            if cred and cred.api_key:
                api_keys.append(cred.api_key)
            else:
                logger.warning(f"No API key found in setting {setting.id}")

        return api_keys

    @classmethod
    def get_user_litellm_settings_with_metadata(cls, user_id: str, project_names: list[str]) -> dict[str, list[dict]]:
        """
        Get all LiteLLM settings with metadata for a user, grouped by type.

        Retrieves LiteLLM credentials from both USER and PROJECT settings
        and returns metadata including api_key, alias, and project_name.

        Args:
            user_id: User ID
            project_names: List of all projects user has access to

        Returns:
            Dictionary with settings metadata grouped by type:
            {
                "user_keys": [
                    {"api_key": "sk-...", "alias": "my-key", "project_name": "ProjectA"},
                    ...
                ],
                "project_keys": [...]
            }
        """
        logger.debug(f"User {user_id} requesting litellm settings with metadata from Settings")

        seen_ids = set()

        def extract_settings_metadata(settings: list[Settings], seen: set[str]) -> list[dict]:
            """Extract api_key, alias, and project_name from settings."""
            result = []
            for setting in settings:
                if setting.id in seen:
                    continue
                seen.add(setting.id)

                # Decrypt and extract credentials
                cls._decrypt_credentials(setting)
                cred = cls._build_credential_result(setting, cls.LITELLM_FIELDS, LiteLLMCredentials)

                if cred and cred.api_key:
                    result.append(
                        {
                            "api_key": cred.api_key,
                            "alias": setting.alias,
                            "project_name": setting.project_name,
                        }
                    )
                else:
                    logger.warning(f"No API key found in setting {setting.id}")

            return result

        # 1. Get USER-scoped settings
        user_settings = Settings.get_by_user_id(user_id, CredentialTypes.LITE_LLM)
        user_keys_metadata = extract_settings_metadata(user_settings, seen_ids)

        # 2. Get PROJECT-scoped settings
        project_settings = Settings.get_by_project_names(project_names, CredentialTypes.LITE_LLM)
        project_keys_metadata = extract_settings_metadata(project_settings, seen_ids)

        return {
            "user_keys": user_keys_metadata,
            "project_keys": project_keys_metadata,
        }

    @classmethod
    def get_user_litellm_api_keys(cls, user_id: str, project_names: list[str]) -> dict[str, list[str]]:
        """
        Get all LiteLLM API keys for a user, grouped by type.

        Retrieves LiteLLM credentials from both USER and PROJECT settings
        across all projects the user has access to, and extracts the API keys
        grouped by their setting type.

        Args:
            user_id: User ID
            project_names: List of all projects user has access to

        Returns:
            Dictionary with keys grouped by type:
            {
                "user_keys": ["key1", "key2", ...],
                "project_keys": ["key3", "key4", ...]
            }
        """
        metadata = cls.get_user_litellm_settings_with_metadata(user_id, project_names)
        return {
            "user_keys": [s["api_key"] for s in metadata["user_keys"]],
            "project_keys": [s["api_key"] for s in metadata["project_keys"]],
        }

    @classmethod
    def get_ide_setting_alias(cls, ide_installation_id: str):
        return f'{cls.IDE_PREFIX}{ide_installation_id}'

    @classmethod
    def get_ide_settings(cls, user_id: str, ide_installation_id: str):
        alias = cls.get_ide_setting_alias(ide_installation_id)
        search_fields = {
            SearchFields.SETTING_TYPE: SettingType.USER.value,
            SearchFields.USER_ID: user_id,
            SearchFields.CREDENTIAL_TYPE: CredentialTypes.PLUGIN,
            SearchFields.PROJECT_NAME: cls.IDE_PROJECT_NAME,
            SearchFields.ALIAS: alias,
        }
        setting = cls.retrieve_setting(search_fields)
        if (
            setting
            and setting.alias == alias
            and setting.project_name == cls.IDE_PROJECT_NAME
            and setting.user_id == user_id
        ):
            return setting

        return None

    @classmethod
    def upsert_ide_settings(cls, user_id: str, ide_installation_id: str, plugin_key: str):
        plugin_setting = cls.get_ide_settings(user_id=user_id, ide_installation_id=ide_installation_id)
        credential_values = cls._encrypt_fields(
            [
                CredentialValues(key=SettingsService.PLUGIN_KEY, value=plugin_key),
                CredentialValues(key="url", value="AutoGenerated"),
            ]
        )

        if plugin_setting:
            plugin_setting.credential_values = credential_values
        else:
            plugin_setting = Settings(
                project_name=cls.IDE_PROJECT_NAME,
                alias=cls.get_ide_setting_alias(ide_installation_id=ide_installation_id),
                credential_type=CredentialTypes.PLUGIN,
                credential_values=credential_values,
                user_id=user_id,
                setting_type=SettingType.USER.value,
            )
        plugin_setting.setting_hash = hash_string(plugin_key)

        plugin_setting.save(refresh=True)

    @classmethod
    def get_jira_creds(
        cls,
        user_id: str,
        project_name: str = None,
        assistant_id: Optional[str] = None,
        setting_id: Optional[str] = None,
        tool_config: Optional[ToolConfig] = None,
    ) -> JiraConfig:
        return cls.get_config(
            config_class=JiraConfig,
            user_id=user_id,
            project_name=project_name,
            assistant_id=assistant_id,
            integration_id=setting_id,
            tool_config=tool_config,
        )

    @classmethod
    def get_confluence_creds(
        cls,
        user_id: str,
        project_name: str,
        assistant_id: Optional[str] = None,
        setting_id: Optional[str] = None,
        tool_config: Optional[ToolConfig] = None,
    ) -> ConfluenceConfig:
        return cls.get_config(
            config_class=ConfluenceConfig,
            user_id=user_id,
            project_name=project_name,
            assistant_id=assistant_id,
            integration_id=setting_id,
            tool_config=tool_config,
        )

    @classmethod
    def get_git_creds(
        cls,
        user_id: str,
        project_name: str,
        repo_link: Optional[str],
        assistant_id: Optional[str] = None,
        setting_id: Optional[str] = None,
        tool_config: Optional[ToolConfig] = None,
    ) -> Credentials:
        config = cls.get_config(
            config_class=Credentials,
            user_id=user_id,
            project_name=project_name,
            assistant_id=assistant_id,
            integration_id=setting_id,
            tool_config=tool_config,
            repo_link=repo_link,
        )
        if not config:
            config = Credentials(url="", token="", token_name="", auth_type="pat")
        return config

    @classmethod
    def get_kubernetes_creds(
        cls,
        user_id: str,
        project_name: str = None,
        assistant_id: Optional[str] = None,
        tool_config: Optional[ToolConfig] = None,
    ) -> Credentials:
        return cls.get_credentials(
            credential_type=CredentialTypes.KUBERNETES,
            user_id=user_id,
            project_name=project_name,
            assistant_id=assistant_id,
            tool_config=tool_config,
            required_fields=cls.KUBERNETES_FIELDS,
            credential_class=Credentials,
        )

    @classmethod
    def get_azure_devops_creds(
        cls,
        user_id: str,
        project_name: str = None,
        assistant_id: Optional[str] = None,
        creds: Optional[Credentials] = None,
        indexed_repo: Optional[Any] = None,
        tool_config: Optional[ToolConfig] = None,
    ) -> AzureDevOpsCredentials:
        """
        Retrieve the Azure DevOps credentials for a given user and project.
        This method is designed to handle both standard and git-specific integrations.
        For git tools, additional parameters `creds` and `indexed_repo` are used to gather
        Git integration credentials for Azure DevOps as the retrieve_setting returns setting object with empty values.

        Parameters:
            user_id (str): The ID of the user whose credentials are being retrieved.
            project_name (str, optional): The name of the project. Defaults to None.
            assistant_id (str, optional): The ID of the assistant. Defaults to None.
            creds (Credentials, optional):
              The credentials object containing default values for git integration. Defaults to None.
            indexed_repo (Any, optional):
              The indexed repository object, used to extract project and organization names for git tools integration.
              Defaults to None.

        Returns:
            AzureDevOpsCredentials: An object containing the Azure DevOps credentials including
            base_url, project, organization, and access_token.

        Notes:
            - For standard integrations, `user_id`, `project_name`, and `assistant_id` are used to retrieve
             the Azure DevOps credentials from the settings.
            - For git tools integration, if the retrieved setting has empty values, `creds` and `indexed_repo`
             are used to fill in the missing information.
            - The `retrieve_setting` object is expected to contain real Azure DevOps credentials values for standard
              integrations.
        """

        def extract_project_and_org_names(indexed_repo):
            if not indexed_repo:
                return "", ""
            parsed_url = urlparse(indexed_repo.link)
            path_parts = parsed_url.path.strip('/').split('/')
            return (path_parts[1] if len(path_parts) > 1 else "", path_parts[0] if len(path_parts) > 0 else "")

        search_fields = {
            SearchFields.USER_ID: user_id,
            SearchFields.CREDENTIAL_TYPE: CredentialTypes.AZURE_DEVOPS,
            SearchFields.PROJECT_NAME: project_name,
        }

        project_name, organization_name = extract_project_and_org_names(indexed_repo)
        setting = cls.retrieve_setting(search_fields, assistant_id)

        def get_credential(attr: str, default: str) -> str:
            return setting.credential(attr) if setting and setting.credential(attr) else default

        base_url = get_credential(cls.URL, creds.url if creds else "")
        project = get_credential(cls.PROJECT, project_name)
        organization = get_credential(cls.ORGANIZATION, organization_name)
        token = get_credential(cls.TOKEN, creds.token if creds else "")

        return AzureDevOpsCredentials(base_url=base_url, project=project, organization=organization, access_token=token)

    @classmethod
    def get_elastic_creds(
        cls,
        user_id: str,
        project_name: str = None,
        assistant_id: Optional[str] = None,
        tool_config: Optional[ToolConfig] = None,
    ) -> ElasticConfig:
        search_fields = {
            SearchFields.USER_ID: user_id,
            SearchFields.CREDENTIAL_TYPE: CredentialTypes.ELASTIC,
            SearchFields.PROJECT_NAME: project_name,
        }
        setting = cls.retrieve_setting(search_fields, assistant_id)
        url = setting.credential(cls.URL) if setting and setting.credential(cls.URL) else ""
        if setting and setting.credential(cls.ELASTIC_API_KEY_ID) and setting.credential(cls.ELASTIC_API_KEY):
            return ElasticConfig(
                url=url, api_key=(setting.credential(cls.ELASTIC_API_KEY_ID), setting.credential(cls.ELASTIC_API_KEY))
            )
        else:
            return ElasticConfig(url=url)

    @classmethod
    def get_aws_creds(
        cls,
        user_id: Optional[str] = None,
        project_name: Optional[str] = None,
        assistant_id: Optional[str] = None,
        tool_config: Optional[ToolConfig] = None,
        integration_id: Optional[str] = None,
    ) -> AWSCredentials:
        return cls.get_config(
            config_class=AWSCredentials,
            user_id=user_id,
            project_name=project_name,
            assistant_id=assistant_id,
            integration_id=integration_id,
            tool_config=tool_config,
        )

    @classmethod
    def get_plugin_creds(
        cls,
        user_id: str,
        project_name: str = None,
        assistant_id: Optional[str] = None,
        tool_config: Optional[ToolConfig] = None,
    ) -> PluginCredentials:
        return cls.get_credentials(
            credential_type=CredentialTypes.PLUGIN,
            user_id=user_id,
            project_name=project_name,
            assistant_id=assistant_id,
            tool_config=tool_config,
            required_fields=cls.PLUGIN_FIELDS,
            credential_class=PluginCredentials,
        )

    @classmethod
    def get_file_system_config(
        cls,
        user_id: str,
        project_name: str = None,
        assistant_id: Optional[str] = None,
        tool_config: Optional[ToolConfig] = None,
    ) -> FileSystemConfig:
        return cls.get_credentials(
            credential_type=CredentialTypes.FILE_SYSTEM,
            user_id=user_id,
            project_name=project_name,
            assistant_id=assistant_id,
            tool_config=tool_config,
            required_fields=cls.FILE_SYSTEM_FIELDS,
            credential_class=FileSystemConfig,
        )

    @classmethod
    def get_sonar_creds(
        cls,
        user_id: str,
        project_name: str = None,
        assistant_id: Optional[str] = None,
        tool_config: Optional[ToolConfig] = None,
    ) -> SonarCredentials:
        return cls.get_credentials(
            credential_type=CredentialTypes.SONAR,
            user_id=user_id,
            project_name=project_name,
            assistant_id=assistant_id,
            tool_config=tool_config,
            required_fields=cls.SONAR_FIELDS,
            credential_class=SonarCredentials,
        )

    @classmethod
    def get_sql_creds(
        cls,
        user_id: str,
        project_name: str = None,
        assistant_id: Optional[str] = None,
        tool_config: Optional[ToolConfig] = None,
    ) -> SQLConfig:
        return cls.get_credentials(
            credential_type=CredentialTypes.SQL,
            user_id=user_id,
            project_name=project_name,
            assistant_id=assistant_id,
            tool_config=tool_config,
            required_fields=cls.SQL_FIELDS,
            credential_class=SQLConfig,
        )

    @classmethod
    def get_zephyr_creds(
        cls,
        user_id: str,
        project_name: str = None,
        assistant_id: Optional[str] = None,
        tool_config: Optional[ToolConfig] = None,
    ) -> ZephyrConfig:
        return cls.get_credentials(
            credential_type=CredentialTypes.ZEPHYR_SCALE,
            user_id=user_id,
            project_name=project_name,
            assistant_id=assistant_id,
            tool_config=tool_config,
            required_fields=cls.ZEPHYR_FIELDS,
            credential_class=ZephyrConfig,
        )

    @classmethod
    def get_zephyr_squad_creds(
        cls,
        user_id: str,
        project_name: str = None,
        assistant_id: Optional[str] = None,
        tool_config: Optional[ToolConfig] = None,
    ) -> ZephyrSquadConfig:
        return cls.get_credentials(
            credential_type=CredentialTypes.ZEPHYR_SQUAD,
            user_id=user_id,
            project_name=project_name,
            assistant_id=assistant_id,
            tool_config=tool_config,
            required_fields=cls.ZEPHYR_SQUAD_FIELDS,
            credential_class=ZephyrSquadConfig,
        )

    @classmethod
    def get_xray_creds(
        cls,
        user_id: str,
        project_name: str = None,
        assistant_id: Optional[str] = None,
        setting_id: Optional[str] = None,
        tool_config: Optional[ToolConfig] = None,
    ) -> XrayConfig:
        return cls.get_config(
            config_class=XrayConfig,
            user_id=user_id,
            project_name=project_name,
            assistant_id=assistant_id,
            integration_id=setting_id,
            tool_config=tool_config,
        )

    @classmethod
    def get_sharepoint_creds(
        cls,
        user_id: str,
        project_name: str = None,
        assistant_id: Optional[str] = None,
        setting_id: Optional[str] = None,
    ) -> SharePointCredentials:
        search_fields = {
            SearchFields.USER_ID: user_id,
            SearchFields.CREDENTIAL_TYPE: CredentialTypes.SHAREPOINT,
            SearchFields.PROJECT_NAME: project_name,
        }

        setting = cls.retrieve_setting(search_fields, assistant_id, setting_id)

        if not setting:
            raise ValueError(f"SharePoint credentials not found for user {user_id}")

        def _get(key: str) -> str:
            val = setting.credential(key) if setting else None
            return val if val else ""

        auth_type = _get("auth_type") or "app"
        expires_at_raw = _get("expires_at")
        try:
            expires_at = int(expires_at_raw) if expires_at_raw else 0
        except (ValueError, TypeError):
            expires_at = 0

        return SharePointCredentials(
            auth_type=auth_type,
            tenant_id=_get(cls.TENANT_ID),
            client_id=_get(cls.CLIENT_ID),
            client_secret=_get(cls.CLIENT_SECRET),
            access_token=_get("access_token"),
            refresh_token=_get("refresh_token"),
            expires_at=expires_at,
            username=_get(cls.USERNAME),
        )

    @classmethod
    def update_sharepoint_oauth_tokens(
        cls,
        setting_id: str,
        access_token: str,
        refresh_token: str,
        expires_at: int,
    ) -> None:
        """Persist refreshed OAuth tokens back to the stored SharePoint setting."""
        setting = Settings.get_by_id(id_=setting_id)
        if not setting:
            logger.error(f"Cannot update SharePoint OAuth tokens: setting {setting_id} not found")
            return

        updates = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": str(expires_at),
        }
        existing_dict = {cred.key: cred for cred in setting.credential_values}
        for key, value in updates.items():
            if key in existing_dict:
                existing_dict[key].value = cls._encrypt_fields([CredentialValues(key=key, value=value)])[0].value
                attributes.flag_modified(setting, "credential_values")
            else:
                encrypted = cls._encrypt_fields([CredentialValues(key=key, value=value)])[0]
                setting.credential_values.append(encrypted)

        setting.update()

    @classmethod
    def get_a2a_creds(cls, user_id: str, project_name: str = None, integration_id: str = None) -> Dict[str, str]:
        """
        Retrieves A2A (Assistant-to-Assistant) credentials for the specified user and project.

        Args:
            user_id: The user ID to retrieve credentials for
            project_name: Optional project name to filter credentials
            integration_id: Optional integration id to get credentials for

        Returns:
            Dictionary containing authentication information for A2A integration
        """
        search_fields = {
            SearchFields.USER_ID: user_id,
            SearchFields.CREDENTIAL_TYPE: CredentialTypes.A2A,
            SearchFields.PROJECT_NAME: project_name,
        }
        setting = cls.retrieve_setting(search_fields=search_fields, setting_id=integration_id)

        # helper to safely get credential values
        def get_cred(key: str, default: Optional[str] = ""):
            return setting.credential(key) if setting else default

        # Get authentication type (bearer, basic, apikey)
        auth_type = setting.credential("auth_type") if setting else ""

        # Get credentials based on auth type
        if auth_type == "basic":
            return {
                "auth_type": auth_type,
                "username": get_cred("username"),
                "password": get_cred("password"),
            }
        if auth_type == "apikey":
            return {
                "auth_type": auth_type,
                "auth_value": get_cred("auth_value"),
                "header_name": get_cred("header_name"),
            }
        if auth_type == "aws_signature":
            creds = {
                "auth_type": auth_type,
                "aws_access_key_id": get_cred("aws_access_key_id"),
                "aws_secret_access_key": get_cred("aws_secret_access_key"),
                "aws_region": get_cred("aws_region"),
                "aws_service_name": get_cred("aws_service_name"),
            }
            aws_session_token = get_cred("aws_session_token", None)
            if aws_session_token:
                creds["aws_session_token"] = aws_session_token
            return creds
        # bearer token is the default
        return {
            "auth_type": "bearer",
            "auth_value": get_cred("auth_value"),
        }

    @classmethod
    def retrieve_setting(cls, search_fields, assistant_id: str = None, setting_id: str = None):
        """
        Invoke setting search chain
        Args:
            search_fields: Dictionary containing search parameters
            assistant_id: Optional assistant identifier
            setting_id: Optional setting identifier

        Returns:
            Settings object or None if no matching settings found
        """
        settings = build_settings_handlers().handle(
            search_fields=search_fields, assistant_id=assistant_id, setting_id=setting_id
        )

        cls._decrypt_credentials(settings)
        return settings

    @classmethod
    def _decrypt_credentials(cls, settings):
        # Decrypt credentials if settings found
        if settings and settings.credential_values:
            try:
                creds = cls._decrypt_fields(
                    settings.credential_values, force_all=settings.credential_type == CredentialTypes.ENVIRONMENT_VARS
                )
                settings.credential_values = creds
            except Exception as e:
                logger.error(f"Failed to decrypt credentials: {str(e)}")

    @classmethod
    def check_webhook_unique(cls, request: SettingRequest, setting_id: Optional[str] = None):
        if request.credential_type != CredentialTypes.WEBHOOK:
            return

        webhook_id = next(
            (webhook.value for webhook in request.credential_values if webhook.key == cls.WEBHOOK_ID), None
        )
        if not webhook_id:
            raise ValueError("Webhook ID must be provided.")
        Settings.check_webhook_id_unique(webhook_id=webhook_id, setting_id=setting_id)
