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

"""Bounded upload streaming; database leases fence receipts, physical locks fence cleanup."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.protocol import check_etag, etag
from codemie.repository.hosted_applications.contract import build_writer_lease
from codemie.rest_api.models.hosted_application import (
    HostedApplicationStorageAttempts as Attempt,
)

# A stream that delivers nothing for this long is abandoned; the total budget covers slow but steady links.
UPLOAD_IDLE_SECONDS = 60
# One artifact upload may take this long; the API ingress read/send timeouts must be at least this long.
UPLOAD_TIMEOUT_SECONDS = 900
# The database lease is 30 seconds; the writer renews it this often.
HEARTBEAT_SECONDS = 10


def upload_timeout():
    return HostedApplicationError("UPLOAD_TIMEOUT", "The upload stalled or took too long; reset it and retry", 408)


async def _bounded(stream, heartbeat):
    """The upload body, failing on an idle stream or a failed heartbeat."""
    iterator = stream.__aiter__()
    while True:
        if heartbeat.done():
            heartbeat.result()
        try:
            chunk = await asyncio.wait_for(anext(iterator), timeout=UPLOAD_IDLE_SECONDS)
        except StopAsyncIteration:
            return
        except TimeoutError as exc:
            raise upload_timeout() from exc
        yield chunk


class ReceivingServiceMixin:
    async def _claim_receive(self, identity, app_id, version_id, generation, if_match):
        async with self.repository.transaction(*identity, write=True) as tx:
            app = await tx.managed_app(app_id, identity.user, lock=True)
            version = await self._owned_version(tx, app, version_id, lock=True)
            attempt = await self._attempt(tx, version, lock=True)
            if generation != version.upload_generation:
                raise HostedApplicationError("UPLOAD_GENERATION_CONFLICT", "Reload the upload before retrying", 409)
            if version.upload_state == "stored":
                raise HostedApplicationError(
                    "UPLOAD_IMMUTABLE", "Artifact bytes are already stored; reload its receipt", 409
                )
            check_etag(if_match, "upload", version.upload_id, version.upload_revision)
            self._upload_live(tx, app, version)
            if version.upload_state != "created" or attempt.state != "planned":
                raise HostedApplicationError(
                    "UPLOAD_IN_PROGRESS", "The upload already started; reload before resetting", 409
                )
            attempt.state, attempt.writer_id, attempt.writer_fence = "writing", uuid4(), uuid4()
            attempt.lease_until = tx.now + timedelta(seconds=30)
            # The database deadline fences the writer; it matches the in-process total budget.
            attempt.write_deadline = tx.now + timedelta(seconds=UPLOAD_TIMEOUT_SECONDS)
            attempt.revision += 1
            attempt.updated_at = tx.now
            version.upload_state = "receiving"
            version.upload_revision += 1
            version.revision += 1
            await tx.session.flush()
            claim = {
                "id": attempt.id,
                "app_id": attempt.app_id,
                "fence": attempt.writer_fence,
                "prefix": attempt.prefix,
                "upper": attempt.byte_upper_bound,
                "size": version.expected_bytes,
                "expected": version.expected_digest,
                "generation": version.upload_generation,
                # Authorized now: the transfer may outlive the sign-in token, never this deadline.
                "deadline": attempt.write_deadline,
            }
            lease_until, write_deadline, database_now = attempt.lease_until, attempt.write_deadline, tx.now
        # The monotonic stamp is taken as soon as the transaction result is in hand.
        claim["lease"] = build_writer_lease(lease_until, write_deadline, database_now, self.monotonic())
        return claim

    async def _receive_heartbeat(self, identity, app_id, version_id, claim):
        async with self.repository.transaction(*identity, write=True, authorized_until=claim["deadline"]) as tx:
            app = await tx.managed_app(app_id, identity.user, lock=True)
            version = await self._owned_version(tx, app, version_id, lock=True)
            attempt = await self._attempt(tx, version, lock=True)
            if (
                version.upload_state != "receiving"
                or version.upload_generation != claim["generation"]
                or attempt.id != claim["id"]
                or attempt.writer_fence != claim["fence"]
                or attempt.state != "writing"
                or attempt.lease_until is None
                or attempt.lease_until <= tx.now
                or attempt.write_deadline <= tx.now
            ):
                raise HostedApplicationError("UPLOAD_LEASE_LOST", "The upload lease ended; reload before retrying", 409)
            attempt.lease_until = min(tx.now + timedelta(seconds=30), attempt.write_deadline)
            attempt.updated_at = tx.now
            lease_until, write_deadline, database_now = attempt.lease_until, attempt.write_deadline, tx.now
        return build_writer_lease(lease_until, write_deadline, database_now, self.monotonic())

    async def _mark_writer_stopped(self, claim):
        # Trusted internal failure bookkeeping must work after a platform logout or policy outage.
        async with self.repository.sessions() as session, session.begin():
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            attempt = (
                await session.execute(
                    select(Attempt)
                    .where(
                        Attempt.app_id == claim["app_id"],
                        Attempt.id == claim["id"],
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if attempt and attempt.writer_fence == claim["fence"] and attempt.state == "writing":
                attempt.state = "uncertain"
                attempt.lease_until = now
                attempt.writer_quiesced_at = now
                attempt.updated_at = now
                attempt.revision += 1

    async def transfer(self, identity, app_id, version_id, generation, if_match, stream):
        await self.directory.require(identity)
        claim = await self._claim_receive(identity, app_id, version_id, generation, if_match)
        heartbeat = None

        async def maintain(writer):
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                writer.renew(await self._receive_heartbeat(identity, app_id, version_id, claim))

        try:
            async with asyncio.timeout(UPLOAD_TIMEOUT_SECONDS):
                async with self.storage.writer(
                    claim["prefix"], byte_limit=claim["upper"], object_limit=1, lease=claim["lease"]
                ) as writer:
                    # Check after obtaining the physical lock: a fenced stale writer must never write.
                    writer.renew(await self._receive_heartbeat(identity, app_id, version_id, claim))
                    heartbeat = asyncio.create_task(maintain(writer))
                    receipt = await writer.put(
                        "original",
                        _bounded(stream, heartbeat),
                        expected_size=claim["size"],
                        expected_digest=claim["expected"] or None,
                    )
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                await self.directory.require(identity)
                return await self._commit_receipt(identity, app_id, version_id, generation, claim, receipt)
        except BaseException as exc:
            if heartbeat:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            await asyncio.shield(self._mark_writer_stopped(claim))
            if isinstance(exc, TimeoutError):
                raise upload_timeout() from exc
            raise
        finally:
            if heartbeat and not heartbeat.done():
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    async def _commit_receipt(self, identity, app_id, version_id, generation, claim, receipt):
        """Record the stored original against the upload, unless its lease ended while it was written."""
        async with self.repository.transaction(*identity, write=True, authorized_until=claim["deadline"]) as tx:
            app = await tx.managed_app(app_id, identity.user, lock=True)
            version = await self._owned_version(tx, app, version_id, lock=True)
            attempt = await self._attempt(tx, version, lock=True)
            if (
                attempt.id != claim["id"]
                or attempt.writer_fence != claim["fence"]
                or attempt.state != "writing"
                or attempt.lease_until <= tx.now
                or version.upload_generation != generation
                or version.upload_state != "receiving"
            ):
                raise HostedApplicationError("UPLOAD_LEASE_LOST", "The upload lease ended; reload before retrying", 409)
            attempt.state, attempt.receipt_digest, attempt.confirmed_bytes = (
                "stored",
                receipt.digest,
                receipt.size,
            )
            attempt.held_bytes -= receipt.size
            attempt.writer_quiesced_at, attempt.updated_at = tx.now, tx.now
            attempt.lease_until = None
            attempt.revision += 1
            tx.quota.reserved_bytes -= receipt.size
            tx.quota.stored_bytes += receipt.size
            tx.quota.updated_at = tx.now
            tx.quota.revision += 1
            version.upload_state = "stored"
            version.upload_revision += 1
            version.revision += 1
            op, _ = await tx.operation("upload.receipt", version.id, {}, app_id=app.id)
            return await tx.finish(
                op,
                {
                    "upload_id": str(version.upload_id),
                    "generation": generation,
                    "state": "stored",
                    "received_bytes": receipt.size,
                    "sha256": receipt.digest.hex(),
                    "upload_etag": etag("upload", version.upload_id, version.upload_revision),
                    "version_etag": etag("version", version.id, version.revision),
                },
                details={"bytes": receipt.size},
            )
