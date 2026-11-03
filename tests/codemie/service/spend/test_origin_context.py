# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""`spend_origin`: the innermost published origin wins while it runs, and never outlives its block."""

import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor

import pytest

from codemie.service.spend.origin_context import current_origin, publish_origin, reset_origin, spend_origin
from codemie.service.spend.traffic_class import TrafficOrigin


class TestTheBlockScopesTheOrigin:
    def test_nothing_is_published_by_default(self):
        assert current_origin() is None

    def test_the_origin_is_visible_inside_the_block_and_gone_after_it(self):
        with spend_origin(TrafficOrigin.CONVERSATION):
            assert current_origin() is TrafficOrigin.CONVERSATION
        assert current_origin() is None

    def test_a_nested_block_wins_while_it_runs_and_hands_the_parent_back(self):
        """A tool-owned model inside a conversation, a sub-workflow inside a workflow: the child's
        origin must not stamp the parent's later calls, which an overwrite would."""
        with spend_origin(TrafficOrigin.CONVERSATION):
            with spend_origin(TrafficOrigin.TOOLS):
                assert current_origin() is TrafficOrigin.TOOLS
            assert current_origin() is TrafficOrigin.CONVERSATION

    def test_an_exception_on_the_way_out_still_restores_the_enclosing_origin(self):
        with spend_origin(TrafficOrigin.WORKFLOW):
            with pytest.raises(RuntimeError):
                with spend_origin(TrafficOrigin.TOOLS):
                    raise RuntimeError("the child failed")
            assert current_origin() is TrafficOrigin.WORKFLOW

    def test_the_token_form_restores_what_it_replaced(self):
        token = publish_origin(TrafficOrigin.DATASOURCE_INDEXING)
        assert current_origin() is TrafficOrigin.DATASOURCE_INDEXING
        reset_origin(token)
        assert current_origin() is None


class TestTheOriginCrossesOnlyACopiedContext:
    def test_an_async_task_inherits_the_origin_published_around_its_creation(self):
        async def read() -> TrafficOrigin | None:
            return current_origin()

        async def run() -> TrafficOrigin | None:
            with spend_origin(TrafficOrigin.CONVERSATION_ANALYSIS):
                return await asyncio.create_task(read())

        assert asyncio.run(run()) is TrafficOrigin.CONVERSATION_ANALYSIS

    def test_a_plain_pool_thread_sees_nothing_unless_the_context_is_copied(self):
        """The assistant executor and the workflow producer are plain pools: a model built there
        reads no origin, sends no key, and the row lands NULL - a known hole, never a wrong value."""
        with spend_origin(TrafficOrigin.WORKFLOW), ThreadPoolExecutor(max_workers=1) as pool:
            bare = pool.submit(current_origin).result()
            copied = pool.submit(contextvars.copy_context().run, current_origin).result()

        assert bare is None
        assert copied is TrafficOrigin.WORKFLOW
