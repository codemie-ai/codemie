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

"""CLI analytics subscription classification (EPMCDME-16440, D7)."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codemie.rest_api.routers.cli_analytics as router_module
import codemie.rest_api.routers.cli_analytics_ingest_route as route_module
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import authenticate
from codemie.service import customer_config_declarations as declarations
from codemie.service.analytics.cli_classification import classify, decode_organization, policy_version

ORGS = ["Acme Corp", "  Beta  "]


@pytest.mark.parametrize(
    ("routed", "org", "orgs", "expected"),
    [
        (True, "Acme Corp", ORGS, "codemie"),  # CodeMie in the list
        (True, "Other", ORGS, "codemie"),  # CodeMie out of the list
        (True, None, [], "codemie"),
        (False, "Acme Corp", [], "unknown"),  # empty list
        (False, None, ORGS, "unknown"),  # missing org
        (False, "   ", ORGS, "unknown"),  # blank org
        (False, "Acme Corp", ORGS, "corporate"),  # match
        (False, "  acme corp ", ORGS, "corporate"),  # trim + case-insensitive
        (False, "BETA", ORGS, "corporate"),  # list entries trimmed too
        (False, "Gamma", ORGS, "personal"),  # non-match
    ],
)
def test_classify_matrix(routed, org, orgs, expected):
    assert classify(routed, org, orgs) == expected


def test_policy_version_depends_only_on_normalized_list():
    assert policy_version(["b", "A"]) == policy_version([" a ", "B", "a"])
    assert policy_version(["a"]) != policy_version(["a", "b"])
    assert policy_version([]) == policy_version([" "])


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[authenticate] = lambda: object()
    return TestClient(app)


def _policy(raw: str | None):
    return patch.object(route_module, "customer_config", MagicMock(get_feature_setting=lambda *a, **k: raw))


def test_policy_endpoint_returns_list_and_version_changing_only_with_list():
    url = "/v1/analytics/cli-analytics/classification-policy"
    with patch.object(router_module, "_ensure_enabled", return_value=None):
        with _policy("Acme\n\n beta \n"):
            first = _client().get(url).json()
        with _policy("beta\nAcme"):  # same list, different layout
            same = _client().get(url).json()
        with _policy("Acme"):
            changed = _client().get(url).json()
        with _policy(None):
            empty = _client().get(url).json()
    assert first["organizations"] == ["Acme", "beta"]
    assert same["version"] == first["version"]
    assert changed["version"] != first["version"]
    assert empty["organizations"] == []


def test_policy_endpoint_is_gated_by_feature_flag():
    with patch.object(router_module, "customer_config", MagicMock(is_feature_enabled=lambda k: False)):
        with pytest.raises(ExtendedHTTPException) as exc:
            _client().get("/v1/analytics/cli-analytics/classification-policy")
    assert exc.value.code == 404


def encode_org(text: str) -> str:
    """What the CLI sender writes: `utf8-b64u.v1:` + unpadded base64url of the original UTF-8 (Node's
    `Buffer.toString('base64url')` gives the same bytes for these vectors)."""
    return "utf8-b64u.v1:" + base64.urlsafe_b64encode(text.encode()).rstrip(b"=").decode()


# Shared vectors: the decoded text is exactly what was sent (no trim, casefold or NFC here).
VECTORS = [
    "Société Générale",
    "Ак" * 128,  # 256 Cyrillic code points = 512 bytes: why the limit is not 256 bytes
    "株式会社テスト組織",
    "Org 😀𝒳",  # astral code points
    "100%25 Org",  # a literal percent sequence stays literal
    "  padded  ",
    "é".encode("utf-8").decode("latin-1"),  # mojibake text is just text
    "a" * 256,
    "😀" * 256,  # 1024 bytes: the byte limit exactly
]


@pytest.mark.parametrize("text", VECTORS)
def test_the_encoded_organization_round_trips_exactly(text):
    assert decode_organization(encode_org(text)) == text


def test_the_decoded_organization_goes_through_the_existing_classifier_normalization():
    assert classify(False, decode_organization(encode_org("  ТОВ Ромашка  ")), ["тов ромашка"]) == "corporate"
    assert classify(False, decode_organization(encode_org("Other")), ["тов ромашка"]) == "personal"


@pytest.mark.parametrize(
    "value",
    [
        "utf8-b64u.v2:QWNtZQ",  # unsupported version
        "QWNtZQ",  # no version
        "utf8-b64u.v1:",  # empty
        "utf8-b64u.v1:QWNtZQ==",  # padded
        "utf8-b64u.v1:QWNt+Q",  # standard alphabet
        "utf8-b64u.v1:QWNt/Q",
        "utf8-b64u.v1:QW Nt",
        "utf8-b64u.v1:Q",  # impossible length
        "utf8-b64u.v1:QWNtZR",  # non-canonical trailing bits (canonical is QWNtZQ)
        "utf8-b64u.v1:_w",  # invalid UTF-8 (0xFF)
        encode_org("   "),  # whitespace only
        encode_org("a\x00b"),  # C0 control
        encode_org("a\x85b"),  # C1 control
        encode_org("a\tb"),
        encode_org("a" * 257),  # 257 code points
        encode_org("😀" * 256 + "a"),  # 1025 bytes
        "utf8-b64u.v1:" + "A" * 1367,  # over the encoded length
    ],
)
def test_a_malformed_encoded_organization_is_rejected(value):
    with pytest.raises(ValueError):
        decode_organization(value)


def test_the_policy_advertises_the_encoding_without_changing_the_version():
    url = "/v1/analytics/cli-analytics/classification-policy"
    with patch.object(router_module, "_ensure_enabled", return_value=None), _policy("Acme"):
        body = _client().get(url).json()
    assert body["organization_encodings"] == ["utf8-b64u.v1"]
    assert body["version"] == policy_version(["Acme"])


def test_declaration_fields():
    decl = declarations.by_component_id("features:cliAnalytics")
    fields = {f.name: f for f in decl.fields}
    assert fields["corporateOrganizations"].type is declarations.FieldType.TEXTAREA
    assert set(fields) == {"corporateOrganizations"}
    assert decl.empty_value() == {"corporateOrganizations": ""}
