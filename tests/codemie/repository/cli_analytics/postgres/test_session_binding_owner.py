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

"""Owner check of a first binding against the legacy identity of the session (EPMCDME-16440, IR-001)."""

import pytest

from codemie.repository.cli_analytics.postgres.session_binding import SessionDiscard, check_owner


def _dims(**row):
    return {"s": row}


def test_the_known_legacy_owner_adopts_whatever_the_case():
    check_owner(["s"], _dims(owner_email=None, legacy_emails=["alice@example.com"]), "Alice@Example.com")


@pytest.mark.parametrize("sender", ["bob@example.com", ""])
def test_another_or_missing_sender_cannot_adopt_a_known_legacy_owner(sender):
    with pytest.raises(SessionDiscard) as exc:
        check_owner(["s"], _dims(owner_email=None, legacy_emails=["alice@example.com"]), sender)
    assert exc.value.reason == "session_owner_conflict"


@pytest.mark.parametrize("legacy_emails", [None, []])
def test_an_unavailable_legacy_identity_is_adopted(legacy_emails):
    check_owner(["s"], _dims(owner_email=None, legacy_emails=legacy_emails), "bob@example.com")


def test_a_bound_owner_still_wins():
    with pytest.raises(SessionDiscard):
        check_owner(["s"], _dims(owner_email="alice@example.com", legacy_emails=None), "bob@example.com")
