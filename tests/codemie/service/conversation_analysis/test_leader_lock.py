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

"""Tests for LeaderLockContext - PostgreSQL advisory lock-based leader election."""

from __future__ import annotations

import threading
from unittest.mock import Mock, patch

import pytest

from codemie.service.conversation_analysis.leader_lock import LeaderLockContext


class TestLeaderLockContext:
    """Test suite for LeaderLockContext context manager."""

    def test_lock_acquisition_success(self):
        """Test successful lock acquisition when lock is available."""
        with patch("codemie.service.conversation_analysis.leader_lock.PostgresClient") as mock_client:
            # Setup mocks
            mock_engine = Mock()
            mock_connection = Mock()
            mock_session = Mock()
            mock_result = Mock()
            mock_result.scalar.return_value = True  # Lock acquired

            mock_engine.connect.return_value = mock_connection
            mock_client.get_engine.return_value = mock_engine

            with patch("codemie.service.conversation_analysis.leader_lock.Session") as mock_session_class:
                mock_session_class.return_value = mock_session
                mock_session.execute.return_value = mock_result

                # Test lock acquisition
                with LeaderLockContext(lock_id=123456) as lock:
                    assert lock.acquired is True
                    assert lock.lock_id == 123456
                    assert lock._connection == mock_connection
                    assert mock_session.execute.called

                    # Verify pg_try_advisory_lock was called
                    call_args = mock_session.execute.call_args_list[0]
                    sql_text = str(call_args[0][0])
                    assert "pg_try_advisory_lock(123456)" in sql_text

                # Verify lock was released after context exit
                assert mock_session.execute.call_count == 2  # acquire + release
                release_call = mock_session.execute.call_args_list[1]
                release_sql = str(release_call[0][0])
                assert "pg_advisory_unlock(123456)" in release_sql

    def test_lock_acquisition_failure_already_held(self):
        """Test lock acquisition failure when another process holds the lock."""
        with patch("codemie.service.conversation_analysis.leader_lock.PostgresClient") as mock_client:
            # Setup mocks
            mock_engine = Mock()
            mock_connection = Mock()
            mock_session = Mock()
            mock_result = Mock()
            mock_result.scalar.return_value = False  # Lock NOT acquired

            mock_engine.connect.return_value = mock_connection
            mock_client.get_engine.return_value = mock_engine

            with patch("codemie.service.conversation_analysis.leader_lock.Session") as mock_session_class:
                mock_session_class.return_value = mock_session
                mock_session.execute.return_value = mock_result

                # Test lock acquisition failure
                with LeaderLockContext(lock_id=123456) as lock:
                    assert lock.acquired is False
                    assert lock.lock_id == 123456

                # Verify unlock was NOT called (we didn't acquire the lock)
                assert mock_session.execute.call_count == 1  # Only acquire attempt

    def test_lock_release_on_exception(self):
        """Test that lock is released even when exception occurs in context."""
        with patch("codemie.service.conversation_analysis.leader_lock.PostgresClient") as mock_client:
            # Setup mocks
            mock_engine = Mock()
            mock_connection = Mock()
            mock_session = Mock()
            mock_result = Mock()
            mock_result.scalar.return_value = True  # Lock acquired

            mock_engine.connect.return_value = mock_connection
            mock_client.get_engine.return_value = mock_engine

            with patch("codemie.service.conversation_analysis.leader_lock.Session") as mock_session_class:
                mock_session_class.return_value = mock_session
                mock_session.execute.return_value = mock_result

                # Test exception handling
                with pytest.raises(ValueError):
                    with LeaderLockContext(lock_id=123456) as lock:
                        assert lock.acquired is True
                        raise ValueError("Test exception")

                # Verify lock was still released despite exception
                assert mock_session.execute.call_count == 2  # acquire + release
                release_call = mock_session.execute.call_args_list[1]
                release_sql = str(release_call[0][0])
                assert "pg_advisory_unlock(123456)" in release_sql

    def test_connection_cleanup_on_success(self):
        """Test that connection and session are properly cleaned up."""
        with patch("codemie.service.conversation_analysis.leader_lock.PostgresClient") as mock_client:
            # Setup mocks
            mock_engine = Mock()
            mock_connection = Mock()
            mock_session = Mock()
            mock_result = Mock()
            mock_result.scalar.return_value = True

            mock_engine.connect.return_value = mock_connection
            mock_client.get_engine.return_value = mock_engine

            with patch("codemie.service.conversation_analysis.leader_lock.Session") as mock_session_class:
                mock_session_class.return_value = mock_session
                mock_session.execute.return_value = mock_result

                with LeaderLockContext(lock_id=123456):
                    pass

                # Verify cleanup was called
                mock_session.close.assert_called_once()
                mock_connection.close.assert_called_once()

    def test_default_lock_id(self):
        """Test that default ADVISORY_LOCK_ID is used when no lock_id provided."""
        with patch("codemie.service.conversation_analysis.leader_lock.PostgresClient") as mock_client:
            # Setup mocks
            mock_engine = Mock()
            mock_connection = Mock()
            mock_session = Mock()
            mock_result = Mock()
            mock_result.scalar.return_value = True

            mock_engine.connect.return_value = mock_connection
            mock_client.get_engine.return_value = mock_engine

            with patch("codemie.service.conversation_analysis.leader_lock.Session") as mock_session_class:
                mock_session_class.return_value = mock_session
                mock_session.execute.return_value = mock_result

                # Test with default lock ID
                with LeaderLockContext() as lock:
                    assert lock.lock_id == LeaderLockContext.ADVISORY_LOCK_ID
                    assert lock.acquired is True

    def test_lock_release_returns_false(self):
        """Test warning when lock release returns false (lock wasn't held)."""
        with patch("codemie.service.conversation_analysis.leader_lock.PostgresClient") as mock_client:
            # Setup mocks
            mock_engine = Mock()
            mock_connection = Mock()
            mock_session = Mock()

            mock_engine.connect.return_value = mock_connection
            mock_client.get_engine.return_value = mock_engine

            with patch("codemie.service.conversation_analysis.leader_lock.Session") as mock_session_class:
                mock_session_class.return_value = mock_session

                # Mock acquire returning True, but release returning False
                mock_acquire_result = Mock()
                mock_acquire_result.scalar.return_value = True
                mock_release_result = Mock()
                mock_release_result.scalar.return_value = False

                mock_session.execute.side_effect = [mock_acquire_result, mock_release_result]

                with patch("codemie.service.conversation_analysis.leader_lock.logger") as mock_logger:
                    with LeaderLockContext(lock_id=123456):
                        pass

                    # Verify warning was logged
                    assert mock_logger.warning.called
                    warning_message = mock_logger.warning.call_args[0][0]
                    assert "was not held during release" in warning_message

    def test_cleanup_handles_session_close_error(self):
        """Test that cleanup continues even if session.close() fails."""
        with patch("codemie.service.conversation_analysis.leader_lock.PostgresClient") as mock_client:
            # Setup mocks
            mock_engine = Mock()
            mock_connection = Mock()
            mock_session = Mock()
            mock_session.close.side_effect = Exception("Session close error")
            mock_result = Mock()
            mock_result.scalar.return_value = True

            mock_engine.connect.return_value = mock_connection
            mock_client.get_engine.return_value = mock_engine

            with patch("codemie.service.conversation_analysis.leader_lock.Session") as mock_session_class:
                mock_session_class.return_value = mock_session
                mock_session.execute.return_value = mock_result

                with patch("codemie.service.conversation_analysis.leader_lock.logger"):
                    with LeaderLockContext(lock_id=123456):
                        pass

                    # Verify connection.close() was still called despite session error
                    mock_connection.close.assert_called_once()

    def test_cleanup_handles_connection_close_error(self):
        """Test that cleanup completes even if connection.close() fails."""
        with patch("codemie.service.conversation_analysis.leader_lock.PostgresClient") as mock_client:
            # Setup mocks
            mock_engine = Mock()
            mock_connection = Mock()
            mock_connection.close.side_effect = Exception("Connection close error")
            mock_session = Mock()
            mock_result = Mock()
            mock_result.scalar.return_value = True

            mock_engine.connect.return_value = mock_connection
            mock_client.get_engine.return_value = mock_engine

            with patch("codemie.service.conversation_analysis.leader_lock.Session") as mock_session_class:
                mock_session_class.return_value = mock_session
                mock_session.execute.return_value = mock_result

                with patch("codemie.service.conversation_analysis.leader_lock.logger"):
                    # Should not raise exception
                    with LeaderLockContext(lock_id=123456):
                        pass

    def test_acquire_exception_triggers_cleanup(self):
        """Test that cleanup is called if lock acquisition fails with exception."""
        with patch("codemie.service.conversation_analysis.leader_lock.PostgresClient") as mock_client:
            # Setup mocks
            mock_engine = Mock()
            mock_connection = Mock()
            mock_session = Mock()
            mock_session.execute.side_effect = Exception("Database error")

            mock_engine.connect.return_value = mock_connection
            mock_client.get_engine.return_value = mock_engine

            with patch("codemie.service.conversation_analysis.leader_lock.Session") as mock_session_class:
                mock_session_class.return_value = mock_session

                # Test that exception is raised and cleanup is called
                with pytest.raises(Exception, match="Database error"):
                    with LeaderLockContext(lock_id=123456):
                        pass

                # Verify cleanup was attempted
                mock_session.close.assert_called_once()
                mock_connection.close.assert_called_once()


class TestLeaderLockContextIntegration:
    """Integration tests requiring actual database connection."""

    @pytest.mark.skip(reason="Requires PostgreSQL database - run manually for integration testing")
    def test_concurrent_lock_attempts(self):
        """
        Test that only one thread can acquire the lock at a time.

        This test requires an actual PostgreSQL connection and should be run
        manually during integration testing.
        """
        acquired_count = 0
        lock_holders = []

        def try_acquire_lock(thread_id):
            nonlocal acquired_count
            with LeaderLockContext(lock_id=999888777) as lock:
                if lock.acquired:
                    acquired_count += 1
                    lock_holders.append(thread_id)
                    # Hold lock briefly
                    import time

                    time.sleep(0.1)

        # Start multiple threads trying to acquire same lock
        threads = []
        for i in range(5):
            thread = threading.Thread(target=try_acquire_lock, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads
        for thread in threads:
            thread.join()

        # Only one thread should have acquired the lock
        assert acquired_count == 1
        assert len(lock_holders) == 1

    @pytest.mark.skip(reason="Requires PostgreSQL database - run manually for integration testing")
    def test_lock_lifecycle_with_real_database(self):
        """
        Test complete lock lifecycle with actual database.

        This test requires an actual PostgreSQL connection and should be run
        manually during integration testing.
        """

        # First acquisition should succeed
        with LeaderLockContext(lock_id=111222333) as lock1:
            assert lock1.acquired is True

            # Try to acquire again from same thread (should fail - already held)
            with LeaderLockContext(lock_id=111222333) as lock2:
                # Note: PostgreSQL allows same session to acquire same lock multiple times
                # This is expected behavior - locks stack
                assert lock2.acquired is True

        # After release, should be able to acquire again
        with LeaderLockContext(lock_id=111222333) as lock3:
            assert lock3.acquired is True
