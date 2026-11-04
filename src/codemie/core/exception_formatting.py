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

"""Formatting of exceptions for log messages."""

from __future__ import annotations


def describe_exception(exc: BaseException) -> str:
    """Describe an exception for logs: its type, then code/message/details when set, then str(exc) when non-empty.

    ExtendedHTTPException subclasses do not pass their message to Exception, so str(exc) alone
    can be empty or uninformative.
    """
    parts = [type(exc).__name__]
    for attr in ("code", "message", "details"):
        value = getattr(exc, attr, None)
        if value is not None:
            parts.append(f"{attr}={value}")
    text = str(exc)
    if text:
        parts.append(text)
    return " ".join(parts)
