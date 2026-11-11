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

from types import SimpleNamespace

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, select

from codemie.core.search_ranking import rank_by_search, search_ranking


class TestSearchRanking:
    @pytest.fixture
    def items(self):
        engine = create_engine("sqlite://")
        table = Table(
            "items",
            MetaData(),
            Column("id", Integer, primary_key=True),
            Column("name", String),
            Column("description", String),
        )
        table.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(
                table.insert(),
                [
                    {"id": 1, "name": "Report helper", "description": "Builds charts"},
                    {"id": 2, "name": "Other", "description": "Writes a REPORT"},
                    {"id": 3, "name": "Unrelated", "description": "Nothing here"},
                    {"id": 4, "name": "report_bot", "description": None},
                    {"id": 5, "name": "Reportage", "description": "report again"},
                ],
            )
        yield engine, table
        engine.dispose()

    def _search(self, items, text):
        engine, table = items
        condition, rank = search_ranking(text, table.c.name, table.c.description)
        query = select(table.c.id).where(condition).order_by(rank, table.c.id)
        with engine.connect() as conn:
            return [row.id for row in conn.execute(query)]

    def test_name_matches_come_before_description_only_matches(self, items):
        assert self._search(items, "report") == [1, 4, 5, 2]

    def test_non_matching_rows_are_excluded(self, items):
        assert 3 not in self._search(items, "report")

    def test_like_wildcards_match_literally(self, items):
        assert self._search(items, "_") == [4]


class TestRankBySearch:
    def test_name_matches_come_before_description_only_matches(self):
        desc_first = SimpleNamespace(name="Other", description="about REPORTS")
        name_first = SimpleNamespace(name="Report builder", description="builds things")
        name_second = SimpleNamespace(name="Weekly report", description=None)
        desc_second = SimpleNamespace(name="Another", description="a report")

        result = rank_by_search([desc_first, name_first, name_second, desc_second], "report")

        assert result == [name_first, name_second, desc_first, desc_second]

    def test_non_matching_items_are_excluded(self):
        miss = SimpleNamespace(name="Unrelated", description="nothing")
        hit = SimpleNamespace(name="Report", description=None)

        assert rank_by_search([miss, hit], "report") == [hit]

    def test_missing_name_and_description_are_tolerated(self):
        assert rank_by_search([SimpleNamespace(name=None, description=None)], "report") == []
