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

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from codemie.core.hosted_applications import settings as settings_module
from codemie.core.hosted_applications.settings import (
    HostedSettings,
    IdentityAuthority,
    log_disabled_reason,
    registrable_domain,
)


def config(**changes):
    values = {
        # A non-local environment where the filesystem adapter is still allowed; production cases set ENV.
        "ENV": "test",
        "HOSTED_APPS_CONTENT_DOMAIN": "codemie-content.net",
        "HOSTED_APPS_CREDENTIAL_SECRET": "a" * 48,
        "HOSTED_APPS_STORAGE_DIR": "/mnt/hosted-applications",
        "HOSTED_APPS_VALIDATOR_URL": "https://validator.internal",
        "HOSTED_APPS_VALIDATOR_KEY": "b" * 48,
        "FILES_STORAGE_DIR": "./codemie-storage",
        "ENABLE_USER_MANAGEMENT": True,
        "IDP_PROVIDER": "local",
        "JWT_ISSUER": "codemie-local",
        "JWKS_VALIDATION_ENABLED": False,
        "JWKS_TRUSTED_ISSUERS": "",
        "JWKS_LEEWAY_SECONDS": 30,
        "FRONTEND_URL": "https://codemie.example.com",
    }
    return SimpleNamespace(**{**values, **changes})


def settings(**changes):
    return HostedSettings.from_config(config(**changes))


LOCAL = {
    "ENV": "local",
    "HOSTED_APPS_CONTENT_DOMAIN": "apps.localhost:8081",
    "HOSTED_APPS_VALIDATOR_URL": "http://127.0.0.1:8091",
    "FRONTEND_URL": "http://localhost:5173",
}


@pytest.fixture(autouse=True)
def feature_flag():
    with patch.object(settings_module, "hosted_feature_enabled", return_value=True) as enabled:
        yield enabled


@pytest.fixture(autouse=True)
def enterprise_idp_installed():
    with patch.object(settings_module, "_enterprise_idp_available", return_value=True) as available:
        yield available


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("a.example.co.uk", "example.co.uk"),
        ("a.foo.github.io", "foo.github.io"),
        ("a.example.com", "example.com"),
        ("www.city.kawasaki.jp", "city.kawasaki.jp"),
        ("a.example.net", "example.net"),
    ],
)
def test_real_public_suffix_rules(host, expected):
    assert registrable_domain(host) == expected


def test_production_needs_only_the_hosted_settings_on_top_of_the_platform():
    result = settings()
    assert result.enabled, result.disabled_reason
    assert result.ui_origins == ("https://codemie.example.com",)
    assert result.authority == IdentityAuthority(30)
    assert result.launch_policy()["content"] == {
        "scheme": "https",
        "registrable_domain": "codemie-content.net",
        "version_host_prefix": "v-",
        "version_id_encoding": "uuid_hex",
        "port": 443,
        "bootstrap_path": "/_platform/launch",
    }


def test_customer_config_switch_turns_the_feature_off_but_keeps_the_content_namespace(feature_flag):
    feature_flag.return_value = False
    result = settings()
    assert not result.enabled
    assert result.disabled_reason == "Hosted applications are disabled"
    # Host dispatch keeps routing the configured namespace to the closed content tree.
    assert result.content_domain == "codemie-content.net"


@pytest.mark.parametrize(
    ("frontend", "origin"),
    [
        ("https://codemie.example.com/", "https://codemie.example.com"),
        ("https://codemie.example.com/ui/#/chats", "https://codemie.example.com"),
        ("https://CodeMie.Example.com:443", "https://codemie.example.com"),
        ("https://codemie.example.com:8443", "https://codemie.example.com:8443"),
    ],
)
def test_ui_origin_is_the_origin_of_frontend_url(frontend, origin):
    assert settings(FRONTEND_URL=frontend).ui_origins == (origin,)


def test_dedicated_volume_is_required_and_kept_out_of_general_file_storage(tmp_path):
    generic = tmp_path / "general-files"
    assert settings(FILES_STORAGE_DIR=str(generic), HOSTED_APPS_STORAGE_DIR=str(tmp_path / "private")).enabled
    for dedicated in ("", "relative/volume", str(generic), str(generic / "owner")):
        result = settings(FILES_STORAGE_DIR=str(generic), HOSTED_APPS_STORAGE_DIR=dedicated)
        assert not result.enabled
        assert "HOSTED_APPS_STORAGE_DIR" in result.disabled_reason


@pytest.mark.parametrize(
    ("changes", "enabled", "reason"),
    [
        ({"FILES_STORAGE_TYPE": "filesystem"}, True, None),
        ({"FILES_STORAGE_TYPE": "filesystem", "HOSTED_APPS_STORAGE_DIR": ""}, False, "HOSTED_APPS_STORAGE_DIR"),
        ({"FILES_STORAGE_TYPE": "aws", "HOSTED_APPS_STORAGE_BUCKET": ""}, False, "HOSTED_APPS_STORAGE_BUCKET"),
        ({"FILES_STORAGE_TYPE": "gcp", "HOSTED_APPS_STORAGE_BUCKET": ""}, False, "HOSTED_APPS_STORAGE_BUCKET"),
        ({"FILES_STORAGE_TYPE": "aws", "HOSTED_APPS_STORAGE_BUCKET": "b", "HOSTED_APPS_STORAGE_DIR": ""}, True, None),
        ({"FILES_STORAGE_TYPE": "gcp", "HOSTED_APPS_STORAGE_BUCKET": "b", "HOSTED_APPS_STORAGE_DIR": ""}, True, None),
        ({"FILES_STORAGE_TYPE": "azure", "HOSTED_APPS_STORAGE_BUCKET": "b"}, False, "Azure"),
        (
            {
                "FILES_STORAGE_TYPE": "aws",
                "HOSTED_APPS_STORAGE_BUCKET": "b",
                "HOSTED_APPS_STORAGE_S3_GRACE_SECONDS": -1,
            },
            False,
            "HOSTED_APPS_STORAGE_S3_GRACE_SECONDS",
        ),
        (
            {"FILES_STORAGE_TYPE": "aws", "HOSTED_APPS_STORAGE_BUCKET": "b", "HOSTED_APPS_STORAGE_SWEEP_SECONDS": 0},
            False,
            "HOSTED_APPS_STORAGE_SWEEP_SECONDS",
        ),
        (
            {"FILES_STORAGE_TYPE": "gcp", "HOSTED_APPS_STORAGE_BUCKET": "b", "HOSTED_APPS_STORAGE_SWEEP_SECONDS": -60},
            False,
            "HOSTED_APPS_STORAGE_SWEEP_SECONDS",
        ),
        (
            {
                "FILES_STORAGE_TYPE": "aws",
                "HOSTED_APPS_STORAGE_BUCKET": "b",
                "HOSTED_APPS_STORAGE_SWEEP_BUDGET_SECONDS": 0,
            },
            False,
            "HOSTED_APPS_STORAGE_SWEEP_BUDGET_SECONDS",
        ),
        (
            {
                "FILES_STORAGE_TYPE": "gcp",
                "HOSTED_APPS_STORAGE_BUCKET": "b",
                "HOSTED_APPS_STORAGE_SWEEP_BUDGET_SECONDS": True,
            },
            False,
            "HOSTED_APPS_STORAGE_SWEEP_BUDGET_SECONDS",
        ),
        # The volume adapter is for local development and tests only.
        ({"FILES_STORAGE_TYPE": "filesystem", "ENV": "prod"}, False, "object storage"),
        ({"FILES_STORAGE_TYPE": "filesystem", "ENV": "dev"}, False, "object storage"),
        ({"FILES_STORAGE_TYPE": "aws", "HOSTED_APPS_STORAGE_BUCKET": "b", "ENV": "prod"}, True, None),
        ({"FILES_STORAGE_TYPE": "minio", "HOSTED_APPS_STORAGE_BUCKET": "b"}, False, "FILES_STORAGE_TYPE=minio"),
        # The sweep runs on object storage only: the filesystem adapter ignores its settings.
        ({"FILES_STORAGE_TYPE": "filesystem", "HOSTED_APPS_STORAGE_SWEEP_SECONDS": 0}, True, None),
    ],
)
def test_storage_readiness_by_files_storage_type(changes, enabled, reason):
    result = settings(**changes)
    assert result.enabled is enabled, result.disabled_reason
    if reason:
        assert reason in result.disabled_reason


@pytest.mark.parametrize(
    "changes",
    [
        {"HOSTED_APPS_CONTENT_DOMAIN": ""},
        {"HOSTED_APPS_CONTENT_DOMAIN": "apps.codemie.example.com"},
        {"HOSTED_APPS_CONTENT_DOMAIN": "co.uk"},
        {"HOSTED_APPS_CONTENT_DOMAIN": "github.io"},
        {"HOSTED_APPS_CONTENT_DOMAIN": "codemie-content.net:8443"},
        {"HOSTED_APPS_CONTENT_DOMAIN": "apps.localhost:8081"},
        {"FRONTEND_URL": "http://codemie.example.com"},
        {"FRONTEND_URL": "https://user:pass@codemie.example.com"},
        {"FRONTEND_URL": "not a url"},
        {"HOSTED_APPS_CREDENTIAL_SECRET": "short"},
        {"HOSTED_APPS_VALIDATOR_KEY": ""},
    ],
)
def test_incomplete_or_unsafe_production_configuration_is_disabled(changes):
    result = settings(**changes)
    assert not result.enabled
    assert result.disabled_reason
    assert "pass" not in result.disabled_reason


def test_local_development_uses_apps_localhost_on_the_backend_port():
    result = settings(**LOCAL)
    assert result.enabled, result.disabled_reason
    assert (result.local, result.content_domain, result.content_port) == (True, "apps.localhost", 8081)
    assert result.ui_origins == ("http://localhost:5173",)
    assert result.launch_policy()["content"]["scheme"] == "http"


@pytest.mark.parametrize(
    "changes",
    [
        {"HOSTED_APPS_CONTENT_DOMAIN": "apps.localhost"},
        {"HOSTED_APPS_CONTENT_DOMAIN": "apps.localhost:80"},
        {"HOSTED_APPS_CONTENT_DOMAIN": "apps.localhost:08081"},
        {"FRONTEND_URL": "http://codemie.example.com"},
        {"HOSTED_APPS_VALIDATOR_URL": "https://validator.internal"},
    ],
)
def test_local_mode_stays_on_loopback(changes):
    result = settings(**{**LOCAL, **changes})
    assert not result.enabled
    assert result.disabled_reason


# Verified identity prerequisite


@pytest.mark.parametrize("provider", ["local", "keycloak", "oidc", "entraid-oidc"])
@pytest.mark.parametrize("jwks", [False, True])
def test_every_provider_works_with_user_management(provider, jwks):
    result = settings(IDP_PROVIDER=provider, ENABLE_USER_MANAGEMENT=True, JWKS_VALIDATION_ENABLED=jwks)
    assert result.enabled, result.disabled_reason
    assert result.authority == IdentityAuthority(30)


@pytest.mark.parametrize("provider", ["local", "keycloak", "oidc", "entraid-oidc"])
def test_feature_is_off_without_user_management(provider):
    result = settings(IDP_PROVIDER=provider, ENABLE_USER_MANAGEMENT=False, JWT_ISSUER="")
    assert not result.enabled
    assert result.disabled_reason == "Hosted applications need user management (ENABLE_USER_MANAGEMENT=true)"


def test_keycloak_service_account_settings_are_not_needed():
    result = settings(
        IDP_PROVIDER="keycloak",
        ENABLE_USER_MANAGEMENT=True,
        KEYCLOAK_ADMIN_URL="",
        KEYCLOAK_ADMIN_REALM="",
        KEYCLOAK_ADMIN_CLIENT_ID="",
        KEYCLOAK_ADMIN_CLIENT_SECRET="",
    )
    assert result.enabled, result.disabled_reason


@pytest.mark.parametrize(("configured", "expected"), [(0, 5), (5, 5), (30, 30)])
def test_leeway_follows_the_jwks_validator(configured, expected):
    result = settings(ENABLE_USER_MANAGEMENT=True, JWKS_LEEWAY_SECONDS=configured)
    assert result.authority.leeway_seconds == expected


def test_enterprise_identity_providers_must_be_installed(enterprise_idp_installed):
    enterprise_idp_installed.return_value = False
    result = settings(IDP_PROVIDER="keycloak", ENABLE_USER_MANAGEMENT=True)
    assert not result.enabled
    assert "identity provider" in result.disabled_reason


def test_local_sign_in_requires_user_management():
    result = settings(ENABLE_USER_MANAGEMENT=False)
    assert not result.enabled
    assert result.disabled_reason


@pytest.mark.parametrize("issuer", ["", "i" * 256])
def test_the_jwt_issuer_length_does_not_matter(issuer):
    result = settings(ENABLE_USER_MANAGEMENT=True, JWT_ISSUER=issuer)
    assert result.enabled, result.disabled_reason
    assert result.authority == IdentityAuthority(30)


# Content domain separation from first-party hosts


@pytest.mark.parametrize(
    ("setting", "changes"),
    [
        ("FRONTEND_URL", {"FRONTEND_URL": "https://ui.codemie-content.net"}),
        ("CALLBACK_API_BASE_URL", {"CALLBACK_API_BASE_URL": "https://api.codemie-content.net"}),
        (
            "KEYCLOAK_LOGOUT_URL",
            {"KEYCLOAK_LOGOUT_URL": "https://sso.codemie-content.net/realms/x/protocol/openid-connect/logout"},
        ),
        ("KEYCLOAK_ADMIN_URL", {"KEYCLOAK_ADMIN_URL": "https://sso.codemie-content.net"}),
        ("TOKEN_EXCHANGE_URL", {"TOKEN_EXCHANGE_URL": "https://sso.codemie-content.net/token"}),
        ("BROKER_AUTH_LOCATION_URL", {"BROKER_AUTH_LOCATION_URL": "https://auth.codemie-content.net/login"}),
    ],
)
def test_content_domain_must_be_separate_from_first_party_hosts(setting, changes):
    result = settings(**changes)
    assert not result.enabled
    assert setting in result.disabled_reason
    assert "codemie-content.net" not in result.disabled_reason


def test_content_domain_must_be_separate_from_the_identity_provider():
    issuer = "https://sso.codemie-content.net/realms/codemie"
    result = settings(
        JWKS_TRUSTED_ISSUERS=json.dumps([{"issuer": issuer, "audience": "c", "jwks_uri": issuer + "/certs"}]),
    )
    assert not result.enabled
    assert "separate" in result.disabled_reason


def test_unrelated_first_party_hosts_keep_the_feature_enabled():
    assert settings(
        CALLBACK_API_BASE_URL="http://host.docker.internal:8080",
        KEYCLOAK_LOGOUT_URL="https://sso.codemie.example.com/logout",
        TOKEN_EXCHANGE_URL="not a url",
    ).enabled


# Validator service URL


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8091",
        "http://localhost:8091",
        "http://[::1]:8091",
        "http://codemie-hosted-validator:8091",
        "http://10.0.0.7:8091",
        "https://validator.internal",
        "https://validator.internal:8443/",
    ],
)
def test_validator_is_a_private_http_or_https_service(url):
    assert settings(HOSTED_APPS_VALIDATOR_URL=url).enabled


@pytest.mark.parametrize(
    "url",
    [
        "ftp://127.0.0.1:8091",
        "http://127.0.0.1:8091/path",
        "http://user:pass@127.0.0.1:8091",
        "http://127.0.0.1:8091?q=1",
        "http://127.0.0.1:port",
        "",
    ],
)
def test_validator_rejects_malformed_urls(url):
    result = settings(HOSTED_APPS_VALIDATOR_URL=url)
    assert not result.enabled
    assert "validation service" in result.disabled_reason
    assert "user:pass" not in result.disabled_reason


# Startup signal


def test_requested_but_unavailable_feature_logs_its_reason_once():
    result = settings(HOSTED_APPS_CREDENTIAL_SECRET="short")
    with patch("codemie.configs.logger.logger") as logger:
        assert log_disabled_reason(result)
    logger.warning.assert_called_once()
    message = logger.warning.call_args.args[0]
    assert "HOSTED_APPS_CREDENTIAL_SECRET" in message
    assert "short" not in message


@pytest.mark.parametrize("enabled", [True, False])
def test_ready_or_switched_off_feature_logs_nothing(feature_flag, enabled):
    feature_flag.return_value = enabled
    result = settings()
    with patch("codemie.configs.logger.logger") as logger:
        assert not log_disabled_reason(result)
    logger.warning.assert_not_called()
