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

from enum import Enum
from typing import Any, List, Literal, Optional, Dict

from pydantic import BaseModel, Field, model_validator
from sqlmodel import Column, Index, Session, select
from sqlmodel import Field as SQLField

from codemie.core.ability import Owned
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.base import (
    BaseModelWithSQLSupport,
    CommonBaseModel,
    PydanticListType,
    PydanticType,
)
from codemie_tools.base.models import CredentialTypes
from codemie_tools.cloud.aws.models import AWSConfig
from codemie.rest_api.security.user import User

PROJECT_NAME_TERM = "project_name.keyword"
USER_ID_TERM = "user_id.keyword"
ALIAS_TERM = "alias.keyword"
CREDENTIAL_TYPE_TERM = "credential_type.keyword"
ASSISTANT_IDS_KEY = "assistant_ids"


class CredentialValues(BaseModel):
    key: str
    value: Any = None


class GitAuthType(str, Enum):
    """Git authentication types."""

    PAT = "pat"
    GITHUB_APP = "github_app"
    # Per-user GitLab OAuth (3LO). The access token is used like a PAT in the clone URL
    # (userinfo "oauth2:<token>@"), so it flows through the token branch in git_loader; this
    # member exists so the credential is labeled accurately instead of masquerading as a PAT.
    OAUTH = "oauth"


class Credentials(BaseModel):
    url: str
    auth_type: GitAuthType = GitAuthType.PAT

    # PAT authentication fields
    token: Optional[str] = None
    token_name: Optional[str] = None

    # GitHub App authentication fields
    app_id: Optional[int] = None
    private_key: Optional[str] = None
    installation_id: Optional[int] = None

    # Header-based authentication (for on-prem git servers like Azure DevOps on-prem)
    use_header_auth: Optional[bool] = False

    @property
    def is_github_app(self) -> bool:
        """Check if GitHub App authentication is configured."""
        return self.auth_type == GitAuthType.GITHUB_APP

    def _validate_pat_fields(self):
        """Validate PAT authentication fields."""
        if not self.token:
            raise ValueError("PAT authentication requires 'token'")
        if self.app_id or self.private_key:
            raise ValueError("Cannot set GitHub App fields when using PAT authentication")

    def _validate_github_app_fields(self):
        """Validate GitHub App authentication fields."""
        if not self.app_id:
            raise ValueError("GitHub App authentication requires 'app_id'")
        if not self.private_key:
            raise ValueError("GitHub App authentication requires 'private_key'")
        if self.token:
            raise ValueError("Cannot set PAT token when using GitHub App authentication")

    @model_validator(mode='after')
    def validate_authentication(self):
        """Validate auth fields based on auth_type."""
        # Skip validation if all auth fields are empty (backward compatibility for empty settings)
        has_auth_fields = self.token or self.app_id or self.private_key

        if has_auth_fields:
            if self.auth_type == GitAuthType.PAT:
                self._validate_pat_fields()
            elif self.auth_type == GitAuthType.GITHUB_APP:
                self._validate_github_app_fields()

        return self

    @model_validator(mode='before')
    def validate_config(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        if "name" in values:
            values["token_name"] = values.pop("name")
        return values


class SVNAuthType(str, Enum):
    """SVN authentication types."""

    BASIC = "basic"


class SVNCredentials(BaseModel):
    auth_type: SVNAuthType = SVNAuthType.BASIC

    # Basic auth fields
    username: Optional[str] = None
    password: Optional[str] = None


class AWSCredentials(AWSConfig):
    pass


class GCPCredentials(BaseModel):
    api_key: str


class AzureCredentials(BaseModel):
    subscription_id: str
    tenant_id: str
    client_id: str
    client_secret: str


class PluginCredentials(BaseModel):
    plugin_key: str


class FileSystemConfig(BaseModel):
    root_directory: str
    activate_command: Optional[str] = None


class EmailAuthType(str, Enum):
    """Email authentication types."""

    BASIC = "basic"
    OAUTH_AZURE = "oauth_azure"


class EmailCredentials(BaseModel):
    url: str
    auth_type: EmailAuthType = EmailAuthType.BASIC

    # SMTP authentication fields
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None

    # OAuth Azure fields
    oauth_from_email: Optional[str] = None
    oauth_client_id: Optional[str] = None
    oauth_client_secret: Optional[str] = None
    oauth_tenant_id: Optional[str] = None
    oauth_authority: Optional[str] = None
    oauth_scope: Optional[str] = None


class SonarCredentials(BaseModel):
    url: str
    token: str
    sonar_project_name: str


class DialCredentials(BaseModel):
    api_version: str
    api_key: str
    url: str


class LiteLLMCredentials(BaseModel):
    api_key: str
    url: str = ""


class LiteLLMContext(BaseModel):
    credentials: Optional[LiteLLMCredentials]
    current_project: str | None


class AzureDevOpsCredentials(BaseModel):
    base_url: str
    project: str
    organization: str
    access_token: str


class SharePointCredentials(BaseModel):
    auth_type: Literal["app", "oauth"] = "app"
    # App credentials
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    # OAuth delegated credentials
    access_token: str = ""
    refresh_token: str = ""
    expires_at: int = 0  # Unix timestamp
    username: str = ""


class Scheduler(BaseModel):
    schedule: str
    is_enabled: bool
    resource_type: str
    resource_id: str
    timezone: str = "UTC"


class SettingRequest(BaseModel):
    project_name: Optional[str] = None
    alias: str
    credential_type: CredentialTypes
    credential_values: List[CredentialValues]
    is_global: Optional[bool] = False
    oauth_state: Optional[str] = None


class TestSettingRequest(BaseModel):
    credential_type: CredentialTypes
    credential_values: Optional[List[CredentialValues]] = Field(default_factory=list)
    setting_id: Optional[str] = None

    __test__ = False


class SettingType(str, Enum):
    USER = "user"
    PROJECT = "project"


class SettingsBase(CommonBaseModel):
    user_id: Optional[str] = SQLField(default=None, index=True)
    created_by: Optional[CreatedByUser] = SQLField(default=None, sa_column=Column(PydanticType(CreatedByUser)))
    project_name: str = SQLField(index=True)
    alias: Optional[str] = None
    default: Optional[bool] = False
    credential_type: CredentialTypes = SQLField(index=True)
    credential_values: List[CredentialValues] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(CredentialValues))
    )
    setting_hash: Optional[str] = None
    setting_type: Optional[SettingType] = SettingType.USER
    is_global: Optional[bool] = SQLField(default=False)

    # Custom PostgreSQL indexes
    __table_args__ = (
        Index(
            'ix_settings_project_name',
            "project_name",
            postgresql_using='gin',
            postgresql_ops={"project_name": "gin_trgm_ops"},
        ),
        Index('ix_settings_alias', "alias", postgresql_using='gin', postgresql_ops={"alias": "gin_trgm_ops"}),
        Index('ix_settings_date', 'date'),
    )

    def normalize_values(self) -> dict:
        """Convert CredentialValues list into a dictionary of key-value pairs.

        Returns:
            dict: Dictionary with keys from CredentialValues.key and values from CredentialValues.value

        Example:
            [
                CredentialValues(key='url', value='https://domain.example.com'),
                CredentialValues(key='username', value='')
            ]
            becomes
            {"url": "https://domain.example.com", "username": ""}
        """
        return {cred.key: cred.value for cred in self.credential_values}

    def credential(self, value):
        """
        Returns first credential value by key
        """
        cred = next((cred for cred in self.credential_values if cred.key == value), None)

        if not cred:
            return None

        return cred.value

    @classmethod
    def check_alias_unique(
        cls,
        project_name: str,
        alias: str,
        setting_id: Optional[str] = None,
        user_id: Optional[str] = None,
        setting_type: Optional[SettingType] = None,
    ) -> bool:
        if not alias:
            raise ValueError("Alias is required")

        if setting_type == SettingType.PROJECT:
            settings = cls.get_by_fields({PROJECT_NAME_TERM: project_name, ALIAS_TERM: alias})
        else:
            settings = cls.get_by_fields({PROJECT_NAME_TERM: project_name, USER_ID_TERM: user_id, ALIAS_TERM: alias})

        if settings and not setting_id or settings and setting_id and setting_id != settings.id:
            raise ValueError(f"There are more than one settings with the alias named {alias}")
        return True

    @classmethod
    def check_ms_teams_exist(cls, project_name: str, setting_id: str | None = None) -> bool:
        existing = cls.get_by_fields(
            {
                PROJECT_NAME_TERM: project_name,
                CREDENTIAL_TYPE_TERM: CredentialTypes.MS_TEAMS.value,
                "setting_type": SettingType.PROJECT.value,
            }
        )
        if existing and (not setting_id or setting_id != existing.id):
            raise ValueError(f"An ms_teams integration already exists for project {project_name!r}")
        return True

    @classmethod
    def check_ms_teams_exist_for_user(cls, user_id: str, setting_id: str | None = None) -> bool:
        existing = cls.get_by_fields(
            {
                USER_ID_TERM: user_id,
                "setting_type": SettingType.USER.value,
                CREDENTIAL_TYPE_TERM: CredentialTypes.MS_TEAMS.value,
            }
        )
        if existing and (not setting_id or setting_id != existing.id):
            raise ValueError(f"An ms_teams integration already exists for user {user_id!r}")
        return True

    @classmethod
    def prune_ms_teams_assistant_id(cls, assistant_id: str, keep_project_name: Optional[str] = None) -> int:
        """Remove assistant_id from every ms_teams settings row's assistant_ids list.

        The ms_teams assistant_ids list lives in a JSONB credential_values blob with no
        FK/cascade to an assistant, unlike the retired assistant_project_mapping table.
        This must be called when an assistant is deleted (keep_project_name=None, prune
        everywhere) or moved to a different project (keep_project_name=the new project,
        prune everywhere else), or a stale reference silently lingers.

        Returns the number of settings rows that were changed.
        """
        settings = cls.get_all_by_fields({CREDENTIAL_TYPE_TERM: CredentialTypes.MS_TEAMS.value})
        updated = 0

        for setting in settings:
            if keep_project_name and setting.project_name == keep_project_name:
                continue
            changed = False
            new_values = []

            for cred in setting.credential_values:
                if cred.key == ASSISTANT_IDS_KEY and isinstance(cred.value, list) and assistant_id in cred.value:
                    cred = CredentialValues(key=cred.key, value=[aid for aid in cred.value if aid != assistant_id])
                    changed = True

                new_values.append(cred)

            if changed:
                setting.credential_values = new_values
                setting.save()
                updated += 1
        return updated

    @classmethod
    def check_webhook_id_unique(cls, webhook_id: str, setting_id: Optional[str] = None) -> bool:
        query = {"credential_values.key.keyword": "webhook_id", "credential_values.value.keyword": webhook_id}

        settings = cls.get_by_fields(query)

        if settings and not setting_id or settings and setting_id and setting_id != settings.id or not webhook_id:
            raise ValueError(
                f"Webhook '{webhook_id}' is not unique and already used. Please choose another webhook id."
            )
        return True


class UserSetting(Owned):
    def __init__(self, setting: SettingsBase) -> None:
        self._setting = setting

    def _check_setting_owning(self, user: User):
        return bool(self._setting.user_id) and self._setting.user_id == user.id

    def is_owned_by(self, user: User):
        return self._check_setting_owning(user)

    def is_managed_by(self, user: User):
        return self._check_setting_owning(user)

    def is_shared_with(self, user: User):
        return self._check_setting_owning(user)


class ProjectSetting(Owned):
    def __init__(self, setting: SettingsBase) -> None:
        self._setting = setting

    def is_owned_by(self, user: User):
        return self._setting.user_id == user.id

    def is_managed_by(self, user: User):
        return self._setting.project_name in user.admin_project_names

    def is_shared_with(self, user: User):
        return self._setting.project_name in user.project_names


type AbilitySetting = UserSetting | ProjectSetting


class Settings(BaseModelWithSQLSupport, SettingsBase, table=True):
    __tablename__ = "settings"

    @classmethod
    def get_all(cls, setting_type: Optional[SettingType] = None, credential_type: Optional[CredentialTypes] = None):
        query_filters = {}

        if setting_type:
            query_filters["setting_type.keyword"] = setting_type.value

        if credential_type:
            query_filters[CREDENTIAL_TYPE_TERM] = credential_type.value

        if query_filters:
            return cls.get_all_by_fields(query_filters)
        else:
            return super().get_all()

    @classmethod
    def get_by_user_id(cls, user_id, credential_type: Optional[CredentialTypes] = None):
        query_filters = {USER_ID_TERM: user_id, "setting_type": SettingType.USER.value}

        if credential_type:
            query_filters["credential_type"] = credential_type.value

        return cls.get_all_by_fields(query_filters)

    @classmethod
    def get_by_project_names(cls, project_names, credential_type: Optional[CredentialTypes] = None):
        with Session(cls.get_engine()) as session:
            statement = select(cls)
            statement = statement.where(cls.project_name.in_(project_names))
            statement = statement.where(cls.setting_type == SettingType.PROJECT.value)
            if credential_type:
                statement = statement.where(cls.credential_type == credential_type.value)
            return session.exec(statement).all()

    @classmethod
    def find_by_resource_id(
        cls, project_name: str, credential_type: CredentialTypes, resource_id: str
    ) -> list["Settings"]:
        """
        Find all settings linked to a specific resource using a JSONB containment query.

        Filters by project_name and credential_type (both indexed) first, then uses
        PostgreSQL JSONB @> operator to match resource_id inside credential_values array.
        """
        with Session(cls.get_engine()) as session:
            statement = (
                select(cls)
                .where(cls.project_name == project_name)
                .where(cls.credential_type == credential_type)
                .where(cls.credential_values.contains([{"key": "resource_id", "value": resource_id}]))
            )
            return session.exec(statement).all()

    @classmethod
    def get_all_project_litellm_settings(cls) -> list["Settings"]:
        """Return all PROJECT-scoped LITE_LLM settings across all projects.

        Returned instances are detached (session is closed on return).
        Only scalar column attributes are safe to access.
        """
        with Session(cls.get_engine()) as session:
            statement = (
                select(cls)
                .where(cls.setting_type == SettingType.PROJECT.value)
                .where(cls.credential_type == CredentialTypes.LITE_LLM.value)
            )
            return session.exec(statement).all()

    @classmethod
    def delete_setting(cls, setting_id: str):
        setting = cls.find_by_id(setting_id)
        if setting:
            return setting.delete()
        return {"status": "not found"}

    @classmethod
    def get_by_alias(
        cls,
        alias: str,
        project_name: str,
        user_id: Optional[str] = None,
    ):
        """
        Retrieve a setting by alias, searching for both user and project settings.
        Returns the first matching Settings object or None.
        """
        # Priority 1: User-scoped settings
        if user_id:
            setting = cls.get_by_fields(
                {
                    ALIAS_TERM: alias,
                    PROJECT_NAME_TERM: project_name,
                    USER_ID_TERM: user_id,
                }
            )
            if setting:
                return setting

        # Priority 2: Project-scoped settings (fallback)
        setting = cls.get_by_fields(
            {
                ALIAS_TERM: alias,
                PROJECT_NAME_TERM: project_name,
            }
        )
        if setting:
            return setting

        return None
