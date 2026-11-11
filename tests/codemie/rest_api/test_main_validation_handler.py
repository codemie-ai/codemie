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

"""A body-validation failure echoing a lone surrogate still answers 422 with a JSON body."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.main import _sanitize_error, validation_exception_handler

SURROGATE = "\ud800"


def _surrogate_attribute_errors() -> list[dict]:
    with pytest.raises(ValidationError) as exc_info:
        AssistantChatRequest(text="hi", attributes={"team": SURROGATE})
    return exc_info.value.errors()


def _request() -> MagicMock:
    request = MagicMock()
    request.method = "POST"
    request.url.path = "/v1/assistants/a/model"
    return request


@pytest.mark.asyncio
async def test_lone_surrogate_attribute_value_answers_422_json():
    errors = [{**error, "loc": ("body", *error["loc"])} for error in _surrogate_attribute_errors()]

    response = await validation_exception_handler(_request(), RequestValidationError(errors))

    body = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 422
    assert SURROGATE not in response.body.decode("utf-8")
    details = body["error"]["details"]
    assert details[0]["input"] == {"team": "\\ud800"}
    assert all(isinstance(detail["loc"], list) for detail in details)
    assert isinstance(details[0]["ctx"]["error"], str)


def test_sanitize_error_escapes_surrogates_in_strings_and_keys_and_stringifies_ctx():
    error = {
        "loc": ("body", "attributes", SURROGATE),
        "msg": f"bad {SURROGATE}",
        "input": {SURROGATE: SURROGATE},
        "ctx": {"error": ValueError("boom"), "limit": 3},
    }

    sanitized = _sanitize_error(error)

    assert sanitized["loc"] == ["body", "attributes", "\\ud800"]
    assert sanitized["msg"] == "bad \\ud800"
    assert sanitized["input"] == {"\\ud800": "\\ud800"}
    assert sanitized["ctx"] == {"error": "boom", "limit": 3}
    json.dumps(sanitized).encode("utf-8")
