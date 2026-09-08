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

from __future__ import annotations

import re

from codemie.configs.customer_config import customer_config


def _get_fallback_label(value: object) -> str:
    if isinstance(value, str) and value:
        return value
    return "Pure chat"


def _compile_framework_rule(item: object) -> tuple[re.Pattern[str], str] | None:
    if not isinstance(item, dict):
        return None

    regex = item.get("regex")
    label = item.get("label")
    if not isinstance(regex, str) or not regex or not isinstance(label, str) or not label:
        return None

    try:
        return re.compile(regex), label
    except re.error:
        return None


def _get_delivery_framework_config() -> tuple[list[tuple[re.Pattern[str], str]], str]:
    """Load delivery-framework classification rules from customer config."""
    raw = customer_config.get_feature_setting("cliAnalytics", "deliveryFramework", default=None)
    if not isinstance(raw, dict):
        return [], "Pure chat"

    raw_rules = raw.get("rules")
    fallback_label = _get_fallback_label(raw.get("fallbackLabel"))

    compiled_rules: list[tuple[re.Pattern[str], str]] = []
    if isinstance(raw_rules, list):
        for item in raw_rules:
            compiled_rule = _compile_framework_rule(item)
            if compiled_rule is not None:
                compiled_rules.append(compiled_rule)

    return compiled_rules, fallback_label


def get_framework_labels() -> list[str]:
    """Return the ordered list of framework labels for use as filter options."""
    rules, fallback_label = _get_delivery_framework_config()
    return [label for _, label in rules] + [fallback_label]


def classify_delivery_framework(skill_names: list[str]) -> str:
    """Return the primary delivery framework for a session based on configured regex rules.

    Priority is positional in customer config — first match wins. Returns the configured
    fallback label when no rule matches (including empty skill_names list).

    To support additional signal types in future (e.g. agent_types, file markers),
    introduce a SessionSignals dataclass carrying all inputs and update this signature.
    """
    rules, fallback_label = _get_delivery_framework_config()
    for pattern, label in rules:
        if any(pattern.match(s) for s in skill_names):
            return label
    return fallback_label
