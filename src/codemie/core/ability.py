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

from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm.exc import DetachedInstanceError

if TYPE_CHECKING:
    from codemie.rest_api.security.user import User

# these cannot be edited
REMOTE_ENTITIES = ["Assistant", "WorkflowConfig", "Guardrail", "IndexInfo"]


class Action(Enum):
    """Enum to define the Ability actions"""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"


_LEVEL_ORDER = (Action.READ, Action.WRITE, Action.DELETE)


def levels_covering(action: Action) -> set[Action]:
    """Permission levels whose grant covers the action (READ < WRITE < DELETE)."""
    return set(_LEVEL_ORDER[_LEVEL_ORDER.index(action) :])


@dataclass(frozen=True)
class PreloadedGrants:
    """Highest named-grant level per resource id, preloaded for one class (``class_name``)."""

    class_name: str
    levels: dict[str, Action]


def preloaded_grant_covers(grants: PreloadedGrants | None, resource_id: Any, action: Action = Action.READ) -> bool:
    """The Python grant term: a loaded level covering the action; no grants is no grant (fail closed)."""
    if grants is None or resource_id is None:
        return False
    return grants.levels.get(str(resource_id)) in levels_covering(action)


# (user, class name) -> that user's live named grants of the class, or None when the class has no named grants.
# A dotted "module:attribute" path imported on first use, so ``core`` imports no service module. It reads through
# the platform's sync engine (one statement), because ``Ability`` checks are synchronous wherever they run.
GRANT_LOADER = "codemie.service.permission.named_grant_service:load_class_grants"


def load_class_grants(user: User, class_name: str) -> PreloadedGrants | None:
    module_name, attribute = GRANT_LOADER.split(":")
    return getattr(importlib.import_module(module_name), attribute)(user, class_name)


class Role(Enum):
    """Enum to define the Ability roles"""

    ANYONE = "anyone"
    ADMIN = "admin"
    MANAGED_BY = "managed_by"
    OWNED_BY = "owned_by"
    SHARED_WITH = "shared_with"
    # A named grant whose level covers the action being checked (the only action-aware role). Listed last in each
    # action's roles, so an owner, manager, admin or project viewer never causes a grant read.
    GRANTED = "granted"


class Owned(ABC):
    """Abstract class to define methods for checking ownership"""

    def is_granted_to(self, user: User, action: Action, grants: PreloadedGrants | None) -> bool:  # NOSONAR: S1172
        """Whether ``grants`` (the user's grants of this class) cover ``action`` for ``user``; none by default."""
        return False

    @abstractmethod
    def is_owned_by(self, user: User) -> bool:
        pass

    @abstractmethod
    def is_managed_by(self, user: User) -> bool:
        pass

    @abstractmethod
    def is_shared_with(self, user: User) -> bool:
        pass


class NotOwnedClassException(Exception):
    """Exception to raise when the class is not inherited from Owned class"""

    pass


class UnregisteredPermissionsException(Exception):
    """Exception to raise when the class is not registered in PERMISSIONS"""

    pass


class Ability:
    """
    Responsible for checking the permissions for the user for class instance

    Usage:
    1. Register the permissions in PERMISSIONS dictionary
       Example: PERMISSIONS = {
            "Assistant": {
                Action.READ: [Role.ANYONE, Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
                Action.WRITE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
                Action.DELETE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            },
        }
    2. Create an instance of the Ability class to check the permissions
        ability = Ability(user)
        ability.can(Action.READ, assistant_instance)

    Named grants (``Role.GRANTED``) need no preparation by the caller: the first check that reaches the role for an
    instance class loads all of the user's live grants of that class in one query and keeps them on this object
    (one request; nothing is cached across requests). ``Ability(user, grants=...)`` passes grants a caller already
    read (hosted applications read them on their own async pool); they count as loaded for their class.
    """

    PERMISSIONS = {
        "Assistant": {
            Action.READ: [Role.SHARED_WITH, Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN, Role.GRANTED],
            Action.WRITE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.DELETE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
        },
        "Conversation": {
            Action.READ: [Role.SHARED_WITH, Role.OWNED_BY],
            Action.WRITE: [Role.OWNED_BY],
            Action.DELETE: [Role.OWNED_BY],
        },
        "WorkflowConfig": {
            Action.READ: [Role.SHARED_WITH, Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.WRITE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.DELETE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
        },
        "IndexInfo": {
            Action.READ: [Role.SHARED_WITH, Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.WRITE: [Role.SHARED_WITH, Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.DELETE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
        },
        "ConversationFolder": {
            Action.READ: [Role.OWNED_BY, Role.ADMIN],
            Action.WRITE: [Role.OWNED_BY, Role.ADMIN],
            Action.DELETE: [Role.OWNED_BY, Role.ADMIN],
        },
        "WorkflowExecution": {
            Action.READ: [Role.SHARED_WITH, Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.WRITE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.DELETE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
        },
        "UserSetting": {
            Action.READ: [Role.OWNED_BY],
            Action.WRITE: [Role.OWNED_BY],
            Action.DELETE: [Role.OWNED_BY],
        },
        "ProjectSetting": {
            Action.READ: [Role.SHARED_WITH, Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.WRITE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.DELETE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
        },
        "Permission": {
            Action.READ: [Role.ANYONE, Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.WRITE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.DELETE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
        },
        "Guardrail": {
            Action.READ: [Role.SHARED_WITH, Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.WRITE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.DELETE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
        },
        "GuardrailAssignment": {
            Action.READ: [Role.SHARED_WITH, Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.WRITE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.DELETE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
        },
        "Skill": {
            Action.READ: [Role.SHARED_WITH, Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.WRITE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.DELETE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
        },
        "Application": {
            Action.READ: [Role.SHARED_WITH, Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.WRITE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.DELETE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
        },
        "HostedApplications": {
            # Edit access for named people is GRANTED under WRITE plus access.MANAGER_GRANT_ACTIONS (never DELETE).
            Action.READ: [Role.SHARED_WITH, Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN, Role.GRANTED],
            Action.WRITE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.DELETE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
        },
    }

    def __init__(self, user: User, *, grants: PreloadedGrants | None = None):
        self.user = user
        # The user's named grants per instance class, loaded on first need (``grants_for``); passed grants count
        # as loaded for their class.
        self._grants: dict[str, PreloadedGrants | None] = {}
        if grants is not None:
            self._grants[grants.class_name] = grants

    def can(self, action: Action, instance: Owned, check_resource_permissions: bool = False):
        """Check if the user has permission for the action on the instance"""
        self._check_instance(instance)

        # Check if this is a WRITE action on a remote entity - such entity cannot be edited
        if action == Action.WRITE and self._is_remote_entity(instance):
            return False

        # Check if this is a WRITE action on a platform index - such entity cannot be edited
        if action == Action.WRITE and self._is_platform_index(instance):
            return False

        if check_resource_permissions:
            from codemie.rest_api.models.permission import Permission as ResourcePermission

            if ResourcePermission.exists_for(user=self.user, instance=instance, action=action):
                return True

        permissions = self.PERMISSIONS[self.klass_name(instance)][action]

        return any(self._holds(role, action, instance) for role in permissions)

    def _holds(self, role: Role, action: Action, instance: Owned) -> bool:
        if role is Role.GRANTED:
            return self.granted(instance, action)
        return getattr(self, role.value)(instance)

    def list(self, instance: Owned):
        """List all the actions that the user can perform on the instance"""
        self._check_instance(instance)

        actions = self.PERMISSIONS[self.klass_name(instance)]
        allowed_actions = []

        for action in actions:
            if self.can(action, instance):
                allowed_actions.append(action)

        return allowed_actions

    def klass_name(self, instance):
        name = instance.__class__.__name__
        return name.removesuffix('Elastic').removesuffix('SQL')

    def anyone(self, *args):  # NOSONAR: S1172 - Unused method parameter
        return True

    def admin(self, *args):  # NOSONAR: S1172 - Unused method parameter
        return self.user.is_admin_or_maintainer

    def owned_by(self, instance):
        return instance.is_owned_by(self.user)

    def managed_by(self, instance):
        return instance.is_managed_by(self.user)

    def shared_with(self, instance):
        return instance.is_shared_with(self.user)

    def granted(self, instance, action: Action) -> bool:
        """A named grant of the user on the instance whose level covers ``action``."""
        return instance.is_granted_to(self.user, action, self.grants_for(self.klass_name(instance)))

    def grants_for(self, class_name: str) -> PreloadedGrants | None:
        """The user's live grants of ``class_name``: passed in, or loaded once (one query) and kept on this object."""
        if class_name not in self._grants:
            self._grants[class_name] = load_class_grants(self.user, class_name)
        return self._grants[class_name]

    def _check_instance(self, instance: Owned):
        """
        Check if the instance is inherited from Owned class
        and registered in PERMISSIONS
        """
        klass_name = self.klass_name(instance)

        if not isinstance(instance, Owned):
            raise NotOwnedClassException(f"{klass_name} should be inherited from Owned class")

        if klass_name not in self.PERMISSIONS:
            raise UnregisteredPermissionsException(f"Please register permissions for {klass_name}")

    def _is_remote_entity(self, instance: Owned):
        """
        Check if the instance is "remote entity", meaning it cannot be edited.
        """
        entity_class = self.klass_name(instance)

        if entity_class not in REMOTE_ENTITIES:
            return False

        try:
            return self._has_remote_attributes(instance)
        except DetachedInstanceError:
            return self._check_remote_on_reloaded_instance(instance)
        except Exception:
            return False

    def _has_remote_attributes(self, instance: Owned) -> bool:
        """Check if instance has Bedrock-related attributes."""
        return (
            getattr(instance, "bedrock", None) is not None
            or getattr(instance, "bedrock_agentcore_runtime", None) is not None
        )

    def _check_remote_on_reloaded_instance(self, instance: Owned) -> bool:
        """Reload instance and check for remote attributes."""
        try:
            instance_id = instance.id  # type: ignore
            if not instance_id:
                return False

            fresh_instance = type(instance).get_by_id(instance_id)  # type: ignore
            if fresh_instance:
                return self._has_remote_attributes(fresh_instance)
        except Exception:
            pass

        return False

    def _is_platform_index(self, instance: Owned):
        """
        Check if the instance is a platform index (e.g., marketplace assistants).
        Platform indexes cannot be edited directly as they are managed by the system.
        """
        entity_class = self.klass_name(instance)

        # Only check IndexInfo instances
        if entity_class != "IndexInfo":
            return False

        try:
            # Use the existing is_platform_index() method from IndexInfo
            if hasattr(instance, 'is_platform_index') and callable(getattr(instance, 'is_platform_index')):
                return instance.is_platform_index()
        except DetachedInstanceError:
            # If we get DetachedInstanceError, we try to reload the object
            try:
                instance_id = instance.id  # type: ignore
                if not instance_id:
                    return False

                fresh_instance = type(instance).get_by_id(instance_id)  # type: ignore
                if fresh_instance and hasattr(fresh_instance, 'is_platform_index'):
                    return fresh_instance.is_platform_index()
            except Exception:
                # If reload fails, assume not platform
                pass
        except Exception:
            # For other exceptions, assume not platform
            pass

        return False
