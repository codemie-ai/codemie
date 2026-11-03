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

"""Tests for the analytics response models' extra-key passthrough.

A money metric carries its spend annotation in the same dict as its figure. Pydantic's default is to
**drop** unknown keys, and a dropped annotation puts a qualified figure on the wire looking exactly
like an exact one - so the passthrough is behaviour worth asserting, not configuration trivia.
"""

from __future__ import annotations

from codemie.rest_api.models.analytics import Metric, SummariesResponse

_METADATA = {
    "timestamp": "2026-03-01T00:00:00+00:00",
    "data_as_of": "2026-03-01T00:00:00+00:00",
    "filters_applied": {},
    "execution_time_ms": 1.0,
}


def _summaries(metrics: list[dict]) -> dict:
    return SummariesResponse(data={"metrics": metrics}, metadata=_METADATA).model_dump(by_alias=True)


def test_spend_annotation_survives_validation_and_dump():
    """The three companion keys must reach the wire beside the figure they qualify."""
    metric = {
        "id": "current_spending",
        "label": "Current Spending",
        "type": "number",
        "value": 7.25,
        "description": "",
        "format": "currency",
        "spend_unavailable": False,
        "spend_partial": True,
        "spend_missing_amount": 2.0,
    }

    dumped = _summaries([metric])["data"]["metrics"][0]

    assert dumped["value"] == 7.25
    assert dumped["spend_unavailable"] is False
    assert dumped["spend_partial"] is True
    assert dumped["spend_missing_amount"] == 2.0


def test_absent_figure_keeps_its_none_rather_than_becoming_a_zero():
    """`value: None` is the no-figure state and must not be coerced into a number."""
    metric = {
        "id": "cli_current_spending",
        "label": "CLI Spending",
        "type": "number",
        "value": None,
        "description": "",
        "format": "currency",
        "spend_unavailable": True,
        "spend_partial": False,
        "spend_missing_amount": None,
    }

    dumped = _summaries([metric])["data"]["metrics"][0]

    assert dumped["value"] is None
    assert dumped["spend_unavailable"] is True


def test_a_metric_with_no_annotation_emits_exactly_the_declared_fields():
    """Every other analytics metric is unchanged: the passthrough adds nothing where nothing was set."""
    metric = {"id": "budget_limit", "label": "Budget Limit", "type": "number", "value": 100.0, "description": ""}

    dumped = _summaries([metric])["data"]["metrics"][0]

    assert set(dumped) == set(Metric.model_fields)
