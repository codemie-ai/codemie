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

"""Ruff selects no ANN rule, so this guards the typing standard for the assistant service modules."""

import inspect
import re
from types import FunctionType, ModuleType

import pytest

from codemie.service.assistant import inline_credentials, sensitive_prompt_variables

BARE_GENERIC = re.compile(r"\b(?:list|dict)\b(?!\[)")


def _functions_defined_in(module: ModuleType) -> list[FunctionType]:
    return [fn for _, fn in inspect.getmembers(module, inspect.isfunction) if fn.__module__ == module.__name__]


FUNCTIONS = [fn for module in (inline_credentials, sensitive_prompt_variables) for fn in _functions_defined_in(module)]


@pytest.mark.parametrize("function", FUNCTIONS, ids=lambda fn: f"{fn.__module__.rsplit('.', 1)[-1]}.{fn.__name__}")
def test_every_parameter_and_the_return_are_annotated(function):
    signature = inspect.signature(function)

    unannotated = [
        name for name, parameter in signature.parameters.items() if parameter.annotation is inspect.Parameter.empty
    ]
    if signature.return_annotation is inspect.Signature.empty:
        unannotated.append("return")

    assert not unannotated


@pytest.mark.parametrize("function", FUNCTIONS, ids=lambda fn: f"{fn.__module__.rsplit('.', 1)[-1]}.{fn.__name__}")
def test_annotations_have_no_bare_list_or_dict(function):
    signature = inspect.signature(function)
    annotations = {name: parameter.annotation for name, parameter in signature.parameters.items()}
    annotations["return"] = signature.return_annotation

    bare = {name: annotation for name, annotation in annotations.items() if BARE_GENERIC.search(str(annotation))}

    assert not bare
