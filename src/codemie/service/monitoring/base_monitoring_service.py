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
from codemie.configs.logger import logging_user_id
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


class BaseMonitoringService:
    METER_NAME = "codemie-business-metrics"

    _counters = {}

    @classmethod
    def _get_or_create_counter(cls, name: str, description: str = "", unit: str = ""):
        if name not in cls._counters:
            meter = metrics.get_meter(cls.METER_NAME)
            cls._counters[name] = meter.create_counter(name=name, description=description, unit=unit)
        return cls._counters[name]

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
            send_log_metric(name, {"count": count, **attributes})
            counter = cls._get_or_create_counter(name, description, unit)
            attributes = attributes or {}
            if not attributes.get(MetricsAttributes.USER_ID):
                attributes.update({MetricsAttributes.USER_ID: logging_user_id.get("-")})
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
