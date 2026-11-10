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
import hashlib
import pytest
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.hosted_apps_gateway.cache import ImmutableContentCache
from codemie.repository.hosted_applications.filesystem import HostedFileStorage


@pytest.mark.asyncio
async def test_malformed_session_cookies_never_reach_the_live_lookup():
    from uuid import uuid4
    import httpx
    from codemie.hosted_apps_gateway.content import ContentGateway
    from codemie.core.hosted_applications.settings import HostedSettings

    class Launch:
        settings = HostedSettings(
            enabled=True,
            disabled_reason=None,
            local=True,
            content_domain='apps.localhost',
            content_port=8081,
            ui_origins=('http://localhost:5173',),
            secret=b's' * 48,
        )
        calls = 0

        async def authorize(self, token, version):
            self.calls += 1
            raise HostedApplicationError('LAUNCH_EXPIRED', 'Return to CodeMie', 401)

    launch = Launch()
    gateway = ContentGateway(launch, object()).app()

    async def app(scope, receive, send):
        scope['state'] = {'hosted_version_id': uuid4()}
        await gateway(scope, receive, send)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://v-test.apps.localhost'
    ) as client:
        # A well-formed but unknown session reaches the live lookup; malformed cookies never do.
        assert (await client.get('/', headers={'Cookie': 'codemie-app-session-local=' + 'f' * 43})).status_code == 401
        assert (await client.get('/', headers={'Cookie': 'codemie-app-session-local=fake'})).status_code == 401
        assert (await client.get('/')).status_code == 401
    assert launch.calls == 1


def manifest_bytes(prefix, name="index.html"):
    import json

    data = b"<html>x</html>"
    entry = {
        "path": name,
        "key": prefix + "/assets/" + name,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "mime": "text/html; charset=utf-8",
        "document": True,
    }
    return json.dumps(
        {"version": 1, "site_root": "", "entrypoint": name, "routing_mode": "static", "files": {name: entry}}
    ).encode()


class ManifestStorage:
    """Serves manifests from memory; reads of gated keys wait until their gate opens."""

    def __init__(self):
        self.objects, self.gates, self.reads = {}, {}, []

    def add(self, prefix, *, gated=False):
        data = manifest_bytes(prefix)
        key = prefix + "/manifest.json"
        self.objects[key] = data
        if gated:
            self.gates[key] = asyncio.Event()
        return {"manifest_key": key, "manifest_digest": hashlib.sha256(data).digest()}, {"prefix": prefix}

    async def read_small(self, key, *, maximum):
        self.reads.append(key)
        if key in self.gates:
            await self.gates[key].wait()
        if self.objects[key] is None:
            raise HostedApplicationError("CONTENT_UNAVAILABLE", "Application content is unavailable", 503)
        return self.objects[key]


@pytest.mark.asyncio
async def test_manifest_loads_are_single_flight_per_key_without_blocking_other_manifests():
    storage = ManifestStorage()
    slow = storage.add("hosted/slow", gated=True)
    fast = storage.add("hosted/fast")
    cache = ImmutableContentCache(storage)
    waiting = [asyncio.create_task(cache.manifest(*slow)) for _ in range(5)]
    await asyncio.sleep(0.01)
    # A slow disk read or parse of one manifest no longer serializes every other version.
    assert (await asyncio.wait_for(cache.manifest(*fast), 1))["entrypoint"] == "index.html"
    waiting[0].cancel()
    storage.gates["hosted/slow/manifest.json"].set()
    results = await asyncio.gather(*waiting[1:])
    assert all(result is results[0] for result in results)
    assert storage.reads.count("hosted/slow/manifest.json") == 1
    # Cached thereafter, even though the first requester was cancelled while waiting.
    assert await cache.manifest(*slow) is results[0]
    assert storage.reads.count("hosted/slow/manifest.json") == 1


@pytest.mark.asyncio
async def test_failed_manifest_load_is_shared_but_never_cached():
    storage = ManifestStorage()
    version, attempt = storage.add("hosted/flaky")
    original = storage.objects[version["manifest_key"]]
    storage.objects[version["manifest_key"]] = None
    cache = ImmutableContentCache(storage)
    with pytest.raises(HostedApplicationError):
        await cache.manifest(version, attempt)
    storage.objects[version["manifest_key"]] = original
    assert (await cache.manifest(version, attempt))["routing_mode"] == "static"
    assert cache.size > 0 and len(cache.manifests) == 1


@pytest.mark.asyncio
async def test_manifest_cache_keeps_its_memory_and_entry_bounds():
    storage = ManifestStorage()
    versions = [storage.add("hosted/bounded" + str(index)) for index in range(6)]
    cost = len(storage.objects[versions[0][0]["manifest_key"]]) + 512
    cache = ImmutableContentCache(storage, maximum=cost * 3, entries=2)
    for version, attempt in versions:
        await cache.manifest(version, attempt)
    assert len(cache.manifests) == 2 and cache.size == 2 * cost
    small = ImmutableContentCache(storage, maximum=cost * 2)
    for version, attempt in versions:
        await small.manifest(version, attempt)
    assert len(small.manifests) == 2 and small.size <= cost * 2


def test_shared_cache_is_one_bounded_instance_per_storage(tmp_path):
    first, second = HostedFileStorage(tmp_path / "one"), HostedFileStorage(tmp_path / "two")
    assert ImmutableContentCache.shared(first) is ImmutableContentCache.shared(first)
    assert ImmutableContentCache.shared(first) is not ImmutableContentCache.shared(second)
    # Storages that cannot be weakly referenced still get a private, bounded cache.
    opaque = object()
    assert ImmutableContentCache.shared(opaque) is not ImmutableContentCache.shared(opaque)
