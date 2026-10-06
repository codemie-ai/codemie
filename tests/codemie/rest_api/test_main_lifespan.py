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

"""Unit tests for the retrieval-unavailable startup warning in lifespan()."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ===========================================
# Tests: lifespan() retrieval warning
# ===========================================

# Marker used to abort lifespan() right after the code under test runs, so the
# test never needs to mock the rest of the (heavy) startup sequence.
_STOP_HERE = RuntimeError("stop-test-here")


@pytest.mark.asyncio
@patch("codemie.rest_api.main._initialize_database_and_defaults", side_effect=_STOP_HERE)
@patch("codemie.rest_api.main.logger")
@patch("codemie.rest_api.main.config")
async def test_lifespan_logs_retrieval_unavailable_warning_when_disabled(mock_config, mock_logger, mock_init_db):
    """RETRIEVAL_BACKEND=none logs a warning naming retrieval as unavailable."""
    from codemie.rest_api.main import lifespan

    mock_config.RETRIEVAL_BACKEND = "none"
    mock_config.to_safe_dict.return_value = {}

    app = MagicMock()

    with pytest.raises(RuntimeError):
        async with lifespan(app):
            pass

    warning_messages = [call.args[0] for call in mock_logger.warning.call_args_list]
    assert any("retrieval" in msg.lower() and "RETRIEVAL_BACKEND" in msg for msg in warning_messages)


@pytest.mark.asyncio
@patch("codemie.rest_api.main._initialize_database_and_defaults", side_effect=_STOP_HERE)
@patch("codemie.rest_api.main.logger")
@patch("codemie.rest_api.main.config")
async def test_lifespan_does_not_log_when_retrieval_available(mock_config, mock_logger, mock_init_db):
    """A supported RETRIEVAL_BACKEND does not log the retrieval-unavailable warning."""
    from codemie.rest_api.main import lifespan

    mock_config.RETRIEVAL_BACKEND = "elasticsearch"
    mock_config.to_safe_dict.return_value = {}

    app = MagicMock()

    with pytest.raises(RuntimeError):
        async with lifespan(app):
            pass

    warning_messages = [call.args[0] for call in mock_logger.warning.call_args_list]
    assert not any("retrieval" in msg.lower() and "RETRIEVAL_BACKEND" in msg for msg in warning_messages)
