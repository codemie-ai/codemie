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

from pydantic import BaseModel, Field, HttpUrl, AfterValidator, ConfigDict, model_validator
from typing import List, Dict, Optional, Annotated, Self, Union
from enum import auto, StrEnum
from fastapi.exceptions import RequestValidationError
from uuid import uuid4

from codemie.rest_api.models.base import (
    CommonBaseModel,
    BaseModelWithSQLSupport,
    PydanticType,
    PydanticListType,
)
from codemie.rest_api.models.base import CamelCaseStrEnum
from sqlmodel import Field as SQLField, Column, String, Session, select


class ProviderConfiguration(BaseModel):
    class AuthType(StrEnum):
        BEARER = "Bearer"

    auth_type: AuthType = Field(default=AuthType.BEARER)


class ProviderToolResultType(CamelCaseStrEnum):
    ANY = auto()
    INTEGER = auto()
    FLOAT = auto()
    STRING = auto()
    BYTES = auto()
    BOOL = auto()
    JSON = auto()
    YAML = auto()
    TEXT = auto()


class ProviderToolkitConfigParameter(BaseModel):
    class ParameterType(CamelCaseStrEnum):
        NUMBER = auto()
        STRING = auto()
        BOOLEAN = auto()
        SECRET = auto()
        URL = "URL"
        UUID = "UUID"

    model_config = ConfigDict(populate_by_name=True)

    description: str = ""
    parameter_type: ParameterType = Field(..., alias="type")
    required: bool = False
    enum: Optional[List[str]] = None
    example: Optional[str] = None
    title: Optional[str] = None


class ProviderToolArgument(BaseModel):
    class ArgType(CamelCaseStrEnum):
        STRING = auto()
        LIST = auto()  # NOSONAR python:S905
        NUMBER = auto()
        BOOLEAN = auto()
        INTEGER = auto()

    model_config = ConfigDict(populate_by_name=True)

    arg_type: ArgType = Field(..., alias="type")
    required: bool = True
    description: Optional[str] = ""
    enum: Optional[List[str]] = None
    title: Optional[str] = None
    example: Optional[str] = None


class ProviderToolMetadata(BaseModel):
    class Config:
        extra = "allow"
        arbitrary_types_allowed = True

    class ActionType(StrEnum):
        CREATE = auto()
        MODIFY = auto()
        REMOVE = auto()
        READ = auto()
        DATA_RETRIEVAL = auto()

    class Purpose(StrEnum):
        LIFE_CYCLE_MANAGEMENT = auto()
        DATA_RETRIEVAL = auto()

    tool_type: Optional[str] = None
    tool_purpose: Optional[Purpose] = Field(None)
    tool_action_type: Optional[ActionType] = Field(None)


class ProviderToolkitMetadata(BaseModel):
    life_cycle_id: Optional[str] = ""
    managed_fields: Optional[Dict[str, str]] = None


class ProviderToolkit(BaseModel):
    class ToolkitConfig(BaseModel):
        model_config = ConfigDict(populate_by_name=True)

        toolkit_config_type: Optional[str] = Field(None, alias="type")
        description: Optional[str] = ""
        parameters: Dict[str, ProviderToolkitConfigParameter] = Field(default_factory=dict)

    class Tool(BaseModel):
        name: str
        description: Optional[str] = ""
        args_schema: Dict[str, ProviderToolArgument]
        tool_metadata: ProviderToolMetadata
        tool_result_type: ProviderToolResultType = Field(ProviderToolResultType.STRING)
        sync_invocation_supported: bool = True
        async_invocation_supported: bool = False

        @property
        def is_datasource_action(self) -> bool:
            """Check if the tool is a datasource CRUD action"""
            return self.tool_metadata.tool_purpose == ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT

        @property
        def is_datasource_tool(self) -> bool:
            """Check if the tool is a datasource tool"""
            return self.tool_metadata.tool_purpose == ProviderToolMetadata.Purpose.DATA_RETRIEVAL

    toolkit_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    description: Optional[str] = ""
    toolkit_config: ToolkitConfig
    provided_tools: List[Tool]
    toolkit_metadata: Optional[ProviderToolkitMetadata] = None

    _ERR_NO_DATA_RETRIEVAL_TOOL = "No data retrieval tools found in the toolkit"
    _ERR_INVALID_LIFECYCLE_ACTIONS = "The toolkit does not have the required lifecycle actions, missing: {missing}"
    _REQUIRED_LIFE_CYCLE_ACTIONS = [
        ProviderToolMetadata.ActionType.CREATE,
        ProviderToolMetadata.ActionType.REMOVE,
        ProviderToolMetadata.ActionType.MODIFY,
    ]

    _has_datasource_definition: bool = False

    @model_validator(mode="after")
    def check_for_datasource_definition(self) -> Self:
        """Checks if datasource tools are present in the toolkit"""
        has_lifecycle_mngmt_tool = any(
            tool.tool_metadata.tool_purpose == ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT
            for tool in self.provided_tools
        )

        has_data_retrieval_tool = any(
            tool.tool_metadata.tool_purpose == ProviderToolMetadata.Purpose.DATA_RETRIEVAL
            for tool in self.provided_tools
        )

        has_definition = has_lifecycle_mngmt_tool or has_data_retrieval_tool

        if has_definition:
            self._has_datasource_definition = True

        return self

    @model_validator(mode="after")
    def check_datasource_retrieval_tools(self) -> Self:
        """Checks if a data retrieval tool exists in the toolkit"""
        if not self._has_datasource_definition:
            return self

        has_data_retrieval_tool = any(
            tool.tool_metadata.tool_purpose == ProviderToolMetadata.Purpose.DATA_RETRIEVAL
            for tool in self.provided_tools
        )

        if not has_data_retrieval_tool:
            raise RequestValidationError(
                [
                    {
                        "loc": ["provided_tools"],
                        "msg": self._ERR_NO_DATA_RETRIEVAL_TOOL,
                        "type": "value_error",
                    }
                ]
            )

        return self

    @model_validator(mode="after")
    def check_datasource_lifecycle_tools(self) -> Self:
        """Checks if required lifecycle actions are present"""
        if not self._has_datasource_definition:
            return self

        lifecycle_actions = [
            tool.tool_metadata.tool_action_type
            for tool in self.provided_tools
            if tool.tool_metadata.tool_purpose == ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT
        ]

        if not all(action in lifecycle_actions for action in self._REQUIRED_LIFE_CYCLE_ACTIONS):
            missing_actions = ", ".join(
                [action.value for action in self._REQUIRED_LIFE_CYCLE_ACTIONS if action not in lifecycle_actions]
            )
            raise RequestValidationError(
                [
                    {
                        "loc": ["provided_tools"],
                        "msg": self._ERR_INVALID_LIFECYCLE_ACTIONS.format(missing=missing_actions),
                        "type": "value_error",
                    }
                ]
            )

        return self

    def get_managed_fields(self):
        if self.toolkit_metadata and self.toolkit_metadata.managed_fields:
            return self.toolkit_metadata.managed_fields
        return {}


class ProviderBase(CommonBaseModel):
    name: str = SQLField(index=True)
    service_location_url: Annotated[HttpUrl, AfterValidator(str)] = SQLField(sa_type=String)
    configuration: ProviderConfiguration = SQLField(sa_column=Column(PydanticType(ProviderConfiguration)))
    provided_toolkits: List[ProviderToolkit] = SQLField(sa_column=Column(PydanticListType(ProviderToolkit)))


class Provider(BaseModelWithSQLSupport, ProviderBase, table=True):
    __tablename__ = "providers"

    @classmethod
    def check_name_is_unique(cls, name: str, provider_id: Optional[str] = None) -> bool:
        with Session(cls.get_engine()) as session:
            statement = select(cls)
            statement = statement.where(cls.name == name)

            if provider_id:
                statement = statement.where(cls.id != provider_id)

            result = session.exec(statement).all()
        return len(result) == 0


# API Request / Response Models
class CreateProviderRequest(BaseModel):
    name: str = Field(..., description="Provider name", min_length=1, examples=["My Provider"])
    service_location_url: Annotated[HttpUrl, AfterValidator(str)] = Field(
        ..., description="Provider service URL", examples=["https://api.example.com"]
    )
    configuration: ProviderConfiguration = Field(..., description="Provider configuration settings")
    provided_toolkits: List[ProviderToolkit] = Field(
        ..., description="List of toolkits provided by this provider", min_length=1
    )

    @model_validator(mode="before")
    @classmethod
    def validate_required_fields(cls, data):
        """Validate required fields with better error messages"""
        if not isinstance(data, dict):
            return data

        errors = []
        if not data.get("name"):
            errors.append(cls._create_error("name", "Provider name is required and cannot be empty"))
        if not data.get("service_location_url"):
            errors.append(cls._create_error("service_location_url", "Service location URL is required"))
        if not data.get("configuration"):
            errors.append(cls._create_error("configuration", "Provider configuration is required"))
        if not data.get("provided_toolkits"):
            errors.append(cls._create_error("provided_toolkits", "At least one toolkit must be provided"))

        if errors:
            raise RequestValidationError(errors)

        return data

    @staticmethod
    def _create_error(loc: str, msg: str):
        return {
            "loc": [loc],
            "msg": msg,
            "type": "value_error",
        }


class UpdateProviderRequest(BaseModel):
    name: Optional[str] = None
    service_location_url: Optional[Annotated[HttpUrl, AfterValidator(str)]] = None
    configuration: Optional[ProviderConfiguration] = None
    provided_toolkits: Optional[List[ProviderToolkit]] = None


class ProviderDataSourceTypeSchema(BaseModel):
    """Defines schema of an action for provider datasource definition"""

    class Parameter(BaseModel):
        class AdditionalTypes(CamelCaseStrEnum):
            """Special types used only for front end interactions"""

            MULTISELECT = auto()

        name: str
        description: str
        required: bool
        parameter_type: Union[
            ProviderToolArgument.ArgType,
            ProviderToolkitConfigParameter.ParameterType,
            AdditionalTypes,
        ]
        enum: Optional[List[str]] = None
        multiselect_options: Optional[List[dict]] = None
        title: Optional[str] = None
        example: Optional[str] = None

    description: str
    parameters: List[Parameter] = Field(default_factory=list)

    def get_sensetive_fields(self) -> List[str]:
        """Returns a list of sensitive fields in the schema"""
        return [
            param.name
            for param in self.parameters
            if param.parameter_type == ProviderToolkitConfigParameter.ParameterType.SECRET
        ]


class ProviderDataSourceSchemas(BaseModel):
    """Defines the schema for creating and updating a datasource"""

    schema_id: str = Field(..., alias="id")
    provider_name: str
    name: str

    base_schema: ProviderDataSourceTypeSchema
    create_schema: ProviderDataSourceTypeSchema

    @property
    def field_names(self) -> List[str]:
        """Returns a list of field names in the schema"""
        return [param.name for param in self.base_schema.parameters + self.create_schema.parameters]


class ProviderAiceDatasource(BaseModel):
    name: str
    datasource_id: str
    project_name: str
