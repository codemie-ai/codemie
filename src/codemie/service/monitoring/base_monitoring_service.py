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
from datetime import datetime
from typing import Any, Optional

from opentelemetry import metrics

from codemie.configs import logger, config
from codemie.configs.logger import current_username, logging_user_id
from codemie.configs.script_call_log_guard import allow_metric_record
from codemie.service.monitoring.metrics_constants import MetricsAttributes

#: Histogram bucket boundaries in seconds (EPMCDME-15537). `record_duration_metric` records seconds,
#: while the OTel default boundaries (0, 5, 10, 25, ...) are scaled for milliseconds and would put
#: every sample under 5 s into one bucket, leaving no percentile readable.
DURATION_BUCKET_BOUNDARIES_SECONDS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)


def limit_string(input_string, max_length: int = 500):
    """
    Limit string length to prevent exceeding metric storage constraints.

    GCP doesn't allow values that exceed the maximum string size of 1024 characters.
    Default limit is 500 characters, but can be customized via max_length parameter.

    Args:
        input_string: String to limit
        max_length: Maximum allowed length (default: 500)

    Returns:
        Truncated string or original if None
    """
    if input_string:
        return input_string[:max_length]
    return input_string


def _add_budget_fallback_attribute(attributes: dict) -> None:
    """Flag metrics of requests whose project budget fell back to personal/default (EPMCDME-15111 AC6)."""
    from codemie.core.dependecies import get_budget_fallback_from

    fallback_from = get_budget_fallback_from()
    if fallback_from:
        attributes.setdefault(MetricsAttributes.BUDGET_FALLBACK_FROM, fallback_from)


def send_log_metric(name: str, attributes: dict):
    _add_budget_fallback_attribute(attributes)
    # Ensure cached_tokens_money_spent is serialized as float, not integer
    # This prevents Elasticsearch from incorrectly mapping it as 'long' instead of 'double'
    if "cached_tokens_money_spent" in attributes and attributes["cached_tokens_money_spent"] is not None:
        attributes["cached_tokens_money_spent"] = float(attributes["cached_tokens_money_spent"])

    # A metric a tool emits while it runs for a workspace script must still reach the log (see script_call_log_guard).
    with allow_metric_record():
        logger.info(json.dumps({"metric_name": name, "attributes": attributes, "time": datetime.now().isoformat()}))


def emit_llm_token_metric(name: str, request_id: Optional[str], base_attributes: dict) -> None:
    """Emit a metric enriched with LLM token usage from RequestSummaryManager.

    Reads the token summary for request_id (if provided) and merges token fields
    into base_attributes before emitting. Does NOT clear the summary — callers
    are responsible for calling clear_summary in a finally block.
    """
    from codemie.service.request_summary_manager import request_summary_manager

    summary = request_summary_manager.get_summary(request_id) if request_id else None
    tokens = summary.tokens_usage if summary else None
    token_attributes: dict = {}
    if tokens:
        token_attributes = {
            MetricsAttributes.INPUT_TOKENS: tokens.input_tokens,
            MetricsAttributes.OUTPUT_TOKENS: tokens.output_tokens,
            MetricsAttributes.CACHE_READ_INPUT_TOKENS: tokens.cached_tokens,
            MetricsAttributes.CACHED_TOKENS_MONEY_SPENT: tokens.cached_tokens_money_spent,
            MetricsAttributes.CACHE_CREATION_TOKENS_MONEY_SPENT: tokens.cached_tokens_creation_money_spent,
        }
        # AD-28: while cost is unsettled `money_spent` is a truthful **subtotal**, not a total - its
        # own field comment says so - and nothing re-emits this document once the ledger settles, so
        # emitting it here freezes a partial (a zero, where nothing settled) into metric history
        # uncorrectably. The four sibling emitters in this package are gated on exactly this pair.
        #
        # **Why it matters beyond the dashboard, which is what makes this worth the deviation below:**
        # `DefaultSpendLedger` sums this very attribute as money (`default_ledger.MONEY_FIELD`), so an
        # ungated subtotal is not merely displayed short - it is read back *through the spend port* as
        # though it were the whole cost. Token counts are unaffected; they are final here.
        #
        # `cached_tokens_money_spent` and `cache_creation_tokens_money_spent` stay unconditional: this
        # is their only emitter and dropping them would break historical Elasticsearch mapping, which
        # is what `inventory.md`'s row for these lines exists to protect.
        if not (tokens.money_spent_pending or tokens.money_spent_unavailable):
            token_attributes[MetricsAttributes.MONEY_SPENT] = tokens.money_spent
    send_log_metric(
        name=name,
        attributes={
            **base_attributes,
            MetricsAttributes.USER_EMAIL: current_username.get("-"),
            **token_attributes,
        },
    )


class BaseMonitoringService:
    METER_NAME = "codemie-business-metrics"

    _counters = {}
    _histograms = {}
    _updown_counters = {}

    @classmethod
    def _get_or_create_counter(cls, name: str, description: str = "", unit: str = ""):
        if name not in cls._counters:
            meter = metrics.get_meter(cls.METER_NAME)
            cls._counters[name] = meter.create_counter(name=name, description=description, unit=unit)
        return cls._counters[name]

    @classmethod
    def _get_or_create_histogram(cls, name: str, description: str = "", unit: str = "s"):
        if name not in cls._histograms:
            meter = metrics.get_meter(cls.METER_NAME)
            cls._histograms[name] = meter.create_histogram(
                name=name,
                description=description,
                unit=unit,
                explicit_bucket_boundaries_advisory=DURATION_BUCKET_BOUNDARIES_SECONDS,
            )
        return cls._histograms[name]

    @classmethod
    def _get_or_create_updown_counter(cls, name: str, description: str = "", unit: str = ""):
        if name not in cls._updown_counters:
            meter = metrics.get_meter(cls.METER_NAME)
            cls._updown_counters[name] = meter.create_up_down_counter(name=name, description=description, unit=unit)
        return cls._updown_counters[name]

    @classmethod
    def send_count_metric(
        cls,
        name: str,
        description: str = "",
        unit: str = "",
        attributes: Optional[dict] = None,
        count: int = 1,
        extra_log_fields: dict[str, Any] | None = None,
    ):
        try:
            attributes = attributes or {}
            _add_budget_fallback_attribute(attributes)
            if not attributes.get(MetricsAttributes.USER_ID):
                attributes.update({MetricsAttributes.USER_ID: logging_user_id.get("-")})
            send_log_metric(name, {"count": count, **attributes, **(extra_log_fields or {})})
            counter = cls._get_or_create_counter(name, description, unit)
            attributes.update(
                {
                    "env": config.ENV,
                }
            )
            # Apply truncation to all string values in the attributes dictionary
            attributes = {k: limit_string(v) if isinstance(v, str) else v for k, v in attributes.items()}
            return counter.add(count, attributes)
        except Exception as e:
            logger.error(f"Error sending count metric: {str(e)}")

    @classmethod
    def send_updown_metric(
        cls,
        name: str,
        amount: int = 1,
        description: str = "",
        unit: str = "",
        attributes: Optional[dict] = None,
    ):
        try:
            counter = cls._get_or_create_updown_counter(name, description, unit)
            attributes = attributes or {}
            attributes.update({"env": config.ENV})
            attributes = {k: limit_string(v) if isinstance(v, str) else v for k, v in attributes.items()}
            counter.add(amount, attributes)
        except Exception as e:
            logger.error(f"Error sending updown metric: {str(e)}")

    @classmethod
    def record_duration_metric(
        cls,
        name: str,
        duration_seconds: float,
        description: str = "",
        attributes: Optional[dict] = None,
        *,
        per_user: bool = True,
    ):
        """Record operation duration as an OTel Histogram (exposed as Prometheus histogram).

        `per_user=False` leaves out the calling user: an infrastructure duration (EPMCDME-15537) is
        not about who asked, and a user attribute would multiply its series by every user.
        """
        try:
            histogram = cls._get_or_create_histogram(name, description)
            attributes = attributes or {}
            _add_budget_fallback_attribute(attributes)
            if per_user and not attributes.get(MetricsAttributes.USER_ID):
                attributes.update({MetricsAttributes.USER_ID: logging_user_id.get("-")})
            attributes.update({"env": config.ENV})
            attributes = {k: limit_string(v) if isinstance(v, str) else v for k, v in attributes.items()}
            histogram.record(duration_seconds, attributes)
        except Exception as e:
            logger.error(f"Error recording duration metric: {str(e)}")
