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

"""Tool OAuth state signing + semantic validation over the enterprise signer."""

import time

import pytest

from tests.codemie.service.oauth.tms_stub import install_tms_stub_for_import

install_tms_stub_for_import()

from codemie.service.oauth.state_signing import (  # noqa: E402
    ToolStateError,
    sign_tool_state,
    verify_tool_state,
)

KEY = b"k" * 32


def _sign(**overrides):
    kwargs = {
        "provider": "jira",
        "integration_id": "s1",
        "user_id": "u1",
        "redirect_uri": "https://cb",
        "signing_key": KEY,
    }
    kwargs.update(overrides)
    return sign_tool_state(**kwargs)


def test_round_trip():
    state = verify_tool_state(_sign(), signing_key=KEY, expected_providers={"jira"})
    assert state.provider == "jira"
    assert state.integration_id == "s1"
    assert state.user_id == "u1"
    assert state.redirect_uri == "https://cb"
    assert state.nonce


def test_rejects_tamper():
    body, sig = _sign().split(".")
    # Flip the FIRST character, not the last: this is a 43-char (32-byte HMAC-SHA256,
    # base64url, no padding) signature, and 43 % 4 == 3 means the last character only
    # carries 4 of its 6 bits (the other 2 are padding) — a differing last character can
    # still decode to the identical byte, leaving the signature valid and making the test
    # flaky rather than a real tamper. The first character sits in a full 4-char group with
    # no padding ambiguity, so a different character there is guaranteed to change the
    # decoded bytes.
    tampered_char = "a" if sig[0] != "a" else "b"
    with pytest.raises(ToolStateError):
        verify_tool_state(f"{body}.{tampered_char}{sig[1:]}", signing_key=KEY, expected_providers={"jira"})


def test_rejects_wrong_key():
    with pytest.raises(ToolStateError):
        verify_tool_state(_sign(), signing_key=b"z" * 32, expected_providers={"jira"})


def test_rejects_expired():
    with pytest.raises(ToolStateError):
        verify_tool_state(_sign(), signing_key=KEY, expected_providers={"jira"}, max_age_seconds=-1)


def test_rejects_future_timestamp(monkeypatch):
    import codemie.service.oauth.state_signing as m

    real_time = time.time
    monkeypatch.setattr(m.time, "time", lambda: real_time() + 3600)  # mint 1h in the future
    token = _sign()
    monkeypatch.setattr(m.time, "time", real_time)
    with pytest.raises(ToolStateError):
        verify_tool_state(token, signing_key=KEY, expected_providers={"jira"})


def test_rejects_wrong_provider():
    with pytest.raises(ToolStateError):
        verify_tool_state(_sign(provider="jira"), signing_key=KEY, expected_providers={"gitlab"})


def test_accepts_provider_in_allowlist():
    state = verify_tool_state(_sign(provider="confluence"), signing_key=KEY, expected_providers={"jira", "confluence"})
    assert state.provider == "confluence"


@pytest.mark.parametrize("bad", ["", "no-dot", "a.b.c"])
def test_rejects_malformed(bad):
    with pytest.raises(ToolStateError):
        verify_tool_state(bad, signing_key=KEY, expected_providers={"jira"})
