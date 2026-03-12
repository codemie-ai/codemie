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

"""Integration tests for ConversationAnalysisService with leader election."""

from __future__ import annotations

from unittest.mock import Mock, patch, AsyncMock

import pytest

from codemie.service.conversation_analysis.conversation_analysis_service import ConversationAnalysisService


class TestConversationAnalysisServiceLeaderElection:
    """Test suite for leader election in ConversationAnalysisService."""

    @pytest.mark.asyncio
    async def test_schedule_analysis_job_when_disabled(self):
        """Test that job returns disabled status when feature is disabled."""
        service = ConversationAnalysisService()

        with patch("codemie.service.conversation_analysis.conversation_analysis_service.config") as mock_config:
            mock_config.CONVERSATION_ANALYSIS_ENABLED = False

            result = await service.schedule_analysis_job()

            assert result["status"] == "disabled"

    @pytest.mark.asyncio
    async def test_schedule_analysis_job_leader_acquired(self):
        """Test successful leader election and queue population."""
        service = ConversationAnalysisService()

        # Mock config
        with patch("codemie.service.conversation_analysis.conversation_analysis_service.config") as mock_config:
            mock_config.CONVERSATION_ANALYSIS_ENABLED = True

            # Mock LeaderLockContext to simulate successful lock acquisition
            with patch(
                "codemie.service.conversation_analysis.conversation_analysis_service.LeaderLockContext"
            ) as mock_lock_context:
                mock_lock = Mock()
                mock_lock.acquired = True
                mock_lock_context.return_value.__enter__.return_value = mock_lock
                mock_lock_context.return_value.__exit__.return_value = False

                # Mock conversations
                mock_conversation = Mock()
                mock_conversation.conversation_id = "conv-123"

                with patch.object(service, "_fetch_conversations_for_analysis", return_value=[mock_conversation]):
                    # Mock queue repository
                    service.queue_repo.add_conversations_to_queue = AsyncMock(return_value=1)

                    result = await service.schedule_analysis_job()

                    # Verify result
                    assert result["status"] == "success"
                    assert result["conversations_queued"] == 1
                    assert result["conversations_found"] == 1

                    # Verify queue was populated
                    service.queue_repo.add_conversations_to_queue.assert_called_once_with(["conv-123"])

                # Verify lock context was used
                mock_lock_context.assert_called_once()

    @pytest.mark.asyncio
    async def test_schedule_analysis_job_not_leader(self):
        """Test that job skips when another pod is leader."""
        service = ConversationAnalysisService()

        with patch("codemie.service.conversation_analysis.conversation_analysis_service.config") as mock_config:
            mock_config.CONVERSATION_ANALYSIS_ENABLED = True

            # Mock LeaderLockContext to simulate failed lock acquisition
            with patch(
                "codemie.service.conversation_analysis.conversation_analysis_service.LeaderLockContext"
            ) as mock_lock_context:
                mock_lock = Mock()
                mock_lock.acquired = False  # Another pod is leader
                mock_lock_context.return_value.__enter__.return_value = mock_lock
                mock_lock_context.return_value.__exit__.return_value = False

                result = await service.schedule_analysis_job()

                # Verify skipped status
                assert result["status"] == "skipped"
                assert result["reason"] == "not_leader"

    @pytest.mark.asyncio
    async def test_schedule_analysis_job_no_conversations(self):
        """Test leader with no conversations to analyze."""
        service = ConversationAnalysisService()

        with patch("codemie.service.conversation_analysis.conversation_analysis_service.config") as mock_config:
            mock_config.CONVERSATION_ANALYSIS_ENABLED = True

            # Mock LeaderLockContext
            with patch(
                "codemie.service.conversation_analysis.conversation_analysis_service.LeaderLockContext"
            ) as mock_lock_context:
                mock_lock = Mock()
                mock_lock.acquired = True
                mock_lock_context.return_value.__enter__.return_value = mock_lock
                mock_lock_context.return_value.__exit__.return_value = False

                # Mock empty conversations list
                with patch.object(service, "_fetch_conversations_for_analysis", return_value=[]):
                    result = await service.schedule_analysis_job()

                    # Verify result
                    assert result["status"] == "success"
                    assert result["conversations_queued"] == 0

    @pytest.mark.asyncio
    async def test_schedule_analysis_job_exception_during_work(self):
        """Test that lock is released even when exception occurs during leader work."""
        service = ConversationAnalysisService()

        with patch("codemie.service.conversation_analysis.conversation_analysis_service.config") as mock_config:
            mock_config.CONVERSATION_ANALYSIS_ENABLED = True

            # Mock LeaderLockContext
            with patch(
                "codemie.service.conversation_analysis.conversation_analysis_service.LeaderLockContext"
            ) as mock_lock_context:
                mock_lock = Mock()
                mock_lock.acquired = True
                # Mock __exit__ to return False (don't suppress exception)
                mock_exit = Mock(return_value=False)
                mock_lock_context.return_value.__enter__.return_value = mock_lock
                mock_lock_context.return_value.__exit__ = mock_exit

                # Mock _fetch_conversations_for_analysis to raise exception
                with patch.object(
                    service, "_fetch_conversations_for_analysis", side_effect=RuntimeError("Database error")
                ):
                    # Should raise RuntimeError
                    with pytest.raises(RuntimeError, match="Database error"):
                        await service.schedule_analysis_job()

                    # Verify __exit__ was called (lock released)
                    assert mock_exit.called

    @pytest.mark.asyncio
    async def test_schedule_analysis_job_multiple_conversations(self):
        """Test leader election with multiple conversations."""
        service = ConversationAnalysisService()

        with patch("codemie.service.conversation_analysis.conversation_analysis_service.config") as mock_config:
            mock_config.CONVERSATION_ANALYSIS_ENABLED = True

            # Mock LeaderLockContext
            with patch(
                "codemie.service.conversation_analysis.conversation_analysis_service.LeaderLockContext"
            ) as mock_lock_context:
                mock_lock = Mock()
                mock_lock.acquired = True
                mock_lock_context.return_value.__enter__.return_value = mock_lock
                mock_lock_context.return_value.__exit__.return_value = False

                # Mock multiple conversations
                mock_conversations = [Mock(conversation_id=f"conv-{i}") for i in range(5)]

                with patch.object(service, "_fetch_conversations_for_analysis", return_value=mock_conversations):
                    # Mock queue repository
                    service.queue_repo.add_conversations_to_queue = AsyncMock(return_value=5)

                    result = await service.schedule_analysis_job()

                    # Verify result
                    assert result["status"] == "success"
                    assert result["conversations_queued"] == 5
                    assert result["conversations_found"] == 5

                    # Verify all conversation IDs were passed
                    expected_ids = [f"conv-{i}" for i in range(5)]
                    service.queue_repo.add_conversations_to_queue.assert_called_once_with(expected_ids)


class TestConversationAnalysisServiceProcessBatch:
    """Test suite for process_batch method (non-leader work)."""

    @pytest.mark.asyncio
    async def test_process_batch_when_disabled(self):
        """Test that batch processing returns disabled status when feature is disabled."""
        service = ConversationAnalysisService()

        with patch("codemie.service.conversation_analysis.conversation_analysis_service.config") as mock_config:
            mock_config.CONVERSATION_ANALYSIS_ENABLED = False

            result = await service.process_batch()

            assert result["status"] == "disabled"

    @pytest.mark.asyncio
    async def test_process_batch_no_work(self):
        """Test batch processing when queue is empty."""
        service = ConversationAnalysisService()

        with patch("codemie.service.conversation_analysis.conversation_analysis_service.config") as mock_config:
            mock_config.CONVERSATION_ANALYSIS_ENABLED = True
            mock_config.CONVERSATION_ANALYSIS_BATCH_SIZE = 10

            # Mock empty queue
            service.queue_repo.claim_batch_for_processing = AsyncMock(return_value=[])

            result = await service.process_batch()

            # Verify no work status
            assert result["status"] == "no_work"
            assert result["processed"] == 0


@pytest.mark.skip(reason="Requires PostgreSQL database - run manually for integration testing")
class TestConversationAnalysisServiceRealDatabase:
    """
    Integration tests with real database connection.

    These tests require an actual PostgreSQL database and should be run
    manually during integration testing.
    """

    @pytest.mark.asyncio
    async def test_multiple_pods_leader_election(self):
        """
        Test that only one service instance becomes leader when multiple
        instances try to schedule analysis simultaneously.
        """
        import asyncio

        # Create multiple service instances (simulating multiple pods)
        services = [ConversationAnalysisService() for _ in range(3)]

        # Track which services became leader
        results = []

        async def try_schedule(service_instance):
            result = await service_instance.schedule_analysis_job()
            results.append(result)

        # All services try to schedule simultaneously
        await asyncio.gather(*[try_schedule(svc) for svc in services])

        # Count how many became leader
        leader_count = sum(1 for r in results if r.get("status") != "skipped")
        not_leader_count = sum(1 for r in results if r.get("reason") == "not_leader")

        # Only one should have become leader
        assert leader_count == 1
        assert not_leader_count == 2

    @pytest.mark.asyncio
    async def test_lock_released_after_completion(self):
        """
        Test that lock is properly released after job completion,
        allowing subsequent runs to acquire it.
        """
        service = ConversationAnalysisService()

        # First run
        result1 = await service.schedule_analysis_job()
        assert result1["status"] in ["success", "disabled"]

        # Second run (should be able to acquire lock)
        result2 = await service.schedule_analysis_job()
        assert result2["status"] in ["success", "disabled"]
        # Should not be skipped due to lock held from first run
        assert result2.get("reason") != "not_leader"

    @pytest.mark.asyncio
    async def test_lock_released_after_exception(self):
        """
        Test that lock is released even when exception occurs,
        allowing recovery on next run.
        """
        service = ConversationAnalysisService()

        # Mock to cause exception during work
        with patch.object(service, "_fetch_conversations_for_analysis", side_effect=ValueError("Test error")):
            # First run should fail with ValueError
            with pytest.raises(ValueError, match="Test error"):
                await service.schedule_analysis_job()

        # Second run should succeed (lock was released despite error)
        with patch.object(service, "_fetch_conversations_for_analysis", return_value=[]):
            result = await service.schedule_analysis_job()
            # Should not be skipped due to leaked lock
            assert result["status"] in ["success", "disabled"]
            assert result.get("reason") != "not_leader"
