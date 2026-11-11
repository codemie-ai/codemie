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

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field, computed_field, model_validator

from codemie.core.constants import Environment, DEMO_PROJECT
from codemie.core.models import UserEntity
from codemie.configs import config, logger
from codemie.rest_api.security.permissions import is_admin_or_maintainer

DEMO_USER_ROLE = "demo_user"

USER_ID_HEADER = "user-id"
AUTHORIZATION_HEADER = "Authorization"


def same_project_name(a: str | None, b: str | None) -> bool:
    """Project names compare case-insensitively; empty values never match (EPMCDME-16625)."""
    return bool(a) and bool(b) and a.casefold() == b.casefold()


def resolve_default_project(project_names: Iterable[str], stored_default: str | None, email: str | None) -> str | None:
    """Return the user's effective default project, or None when the user has no projects.

    The stored default wins; without one, the personal project (named after the email) keeps
    unbound spend on the personal budget, and the first project by name is the last resort.
    The fallback is never persisted, so every reader must derive it here: the UI then always
    shows exactly one default and runtime routing follows what it shows (EPMCDME-15738).
    """
    if stored_default:
        return stored_default
    names = set(project_names)
    if not names:
        return None
    personal = next((name for name in sorted(names) if same_project_name(name, email)), None)
    if personal:
        return personal
    return min(names)


def idp_workspace_name(project_names: Iterable[str], username: str | None) -> str | None:
    """The username-named personal workspace IDP-claims mode creates when the claims list it (legacy_jwt)."""
    return username if username and username in set(project_names) else None


def derive_idp_default_project(project_names: Iterable[str], email: str | None, username: str | None) -> str | None:
    """IDP-claims mode stores no default: the email-named personal project, else the username workspace,
    else the first project by name (EPMCDME-16625 D8). Login and runs started without a request share it."""
    names = list(project_names)
    derived = resolve_default_project(names, None, email)
    if same_project_name(derived, email):
        return derived
    return idp_workspace_name(names, username) or derived


def personal_workspace_names(email: str | None, username: str | None, project_names: Iterable[str]) -> frozenset[str]:
    """Names that denote the user's own personal budget rather than a project budget (EPMCDME-16625).

    The email-named personal project always counts; in IDP-claims mode the username-named workspace does too.
    """
    names = {email} if email else set()
    if not config.ENABLE_USER_MANAGEMENT and (workspace := idp_workspace_name(project_names, username)):
        names.add(workspace)
    return frozenset(names)


def resolve_membership_default(memberships: Iterable[Any], email: str | None) -> str | None:
    """resolve_default_project over UserProject-like rows (``project_name``, ``is_default``)."""
    rows = list(memberships)
    stored_default = next((row.project_name for row in rows if row.is_default), None)
    return resolve_default_project((row.project_name for row in rows), stored_default, email)


def _mask_value(value: str | None) -> str | None:
    """Partially mask a free-text value for diagnostic logs (EPMCDME-13260).

    Falsy input (None / empty string) is returned unchanged. Values of two chars
    or fewer collapse to ``***``; longer values keep a two-char prefix.
    """
    if not value:
        return value
    return "***" if len(value) <= 2 else f"{value[:2]}***"


def _mask_email(value: str | None) -> str | None:
    """Mask an email to ``<first-local-char>***@<domain>`` for diagnostic logs.

    Falsy input is returned unchanged. A value with no ``@`` falls back to
    :func:`_mask_value`.
    """
    if not value:
        return value
    local, sep, domain = value.partition("@")
    if not sep:
        return _mask_value(value)
    return f"{(local[:1] or '')}***@{domain}"


class User(BaseModel):
    """User model for authentication context

    This model is populated from:
    - IDP JWT claims when ENABLE_USER_MANAGEMENT=False
    - Database tables when ENABLE_USER_MANAGEMENT=True
    """

    id: str
    username: str = ""
    name: str = ""
    email: str = ""
    roles: list = Field(default_factory=list)
    project_names: list[str] = Field(default_factory=lambda: ['demo'])
    admin_project_names: list[str] = Field(default_factory=list)
    default_project: str | None = Field(default=None)
    picture: str = ""
    knowledge_bases: list = Field(default_factory=list)
    user_type: str | None = 'regular'
    is_admin: bool = Field(default=False)
    is_maintainer: bool = Field(default=False)
    is_auditor: bool = Field(default=False)
    project_limit: int | None = Field(default=None)  # NULL = unlimited (admins); set from DB when flag ON
    auth_token: str | None = Field(None, exclude=True)
    # Client-scoped OAuth2 access token (e.g. BFF client-credentials). Never a user token.
    client_access_token: str | None = Field(None, exclude=True)
    tenant_id: str | None = Field(None, exclude=True)
    extra_attributes: dict[str, Any] | None = None

    @model_validator(mode='after')
    def resolve_is_admin(self) -> 'User':
        """Resolve is_admin at construction time.

        - ENV=local: Always True (dev override)
        - ENABLE_USER_MANAGEMENT=False: Legacy IDP role-based
        - ENABLE_USER_MANAGEMENT=True: Value passed from DB at construction
        """
        if Environment.LOCAL.value == config.ENV:
            self.is_admin = True
        elif not config.ENABLE_USER_MANAGEMENT:
            self.is_admin = (bool(config.ADMIN_USER_ID) and self.id == config.ADMIN_USER_ID) or (
                config.ADMIN_ROLE_NAME in self.roles
            )

        if self.is_maintainer:
            self.is_admin = True

        return self

    @computed_field
    @property
    def applications(self) -> list[str]:
        return self.project_names

    @computed_field
    @property
    def applications_admin(self) -> list[str]:
        return self.admin_project_names

    @property
    def full_name(self):
        return self.name or self.username or self.id

    @property
    def is_admin_or_maintainer(self) -> bool:
        return is_admin_or_maintainer(self)

    @property
    def is_applications_admin(self) -> bool:
        return len(self.admin_project_names) > 0 or self.is_admin_or_maintainer

    def is_application_admin(self, app_name: str) -> bool:
        return app_name in self.admin_project_names

    @property
    def is_demo_user(self) -> bool:
        return DEMO_USER_ROLE in self.roles

    @property
    def is_external_user(self) -> bool:
        """Check if user is external (temporary marketplace user)"""
        return self.user_type == config.EXTERNAL_USER_TYPE

    def effective_default_project(self) -> str | None:
        """The default project the UI shows and budget attribution tries first, or None without projects.

        IDP-claims mode stores no default, so it is derived as attribution derives it: the UI then shows the
        project that pays (EPMCDME-16625 AR-7; routing follows what the UI shows, EPMCDME-15738).
        """
        if config.ENABLE_USER_MANAGEMENT:
            return resolve_default_project(self.project_names, self.default_project, self.email)
        return self.default_project or derive_idp_default_project(
            self.project_names, self.email or None, self.username or None
        )

    @property
    def current_project(self) -> str:
        # Not effective_default_project(): asset-less calls use this as their credentials project, and credentials
        # keep their pre-EPMCDME-16625 choice (matrix row 18).
        return resolve_default_project(self.project_names, self.default_project, self.email) or DEMO_PROJECT

    def has_access_to_application(self, app_name: str) -> bool:
        return self.is_admin_or_maintainer or (app_name in self.project_names) or self.is_application_admin(app_name)

    def has_access_to_kb(self, name: str) -> bool:
        return self.is_admin_or_maintainer or (name in self.knowledge_bases)

    def as_user_model(self) -> UserEntity:
        return UserEntity(user_id=self.id, name=self.name, username=self.username)


def stand_in_user(user_id: str, *, component: str, auth_token: str | None = None) -> User:
    """A user known only by id: no memberships and no project-admin rights (EPMCDME-16625 AR-3).

    The model's ``['demo']`` default would count as a membership and bill the run to "demo"; without memberships
    budget attribution charges the personal budget.
    """
    logger.info(f"budget_event=stand_in_owner component={component} user_id={user_id!r}")
    return User(
        id=user_id, username=user_id, name=user_id, project_names=[], admin_project_names=[], auth_token=auth_token
    )


class UserContext(BaseModel):
    """Non-sensitive user profile forwarded to downstream tool invocations (DSP and MCP).

    Describes the authenticated initiator of the current request. Never includes
    credentials or internal identifiers (auth_token, tenant_id).
    """

    id: str | None = None
    username: str | None = None
    name: str | None = None
    email: str | None = None
    roles: list[str] | None = None
    is_admin: bool | None = None
    is_maintainer: bool | None = None
    is_auditor: bool | None = None
    user_type: str | None = None
    project_names: list[str] | None = None
    admin_project_names: list[str] | None = None
    knowledge_bases: list[str] | None = None
    picture: str | None = None
    extra_attributes: dict[str, Any] | None = None

    def masked_for_logging(self) -> dict[str, Any]:
        """PII-safe view of this context for diagnostic logs (EPMCDME-13260).

        email/name/picture/username are masked; extra_attributes and
        knowledge_bases collapse to a count-only placeholder; every other
        field passes through. None fields are omitted.
        """
        out: dict[str, Any] = {}
        for key in (
            "id",
            "roles",
            "is_admin",
            "is_maintainer",
            "is_auditor",
            "user_type",
            "project_names",
            "admin_project_names",
        ):
            val = getattr(self, key)
            if val is not None:
                out[key] = val
        if self.email is not None:
            out["email"] = _mask_email(self.email)
        if self.name is not None:
            out["name"] = _mask_value(self.name)
        if self.picture is not None:
            out["picture"] = _mask_value(self.picture)
        if self.username is not None:
            out["username"] = _mask_value(self.username)
        if self.extra_attributes is not None:
            out["extra_attributes"] = f"<redacted, {len(self.extra_attributes)} keys>"
        if self.knowledge_bases is not None:
            out["knowledge_bases"] = f"<redacted, {len(self.knowledge_bases)} items>"
        return out

    @classmethod
    def from_user(cls, user: "User") -> "UserContext":
        return cls(
            id=user.id,
            username=user.username,
            name=user.name,
            email=user.email,
            roles=user.roles,
            is_admin=user.is_admin,
            is_maintainer=user.is_maintainer,
            is_auditor=user.is_auditor,
            user_type=user.user_type,
            project_names=user.project_names,
            admin_project_names=user.admin_project_names,
            knowledge_bases=user.knowledge_bases,
            picture=user.picture,
            extra_attributes=user.extra_attributes,
        )
