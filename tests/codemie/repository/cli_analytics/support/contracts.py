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

"""Structural checks that an adapter class implements a storage port exactly."""

from __future__ import annotations

import inspect


def port_methods(port: type) -> list[str]:
    """Names of the async methods a Protocol declares, in declaration order."""
    return [name for name, member in vars(port).items() if inspect.iscoroutinefunction(member)]


def _shape(func) -> list[tuple[str, object, object]]:
    return [(p.name, p.kind, p.default) for p in inspect.signature(func).parameters.values()]


def assert_implements_port(impl: type, port: type) -> None:
    """Every port method exists on `impl` as a coroutine with the same parameters."""
    for name in port_methods(port):
        method = getattr(impl, name, None)
        assert method is not None, f"{impl.__name__} lacks {name}"
        assert inspect.iscoroutinefunction(method), f"{impl.__name__}.{name} is not async"
        assert _shape(method) == _shape(getattr(port, name)), f"{impl.__name__}.{name} signature differs from the port"
