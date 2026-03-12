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

"""Leader election using PostgreSQL advisory locks.

This module provides a context manager for distributed leader election
using PostgreSQL advisory locks in a connection pooling environment.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlmodel import Session

from codemie.clients.postgres import PostgresClient
from codemie.configs import logger


class LeaderLockContext:
    """
    Context manager for PostgreSQL advisory lock-based leader election.

    Ensures the same database connection is used for lock acquisition,
    leader operations, and lock release. Prevents lock leakage in
    connection pooling environments.

    Advisory locks in PostgreSQL are connection-scoped, meaning they persist
    for the lifetime of the database connection. In a connection pooling
    environment, if we acquire a lock using one connection and try to release
    it using another, the release will fail and the lock will leak.

    This context manager solves this problem by keeping the same connection
    alive for the entire duration of the leader operation.

    Usage:
        with LeaderLockContext(lock_id=123) as lock:
            if not lock.acquired:
                return  # Another process is leader

            # Do leader work here
            # Lock held on same connection throughout

        # Lock automatically released here (even on exception)

    Example:
        with LeaderLockContext() as leader_lock:
            if not leader_lock.acquired:
                logger.info("Not the leader, skipping")
                return

            # This pod is the leader
            perform_leader_work()
            # Lock released automatically on exit
    """

    ADVISORY_LOCK_ID = 987654321  # Unique ID for conversation analysis lock

    def __init__(self, lock_id: int | None = None):
        """
        Initialize leader lock context.

        Args:
            lock_id: PostgreSQL advisory lock ID (defaults to class constant)
        """
        self.lock_id = lock_id or self.ADVISORY_LOCK_ID
        self.session: Session | None = None
        self.acquired: bool = False
        self._connection = None

    def __enter__(self) -> LeaderLockContext:
        """
        Acquire advisory lock on entry.

        Gets a connection from the pool and attempts to acquire the advisory lock.
        The connection is kept alive for the duration of the context.

        Returns:
            Self with `acquired` attribute set to True/False

        Raises:
            Exception: If lock acquisition fails due to database error
        """
        engine = PostgresClient.get_engine()

        # Get connection from pool and keep it alive
        self._connection = engine.connect()

        # Create session bound to this specific connection
        self.session = Session(bind=self._connection)

        try:
            # Try to acquire advisory lock (non-blocking)
            result = self.session.execute(text(f"SELECT pg_try_advisory_lock({self.lock_id})")).scalar()

            self.acquired = bool(result)

            if self.acquired:
                logger.info(f"Advisory lock {self.lock_id} acquired successfully (connection: {id(self._connection)})")
            else:
                logger.info(f"Advisory lock {self.lock_id} already held by another process")

        except Exception as e:
            logger.error(f"Failed to acquire advisory lock {self.lock_id}: {e}", exc_info=True)
            self.acquired = False
            # Clean up connection if acquisition fails
            self._cleanup()
            raise

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Release advisory lock on exit (even on exception).

        This method is called automatically when exiting the context,
        whether normally or due to an exception. It ensures the lock
        is released and the connection is returned to the pool.

        Args:
            exc_type: Exception type (if raised)
            exc_val: Exception value (if raised)
            exc_tb: Exception traceback (if raised)

        Returns:
            False to propagate exceptions from the with block
        """
        try:
            if self.acquired and self.session:
                # Release lock on same connection that acquired it
                result = self.session.execute(text(f"SELECT pg_advisory_unlock({self.lock_id})")).scalar()

                if result:
                    logger.info(
                        f"Advisory lock {self.lock_id} released successfully (connection: {id(self._connection)})"
                    )
                else:
                    logger.warning(
                        f"Advisory lock {self.lock_id} was not held during release "
                        f"(connection: {id(self._connection)}). This may indicate a bug."
                    )
        except Exception as e:
            logger.error(f"Failed to release advisory lock {self.lock_id}: {e}", exc_info=True)
        finally:
            self._cleanup()

        # Don't suppress exceptions from the with block
        return False

    def _cleanup(self):
        """
        Close session and return connection to pool.

        This method ensures resources are properly cleaned up even if
        errors occur during the cleanup process itself.
        """
        if self.session:
            try:
                self.session.close()
            except Exception as e:
                logger.error(f"Error closing session: {e}")
            finally:
                self.session = None

        if self._connection:
            try:
                self._connection.close()  # Returns connection to pool
            except Exception as e:
                logger.error(f"Error closing connection: {e}")
            finally:
                self._connection = None
