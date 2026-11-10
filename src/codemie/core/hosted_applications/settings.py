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

"""Fail-closed deployment readiness and browser-origin policy.

Hosted applications run only while the customer-config feature ``features:hostedApplications`` is on and
the deployment can isolate uploaded content. The UI origin comes from FRONTEND_URL and the identity
settings from the platform's sign-in configuration, so the feature only adds settings the platform does not
already have. Every disabled reason is a fixed sentence naming at most a setting, never a configured value.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

# Customer-config component `features:hostedApplications`; FEATURE_HOSTED_APPLICATIONS=true|false overrides it.
FEATURE = "hostedApplications"
# Local development serves versions from v-<id>.apps.localhost on the backend's own port.
LOCAL_CONTENT_DOMAIN = "apps.localhost"
# Local development must reach the validator on the loopback interface.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# Browser-facing first-party services that must never share a registrable domain with uploaded content.
_FIRST_PARTY_URL_SETTINGS = (
    "FRONTEND_URL",
    "CALLBACK_API_BASE_URL",
    "KEYCLOAK_LOGOUT_URL",
    "KEYCLOAK_ADMIN_URL",
    "TOKEN_EXCHANGE_URL",
    "BROKER_AUTH_LOCATION_URL",
)


def hosted_feature_enabled() -> bool:
    from codemie.configs.customer_config import customer_config

    return customer_config.is_feature_enabled(FEATURE)


@dataclass(frozen=True)
class IdentityAuthority:
    """Identity settings of hosted applications: CodeMie user management, for every sign-in provider.

    ``leeway_seconds`` is the clock skew that the platform's own token validation accepts for a future
    ``iat`` or ``auth_time``.
    """

    leeway_seconds: int = 5


@lru_cache(maxsize=1)
def _suffix_rules():
    exact, wildcard, exceptions = set(), set(), set()
    for line in Path(__file__).with_name("public_suffix_list.dat").read_text().splitlines():
        rule = line.strip()
        if not rule or rule.startswith("//"):
            continue
        if rule.startswith("!"):
            exceptions.add(rule[1:].encode("idna").decode())
        elif rule.startswith("*."):
            wildcard.add(rule[2:].encode("idna").decode())
        else:
            exact.add(rule.encode("idna").decode())
    return exact, wildcard, exceptions


def registrable_domain(host: str) -> str | None:
    """PSL algorithm, including private domains and wildcard exceptions."""
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or ".." in host:
        return None
    labels = host.split(".")
    if len(host) > 253 or any(len(x) > 63 or x.startswith("-") or x.endswith("-") for x in labels):
        return None
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    exact, wildcard, exceptions = _suffix_rules()
    suffix_count = 1
    for index in range(len(labels)):
        candidate = ".".join(labels[index:])
        count = len(labels) - index
        if candidate in exceptions:
            suffix_count = count - 1
            break
        if candidate in exact:
            suffix_count = max(suffix_count, count)
        if index > 0 and candidate in wildcard:
            suffix_count = max(suffix_count, count + 1)
    return ".".join(labels[-suffix_count - 1 :]) if len(labels) > suffix_count else None


def _ui_origin(config, *, local: bool) -> str:
    """The CodeMie UI's browser origin, from FRONTEND_URL: the origin the API already trusts for CORS."""
    invalid = "FRONTEND_URL is not a valid address"
    try:
        parsed = urlsplit(getattr(config, "FRONTEND_URL", "") or "")
        host, port = parsed.hostname, parsed.port
    except ValueError as exc:
        raise ValueError(invalid) from exc
    if not host or parsed.username or parsed.password:
        raise ValueError(invalid)
    if local:
        if parsed.scheme != "http" or host not in ("localhost", "127.0.0.1"):
            raise ValueError("Local content needs FRONTEND_URL on http://localhost")
    elif parsed.scheme != "https" or not registrable_domain(host):
        raise ValueError("FRONTEND_URL must be the HTTPS address of the CodeMie UI")
    default_port = 80 if parsed.scheme == "http" else 443
    return f"{parsed.scheme}://{host}" + (f":{port}" if port and port != default_port else "")


def _jwks_entries(raw) -> list[dict]:
    """JWKS_TRUSTED_ISSUERS entries; raises ValueError when the setting is not a JSON list of objects."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("JWKS trusted issuers are not configured")
    try:
        items = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("JWKS trusted issuers are not valid JSON") from exc
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError("JWKS trusted issuers must be a JSON list of objects")
    return items


def _enterprise_idp_available() -> bool:
    """External providers are registered by the enterprise package; without it IdpFactory falls back to LocalIdp."""
    try:
        from codemie.enterprise.loader import HAS_IDP
    except ImportError:
        return False
    return bool(HAS_IDP)


def identity_authority(config) -> IdentityAuthority:
    """The identity settings of hosted applications: CodeMie user management.

    Every sign-in provider works, because the feature binds the user that the platform has already
    authenticated. The persistent user table serves membership checks and people search, so user management
    is required.
    """
    if not config.ENABLE_USER_MANAGEMENT:
        raise ValueError("Hosted applications need user management (ENABLE_USER_MANAGEMENT=true)")
    if str(getattr(config, "IDP_PROVIDER", "local")).lower() != "local" and not _enterprise_idp_available():
        raise ValueError("The configured identity provider is not installed")
    return IdentityAuthority(max(5, int(getattr(config, "JWKS_LEEWAY_SECONDS", 5))))


# Supported FILES_STORAGE_TYPE values: aws (S3) and gcp (GCS) everywhere; filesystem only where ENV is one of
# these. Azure is not supported yet and keeps the feature unavailable.
FILESYSTEM_STORAGE_ENVIRONMENTS = ("local", "test")


def _storage_readiness(config):
    grace = getattr(config, "HOSTED_APPS_STORAGE_S3_GRACE_SECONDS", 30)
    if isinstance(grace, bool) or not isinstance(grace, int) or grace < 0:
        raise ValueError("HOSTED_APPS_STORAGE_S3_GRACE_SECONDS must be a non-negative integer")
    storage_type = str(getattr(config, "FILES_STORAGE_TYPE", "filesystem")).lower()
    if storage_type == "azure":
        raise ValueError("Hosted applications do not support Azure storage yet")
    if storage_type in ("aws", "gcp"):
        _object_storage_readiness(config)
        return
    if storage_type != "filesystem":
        raise ValueError(f"Hosted applications do not support FILES_STORAGE_TYPE={storage_type}")
    _filesystem_storage_readiness(config)


def _object_storage_readiness(config):
    if not getattr(config, "HOSTED_APPS_STORAGE_BUCKET", ""):
        raise ValueError("HOSTED_APPS_STORAGE_BUCKET is required for object storage")
    # The reconciliation sweep runs on object storage only.
    for name, default in (
        ("HOSTED_APPS_STORAGE_SWEEP_SECONDS", 3600),
        ("HOSTED_APPS_STORAGE_SWEEP_BUDGET_SECONDS", 20),
    ):
        value = getattr(config, name, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")


def _filesystem_storage_readiness(config):
    # The volume adapter has no fencing or cleanup across replicas: local development and tests only.
    if str(getattr(config, "ENV", "")).lower() not in FILESYSTEM_STORAGE_ENVIRONMENTS:
        raise ValueError(
            "Hosted applications need object storage (FILES_STORAGE_TYPE=aws or gcp); "
            "the filesystem adapter is for local development and tests only"
        )
    dedicated = getattr(config, "HOSTED_APPS_STORAGE_DIR", "") or ""
    if not Path(dedicated).is_absolute():
        raise ValueError("HOSTED_APPS_STORAGE_DIR must be an absolute path")
    generic = Path(getattr(config, "FILES_STORAGE_DIR", "./codemie-storage")).resolve()
    resolved = Path(dedicated).resolve()
    if resolved == generic or resolved.is_relative_to(generic):
        raise ValueError("HOSTED_APPS_STORAGE_DIR cannot be inside FILES_STORAGE_DIR")


def _first_party_urls(config) -> list[tuple[str, str]]:
    """(setting, URL) pairs for browser-facing first-party services, including identity providers."""
    urls = [(name, getattr(config, name, "") or "") for name in _FIRST_PARTY_URL_SETTINGS]
    try:
        entries = _jwks_entries(getattr(config, "JWKS_TRUSTED_ISSUERS", ""))
    except ValueError:
        entries = []
    for entry in entries:
        urls += [("JWKS_TRUSTED_ISSUERS", entry.get(key) or "") for key in ("issuer", "jwks_uri", "discovery_url")]
    return urls


def _separate_from_first_party(config, domain):
    content = registrable_domain(domain)
    for name, url in _first_party_urls(config):
        try:
            hostname = urlsplit(url).hostname if isinstance(url, str) else None
        except ValueError:
            continue
        if hostname and registrable_domain(hostname.rstrip(".")) == content:
            raise ValueError(f"Content must use a registrable domain separate from {name}")


def _validate_validator(config, *, local):
    """The validator is a private in-cluster service (HTTP or HTTPS) authenticated by its service key.

    Local development must use a validator on the loopback interface.
    """
    unprotected = "A valid validation service URL is required"
    try:
        validator = urlsplit(config.HOSTED_APPS_VALIDATOR_URL)
        validator.port  # noqa: B018 - property access validates the port syntax
    except ValueError as exc:
        raise ValueError(unprotected) from exc
    loopback = validator.hostname in _LOOPBACK_HOSTS
    if (
        validator.username
        or validator.password
        or validator.query
        or validator.fragment
        or validator.path not in ("", "/")
        or not validator.hostname
        or validator.scheme not in ("http", "https")
    ):
        raise ValueError(unprotected)
    if local and not loopback:
        raise ValueError("Local content requires a loopback validation service")
    if len(config.HOSTED_APPS_VALIDATOR_KEY) < 32:
        raise ValueError("HOSTED_APPS_VALIDATOR_KEY must be at least 32 characters")


def _check_content_namespace(domain, port, port_text, secret, *, local):
    if not hosted_feature_enabled():
        raise ValueError("Hosted applications are disabled")
    if not domain:
        raise ValueError("HOSTED_APPS_CONTENT_DOMAIN is not set")
    if len(secret) < 32:
        raise ValueError("HOSTED_APPS_CREDENTIAL_SECRET must be at least 32 characters")
    if local:
        if not 1024 <= port <= 65535 or port_text != str(port):
            raise ValueError("Local content must use apps.localhost with the backend port")
    elif port_text or not registrable_domain(domain):
        raise ValueError("Production content requires a registrable HTTPS domain")


def _check_ui_separation(ui_origin, domain, *, local):
    if not local and registrable_domain(urlsplit(ui_origin).hostname) == registrable_domain(domain):
        raise ValueError("Content must use a registrable domain separate from FRONTEND_URL")


@dataclass(frozen=True)
class HostedSettings:
    enabled: bool
    disabled_reason: str | None
    content_domain: str
    content_port: int
    local: bool
    ui_origins: tuple[str, ...]
    secret: bytes = field(repr=False)
    authority: IdentityAuthority | None = None

    def version_host(self, version_id, *, viewer=False) -> str:
        """Two distinct origins under the wildcard: uploaded code and its immutable standalone frame."""
        suffix = "" if self.content_port == 443 else ":" + str(self.content_port)
        prefix = "w-" if viewer else "v-"
        return prefix + version_id.hex + "." + self.content_domain + suffix

    def version_origin(self, version_id, *, viewer=False) -> str:
        return ("http" if self.local else "https") + "://" + self.version_host(version_id, viewer=viewer)

    @classmethod
    def from_config(cls, config) -> "HostedSettings":
        # The content namespace is parsed even while disabled: host dispatch keeps it closed during outages.
        raw = (getattr(config, "HOSTED_APPS_CONTENT_DOMAIN", "") or "").strip().lower()
        domain, _, port_text = raw.partition(":")
        domain = domain.rstrip(".")
        local = str(getattr(config, "ENV", "")).lower() == "local" and domain == LOCAL_CONTENT_DOMAIN
        port = int(port_text) if port_text.isdigit() else 443
        secret = config.HOSTED_APPS_CREDENTIAL_SECRET.encode()
        origins, authority, reason = (), None, None
        try:
            _check_content_namespace(domain, port, port_text, secret, local=local)
            origins = (_ui_origin(config, local=local),)
            _check_ui_separation(origins[0], domain, local=local)
            authority = identity_authority(config)
            _storage_readiness(config)
            if not local:
                _separate_from_first_party(config, domain)
            _validate_validator(config, local=local)
        except ValueError as exc:
            reason = str(exc)
        return cls(reason is None, reason, domain, port, local, origins, secret, authority)

    def launch_policy(self) -> dict:
        return {
            "version": "origin-policy-1",
            "ui_origins": list(self.ui_origins),
            "content": {
                "scheme": "http" if self.local else "https",
                "registrable_domain": self.content_domain,
                "version_host_prefix": "v-",
                "version_id_encoding": "uuid_hex",
                "port": self.content_port,
                "bootstrap_path": "/_platform/launch",
            },
        }


def log_disabled_reason(settings: HostedSettings) -> bool:
    """Startup signal for an operator who turned the feature on for a deployment that cannot run it safely."""
    if settings.enabled or not hosted_feature_enabled():
        return False
    from codemie.configs.logger import logger

    logger.warning(f"Hosted applications are enabled but unavailable: {settings.disabled_reason}")
    return True
