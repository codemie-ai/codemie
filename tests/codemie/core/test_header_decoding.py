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

import pytest

from codemie.core.header_decoding import decode_attribution_header


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "clients/%D1%80%D0%B5%D0%BF%D0%BE-%D1%82%D0%B5%D1%81%D1%82",
            "clients/репо-тест",
        ),
        ("my%20repo", "my repo"),
        ("org/repo", "org/repo"),  # raw passthrough, no-op
        ("100%", "100%"),  # malformed escape -> fallback to raw segment
        ("%25", "%"),  # literal percent round trip
        (None, None),
    ],
)
def test_decode_attribution_header(raw, expected):
    assert decode_attribution_header(raw) == expected


def test_decode_attribution_header_non_utf8_fallback():
    # a %-escape sequence that decodes to bytes that are not valid UTF-8 falls back to raw
    raw = "clients/%FF%FE"
    assert decode_attribution_header(raw) == raw


def test_decode_attribution_header_malformed_hex_escape_passes_through_silently():
    # unquote(errors="strict") does not raise for hex-escape-shaped-but-invalid syntax (a bare
    # "%" with no following hex digits): it tolerates it and returns the text unmodified. That
    # means this case never reaches our except branch and is never logged, unlike a genuinely
    # invalid UTF-8 byte sequence (test_decode_attribution_header_non_utf8_fallback_is_logged).
    from unittest.mock import patch

    with patch("codemie.core.header_decoding.logger.warning") as mock_warning:
        result = decode_attribution_header("100%")

    assert result == "100%"
    mock_warning.assert_not_called()


def test_decode_attribution_header_non_utf8_fallback_is_logged():
    # CR-001: the raw-text fallback must be auditable, not a silent default. The repo's
    # logging config disables propagation to the root logger, so assert directly on the
    # module logger rather than relying on caplog reaching it.
    from unittest.mock import patch

    with patch("codemie.core.header_decoding.logger.warning") as mock_warning:
        decode_attribution_header("clients/%FF%FE")

    mock_warning.assert_called_once()
    assert "decod" in mock_warning.call_args[0][0].lower()


@pytest.mark.parametrize(
    "raw",
    [
        "%20",  # single percent-encoded space
        "   ",  # literal whitespace, unencoded
        "",  # empty string decodes to empty
    ],
)
def test_decode_attribution_header_whitespace_only_result_is_none(raw):
    # CR-002: a whitespace-only decoded value must be treated as absent, not a truthy value
    # that can silently override a legitimate body-supplied repository/branch/project.
    assert decode_attribution_header(raw) is None
