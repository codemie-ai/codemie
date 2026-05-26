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

from datetime import datetime
from enum import Enum
from typing import Literal, List, Annotated, Optional
from typing import Union, Any, Dict
from uuid import uuid4

from pydantic import BaseModel, Field, TypeAdapter
from pydantic import model_validator, ConfigDict, field_serializer
from typing_extensions import Self
from sqlmodel import Field as SQLField, Column

from codemie.rest_api.models.base import BaseModelWithSQLSupport, PydanticListType, PydanticType


class TaskState(str, Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    AUTH_REQUIRED = "auth-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


def _normalize_part_discriminator(data):
    """Normalize v0.1 'type' field to v0.2 'kind' field for backward compat."""
    if isinstance(data, dict) and "type" in data and "kind" not in data:
        data["kind"] = data.pop("type")
    return data


class TextPart(BaseModel):
    kind: Literal["text"] = "text"
    text: str
    metadata: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_discriminator(cls, data):
        return _normalize_part_discriminator(data)


class FileContent(BaseModel):
    name: str | None = None
    mimeType: str | None = None
    bytes: str | None = None
    uri: str | None = None

    @model_validator(mode="after")
    def check_content(self) -> Self:
        if not (self.bytes or self.uri):
            raise ValueError("Either 'bytes' or 'uri' must be present in the file data")
        if self.bytes and self.uri:
            raise ValueError("Only one of 'bytes' or 'uri' can be present in the file data")
        return self


class FilePart(BaseModel):
    kind: Literal["file"] = "file"
    file: FileContent
    metadata: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_discriminator(cls, data):
        return _normalize_part_discriminator(data)


class DataPart(BaseModel):
    kind: Literal["data"] = "data"
    data: dict[str, Any]
    metadata: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_discriminator(cls, data):
        return _normalize_part_discriminator(data)


Part = Annotated[Union[TextPart, FilePart, DataPart], Field(discriminator="kind")]


class Message(BaseModel):
    role: Literal["user", "agent"]
    parts: List[Part]
    metadata: dict[str, Any] | None = None
    messageId: str | None = None
    taskId: str | None = None
    contextId: str | None = None
    kind: Literal["message"] = "message"


class TaskStatus(BaseModel):
    state: TaskState
    message: Message | None = None
    timestamp: datetime = Field(default_factory=datetime.now)

    @field_serializer("timestamp")
    def serialize_dt(self, dt: datetime, _info):
        return dt.isoformat()


class Artifact(BaseModel):
    artifactId: str | None = None
    name: str | None = None
    description: str | None = None
    parts: List[Part]
    metadata: dict[str, Any] | None = None
    index: int = 0
    append: bool | None = None
    lastChunk: bool | None = None


class Task(BaseModelWithSQLSupport, table=True):
    __tablename__ = "a2a_tasks"

    sessionId: str | None = None
    contextId: str | None = None
    status: TaskStatus = SQLField(sa_column=Column(PydanticType(TaskStatus)))
    artifacts: List[Artifact] | None = SQLField(default=None, sa_column=Column(PydanticListType(Artifact)))
    history: List[Message] | None = SQLField(default=None, sa_column=Column(PydanticListType(Message)))

    @field_serializer("update_date", "date")
    def serialize_dt(self, dt: datetime, _info):
        return dt.isoformat()


class TaskStatusUpdateEvent(BaseModel):
    id: str
    status: TaskStatus
    final: bool = False
    metadata: dict[str, Any] | None = None
    kind: Literal["status-update"] = "status-update"


class TaskArtifactUpdateEvent(BaseModel):
    id: str
    artifact: Artifact
    metadata: dict[str, Any] | None = None
    kind: Literal["artifact-update"] = "artifact-update"


class AuthenticationInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    schemes: List[str]
    credentials: str | None = None


class PushNotificationConfig(BaseModel):
    url: str
    token: str | None = None
    authentication: AuthenticationInfo | None = None


class TaskIdParams(BaseModel):
    id: str
    metadata: dict[str, Any] | None = None


class TaskQueryParams(TaskIdParams):
    historyLength: int | None = None


class TaskSendParams(BaseModel):
    id: str
    sessionId: str = Field(default_factory=lambda: uuid4().hex)
    contextId: str | None = None
    message: Message
    acceptedOutputModes: Optional[List[str]] = None
    pushNotification: PushNotificationConfig | None = None
    historyLength: int | None = None
    metadata: dict[str, Any] | None = None


class TaskPushNotificationConfig(BaseModel):
    id: str
    pushNotificationConfig: PushNotificationConfig


class JSONRPCMessage(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str | None = Field(default_factory=lambda: uuid4().hex)


class JSONRPCRequest(JSONRPCMessage):
    method: str
    params: dict[str, Any] | None = None


class JSONRPCError(BaseModel):
    code: int
    message: str
    data: Any | None = None


class JSONRPCResponse(JSONRPCMessage):
    result: Any | None = None
    error: JSONRPCError | None = None


# --- v0.2 Request types (accept both v0.1 and v0.2 method names) ---

class MessageSendRequest(JSONRPCRequest):
    method: Literal["message/send", "tasks/send"] = "message/send"
    params: TaskSendParams


class MessageSendResponse(JSONRPCResponse):
    result: Task | Message | None = None


class MessageStreamRequest(JSONRPCRequest):
    method: Literal["message/stream", "tasks/sendSubscribe"] = "message/stream"
    params: TaskSendParams


class SendTaskStreamingResponse(JSONRPCResponse):
    result: TaskStatusUpdateEvent | TaskArtifactUpdateEvent | None = None


class GetTaskRequest(JSONRPCRequest):
    method: Literal["tasks/get"] = "tasks/get"
    params: TaskQueryParams


class GetTaskResponse(JSONRPCResponse):
    result: Task | None = None


class CancelTaskRequest(JSONRPCRequest):
    method: Literal["tasks/cancel"] = "tasks/cancel"
    params: TaskIdParams


class CancelTaskResponse(JSONRPCResponse):
    result: Task | None = None


class TaskResubscribeRequest(JSONRPCRequest):
    method: Literal["tasks/resubscribe"] = "tasks/resubscribe"
    params: TaskQueryParams


class SetTaskPushNotificationConfigRequest(JSONRPCRequest):
    method: Literal["tasks/pushNotificationConfig/set", "tasks/pushNotification/set"] = "tasks/pushNotificationConfig/set"
    params: TaskPushNotificationConfig


class SetTaskPushNotificationConfigResponse(JSONRPCResponse):
    result: TaskPushNotificationConfig | None = None


class GetTaskPushNotificationConfigRequest(JSONRPCRequest):
    method: Literal["tasks/pushNotificationConfig/get", "tasks/pushNotification/get"] = "tasks/pushNotificationConfig/get"
    params: TaskIdParams


class GetTaskPushNotificationConfigResponse(JSONRPCResponse):
    result: TaskPushNotificationConfig | None = None


# Backward-compatible aliases
SendTaskRequest = MessageSendRequest
SendTaskResponse = MessageSendResponse
SendTaskStreamingRequest = MessageStreamRequest
SetTaskPushNotificationRequest = SetTaskPushNotificationConfigRequest
SetTaskPushNotificationResponse = SetTaskPushNotificationConfigResponse
GetTaskPushNotificationRequest = GetTaskPushNotificationConfigRequest
GetTaskPushNotificationResponse = GetTaskPushNotificationConfigResponse


A2ARequest = TypeAdapter(
    Annotated[
        Union[
            MessageSendRequest,
            GetTaskRequest,
            MessageStreamRequest,
            CancelTaskRequest,
            TaskResubscribeRequest,
            SetTaskPushNotificationConfigRequest,
            GetTaskPushNotificationConfigRequest,
        ],
        Field(discriminator="method"),
    ]
)


## Error types
class JSONParseError(JSONRPCError):
    code: int = -32700
    message: str = "Invalid JSON payload"
    data: Any | None = None


class InvalidRequestError(JSONRPCError):
    code: int = -32600
    message: str = "Request payload validation error"
    data: Any | None = None


class MethodNotFoundError(JSONRPCError):
    code: int = -32601
    message: str = "Method not found"
    data: None = None


class InvalidParamsError(JSONRPCError):
    code: int = -32602
    message: str = "Invalid parameters"
    data: Any | None = None


class InternalError(JSONRPCError):
    code: int = -32603
    message: str = "Internal error"
    data: Any | None = None


class TaskNotFoundError(JSONRPCError):
    code: int = -32001
    message: str = "Task not found"
    data: None = None


class TaskNotCancelableError(JSONRPCError):
    code: int = -32002
    message: str = "Task cannot be canceled"
    data: None = None


class PushNotificationNotSupportedError(JSONRPCError):
    code: int = -32003
    message: str = "Push Notification is not supported"
    data: None = None


class UnsupportedOperationError(JSONRPCError):
    code: int = -32004
    message: str = "This operation is not supported"
    data: None = None


class ContentTypeNotSupportedError(JSONRPCError):
    code: int = -32005
    message: str = "Incompatible content types"
    data: None = None


class AgentProvider(BaseModel):
    organization: str
    url: str | None = None


class AgentCapabilities(BaseModel):
    streaming: bool = False
    pushNotifications: bool = False
    stateTransitionHistory: bool = False
    supportsAuthenticatedExtendedCard: bool = False


class AgentAuthentication(BaseModel):
    schemes: List[str]
    credentials: str | None = None


class AgentSkill(BaseModel):
    id: str
    name: str
    description: str | None = None
    tags: List[str] | None = None
    examples: List[str] | None = None
    inputModes: List[str] | None = None
    outputModes: List[str] | None = None


class AgentCard(BaseModel):
    name: str
    description: str | None = None
    url: str
    provider: AgentProvider | None = None
    version: str
    documentationUrl: str | None = None
    capabilities: AgentCapabilities
    authentication: AgentAuthentication | None = None
    defaultInputModes: List[str] = ["text"]
    defaultOutputModes: List[str] = ["text"]
    skills: List[AgentSkill]
    project_name: str | None = None
    integration_id: str | None = None
    user_id: str | None = None
    bedrock_agentcore: bool = False


class ProtocolVersion(str, Enum):
    V01 = "0.1.0"
    V02 = "0.2.0"


class A2AClientError(Exception):
    pass


class A2AClientHTTPError(A2AClientError):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"HTTP Error {status_code}: {message}")


class A2AClientJSONError(A2AClientError):
    def __init__(self, message: str):
        self.message = message
        super().__init__(f"JSON Error: {message}")


class MissingAPIKeyError(Exception):
    """Exception for missing API key."""

    pass


class A2ARequestBody(BaseModel):
    """A2A JSON-RPC request body"""

    jsonrpc: str = Field("2.0", description="JSON-RPC version")
    id: str = Field("request-123", description="Request identifier")
    method: str = Field("message/send", description="Method name (e.g., message/send, tasks/send)")
    params: Dict[str, Any] = Field(
        {
            "id": "task-123",
            "sessionId": "session-123",
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "Hello, can you help me with this question?"}],
            },
            "historyLength": 10,
        },
        description="Method parameters",
    )
