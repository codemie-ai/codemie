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

"""The estimated-savings metric of script runs: one tiktoken estimator, two sides, two directions.

``direction`` is the model's, not the tool's: ``model_output`` is what the model had to write (the arguments of a tool
call, the code of a script: the tokens it generates) and ``model_input`` is what it had to read (a tool's result, the
script tool's answer: the tokens it takes in). What a tool receives is the model's output and what it returns is the
model's input, so the two read the other way round from a tool's own point of view.

These are inflatable upper-bound estimates (the script chooses the calls and sizes), never to feed rankings or billing,
and never added to ``codemie_tools_usage_tokens``. Emission never raises.
"""

from __future__ import annotations

from typing import Final

from codemie.configs import logger
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import (
    SCRIPT_TOKENS_ESTIMATED_METRIC,
    MetricsAttributes,
    ScriptEstimateSide,
)
from codemie.service.script_tool_calls.context import ScriptRunContext
from codemie.service.script_tool_calls.dimensions import bridge_labels, run_dimensions, run_ok_label
from codemie.service.script_tool_calls.observer import RunSnapshot

ESTIMATOR: Final[str] = "tiktoken_v1"
DIRECTION_MODEL_OUTPUT: Final[str] = "model_output"
DIRECTION_MODEL_INPUT: Final[str] = "model_input"


def _base_attributes(snapshot: RunSnapshot, context: ScriptRunContext | None, side: str) -> dict:
    return {
        **run_dimensions(context or snapshot.attribution),
        MetricsAttributes.SIDE: side,
        MetricsAttributes.ESTIMATOR: ESTIMATOR,
    }


def _send(attributes: dict, direction: str, tokens: int) -> None:
    BaseMonitoringService.send_count_metric(
        name=SCRIPT_TOKENS_ESTIMATED_METRIC,
        count=max(tokens, 0),
        attributes={**attributes, MetricsAttributes.DIRECTION: direction},
    )


def _emit_inner_calls(snapshot: RunSnapshot, run_ok: bool) -> None:
    """One record per resolved tool and direction for the run's success-delivered calls. Never raises. Call it through
    ``emit_savings``, which applies the savings rule (private: nothing else may write half of the pair)."""
    try:
        for tool_name, (written_tokens, read_tokens) in snapshot.tool_tokens.items():
            attributes = _base_attributes(snapshot, None, ScriptEstimateSide.INNER_CALLS)
            attributes.update(run_ok_label(run_ok))
            attributes[MetricsAttributes.TOOL_NAME] = tool_name
            _send(attributes, DIRECTION_MODEL_OUTPUT, written_tokens)
            _send(attributes, DIRECTION_MODEL_INPUT, read_tokens)
    except Exception as exc:  # noqa: BLE001 - a metric must never fail the run
        logger.warning(f"Script inner-calls token estimate failed: {exc}")


def _emit_script_tool(
    snapshot: RunSnapshot, context: ScriptRunContext, *, written_tokens: int, read_tokens: int, run_ok: bool
) -> None:
    """The script tool's own cost: what the model wrote for it (its call arguments and the script code) and what it read
    back (the returned string). Never raises."""
    try:
        attributes = _base_attributes(snapshot, context, ScriptEstimateSide.SCRIPT_TOOL)
        attributes.update(bridge_labels(snapshot))
        attributes.update(run_ok_label(run_ok))
        _send(attributes, DIRECTION_MODEL_OUTPUT, written_tokens)
        _send(attributes, DIRECTION_MODEL_INPUT, read_tokens)
    except Exception as exc:  # noqa: BLE001 - a metric must never fail the run
        logger.warning(f"Script tool token estimate failed: {exc}")


def emit_savings(
    snapshot: RunSnapshot, context: ScriptRunContext, *, written_tokens: int, read_tokens: int, run_ok: bool
) -> None:
    """Both halves of the savings estimate of one run: what its inner calls would have cost the model and what the
    script tool itself cost it (``written_tokens``, ``read_tokens``). ``run_ok`` says whether the run ended without an error;
    both halves carry it as the ``run_ok`` label.

    The one entry and the one place the rule is applied: a run that no assistant's model wrote (no assistant scope, or
    a workflow tool step) gets no estimate at all, so the two halves cannot disagree about which runs count. Never
    raises.
    """
    if not context.counts_toward_savings:
        return
    _emit_inner_calls(snapshot, run_ok)
    _emit_script_tool(snapshot, context, written_tokens=written_tokens, read_tokens=read_tokens, run_ok=run_ok)
