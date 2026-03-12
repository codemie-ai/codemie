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

from __future__ import annotations

from typing import Type

from codemie.configs import config
from codemie.core.constants import IdentityProvider
from codemie.rest_api.security.idp.base import BaseIdp
from codemie.rest_api.security.idp.local import LocalIdp


class IdpFactory:
    """Factory for creating IDP instances.

    Uses a registry pattern — base registers LocalIdp, enterprise
    registers additional providers (Keycloak, OIDC) at startup.

    The registry is a class-level mutable dict. Enterprise modules
    call IdpFactory.register() during application startup to add
    their providers before any authentication requests are processed.
    """

    # Base package only registers LocalIdp
    _idp_registry: dict[str, Type[BaseIdp]] = {
        IdentityProvider.LOCAL: LocalIdp,
    }

    @classmethod
    def register(cls, provider_type: str, idp_class: Type[BaseIdp]) -> None:
        """Register an IDP provider class.

        Called by enterprise integration layer during startup to register
        enterprise IDP providers (Keycloak, OIDC, etc.).

        Args:
            provider_type: Provider type key (e.g., "keycloak", "oidc")
            idp_class: BaseIdp subclass to instantiate for this provider type
        """
        cls._idp_registry[provider_type.lower()] = idp_class

    @classmethod
    def unregister(cls, provider_type: str) -> None:
        """Remove a registered IDP provider (useful for testing).

        Args:
            provider_type: Provider type key to remove
        """
        cls._idp_registry.pop(provider_type.lower(), None)

    @classmethod
    def get_registered_providers(cls) -> list[str]:
        """Return list of registered provider type keys."""
        return list(cls._idp_registry.keys())

    @classmethod
    def create(cls, provider_type: str | None = None) -> BaseIdp:
        """Create an IDP instance based on configuration or parameter.

        Args:
            provider_type: Override provider type. If None, reads from config.

        Returns:
            BaseIdp instance. Falls back to LocalIdp if provider not registered.
        """
        if not provider_type:
            provider_type = getattr(config, "IDP_PROVIDER", IdentityProvider.LOCAL)

        idp_class = cls._idp_registry.get(provider_type.lower())
        if not idp_class:
            idp_class = LocalIdp

        return idp_class()


def get_idp_provider(provider_type: str | None = None) -> BaseIdp:
    """Get the IDP provider instance."""
    return IdpFactory.create(provider_type)
