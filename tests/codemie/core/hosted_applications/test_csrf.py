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

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.rest_api.security.hosted_applications import guard_mutation, require_verified_context

SETTINGS = SimpleNamespace(ui_origins=("https://codemie.example.com",))


def request(headers, method="POST"):
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/",
            "headers": [(name.lower().encode(), value.encode()) for name, value in headers],
            "state": {},
        }
    )


def browser(**changes):
    headers = {
        "Cookie": "auth=test",
        "Origin": "https://codemie.example.com",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Content-Type": "application/json",
    }
    for key, value in changes.items():
        if value is None:
            headers.pop(key, None)
        else:
            headers[key] = value
    return request(list(headers.items()))


def test_exact_ui_origin_cookie_mutation():
    guard_mutation(browser(), SETTINGS)


@pytest.mark.parametrize(
    "changes",
    [
        {"Origin": "https://v-" + "a" * 32 + ".content.net"},
        {"Origin": "https://codemie.example.com.attacker.net"},
        {"Origin": None},
        {"Sec-Fetch-Site": None},
        {"Sec-Fetch-Mode": "navigate"},
        {"Content-Type": "text/plain"},
        {"Content-Type": "application/json; charset=shift-jis"},
    ],
)
def test_cookie_csrf_guards_fail_closed(changes):
    with pytest.raises(HostedApplicationError) as error:
        guard_mutation(browser(**changes), SETTINGS)
    assert error.value.status in (403, 415)


def test_bearer_only_cli_can_omit_browser_headers():
    guard_mutation(request([("Authorization", "Bearer signed-token"), ("Content-Type", "application/json")]), SETTINGS)


def test_cookie_plus_bearer_still_requires_cookie_csrf_guard():
    with pytest.raises(HostedApplicationError):
        guard_mutation(
            request(
                [
                    ("Cookie", "auth=test"),
                    ("Authorization", "Bearer signed-token"),
                    ("Content-Type", "application/json"),
                ]
            ),
            SETTINGS,
        )


def test_duplicate_origins_are_rejected():
    req = browser()
    req.scope["headers"].append((b"origin", b"https://attacker.net"))
    with pytest.raises(HostedApplicationError):
        guard_mutation(req, SETTINGS)


def test_coalesced_user_alone_cannot_supply_verified_identity():
    with pytest.raises(HostedApplicationError) as error:
        require_verified_context(request([]), SimpleNamespace(id="user", user_type="regular"))
    # 403, never 401: the UI treats a 401 as an expired session and would loop through sign-in.
    assert (error.value.code, error.value.status) == ("ACCOUNT_NOT_SUPPORTED", 403)
