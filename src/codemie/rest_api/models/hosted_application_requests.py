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

"""Strict public request shapes; owner, storage, and policy fields are server-owned."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from codemie.core.hosted_applications.paths import canonical_artifact_path, normalize_name


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShareUsersRequest(RequestModel):
    """Named view-only grantees to add or remove; nothing but ids is accepted (no permission level)."""

    user_ids: list[StrictStr] = Field(min_length=1, max_length=100)


class ShareLinkOpenRequest(RequestModel):
    """The opaque share-link token, carried in the body so it never appears in a path or log."""

    token: StrictStr = Field(min_length=1, max_length=512)


def _plain(value: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError("Text is required")
    try:
        normalized = normalize_name(value, maximum=limit)[0]
    except Exception as exc:
        raise ValueError("Invalid text") from exc
    if not normalized or len(normalized.encode()) > limit * 4:
        raise ValueError("Text is required")
    return normalized


class AppCreate(RequestModel):
    name: StrictStr
    description: StrictStr
    project: StrictStr
    # Shared with every member of ``project``; creating needs project access, so nothing more is checked here.
    shared: StrictBool = False

    @field_validator("project")
    @classmethod
    def project_name(cls, value):
        return _plain(value, 255)

    @field_validator("name")
    @classmethod
    def name_text(cls, value):
        return _plain(value, 120)

    @field_validator("description")
    @classmethod
    def description_text(cls, value):
        if not isinstance(value, str):
            raise ValueError("Description is required")
        normalized = unicodedata.normalize("NFC", value).strip()
        if (
            not normalized
            or len(normalized) > 2000
            or len(normalized.encode()) > 8000
            or any(unicodedata.category(c).startswith("C") and c not in "\n\r\t" for c in normalized)
        ):
            raise ValueError("Description must contain 1–2000 printable characters")
        return normalized


class AppPatch(AppCreate):
    name: StrictStr | None = None
    description: StrictStr | None = None
    project: StrictStr | None = None
    shared: StrictBool | None = None

    @model_validator(mode="after")
    def has_changes(self):
        if not self.model_fields_set or any(
            getattr(self, key) is None
            for key in ("name", "description", "project", "shared")
            if key in self.model_fields_set
        ):
            raise ValueError("At least one metadata change is required")
        return self


class VersionCreate(RequestModel):
    filename: StrictStr
    artifact_kind: Literal["html", "zip"]
    byte_size: StrictInt
    sha256: StrictStr | None = None
    routing_mode: Literal["static", "spa_history"] = "static"
    site_root_hint: StrictStr | None = None
    entrypoint_hint: StrictStr | None = None

    @model_validator(mode="after")
    def artifact(self):
        maximum = (20 if self.artifact_kind == "html" else 100) * 1024 * 1024
        try:
            filename = canonical_artifact_path(self.filename)
            if "/" in filename or len(filename.encode()) > 255:
                raise ValueError("Invalid filename")
            for hint in (self.site_root_hint, self.entrypoint_hint):
                if hint:
                    canonical_artifact_path(hint)
        except Exception as exc:
            raise ValueError("Invalid artifact path") from exc
        suffix = filename.lower()
        if not suffix.endswith((".html", ".htm") if self.artifact_kind == "html" else (".zip",)):
            raise ValueError("Artifact filename does not match its format")
        if not 1 <= self.byte_size <= maximum:
            raise ValueError("Artifact size exceeds its limit")
        if self.sha256 is not None and not re.fullmatch("[0-9a-f]{64}", self.sha256):
            raise ValueError("Invalid artifact checksum")
        return self


class UploadReset(RequestModel):
    generation: StrictInt


class UploadComplete(UploadReset):
    upload_id: UUID
    sha256: StrictStr

    @field_validator("sha256")
    @classmethod
    def digest(cls, value):
        if not re.fullmatch("[0-9a-f]{64}", value):
            raise ValueError("Invalid artifact checksum")
        return value


class VersionConfiguration(RequestModel):
    site_root: StrictStr
    entrypoint_path: StrictStr
    routing_mode: Literal["static", "spa_history"]
    candidate_manifest_digest: StrictStr
    configuration_revision: StrictInt


class VersionSelection(RequestModel):
    version_id: UUID


class SecurityReason(RequestModel):
    reason: StrictStr
    version_id: UUID | None = None
    delete_previous: StrictBool = False

    @field_validator('reason')
    @classmethod
    def validate_reason(cls, value):
        return _plain(value, 1000)
