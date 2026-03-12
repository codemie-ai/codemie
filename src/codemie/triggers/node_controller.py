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

"""Module for triggers core service"""

import platform
import asyncio
from elasticsearch.exceptions import NotFoundError, ConflictError, ApiError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from codemie.configs import logger
from codemie.triggers.state import TransactionElasticSupport
from codemie.triggers.bindings.cron import Cron


class State(TransactionElasticSupport):
    _index: str = "trigger_engine_node"
    id: str = platform.uname().node
    is_active: bool = False

    def active_node(self):
        """Active node lock"""
        previous_state = self.is_active
        self.__invalidate_lock_by_timeout()

        try:
            if self.elastic_client.count(index=self._index)["count"] == 0:
                if not previous_state:
                    logger.debug(
                        "Trigger controller node lock does not exist, creating lock for %s",
                        self.id,
                    )
                self.save()
                self.is_active = True
            elif self.get_by_id(self.id):
                if not previous_state:
                    logger.debug("Trigger controller node lock acquired, active node: %s", self.id)
                self.update()
                self.is_active = True
        except NotFoundError:
            if previous_state:
                logger.warning("Trigger controller node lock lost, switched %s to inactive", self.id)
            self.is_active = False

    def __invalidate_lock_by_timeout(self):
        """Invalidate node by timeout"""
        try:
            self.elastic_client.delete_by_query(
                index=self._index,
                body={"query": {"range": {"update_date": {"lt": "now-11s"}}}},
            )
        except ConflictError as e:
            logger.warning("ConflictError while invalidating lock by timeout: %s", e)
        except ApiError as e:
            logger.warning("ApiError while invalidating lock by timeout: %s", e)

    def enable_trigger_active_node_index(self):
        """Create trigger active node index if not exists"""
        if self.elastic_client.indices.exists(index=self._index):
            logger.info("Trigger active node index exists, skipping creation. Node: %s", self.id)
        else:
            logger.info("Creating trigger active node index. Node: %s", self.id)
            self.elastic_client.indices.create(index=self._index)


class NodeController:
    """Trigger engine active node controller"""

    start_delay = 10

    def __init__(self):
        """Initialize NodeController"""
        self.state = State()
        self.engine_task = None
        self.cron_instance = Cron()
        self.scheduler = None
        self.node_watcher = None
        self.tg = None

    async def start(self):
        """Start the trigger engine"""
        self.state.enable_trigger_active_node_index()
        self.scheduler = AsyncIOScheduler()
        self.scheduler.start()
        self.node_watcher = self.scheduler.add_job(self.state.active_node, "interval", seconds=self.start_delay)

        await asyncio.sleep(self.start_delay)

        async with asyncio.TaskGroup() as tg:
            self.tg = tg
            await self.__engine_watchdog()

    async def __engine_watchdog(self):
        """Start the trigger engine"""
        logger.info("Trigger Engine Node Controller Watchdog started on node: %s", self.state.id)
        while True:
            if self.state.is_active and not self.engine_task:
                # Reuse the same Cron instance instead of creating a new one
                self.engine_task = self.tg.create_task(self.cron_instance.start_async())
                logger.info("Trigger Engine started on node: %s", self.state.id)
            elif not self.state.is_active and self.engine_task:
                # Properly shutdown the Cron instance before cancelling the task
                try:
                    self.cron_instance.shutdown()
                    logger.info("Trigger Engine shutdown completed on node: %s", self.state.id)
                except Exception as e:
                    logger.error("Error during Cron shutdown on node %s: %s", self.state.id, e, exc_info=True)
                finally:
                    self.engine_task.cancel()
                    try:
                        await self.engine_task
                    except asyncio.CancelledError:
                        logger.debug("Engine task cancelled successfully")
                    self.engine_task = None
                    logger.info("Trigger Engine stopped on node: %s", self.state.id)
            await asyncio.sleep(3)
