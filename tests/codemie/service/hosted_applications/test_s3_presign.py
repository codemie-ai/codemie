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

"""The S3 fenced multipart writer: presigned Create and Complete, offline.

The botocore cases sign with a real client and dummy credentials, and send through an in-test HTTP
stand-in, so no request leaves the process. The expiry cases run the writer on ``FakeS3``.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.repository.hosted_applications.contract import WriterLease
from codemie.repository.hosted_applications.object_storage import (
    HostedObjectStorage,
    ObjectExists,
    ObjectOpsError,
    ObjectUnavailable,
    UploadGone,
    read_storage_id,
)
from codemie.repository.hosted_applications.s3_backend import BotoS3Ops, s3_client
from tests.codemie.service.hosted_applications.storage_fakes import FakeClock, FakeS3

PREFIX = "attempt/" + "b" * 32
CREATED = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<InitiateMultipartUploadResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
    b"<Bucket>hosted-bucket</Bucket><Key>a/b</Key><UploadId>upload-7</UploadId>"
    b"</InitiateMultipartUploadResult>"
)
COMPLETED = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<CompleteMultipartUploadResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
    b'<Bucket>hosted-bucket</Bucket><Key>a/b</Key><ETag>"done-1"</ETag>'
    b"</CompleteMultipartUploadResult>"
)


def error_body(code):
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><Error><Code>{code}</Code>'
        f"<Message>failure</Message><RequestId>r</RequestId></Error>"
    ).encode()


class Response:
    def __init__(self, status, data=b"", headers=None):
        self.status_code, self.content, self.headers = status, data, headers or {}


class RecordingHTTP:
    """Stands in for botocore's HTTP session: records each prepared request and answers from a queue."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def send(self, request):
        self.requests.append((request.method, request.url, request.body, dict(request.headers)))
        return self.responses.pop(0)


def boto_ops(*responses):
    client = s3_client(
        region_name="us-east-1", aws_access_key_id="AKIAEXAMPLEEXAMPLE00", aws_secret_access_key="x" * 40
    )
    http = RecordingHTTP(*responses)
    return BotoS3Ops("hosted-bucket", client=client, http=http), http


def query(url):
    return {name: values[0] for name, values in parse_qs(urlparse(url).query, keep_blank_values=True).items()}


def test_complete_signs_if_none_match():
    ops, http = boto_ops(Response(200, CREATED), Response(200, COMPLETED))
    model = ops.client.meta.service_model
    # botocore models the conditional header on Complete only, so Create can never carry it.
    assert "IfNoneMatch" in model.operation_model("CompleteMultipartUpload").input_shape.members
    assert "IfNoneMatch" not in model.operation_model("CreateMultipartUpload").input_shape.members

    assert ops.presigned_create("a/b", 18) == "upload-7"
    ops.presigned_complete("a/b", "upload-7", [(1, '"etag-1"')], 17)

    (create_method, create_url, _, create_headers), (method, url, body, headers) = http.requests
    assert create_method == method == "POST"
    assert query(create_url)["X-Amz-Expires"] == "18" and "uploads" in query(create_url)
    assert "if-none-match" not in query(create_url)["X-Amz-SignedHeaders"].split(";")
    assert "If-None-Match" not in create_headers
    signed = query(url)
    assert signed["X-Amz-Expires"] == "17" and signed["uploadId"] == "upload-7"
    assert "if-none-match" in signed["X-Amz-SignedHeaders"].split(";")
    assert headers["If-None-Match"] == "*"
    assert b"<PartNumber>1</PartNumber>" in body and b'<ETag>"etag-1"</ETag>' in body


def test_complete_error_in_200_body_fails():
    ops, _ = boto_ops(
        Response(200, error_body("InternalError")),
        Response(200, error_body("PreconditionFailed")),
        Response(200, b"<CompleteMultipartUploadResult><Bucket>trunc"),
    )
    with pytest.raises(ObjectUnavailable) as error:
        ops.presigned_complete("a/b", "upload-7", [(1, '"etag-1"')], 17)
    assert error.value.code == "InternalError"
    with pytest.raises(ObjectExists):
        ops.presigned_complete("a/b", "upload-7", [(1, '"etag-1"')], 17)
    # A body cut off by the network is not a success either.
    with pytest.raises(ObjectUnavailable):
        ops.presigned_complete("a/b", "upload-7", [(1, '"etag-1"')], 17)


def test_presigned_status_codes_map_to_typed_errors():
    ops, _ = boto_ops(
        Response(412, error_body("PreconditionFailed")),
        Response(404, error_body("NoSuchUpload")),
        Response(403, error_body("AccessDenied")),
        Response(409, error_body("ConditionalRequestConflict")),
        Response(503, error_body("SlowDown")),
    )
    complete = ("a/b", "upload-7", [(1, '"etag-1"')], 17)
    with pytest.raises(ObjectExists):
        ops.presigned_complete(*complete)
    with pytest.raises(UploadGone):
        ops.presigned_complete(*complete)
    for code, status in (("AccessDenied", 403), ("ConditionalRequestConflict", 409), ("SlowDown", 503)):
        with pytest.raises(ObjectUnavailable) as error:
            ops.presigned_complete(*complete)
        assert (error.value.code, error.value.status) == (code, status)


EXPIRED = (
    b'<?xml version="1.0" encoding="UTF-8"?><Error><Code>AccessDenied</Code><Message>Request has expired</Message>'
    b"<X-Amz-Expires>17</X-Amz-Expires><Expires>2026-01-01T00:00:17Z</Expires>"
    b"<ServerTime>2026-01-01T00:00:20Z</ServerTime><RequestId>r</RequestId></Error>"
)


def test_expired_presign_and_missing_bucket_map_to_their_own_errors():
    ops, _ = boto_ops(Response(403, EXPIRED), Response(404, error_body("NoSuchBucket")))
    with pytest.raises(ObjectOpsError) as error:
        ops.presigned_complete("a/b", "upload-7", [(1, '"etag-1"')], 17)
    # Only an expired presigned request means the lease ended; any other 403 is a permission problem.
    assert type(error.value).__name__ == "PresignExpired" and error.value.status == 403
    with pytest.raises(ObjectOpsError) as error:
        ops.presigned_create("a/b", 18)
    # A missing bucket is a misconfiguration, never a gone upload.
    assert type(error.value) is ObjectUnavailable and (error.value.code, error.value.status) == ("NoSuchBucket", 404)


async def chunks(*data):
    for chunk in data:
        yield chunk


def clocked_storage():
    clock = FakeClock()
    fake = FakeS3(clock=clock)
    storage = HostedObjectStorage(
        fake, provider="aws", storage_id=read_storage_id(fake), cleanup_grace_seconds=30, monotonic=clock.monotonic
    )
    fake.calls.clear()
    return storage, fake, clock


def lease_of(clock, seconds):
    return WriterLease(until=clock() + timedelta(seconds=seconds), database_now=clock(), received_at=clock.monotonic())


def sent(fake, *operations):
    return [call for call in fake.calls if call[0] in operations]


@pytest.mark.asyncio
async def test_expiry_shrinks_with_monotonic_time_since_heartbeat():
    storage, fake, clock = clocked_storage()
    async with storage.writer(PREFIX, byte_limit=100, object_limit=4, lease=lease_of(clock, 30)) as writer:
        clock.advance(12)
        await writer.put("original", chunks(b"data"), expected_size=4)
        assert [call[-1] for call in sent(fake, "presigned_create", "presigned_complete")] == [18, 18]
        fake.calls.clear()
        # The writer stalls after its last heartbeat: the stale lease must not sign a fresh window.
        clock.advance(17.5)
        with pytest.raises(HostedApplicationError) as error:
            await writer.put("second", chunks(b"data"), expected_size=4)
        assert (error.value.code, error.value.status) == ("STORAGE_LEASE_LOST", 409)
        assert sent(fake, "presigned_create", "presigned_complete") == []
        # A renewed lease restores the window from its own receipt time.
        writer.renew(lease_of(clock, 30))
        clock.advance(5)
        await writer.put("third", chunks(b"data"), expected_size=4)
        assert [call[-1] for call in sent(fake, "presigned_create", "presigned_complete")] == [25, 25]


@pytest.mark.asyncio
async def test_complete_conflict_409_is_retried():
    storage, fake, clock = clocked_storage()
    expiries = []

    def conflict_once(expires_in, **_):
        expiries.append(expires_in)
        if len(expiries) == 1:
            clock.advance(3)
            raise ObjectUnavailable("ConditionalRequestConflict", 409)

    fake.hooks["before_presigned_complete"] = conflict_once
    async with storage.writer(PREFIX, byte_limit=100, object_limit=4, lease=lease_of(clock, 30)) as writer:
        receipt = await writer.put("original", chunks(b"data"), expected_size=4)
    assert receipt.size == 4
    assert fake.objects[PREFIX + "/original"][0] == b"data"
    # The retry signs a fresh expiry from the same lease.
    assert expiries == [30, 27]
    assert not fake.uploads
