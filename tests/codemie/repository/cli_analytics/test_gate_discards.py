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

"""Gate discards are visible (EPMCDME-16441): one log line per discarded request."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from codemie.repository.cli_analytics.gate_discards import record_gate_discard
from codemie.repository.cli_analytics.postgres import ingestor as ingestor_module
from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from codemie.repository.cli_analytics.postgres.otlp import DecodedBatch

REASONS = ("cli_analytics_disabled", "project_context_required", "project_context_conflict")
LOGGER = "codemie.repository.cli_analytics.gate_discards"


@pytest.fixture(autouse=True)
def _capture_discard_log(caplog: pytest.LogCaptureFixture):
    """The platform logger does not propagate to the root, so attach caplog's handler to the discard logger."""
    logger = logging.getLogger(LOGGER)
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger=LOGGER)
    yield
    logger.removeHandler(caplog.handler)


def _lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER]


def _gate(reason: str | None, user_hash: str | None = "abcd1234abcd1234"):
    async def gate(_explicit):
        return reason

    gate.user_hash = user_hash
    return gate


class _NoSessionIdsBatch(DecodedBatch):
    """A real decoded batch whose logging-only `session_ids` fails when read."""

    def session_ids(self) -> set[str]:
        raise AssertionError("session ids read on the accepted path")


def _batch(wire_count: int = 3, cls: type[DecodedBatch] = DecodedBatch) -> DecodedBatch:
    return cls(wire_count=wire_count)


@pytest.mark.parametrize("route", ["otlp", "hooks"])
@pytest.mark.parametrize("reason", REASONS)
@pytest.mark.asyncio
async def test_discarded_request_logs_once(reason, route, caplog):
    ingestor = PostgresTelemetryIngestor(MagicMock())
    with (
        patch.object(ingestor_module, "decode_otlp", return_value=_batch(3)),
        patch.object(ingestor_module, "decode_hook_events", return_value=_batch(3)),
    ):
        if route == "otlp":
            await ingestor.ingest_otlp("logs", b"", "application/json", gate=_gate(reason))
        else:
            await ingestor.ingest_hook_events([{}], "u@example.com", 0, gate=_gate(reason))

    lines = _lines(caplog)
    assert len(lines) == 1
    for part in (reason, route, "3", "abcd1234abcd1234"):
        assert part in lines[0]


@pytest.mark.asyncio
async def test_otlp_discard_with_zero_records_is_still_logged(caplog):
    ingestor = PostgresTelemetryIngestor(MagicMock())
    with patch.object(ingestor_module, "decode_otlp", return_value=_batch(0)):
        await ingestor.ingest_otlp("logs", b"", "application/json", gate=_gate("cli_analytics_disabled"))
    lines = _lines(caplog)
    assert len(lines) == 1
    assert "dropped=0" in lines[0]


@pytest.mark.asyncio
async def test_accepted_request_logs_nothing(caplog):
    ingestor = PostgresTelemetryIngestor(MagicMock())
    ingestor._store = MagicMock(side_effect=lambda *_a, **_k: _done((1, 0)))
    with (
        patch.object(ingestor_module, "decode_hook_events", return_value=_batch(1)),
        patch.object(ingestor_module, "_attributed_to", side_effect=lambda b, _p: b),
    ):
        await ingestor.ingest_hook_events([{}], "u@example.com", 0, gate=_gate(None))
    assert _lines(caplog) == []


@pytest.mark.asyncio
async def test_accepted_request_never_reads_the_session_ids():
    ingestor = PostgresTelemetryIngestor(MagicMock())
    ingestor._store = MagicMock(side_effect=lambda *_a, **_k: _done((1, 0)))
    with (
        patch.object(ingestor_module, "decode_hook_events", return_value=_batch(1, _NoSessionIdsBatch)),
        patch.object(ingestor_module, "_attributed_to", side_effect=lambda b, _p: b),
    ):
        await ingestor.ingest_hook_events([{}], "u@example.com", 0, gate=_gate(None))


async def _done(value):
    return value


def test_empty_key_emits_no_hash_field(caplog):
    record_gate_discard("hooks", "cli_analytics_disabled", 2, None)
    lines = _lines(caplog)
    assert len(lines) == 1
    assert "user_hash" not in lines[0]


def test_log_has_no_email_project_or_raw_user_id(caplog):
    record_gate_discard("otlp", "project_context_required", 5, "0123456789abcdef")
    line = _lines(caplog)[0]
    assert "user_hash=0123456789abcdef" in line
    assert "@" not in line and "project=" not in line and "user_id" not in line


def test_session_count_and_sorted_ids_sit_between_dropped_and_user_hash(caplog):
    record_gate_discard("hooks", "cli_analytics_disabled", 4, "abcd1234abcd1234", {"b", "a"})
    lines = _lines(caplog)
    assert len(lines) == 1
    assert "dropped=4 session_count=2 session_ids=a,b user_hash=abcd1234abcd1234" in lines[0]


def test_no_sessions_logs_a_zero_count_and_no_ids_field(caplog):
    record_gate_discard("otlp", "cli_analytics_disabled", 0, None)
    line = _lines(caplog)[0]
    assert line.endswith("dropped=0 session_count=0")
    assert "session_ids" not in line


def test_hostile_ids_stay_on_one_line_without_whitespace_or_control_chars(caplog):
    record_gate_discard("otlp", "cli_analytics_disabled", 1, None, {"x\ny\r\x1b[31m z"})
    lines = _lines(caplog)
    assert len(lines) == 1
    ids = lines[0].split("session_ids=")[1]
    assert not any(c in ids for c in "\n\r\x1b ")
    assert ids == "x?y??[31m?z"


def test_sample_is_capped_at_three_ids_and_sixty_four_chars(caplog):
    record_gate_discard("otlp", "cli_analytics_disabled", 5, None, {"e", "d", "c", "b", "a"})
    assert "session_count=5 session_ids=a,b,c" in _lines(caplog)[0]
    caplog.clear()
    record_gate_discard("otlp", "cli_analytics_disabled", 1, None, {"z" * 100})
    assert f"session_ids={'z' * 64}" in _lines(caplog)[0]
    assert "z" * 65 not in _lines(caplog)[0]


def test_control_only_id_becomes_question_marks_on_one_line(caplog):
    record_gate_discard("otlp", "cli_analytics_disabled", 1, None, {"\n\r"})
    lines = _lines(caplog)
    assert len(lines) == 1
    assert lines[0].endswith("session_count=1 session_ids=??")


def test_ids_that_sanitize_alike_are_counted_twice_and_sampled_once(caplog):
    record_gate_discard("otlp", "cli_analytics_disabled", 2, None, {"a b", "a\tb"})
    assert _lines(caplog)[0].endswith("session_count=2 session_ids=a?b")
