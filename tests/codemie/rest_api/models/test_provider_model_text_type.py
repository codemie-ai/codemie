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

"""Tests for the multiline TEXT parameter / arg type added per EPMCDME-12146.

The on-wire value is the StrEnum value, produced by ``auto()`` through
``CamelCaseStrEnum.camel_case`` — so ``TEXT`` serializes to ``"Text"``
(capital T). Pydantic's StrEnum value matching is case-sensitive, so
``"text"`` (lowercase) MUST be rejected with a ``ValidationError``.
"""

import pytest
from pydantic import ValidationError

from codemie.rest_api.models.provider import (
    ProviderToolArgument,
    ProviderToolkitConfigParameter,
)


def test_toolkit_config_parameter_accepts_text_type():
    """AC1 evidence: ``"type": "Text"`` is accepted on ProviderToolkitConfigParameter."""
    param = ProviderToolkitConfigParameter(**{"type": "Text", "description": "multiline prompt", "required": False})

    assert param.parameter_type == ProviderToolkitConfigParameter.ParameterType.TEXT
    assert param.parameter_type.value == "Text"


def test_tool_argument_accepts_text_type():
    """AC1 evidence: ``"type": "Text"`` is accepted on ProviderToolArgument."""
    arg = ProviderToolArgument(**{"type": "Text", "required": True, "description": "free text"})

    assert arg.arg_type == ProviderToolArgument.ArgType.TEXT
    assert arg.arg_type.value == "Text"


def test_toolkit_config_parameter_rejects_lowercase_text():
    """D3 guard: lowercase ``"text"`` must be rejected — StrEnum matching is case-sensitive."""
    with pytest.raises(ValidationError) as exc_info:
        ProviderToolkitConfigParameter(**{"type": "text", "description": "x", "required": False})

    assert "Text" in str(exc_info.value)


def test_tool_argument_rejects_lowercase_text():
    """D3 guard: lowercase ``"text"`` must be rejected on the arg-type enum as well."""
    with pytest.raises(ValidationError) as exc_info:
        ProviderToolArgument(**{"type": "text", "required": False})

    assert "Text" in str(exc_info.value)
