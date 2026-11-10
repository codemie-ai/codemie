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

"""Content gateway boundary behavior without a database: live authorization is a seam here."""

import asyncio
import base64
import hashlib
import json
from pathlib import PurePosixPath
from uuid import uuid4

import httpx
import pytest

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.paths import strip_root_path
from codemie.core.hosted_applications.settings import HostedSettings
from codemie.core.hosted_applications.validation import MIME_TYPES
from codemie.hosted_apps_gateway.admission import ContentLimits
from codemie.hosted_apps_gateway.content import ContentGateway
from codemie.hosted_apps_gateway.dispatch import HostDispatcher
from codemie.repository.hosted_applications.filesystem import CHUNK_BYTES
from tests.codemie.service.hosted_applications.storage_fakes import (
    CROSS_ADAPTERS,
    filesystem_adapter,
    writer_lease,
)

VIEWER, OTHER, PLANTED = "v" * 43, "o" * 43, "p" * 43
INDEX = b"<html><body>Version content</body></html>"
LARGE = bytes(range(256)) * (CHUNK_BYTES * 5 // 2 // 256)


class Launch:
    def __init__(self, version, attempt, *, local=True):
        self.settings = HostedSettings(
            enabled=True,
            disabled_reason=None,
            local=local,
            ui_origins=("http://localhost:5173",),
            content_domain="apps.localhost" if local else "apps-content.example.net",
            content_port=8081 if local else 443,
            secret=b"s" * 48,
        )
        self.version, self.attempt = version, attempt
        self.sessions = {VIEWER: "viewer", OTHER: "other"}
        self.tokens = []

    async def consume(self, ticket, host):
        raise HostedApplicationError("LAUNCH_EXPIRED", "Return to CodeMie", 401)

    async def authorize(self, token, version_id):
        self.tokens.append(token)
        if token not in self.sessions or version_id != self.version["id"]:
            raise HostedApplicationError("LAUNCH_EXPIRED", "Return to CodeMie", 401)
        return {
            "app": {},
            "version": self.version,
            "attempt": self.attempt,
            "viewer_subject": self.sessions[token],
        }


class GatedStorage:
    """Delegates to real storage; reads of one key wait until the gate opens (a slow reader)."""

    def __init__(self, storage, key):
        self.storage, self.key = storage, key
        self.entered, self.gate = asyncio.Event(), asyncio.Event()

    def __getattr__(self, name):
        return getattr(self.storage, name)

    async def read_range(self, key, start, end):
        if key == self.key:
            self.entered.set()
            await self.gate.wait()
        async for chunk in self.storage.read_range(key, start, end):
            yield chunk


@pytest.fixture(params=CROSS_ADAPTERS)
def adapter(request):
    """Each offline storage adapter in turn: the gateway must answer the same on every one."""
    return request.param


async def build_site(tmp_path, files=None, adapter=filesystem_adapter):
    files = files or {"index.html": INDEX, "large.png": LARGE, "style.css": b"body{color:#123}"}
    storage = adapter(tmp_path / "private")
    prefix = "hosted/attempt-" + uuid4().hex
    entries = {}

    async def single(data):
        yield data

    async with storage.writer(prefix, byte_limit=64 * 1024 * 1024, object_limit=16, lease=writer_lease()) as writer:
        for path, data in files.items():
            suffix = PurePosixPath(path).suffix
            await writer.put("assets/" + path, single(data), expected_size=len(data))
            entries[path] = {
                "path": path,
                "key": prefix + "/assets/" + path,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "mime": MIME_TYPES[suffix],
                "document": suffix in (".html", ".htm", ".svg"),
            }
        manifest = json.dumps(
            {"version": 1, "site_root": "", "entrypoint": "index.html", "routing_mode": "static", "files": entries}
        ).encode()
        await writer.put("manifest.json", single(manifest), expected_size=len(manifest))
    version = {
        "id": uuid4(),
        "manifest_key": prefix + "/manifest.json",
        "manifest_digest": hashlib.sha256(manifest).digest(),
    }
    return storage, version, {"prefix": prefix}, entries


def mount(gateway, version_id, root_path=""):
    app = gateway.app()

    async def tree(scope, receive, send):
        scope.setdefault("state", {})["hosted_version_id"] = version_id
        if root_path:
            # Mirror uvicorn --root-path: the prefix is prepended to path and raw_path.
            scope = {**scope, "root_path": root_path, "path": root_path + scope["path"]}
            scope["raw_path"] = root_path.encode() + scope["raw_path"]
        await app(scope, receive, send)

    return tree


def client(app, peer="127.0.0.1"):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(peer, 4711)), base_url="http://v-test.apps.localhost:8081"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("local", [True, False])
async def test_cookie_planted_by_a_sibling_version_origin_cannot_replace_the_session(tmp_path, local):
    storage, version, attempt, _ = await build_site(tmp_path)
    launch = Launch(version, attempt, local=local)
    gateway = ContentGateway(launch, storage)
    name = gateway.embedded_cookie_name
    async with client(mount(gateway, version["id"])) as http:
        for cookie in (
            f"{name}={VIEWER}; x=1 {name}={PLANTED}",
            f"x=1 {name}={PLANTED}; {name}={VIEWER}",
            f"x {name}={PLANTED}; {name}={VIEWER}",
        ):
            response = await http.get("/", headers={"cookie": cookie})
            assert response.status_code == 200 and launch.tokens[-1] == VIEWER
        launch.tokens.clear()
        for cookie in (f"{name}={VIEWER}; {name}={PLANTED}", f"{name}=short", f"x=1 {name}={PLANTED}"):
            assert (await http.get("/", headers={"cookie": cookie})).status_code == 401
        # Ambiguous or malformed platform cookies are rejected before any authorization lookup.
        assert launch.tokens == []


def test_root_path_is_stripped_on_a_segment_boundary():
    assert strip_root_path(b"/prefix/index.html", "/prefix") == b"/index.html"
    assert strip_root_path(b"/prefix", "/prefix") == b"/"
    assert strip_root_path(b"/prefix/", "/prefix/") == b"/"
    assert strip_root_path(b"/prefixed/index.html", "/prefix") == b"/prefixed/index.html"
    assert strip_root_path(b"/index.html", "") == b"/index.html"
    assert strip_root_path(b"/a%20b/x.js", "/a b") == b"/a%20b/x.js"


@pytest.mark.asyncio
@pytest.mark.parametrize("local", [True, False])
async def test_standalone_navigation_preserves_isolation_and_never_serves_uploaded_top_level_code(tmp_path, local):
    storage, version, attempt, _ = await build_site(tmp_path)
    launch = Launch(version, attempt, local=local)
    gateway = ContentGateway(launch, storage)
    suffix = ".apps.localhost:8081" if local else ".apps-content.example.net"
    scheme = "http" if local else "https"
    content_origin = f"{scheme}://v-{version['id'].hex}{suffix}"
    viewer_origin = f"{scheme}://w-{version['id'].hex}{suffix}"

    async def management(scope, receive, send):
        raise AssertionError("A viewer or content host reached management")

    app = HostDispatcher(
        management,
        gateway.app(),
        content_domain=launch.settings.content_domain,
        content_port=launch.settings.content_port,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
        headers = {"cookie": gateway.cookie_name + "=" + VIEWER, "sec-fetch-dest": "document"}
        opened = await http.get(content_origin + "/?route=example", headers=headers)
        assert opened.status_code == 303
        assert opened.headers["location"] == viewer_origin + "/?route=example"
        assert "Version content" not in opened.text
        assert opened.headers["cache-control"] == "private, no-store"

        wrapper = await http.get(opened.headers["location"])
        assert wrapper.status_code == 200
        assert 'title="Application content"' in wrapper.text
        assert 'sandbox="allow-scripts allow-same-origin"' in wrapper.text
        assert "fullscreen 'none'" in wrapper.text
        assert f'src="{content_origin}/?route=example"' in wrapper.text
        assert "Version content" not in wrapper.text
        assert "set-cookie" not in wrapper.headers
        script = wrapper.text.split("<script>")[1].split("</script>")[0]
        script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        assert f"script-src 'sha256-{script_hash}'" in wrapper.headers["content-security-policy"]
        assert "'unsafe-inline'" not in wrapper.headers["content-security-policy"].split("script-src ")[1].split(";")[0]
        assert "frame-src " + content_origin in wrapper.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in wrapper.headers["content-security-policy"]
        # The unauthenticated wrapper holds no authority; its child still requires a live viewing session.
        assert launch.tokens == [VIEWER]
        assert (await http.get(content_origin + "/", headers={"sec-fetch-dest": "iframe"})).status_code == 401
        embedded = await http.get(content_origin + "/", headers=headers | {"sec-fetch-dest": "iframe"})
        assert embedded.status_code == 200 and embedded.text == INDEX.decode()
        ancestors = embedded.headers["content-security-policy"].split("frame-ancestors ")[1].split(";")[0]
        assert ancestors == "http://localhost:5173 " + viewer_origin
        assert "w-" + "0" * 32 not in ancestors

        # A version asset remains a normal authenticated fetch, and a wrapper can never receive upload bytes.
        assert (
            await http.get(content_origin + "/style.css", headers=headers | {"sec-fetch-dest": "style"})
        ).status_code == 200
        assert (await http.post(viewer_origin + "/_platform/launch", content=b"ticket=not-a-ticket")).status_code == 405
        assert (await http.get(viewer_origin + "/_platform/launch")).status_code == 404


@pytest.mark.asyncio
async def test_standalone_wrapper_escapes_target_and_retains_the_fixed_content_origin(tmp_path):
    storage, version, attempt, _ = await build_site(tmp_path)
    launch = Launch(version, attempt)
    gateway = ContentGateway(launch, storage)
    app = gateway.app()

    async def tree(scope, receive, send):
        scope.setdefault("state", {}).update(hosted_version_id=version["id"], hosted_viewer_host=True)
        await app(scope, receive, send)

    async with client(tree) as http:
        response = await http.get('/?x=%22%3E%3Cscript%3Ealert(1)%3C/script%3E&next=https://evil.example')
        assert response.status_code == 200
        assert response.text.count("<script>") == 1
        assert "alert(1)" not in response.text.split("<script>")[1]
        assert "&amp;next=" in response.text
        assert f'src="http://v-{version["id"].hex}.apps.localhost:8081/' in response.text
        assert "frame-src http://v-" in response.headers["content-security-policy"]
        assert launch.tokens == []


@pytest.mark.asyncio
async def test_interactive_navigation_representation_preserves_ranges_and_cache(tmp_path, adapter):
    storage, version, attempt, _ = await build_site(tmp_path, adapter=adapter)
    launch = Launch(version, attempt)
    gateway = ContentGateway(launch, storage)
    nav = {
        "cookie": gateway.cookie_name + "=" + VIEWER,
        "sec-fetch-mode": "navigate",
        "sec-fetch-dest": "iframe",
        "accept": "text/html",
    }
    async with client(mount(gateway, version["id"])) as http:
        plain = await http.get("/", headers={"cookie": nav["cookie"]})
        page = await http.get("/", headers=nav)
        assert page.content.startswith(INDEX)
        assert "codemie-hosted-location" in page.text
        assert launch.settings.version_origin(version["id"], viewer=True) in page.text
        assert len(page.content) == int(page.headers["content-length"])
        assert page.headers["etag"] != plain.headers["etag"]
        assert (await http.get("/", headers=nav | {"if-none-match": page.headers["etag"]})).status_code == 304
        assert (
            await http.get("/", headers={"cookie": nav["cookie"], "if-none-match": page.headers["etag"]})
        ).status_code == 200
        head = await http.head("/", headers=nav)
        assert not head.content and head.headers["content-length"] == page.headers["content-length"]
        for start, end in ((0, 3), (len(INDEX) - 3, len(INDEX) + 3), (len(INDEX), len(page.content) - 1)):
            response = await http.get("/", headers=nav | {"range": f"bytes={start}-{end}"})
            assert response.status_code == 206
            assert response.content == page.content[start : end + 1]
            assert response.headers["content-range"] == f"bytes {start}-{end}/{len(page.content)}"
        launch.sessions.pop(VIEWER)
        assert (await http.get("/", headers=nav | {"if-none-match": page.headers["etag"]})).status_code == 401


@pytest.mark.asyncio
async def test_content_is_served_under_a_server_root_path(tmp_path):
    storage, version, attempt, _ = await build_site(tmp_path)
    gateway = ContentGateway(Launch(version, attempt), storage)
    async with client(mount(gateway, version["id"], root_path="/code-assistant-api")) as http:
        cookie = {"cookie": gateway.cookie_name + "=" + VIEWER}
        assert (await http.get("/", headers=cookie)).text == INDEX.decode()
        assert (await http.get("/style.css", headers=cookie)).status_code == 200
        assert (await http.get("/missing.css", headers=cookie)).status_code == 404


@pytest.mark.asyncio
async def test_every_content_response_disables_dns_prefetch(tmp_path):
    storage, version, attempt, _ = await build_site(tmp_path)
    gateway = ContentGateway(Launch(version, attempt), storage)
    app = mount(gateway, version["id"])
    session = {"cookie": gateway.cookie_name + "=" + VIEWER}
    async with client(app) as http:
        first = await http.get("/", headers=session)
        responses = [
            first,
            await http.get("/", headers=session | {"if-none-match": first.headers["etag"]}),
            await http.get("/", headers={"cookie": gateway.cookie_name + "=" + PLANTED}),
            await http.get(
                "/", headers={"accept": "text/html", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document"}
            ),
            await http.get("/missing.js", headers=session),
            await http.get("/", headers=session | {"range": "bytes=999999-"}),
            await http.put("/_platform/view", headers=session),
            await http.get("/_platform/view", headers=session),
        ]
    for response in responses:
        assert "webrtc" not in response.headers["content-security-policy"], response.status_code
        assert response.headers["x-dns-prefetch-control"] == "off", response.status_code
    busy = ContentGateway(
        Launch(version, attempt), storage, policy=ContentLimits(total_concurrency=1, queue_seconds=0.05)
    )
    slot = busy.capacity.slot()
    assert await slot.admit()
    try:
        async with client(mount(busy, version["id"])) as http:
            rejected = await http.get("/", headers=session)
    finally:
        slot.release()
    assert rejected.status_code == 503 and rejected.headers["x-dns-prefetch-control"] == "off"
    assert "sandbox allow-scripts allow-same-origin" in rejected.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_version_assets_revalidate_while_platform_documents_and_errors_are_never_stored(tmp_path, adapter):
    storage, version, attempt, _ = await build_site(tmp_path, adapter=adapter)
    gateway = ContentGateway(Launch(version, attempt), storage)
    session = {"cookie": gateway.cookie_name + "=" + VIEWER}
    async with client(mount(gateway, version["id"])) as http:
        first = await http.get("/style.css", headers=session)
        assert first.status_code == 200 and first.headers["cache-control"] == "private, no-cache"
        tag = first.headers["etag"]
        assert tag == '"' + hashlib.sha256(b"body{color:#123}").hexdigest() + '"'
        for headers, status in (
            ({"if-none-match": tag}, 304),
            ({"if-none-match": '"other", W/' + tag}, 304),
            ({"range": "bytes=0-3"}, 206),
            ({"range": "bytes=0-3", "if-range": tag}, 206),
        ):
            response = await http.get("/style.css", headers=session | headers)
            assert response.status_code == status and response.headers["cache-control"] == "private, no-cache"
            assert response.headers["etag"] == tag
        head = await http.head("/style.css", headers=session)
        assert head.status_code == 200 and head.headers["cache-control"] == "private, no-cache"
        # Authorization still runs first: a revalidation without a live session is denied, never 304.
        denied = await http.get("/style.css", headers={"if-none-match": tag})
        assert denied.status_code == 401 and denied.headers["cache-control"] == "private, no-store"
        for response in (
            await http.get("/_platform/view", headers=session),
            await http.get("/missing.css", headers=session),
            await http.get("/style.css", headers=session | {"range": "bytes=99999-"}),
            await http.post("/_platform/launch", data={"ticket": "x"}),
        ):
            assert response.headers["cache-control"] == "private, no-store", response.status_code


@pytest.mark.asyncio
async def test_one_viewer_cannot_hold_every_content_slot(tmp_path):
    storage, version, attempt, entries = await build_site(tmp_path)
    gated = GatedStorage(storage, entries["large.png"]["key"])
    policy = ContentLimits(total_concurrency=2, viewer_concurrency=1, queue_seconds=0.2)
    gateway = ContentGateway(Launch(version, attempt), gated, policy=policy)
    app = mount(gateway, version["id"])
    viewer = {"cookie": gateway.cookie_name + "=" + VIEWER}
    other = {"cookie": gateway.cookie_name + "=" + OTHER}
    async with client(app) as http:
        slow = asyncio.create_task(http.get("/large.png", headers=viewer))
        await asyncio.wait_for(gated.entered.wait(), 5)
        queued = asyncio.create_task(http.get("/style.css", headers=viewer))
        await asyncio.sleep(0.05)
        # The queued request of the slow viewer holds no general slot, so another viewer is served.
        assert (await http.get("/style.css", headers=other)).status_code == 200
        limited = await queued
        assert limited.status_code == 429 and limited.headers["retry-after"] == "5"
        gated.gate.set()
        assert (await slow).status_code == 200
        assert (await http.get("/style.css", headers=viewer)).status_code == 200
    assert gateway.capacity.viewers == {}


@pytest.mark.asyncio
async def test_sites_may_use_docs_and_api_folders(tmp_path):
    files = {"index.html": INDEX, "docs/guide.html": b"<html>Guide</html>", "api/data.json": b"{}"}
    storage, version, attempt, _ = await build_site(tmp_path, files)
    gateway = ContentGateway(Launch(version, attempt), storage)
    session = {"cookie": gateway.cookie_name + "=" + VIEWER}
    async with client(mount(gateway, version["id"])) as http:
        assert (await http.get("/docs/guide.html", headers=session)).status_code == 200
        assert (await http.get("/api/data.json", headers=session)).status_code == 200
        assert (await http.get("/_platform/other", headers=session)).status_code == 404


@pytest.mark.asyncio
async def test_busy_or_failing_content_hosts_tell_the_viewer_and_log_server_errors(tmp_path):
    from unittest.mock import patch

    storage, version, attempt, _ = await build_site(tmp_path)
    launch = Launch(version, attempt)
    gateway = ContentGateway(launch, storage)
    session = {"cookie": gateway.cookie_name + "=" + VIEWER}
    navigate = {"sec-fetch-mode": "navigate", "sec-fetch-dest": "iframe", "accept": "text/html"}

    async def slow(token, version_id):
        raise TimeoutError

    async def broken(token, version_id):
        raise RuntimeError("database detail that must not reach the browser")

    with patch("codemie.hosted_apps_gateway.content.logger") as logger:
        async with client(mount(gateway, version["id"])) as http:
            launch.authorize = slow
            busy = await http.get("/_platform/view", headers=session | navigate)
            launch.authorize = broken
            failed = await http.get("/style.css", headers=session)
    assert busy.status_code == 503 and busy.headers["retry-after"] == "5"
    assert "codemie-hosted-unavailable" in busy.text
    assert failed.status_code == 503 and "database detail" not in failed.text
    assert "sandbox allow-scripts allow-same-origin" in failed.headers["content-security-policy"]
    # A busy host is counted by metrics; only the unexpected failure is logged.
    assert logger.error.call_count == 1


@pytest.mark.asyncio
async def test_every_adapter_serves_the_same_statuses_headers_and_bytes(tmp_path, adapter):
    storage, version, attempt, entries = await build_site(tmp_path, adapter=adapter)
    gateway = ContentGateway(Launch(version, attempt), storage)
    session = {"cookie": gateway.cookie_name + "=" + VIEWER}
    tag = '"' + entries["large.png"]["sha256"] + '"'
    middle = len(LARGE) // 2
    async with client(mount(gateway, version["id"])) as http:
        full = await http.get("/large.png", headers=session)
        assert full.status_code == 200 and full.content == LARGE
        assert (full.headers["etag"], full.headers["content-length"]) == (tag, str(len(LARGE)))
        assert full.headers["content-type"].startswith("image/png")
        ranged = await http.get("/large.png", headers=session | {"range": f"bytes={middle - 5}-{middle + CHUNK_BYTES}"})
        assert ranged.status_code == 206 and ranged.content == LARGE[middle - 5 : middle + CHUNK_BYTES + 1]
        assert ranged.headers["content-range"] == f"bytes {middle - 5}-{middle + CHUNK_BYTES}/{len(LARGE)}"
        tail = await http.get("/large.png", headers=session | {"range": "bytes=-7"})
        assert tail.status_code == 206 and tail.content == LARGE[-7:]
        cached = await http.get("/large.png", headers=session | {"if-none-match": tag})
        assert cached.status_code == 304 and not cached.content and cached.headers["etag"] == tag
        past = await http.get("/large.png", headers=session | {"range": f"bytes={len(LARGE)}-"})
        assert past.status_code == 416 and past.headers["cache-control"] == "private, no-store"
        assert (await http.get("/", headers=session)).content == INDEX
