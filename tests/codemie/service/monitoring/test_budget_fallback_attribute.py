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

"""EPMCDME-15111 AC6: every metric of a fallback request carries budget_fallback_from."""

import contextvars
from unittest.mock import patch

from codemie.core.dependecies import litellm_context
from codemie.rest_api.models.settings import LiteLLMContext
from codemie.service.monitoring.base_monitoring_service import send_log_metric


def _emit(ctx):
    captured = {}

    def run():
        if ctx is not None:
            litellm_context.set(ctx)
        with patch("codemie.service.monitoring.base_monitoring_service.logger"):
            attributes = {"project": "alice@example.com"}
            send_log_metric("some_metric", attributes)
            captured.update(attributes)

    contextvars.copy_context().run(run)
    return captured


def test_fallback_context_adds_marker():
    ctx = LiteLLMContext(credentials=None, current_project="alice@example.com", budget_fallback_from="philips-hr")

    assert _emit(ctx)["budget_fallback_from"] == "philips-hr"


def test_no_fallback_adds_nothing():
    ctx = LiteLLMContext(credentials=None, current_project="philips-hr")

    assert "budget_fallback_from" not in _emit(ctx)


def test_no_context_adds_nothing():
    assert "budget_fallback_from" not in _emit(None)
