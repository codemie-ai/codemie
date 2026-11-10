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

"""Marketplace publication, version selection and activation.

Publishing sets ``is_global`` and nothing else: who may read the app inside its project stays with ``shared``.
``activate`` is the one place a version becomes the live one; a later reinstate step imports it from here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from codemie.core.hosted_applications.access import suspend_refuses
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.protocol import check_etag

if TYPE_CHECKING:
    from codemie.repository.hosted_applications.repository import HostedTransaction
    from codemie.rest_api.models.hosted_application import HostedApplicationVersions, HostedApplications
    from codemie.rest_api.models.hosted_application_requests import VersionSelection
    from codemie.rest_api.security.hosted_applications import HostedActor

Change = Callable[["HostedTransaction", "HostedApplications"], Awaitable[None]]


def activate(app: HostedApplications, version: HostedApplicationVersions | None) -> HostedApplications:
    """Make ``version`` the live and the working version; ``version`` is the result of ``_eligible_version``."""
    if version is None:
        raise HostedApplicationError(
            "VERSION_NOT_ACCEPTED", "Choose an accepted version allowed by the current security policy"
        )
    app.active_version_id = version.id
    app.selected_version_id = version.id
    app.artifact_kind = version.artifact_kind
    return app


class PublicationServiceMixin:
    async def _mutate(
        self,
        identity: HostedActor,
        app_id: UUID | str,
        action: str,
        payload: dict[str, Any],
        if_match: str | None,
        key: str,
        change: Change,
    ) -> dict[str, Any]:
        """One locked management mutation: replay by key, revision check, ``change(tx, app)``, revision bump."""
        async with self.repository.transaction(*identity, write=True) as tx:
            app = await tx.managed_app(app_id, identity.user, lock=True)
            op, replay = await tx.operation(action, app.id, {**payload, "if_match": if_match}, key, app_id=app.id)
            if replay is not None:
                return replay
            check_etag(if_match, "app", app.id, app.revision)
            await change(tx, app)
            app.revision += 1
            app.updated_at = tx.now
            return await tx.finish(op, await self._project(tx, app, identity.user))

    async def select_version(
        self, identity: HostedActor, app_id: UUID | str, request: VersionSelection, if_match: str | None, key: str
    ) -> dict[str, Any]:
        """The working version only; the live version is untouched."""

        async def change(tx: HostedTransaction, app: HostedApplications) -> None:
            version = await self._eligible_version(tx, app, request.version_id, activation=True)
            if version is None:
                raise HostedApplicationError(
                    "VERSION_NOT_ACCEPTED", "Choose an accepted version allowed by the current security policy"
                )
            app.selected_version_id = version.id
            app.artifact_kind = version.artifact_kind

        return await self._mutate(
            identity, app_id, "app.select", {"request": request.model_dump(mode="json")}, if_match, key, change
        )

    async def activate_version(
        self, identity: HostedActor, app_id: UUID | str, request: VersionSelection, if_match: str | None, key: str
    ) -> dict[str, Any]:
        async def change(tx: HostedTransaction, app: HostedApplications) -> None:
            suspend_refuses(app, "activate")
            activate(app, await self._eligible_version(tx, app, request.version_id, activation=True))

        return await self._mutate(
            identity, app_id, "app.activate", {"request": request.model_dump(mode="json")}, if_match, key, change
        )

    async def publish(
        self, identity: HostedActor, app_id: UUID | str, if_match: str | None, key: str
    ) -> dict[str, Any]:
        async def change(tx: HostedTransaction, app: HostedApplications) -> None:
            suspend_refuses(app, "publish")
            app.is_global = True

        return await self._mutate(identity, app_id, "app.publish", {}, if_match, key, change)

    async def unpublish(
        self, identity: HostedActor, app_id: UUID | str, if_match: str | None, key: str
    ) -> dict[str, Any]:
        async def change(tx: HostedTransaction, app: HostedApplications) -> None:
            app.is_global = False

        return await self._mutate(identity, app_id, "app.unpublish", {}, if_match, key, change)
