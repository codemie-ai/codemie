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

"""`gate_hits.critical` counts assistants, because that is what it is labelled.

Found by verifying the Overview against the database. On the reference corpus
the tile read 55 while only 45 assistants failed a critical check: 7 assistants
fail more than one critical rule, and each of their findings was counted
separately.

The tile is labelled "Failing a critical check" and sits in a row beside
"Pending review" and "Assistants in marketplace" — every neighbour is a count of
assistants. `per_rule` already counts distinct assistants per rule, so the
summary contradicted the table underneath it.
"""

from codemie.rest_api.routers.marketplace_management import _gate_hits


def test_an_assistant_failing_two_critical_rules_is_counted_once() -> None:
    """The headline is a count of assistants, not of findings."""
    # a-1 fails two critical rules; a-2 fails one. Two assistants, three findings.
    result = _gate_hits({"name_length": ["a-1"], "description_length": ["a-1", "a-2"]})

    assert result["critical"] == 2, (
        f"two assistants fail a critical check, but the headline said "
        f"{result['critical']} — it counted findings, and a-1 fails two rules"
    )


def test_per_rule_still_counts_assistants_per_rule() -> None:
    """The table underneath keeps its own semantics."""
    result = _gate_hits({"name_length": ["a-1"], "description_length": ["a-1", "a-2"]})

    per_rule = {row["rule"]: row["count"] for row in result["per_rule"]}
    assert per_rule["description_length"] == 2
    assert per_rule["name_length"] == 1


def test_critical_and_minor_are_counted_independently() -> None:
    """One assistant can be in both totals; neither absorbs the other."""
    result = _gate_hits({"name_length": ["a-1"], "description_not_equal_name": ["a-1"]})

    assert result["critical"] == 1
    assert result["optional"] == 1


def test_the_summary_never_exceeds_the_audited_population() -> None:
    """The invariant the old sum could break: it reported 55 of 219 audited
    when only 45 assistants were affected. A headline larger than the number
    of distinct assistants in the input is definitionally wrong."""
    per_rule = {
        "name_length": ["a-1", "a-2"],
        "description_length": ["a-1", "a-2"],
        "substance": ["a-1"],
    }
    distinct = {i for ids in per_rule.values() for i in ids}

    result = _gate_hits(per_rule)

    assert result["critical"] <= len(distinct)
    assert result["critical"] == 2
