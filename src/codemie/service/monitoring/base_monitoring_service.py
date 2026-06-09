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
from typing import Optional

from opentelemetry import metrics

from codemie.configs import logger, config
from codemie.configs.logger import current_user_email, logging_user_id
from codemie.service.monitoring.metrics_constants import MetricsAttributes


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


def send_log_metric(name: str, attributes: dict):
    # Ensure cached_tokens_money_spent is serialized as float, not integer
    # This prevents Elasticsearch from incorrectly mapping it as 'long' instead of 'double'
    if "cached_tokens_money_spent" in attributes and attributes["cached_tokens_money_spent"] is not None:
        attributes["cached_tokens_money_spent"] = float(attributes["cached_tokens_money_spent"])

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
    send_log_metric(
        name=name,
        attributes={
            **base_attributes,
            MetricsAttributes.USER_EMAIL: current_user_email.get("-"),
            **(
                {
                    MetricsAttributes.INPUT_TOKENS: tokens.input_tokens,
                    MetricsAttributes.OUTPUT_TOKENS: tokens.output_tokens,
                    MetricsAttributes.CACHE_READ_INPUT_TOKENS: tokens.cached_tokens,
                    MetricsAttributes.MONEY_SPENT: tokens.money_spent,
                    MetricsAttributes.CACHED_TOKENS_MONEY_SPENT: tokens.cached_tokens_money_spent,
                    MetricsAttributes.CACHE_CREATION_TOKENS_MONEY_SPENT: tokens.cached_tokens_creation_money_spent,
                }
                if tokens
                else {}
            ),
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
            cls._histograms[name] = meter.create_histogram(name=name, description=description, unit=unit)
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
    ):
        try:
            attributes = attributes or {}
            if not attributes.get(MetricsAttributes.USER_ID):
                attributes.update({MetricsAttributes.USER_ID: logging_user_id.get("-")})
            send_log_metric(name, {"count": count, **attributes})
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
    ):
        """Record operation duration as an OTel Histogram (exposed as Prometheus histogram)."""
        try:
            histogram = cls._get_or_create_histogram(name, description)
            attributes = attributes or {}
            if not attributes.get(MetricsAttributes.USER_ID):
                attributes.update({MetricsAttributes.USER_ID: logging_user_id.get("-")})
            attributes.update({"env": config.ENV})
            attributes = {k: limit_string(v) if isinstance(v, str) else v for k, v in attributes.items()}
            histogram.record(duration_seconds, attributes)
        except Exception as e:
            logger.error(f"Error recording duration metric: {str(e)}")
