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

"""The API side of the validator protocol: a slot first, then the upload; busy and outages leave jobs queued."""

import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.preview import MAX_PREVIEW_BYTES
from codemie.hosted_apps_validator.contract import validation_budget
from codemie.service.hosted_applications.validator_client import PreviewFile, ValidatorClient

KEY = "k" * 32
RESERVATION = "a" * 64
CONTENT = b"<html>Queued</html>"


def client_for(handler):
    transport = httpx.MockTransport(handler)
    return ValidatorClient("http://validator:8091", KEY, client=httpx.AsyncClient(transport=transport))


async def chunks():
    yield CONTENT


async def inspect(client, kind="html"):
    async with client.inspect(chunks(), size=len(CONTENT), digest=hashlib.sha256(CONTENT).digest(), kind=kind):
        pytest.fail("The validation must not start")


@pytest.mark.asyncio
async def test_busy_validator_is_not_sent_the_upload_and_pauses_claiming():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/reservations":
            return httpx.Response(503, json={"code": "VALIDATOR_BUSY", "message": "busy"})
        return httpx.Response(200, json={})

    client = client_for(handler)
    with pytest.raises(HostedApplicationError) as error:
        await inspect(client)
    assert (error.value.code, error.value.status) == ("VALIDATOR_BUSY", 503)
    assert seen == ["/reservations"], "No upload byte is sent without a slot"
    assert not await client.ready(), "A busy validator gets no new claims for a moment"
    assert seen == ["/reservations"], "The pause answers without asking the validator"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["connect", "status", "garbage"])
async def test_unreachable_or_misbehaving_validator_never_starts_the_job(failure):
    def handler(request):
        if failure == "connect":
            raise httpx.ConnectError("refused", request=request)
        if failure == "status":
            return httpx.Response(503, json={"code": "VALIDATION_UNAVAILABLE"})
        return httpx.Response(201, json={"reservation": "not-a-key"})

    client = client_for(handler)
    with pytest.raises(HostedApplicationError) as error:
        await inspect(client)
    assert error.value.code == "VALIDATOR_UNREACHABLE"
    assert not await client.ready()


@pytest.mark.asyncio
async def test_health_verdict_is_cached_both_ways(monkeypatch):
    from codemie.service.hosted_applications import validator_client

    answers = [200, 503, 200]
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(answers[len(requests) - 1], json={"version": "1.5.4"})

    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(validator_client, "time", SimpleNamespace(monotonic=lambda: clock.now))
    client = client_for(handler)
    assert await client.ready() and await client.ready()
    assert len(requests) == 1
    clock.now += validator_client.HEALTH_SECONDS + 1
    assert not await client.ready() and not await client.ready()
    assert len(requests) == 2, "An unhealthy validator is not asked again during the pause"
    clock.now += validator_client.HEALTH_SECONDS + 1
    assert await client.ready()
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_upload_carries_its_reservation_and_waits_for_the_whole_job_budget():
    seen = {}

    def handler(request):
        if request.url.path == "/reservations":
            return httpx.Response(201, json={"reservation": RESERVATION})
        seen.update(headers=request.headers, timeout=request.extensions["timeout"])
        return httpx.Response(422, json={"code": "SCAN_LIMIT_EXCEEDED", "message": "Heuristics.Limits.Exceeded"})

    client = client_for(handler)
    with pytest.raises(HostedApplicationError) as error:
        await inspect(client, kind="zip")
    assert seen["headers"]["x-validation-reservation"] == RESERVATION
    assert seen["timeout"]["read"] >= validation_budget("zip", len(CONTENT))
    assert error.value.code == "SCAN_LIMIT_EXCEEDED" and "Heuristics" not in error.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (422, {"code": "VALIDATION_LIMIT_EXCEEDED"}, "VALIDATION_LIMIT_EXCEEDED"),
        (422, {"code": "MALWARE_DETECTED"}, "MALWARE_DETECTED"),
        (503, {"code": "VALIDATOR_BUSY"}, "VALIDATOR_BUSY"),
        (503, {"code": "VALIDATION_UNAVAILABLE"}, "VALIDATION_UNAVAILABLE"),
        (422, {"code": "INVENTED"}, "VALIDATION_UNAVAILABLE"),
        (422, ["not", "an", "object"], "VALIDATION_UNAVAILABLE"),
    ],
)
async def test_validator_outcomes_after_the_slot_was_granted(status, body, code):
    def handler(request):
        if request.url.path == "/reservations":
            return httpx.Response(201, json={"reservation": RESERVATION})
        return httpx.Response(status, json=body)

    with pytest.raises(HostedApplicationError) as error:
        await inspect(client_for(handler))
    assert error.value.code == code


@pytest.mark.asyncio
async def test_transferred_file_must_have_its_announced_size():
    def handler(request):
        return httpx.Response(200, content=b"12345")

    client = client_for(handler)
    exact = SimpleNamespace(size=5, sha256="0" * 64)
    assert b"".join([chunk async for chunk in client.file_chunks(RESERVATION, 0, exact)]) == b"12345"
    for size in (4, 6):
        with pytest.raises(HostedApplicationError) as error:
            _ = [chunk async for chunk in client.file_chunks(RESERVATION, 0, SimpleNamespace(size=size, sha256=""))]
        assert error.value.code == "VALIDATION_UNAVAILABLE"


GOOD_PREVIEW = {"index": 3, "size": 5, "sha256": "ab" * 32, "width": 1280, "height": 800}


async def capture(client, identifier=RESERVATION):
    return await client.capture(identifier, "site", "index.html", "static", lock_wait=10, timeout=5.0)


@pytest.mark.asyncio
async def test_capture_returns_preview_file():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/preview"):
            return httpx.Response(200, json={"preview": GOOD_PREVIEW})
        return httpx.Response(200, content=b"12345")

    client = client_for(handler)
    preview = await capture(client)
    assert preview == PreviewFile(3, 5, "ab" * 32, 1280, 800)
    request = seen[0]
    assert (request.method, request.url.path) == ("POST", f"/results/{RESERVATION}/preview")
    assert request.headers["x-validator-key"] == KEY
    assert json.loads(request.content) == {
        "site_root": "site",
        "entrypoint": "index.html",
        "routing_mode": "static",
        "lock_wait": 10,
    }
    data = b"".join([chunk async for chunk in client.file_chunks(RESERVATION, preview.index, preview)])
    assert data == b"12345"


def _preview(**changes):
    return {"preview": GOOD_PREVIEW | changes}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"preview": None, "reason": "slow"}),
        httpx.Response(200, json={"preview": None, "reason": "busy", "extra": 1}),
        httpx.Response(200, json={"preview": GOOD_PREVIEW, "reason": "busy"}),
        httpx.Response(200, json={"preview": GOOD_PREVIEW, "extra": 1}),
        httpx.Response(200, json={"preview": {k: v for k, v in GOOD_PREVIEW.items() if k != "width"}}),
        httpx.Response(200, json={"preview": GOOD_PREVIEW | {"extra": 1}}),
        httpx.Response(200, json=_preview(size=MAX_PREVIEW_BYTES + 1)),
        httpx.Response(200, json=_preview(size=0)),
        httpx.Response(200, json=_preview(size=True)),
        httpx.Response(200, json=_preview(index=-1)),
        httpx.Response(200, json=_preview(sha256="AB" * 32)),
        httpx.Response(200, json=_preview(sha256="ab" * 31)),
        httpx.Response(200, json=_preview(width=2049)),
        httpx.Response(200, json=_preview(height=0)),
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, content=b" " * 8192),
        httpx.Response(422, json={"code": "BAD"}),
        httpx.Response(503, json={"code": "VALIDATION_UNAVAILABLE"}),
    ],
)
async def test_capture_gives_failed_for_an_unusable_answer(response):
    assert await capture(client_for(lambda request: response)) == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer,expected",
    [
        ({"preview": None, "reason": "busy"}, "busy"),
        ({"preview": None, "reason": "off"}, "off"),
        ({"preview": None, "reason": "no_picture"}, "no_picture"),
        ({"preview": None}, "unknown"),
        ({"preview": None, "reason": "slow"}, "failed"),
    ],
)
async def test_capture_gives_the_validator_reason_for_a_null_answer(answer, expected):
    assert await capture(client_for(lambda request: httpx.Response(200, json=answer))) == expected


@pytest.mark.asyncio
async def test_capture_refused_request_is_failed():
    # An older validator refuses the request that carries lock_wait with 422.
    assert (
        await capture(client_for(lambda request: httpx.Response(422, json={"code": "INVALID_CONFIGURATION"})))
        == "failed"
    )
    assert await capture(client_for(lambda request: httpx.Response(400, json={}))) == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
async def test_capture_gives_failed_on_a_transport_error(error):
    def handler(request):
        raise error("down", request=request)

    assert await capture(client_for(handler)) == "failed"
