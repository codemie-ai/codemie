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

"""The read path that lifts a stored money figure onto a returned message.

Carrying the number without its two flags hands a caller a settled subtotal indistinguishable from a
final cost - the same producer/consumer gap, one layer out.
"""

from codemie.core.models import TokensUsage
from codemie.rest_api.models.conversation import GeneratedMessage


def a_message(**fields) -> GeneratedMessage:
    return GeneratedMessage(role="Assistant", message="x", **fields)


class TestTheMessageModelCanCarryTheFlags:
    """Without the fields the read path cannot carry them, and the gap merely moves."""

    def test_both_flags_are_accepted_alongside_the_figure(self):
        message = a_message(money_spent=0.0, money_spent_pending=True, money_spent_unavailable=False)
        assert message.money_spent == 0.0
        assert message.money_spent_pending is True
        assert message.money_spent_unavailable is False

    def test_both_default_to_absent_so_existing_producers_are_unchanged(self):
        """Additive: a caller that never sets them keeps meaning exactly what it meant before."""
        message = a_message()
        assert message.money_spent_pending is None
        assert message.money_spent_unavailable is None


class TestTheReadPathCopiesThemFromTheStoredUsage:
    def test_a_pending_execution_yields_a_pending_message(self):
        stored = TokensUsage(input_tokens=1, output_tokens=2, money_spent=0.0, money_spent_pending=True)
        message = a_message(
            money_spent=stored.money_spent,
            money_spent_pending=stored.money_spent_pending,
            money_spent_unavailable=stored.money_spent_unavailable,
        )
        assert message.money_spent_pending is True
        assert message.money_spent_unavailable is False
        assert message.money_spent == 0.0, "the settled subtotal still travels; the flag qualifies it"

    def test_a_settled_execution_yields_both_flags_false(self):
        stored = TokensUsage(input_tokens=1, output_tokens=2, money_spent=3.5)
        message = a_message(
            money_spent=stored.money_spent,
            money_spent_pending=stored.money_spent_pending,
            money_spent_unavailable=stored.money_spent_unavailable,
        )
        assert message.money_spent == 3.5
        assert message.money_spent_pending is False
        assert message.money_spent_unavailable is False

    def test_no_stored_usage_yields_no_figure_and_no_flags(self):
        """Absent usage is absent, not zero and not settled."""
        message = a_message(money_spent=None, money_spent_pending=None, money_spent_unavailable=None)
        assert message.money_spent is None
        assert message.money_spent_pending is None
