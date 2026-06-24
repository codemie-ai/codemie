# Approach C: Inline Discovery Safety Net — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `POST /v1/mcp-auth/oauth2/initiate` never dead-end on a missing discovered-flow snapshot by running the existing discovery probe inline and healing both the by-binding-miss and by-id-expired entry points.

**Architecture:** Add a `skip_auth_resolution` flag to `MCPToolkitService._resolve_server_auth` (and thread it up through `_prepare_server_config` → `_process_single_mcp_server`). Add `_mcp_server_from_config` and `ensure_discovered_snapshot_for_server` to `MCPToolkitService`. Wire the heal in `build_discovered_oauth2_initiate_response` via a non-raising probe → `ensure_...` → binding re-read → original raising loader fallback.

**Tech Stack:** Python, pytest, FastAPI, SQLModel, Redis (via codemie-enterprise discovered-flow store), httpx (MCP connect)

**STOP CONDITION FOR THIS PASS:** Write the failing tests (Steps marked `[RED]`) and confirm they fail. Do NOT implement any production code. Steps marked `[GREEN — follow-up pass]` are documented for the next implementation pass.

---

## File Map

| Action | File | Purpose |
|---|---|---|
| Modify | `src/codemie/service/mcp/toolkit_service.py` | Add `skip_auth_resolution` flag to 3 methods; add `_mcp_server_from_config`; add `ensure_discovered_snapshot_for_server` |
| Modify | `src/codemie/enterprise/mcp_auth/_initiate.py` | Non-raising probe + heal wiring in `build_discovered_oauth2_initiate_response` |
| Modify | `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py` | Unit tests for `skip_auth_resolution`, `_mcp_server_from_config`, `ensure_...` |
| Modify | `tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py` | Integration tests for heal paths, 400-parity, auth ordering, no-op guard |

---

## Task 1: `skip_auth_resolution` flag on `_resolve_server_auth`

**Files:**
- Modify: `src/codemie/service/mcp/toolkit_service.py:874-900` (`_resolve_server_auth`)
- Modify: `src/codemie/service/mcp/toolkit_service.py:825-871` (`_prepare_server_config`)
- Modify: `src/codemie/service/mcp/toolkit_service.py:677-753` (`_process_single_mcp_server`)
- Test: `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py`

- [ ] **Step 1 [RED]: Write failing tests**

Add to `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py`:

```python
# ── skip_auth_resolution flag ─────────────────────────────────────────────────

class _CallTrackingResolver:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def can_handle(self, server_config: object) -> bool:
        self.calls.append("can_handle")
        return True

    def resolve(self, server_config: object, user_id: object, execution_context: object) -> bool:
        self.calls.append("resolve")
        return True


def test_resolve_server_auth_skip_bypasses_enterprise_loop_and_legacy_fallback(monkeypatch) -> None:
    from codemie.service.mcp.models import MCPServerConfig
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    server_config = MCPServerConfig(url="https://mcp.example.com/")
    tracker = _CallTrackingResolver()
    legacy_calls: list[str] = []

    monkeypatch.setattr(MCPToolkitService, "_auth_resolvers", [tracker])
    monkeypatch.setattr(MCPToolkitService._legacy_token_resolver, "can_handle", lambda _: legacy_calls.append("can_handle") or True)
    monkeypatch.setattr(MCPToolkitService._legacy_token_resolver, "resolve", lambda *_: legacy_calls.append("resolve"))

    # Raises TypeError today ("unexpected keyword argument 'skip_auth_resolution'") → RED
    MCPToolkitService._resolve_server_auth(server_config, user_id=None, execution_context=None, skip_auth_resolution=True)

    assert tracker.calls == [], "skip_auth_resolution=True must bypass enterprise resolver loop"
    assert legacy_calls == [], "skip_auth_resolution=True must bypass legacy fallback"


def test_resolve_server_auth_skip_false_still_calls_resolvers(monkeypatch) -> None:
    from codemie.service.mcp.models import MCPServerConfig
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    server_config = MCPServerConfig(url="https://mcp.example.com/")
    tracker = _CallTrackingResolver()

    monkeypatch.setattr(MCPToolkitService, "_auth_resolvers", [tracker])

    MCPToolkitService._resolve_server_auth(server_config, user_id=None, execution_context=None, skip_auth_resolution=False)

    assert "can_handle" in tracker.calls, "skip_auth_resolution=False must run resolvers as normal"
```

- [ ] **Step 2 [RED]: Run tests to confirm failure**

```bash
cd /home/taras_spashchenko/EPAM/cm/codemie
poetry run pytest tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py \
  -k "test_resolve_server_auth_skip" -v 2>&1 | tail -20
```

Expected: `FAILED ... TypeError: _resolve_server_auth() got an unexpected keyword argument 'skip_auth_resolution'`

- [ ] **[GREEN — follow-up pass] Step 3: Add `skip_auth_resolution` to `_resolve_server_auth`**

In `toolkit_service.py` at the `_resolve_server_auth` definition (line 874):

```python
@classmethod
def _resolve_server_auth(
    cls,
    server_config: MCPServerConfig,
    user_id: str | None,
    execution_context: MCPExecutionContext | None = None,
    *,
    skip_auth_resolution: bool = False,
) -> None:
    """Run registered enterprise resolvers first, then the inline LegacyTokenResolver fallback."""
    if skip_auth_resolution:
        return
    if server_config.auth_config is not None:
        cls._strip_legacy_token_placeholder_headers(server_config)
        if not server_config.auth_config:
            raise MCPAuthenticationRequiredException(
                {
                    "status": "config_error",
                    "error_context": "MCP auth configuration is empty.",
                }
            )

    for resolver in cls._auth_resolvers:
        if resolver.can_handle(server_config):
            handled = resolver.resolve(server_config, user_id, execution_context)
            if handled is False:
                continue
            return

    if cls._legacy_token_resolver.can_handle(server_config):
        cls._legacy_token_resolver.resolve(server_config, user_id, execution_context)
```

Thread the flag in `_prepare_server_config` (add `skip_auth_resolution: bool = False` parameter, pass to `_resolve_server_auth`). Thread in `_process_single_mcp_server` (add `skip_auth_resolution: bool = False` parameter, pass to `_prepare_server_config`).

- [ ] **[GREEN — follow-up pass] Step 4: Run tests to confirm GREEN**

```bash
poetry run pytest tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py \
  -k "test_resolve_server_auth_skip" -v 2>&1 | tail -10
```

Expected: `2 passed`

- [ ] **[GREEN — follow-up pass] Step 5: Commit**

```bash
git add src/codemie/service/mcp/toolkit_service.py \
        tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py
git commit -m "EPMCDME-13049: Add skip_auth_resolution flag to MCPToolkitService resolver chain"
```

---

## Task 2: `_mcp_server_from_config` static method

**Files:**
- Modify: `src/codemie/service/mcp/toolkit_service.py` (add static method after `_build_discovery_candidate_from_challenge`)
- Test: `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py`

- [ ] **Step 1 [RED]: Write failing tests**

Add to `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py`:

```python
# ── _mcp_server_from_config ───────────────────────────────────────────────────

def _make_mcp_config(
    *,
    config_id: str = "mcp-config-1",
    name: str = "test-server",
    url: str = "https://mcp.example.com/",
    allow_issuer_prefix_match: bool = False,
) -> object:
    from types import SimpleNamespace

    config_data = SimpleNamespace(
        url=url,
        type="streamable_http",
        command=None,
        args=[],
        headers={},
        env={},
        auth_config=None,
        auth_token=None,
        single_usage=False,
        tools=None,
        audience=None,
        allow_issuer_prefix_match=allow_issuer_prefix_match,
        bucket_key=None,
    )
    config_data.model_dump = lambda: {
        "url": config_data.url,
        "type": config_data.type,
        "command": config_data.command,
        "args": config_data.args,
        "headers": config_data.headers,
        "env": config_data.env,
        "auth_config": config_data.auth_config,
        "auth_token": config_data.auth_token,
        "single_usage": config_data.single_usage,
        "tools": config_data.tools,
        "audience": config_data.audience,
        "allow_issuer_prefix_match": config_data.allow_issuer_prefix_match,
        "bucket_key": config_data.bucket_key,
    }
    return SimpleNamespace(id=config_id, name=name, config=config_data)


def test_mcp_server_from_config_returns_mcp_server_details() -> None:
    from codemie.rest_api.models.assistant import MCPServerDetails
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config(config_id="cfg-1", name="my-server", url="https://mcp.example.com/")

    # AttributeError today ('MCPToolkitService' has no attribute '_mcp_server_from_config') → RED
    result = MCPToolkitService._mcp_server_from_config(mcp_config)

    assert isinstance(result, MCPServerDetails)
    assert result.name == "my-server"
    assert result.mcp_config_id == "cfg-1"
    assert result.config.url == "https://mcp.example.com/"
    assert result.config.auth_config is None


def test_mcp_server_from_config_allow_issuer_prefix_match_round_trip() -> None:
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config(
        config_id="rovo-cfg",
        name="atlassian-rovo",
        url="https://rovo.atlassian.net/sse",
        allow_issuer_prefix_match=True,
    )

    result = MCPToolkitService._mcp_server_from_config(mcp_config)

    assert result.config.allow_issuer_prefix_match is True, (
        "allow_issuer_prefix_match must survive the MCPServerConfigData → MCPServerConfig mapping "
        "so the discovery candidate carries it to _resolve_discovered_candidate_payload"
    )


def test_mcp_server_from_config_candidate_has_required_fields() -> None:
    """_build_discovery_candidate_from_challenge reads mcp_config_id from outer MCPServerDetails
    and allow_issuer_prefix_match from inner MCPServerConfig — both must be set."""
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config(config_id="cfg-2", name="srv", url="https://srv.example.com/")
    result = MCPToolkitService._mcp_server_from_config(mcp_config)

    assert result.mcp_config_id == "cfg-2"
    assert result.name == "srv"
    assert result.config.url == "https://srv.example.com/"
    assert result.config.auth_config is None, "auth_config must be None so _build_discovery_candidate_from_challenge passes"
```

- [ ] **Step 2 [RED]: Run tests to confirm failure**

```bash
poetry run pytest tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py \
  -k "test_mcp_server_from_config" -v 2>&1 | tail -15
```

Expected: `FAILED ... AttributeError: type object 'MCPToolkitService' has no attribute '_mcp_server_from_config'`

- [ ] **[GREEN — follow-up pass] Step 3: Add `_mcp_server_from_config`**

In `toolkit_service.py`, after `_build_discovery_candidate_from_challenge` (after line 566):

```python
@staticmethod
def _mcp_server_from_config(mcp_config: Any) -> MCPServerDetails:
    from codemie.rest_api.models.assistant import MCPServerDetails
    from codemie.service.mcp.models import MCPServerConfig
    return MCPServerDetails(
        name=mcp_config.name,
        mcp_config_id=mcp_config.id,
        config=MCPServerConfig(**mcp_config.config.model_dump()),
    )
```

- [ ] **[GREEN — follow-up pass] Step 4: Run tests**

```bash
poetry run pytest tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py \
  -k "test_mcp_server_from_config" -v 2>&1 | tail -10
```

Expected: `3 passed`

- [ ] **[GREEN — follow-up pass] Step 5: Commit**

```bash
git add src/codemie/service/mcp/toolkit_service.py \
        tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py
git commit -m "EPMCDME-13049: Add _mcp_server_from_config static method to MCPToolkitService"
```

---

## Task 3: `ensure_discovered_snapshot_for_server` classmethod + catalog-survival test

**Files:**
- Modify: `src/codemie/service/mcp/toolkit_service.py` (add classmethod after `_mcp_server_from_config`)
- Test: `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py`

- [ ] **Step 1 [RED]: Write failing tests**

Add to `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py`:

```python
# ── ensure_discovered_snapshot_for_server ────────────────────────────────────

import httpx


def _make_401_exception(www_authenticate: str = 'Bearer realm="test"') -> Exception:
    """Build an MCPToolLoadException wrapping a 401 httpx.HTTPStatusError."""
    from codemie.service.mcp.models import MCPToolLoadException

    response = httpx.Response(
        status_code=401,
        headers={"WWW-Authenticate": www_authenticate},
        request=httpx.Request("GET", "https://mcp.example.com/"),
    )
    http_error = httpx.HTTPStatusError("401 Unauthorized", request=response.request, response=response)
    return MCPToolLoadException("test-server", http_error)


def test_ensure_discovered_snapshot_returns_flow_id_on_challenge(monkeypatch) -> None:
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config(config_id="cfg-discover", name="disco", url="https://mcp.example.com/")
    exc = _make_401_exception()

    monkeypatch.setattr(
        MCPToolkitService,
        "_process_single_mcp_server",
        classmethod(lambda cls, **kwargs: (_ for _ in ()).throw(exc)),
    )
    monkeypatch.setattr(
        MCPToolkitService,
        "_run_discovery_probe_and_collect_failures",
        classmethod(lambda cls, **kwargs: ([{"discovered_flow_id": "flow-healed-1"}], [])),
    )

    # AttributeError today → RED
    result = MCPToolkitService.ensure_discovered_snapshot_for_server(
        mcp_config=mcp_config,
        user_id="user-1",
        session_binding_hash="binding-hash-1",
    )

    assert result == "flow-healed-1"


def test_ensure_discovered_snapshot_returns_none_when_no_401(monkeypatch) -> None:
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config(config_id="cfg-open", name="open-server", url="https://mcp.example.com/")

    # Server is accessible without auth — no exception raised
    monkeypatch.setattr(
        MCPToolkitService,
        "_process_single_mcp_server",
        classmethod(lambda cls, **kwargs: []),
    )

    result = MCPToolkitService.ensure_discovered_snapshot_for_server(
        mcp_config=mcp_config,
        user_id="user-1",
        session_binding_hash="binding-hash-1",
    )

    assert result is None


def test_ensure_discovered_snapshot_returns_none_when_401_has_no_www_authenticate(monkeypatch) -> None:
    from codemie.service.mcp.models import MCPToolLoadException
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config(config_id="cfg-bare401", name="bare", url="https://mcp.example.com/")

    response = httpx.Response(
        status_code=401,
        headers={},  # no WWW-Authenticate
        request=httpx.Request("GET", "https://mcp.example.com/"),
    )
    http_error = httpx.HTTPStatusError("401", request=response.request, response=response)
    exc = MCPToolLoadException("bare", http_error)

    monkeypatch.setattr(
        MCPToolkitService,
        "_process_single_mcp_server",
        classmethod(lambda cls, **kwargs: (_ for _ in ()).throw(exc)),
    )

    result = MCPToolkitService.ensure_discovered_snapshot_for_server(
        mcp_config=mcp_config,
        user_id="user-1",
        session_binding_hash="binding-hash-1",
    )

    assert result is None


def test_ensure_discovered_snapshot_returns_none_on_mcp_auth_required(monkeypatch) -> None:
    from codemie.core.exceptions import MCPAuthenticationRequiredException
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config(config_id="cfg-auth", name="auth-server", url="https://mcp.example.com/")

    monkeypatch.setattr(
        MCPToolkitService,
        "_process_single_mcp_server",
        classmethod(lambda cls, **kwargs: (_ for _ in ()).throw(MCPAuthenticationRequiredException({}))),
    )

    result = MCPToolkitService.ensure_discovered_snapshot_for_server(
        mcp_config=mcp_config,
        user_id="user-1",
        session_binding_hash="binding-hash-1",
    )

    assert result is None


def test_ensure_discovered_snapshot_uses_real_user_id_for_probe_not_connect(monkeypatch) -> None:
    """user_id=None for credential-less connect, real user_id for probe so binding stores correctly."""
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config(config_id="cfg-uid", name="uid-server", url="https://mcp.example.com/")
    exc = _make_401_exception()

    connect_kwargs: dict = {}
    probe_kwargs: dict = {}

    def fake_process(**kwargs):
        connect_kwargs.update(kwargs)
        raise exc

    def fake_probe(**kwargs):
        probe_kwargs.update(kwargs)
        return [{"discovered_flow_id": "flow-uid-1"}], []

    monkeypatch.setattr(MCPToolkitService, "_process_single_mcp_server", classmethod(lambda cls, **kw: fake_process(**kw)))
    monkeypatch.setattr(MCPToolkitService, "_run_discovery_probe_and_collect_failures", classmethod(lambda cls, **kw: fake_probe(**kw)))

    MCPToolkitService.ensure_discovered_snapshot_for_server(
        mcp_config=mcp_config,
        user_id="real-user-id",
        session_binding_hash="binding-hash-abc",
    )

    assert connect_kwargs.get("user_id") is None, "credential-less connect must use user_id=None"
    assert probe_kwargs.get("user_id") == "real-user-id", "probe must use the real user_id for correct binding storage"
    assert probe_kwargs.get("session_binding_hash") == "binding-hash-abc"


def test_ensure_discovered_snapshot_ssrf_path_via_probe_not_raw_http(monkeypatch) -> None:
    """Heal always uses _run_discovery_probe_and_collect_failures (SSRF-gated), never a raw client."""
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config(config_id="cfg-ssrf", name="ssrf-server", url="https://mcp.example.com/")
    exc = _make_401_exception()

    probe_called = []

    monkeypatch.setattr(MCPToolkitService, "_process_single_mcp_server", classmethod(lambda cls, **kw: (_ for _ in ()).throw(exc)))
    monkeypatch.setattr(
        MCPToolkitService,
        "_run_discovery_probe_and_collect_failures",
        classmethod(lambda cls, **kw: probe_called.append(kw) or ([{"discovered_flow_id": "flow-ssrf"}], [])),
    )

    result = MCPToolkitService.ensure_discovered_snapshot_for_server(
        mcp_config=mcp_config,
        user_id="user-1",
        session_binding_hash="binding-1",
    )

    assert result == "flow-ssrf"
    assert len(probe_called) == 1, "SSRF-gated probe must be called exactly once"


def test_ensure_discovered_snapshot_catalog_resolution_survives(monkeypatch) -> None:
    """_mcp_server_from_config must produce MCPServerDetails that passes _build_mcp_server_config
    catalog check — the most common silent failure point if mcp_config_id or inline config is wrong."""
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mcp_config = _make_mcp_config(config_id="catalog-cfg", name="catalog-server", url="https://mcp.example.com/")
    mcp_server = MCPToolkitService._mcp_server_from_config(mcp_config)

    # _build_mcp_server_config calls MCPAccessControlService.resolve_catalog_config when no inline config,
    # or uses the inline config directly. Since _mcp_server_from_config sets an inline config,
    # the method must not return None (which would mean the server is silently skipped).
    server_config = MCPToolkitService._build_mcp_server_config(mcp_server, user_id=None, project_name=None)

    assert server_config is not None, (
        "_mcp_server_from_config must produce MCPServerDetails with inline config that survives "
        "_build_mcp_server_config — otherwise ensure_... silently returns [] instead of connecting"
    )
    assert server_config.url == "https://mcp.example.com/"
```

- [ ] **Step 2 [RED]: Run tests to confirm failure**

```bash
poetry run pytest tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py \
  -k "test_ensure_discovered_snapshot" -v 2>&1 | tail -20
```

Expected: most fail with `AttributeError: type object 'MCPToolkitService' has no attribute 'ensure_discovered_snapshot_for_server'` (the catalog test may fail differently if `_mcp_server_from_config` also absent).

- [ ] **[GREEN — follow-up pass] Step 3: Add `ensure_discovered_snapshot_for_server`**

In `toolkit_service.py`, after `_mcp_server_from_config`:

```python
@classmethod
def ensure_discovered_snapshot_for_server(
    cls,
    *,
    mcp_config: Any,
    user_id: str,
    session_binding_hash: str,
) -> str | None:
    mcp_server = cls._mcp_server_from_config(mcp_config)
    try:
        cls._process_single_mcp_server(
            mcp_server=mcp_server,
            default_toolkit_service=cls.get_instance(),
            user_id=None,
            skip_auth_resolution=True,
        )
        return None
    except MCPToolLoadException as exc:
        candidate = cls._build_discovery_candidate_from_challenge(mcp_server, exc)
        if candidate is None:
            return None
    except (MCPAuthenticationRequiredException, BrokerAuthRequiredException):
        return None

    auth_failures, _ = cls._run_discovery_probe_and_collect_failures(
        discovery_candidates=[candidate],
        user_id=user_id,
        session_binding_hash=session_binding_hash,
        workflow_execution_id=None,
    )
    if not auth_failures:
        return None
    return auth_failures[0].get("discovered_flow_id")
```

- [ ] **[GREEN — follow-up pass] Step 4: Run tests**

```bash
poetry run pytest tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py \
  -k "test_ensure_discovered_snapshot" -v 2>&1 | tail -15
```

Expected: `7 passed`

- [ ] **[GREEN — follow-up pass] Step 5: Commit**

```bash
git add src/codemie/service/mcp/toolkit_service.py \
        tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py
git commit -m "EPMCDME-13049: Add ensure_discovered_snapshot_for_server to MCPToolkitService"
```

---

## Task 4: Heal wiring in `build_discovered_oauth2_initiate_response`

**Files:**
- Modify: `src/codemie/enterprise/mcp_auth/_initiate.py:102-176` (`build_discovered_oauth2_initiate_response`)
- Test: `tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py`

- [ ] **Step 1 [RED]: Write failing tests**

Add to `tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py`:

```python
# ── Approach C heal path tests ────────────────────────────────────────────────

def _build_discovered_mcp_config(
    *,
    owner_id: str = "user-1",
    is_public: bool = False,
    url: str = "https://mcp.example.com/",
    allow_issuer_prefix_match: bool = False,
) -> object:
    """MCP config with auth_config=None (discovered OAuth2 server)."""
    return SimpleNamespace(
        id="mcp-config-disc-1",
        name="discovered-server",
        user_id=owner_id,
        is_public=is_public,
        config=SimpleNamespace(
            url=url,
            auth_config=None,
            allow_issuer_prefix_match=allow_issuer_prefix_match,
        ),
    )


def _build_discovered_snapshot(
    *,
    discovered_flow_id: str = "flow-healed-1",
    mcp_config_id: str = "mcp-config-disc-1",
    user_id: str = "user-1",
    status: str = "authentication_required",
) -> object:
    return SimpleNamespace(
        discovered_flow_id=discovered_flow_id,
        discovered_auth_id="auth-id-healed",
        mcp_config_id=mcp_config_id,
        user_id=user_id,
        session_binding_hash="binding-hash-token123",
        status=status,
        flow_config=SimpleNamespace(
            auth_type="oauth2",
            authorization_url="https://idp.example.com/oauth2/authorize",
            token_url="https://idp.example.com/oauth2/token",
            client_id="client-healed",
            client_type="public",
            scopes=["openid"],
            token_delivery={"method": "header"},
        ),
        canonical_resource="https://mcp.example.com/",
        as_hostname="idp.example.com",
        redirect_uri="https://api.example.com/v1/mcp-auth/oauth2/callback",
    )


def _patch_enterprise_for_discovered_build(monkeypatch, *, snapshot: object, auth_url: str = "https://idp.example.com/oauth2/authorize?state=healed") -> None:
    """Minimal enterprise stubs for build_discovered_oauth2_initiate_response to complete."""
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_deps

    # Stub discovered-flow store: return snapshot on get_for_binding
    class _FakeStore:
        def __init__(self, _snapshot: object) -> None:
            self._snapshot = _snapshot

        def get(self, flow_id: str) -> object | None:
            return self._snapshot if flow_id == getattr(self._snapshot, "discovered_flow_id", None) else None

        def get_for_binding(self, user_id: str, session_binding_hash: str, mcp_config_id: str) -> object | None:
            return self._snapshot

    monkeypatch.setattr(mcp_auth_deps, "_mcp_auth_discovered_flow_store", _FakeStore(snapshot))

    # Stub PKCE store and encryption
    monkeypatch.setattr(mcp_auth_deps, "_pkce_store", SimpleNamespace(store=lambda state, pkce: None))
    monkeypatch.setattr(mcp_auth_deps, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))

    # Stub enterprise build response
    import sys
    fake_enterprise = SimpleNamespace(
        build_oauth2_initiate_response=lambda **kw: SimpleNamespace(
            auth_url=auth_url,
            redirect_uri_hostname="api.example.com",
            localhost_warning=False,
            model_dump=lambda: {
                "auth_url": auth_url,
                "redirect_uri_hostname": "api.example.com",
                "localhost_warning": False,
            },
        ),
        MCPAuthRedisUnavailable=Exception,
        OAuth2AuthConfig=SimpleNamespace(model_validate=lambda d: d),
    )
    monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth", fake_enterprise)


def test_initiate_heals_binding_miss_via_inline_discovery(monkeypatch, app_client) -> None:
    """By-binding miss: store has no snapshot → ensure_... heals → 200 with auth_url."""
    from codemie.enterprise.mcp_auth import router as mcp_auth_router
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_deps
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    app, client = app_client
    user = _build_user()
    mcp_config = _build_discovered_mcp_config()
    snapshot = _build_discovered_snapshot()

    # Store: first get_for_binding returns None (miss), second returns snapshot (after heal)
    get_for_binding_calls: list[int] = [0]

    class _HealableStore:
        def get(self, flow_id: str) -> object | None:
            return None

        def get_for_binding(self, user_id: str, session_binding_hash: str, mcp_config_id: str) -> object | None:
            get_for_binding_calls[0] += 1
            return None if get_for_binding_calls[0] == 1 else snapshot

    monkeypatch.setattr(mcp_auth_deps, "_mcp_auth_discovered_flow_store", _HealableStore())
    monkeypatch.setattr(mcp_auth_deps, "_pkce_store", SimpleNamespace(store=lambda state, pkce: None))
    monkeypatch.setattr(mcp_auth_deps, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))

    ensure_calls: list[dict] = []

    def fake_ensure(cls_or_self=None, *, mcp_config, user_id, session_binding_hash, **kwargs):
        ensure_calls.append({"mcp_config": mcp_config, "user_id": user_id})
        return "flow-healed-1"

    monkeypatch.setattr(MCPToolkitService, "ensure_discovered_snapshot_for_server", classmethod(fake_ensure))

    import sys
    auth_url = "https://idp.example.com/oauth2/authorize?state=healed"
    monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth", SimpleNamespace(
        build_oauth2_initiate_response=lambda **kw: SimpleNamespace(
            auth_url=auth_url,
            redirect_uri_hostname="api.example.com",
            localhost_warning=False,
            model_dump=lambda: {"auth_url": auth_url, "redirect_uri_hostname": "api.example.com", "localhost_warning": False},
        ),
        MCPAuthRedisUnavailable=Exception,
    ))

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config)

    # Without heal wiring in build_discovered_oauth2_initiate_response this returns 400 → RED
    response = client.post("/v1/mcp-auth/oauth2/initiate", json={"mcp_config_id": mcp_config.id})

    assert response.status_code == 200, f"Expected 200 after heal, got {response.status_code}: {response.text}"
    assert response.json()["auth_url"] == auth_url
    assert len(ensure_calls) == 1, "ensure_discovered_snapshot_for_server must be called exactly once on miss"
    assert get_for_binding_calls[0] == 2, "store.get_for_binding must be called twice (probe then re-read)"


def test_initiate_heals_by_id_expired_via_inline_discovery(monkeypatch, app_client) -> None:
    """By-id-expired: store.get(flow_id) returns None → ensure_... heals → re-read by binding → 200."""
    from codemie.enterprise.mcp_auth import router as mcp_auth_router
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_deps
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    app, client = app_client
    user = _build_user()
    mcp_config = _build_discovered_mcp_config()
    snapshot = _build_discovered_snapshot()

    class _ExpiredIdStore:
        def get(self, flow_id: str) -> object | None:
            return None  # stale id — expired

        def get_for_binding(self, user_id: str, session_binding_hash: str, mcp_config_id: str) -> object | None:
            return snapshot  # heal stored it by binding

    monkeypatch.setattr(mcp_auth_deps, "_mcp_auth_discovered_flow_store", _ExpiredIdStore())
    monkeypatch.setattr(mcp_auth_deps, "_pkce_store", SimpleNamespace(store=lambda state, pkce: None))
    monkeypatch.setattr(mcp_auth_deps, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))

    monkeypatch.setattr(MCPToolkitService, "ensure_discovered_snapshot_for_server", classmethod(lambda cls, **kw: "flow-healed-2"))

    import sys
    auth_url = "https://idp.example.com/oauth2/authorize?state=healed-id"
    monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth", SimpleNamespace(
        build_oauth2_initiate_response=lambda **kw: SimpleNamespace(
            auth_url=auth_url,
            redirect_uri_hostname="api.example.com",
            localhost_warning=False,
            model_dump=lambda: {"auth_url": auth_url, "redirect_uri_hostname": "api.example.com", "localhost_warning": False},
        ),
        MCPAuthRedisUnavailable=Exception,
    ))

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config)

    # With stale discovered_flow_id — heal must re-read by binding, NOT re-read by stale id → RED until wired
    response = client.post(
        "/v1/mcp-auth/oauth2/initiate",
        json={"mcp_config_id": mcp_config.id, "discovered_flow_id": "stale-flow-id"},
    )

    assert response.status_code == 200, f"Expected 200 after heal of expired id, got {response.status_code}: {response.text}"
    assert response.json()["auth_url"] == auth_url


def test_initiate_returns_400_not_500_when_discovery_fails(monkeypatch, app_client) -> None:
    """If ensure_... returns None, the original 400 is raised — never a 500."""
    from codemie.enterprise.mcp_auth import router as mcp_auth_router
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_deps
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    app, client = app_client
    user = _build_user()
    mcp_config = _build_discovered_mcp_config()

    class _MissStore:
        def get(self, flow_id: str) -> object | None:
            return None

        def get_for_binding(self, user_id: str, session_binding_hash: str, mcp_config_id: str) -> object | None:
            return None

    monkeypatch.setattr(mcp_auth_deps, "_mcp_auth_discovered_flow_store", _MissStore())
    monkeypatch.setattr(mcp_auth_deps, "_pkce_store", SimpleNamespace(store=lambda state, pkce: None))
    monkeypatch.setattr(mcp_auth_deps, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))

    # ensure_... returns None → discovery failed
    monkeypatch.setattr(MCPToolkitService, "ensure_discovered_snapshot_for_server", classmethod(lambda cls, **kw: None))

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config)

    response = client.post("/v1/mcp-auth/oauth2/initiate", json={"mcp_config_id": mcp_config.id})

    assert response.status_code == 400, f"Expected 400 (not 500), got {response.status_code}"
    assert response.status_code != 500, "Discovery failure must never surface as 500"


def test_initiate_valid_snapshot_does_not_trigger_heal(monkeypatch, app_client) -> None:
    """If the snapshot exists already, ensure_... must NOT be called (no-op guard)."""
    from codemie.enterprise.mcp_auth import router as mcp_auth_router
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_deps
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    app, client = app_client
    user = _build_user()
    mcp_config = _build_discovered_mcp_config()
    snapshot = _build_discovered_snapshot()

    class _HitStore:
        def get(self, flow_id: str) -> object | None:
            return snapshot

        def get_for_binding(self, user_id: str, session_binding_hash: str, mcp_config_id: str) -> object | None:
            return snapshot

    monkeypatch.setattr(mcp_auth_deps, "_mcp_auth_discovered_flow_store", _HitStore())
    monkeypatch.setattr(mcp_auth_deps, "_pkce_store", SimpleNamespace(store=lambda state, pkce: None))
    monkeypatch.setattr(mcp_auth_deps, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))

    ensure_calls: list[int] = [0]

    def must_not_be_called(cls_or_self=None, **kwargs):
        ensure_calls[0] += 1
        return "should-not-reach-this"

    monkeypatch.setattr(MCPToolkitService, "ensure_discovered_snapshot_for_server", classmethod(must_not_be_called))

    import sys
    auth_url = "https://idp.example.com/oauth2/authorize?state=existing"
    monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth", SimpleNamespace(
        build_oauth2_initiate_response=lambda **kw: SimpleNamespace(
            auth_url=auth_url,
            redirect_uri_hostname="api.example.com",
            localhost_warning=False,
            model_dump=lambda: {"auth_url": auth_url, "redirect_uri_hostname": "api.example.com", "localhost_warning": False},
        ),
        MCPAuthRedisUnavailable=Exception,
    ))

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config)

    response = client.post("/v1/mcp-auth/oauth2/initiate", json={"mcp_config_id": mcp_config.id})

    assert ensure_calls[0] == 0, "ensure_... must NOT be called when a valid snapshot exists"
    assert response.status_code == 200


def test_initiate_auth_rejection_precedes_discovery(monkeypatch, app_client) -> None:
    """_check_mcp_config_access raises 403 before ensure_... is ever called."""
    from codemie.enterprise.mcp_auth import router as mcp_auth_router
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    app, client = app_client
    other_user = _build_user(id="other-user", auth_token="Bearer other-token")
    mcp_config = _build_discovered_mcp_config(owner_id="owner-user")  # different owner

    ensure_calls: list[int] = [0]
    monkeypatch.setattr(MCPToolkitService, "ensure_discovered_snapshot_for_server", classmethod(lambda cls, **kw: ensure_calls.__setitem__(0, ensure_calls[0] + 1) or "flow"))

    app.dependency_overrides[router_authenticate] = lambda: other_user
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config)

    response = client.post("/v1/mcp-auth/oauth2/initiate", json={"mcp_config_id": mcp_config.id})

    assert response.status_code == 403, "Non-owner must be rejected before any discovery"
    assert ensure_calls[0] == 0, "ensure_... must never run after a 403"


def test_initiate_infra_error_in_probe_propagates_as_503(monkeypatch, app_client) -> None:
    """Redis outage on the non-raising probe must NOT trigger heal — it propagates as 503."""
    from codemie.enterprise.mcp_auth import router as mcp_auth_router
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_deps
    from codemie.core.exceptions import ExtendedHTTPException
    from codemie.service.mcp.toolkit_service import MCPToolkitService
    from fastapi import status as http_status

    app, client = app_client
    user = _build_user()
    mcp_config = _build_discovered_mcp_config()

    class _RedisDownStore:
        def get(self, flow_id: str) -> object | None:
            raise ExtendedHTTPException(
                code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                message="MCP auth temporarily unavailable",
                details="Redis is down",
                help="Try again later.",
            )

        def get_for_binding(self, user_id: str, session_binding_hash: str, mcp_config_id: str) -> object | None:
            raise ExtendedHTTPException(
                code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                message="MCP auth temporarily unavailable",
                details="Redis is down",
                help="Try again later.",
            )

    monkeypatch.setattr(mcp_auth_deps, "_mcp_auth_discovered_flow_store", _RedisDownStore())

    ensure_calls: list[int] = [0]
    monkeypatch.setattr(MCPToolkitService, "ensure_discovered_snapshot_for_server", classmethod(lambda cls, **kw: ensure_calls.__setitem__(0, ensure_calls[0] + 1) or "flow"))

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config)

    response = client.post("/v1/mcp-auth/oauth2/initiate", json={"mcp_config_id": mcp_config.id})

    assert response.status_code == 503, f"Redis outage must surface as 503, got {response.status_code}"
    assert ensure_calls[0] == 0, "ensure_... must NOT run on infra error — only on genuine absence"
```

- [ ] **Step 2 [RED]: Run tests to confirm failure**

```bash
poetry run pytest tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py \
  -k "heals_binding_miss or heals_by_id or returns_400_not_500 or valid_snapshot_does_not or auth_rejection_precedes or infra_error" \
  -v 2>&1 | tail -30
```

Expected outcomes per test:
- `test_initiate_heals_binding_miss_via_inline_discovery` — FAIL: 400 instead of 200 (no heal wiring)
- `test_initiate_heals_by_id_expired_via_inline_discovery` — FAIL: 400 instead of 200
- `test_initiate_returns_400_not_500_when_discovery_fails` — PASS (already returns 400 today) or FAIL depending on wiring
- `test_initiate_valid_snapshot_does_not_trigger_heal` — PASS (no wiring = no call to ensure_...)
- `test_initiate_auth_rejection_precedes_discovery` — PASS (403 is already raised by _check_mcp_config_access)
- `test_initiate_infra_error_in_probe_propagates_as_503` — FAIL: the existing loader converts this differently

At least the two heal-path tests are RED, confirming the plan's stop condition.

- [ ] **[GREEN — follow-up pass] Step 3: Add non-raising probe + heal in `build_discovered_oauth2_initiate_response`**

In `_initiate.py`, replace the ternary at lines 120–128:

```python
session_binding_hash = _get_authenticated_bearer_token_hash(user)

# Non-raising probe: only genuine None triggers heal; infra errors propagate as-is
_store = _deps._require_initialized_discovered_flow_store()
_probe = _store.get(discovered_flow_id) if discovered_flow_id else \
    _store.get_for_binding(user.id, session_binding_hash, mcp_config.id)

# Heal on genuine absence
if _probe is None:
    from codemie.service.mcp.toolkit_service import MCPToolkitService  # lazy import avoids cycle
    _new_flow_id = MCPToolkitService.ensure_discovered_snapshot_for_server(
        mcp_config=mcp_config,
        user_id=user.id,
        session_binding_hash=session_binding_hash,
    )
    if _new_flow_id is not None:
        _probe = _store.get_for_binding(user.id, session_binding_hash, mcp_config.id)

# Fallback: original raising loaders — same 400 as today on total miss
if _probe is not None:
    snapshot = _probe
else:
    snapshot = (
        _deps._load_discovered_flow_snapshot_or_error(discovered_flow_id)
        if discovered_flow_id
        else _load_discovered_flow_snapshot_for_binding_or_error(
            user_id=user.id,
            session_binding_hash=session_binding_hash,
            mcp_config_id=mcp_config.id,
        )
    )
```

- [ ] **[GREEN — follow-up pass] Step 4: Run tests**

```bash
poetry run pytest tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py \
  -k "heals_binding_miss or heals_by_id or returns_400_not_500 or valid_snapshot_does_not or auth_rejection_precedes or infra_error" \
  -v 2>&1 | tail -20
```

Expected: all 6 new tests pass; no existing tests broken.

Full regression run:

```bash
poetry run pytest tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py -v 2>&1 | tail -20
```

- [ ] **[GREEN — follow-up pass] Step 5: Commit**

```bash
git add src/codemie/enterprise/mcp_auth/_initiate.py \
        tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py
git commit -m "EPMCDME-13049: Wire inline discovery heal in build_discovered_oauth2_initiate_response"
```

---

## Summary of this pass (RED tests)

Run all failing tests at once to see the full RED set:

```bash
poetry run pytest \
  tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py \
  tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py \
  -k "skip_auth_resolution or mcp_server_from_config or ensure_discovered_snapshot or heals_binding_miss or heals_by_id or returns_400_not_500 or auth_rejection_precedes or infra_error" \
  -v 2>&1 | tail -40
```

All tests with `ensure_...` calls fail with `AttributeError`. Heal-path integration tests fail with `AssertionError: Expected 200`. Auth-ordering and 400-parity tests may already pass (they test existing behavior) — that is correct for those scenarios.

**Stop here.** The failing tests are in place and the plan is reviewable. Follow-up pass: implement production code tasks 1–4 (Steps marked `[GREEN — follow-up pass]`) in order.
