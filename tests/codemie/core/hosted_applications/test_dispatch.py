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

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from starlette.responses import PlainTextResponse

from codemie.hosted_apps_gateway.dispatch import HostDispatcher, protect_content_hosts

HOST = "v-0123456789abcdef0123456789abcdef.apps-content.example"
DOMAIN = "apps-content.example"


async def management(scope, receive, send):
    await PlainTextResponse("management", headers={"x-management": "yes"})(scope, receive, send)


async def content(scope, receive, send):
    if scope["path"] != "/index.html":
        await PlainTextResponse("unavailable", status_code=404)(scope, receive, send)
    else:
        await PlainTextResponse("content")(scope, receive, send)


def dispatcher(content_app=content, management_app=management, **changes):
    return HostDispatcher(management_app, content_app, content_domain=DOMAIN, **changes)


def call(app, headers, *, path="/index.html", method="GET", client=("203.0.113.9", 50000)):
    """Drive the ASGI app with exact raw headers; HTTP clients would normalize or add Host."""

    async def run():
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers],
            "client": client,
            "server": ("10.0.0.1", 8080),
        }
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await app(scope, receive, send)
        start = next(message for message in messages if message["type"] == "http.response.start")
        body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        return start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}, body.decode()

    return asyncio.run(run())


def get(host, path="/index.html", headers=None, method="GET"):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=dispatcher()), base_url=f"https://{host}"
        ) as http:
            return await http.request(method, path, headers=headers if headers is not None else {"Host": host})

    return asyncio.run(run())


def assert_closed(status, headers, expected):
    assert status == expected
    assert "x-management" not in headers
    assert headers["content-security-policy"].startswith("default-src 'none'")
    assert headers["cache-control"] == "private, no-store"


def test_same_workload_dispatch_keeps_closed_app_trees():
    assert get("api.example").text == "management"
    assert get(HOST).text == "content"
    assert "x-management" not in get(HOST).headers


@pytest.mark.parametrize(
    "path", ["/v1/users", "/docs", "/openapi.json", "/metrics", "/health", "/v1/hosted-applications"]
)
@pytest.mark.parametrize("method", ["GET", "POST", "OPTIONS"])
def test_content_host_never_falls_through_to_management(path, method):
    response = get(HOST, path, method=method)
    assert response.status_code in (404, 405)
    assert "x-management" not in response.headers
    assert "set-cookie" not in response.headers
    assert "location" not in response.headers


@pytest.mark.parametrize(
    "host",
    [
        "10.12.0.7:8080",  # kubelet probe addressed by pod IP
        "codemie:8080",  # in-cluster service name
        "codemie.codemie.svc.cluster.local:8080",
        "[fd00::7]:8080",
        "localhost",
        "api.example",
        "unknown.example",
        HOST + ".attacker.example",  # another site entirely, not the content namespace
        "notapps-content.example",
        "API.Example",  # non-canonical, but nowhere near the content namespace
        "codemie_api:8080",  # compose service names may carry underscores
    ],
)
def test_every_host_outside_the_content_namespace_reaches_management(host):
    status, headers, body = call(dispatcher(), [("Host", host)])
    assert (status, body) == (200, "management")
    assert headers["x-management"] == "yes"


def test_request_without_host_reaches_management():
    status, _, body = call(dispatcher(), [])
    assert (status, body) == (200, "management")


@pytest.mark.parametrize(
    "host",
    [
        DOMAIN,
        "www." + DOMAIN,
        "v-0123456789abcdef." + DOMAIN,
        "v-0123456789abcdef0123456789abcdeF." + DOMAIN,
        HOST.upper(),
        HOST + ":444",
        HOST + ".",
        "a.b." + DOMAIN + ":8443",
        HOST + ":443:443",
    ],
)
def test_content_namespace_hosts_that_are_not_version_hosts_are_closed(host):
    assert_closed(*call(dispatcher(), [("Host", host)])[:2], 404)


def test_version_host_on_its_configured_port():
    assert call(dispatcher(), [("Host", HOST + ":443")])[2] == "content"
    local = dispatcher(content_port=8081)
    assert call(local, [("Host", HOST + ":8081")])[2] == "content"
    assert_closed(*call(local, [("Host", HOST)])[:2], 404)


@pytest.mark.parametrize(
    "headers",
    [
        [("Host", "api.example"), ("Host", HOST)],
        [("Host", "api.example"), ("Host", "api.example")],
        [("Host", "api.example"), ("X-Forwarded-Host", HOST)],
        [("Host", "api.example"), ("X-Original-Host", HOST)],
        [("Host", "api.example"), ("Forwarded", f'for=10.0.0.9;host="{HOST}";proto=https')],
        [("Host", "api.example"), ("X-Forwarded-Host", "api.example, " + DOMAIN.upper())],
        [("X-Forwarded-Host", HOST)],
        [("Host", HOST), ("X-Forwarded-Host", "api.example")],
        [("Host", HOST), ("Forwarded", "host=api.example")],
        [("Host", HOST), ("X-Original-Host", HOST)],
    ],
)
def test_ambiguous_host_routing_is_rejected_closed(headers):
    assert_closed(*call(dispatcher(), headers)[:2], 400)


@pytest.mark.parametrize(
    "forwarding",
    [
        ("X-Forwarded-Host", "api.example"),
        ("X-Forwarded-Host", "codemie:8080"),
        ("Forwarded", "for=10.0.0.9;proto=https"),
        ("X-Original-Host", "internal-gateway.example"),
    ],
)
def test_forwarding_headers_never_route_management(forwarding):
    status, _, body = call(dispatcher(), [("Host", "api.example"), forwarding])
    assert (status, body) == (200, "management")


def test_content_accepts_only_one_exact_host_echo():
    echo = [("Host", HOST), ("X-Forwarded-Host", HOST)]
    assert call(dispatcher(), echo)[2] == "content"
    assert_closed(*call(dispatcher(), echo + [("X-Forwarded-Host", HOST)])[:2], 400)
    assert_closed(*call(dispatcher(), echo + [("Forwarded", "proto=https")])[:2], 400)


def test_version_host_is_available_to_gateway_as_server_fact():
    async def capture(scope, receive, send):
        state = scope["state"]
        await PlainTextResponse(f"{state['hosted_version_id']}")(scope, receive, send)

    assert call(dispatcher(capture), [("Host", HOST)])[2] == "01234567-89ab-cdef-0123-456789abcdef"


def test_standalone_viewer_has_a_distinct_origin_and_closed_gateway_route():
    async def capture(scope, receive, send):
        state = scope["state"]
        await PlainTextResponse(f"{state['hosted_version_id']}:{state.get('hosted_viewer_host', False)}")(
            scope, receive, send
        )

    viewer = HOST.replace("v-", "w-", 1)
    assert call(dispatcher(capture), [("Host", viewer)])[2] == "01234567-89ab-cdef-0123-456789abcdef:True"
    assert call(dispatcher(capture), [("Host", HOST)])[2] == "01234567-89ab-cdef-0123-456789abcdef:False"
    assert_closed(*call(dispatcher(), [("Host", viewer + ":444")])[:2], 404)
    assert_closed(*call(dispatcher(), [("Host", viewer), ("X-Forwarded-Host", HOST)])[:2], 400)


def test_websockets_follow_the_same_namespace_rule():
    seen = []

    async def management_ws(scope, receive, send):
        seen.append(scope["type"])
        await send({"type": "websocket.close", "code": 1000})

    async def run(host):
        sent = []

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            sent.append(message)

        scope = {"type": "websocket", "path": "/ws", "headers": [(b"host", host.encode())], "client": ("10.0.0.1", 1)}
        await dispatcher(management_app=management_ws)(scope, receive, send)
        return sent

    assert asyncio.run(run(HOST)) == [{"type": "websocket.close", "code": 1008}]
    assert asyncio.run(run("api.example")) == [{"type": "websocket.close", "code": 1000}]
    assert seen == ["websocket"]


def test_lifespan_belongs_to_management_and_other_scopes_follow_the_host_rule():
    seen = []

    async def management_any(scope, receive, send):
        seen.append(scope["type"])

    async def run():
        tree = dispatcher(management_app=management_any)
        await tree({"type": "lifespan"}, None, None)
        await tree({"type": "extension", "headers": [(b"host", b"api.example")]}, None, None)
        await tree({"type": "extension", "headers": [(b"host", HOST.encode())]}, None, None)

    asyncio.run(run())
    assert seen == ["lifespan", "extension"]


def test_dispatcher_requires_a_content_domain():
    with pytest.raises(ValueError):
        HostDispatcher(management, content, content_domain=" ")


@pytest.mark.parametrize("domain", ["apps-cöntent.example", "äpps.example", "apps.exämple"])
def test_dispatcher_refuses_a_non_ascii_content_domain(domain):
    """CR-006: a lossy encoding would let malformed content hosts reach management instead of failing closed."""
    with pytest.raises(ValueError):
        HostDispatcher(management, content, content_domain=domain)


@pytest.mark.parametrize("enabled", [False, True])
def test_configured_content_hosts_remain_closed_during_readiness_failures(enabled):
    settings = SimpleNamespace(enabled=enabled, content_domain=DOMAIN, content_port=443)
    tree = protect_content_hosts(management, settings)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=tree), base_url=f"https://{HOST}") as client:
            for path in ("/docs", "/v1/users", "/health", "/index.html"):
                response = await client.get(path)
                assert response.status_code == 503
                assert "x-management" not in response.headers
                assert response.headers["content-security-policy"].startswith("default-src 'none'")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=tree), base_url="http://10.0.0.7:8080"
        ) as client:
            probe = await client.get("/health")
            assert probe.text == "management"

    asyncio.run(run())


def test_unconfigured_content_domain_leaves_management_untouched():
    settings = SimpleNamespace(enabled=False, content_domain="", content_port=443)
    assert protect_content_hosts(management, settings) is management
