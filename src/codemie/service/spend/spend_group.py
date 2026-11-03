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

"""The four coarse groups the summary cards split money into, and how each is selected.

**Not a fifth traffic class, and not four partitions.** The summary screen has always shown one total and
three cards cut from it, and two of the cards overlap: *platform* is everything that is not CLI - which
includes the embedding traffic, because the metric-name group it was always read from includes the
datasource metric (owner, Q2) - and *embedding* is a slice of it. So `TOTAL = PLATFORM + CLI` and
`EMBEDDING ⊂ PLATFORM`; a consumer that adds the four up has counted the embeddings twice.

**Owned by the port for the reason every closed selector table here is.** The alternative -
`platform = total − cli` computed in the handler from two port reads - subtracts two independently
floored figures, which AD-29 forbids: each carries its own flag and portion, and the difference is a
number with nobody's qualification. One scan answers all four, once floored, so the cards on one screen
foot against each other by construction.

**Platform is the CLI class's complement, on both of the columns that say what CLI traffic is, and
NULL-safe on each.** The NULL-safety argument is the original one and it now has two columns to hold
for: an unattributed row carries a NULL category and a pre-rollout row carries a NULL origin, and
`NULL <> 'cli'` is *unknown* on either - a `WHERE` never admits it, so a bare inequality on either
column would quietly omit exactly the money the total still counts. Written as `cli_complement`
rather than as a second opinion about the category, because `TOTAL = PLATFORM + CLI` holds only while
every row is on exactly one side: a CLI session billed to a premium budget carries
`codemie_origin = 'cli'` beside `attr_category = 'premium_models'`, and a platform card keyed on the
category alone would count that row in both cards.

**Below the coverage floor, each group is the summary card's own Elasticsearch aggregation reproduced
verbatim** - the total is every document's money, platform the platform metric names, CLI the proxy metric
with the CLI flag, embedding the datasource metric - so a pre-cutover window shows today's figures to the
cent. That side does not foot as a partition and never did; the ledger side does.
"""

from __future__ import annotations

from enum import Enum

from sqlalchemy import ColumnElement

from codemie.service.spend.traffic_class import TrafficClass, cli_complement, traffic_class_predicate

__all__ = ["SpendGroup", "spend_group_predicate"]


class SpendGroup(str, Enum):
    """The four summary figures. `TOTAL = PLATFORM + CLI`; `EMBEDDING` is a slice of `PLATFORM`."""

    TOTAL = "total"
    PLATFORM = "platform"
    CLI = "cli"
    EMBEDDING = "embedding"


def spend_group_predicate(group: SpendGroup) -> ColumnElement[bool] | None:
    """What selects one group's rows, or `None` for the total, which selects them all.

    CLI and embedding are the traffic classes' own selectors, so the card and the chart beside it
    cannot come to mean different things by "CLI" or by "embedding".
    """
    match group:
        case SpendGroup.TOTAL:
            return None
        case SpendGroup.PLATFORM:
            return cli_complement()
        case SpendGroup.CLI:
            return traffic_class_predicate(TrafficClass.CLI)
        case SpendGroup.EMBEDDING:
            return traffic_class_predicate(TrafficClass.DATASOURCE)
    raise TypeError(f"no predicate is defined for spend group {group!r}")
