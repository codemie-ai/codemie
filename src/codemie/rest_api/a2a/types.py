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


import contextlib
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Dict, Iterable, List, Literal, Self, Union
from uuid import uuid4

from pydantic import BaseModel, Field, PrivateAttr, TypeAdapter, field_serializer, model_serializer, model_validator
from sqlmodel import Column
from sqlmodel import Field as SQLField

from codemie.configs import logger
from codemie.rest_api.a2a.version import A2A_PROTOCOL_VERSION, LEGACY_PROTOCOL_VERSION
from codemie.rest_api.models.base import BaseModelWithSQLSupport, PydanticListType, PydanticType

TEXT_PLAIN = "text/plain"

# NOTE: this module must stay import-light (no `a2a.utils` import) because `models.assistant` imports it.
# Wire names are the Python attribute names (ProtoJSON camelCase); persistence uses a plain `model_dump()`.


class TaskState(str, Enum):
    SUBMITTED = "TASK_STATE_SUBMITTED"
    WORKING = "TASK_STATE_WORKING"
    COMPLETED = "TASK_STATE_COMPLETED"
    FAILED = "TASK_STATE_FAILED"
    CANCELED = "TASK_STATE_CANCELED"
    INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
    REJECTED = "TASK_STATE_REJECTED"
    AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"


class Role(str, Enum):
    USER = "ROLE_USER"
    AGENT = "ROLE_AGENT"


_LEGACY_TASK_STATES = {
    "submitted": TaskState.SUBMITTED,
    "working": TaskState.WORKING,
    "completed": TaskState.COMPLETED,
    "failed": TaskState.FAILED,
    "canceled": TaskState.CANCELED,
    "input-required": TaskState.INPUT_REQUIRED,
    "rejected": TaskState.REJECTED,
    "auth-required": TaskState.AUTH_REQUIRED,
}
_LEGACY_ROLES = {"user": Role.USER, "agent": Role.AGENT}


def _normalize_task_state(value: Any) -> Any:
    """Map pre-v1.0 task state values to `TASK_STATE_*`; the legacy `unknown` state becomes FAILED."""
    if isinstance(value, str):
        if value in _LEGACY_TASK_STATES:
            return _LEGACY_TASK_STATES[value].value
        if value == "unknown":
            logger.warning("A2A task state 'unknown' is not a v1.0 state; normalised to TASK_STATE_FAILED")
            return TaskState.FAILED.value
    return value


class Part(BaseModel):
    """A content part: exactly one of text | raw (base64) | url | data. No discriminator on the wire."""

    text: str | None = None
    raw: str | None = None
    url: str | None = None
    data: Any = None
    metadata: dict[str, Any] | None = None
    filename: str | None = None
    mediaType: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_file(cls, value: Any) -> Any:
        """Map a pre-v1.0 `{"file": {bytes|uri, name, mimeType}}` part to raw/url + filename + mediaType."""
        if not isinstance(value, dict) or "file" not in value:
            return value
        value = dict(value)
        file = value.pop("file")
        if isinstance(file, dict):
            if file.get("bytes") is not None:
                value.setdefault("raw", file["bytes"])
            elif file.get("uri") is not None:
                value.setdefault("url", file["uri"])
            if file.get("name") is not None:
                value.setdefault("filename", file["name"])
            if file.get("mimeType") is not None:
                value.setdefault("mediaType", file["mimeType"])
        return value

    @model_validator(mode="after")
    def _exactly_one_content(self) -> "Part":
        present = [name for name in ("text", "raw", "url", "data") if getattr(self, name) is not None]
        if len(present) != 1:
            raise ValueError("A Part must carry exactly one of 'text', 'raw', 'url' or 'data'")
        return self


class Message(BaseModel):
    messageId: str = Field(default_factory=lambda: uuid4().hex)
    contextId: str | None = None
    taskId: str | None = None
    role: Role
    parts: List[Part] = Field(min_length=1)
    metadata: dict[str, Any] | None = None
    extensions: List[str] | None = None
    referenceTaskIds: List[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        value = dict(value)
        if isinstance(value.get("role"), str) and value["role"] in _LEGACY_ROLES:
            value["role"] = _LEGACY_ROLES[value["role"]].value
        if value.get("messageId") is None:
            value.pop("messageId", None)
        return value


class TaskStatus(BaseModel):
    state: TaskState
    message: Message | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "state" not in value:
            return value
        return {**value, "state": _normalize_task_state(value["state"])}

    @field_serializer("timestamp")
    def serialize_dt(self, dt: datetime, _info):
        """ISO-8601 UTC with a `Z` suffix (naive legacy values are taken as UTC)."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class Artifact(BaseModel):
    artifactId: str = Field(default_factory=lambda: uuid4().hex)
    name: str | None = None
    description: str | None = None
    parts: List[Part]
    metadata: dict[str, Any] | None = None
    extensions: List[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy(cls, value: Any) -> Any:
        """Legacy `index`/`append`/`lastChunk` are ignored (extra keys); a missing/None artifactId is generated."""
        if isinstance(value, dict) and value.get("artifactId") is None:
            value = {k: v for k, v in value.items() if k != "artifactId"}
        return value


class Task(BaseModelWithSQLSupport, table=True):
    __tablename__ = "a2a_tasks"

    id: str | None = SQLField(default=None, primary_key=True)
    contextId: str | None = None
    # DB column mapping for legacy rows only; read sites use `task.contextId or task.sessionId`.
    sessionId: str | None = SQLField(default=None, exclude=True)
    # Ownership scoping (server-side only; never serialized to clients).
    assistant_id: str | None = SQLField(default=None, index=True, exclude=True)
    user_id: str | None = SQLField(default=None, index=True, exclude=True)
    status: TaskStatus = SQLField(sa_column=Column(PydanticType(TaskStatus)))
    artifacts: List[Artifact] | None = SQLField(default=None, sa_column=Column(PydanticListType(Artifact)))
    history: List[Message] | None = SQLField(default=None, sa_column=Column(PydanticListType(Message)))

    # `metadata` is reserved by SQLModel (table metadata), so the wire field lives in a private attribute.
    _wire_metadata: dict[str, Any] | None = PrivateAttr(default=None)

    @model_validator(mode="wrap")
    @classmethod
    def _capture_metadata(cls, value: Any, handler):
        wire_metadata = None
        if isinstance(value, dict) and "metadata" in value:
            value = dict(value)
            wire_metadata = value.pop("metadata")
        task = handler(value)
        if wire_metadata is not None and isinstance(wire_metadata, dict):
            task._wire_metadata = wire_metadata
        return task

    @property
    def wire_metadata(self) -> dict[str, Any] | None:
        # Rows loaded by SQLAlchemy bypass __init__, so pydantic private state may not exist.
        private = getattr(self, "__pydantic_private__", None)
        return private.get("_wire_metadata") if private else None

    @model_serializer(mode="wrap")
    def _serialize_wire(self, handler):
        """Server-only bookkeeping columns are never part of the wire Task; legacy sessionId fills contextId."""
        data = handler(self)
        if not data.get("contextId") and self.sessionId:
            data["contextId"] = self.sessionId
        if self.wire_metadata:
            data["metadata"] = self.wire_metadata
        for key in ("date", "update_date"):
            data.pop(key, None)
        for key in ("artifacts", "history"):
            if data.get(key) == []:
                data.pop(key)
        return data


class TaskStatusUpdateEvent(BaseModel):
    taskId: str
    contextId: str
    status: TaskStatus
    metadata: dict[str, Any] | None = None


class TaskArtifactUpdateEvent(BaseModel):
    taskId: str
    contextId: str
    artifact: Artifact
    append: bool | None = None
    lastChunk: bool | None = None
    metadata: dict[str, Any] | None = None


class _ExactlyOne(BaseModel):
    """A proto `oneof` object: exactly one member is set."""

    @model_validator(mode="after")
    def _exactly_one_member(self):
        present = [name for name in type(self).model_fields if getattr(self, name) is not None]
        if len(present) != 1:
            raise ValueError(f"Exactly one of {list(type(self).model_fields)} must be set, got {present}")
        return self


class SendMessageResponse(_ExactlyOne):
    task: Task | None = None
    message: Message | None = None


class StreamResponse(_ExactlyOne):
    task: Task | None = None
    message: Message | None = None
    statusUpdate: TaskStatusUpdateEvent | None = None
    artifactUpdate: TaskArtifactUpdateEvent | None = None


class AuthenticationInfo(BaseModel):
    scheme: str
    credentials: str | None = None


class TaskPushNotificationConfig(BaseModel):
    tenant: str | None = None
    id: str | None = None
    taskId: str | None = None
    url: str
    token: str | None = None
    authentication: AuthenticationInfo | None = None


class SendMessageConfiguration(BaseModel):
    acceptedOutputModes: List[str] | None = None
    taskPushNotificationConfig: TaskPushNotificationConfig | None = None
    historyLength: int | None = None
    returnImmediately: bool | None = None


class SendMessageRequest(BaseModel):
    """`SendMessage` / `SendStreamingMessage` params (proto `SendMessageRequest`)."""

    tenant: str | None = None
    message: Message
    configuration: SendMessageConfiguration | None = None
    metadata: dict[str, Any] | None = None


class GetTaskRequest(BaseModel):
    tenant: str | None = None
    id: str
    historyLength: int | None = None


class CancelTaskRequest(BaseModel):
    tenant: str | None = None
    id: str
    metadata: dict[str, Any] | None = None


class GetExtendedAgentCardRequest(BaseModel):
    tenant: str | None = None


class JSONRPCMessage(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str | None = Field(default_factory=lambda: uuid4().hex)


class JSONRPCRequest(JSONRPCMessage):
    method: str
    params: dict[str, Any] | None = None


class JSONRPCError(BaseModel):
    code: int
    message: str
    # Array of typed detail objects, each carrying an `@type` key (google.rpc.ErrorInfo / BadRequest).
    data: List[dict[str, Any]] | None = None


class JSONRPCResponse(JSONRPCMessage):
    result: Any | None = None
    error: JSONRPCError | None = None


class SendMessageRPCRequest(JSONRPCRequest):
    method: Literal["SendMessage"] = "SendMessage"
    params: SendMessageRequest


class SendMessageRPCResponse(JSONRPCResponse):
    result: SendMessageResponse | None = None


class SendStreamingMessageRPCRequest(JSONRPCRequest):
    method: Literal["SendStreamingMessage"] = "SendStreamingMessage"
    params: SendMessageRequest


class StreamRPCResponse(JSONRPCResponse):
    result: StreamResponse | None = None


class GetTaskRPCRequest(JSONRPCRequest):
    method: Literal["GetTask"] = "GetTask"
    params: GetTaskRequest


class GetTaskRPCResponse(JSONRPCResponse):
    result: Task | None = None


class CancelTaskRPCRequest(JSONRPCRequest):
    method: Literal["CancelTask"] = "CancelTask"
    params: CancelTaskRequest


class CancelTaskRPCResponse(JSONRPCResponse):
    result: Task | None = None


class GetExtendedAgentCardRPCRequest(JSONRPCRequest):
    method: Literal["GetExtendedAgentCard"] = "GetExtendedAgentCard"
    params: dict[str, Any] | None = None


A2ARequest = TypeAdapter(
    Annotated[
        Union[
            SendMessageRPCRequest,
            SendStreamingMessageRPCRequest,
            GetTaskRPCRequest,
            CancelTaskRPCRequest,
            GetExtendedAgentCardRPCRequest,
        ],
        Field(discriminator="method"),
    ]
)

_ERROR_DOMAIN = "a2a-protocol.org"


def error_info(reason: str, **metadata: Any) -> dict[str, Any]:
    """Build a `google.rpc.ErrorInfo` error detail object."""
    detail: dict[str, Any] = {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": reason,
        "domain": _ERROR_DOMAIN,
    }
    if metadata:
        detail["metadata"] = {key: str(value) for key, value in metadata.items()}
    return detail


def bad_request(violations: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """Build a `google.rpc.BadRequest` error detail object from (field, description) pairs."""
    return {
        "@type": "type.googleapis.com/google.rpc.BadRequest",
        "fieldViolations": [{"field": field, "description": description} for field, description in violations],
    }


def _error_detail(reason: str, **metadata: Any):
    return Field(default_factory=lambda: [error_info(reason, **metadata)])


## Error types
class JSONParseError(JSONRPCError):
    code: int = -32700
    message: str = "Invalid JSON payload"
    data: List[dict[str, Any]] | None = _error_detail("JSON_PARSE_ERROR")


class InvalidRequestError(JSONRPCError):
    code: int = -32600
    message: str = "Request payload validation error"
    data: List[dict[str, Any]] | None = _error_detail("INVALID_REQUEST")


class MethodNotFoundError(JSONRPCError):
    code: int = -32601
    message: str = "Method not found"
    data: List[dict[str, Any]] | None = _error_detail("METHOD_NOT_FOUND")


class InvalidParamsError(JSONRPCError):
    code: int = -32602
    message: str = "Invalid parameters"


class InternalError(JSONRPCError):
    code: int = -32603
    message: str = "Internal error"
    data: List[dict[str, Any]] | None = _error_detail("INTERNAL_ERROR")


class TaskNotFoundError(JSONRPCError):
    code: int = -32001
    message: str = "Task not found"
    data: List[dict[str, Any]] | None = _error_detail("TASK_NOT_FOUND")


class TaskNotCancelableError(JSONRPCError):
    code: int = -32002
    message: str = "Task cannot be canceled"
    data: List[dict[str, Any]] | None = _error_detail("TASK_NOT_CANCELABLE")


class PushNotificationNotSupportedError(JSONRPCError):
    code: int = -32003
    message: str = "Push Notification is not supported"
    data: List[dict[str, Any]] | None = _error_detail("PUSH_NOTIFICATION_NOT_SUPPORTED")


class UnsupportedOperationError(JSONRPCError):
    code: int = -32004
    message: str = "This operation is not supported"
    data: List[dict[str, Any]] | None = _error_detail("UNSUPPORTED_OPERATION")


class ContentTypeNotSupportedError(JSONRPCError):
    code: int = -32005
    message: str = "Incompatible content types"
    data: List[dict[str, Any]] | None = _error_detail("CONTENT_TYPE_NOT_SUPPORTED")


class VersionNotSupportedError(JSONRPCError):
    code: int = -32009
    message: str = "A2A protocol version not supported"
    data: List[dict[str, Any]] | None = _error_detail("VERSION_NOT_SUPPORTED", supportedVersions=A2A_PROTOCOL_VERSION)


class AgentProvider(BaseModel):
    organization: str
    url: str | None = None


class AgentExtension(BaseModel):
    uri: str | None = None
    description: str | None = None
    required: bool | None = None
    params: dict[str, Any] | None = None


class AgentCapabilities(BaseModel):
    streaming: bool = False
    pushNotifications: bool = False
    extensions: List[AgentExtension] | None = None
    extendedAgentCard: bool = False


def _normalize_modes(modes: Any) -> Any:
    """Legacy media-type shorthand `text` becomes `TEXT_PLAIN`."""
    if isinstance(modes, list):
        return [TEXT_PLAIN if mode == "text" else mode for mode in modes]
    return modes


class AgentSkill(BaseModel):
    id: str
    name: str
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    examples: List[str] | None = None
    inputModes: List[str] | None = None
    outputModes: List[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for key in ("description", "tags"):
            if data.get(key) is None:
                data.pop(key, None)
        for key in ("inputModes", "outputModes"):
            if key in data:
                data[key] = _normalize_modes(data[key])
        return data


class AgentInterface(BaseModel):
    url: str
    protocolBinding: str
    protocolVersion: str = A2A_PROTOCOL_VERSION
    tenant: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy(cls, data: Any) -> Any:
        """Legacy interface `transport` maps to `protocolBinding`."""
        if not isinstance(data, dict) or "transport" not in data:
            return data
        data = dict(data)
        transport = data.pop("transport")
        data.setdefault("protocolBinding", transport)
        return data


class StringList(BaseModel):
    list: List[str] = Field(default_factory=lambda: [])


class SecurityRequirement(BaseModel):
    schemes: dict[str, StringList]


class SecurityScheme(BaseModel):
    """A proto `oneof`: exactly one scheme member is set (ProtoJSON member names)."""

    apiKeySecurityScheme: dict[str, Any] | None = None
    httpAuthSecurityScheme: dict[str, Any] | None = None
    oauth2SecurityScheme: dict[str, Any] | None = None
    openIdConnectSecurityScheme: dict[str, Any] | None = None
    mtlsSecurityScheme: dict[str, Any] | None = None


class AgentCardSignature(BaseModel):
    protected: str
    signature: str
    header: dict[str, Any] | None = None


# CodeMie-internal fields kept on the stored/remote card model; never part of the public card JSON.
INTERNAL_CARD_FIELDS = ("project_name", "integration_id", "user_id", "bedrock_agentcore", "url")


def _legacy_security_scheme(scheme: Any) -> dict[str, Any] | None:
    """Map a pre-v1.0 `{"type": ...}` security scheme to the ProtoJSON oneof form (None when unmappable)."""
    if not isinstance(scheme, dict):
        return None
    if any(key.endswith("SecurityScheme") for key in scheme):
        return scheme
    kind = scheme.get("type")
    description = scheme.get("description")
    if kind == "http" and scheme.get("scheme"):
        body = {"scheme": scheme["scheme"], "bearerFormat": scheme.get("bearerFormat")}
        return {"httpAuthSecurityScheme": {**body, "description": description}}
    if kind == "apiKey" and scheme.get("name") and scheme.get("in"):
        return {"apiKeySecurityScheme": {"location": scheme["in"], "name": scheme["name"], "description": description}}
    if kind == "openIdConnect" and scheme.get("openIdConnectUrl"):
        return {
            "openIdConnectSecurityScheme": {"openIdConnectUrl": scheme["openIdConnectUrl"], "description": description}
        }
    if kind == "oauth2":
        return {"oauth2SecurityScheme": {"flows": scheme.get("flows") or {}, "description": description}}
    if kind == "mutualTLS":
        return {"mtlsSecurityScheme": {"description": description}}
    return None


def _drop_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _legacy_security_schemes(schemes: Any) -> dict[str, Any]:
    if not isinstance(schemes, dict):
        return {}
    result: dict[str, Any] = {}
    for name, scheme in schemes.items():
        if isinstance(scheme, SecurityScheme):
            result[name] = scheme
            continue
        converted = _legacy_security_scheme(scheme)
        if converted is not None:
            result[name] = {member: _drop_none(body) for member, body in converted.items()}
    return result


def _legacy_security_requirements(security: Any) -> list[dict[str, Any]]:
    if not isinstance(security, list):
        return []
    return [
        {"schemes": {name: {"list": list(scopes or [])} for name, scopes in requirement.items()}}
        for requirement in security
        if isinstance(requirement, dict)
    ]


class AgentCard(BaseModel):
    name: str
    description: str = ""
    supportedInterfaces: List[AgentInterface]
    provider: AgentProvider | None = None
    version: str
    documentationUrl: str | None = None
    capabilities: AgentCapabilities
    securitySchemes: dict[str, SecurityScheme] | None = None
    securityRequirements: List[SecurityRequirement] | None = None
    defaultInputModes: List[str] = Field(default_factory=lambda: [TEXT_PLAIN])
    defaultOutputModes: List[str] = Field(default_factory=lambda: [TEXT_PLAIN])
    skills: List[AgentSkill]
    signatures: List[AgentCardSignature] | None = None
    iconUrl: str | None = None
    project_name: str | None = None
    integration_id: str | None = None
    user_id: str | None = None
    bedrock_agentcore: bool = False
    url: str | None = None
    """Back-compat top-level URL (codemie-ui's remote-assistant edit form reads `agent_card.url`)."""

    @model_validator(mode="after")
    def _set_url(self) -> Self:
        if self.url is None:
            with contextlib.suppress(AgentCardInterfaceError):
                self.url = get_jsonrpc_interface(self).url
        return self

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy(cls, data: Any) -> Any:
        """Map a pre-v1.0 (v0.3-style) card onto the v1.0 shape; never raises on legacy input."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        legacy_version = data.pop("protocolVersion", None)
        data.pop("authentication", None)
        data = cls._normalize_interfaces(data, legacy_version)
        if data.get("description") is None:
            data.pop("description", None)
        for key in ("defaultInputModes", "defaultOutputModes"):
            if key in data:
                data[key] = _normalize_modes(data[key])
        if data.pop("supportsAuthenticatedExtendedCard", None) and isinstance(data.get("capabilities"), dict):
            data["capabilities"] = {**data["capabilities"], "extendedAgentCard": True}
        if "securitySchemes" in data:
            data["securitySchemes"] = _legacy_security_schemes(data["securitySchemes"])
        legacy_security = data.pop("security", None)
        if legacy_security and not data.get("securityRequirements"):
            data["securityRequirements"] = _legacy_security_requirements(legacy_security)
        return data

    @staticmethod
    def _normalize_interfaces(data: dict[str, Any], legacy_version: str | None) -> dict[str, Any]:
        """Map a pre-v1.0 `url` (+ preferredTransport, additionalInterfaces) to supportedInterfaces."""
        url = data.pop("url", None)
        transport = data.pop("preferredTransport", None) or "JSONRPC"
        additional = data.pop("additionalInterfaces", None) or []
        built_from_legacy_url = bool(url) and not data.get("supportedInterfaces")
        if built_from_legacy_url:
            interfaces = [{"url": url, "protocolBinding": transport}]
            interfaces += [
                {"url": i["url"], "protocolBinding": i.get("protocolBinding") or i["transport"]}
                for i in additional
                if isinstance(i, dict) and i.get("url") and (i.get("protocolBinding") or i.get("transport"))
            ]
            data["supportedInterfaces"] = interfaces
        # a2a-sdk 1.x cards carry a v0.3 compat `protocolVersion` next to real v1.0 interfaces; only stamp it
        # onto interfaces synthesised from a legacy `url`.
        if built_from_legacy_url:
            data["supportedInterfaces"] = [
                {"protocolVersion": legacy_version or LEGACY_PROTOCOL_VERSION, **i} if isinstance(i, dict) else i
                for i in data["supportedInterfaces"]
            ]
        return data


class AgentCardInterfaceError(ValueError):
    """Raised when an agent card declares no usable JSON-RPC interface."""


def get_jsonrpc_interface(card: AgentCard) -> AgentInterface:
    """Return the JSON-RPC interface of an agent card (binding matched case-insensitively)."""
    for interface in card.supportedInterfaces or []:
        if interface.protocolBinding.upper() == "JSONRPC" and interface.url:
            return interface
    raise AgentCardInterfaceError("Agent card has no JSON-RPC interface")


def get_jsonrpc_url(card: AgentCard) -> str:
    """Return the JSON-RPC interface URL of an agent card."""
    return get_jsonrpc_interface(card).url


class GetExtendedAgentCardRPCResponse(JSONRPCResponse):
    # The public card as a dict: CodeMie-internal AgentCard fields must not reach the wire.
    result: dict[str, Any] | None = None


class A2AClientError(Exception):
    pass


class A2AProtocolVersionError(A2AClientError):
    """The remote agent does not speak the A2A protocol version this backend implements."""


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
    """A2A JSON-RPC request body (Swagger documentation only; the endpoint parses the raw body itself)."""

    jsonrpc: str = Field("2.0", description="JSON-RPC version")
    id: str | int = Field("request-123", description="Request identifier")
    method: str = Field(
        "SendMessage",
        description="Method name: SendMessage, SendStreamingMessage, GetTask, CancelTask or GetExtendedAgentCard",
    )
    params: Dict[str, Any] = Field(
        {
            "message": {
                "messageId": "message-123",
                "role": "ROLE_USER",
                "parts": [{"text": "Hello, can you help me with this question?"}],
            },
            "configuration": {"acceptedOutputModes": [TEXT_PLAIN], "historyLength": 10},
        },
        description="Method parameters",
    )
