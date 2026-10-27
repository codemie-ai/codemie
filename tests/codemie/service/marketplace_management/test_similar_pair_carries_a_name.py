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

"""The closest match must name the assistant it matched.

Found in manual testing: the review drawer's "Closest match" panel rendered

    Score 0.84 · es_knn
    ddf30756-8544-40c5-a13f-134041d5a28b
    cosine 0.84

A reviewer deciding whether a submission is a duplicate cannot act on a UUID.
The frontend was not at fault — `SimilarPair` carries only `b_id`, so a UUID is
the only thing it has to render. The name has to come from the API.
"""

from codemie.service.marketplace_management.models import SimilarPair


def test_similar_pair_can_carry_the_matched_entity_name() -> None:
    """The contract must have somewhere to put the name."""
    pair = SimilarPair(
        a_id="a-1",
        b_id="b-1",
        b_name="Puppeteer MCP Helper",
        combined=0.84,
        signals={"cosine": 0.84},
    )

    assert pair.b_name == "Puppeteer MCP Helper"


def test_b_name_is_optional_so_older_runs_still_deserialise() -> None:
    """Similarity rows persisted before this field existed must still load.

    `mm_audit_similarity` holds rows from earlier audits that never recorded a
    name. They have to keep deserialising, or the Cleanup table breaks for
    every historical run.
    """
    pair = SimilarPair(a_id="a-1", b_id="b-1", combined=0.5)

    assert pair.b_name is None
