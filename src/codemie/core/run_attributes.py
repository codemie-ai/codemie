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

import json
import re
import unicodedata
from typing import Any

from codemie.configs import config

MAX_KEY_LEN = 64
MAX_VALUE_LEN = 128
ES_IGNORE_ABOVE = 256
KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}[a-z0-9]$")
RESERVED_PREFIXES = ("codemie.", "otel.")
PAIR_SEPARATOR = "="
KEYS_RECORD_FIELD = "run_attribute_keys"
PAIRS_RECORD_FIELD = "run_attribute_pairs"
KEYS_FIELD = f"attributes.{KEYS_RECORD_FIELD}.keyword"
PAIRS_FIELD = f"attributes.{PAIRS_RECORD_FIELD}.keyword"

assert MAX_KEY_LEN + 1 + MAX_VALUE_LEN < ES_IGNORE_ABOVE

_RUN_ATTRIBUTES_INPUT_DESC = (
    "Analytics labels for this run: an object of string keys and string values, for example "
    '{"com.epam.gen_ai.service.work_item.id": "ABC-123"}. '
    f"At most {config.RUN_ATTRIBUTES_MAX_KEYS} keys. A key is 2-{MAX_KEY_LEN} lowercase letters, digits, '.', '_' "
    f"or '-', starts and ends with a letter or digit, and must not start with {' or '.join(RESERVED_PREFIXES)}. "
    f"A value is 1-{MAX_VALUE_LEN} characters (UTF-16 code units) without control characters or unpaired "
    "surrogates. Invalid attributes return 422 and nothing runs. "
)
_RUN_ATTRIBUTES_USAGE_DESC = (
    "They are never passed to the assistant, its tools or the workflow (use metadata, prompt_variables or "
    "user_input for that; workflow tags only label Langfuse traces). "
    "They are stored with the run, written to usage metrics and application logs, and shown to every user who can "
    "see the run or its usage analytics, so they must not contain secrets or personal data. Analytics totals grouped "
    "or filtered by attributes rely on the values callers declare; they are not verified."
)
RUN_ATTRIBUTES_REQUEST_DESC = (
    _RUN_ATTRIBUTES_INPUT_DESC
    + "Attributes are fixed when the conversation starts: later messages of the conversation keep them and "
    "sending different ones returns 422. Ignored when save_history is false: such chats store no conversation and "
    "write no usage record. " + _RUN_ATTRIBUTES_USAGE_DESC
)
WORKFLOW_RUN_ATTRIBUTES_REQUEST_DESC = (
    _RUN_ATTRIBUTES_INPUT_DESC
    + "Attributes are fixed for this execution: a resume keeps them and sub-workflows inherit them. Each execution "
    "takes only the attributes of its own request, so executions that continue the same conversation_id neither "
    "inherit nor are checked against the attributes of earlier executions. " + _RUN_ATTRIBUTES_USAGE_DESC
)
RUN_ATTRIBUTES_RESPONSE_DESC = "Run attributes set when the run started, or null if none. They cannot be changed."
CONVERSATION_RUN_ATTRIBUTES_RESPONSE_DESC = (
    "Run attributes set when the conversation started, or null if none. They cannot be changed. Always null for "
    "workflow conversations: workflow run attributes are stored on each workflow execution."
)


class RunAttributesError(Exception):
    pass


def validate_attribute_key(key: str) -> str:
    if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
        raise RunAttributesError(
            f"Invalid attribute key {key!r}: must be 2-{MAX_KEY_LEN} characters of lowercase letters, digits, "
            "'.', '_' or '-', starting and ending with a letter or digit"
        )
    if key.startswith(RESERVED_PREFIXES):
        raise RunAttributesError(f"Attribute key {key!r} uses a reserved prefix: {', '.join(RESERVED_PREFIXES)}")
    return key


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def _validate_attribute_value(key: str, value: str) -> str:
    if not isinstance(value, str):
        raise RunAttributesError(f"Attribute {key!r} value must be a string")
    if not 1 <= _utf16_length(value) <= MAX_VALUE_LEN:
        raise RunAttributesError(f"Attribute {key!r} value must be 1-{MAX_VALUE_LEN} UTF-16 code units long")
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise RunAttributesError(f"Attribute {key!r} value must not contain control characters")
    if any(unicodedata.category(char) == "Cs" for char in value):
        raise RunAttributesError(f"Attribute {key!r} value must not contain unpaired surrogates")
    return value


def validate_run_attributes(attrs: dict[str, str] | None) -> dict[str, str] | None:
    if not attrs:
        return None
    if not isinstance(attrs, dict):
        raise RunAttributesError("Attributes must be an object of string keys and string values")
    max_keys = config.RUN_ATTRIBUTES_MAX_KEYS
    if len(attrs) > max_keys:
        raise RunAttributesError(f"Too many attributes: at most {max_keys} are allowed")
    return {validate_attribute_key(key): _validate_attribute_value(key, value) for key, value in attrs.items()}


def parse_attributes_filter(raw: str | None) -> dict[str, str] | None:
    if not raw or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise RunAttributesError("Attributes filter must be a valid JSON object") from exc
    if not isinstance(parsed, dict):
        raise RunAttributesError("Attributes filter must be a JSON object of string keys and string values")
    return validate_run_attributes(parsed)


def pair_prefix(key: str) -> str:
    return f"{key}{PAIR_SEPARATOR}"


def encode_pair(key: str, value: str) -> str:
    return f"{pair_prefix(key)}{value}"


def build_record_fields(attrs: dict[str, str] | None) -> dict[str, list[str]]:
    if not attrs:
        return {}
    return {
        KEYS_RECORD_FIELD: sorted(attrs),
        PAIRS_RECORD_FIELD: sorted(encode_pair(key, value) for key, value in attrs.items()),
    }


def attributes_from_pairs(pairs: list[str] | None) -> dict[str, str] | None:
    if not isinstance(pairs, list):
        return None
    attrs = dict(pair.split(PAIR_SEPARATOR, 1) for pair in pairs if isinstance(pair, str) and PAIR_SEPARATOR in pair)
    return attrs or None


def attributes_from_record(record: dict[str, Any]) -> dict[str, str] | None:
    return attributes_from_pairs(record.get(PAIRS_RECORD_FIELD))
