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

"""Administrator takedown: suspend an application at once, or reinstate it after an independent check.

Platform administrators call it directly over the API during incident response; there is no UI for it.
"""

from __future__ import annotations

from codemie.core.hosted_applications.access import SECURITY_CLEAR, SECURITY_SUSPENDED, is_suspended, platform_user
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.protocol import check_etag
from codemie.rest_api.models.hosted_application import HostedApplicationVersions as Version
from codemie.service.hosted_applications.publication import activate
from codemie.service.hosted_applications.retention import Actor, retire_version


class HostedApplicationAdministration:
    def __init__(self, catalog):
        self.catalog, self.repository = catalog, catalog.repository

    @staticmethod
    def digest(reason, if_match, version_id, delete_previous):
        """What a retried request must repeat exactly under the same idempotency key."""
        return {
            "reason": reason,
            "if_match": if_match,
            "version_id": str(version_id) if version_id else None,
            "delete_previous": delete_previous,
        }

    async def security(
        self, identity, app_id, action, reason, if_match, key, *, version_id=None, delete_previous=False
    ):
        """Suspend or reinstate an application; only a platform administrator (maintainers included) may.

        Administrator status comes from the ``users`` row, the source ``manageable`` reads, not the request's roles.
        """
        async with self.repository.transaction(*identity, write=True) as tx:
            user = await platform_user(tx.session, identity.user)
            if not user.is_admin:
                raise HostedApplicationError("ACCESS_DENIED", "Administrator permission is required", 403)
            if action not in ("suspend", "reinstate") or not reason.strip():
                raise HostedApplicationError("INVALID_ACTION", "Choose a security action and explain it", 422)
            if action == "suspend" and (version_id is not None or delete_previous):
                raise HostedApplicationError("INVALID_ACTION", "Suspending takes no version choice", 422)
            # The creator's quota row, then the app row, as every management mutation: an activate or publish
            # that raced this suspend has committed or is refused on the locked row.
            app = await tx.managed_app(app_id, user, lock=True)
            if action == "reinstate" and app.created_by.id == user.id:
                # Nobody reinstates their own application.
                raise HostedApplicationError(
                    "INDEPENDENT_REVIEW_REQUIRED", "Another administrator must reinstate your application", 403
                )
            op, replay = await tx.operation(
                "app." + action, app.id, self.digest(reason, if_match, version_id, delete_previous), key, app_id=app.id
            )
            if replay is not None:
                return replay
            # A moderation operation, the no-op second suspend included (spec 5.6).
            op.actor_kind = "administrator"
            # A takedown may target an application the administrator did not open, so suspend checks the
            # current revision only when the caller sends one. Reinstate may activate another version and
            # retire the previous one, so it always requires the revision the administrator saw.
            if if_match is not None or action == "reinstate":
                check_etag(if_match, "app", app.id, app.revision)
            details = {"reason": reason, "admin_id": user.id}
            if action == "suspend":
                if is_suspended(app):
                    # Suspending twice changes nothing.
                    return await tx.finish(op, await self.catalog._project(tx, app, user), details=details)
                # The security state only: sharing, grants and open sessions' revision are left alone.
                app.security_state = SECURITY_SUSPENDED
            else:
                details |= await self._reinstate(tx, app, user, version_id, delete_previous)
            app.revision += 1
            app.updated_at = tx.now
            return await tx.finish(op, await self.catalog._project(tx, app, user), details=details)

    async def _reinstate(self, tx, app, user, version_id, delete_previous):
        if not is_suspended(app):
            raise HostedApplicationError("NOT_SUSPENDED", "This application is not suspended", 409)
        previous = app.active_version_id
        if delete_previous and (previous is None or version_id is None or version_id == previous):
            raise HostedApplicationError(
                "NO_OTHER_VERSION", "Choose another version to make active before deleting the previous one", 409
            )
        app.security_state = SECURITY_CLEAR
        chosen = version_id or previous
        if chosen is not None:
            eligible = await self.catalog._eligible_version(tx, app, chosen, activation=True)
            if eligible is None:
                raise HostedApplicationError(
                    "REVALIDATION_REQUIRED", "That version is not eligible, so it cannot be made active", 409
                )
            activate(app, eligible)
        if delete_previous:
            # After activate moved the live and working versions away from it.
            await retire_version(tx, app, await tx.session.get(Version, previous), Actor(user.id, "administrator"))
        return {
            "active_version_id": str(chosen) if chosen else None,
            "deleted_version_id": str(previous) if delete_previous else None,
        }
