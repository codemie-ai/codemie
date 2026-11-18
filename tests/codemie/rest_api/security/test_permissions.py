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

"""Tests for the shared role predicates in permissions.py (EPMCDME-17380)."""

from types import SimpleNamespace

import pytest

from codemie.rest_api.security.permissions import is_admin_maintainer_or_auditor, is_admin_or_maintainer


def _user(**flags):
    return SimpleNamespace(**{"is_admin": False, "is_maintainer": False, "is_auditor": False, **flags})


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ({}, False),
        ({"is_admin": True}, True),
        ({"is_maintainer": True}, True),
        ({"is_auditor": True}, True),
        ({"is_admin": True, "is_auditor": True}, True),
    ],
)
def test_is_admin_maintainer_or_auditor(flags, expected):
    assert is_admin_maintainer_or_auditor(_user(**flags)) is expected


def test_auditor_is_not_admin_or_maintainer():
    assert is_admin_or_maintainer(_user(is_auditor=True)) is False


def test_non_bool_auditor_flag_is_ignored():
    assert is_admin_maintainer_or_auditor(_user(is_auditor="yes")) is False


def test_user_without_role_attributes_is_denied():
    assert is_admin_maintainer_or_auditor(SimpleNamespace()) is False
