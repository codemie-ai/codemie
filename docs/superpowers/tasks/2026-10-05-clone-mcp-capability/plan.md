> **Superseded in part (2026-10-07):** the secret stripping, `copy_values`, `stripped_fields` and placeholder-warning parts described here were removed; the clone is now a full copy. Current requirements: `spec.md` (revised) in this directory; change plan: `docs/superpowers/tasks/2026-10-07-clone-mcp-full-copy-remove-stripping/plan.md`. Kept as an execution record.

# Clone MCP Configuration (backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** `POST /v1/mcp-configs/{id}/clone` creates an independent, secret-stripped copy of an MCP configuration named `Copy N of <base>`. Spec: `spec.md` in this directory.

**Architecture:** Pure logic (placeholders, URL/args/config sanitizing, `copy_values` validation, name allocation) lives in three new modules under `src/codemie/service/mcp/`. `MCPConfigService.clone` orchestrates them and builds the row itself (it does NOT call `create`, which forces `is_system=True`). A thin admin-only route delegates to it. Clone-policy metadata on the models decides which descriptive fields are copied, and a drift-guard test forces a decision for every new field.

**Tech Stack:** Python 3.12, FastAPI, SQLModel/PostgreSQL, pytest. Run tests with `poetry run pytest <path> -v`. Commit per task using the repository's existing convention.

## Global Constraints

- Text placeholder `<REQUIRED: re-enter value>` (no quotes, backslashes or control characters); URL token `REQUIRED_REENTER_VALUE`.
- Name is `Copy N of <base>`; `<base>` is the source name truncated so the final name is at most 255 characters; N is the lowest positive integer unused by the cloning user. Scan selects ONLY the `name` column, filters with `LIKE ... ESCAPE` (portable SQL, `%`, `_` and backslash escaped), then applies a strict regex in Python. Scan and insert share one transaction. No lock, no migration, no new constraint.
- Clone row: `is_system=False`, `is_public` copied, `usage_count=0`, `is_active=True`, `user_id` and `created_by` = cloning user. `create` semantics unchanged. Permission stays `authenticate` + `admin_access_only`.
- Never copyable even if requested: `auth_token`, `auth_config` and `auth_config.*`, `url.userinfo`, `url.fragment` (fragment is always removed). Copyable grammar: `headers.<Key>`, `env.<KEY>`, `args[<index>]`, `url.query.<key>`.
- Order of checks: permission, source lookup (404), `copy_values` validation (422), then any write. Clone is all-or-nothing.
- Response is a flat `MCPConfigCloneResponse` (no envelope); secrets never appear in it. Errors are `ExtendedHTTPException(code, message, details, help)`.
- Out of scope: UI, telemetry/clone events, bulk clone, re-linking assistants, wider permissions, migrations.

## File Structure

- Create `src/codemie/service/mcp/clone_constants.py` (placeholders), `clone_sanitizer.py` (URL/args/config sanitizing, validation, placeholder scan), `clone_naming.py` (name allocation).
- Modify `src/codemie/rest_api/models/mcp_config.py` (policy metadata, clone models), `src/codemie/service/mcp_config_service.py` (`clone`, warning), `src/codemie/rest_api/routers/mcp_config.py` (route).
- Tests: `tests/codemie/rest_api/models/test_mcp_config_clone_policy.py`, `tests/codemie/service/mcp/test_clone_*.py`, `tests/codemie/service/test_mcp_config_service_clone.py`, `tests/codemie/service/test_mcp_config_service_placeholder_warning.py`, `tests/codemie/rest_api/routers/test_mcp_config.py`.

---

### Task 1: Clone policy metadata, request/response models, drift guard

**Files:**
- Modify: `src/codemie/rest_api/models/mcp_config.py`
- Test: `tests/codemie/rest_api/models/test_mcp_config_clone_policy.py`

**Interfaces:**
- Produces: `MCPServerConfigData.CLONE_POLICY` and `MCPConfig.CLONE_POLICY` (`ClassVar[dict[str, str]]`), `CLONE_POLICY_VALUES: frozenset[str]`, `get_clone_policy(model_cls) -> dict[str, str | None]` (declared policy per model field, `None` when undeclared, which the clone code treats as `drop`), `MCPConfigCloneRequest(copy_values: list[str])`, `MCPConfigCloneResponse(MCPConfigResponse)` with `stripped_fields: list[str]`.

Test-first: yes — drift guard fails when a field has no `CLONE_POLICY` decision (including a field added by a subclass), and every current field of `MCPServerConfigData` and `MCPConfig` has one.

- [ ] **Step 1: Write the failing test** at `tests/codemie/rest_api/models/test_mcp_config_clone_policy.py`

```python
import pytest

from codemie.rest_api.models.mcp_config import (
    CLONE_POLICY_VALUES,
    MCPConfig,
    MCPConfigCloneRequest,
    MCPConfigCloneResponse,
    MCPConfigResponse,
    MCPServerConfigData,
    get_clone_policy,
)


@pytest.mark.parametrize("model_cls", [MCPServerConfigData, MCPConfig])
def test_every_field_has_explicit_clone_policy(model_cls):
    policy = get_clone_policy(model_cls)

    undeclared = sorted(name for name, value in policy.items() if value is None)

    assert not undeclared, f"Add a CLONE_POLICY decision (drop/copy/blank_values) for: {undeclared}"
    assert set(policy.values()) <= CLONE_POLICY_VALUES


@pytest.mark.parametrize("model_cls", [MCPServerConfigData, MCPConfig])
def test_policy_has_no_stale_entries(model_cls):
    assert set(model_cls.CLONE_POLICY) <= set(model_cls.model_fields)


def test_unannotated_subclass_field_is_reported_as_undeclared():
    class Extended(MCPServerConfigData):
        new_secret: str | None = None

    assert get_clone_policy(Extended)["new_secret"] is None


def test_clone_request_defaults_to_empty_copy_values():
    assert MCPConfigCloneRequest().copy_values == []


def test_clone_response_extends_config_response_with_stripped_fields():
    assert issubclass(MCPConfigCloneResponse, MCPConfigResponse)
    assert "stripped_fields" in MCPConfigCloneResponse.model_fields
```

- [ ] **Step 2: Run it to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/models/test_mcp_config_clone_policy.py -v`
Expected: FAIL with `ImportError: cannot import name 'CLONE_POLICY_VALUES'`.

- [ ] **Step 3: Implement in `src/codemie/rest_api/models/mcp_config.py`**

3a. Change `from typing import Any` to:

```python
from typing import Any, ClassVar
```

3b. In `MCPServerConfigData`, directly after the `allow_issuer_prefix_match` field (the last field of the class), add:

```python
    # Clone policy: drop | copy | blank_values. A new field MUST be added here (drift-guard test enforces it).
    # auth_config is "copy" because the clone sanitizer strips secrets/id, and drops it entirely for confidential OAuth2.
    CLONE_POLICY: ClassVar[dict[str, str]] = {
        "command": "copy",
        "url": "blank_values",
        "args": "blank_values",
        "headers": "blank_values",
        "env": "blank_values",
        "type": "copy",
        "auth_token": "drop",
        "single_usage": "copy",
        "tools": "copy",
        "audience": "copy",
        "auth_config": "copy",
        "allow_issuer_prefix_match": "copy",
    }
```

3c. In `MCPConfig`, directly after the `is_active` field and before the `# Indexes for common queries` comment, add:

```python
    # Clone policy: "copy" fields are deep-copied by MCPConfigService.clone; everything else is set explicitly
    # or dropped. A new field MUST be added here (drift-guard test enforces it).
    CLONE_POLICY: ClassVar[dict[str, str]] = {
        "id": "drop",
        "date": "drop",
        "update_date": "drop",
        "name": "drop",
        "description": "copy",
        "server_home_url": "copy",
        "source_url": "copy",
        "logo_url": "copy",
        "categories": "copy",
        "config": "blank_values",
        "required_env_vars": "copy",
        "user_id": "drop",
        "is_public": "copy",
        "is_system": "drop",
        "created_by": "drop",
        "usage_count": "drop",
        "is_active": "drop",
    }
```

3d. Append after `MCPConfigListResponse` at the end of the file:

```python
class MCPConfigCloneRequest(BaseModel):
    """Optional body for cloning an MCP configuration."""

    copy_values: list[str] = Field(
        default_factory=list,
        description="Dotted paths (headers.<Key>, env.<KEY>, args[<i>], url.query.<key>) whose values are copied "
        "from the source instead of being replaced by a placeholder",
    )


class MCPConfigCloneResponse(MCPConfigResponse):
    """Clone result: a plain config object plus the paths whose values were NOT copied."""

    stripped_fields: list[str] = Field(default_factory=list)


CLONE_POLICY_VALUES: frozenset[str] = frozenset({"drop", "copy", "blank_values"})


def get_clone_policy(model_cls: type[BaseModel]) -> dict[str, str | None]:
    """Return the declared clone policy for every model field (None when undeclared)."""
    declared: dict[str, str] = getattr(model_cls, "CLONE_POLICY", {})
    return {name: declared.get(name) for name in model_cls.model_fields}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `poetry run pytest tests/codemie/rest_api/models/test_mcp_config_clone_policy.py -v`
Expected: 6 passed.

---

### Task 2: Placeholders and URL / args sanitizing

**Files:**
- Create: `src/codemie/service/mcp/clone_constants.py`, `src/codemie/service/mcp/clone_sanitizer.py`
- Test: `tests/codemie/service/mcp/test_clone_sanitizer_url_args.py`

**Interfaces:**
- Produces: `TEXT_PLACEHOLDER`, `URL_PLACEHOLDER`, `PLACEHOLDER_WARNING_CODE`; `sanitize_url(url: str, copy_paths: set[str]) -> tuple[str, list[str]]`; `sanitize_args(args: list[str], copy_paths: set[str]) -> tuple[list[str], list[str]]`. Second tuple element is the list of stripped dotted paths.

Test-first: yes — the spec example URL becomes `https://REQUIRED_REENTER_VALUE@mcp.example.com:8443/v1/sse?api_key=REQUIRED_REENTER_VALUE&region=REQUIRED_REENTER_VALUE`; fragment removed; copied query keys kept; `--token=abc` and `--api-key abc` are blanked and kept when their index is in `copy_paths`.

- [ ] **Step 1: Write the failing test** at `tests/codemie/service/mcp/test_clone_sanitizer_url_args.py`

```python
from urllib.parse import urlsplit

from codemie.service.mcp.clone_constants import TEXT_PLACEHOLDER, URL_PLACEHOLDER
from codemie.service.mcp.clone_sanitizer import sanitize_args, sanitize_url

SPEC_URL = "https://user:pass@mcp.example.com:8443/v1/sse?api_key=abc123&region=us"


def test_placeholder_text_is_json_safe():
    assert not any(ch in TEXT_PLACEHOLDER for ch in ('"', "\\"))
    assert all(ch.isprintable() for ch in TEXT_PLACEHOLDER)


def test_url_spec_example():
    result, stripped = sanitize_url(SPEC_URL, set())

    assert result == (
        "https://REQUIRED_REENTER_VALUE@mcp.example.com:8443/v1/sse"
        "?api_key=REQUIRED_REENTER_VALUE&region=REQUIRED_REENTER_VALUE"
    )
    assert stripped == ["url.userinfo", "url.query.api_key", "url.query.region"]


def test_url_keeps_scheme_host_port_path_and_still_parses():
    result, _ = sanitize_url(SPEC_URL + "#frag", set())
    parts = urlsplit(result)

    assert (parts.scheme, parts.hostname, parts.port, parts.path) == ("https", "mcp.example.com", 8443, "/v1/sse")
    assert parts.fragment == ""


def test_url_fragment_removed_and_reported():
    _, stripped = sanitize_url("https://h.example.com/p#secret", set())

    assert stripped == ["url.fragment"]


def test_url_without_userinfo_or_query_is_unchanged():
    assert sanitize_url("https://h.example.com/sse", set()) == ("https://h.example.com/sse", [])


def test_url_copied_query_value_is_kept_and_not_reported():
    result, stripped = sanitize_url(SPEC_URL, {"url.query.region"})

    assert "region=us" in result
    assert "api_key=REQUIRED_REENTER_VALUE" in result
    assert "url.query.region" not in stripped


def test_args_inline_secret_flag_is_blanked():
    result, stripped = sanitize_args(["--token=abc", "--region", "us"], set())

    assert result == [f"--token={TEXT_PLACEHOLDER}", "--region", "us"]
    assert stripped == ["args[0]"]


def test_args_separate_secret_value_is_blanked():
    result, stripped = sanitize_args(["--api-key", "abc", "--verbose"], set())

    assert result == ["--api-key", TEXT_PLACEHOLDER, "--verbose"]
    assert stripped == ["args[1]"]


def test_args_copied_index_is_kept():
    args = ["--token=abc", "--api-key", "xyz"]

    result, stripped = sanitize_args(args, {"args[0]", "args[2]"})

    assert result == args
    assert stripped == []


def test_args_flag_followed_by_another_flag_does_not_consume_it():
    result, stripped = sanitize_args(["--password", "--verbose"], set())

    assert result == ["--password", "--verbose"]
    assert stripped == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `poetry run pytest tests/codemie/service/mcp/test_clone_sanitizer_url_args.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codemie.service.mcp.clone_constants'`.

- [ ] **Step 3: Create `src/codemie/service/mcp/clone_constants.py`**

```python
# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""Shared constants for MCP configuration cloning."""

# Replaces header, env and args values. Must not contain quotes, backslashes or control characters.
TEXT_PLACEHOLDER = "<REQUIRED: re-enter value>"

# URL-safe token that replaces URL userinfo and query values.
URL_PLACEHOLDER = "REQUIRED_REENTER_VALUE"

# ConfigWarning.code emitted when a placeholder is still present in a saved config.
PLACEHOLDER_WARNING_CODE = "placeholder_value_present"
```

- [ ] **Step 4: Create `src/codemie/service/mcp/clone_sanitizer.py`** (same license header as above)

```python
"""Secret stripping and copy_values handling for MCP configuration cloning."""

from __future__ import annotations

import re
from typing import Any, Iterator
from urllib.parse import unquote, urlsplit, urlunsplit

from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.mcp_config import MCPServerConfigData
from codemie.service.mcp.clone_constants import TEXT_PLACEHOLDER, URL_PLACEHOLDER

_SECRET_WORDS = r"(?:token|api[-_]?key|apikey|password|passwd|secret|key)"
_FLAG_WITH_VALUE_RE = re.compile(
    rf"^(?P<flag>--?[\w-]*{_SECRET_WORDS}[\w-]*)=(?P<value>.*)$", re.IGNORECASE | re.DOTALL
)
_FLAG_ONLY_RE = re.compile(rf"^--?[\w-]*{_SECRET_WORDS}[\w-]*$", re.IGNORECASE)


def sanitize_url(url: str, copy_paths: set[str]) -> tuple[str, list[str]]:
    """Blank URL userinfo and query values, drop the fragment; keep scheme, host, port, path and query keys."""
    parts = urlsplit(url)
    stripped: list[str] = []

    netloc = parts.netloc
    if "@" in netloc:
        netloc = f"{URL_PLACEHOLDER}@{netloc.rpartition('@')[2]}"
        stripped.append("url.userinfo")

    segments: list[str] = []
    for segment in parts.query.split("&"):
        if not segment:
            continue
        key = segment.partition("=")[0]
        path = f"url.query.{unquote(key)}"
        if path in copy_paths:
            segments.append(segment)
            continue
        segments.append(f"{key}={URL_PLACEHOLDER}")
        if path not in stripped:
            stripped.append(path)

    if parts.fragment:
        stripped.append("url.fragment")

    return urlunsplit((parts.scheme, netloc, parts.path, "&".join(segments), "")), stripped


def sanitize_args(args: list[str], copy_paths: set[str]) -> tuple[list[str], list[str]]:
    """Blank the value of secret-looking arguments (`--token=v`, `--api-key v`) unless its index is copied."""
    secret_indexes: dict[int, str] = {}
    for index, arg in enumerate(args):
        if _FLAG_WITH_VALUE_RE.match(arg):
            secret_indexes[index] = "inline"
        elif _FLAG_ONLY_RE.match(arg) and index + 1 < len(args) and not args[index + 1].startswith("-"):
            secret_indexes.setdefault(index + 1, "separate")

    result = list(args)
    stripped: list[str] = []
    for index, kind in sorted(secret_indexes.items()):
        path = f"args[{index}]"
        if path in copy_paths:
            continue
        if kind == "inline":
            flag = _FLAG_WITH_VALUE_RE.match(args[index]).group("flag")  # type: ignore[union-attr]
            result[index] = f"{flag}={TEXT_PLACEHOLDER}"
        else:
            result[index] = TEXT_PLACEHOLDER
        stripped.append(path)
    return result, stripped
```

- [ ] **Step 5: Run it to verify it passes**

Run: `poetry run pytest tests/codemie/service/mcp/test_clone_sanitizer_url_args.py -v`
Expected: 10 passed.

---

### Task 3: Config sanitizer, copy_values validation, placeholder scan

**Files:**
- Modify: `src/codemie/service/mcp/clone_sanitizer.py`
- Test: `tests/codemie/service/mcp/test_clone_sanitizer_config.py`

**Interfaces:**
- Consumes: `sanitize_url`, `sanitize_args`, constants from Task 2; `MCPServerConfigData` (Task 1 unchanged fields); `_is_confidential_oauth2_auth_config` from `codemie.service.mcp_config_service` (lazy import, avoids a cycle).
- Produces: `validate_copy_values(raw: list[str], source: MCPServerConfigData) -> set[str]`; `sanitize_config(source: MCPServerConfigData, copy_paths: set[str]) -> tuple[MCPServerConfigData, list[str]]`; `config_contains_placeholder(config: MCPServerConfigData) -> bool`.

Test-first: yes — empty copy set blanks all values with keys intact and the source unmutated; selected paths survive (JSON-special characters round-trip); non-copyable, unknown and missing paths raise 422 naming them; duplicates tolerated; confidential OAuth2 yields `auth_config=None` and no secret; other auth types copy `auth_config` without secret or id.

- [ ] **Step 1: Write the failing test** at `tests/codemie/service/mcp/test_clone_sanitizer_config.py`

```python
import json

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.mcp_config import MCPServerConfigData
from codemie.service.mcp.clone_constants import TEXT_PLACEHOLDER, URL_PLACEHOLDER
from codemie.service.mcp.clone_sanitizer import (
    config_contains_placeholder,
    sanitize_config,
    validate_copy_values,
)

SECRETS = ("secret-1", "secret-2", "secret-3", "secret-4", "p4ssw0rd", "abc123")


def _source(**overrides) -> MCPServerConfigData:
    data = {
        "url": "https://user:p4ssw0rd@mcp.example.com:8443/v1/sse?api_key=abc123&region=us#frag",
        "type": "streamable-http",
        "headers": {"Authorization": "Bearer secret-1", "X-Trace": "trace"},
        "env": {"API_KEY": "secret-2", "RETRIES": 3, "LIST": ["a"]},
        "args": ["--token=secret-3", "--region", "us"],
        "auth_token": "secret-4",
    }
    data.update(overrides)
    return MCPServerConfigData(**data)


def test_empty_copy_set_blanks_every_value_and_keeps_keys():
    source = _source()

    result, stripped = sanitize_config(source, set())

    assert result.headers == {"Authorization": TEXT_PLACEHOLDER, "X-Trace": TEXT_PLACEHOLDER}
    assert result.env == {"API_KEY": TEXT_PLACEHOLDER, "RETRIES": TEXT_PLACEHOLDER, "LIST": TEXT_PLACEHOLDER}
    assert result.args == [f"--token={TEXT_PLACEHOLDER}", "--region", "us"]
    assert result.auth_token is None
    assert result.url == (
        "https://REQUIRED_REENTER_VALUE@mcp.example.com:8443/v1/sse"
        "?api_key=REQUIRED_REENTER_VALUE&region=REQUIRED_REENTER_VALUE"
    )
    assert set(stripped) == {
        "auth_token",
        "headers.Authorization",
        "headers.X-Trace",
        "env.API_KEY",
        "env.RETRIES",
        "env.LIST",
        "args[0]",
        "url.userinfo",
        "url.query.api_key",
        "url.query.region",
        "url.fragment",
    }
    dump = result.model_dump_json()
    assert not any(secret in dump for secret in SECRETS)


def test_source_is_not_mutated():
    source = _source()
    before = source.model_dump()

    sanitize_config(source, set())

    assert source.model_dump() == before


def test_selected_values_are_copied_others_blanked_and_not_reported():
    source = _source()
    copy_paths = validate_copy_values(
        ["headers.Authorization", "env.API_KEY", "url.query.region", "args[0]"], source
    )

    result, stripped = sanitize_config(source, copy_paths)

    assert result.headers == {"Authorization": "Bearer secret-1", "X-Trace": TEXT_PLACEHOLDER}
    assert result.env["API_KEY"] == "secret-2"
    assert result.env["RETRIES"] == TEXT_PLACEHOLDER
    assert result.args[0] == "--token=secret-3"
    assert "region=us" in result.url and "api_key=REQUIRED_REENTER_VALUE" in result.url
    assert not copy_paths & set(stripped)
    assert set(result.headers) == set(source.headers) and set(result.env) == set(source.env)


def test_copied_value_with_json_special_characters_survives_round_trip():
    tricky = 'a"b\\c\nd'
    source = _source(headers={"X-Tricky": tricky})

    result, _ = sanitize_config(source, validate_copy_values(["headers.X-Tricky"], source))

    assert json.loads(json.dumps(result.model_dump()))["headers"]["X-Tricky"] == tricky


def test_blanked_config_survives_json_round_trip():
    result, _ = sanitize_config(_source(), set())

    assert json.loads(json.dumps(result.model_dump())) == result.model_dump()


@pytest.mark.parametrize(
    "path",
    ["auth_token", "auth_config", "auth_config.client_secret", "url.userinfo", "url.fragment"],
)
def test_non_copyable_paths_are_rejected_with_422(path):
    with pytest.raises(ExtendedHTTPException) as exc:
        validate_copy_values([path], _source())

    assert exc.value.code == 422
    assert path in exc.value.details


@pytest.mark.parametrize("path", ["bogus.thing", "headers.Nope", "env.MISSING", "args[9]", "url.query.nope"])
def test_unknown_or_missing_paths_are_rejected_with_422(path):
    with pytest.raises(ExtendedHTTPException) as exc:
        validate_copy_values([path], _source())

    assert exc.value.code == 422
    assert path in exc.value.details


def test_all_offending_paths_are_named_together():
    with pytest.raises(ExtendedHTTPException) as exc:
        validate_copy_values(["auth_token", "bogus", "headers.Nope"], _source())

    assert all(name in exc.value.details for name in ("auth_token", "bogus", "headers.Nope"))


def test_duplicate_entries_are_tolerated():
    assert validate_copy_values(["headers.X-Trace"] * 2, _source()) == {"headers.X-Trace"}


def test_confidential_oauth2_drops_auth_config_entirely():
    auth = {
        "auth_type": "oauth2",
        "client_type": "confidential",
        "client_id": "cid",
        "client_secret": "enc-secret",
        "id": "auth-1",
    }

    result, stripped = sanitize_config(_source(auth_config=auth), set())

    assert result.auth_config is None
    assert "auth_config" in stripped
    assert "enc-secret" not in result.model_dump_json()


@pytest.mark.parametrize("auth_type,client_type", [("oauth2", "public"), ("saml", None)])
def test_other_auth_types_copy_auth_config_without_secret_or_id(auth_type, client_type):
    auth = {
        "auth_type": auth_type,
        "client_id": "cid",
        "client_secret": "leftover",
        "has_client_secret": True,
        "id": "auth-1",
    }
    if client_type:
        auth["client_type"] = client_type

    result, stripped = sanitize_config(_source(auth_config=auth), set())

    assert result.auth_config == {"auth_type": auth_type, "client_id": "cid", **({"client_type": client_type} if client_type else {})}
    assert "auth_config.client_secret" in stripped


def test_no_auth_config_stays_none_and_no_secret_entry_without_secret():
    result, stripped = sanitize_config(_source(), set())

    assert result.auth_config is None
    assert "auth_config" not in stripped and "auth_config.client_secret" not in stripped


def test_config_contains_placeholder_detects_both_constants_and_ignores_copied_values():
    assert config_contains_placeholder(MCPServerConfigData(headers={"X": TEXT_PLACEHOLDER}))
    assert config_contains_placeholder(MCPServerConfigData(url=f"https://{URL_PLACEHOLDER}@h.example.com/sse"))
    assert config_contains_placeholder(MCPServerConfigData(env={"K": [f"a {TEXT_PLACEHOLDER}"]}))
    assert config_contains_placeholder(MCPServerConfigData(args=[f"--token={TEXT_PLACEHOLDER}"]))
    assert not config_contains_placeholder(MCPServerConfigData(headers={"X": "real"}, url="https://h.example.com"))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `poetry run pytest tests/codemie/service/mcp/test_clone_sanitizer_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'config_contains_placeholder'`.

- [ ] **Step 3: Append to `src/codemie/service/mcp/clone_sanitizer.py`**

```python
_NON_COPYABLE_PATHS = frozenset({"auth_token", "auth_config", "url.userinfo", "url.fragment"})
_COPYABLE_GRAMMAR = (
    re.compile(r"^headers\..+$"),
    re.compile(r"^env\..+$"),
    re.compile(r"^args\[\d+\]$"),
    re.compile(r"^url\.query\..+$"),
)
_COPY_VALUES_HELP = (
    "Send only existing paths of the form headers.<Key>, env.<KEY>, args[<index>] or url.query.<key>; "
    "auth_token, auth_config and URL userinfo/fragment can never be copied"
)


def _query_keys(url: str | None) -> list[str]:
    if not url:
        return []
    return [unquote(segment.partition("=")[0]) for segment in urlsplit(url).query.split("&") if segment]


def _available_copy_paths(config: MCPServerConfigData) -> set[str]:
    paths = {f"headers.{key}" for key in config.headers}
    paths |= {f"env.{key}" for key in config.env}
    paths |= {f"args[{index}]" for index in range(len(config.args))}
    paths |= {f"url.query.{key}" for key in _query_keys(config.url)}
    return paths


def validate_copy_values(raw: list[str], source: MCPServerConfigData) -> set[str]:
    """Deduplicate and validate requested copy paths; raise 422 naming every offending path."""
    requested = list(dict.fromkeys(raw))
    available = _available_copy_paths(source)
    not_copyable: list[str] = []
    invalid: list[str] = []
    missing: list[str] = []
    for path in requested:
        if path in _NON_COPYABLE_PATHS or path.startswith("auth_config."):
            not_copyable.append(path)
        elif not any(pattern.match(path) for pattern in _COPYABLE_GRAMMAR):
            invalid.append(path)
        elif path not in available:
            missing.append(path)

    problems = []
    if not_copyable:
        problems.append(f"Not copyable: {', '.join(not_copyable)}")
    if invalid:
        problems.append(f"Unknown or invalid path: {', '.join(invalid)}")
    if missing:
        problems.append(f"Not found in source: {', '.join(missing)}")
    if problems:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Invalid copy_values",
            details="; ".join(problems),
            help=_COPY_VALUES_HELP,
        )
    return set(requested)


def sanitize_config(source: MCPServerConfigData, copy_paths: set[str]) -> tuple[MCPServerConfigData, list[str]]:
    """Return a deep-copied config with secrets blanked, plus the dotted paths that were NOT copied."""
    # Imported lazily: mcp_config_service imports this module.
    from codemie.service.mcp_config_service import _is_confidential_oauth2_auth_config

    data = source.model_copy(deep=True)
    stripped: list[str] = []

    if data.auth_token:
        stripped.append("auth_token")
    data.auth_token = None

    for section_name in ("headers", "env"):
        section = getattr(data, section_name)
        for key in list(section):
            path = f"{section_name}.{key}"
            if path not in copy_paths:
                section[key] = TEXT_PLACEHOLDER
                stripped.append(path)

    data.args, args_stripped = sanitize_args(data.args, copy_paths)
    stripped.extend(args_stripped)

    if data.url:
        data.url, url_stripped = sanitize_url(data.url, copy_paths)
        stripped.extend(url_stripped)

    auth_config = data.auth_config
    if auth_config is not None:
        if _is_confidential_oauth2_auth_config(auth_config):
            data.auth_config = None
            stripped.append("auth_config")
        else:
            if auth_config.pop("client_secret", None):
                stripped.append("auth_config.client_secret")
            auth_config.pop("has_client_secret", None)
            auth_config.pop("id", None)

    return data, stripped


def _iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def config_contains_placeholder(config: MCPServerConfigData) -> bool:
    """True when any url, auth_token, header, env or args string still contains a clone placeholder."""
    candidates = (config.url, config.auth_token, config.headers, config.env, config.args)
    return any(
        TEXT_PLACEHOLDER in text or URL_PLACEHOLDER in text for value in candidates for text in _iter_strings(value)
    )
```

- [ ] **Step 4: Run it to verify it passes**

Run: `poetry run pytest tests/codemie/service/mcp/test_clone_sanitizer_config.py tests/codemie/service/mcp/test_clone_sanitizer_url_args.py -v`
Expected: all passed.

---

### Task 4: Copy-name allocation

**Files:**
- Create: `src/codemie/service/mcp/clone_naming.py`
- Test: `tests/codemie/service/mcp/test_clone_naming.py`

**Interfaces:**
- Consumes: `MCPConfig` (table model).
- Produces: `compute_base(source_name: str) -> str`; `allocate_name(session: Session, user_id: str, base: str) -> str`; module constants `MAX_NAME_LENGTH = 255`, `COPY_NUMBER_MAX_WIDTH = 6`.

Test-first: yes — gaps reuse the lowest free N; bases with `%`, `_` and backslash are escaped in the `LIKE` pattern and matched literally by the regex; a 255-char source yields a name of at most 255 characters with the same base in scan and final name; other-base, malformed and over-long-number names are ignored; the statement selects only the `name` column.

- [ ] **Step 1: Write the failing test** at `tests/codemie/service/mcp/test_clone_naming.py`

```python
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.mcp import clone_naming
from codemie.service.mcp.clone_naming import MAX_NAME_LENGTH, allocate_name, compute_base


def _session(names: list[str]) -> MagicMock:
    session = MagicMock()
    session.exec.return_value.all.return_value = names
    return session


def _compiled(session: MagicMock):
    return session.exec.call_args.args[0].compile(dialect=postgresql.dialect())


def test_first_clone_is_copy_1():
    assert allocate_name(_session([]), "u1", "Server") == "Copy 1 of Server"


def test_lowest_free_number_is_reused():
    session = _session(["Copy 1 of Server", "Copy 3 of Server"])

    assert allocate_name(session, "u1", "Server") == "Copy 2 of Server"


def test_non_matching_names_are_ignored():
    names = ["Copy x of Server", "Copy 1 of ServerXL", "Copy 1 of Server extra", "Server", "Copy 1 of Other"]

    assert allocate_name(_session(names), "u1", "Server") == "Copy 1 of Server"


def test_wildcards_are_escaped_in_like_pattern():
    session = _session([])

    allocate_name(session, "u1", "100%_done\\x")

    assert "Copy % of 100\\%\\_done\\\\x" in _compiled(session).params.values()


def test_regex_matches_special_characters_literally():
    base = "100%_done\\x"
    names = [f"Copy 1 of {base}", "Copy 2 of 100Xydone\\x"]

    assert allocate_name(_session(names), "u1", base) == f"Copy 2 of {base}"


def test_scan_selects_only_the_name_column_filtered_by_user():
    session = _session([])

    allocate_name(session, "u1", "Server")

    statement = session.exec.call_args.args[0]
    sql = str(_compiled(session))
    assert len(statement.selected_columns) == 1
    assert sql.startswith("SELECT mcp_configs.name")
    assert "mcp_configs.config" not in sql
    assert "ESCAPE" in sql and "user_id" in sql
    assert "u1" in _compiled(session).params.values()


def test_long_source_name_is_truncated_and_scan_uses_same_base():
    source = "x" * MAX_NAME_LENGTH
    base = compute_base(source)
    session = _session([])

    name = allocate_name(session, "u1", base)

    assert len(name) <= MAX_NAME_LENGTH
    assert name == f"Copy 1 of {base}"
    assert f"Copy % of {base}" in _compiled(session).params.values()


def test_short_source_name_is_not_truncated():
    assert compute_base("Server") == "Server"


def test_number_overflowing_reserved_width_is_rejected(monkeypatch):
    monkeypatch.setattr(clone_naming, "COPY_NUMBER_MAX_WIDTH", 1)
    names = [f"Copy {n} of X" for n in range(1, 10)]

    with pytest.raises(ExtendedHTTPException) as exc:
        allocate_name(_session(names), "u1", "X")

    assert exc.value.code == 409
```

- [ ] **Step 2: Run it to verify it fails**

Run: `poetry run pytest tests/codemie/service/mcp/test_clone_naming.py -v`
Expected: FAIL with `ImportError: cannot import name 'clone_naming'`.

- [ ] **Step 3: Create `src/codemie/service/mcp/clone_naming.py`** (same license header as Task 2)

```python
"""Name allocation for cloned MCP configurations: `Copy N of <base>`."""

from __future__ import annotations

import re

from fastapi import status
from sqlmodel import Session, select

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.mcp_config import MCPConfig

MAX_NAME_LENGTH = 255
COPY_NUMBER_MAX_WIDTH = 6


def compute_base(source_name: str) -> str:
    """Truncate the source name so `Copy <N> of <base>` fits MAX_NAME_LENGTH for any N up to the reserved width."""
    reserved = len("Copy ") + COPY_NUMBER_MAX_WIDTH + len(" of ")
    return source_name[: MAX_NAME_LENGTH - reserved]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def allocate_name(session: Session, user_id: str, base: str) -> str:
    """Return `Copy N of <base>` with the lowest positive N not used by this user's configs."""
    pattern = f"Copy % of {_escape_like(base)}"
    statement = select(MCPConfig.name).where(
        MCPConfig.user_id == user_id,  # type: ignore[arg-type]
        MCPConfig.name.like(pattern, escape="\\"),  # type: ignore[attr-defined]
    )
    strict = re.compile(rf"Copy (\d+) of {re.escape(base)}")
    used = {int(match.group(1)) for name in session.exec(statement).all() if (match := strict.fullmatch(name))}

    number = 1
    while number in used:
        number += 1
    if number >= 10**COPY_NUMBER_MAX_WIDTH:
        raise ExtendedHTTPException(
            code=status.HTTP_409_CONFLICT,
            message="Too many copies",
            details=f"No free copy number is available for '{base}'",
            help="Rename or delete some existing copies and retry",
        )
    return f"Copy {number} of {base}"
```

- [ ] **Step 4: Run it to verify it passes**

Run: `poetry run pytest tests/codemie/service/mcp/test_clone_naming.py -v`
Expected: 9 passed.

---

### Task 5: `MCPConfigService.clone`

**Files:**
- Modify: `src/codemie/service/mcp_config_service.py`
- Test: `tests/codemie/service/test_mcp_config_service_clone.py`

**Interfaces:**
- Consumes: `validate_copy_values`, `sanitize_config` (Task 3), `compute_base`, `allocate_name` (Task 4), `get_clone_policy`, `MCPConfigCloneResponse` (Task 1); existing `_get_raw_auth_config`, `_ensure_auth_config_id`, `_validate_auth_config_for_save`, `_prepare_auth_config_for_persistence`, `_is_auth_config_id_conflict`, `_to_response`, `MCP_CONFIG_NOT_FOUND_MESSAGE`, `VERIFY_CONFIG_ID_HELP`.
- Produces: `MCPConfigService.clone(source_id: str, user: User, copy_values: list[str]) -> MCPConfigCloneResponse`.

Test-first: yes — flags/owner as specified and a system source clones to `is_system=False`; deep copy keeps edits independent; unknown id gives 404 and never opens a session; 422 from `copy_values` and 409 from `IntegrityError` persist nothing; non-confidential `auth_config` gets a new id and no secret; confidential source gives `auth_config=None` and `auth_config` in `stripped_fields`; the audit log holds copied paths and no values.

- [ ] **Step 1: Write the failing test** at `tests/codemie/service/test_mcp_config_service_clone.py`

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.mcp_config import MCPConfig, MCPConfigCloneResponse, MCPServerConfigData
from codemie.service.mcp.clone_constants import TEXT_PLACEHOLDER
from codemie.service.mcp_config_service import _AUTH_CONFIG_ID_INDEX, MCPConfigService


def _make_user() -> MagicMock:
    user = MagicMock()
    user.id = "user-1"
    user.name = "Test User"
    user.username = "test-user"
    return user


def _make_source(**config_overrides) -> MCPConfig:
    config = {
        "url": "https://mcp.example.com/sse",
        "headers": {"Authorization": "Bearer super-secret-value"},
        "auth_token": "tok-123",
    }
    config.update(config_overrides)
    return MCPConfig(
        id="src-1",
        name="Server",
        description="desc",
        server_home_url="https://home.example.com",
        source_url="https://git.example.com",
        logo_url="https://logo.example.com/l.png",
        categories=["Development"],
        config=MCPServerConfigData(**config),
        required_env_vars=[],
        user_id="admin-0",
        is_public=True,
        is_system=True,
        created_by=CreatedByUser(id="admin-0", name="Admin", username="admin"),
        usage_count=7,
        is_active=False,
    )


@pytest.fixture
def session():
    fake = MagicMock()
    fake.__enter__.return_value = fake
    with (
        patch("codemie.service.mcp_config_service.Session", return_value=fake) as session_cls,
        patch.object(MCPConfig, "get_engine", return_value=MagicMock()),
        patch("codemie.service.mcp_config_service.allocate_name", return_value="Copy 1 of Server") as allocate,
        patch("codemie.service.mcp_config_service.validate_auth_config_on_save", return_value=[]) as validate,
        patch("codemie.service.mcp_config_service.is_mcp_auth_enabled", return_value=True),
    ):
        fake.session_cls = session_cls
        fake.allocate = allocate
        fake.validate = validate
        yield fake


def _clone(source, copy_values=None) -> MCPConfigCloneResponse:
    with patch.object(MCPConfig, "find_by_id", return_value=source):
        return MCPConfigService.clone("src-1", _make_user(), copy_values or [])


def _saved_row(session) -> MCPConfig:
    return session.add.call_args.args[0]


def test_clone_sets_flags_owner_and_copies_descriptive_fields(session):
    response = _clone(_make_source())

    row = _saved_row(session)
    assert isinstance(response, MCPConfigCloneResponse)
    assert row.name == "Copy 1 of Server" and response.name == "Copy 1 of Server"
    assert (row.is_system, row.usage_count, row.is_active) == (False, 0, True)
    assert row.is_public is True
    assert (row.user_id, row.created_by.id, row.created_by.username) == ("user-1", "user-1", "test-user")
    assert row.id and row.id != "src-1" and response.id == row.id
    assert (row.description, row.server_home_url, row.source_url, row.logo_url) == (
        "desc",
        "https://home.example.com",
        "https://git.example.com",
        "https://logo.example.com/l.png",
    )
    assert row.categories == ["Development"]
    session.commit.assert_called_once()


def test_clone_allocates_name_from_cloner_and_source_name(session):
    _clone(_make_source())

    session.allocate.assert_called_once_with(session, "user-1", "Server")


def test_clone_blanks_secrets_and_reports_stripped_fields(session):
    response = _clone(_make_source())

    row = _saved_row(session)
    assert row.config.headers == {"Authorization": TEXT_PLACEHOLDER}
    assert row.config.auth_token is None
    assert response.stripped_fields == ["auth_token", "headers.Authorization"]
    assert "super-secret-value" not in response.model_dump_json()


def test_clone_copies_selected_value_and_omits_it_from_stripped(session):
    response = _clone(_make_source(), ["headers.Authorization"])

    assert _saved_row(session).config.headers == {"Authorization": "Bearer super-secret-value"}
    assert response.stripped_fields == ["auth_token"]


def test_clone_is_a_deep_copy_independent_of_the_source(session):
    source = _make_source()
    before = source.config.model_dump()

    _clone(source)
    row = _saved_row(session)
    row.config.headers["Added"] = "x"
    row.categories.append("Other")

    assert source.config.model_dump() == before
    assert source.categories == ["Development"]


def test_system_source_is_cloneable_and_source_flags_are_untouched(session):
    source = _make_source()

    _clone(source)

    assert _saved_row(session).is_system is False
    assert (source.is_system, source.usage_count) == (True, 7)


def test_unknown_source_returns_404_and_opens_no_session(session):
    with pytest.raises(ExtendedHTTPException) as exc:
        _clone(None)

    assert exc.value.code == 404
    session.session_cls.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.parametrize("bad_path", ["auth_token", "headers.Nope", "bogus"])
def test_invalid_copy_values_return_422_and_persist_nothing(session, bad_path):
    with pytest.raises(ExtendedHTTPException) as exc:
        _clone(_make_source(), [bad_path])

    assert exc.value.code == 422
    assert bad_path in exc.value.details
    session.session_cls.assert_not_called()
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_auth_config_id_conflict_maps_to_409_and_rolls_back(session):
    session.commit.side_effect = IntegrityError("stmt", {}, Exception(f"duplicate key {_AUTH_CONFIG_ID_INDEX}"))

    with pytest.raises(ExtendedHTTPException) as exc:
        _clone(_make_source())

    assert exc.value.code == 409
    session.rollback.assert_called_once()


def test_other_integrity_errors_are_reraised_after_rollback(session):
    session.commit.side_effect = IntegrityError("stmt", {}, Exception("some other constraint"))

    with pytest.raises(IntegrityError):
        _clone(_make_source())

    session.rollback.assert_called_once()


def test_non_confidential_auth_config_gets_new_id_and_no_secret(session):
    auth = {
        "auth_type": "oauth2",
        "client_type": "public",
        "client_id": "cid",
        "client_secret": "leftover",
        "id": "old-id",
    }

    response = _clone(_make_source(auth_config=auth))

    saved_auth = _saved_row(session).config.auth_config
    assert saved_auth["id"] and saved_auth["id"] != "old-id"
    assert "client_secret" not in saved_auth
    assert "auth_config.client_secret" in response.stripped_fields
    session.validate.assert_called_once()


def test_confidential_oauth2_clone_has_no_auth_config_and_skips_enterprise_validation(session):
    auth = {
        "auth_type": "oauth2",
        "client_type": "confidential",
        "client_id": "cid",
        "client_secret": "enc-secret",
        "id": "old-id",
    }

    response = _clone(_make_source(auth_config=auth))

    assert _saved_row(session).config.auth_config is None
    assert "auth_config" in response.stripped_fields
    assert "enc-secret" not in response.model_dump_json()
    session.validate.assert_not_called()


def test_audit_log_contains_copied_paths_and_no_values(session):
    with patch("codemie.service.mcp_config_service.logger") as logger:
        _clone(_make_source(), ["headers.Authorization"])

    logged = " ".join(str(call.args[0]) for call in logger.info.call_args_list)
    assert "headers.Authorization" in logged
    assert "super-secret-value" not in logged
```

- [ ] **Step 2: Run it to verify it fails**

Run: `poetry run pytest tests/codemie/service/test_mcp_config_service_clone.py -v`
Expected: FAIL (`AttributeError: ... has no attribute 'clone'` / patch target `allocate_name` missing).

- [ ] **Step 3: Implement in `src/codemie/service/mcp_config_service.py`**

3a. Add imports. Replace `import uuid` with:

```python
import copy
import uuid
from datetime import datetime
```

Add `MCPConfigCloneResponse,` and `get_clone_policy,` to the existing `from codemie.rest_api.models.mcp_config import (...)` list, and after the `EncryptionFactory` import add:

```python
from codemie.service.mcp.clone_naming import allocate_name, compute_base
from codemie.service.mcp.clone_sanitizer import sanitize_config, validate_copy_values
```

3b. Insert this classmethod directly after `create` (before `get_by_id`):

```python
    @classmethod
    def clone(cls, source_id: str, user: User, copy_values: list[str]) -> MCPConfigCloneResponse:
        """
        Create an independent, secret-stripped copy of an MCP configuration.

        Does not go through ``create`` (which forces is_system/is_public). Order: source lookup, copy_values
        validation, then a single transaction for name allocation and insert.

        Args:
            source_id: ID of the configuration to clone (system-provided sources are allowed)
            user: Cloning user; becomes owner and creator of the copy
            copy_values: Dotted paths whose values are copied instead of replaced by a placeholder

        Returns:
            The persisted clone plus the dotted paths whose values were NOT copied

        Raises:
            ExtendedHTTPException: 404 unknown source, 422 invalid copy_values or auth_config, 409 auth_config.id conflict
        """
        source = MCPConfig.find_by_id(source_id)
        if not source:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=MCP_CONFIG_NOT_FOUND_MESSAGE,
                details=f"No MCP configuration found with id: {source_id}",
                help=VERIFY_CONFIG_ID_HELP,
            )

        source_config = source.config or MCPServerConfigData()
        copy_paths = validate_copy_values(copy_values, source_config)
        cloned_config, stripped_fields = sanitize_config(source_config, copy_paths)

        auth_config = _get_raw_auth_config(cloned_config)
        if auth_config is not None:
            _ensure_auth_config_id(auth_config)
            _validate_auth_config_for_save(cloned_config)
            cls._prepare_auth_config_for_persistence(auth_config)

        logger.info(f"Cloning MCP config: source_id={source_id} user_id={user.id} copied_paths={sorted(copy_paths)}")

        copied_fields = {
            field: copy.deepcopy(getattr(source, field))
            for field, policy in get_clone_policy(MCPConfig).items()
            if policy == "copy"
        }
        base = compute_base(source.name)
        now = datetime.now()
        with Session(MCPConfig.get_engine()) as session:
            try:
                row = MCPConfig(
                    **copied_fields,
                    id=str(uuid.uuid4()),
                    name=allocate_name(session, user.id, base),
                    config=cloned_config,
                    user_id=user.id,
                    is_system=False,
                    usage_count=0,
                    is_active=True,
                    created_by=CreatedByUser(id=user.id, name=user.name, username=user.username),
                    date=now,
                    update_date=now,
                )
                session.add(row)
                session.commit()
                session.refresh(row)
            except IntegrityError as e:
                session.rollback()
                if _is_auth_config_id_conflict(e):
                    raise ExtendedHTTPException(
                        code=status.HTTP_409_CONFLICT,
                        message="auth_config.id already in use",
                        details="Another MCP configuration already uses this auth_config.id value",
                        help="Retry the clone; a new auth_config.id is generated on every attempt",
                    ) from e
                raise
            response = cls._to_response(row)

        logger.info(f"MCP config cloned: source_id={source_id} clone_id={response.id}")
        return MCPConfigCloneResponse(**dict(response), stripped_fields=stripped_fields)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `poetry run pytest tests/codemie/service/test_mcp_config_service_clone.py -v`
Expected: 15 passed (parametrized cases included).

---

### Task 6: `placeholder_value_present` warning

**Files:**
- Modify: `src/codemie/service/mcp_config_service.py`
- Test: `tests/codemie/service/test_mcp_config_service_placeholder_warning.py`

**Interfaces:**
- Consumes: `config_contains_placeholder` (Task 3), `PLACEHOLDER_WARNING_CODE` (Task 2), `ConfigWarning`, `MCPConfigService.clone` (Task 5).
- Produces: `MCPConfigService._add_placeholder_warnings(response: MCPConfigResponse) -> MCPConfigResponse`, applied only to create, update and clone responses.

Test-first: yes — a header holding the text placeholder, or a URL holding `REQUIRED_REENTER_VALUE`, yields a `placeholder_value_present` warning on create, update and clone; a clone whose placeholders were all replaced via `copy_values` has none; `get_by_id` never carries it.

- [ ] **Step 1: Write the failing test** at `tests/codemie/service/test_mcp_config_service_placeholder_warning.py`

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.models.mcp_config import (
    MCPConfig,
    MCPConfigCreateRequest,
    MCPConfigUpdateRequest,
    MCPServerConfigData,
)
from codemie.service.mcp.clone_constants import PLACEHOLDER_WARNING_CODE, TEXT_PLACEHOLDER, URL_PLACEHOLDER
from codemie.service.mcp_config_service import MCPConfigService


def _make_user() -> MagicMock:
    user = MagicMock()
    user.id = "user-1"
    user.name = "Test User"
    user.username = "test-user"
    return user


def _codes(response) -> list[str]:
    return [warning.code for warning in response.warnings]


def _row(config: MCPServerConfigData) -> MCPConfig:
    return MCPConfig(id="cfg-1", name="Server", config=config, user_id="user-1", is_public=True)


def _assign_id(self, *args, **kwargs):
    self.id = "new-1"


def test_create_warns_when_text_placeholder_present():
    request = MCPConfigCreateRequest(name="S", config=MCPServerConfigData(headers={"X": TEXT_PLACEHOLDER}))

    with (
        patch.object(MCPConfig, "get_by_fields", return_value=None),
        patch.object(MCPConfig, "save", autospec=True, side_effect=_assign_id),
    ):
        response = MCPConfigService.create(request, _make_user())

    assert PLACEHOLDER_WARNING_CODE in _codes(response)


def test_create_without_placeholder_has_no_warning():
    request = MCPConfigCreateRequest(name="S", config=MCPServerConfigData(headers={"X": "real"}))

    with (
        patch.object(MCPConfig, "get_by_fields", return_value=None),
        patch.object(MCPConfig, "save", autospec=True, side_effect=_assign_id),
    ):
        response = MCPConfigService.create(request, _make_user())

    assert PLACEHOLDER_WARNING_CODE not in _codes(response)


def test_update_warns_when_url_placeholder_present():
    existing = _row(MCPServerConfigData(url="https://h.example.com/sse"))
    request = MCPConfigUpdateRequest(config=MCPServerConfigData(url=f"https://{URL_PLACEHOLDER}@h.example.com/sse"))

    with (
        patch.object(MCPConfig, "find_by_id", return_value=existing),
        patch.object(MCPConfig, "update", autospec=True),
    ):
        response = MCPConfigService.update("cfg-1", request)

    assert PLACEHOLDER_WARNING_CODE in _codes(response)


def test_get_by_id_never_carries_the_warning():
    existing = _row(MCPServerConfigData(headers={"X": TEXT_PLACEHOLDER}))

    with patch.object(MCPConfig, "find_by_id", return_value=existing):
        response = MCPConfigService.get_by_id("cfg-1")

    assert PLACEHOLDER_WARNING_CODE not in _codes(response)


@pytest.fixture
def session():
    fake = MagicMock()
    fake.__enter__.return_value = fake
    with (
        patch("codemie.service.mcp_config_service.Session", return_value=fake),
        patch.object(MCPConfig, "get_engine", return_value=MagicMock()),
        patch("codemie.service.mcp_config_service.allocate_name", return_value="Copy 1 of Server"),
    ):
        yield fake


def _clone(copy_values: list[str]):
    source = _row(MCPServerConfigData(headers={"Authorization": "Bearer s3cret"}))
    with patch.object(MCPConfig, "find_by_id", return_value=source):
        return MCPConfigService.clone("cfg-1", _make_user(), copy_values)


def test_clone_warns_when_placeholder_remains(session):
    assert PLACEHOLDER_WARNING_CODE in _codes(_clone([]))


def test_clone_does_not_warn_when_all_placeholders_were_replaced_by_copied_values(session):
    assert PLACEHOLDER_WARNING_CODE not in _codes(_clone(["headers.Authorization"]))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `poetry run pytest tests/codemie/service/test_mcp_config_service_placeholder_warning.py -v`
Expected: FAIL on the three "warns" tests (`assert 'placeholder_value_present' in []`); the other tests pass.

- [ ] **Step 3: Implement in `src/codemie/service/mcp_config_service.py`**

3a. Add imports after the Task 5 clone imports:

```python
from codemie.service.mcp.clone_constants import PLACEHOLDER_WARNING_CODE
```

and extend the sanitizer import to `from codemie.service.mcp.clone_sanitizer import config_contains_placeholder, sanitize_config, validate_copy_values`.

3b. Add this classmethod directly after `_to_response`:

```python
    @classmethod
    def _add_placeholder_warnings(cls, response: MCPConfigResponse) -> MCPConfigResponse:
        """Append a non-blocking warning when a clone placeholder is still present in the config."""
        if response.config is not None and config_contains_placeholder(response.config):
            response.warnings.append(
                ConfigWarning(
                    code=PLACEHOLDER_WARNING_CODE,
                    message=f"Configuration '{response.name}' still contains placeholder values that must be replaced",
                    action="Replace every REQUIRED placeholder value with the real value and save the configuration",
                )
            )
        return response
```

3c. Wire it into the three return sites. In `create`, replace

```python
        logger.info(f"MCP config created: {save_response}")

        # Return response
        return cls._to_response(mcp_config)
```

with

```python
        logger.info(f"MCP config created: {save_response}")

        # Return response
        return cls._add_placeholder_warnings(cls._to_response(mcp_config))
```

In `update`, replace

```python
        logger.info(f"MCP config updated: {config_id}")

        return cls._to_response(mcp_config)
```

with

```python
        logger.info(f"MCP config updated: {config_id}")

        return cls._add_placeholder_warnings(cls._to_response(mcp_config))
```

In `clone`, replace `response = cls._to_response(row)` with:

```python
            response = cls._add_placeholder_warnings(cls._to_response(row))
```

`get_by_id` and `list_configs` stay untouched.

- [ ] **Step 4: Run it to verify it passes**

Run: `poetry run pytest tests/codemie/service/test_mcp_config_service_placeholder_warning.py tests/codemie/service/test_mcp_config_service_clone.py tests/codemie/service/test_mcp_config_service_auth_crud.py -v`
Expected: all passed (existing auth CRUD tests confirm create/update are otherwise unchanged).

---

### Task 7: Router endpoint and first router tests for `/v1/mcp-configs`

**Files:**
- Modify: `src/codemie/rest_api/routers/mcp_config.py`
- Test: `tests/codemie/rest_api/routers/test_mcp_config.py`

**Interfaces:**
- Consumes: `MCPConfigService.clone(source_id, user, copy_values)`, `MCPConfigCloneRequest`, `MCPConfigCloneResponse` (Tasks 1, 5).
- Produces: `POST /v1/mcp-configs/{config_id}/clone` (201, flat `MCPConfigCloneResponse`, optional body, `authenticate` + `admin_access_only`).

Test-first: yes — non-admin gets 403 and the service is never called (also with an invalid `copy_values` body, proving permission precedes validation); admin without body calls the service with `[]`; the response is a flat config object with top-level `stripped_fields` and no secrets; service 404/422 propagate as typed error bodies.

- [ ] **Step 1: Write the failing test** at `tests/codemie/rest_api/routers/test_mcp_config.py`

```python
from unittest.mock import patch

import pytest
from fastapi import Request, status
from httpx import ASGITransport, AsyncClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import app
from codemie.rest_api.models.mcp_config import MCPConfigCloneResponse, MCPServerConfigData
from codemie.rest_api.routers import mcp_config as mcp_config_router
from codemie.rest_api.security.user import User
from codemie.service.mcp.clone_constants import TEXT_PLACEHOLDER
from codemie.service.mcp_config_service import MCPConfigService

CLONE_URL = "/v1/mcp-configs/src-1/clone"


@pytest.fixture
def admin() -> User:
    return User(id="admin-1", username="admin", name="Admin", is_admin=True)


@pytest.fixture
def regular_user() -> User:
    return User(id="user-1", username="user", name="User")


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    app.dependency_overrides = {}


def _authenticate_as(user: User) -> None:
    async def _override(request: Request) -> User:
        request.state.user = user
        return user

    app.dependency_overrides[mcp_config_router.authenticate] = _override


def _clone_response(**overrides) -> MCPConfigCloneResponse:
    payload = {
        "id": "new-1",
        "name": "Copy 1 of Server",
        "description": None,
        "server_home_url": None,
        "source_url": None,
        "logo_url": None,
        "categories": [],
        "config": MCPServerConfigData(url="https://h.example.com/sse", headers={"Authorization": TEXT_PLACEHOLDER}),
        "required_env_vars": [],
        "user_id": "admin-1",
        "is_public": True,
        "is_system": False,
        "created_by": None,
        "usage_count": 0,
        "is_active": True,
        "date": None,
        "update_date": None,
        "stripped_fields": ["auth_token", "headers.Authorization"],
    }
    payload.update(overrides)
    return MCPConfigCloneResponse(**payload)


async def _post(**kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        return await client.post(CLONE_URL, headers={"Authorization": "Bearer testtoken"}, **kwargs)


@pytest.mark.asyncio
async def test_non_admin_gets_403_and_service_is_not_called(regular_user):
    _authenticate_as(regular_user)

    with patch.object(MCPConfigService, "clone") as clone:
        response = await _post()

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert {"message", "details", "help"} <= set(response.json()["error"])
    clone.assert_not_called()


@pytest.mark.asyncio
async def test_permission_is_checked_before_copy_values_validation(regular_user):
    _authenticate_as(regular_user)

    with patch.object(MCPConfigService, "clone") as clone:
        response = await _post(json={"copy_values": "not-a-list"})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    clone.assert_not_called()


@pytest.mark.asyncio
async def test_admin_without_body_calls_service_with_empty_copy_values(admin):
    _authenticate_as(admin)

    with patch.object(MCPConfigService, "clone", return_value=_clone_response()) as clone:
        response = await _post()

    assert response.status_code == status.HTTP_201_CREATED
    clone.assert_called_once_with("src-1", admin, [])


@pytest.mark.asyncio
async def test_admin_body_copy_values_are_forwarded(admin):
    _authenticate_as(admin)

    with patch.object(MCPConfigService, "clone", return_value=_clone_response()) as clone:
        response = await _post(json={"copy_values": ["headers.Authorization"]})

    assert response.status_code == status.HTTP_201_CREATED
    clone.assert_called_once_with("src-1", admin, ["headers.Authorization"])


@pytest.mark.asyncio
async def test_response_is_a_flat_config_object_with_top_level_stripped_fields(admin):
    _authenticate_as(admin)

    with patch.object(MCPConfigService, "clone", return_value=_clone_response()):
        response = await _post()

    body = response.json()
    assert body["id"] == "new-1" and body["name"] == "Copy 1 of Server"
    assert body["is_system"] is False
    assert body["stripped_fields"] == ["auth_token", "headers.Authorization"]
    assert isinstance(body["config"], dict) and "data" not in body
    assert "secret" not in response.text.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,message",
    [(404, "MCP configuration not found"), (422, "Invalid copy_values")],
)
async def test_service_errors_propagate_as_typed_bodies(admin, code, message):
    _authenticate_as(admin)
    error = ExtendedHTTPException(code=code, message=message, details="some details", help="some help")

    with patch.object(MCPConfigService, "clone", side_effect=error):
        response = await _post()

    assert response.status_code == code
    assert response.json() == {"error": {"message": message, "details": "some details", "help": "some help"}}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_mcp_config.py -v`
Expected: FAIL (`patch.object(MCPConfigService, "clone")` succeeds after Task 5, but the route does not exist: responses are 404/405 instead of 201/403).

- [ ] **Step 3: Implement in `src/codemie/rest_api/routers/mcp_config.py`**

3a. Change `from fastapi import APIRouter, status, Depends, Query` to:

```python
from fastapi import APIRouter, Body, Depends, Query, status
```

3b. Add `MCPConfigCloneRequest,` and `MCPConfigCloneResponse,` to the `from codemie.rest_api.models.mcp_config import (...)` list.

3c. Insert directly after `create_mcp_config` (before the `GET /mcp-configs/{config_id}` route):

```python
@router.post(
    "/mcp-configs/{config_id}/clone",
    status_code=status.HTTP_201_CREATED,
    response_model=MCPConfigCloneResponse,
    summary="Clone MCP Configuration",
    description="Create an independent, secret-stripped copy of an MCP configuration (Admin only)",
    dependencies=[Depends(authenticate), Depends(admin_access_only)],
)
def clone_mcp_config(
    config_id: str,
    request: MCPConfigCloneRequest | None = Body(default=None),
    user: User = Depends(authenticate),
) -> MCPConfigCloneResponse:
    """
    Clone an MCP server configuration.

    The copy is named `Copy N of <name>`, owned by the caller, and never carries secrets unless their
    dotted paths are listed in `copy_values`.

    **Path Parameters:**
    - **config_id**: ID of the configuration to clone (system-provided configurations are allowed)

    **Request Body (optional):**
    - **copy_values**: Paths (`headers.<Key>`, `env.<KEY>`, `args[<index>]`, `url.query.<key>`) whose values are
      copied instead of replaced by a placeholder. Omit it to blank every value.

    **Returns:**
    - The new configuration plus `stripped_fields`, the paths whose values were not copied
    """
    copy_values = request.copy_values if request else []
    logger.info(f"Cloning MCP config: {config_id} by user: {user.id}")
    return MCPConfigService.clone(config_id, user, copy_values)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_mcp_config.py -v`
Expected: 7 passed (the parametrized test counts twice).

---

### Task 8: Verify OAuth2 auto-discovery for a clone without `auth_config`

**Files:**
- Create: `tests/codemie/service/mcp/test_clone_auto_discovery_behavior.py`
- Modify: docstring of `MCPConfigService.clone` in `src/codemie/service/mcp_config_service.py`

Findings from the code (verified): `MCPServerConfigData.allow_issuer_prefix_match` only takes effect "when auth_config is absent and OAuth2 auto-discovery is used". That path is `ToolkitService._build_discovery_candidate_from_challenge` (`src/codemie/service/mcp/toolkit_service.py:563-589`): it returns a candidate only when `auth_config is None`, the server has a `url`, and loading failed with an `httpx.HTTPStatusError` of status 401 carrying a `WWW-Authenticate` header. The candidate forwards `allow_issuer_prefix_match` from the config. The builder itself does not consult `is_mcp_auth_enabled()`; it is called from the load-failure paths at `toolkit_service.py:371` and `:629`.

Test-first: no — characterization of existing behavior; one step.

- [ ] **Step 1: Write the characterization tests and record the finding**

Create `tests/codemie/service/mcp/test_clone_auto_discovery_behavior.py`:

```python
from types import SimpleNamespace

import httpx
import pytest

from codemie.rest_api.models.mcp_config import MCPServerConfigData
from codemie.service.mcp.clone_sanitizer import sanitize_config
from codemie.service.mcp.toolkit_service import ToolkitService

WWW_AUTHENTICATE = 'Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"'


def _cloned_from_confidential_source() -> MCPServerConfigData:
    source = MCPServerConfigData(
        url="https://mcp.example.com/sse",
        allow_issuer_prefix_match=True,
        auth_config={
            "auth_type": "oauth2",
            "client_type": "confidential",
            "client_id": "cid",
            "client_secret": "enc-secret",
            "id": "auth-1",
        },
    )
    clone, _ = sanitize_config(source, set())
    return clone


def _load_failure(status_code: int | None, www_authenticate: str | None = None) -> Exception:
    exc = Exception("tool load failed")
    if status_code is not None:
        request = httpx.Request("GET", "https://mcp.example.com/sse")
        headers = {"WWW-Authenticate": www_authenticate} if www_authenticate else {}
        response = httpx.Response(status_code, headers=headers, request=request)
        exc.__cause__ = httpx.HTTPStatusError("boom", request=request, response=response)
    return exc


def _server(config: MCPServerConfigData) -> SimpleNamespace:
    return SimpleNamespace(name="srv", mcp_config_id="cfg-1", config=config)


def test_clone_keeps_issuer_prefix_flag_but_gains_no_auth_config():
    clone = _cloned_from_confidential_source()

    assert clone.auth_config is None
    assert clone.allow_issuer_prefix_match is True


@pytest.mark.parametrize(
    "exc",
    [
        _load_failure(None),
        _load_failure(401, None),
        _load_failure(403, WWW_AUTHENTICATE),
        _load_failure(500, WWW_AUTHENTICATE),
    ],
    ids=["no-http-cause", "401-without-header", "403-with-header", "500-with-header"],
)
def test_no_discovery_candidate_without_a_401_challenge(exc):
    server = _server(_cloned_from_confidential_source())

    assert ToolkitService._build_discovery_candidate_from_challenge(server, exc) is None


def test_discovery_candidate_appears_only_after_a_real_401_challenge():
    server = _server(_cloned_from_confidential_source())

    candidate = ToolkitService._build_discovery_candidate_from_challenge(
        server, _load_failure(401, WWW_AUTHENTICATE)
    )

    assert candidate["mcp_resource_url"] == "https://mcp.example.com/sse"
    assert candidate["www_authenticate_header"] == WWW_AUTHENTICATE
    assert candidate["allow_issuer_prefix_match"] is True
```

Then run `poetry run pytest tests/codemie/service/mcp/test_clone_auto_discovery_behavior.py -v` (expected: 6 passed; the parametrized test counts four times). If any case shows a candidate without a 401 `WWW-Authenticate` challenge, stop and return `needs_input` instead of changing code.

Finally, in `src/codemie/service/mcp_config_service.py`, replace the end of the `clone` docstring

```python
            ExtendedHTTPException: 404 unknown source, 422 invalid copy_values or auth_config, 409 auth_config.id conflict
        """
```

with

```python
            ExtendedHTTPException: 404 unknown source, 422 invalid copy_values or auth_config, 409 auth_config.id conflict

        Note:
            A clone of a confidential OAuth2 source has ``auth_config=None`` and keeps the source's
            ``allow_issuer_prefix_match``. It never gains an ``auth_config`` on its own. OAuth2 auto-discovery
            (``ToolkitService._build_discovery_candidate_from_challenge``) is reachable for such a clone only after
            the MCP server answers 401 with a ``WWW-Authenticate`` header; without that challenge no discovery
            candidate is produced. Covered by tests/codemie/service/mcp/test_clone_auto_discovery_behavior.py.
        """
```

---

## Self-review

- **Spec coverage:** Endpoint, guards and order of checks (Tasks 5, 7); flags and ownership, deep copy, system source (Task 5); naming incl. escaping, truncation, name-only projection, lowest free N (Task 4); URL, args, headers/env (incl. non-string env), `auth_token`, confidential and other auth types (Tasks 2, 3, 5); `copy_values` grammar, 422 cases, duplicates, audit log (Tasks 3, 5); `stripped_fields` contract (Tasks 1, 3, 5, 7); placeholder warning for create, update and clone only (Task 6); drift guard and policy-driven field copying (Tasks 1, 5); JSON round trip (Task 3); 409 mapping and rollback (Task 5); first router tests incl. 403-before-validation (Task 7); auto-discovery verification (Task 8). Acceptance criteria 1-21 each map to at least one of these tasks.
- **Placeholder scan:** no TBD/TODO/"similar to" steps; every step carries full code or an exact replace-this-with-that edit, and exact commands with expected results.
- **Type consistency:** `copy_paths: set[str]` is produced by `validate_copy_values` (Task 3) and consumed by `sanitize_config`, `sanitize_url`, `sanitize_args` (Tasks 2, 3) and `clone` (Task 5); `stripped_fields: list[str]` flows from `sanitize_config` into `MCPConfigCloneResponse` (Tasks 1, 5, 7); `allocate_name(session, user_id, base)` and `compute_base(name)` match between Tasks 4 and 5; `get_clone_policy` (Task 1) is used by `clone` (Task 5); `config_contains_placeholder` (Task 3) and `PLACEHOLDER_WARNING_CODE` (Task 2) are used by Task 6; the `clone` signature `(source_id, user, copy_values)` matches the router call and its test (Task 7).
- **Negative constraints:** `clone` never calls `create` and sets `is_system=False` (Task 5); no migration, lock, unique index, Postgres-only operator or full-row scan (Task 4); no envelope, UI, telemetry or bulk clone; `admin_access_only` unchanged (Task 7); `auth_token`, `auth_config` and URL userinfo/fragment are rejected as copy paths (Task 3); create/update change only by the non-blocking warning (Task 6). No task violates these.
