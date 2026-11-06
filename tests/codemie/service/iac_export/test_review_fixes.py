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

import json
import time
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from codemie.service.iac_export.credential_ref import env_ref, env_ref_scope
from codemie.service.iac_export.document import ExportDocument, ExportError, to_canonical_bytes
from codemie.service.iac_export.redaction import (
    has_literal_credential,
    redact_arg_list,
    redact_args,
    redact_url,
    text_has_credentials,
    url_has_credentials,
)
from codemie.service.iac_export.references import ExportScope, NameEntry, NameResolver

SCOPE = ExportScope(project="p")


# CR-002
@pytest.mark.parametrize("key", ["", "   ", "!!!"])
def test_env_ref_rejects_empty_key(key):
    with pytest.raises(ExportError):
        env_ref("ident", key)


def test_env_ref_scope_detects_collision_between_distinct_pairs():
    with env_ref_scope():
        env_ref("a_b", "c")
        with pytest.raises(ExportError, match="collid"):
            env_ref("a", "b_c")


def test_env_ref_scope_allows_repeated_identical_pair_and_resets():
    with env_ref_scope():
        assert env_ref("a", "b") == env_ref("a", "b")
    with env_ref_scope():
        env_ref("a_b", "c")
    env_ref("a", "b_c")  # outside any scope: no tracking


# CR-003
class _Meta(BaseModel):
    kind: str = "k"


class _Spec(BaseModel):
    tools: list[dict] = []
    custom: dict = {}


def test_free_form_fields_are_preserved_verbatim():
    custom = {"tools": [{"x": 1}, {"y": None}], "n": None, "skills": [3, 1]}
    doc = ExportDocument(
        meta=_Meta(),
        spec=_Spec(tools=[{"name": "b"}, {"name": "a"}], custom=custom),
        sort_keys={"tools": lambda t: t["name"], "skills": lambda s: s},
        verbatim=frozenset({"custom"}),
    )
    data = json.loads(to_canonical_bytes(doc))["spec"]
    assert data["custom"] == custom
    assert [t["name"] for t in data["tools"]] == ["a", "b"]


# CR-002 (final review)
def test_sort_is_independent_of_stored_order_with_missing_and_mixed_keys():
    items = [{"n": 1}, {"name": "b"}, {"name": 3}, {"name": "a"}, {"other": True}, {"name": ["x"]}]
    outputs = set()
    for order in (items, list(reversed(items)), items[2:] + items[:2]):
        doc = ExportDocument(meta=_Meta(), spec=_Spec(tools=order), sort_keys={"tools": lambda t: t["name"]})
        outputs.add(to_canonical_bytes(doc))
    assert len(outputs) == 1
    names = [t.get("name") for t in json.loads(outputs.pop())["spec"]["tools"]]
    assert names == [["x"], 3, "a", "b", None, None]


# CR-006
def test_redact_url_replaces_userinfo_and_secret_query():
    assert redact_url("https://u:p@h/x", "I", "URL") == "env:I_URL"
    assert redact_url("https://h/x?api_key=zzz", "I", "URL") == "env:I_URL"
    assert redact_url("https://h/x?v=1", "I", "URL") == "https://h/x?v=1"
    assert redact_url(None, "I", "URL") is None


def test_redact_args_replaces_credential_bearing_text():
    assert redact_args("--token abc123", "I", "ARGS") == "env:I_ARGS"
    assert redact_args("API_KEY=abc", "I", "ARGS") == "env:I_ARGS"
    assert redact_args("-y @scope/server", "I", "ARGS") == "-y @scope/server"
    assert redact_args(None, "I", "ARGS") is None


# CR-014
def test_unrelated_duplicate_of_another_name_does_not_fail():
    resolver = NameResolver(lambda k, s: [NameEntry("a1", "Alpha"), NameEntry("a2", "Beta"), NameEntry("a3", "Beta")])
    assert resolver.resolve("assistant", "a1", SCOPE) == "Alpha"


def test_clash_among_referenced_names_fails():
    resolver = NameResolver(lambda k, s: [NameEntry("a2", "Beta"), NameEntry("a3", "Beta")])
    with pytest.raises(ExportError, match="Beta"):
        resolver.resolve_many("assistant", ["a2", "a3"], SCOPE)


def test_default_resolver_queries_only_referenced_ids():
    calls = []

    def ids_lookup(kind, ids, scope):
        calls.append((kind, tuple(ids)))
        return [NameEntry(i, f"n-{i}") for i in ids]

    resolver = NameResolver(ids_lookup=ids_lookup, names_lookup=lambda k, names, s: [])
    assert resolver.resolve_many("integration", ["i1", "i2", "i1"], SCOPE) == {"i1": "n-i1", "i2": "n-i2"}
    resolver.resolve("integration", "i1", SCOPE)
    assert calls == [("integration", ("i1", "i2"))]


def test_missing_referenced_id_is_precise_error():
    resolver = NameResolver(ids_lookup=lambda k, ids, s: [])
    with pytest.raises(ExportError, match="'zz'"):
        resolver.resolve("skill", "zz", SCOPE)


def test_db_ids_lookup_filters_by_ids():
    from unittest.mock import patch

    from codemie.service.iac_export.references import db_ids_lookup

    session = MagicMock()
    with patch("codemie.clients.postgres.get_session") as gs:
        gs.return_value.__enter__.return_value = session
        for kind in ("assistant", "integration"):
            session.exec.return_value.all.return_value = [("i1", "N1", "p"), ("i2", None, "p")]
            assert db_ids_lookup(kind, ["i1", "i2"], SCOPE) == [NameEntry("i1", "N1")]
        session.exec.return_value.all.return_value = [("i1", "N1"), ("i2", None)]
        assert db_ids_lookup("category", ["i1", "i2"], SCOPE) == [NameEntry("i1", "N1")]
    assert db_ids_lookup("assistant", [], SCOPE) == []


# MR 4545 review (CR 26bf9077): the engine's own apply-time lookup only searches the target project.
def test_db_ids_lookup_refuses_a_reference_outside_the_target_project():
    from unittest.mock import patch

    from codemie.service.iac_export.references import db_ids_lookup

    session = MagicMock()
    session.exec.return_value.all.return_value = [("a1", "Other Project's Assistant", "other-project")]
    with patch("codemie.clients.postgres.get_session") as gs:
        gs.return_value.__enter__.return_value = session
        with pytest.raises(ExportError, match="outside target project"):
            db_ids_lookup("assistant", ["a1"], SCOPE)


# CR-006 (final review)
@pytest.mark.parametrize("url", ["http://[::1/x", "http://h:notaport/x", "http://[bad"])
def test_malformed_url_is_redacted_not_raised(url):
    assert redact_url(url, "I", "URL") == "env:I_URL"


# CR-007 (final review)
@pytest.mark.parametrize(
    "args",
    [["--author", "me"], ["--author=me", "run"], ["-y", "@scope/server"], ["--port", "80", "--timeout", "5"]],
)
def test_benign_arguments_are_kept(args):
    assert redact_arg_list(args, "I", "ARGS") == args


@pytest.mark.parametrize(
    "args,secret_index",
    [
        (["--token", "s3cr3t"], 1),
        (["--key", "s3cr3t"], 1),
        (["--sig", "s3cr3t"], 1),
        (["-p", "s3cr3t"], 1),
        (["-k", "s3cr3t"], 1),
        (["--api-key=s3cr3t"], 0),
        (["--password", "s3cr3t"], 1),
        (["run", "sk-abcdefghijklmnopqrstuvwx"], 1),
        (["run", "ghp_abcdefghijklmnopqrstuvwxyz0123"], 1),
        (["run", "key=s3cr3t"], 1),
    ],
)
def test_credential_arguments_are_referenced(args, secret_index):
    out = redact_arg_list(args, "I", "ARGS")
    assert out[secret_index].startswith("env:I_ARGS_")
    assert "s3cr3t" not in str(out) and "sk-abc" not in str(out) and "ghp_" not in str(out)


def test_value_after_an_inline_flag_is_not_swallowed():
    assert redact_arg_list(["--token=abc", "run"], "I", "ARGS") == ["env:I_ARGS_0", "run"]


@pytest.mark.parametrize("url", ["https://h/x?key=zzz", "https://h/x?sig=zzz", "https://h/x?code=zzz"])
def test_short_secret_query_names_are_redacted(url):
    assert redact_url(url, "I", "URL") == "env:I_URL"


# MR 4545 review: redaction must not fail open
@pytest.mark.parametrize(
    "url",
    [
        "https://mcp.example.com/sse/ghp_AbCdEfGhIjKlMnOpQrStUvWx",
        "https://hooks.slack.com/services/T0123/B0456/aBcD1234eFgH5678iJkL9012",
        "https://h/mcp?config=eyJhYmMiOiJkZWYifQabcdefghij0123456789",
        "https://h/mcp?k=sk-abcdefghijklmnopqrstuvwx",
    ],
)
def test_redact_url_replaces_tokens_in_path_and_neutral_query(url):
    assert redact_url(url, "srv", "URL").startswith("env:")


@pytest.mark.parametrize(
    "url",
    ["https://h/mcp/123e4567-e89b-12d3-a456-426614174000", "https://github.com/org/my-long-repository-name-2024-v2"],
)
def test_redact_url_keeps_uuid_and_hyphenated_names(url):
    assert redact_url(url, "srv", "URL") == url


def test_redact_arg_list_handles_json_config_argument():
    out = redact_arg_list(["--config", '{"apiKey":"abc123secret"}'], "srv", "ARGS")
    assert out[0] == "--config" and out[1].startswith("env:")
    assert redact_args('npx srv --config {"token": "abc"}', "srv", "CMD").startswith("env:")


def test_redact_arg_list_keeps_sizes_and_ports():
    args = ["--max-tokens", "4096", "-p", "8080"]
    assert redact_arg_list(args, "srv", "ARGS") == args
    assert redact_arg_list(["-p", "hunter2"], "srv", "ARGS")[1].startswith("env:")


def test_redact_free_form_non_string_secret_values_and_mixed_placeholders():
    from codemie.service.iac_export.redaction import redact_free_form

    out = redact_free_form(
        {
            "api_key": 123456,
            "token": {"value": "xyz"},
            "password": ["a"],
            "pw_mixed": "hunter2{{x}}",
            "password2": "hunter2{{x}}",
            "api_key3": "env:prod-raw-secret",
            "Authorization": "Bearer {{user.token}}",
            "Content-Type": "application/json",
            "max_tokens": 4096,
        },
        "i",
        "k",
    )
    assert out["api_key"].startswith("env:") and out["token"].startswith("env:") and out["password"].startswith("env:")
    assert out["password2"].startswith("env:") and out["api_key3"].startswith("env:")
    assert out["Authorization"] == "Bearer {{user.token}}"
    assert out["Content-Type"] == "application/json" and out["max_tokens"] == 4096


# MR 4545 review (CR 1f3a8151): _SECRET_ARG and the `eyJ` branch of _TOKEN_LITERAL were still quadratic.
@pytest.mark.parametrize(
    "text",
    [
        "apikey" * 8_000,  # ~48 KB: _SECRET_ARG backtracking on a long run with no real separator
        "eyJ" + "a" * 24_000 + "." + "b" * 24_000,  # ~48 KB: the eyJ branch's three unbounded segments
    ],
)
def test_secret_detection_is_not_quadratic(text):
    start = time.monotonic()
    text_has_credentials(text)
    assert time.monotonic() - start < 1  # was ~20s for the first case before the fix


def test_overlong_text_fails_closed_without_scanning():
    text = "x" * 50_001
    assert text_has_credentials(text) is True
    assert url_has_credentials("https://h/" + "x" * 50_001) is True


# MR 4545 review (auto_epmd-edp_vcs): has_literal_credential must not reject long credential-free content — it
# backs the whole-document leak check in document.py, which runs over system prompts and skill content that are
# legitimately larger than the bounded fields url_has_credentials/text_has_credentials guard (links, args, URLs).
def test_long_credential_free_text_is_not_flagged_as_a_credential():
    text = "This is a long, ordinary system prompt. " * 2_000  # ~84 KB, well past _MAX_SCAN_LENGTH
    start = time.monotonic()
    assert has_literal_credential(text) is False
    assert time.monotonic() - start < 1


def test_long_text_with_a_real_credential_is_still_caught():
    text = "This is a long, ordinary system prompt. " * 2_000 + "ghp_AbCdEfGhIjKlMnOpQrStUvWx"
    assert has_literal_credential(text) is True


def test_canonical_bytes_refuses_a_literal_credential_left_in_any_field():
    class Spec(BaseModel):
        jql: str

    doc = ExportDocument(
        meta=_Meta(kind="datasource", name="x"), spec=Spec(jql="text ~ 'ghp_AbCdEfGhIjKlMnOpQrStUvWx'")
    )
    with pytest.raises(ExportError, match=r"\$\.spec\.jql"):
        to_canonical_bytes(doc)
