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

"""
Unit tests for Conversation access control via Ability system.

These tests verify the security fix:
- Only conversation owners can access their conversations via regular links
- Admins cannot access other users' conversations via regular links
- Shared conversations work through dedicated share endpoints
"""

import pytest

from codemie.core.ability import Ability, Action
from codemie.rest_api.models.conversation import Conversation
from codemie.rest_api.security.user import User


@pytest.fixture
def conversation_owner():
    """Fixture for conversation owner user."""
    return User(id="owner-123", username="owner", name="Owner User", is_admin=False)


@pytest.fixture
def admin_user():
    """Fixture for admin user."""
    return User(id="admin-456", username="admin", name="Admin User", is_admin=True)


@pytest.fixture
def other_user():
    """Fixture for a different non-admin user."""
    return User(id="other-789", username="other", name="Other User", is_admin=False)


@pytest.fixture
def conversation(conversation_owner):
    """Fixture for a conversation owned by conversation_owner."""
    return Conversation(
        id="conv-123",
        conversation_id="conv-123",
        conversation_name="Test Conversation",
        user_id=conversation_owner.id,
        user_name=conversation_owner.name,
        history=[],
    )


class TestConversationAccessControl:
    """Test suite for conversation access control security fix."""

    def test_owner_can_read_own_conversation(self, conversation_owner, conversation):
        """
        Test that conversation owner can READ their own conversation.

        Expected: Owner should have READ access to their own conversation.
        """
        ability = Ability(user=conversation_owner)

        assert ability.can(Action.READ, conversation) is True

    def test_owner_can_write_own_conversation(self, conversation_owner, conversation):
        """
        Test that conversation owner can WRITE to their own conversation.

        Expected: Owner should have WRITE access to their own conversation.
        """
        ability = Ability(user=conversation_owner)

        assert ability.can(Action.WRITE, conversation) is True

    def test_owner_can_delete_own_conversation(self, conversation_owner, conversation):
        """
        Test that conversation owner can DELETE their own conversation.

        Expected: Owner should have DELETE access to their own conversation.
        """
        ability = Ability(user=conversation_owner)

        assert ability.can(Action.DELETE, conversation) is True

    def test_admin_cannot_read_other_user_conversation(self, admin_user, conversation):
        """
        Test that admin CANNOT read other users' conversations via regular links.

        Expected: Admin should NOT have READ access to conversations they don't own.
        """
        ability = Ability(user=admin_user)

        assert ability.can(Action.READ, conversation) is False

    def test_admin_cannot_write_other_user_conversation(self, admin_user, conversation):
        """
        Test that admin CANNOT write to other users' conversations.

        Expected: Admin should NOT have WRITE access to conversations they don't own.
        """
        ability = Ability(user=admin_user)

        assert ability.can(Action.WRITE, conversation) is False

    def test_admin_cannot_delete_other_user_conversation(self, admin_user, conversation):
        """
        Test that admin CANNOT delete other users' conversations.

        Expected: Admin should NOT have DELETE access to conversations they don't own.
        """
        ability = Ability(user=admin_user)

        assert ability.can(Action.DELETE, conversation) is False

    def test_other_user_cannot_read_conversation(self, other_user, conversation):
        """
        Test that non-owner, non-admin users cannot read conversations.

        Expected: Other users should NOT have READ access to conversations they don't own.
        """
        ability = Ability(user=other_user)

        assert ability.can(Action.READ, conversation) is False

    def test_other_user_cannot_write_conversation(self, other_user, conversation):
        """
        Test that non-owner users cannot write to conversations.

        Expected: Other users should NOT have WRITE access to conversations they don't own.
        """
        ability = Ability(user=other_user)

        assert ability.can(Action.WRITE, conversation) is False

    def test_other_user_cannot_delete_conversation(self, other_user, conversation):
        """
        Test that non-owner users cannot delete conversations.

        Expected: Other users should NOT have DELETE access to conversations they don't own.
        """
        ability = Ability(user=other_user)

        assert ability.can(Action.DELETE, conversation) is False

    def test_non_owner_cannot_read_conversation_even_if_shared(self, other_user, conversation):
        """
        Test that non-owners cannot access conversations via regular conversation endpoints.

        Shared conversations are accessed through dedicated share endpoints
        (/v1/share/conversations/{token}), not through regular conversation endpoints.

        Expected: Non-owners should NOT have READ access via regular endpoints,
        even if the conversation is shared. Sharing is handled separately.
        """
        ability = Ability(user=other_user)

        # User should NOT be able to read conversation via regular endpoint
        # Sharing is handled through /v1/share/conversations/{token} endpoint
        assert ability.can(Action.READ, conversation) is False

    def test_ability_list_for_owner(self, conversation_owner, conversation):
        """
        Test that owner has all abilities for their conversation.

        Expected: Owner should have READ, WRITE, and DELETE permissions.
        """
        ability = Ability(user=conversation_owner)

        abilities = ability.list(conversation)

        assert Action.READ in abilities
        assert Action.WRITE in abilities
        assert Action.DELETE in abilities
        assert len(abilities) == 3

    def test_ability_list_for_admin(self, admin_user, conversation):
        """
        Test that admin has NO abilities for other users' conversations.

        Expected: Admin should have empty list of abilities for conversations they don't own.
        """
        ability = Ability(user=admin_user)

        abilities = ability.list(conversation)

        assert abilities == []

    def test_ability_list_for_other_user(self, other_user, conversation):
        """
        Test that non-owner users have NO abilities for conversations.

        Expected: Other users should have empty list of abilities for conversations they don't own.
        """
        ability = Ability(user=other_user)

        abilities = ability.list(conversation)

        assert abilities == []


class TestConversationOwnershipMethods:
    """Test suite for Conversation ownership check methods."""

    def test_is_owned_by_returns_true_for_owner(self, conversation_owner, conversation):
        """
        Test that is_owned_by returns True for the conversation owner.

        Expected: is_owned_by should return True when user_id matches.
        """
        assert conversation.is_owned_by(conversation_owner) is True

    def test_is_owned_by_returns_false_for_non_owner(self, admin_user, conversation):
        """
        Test that is_owned_by returns False for non-owners.

        Expected: is_owned_by should return False when user_id doesn't match.
        """
        assert conversation.is_owned_by(admin_user) is False

    def test_is_managed_by_always_returns_false(self, conversation_owner, conversation):
        """
        Test that is_managed_by always returns False for conversations.

        Expected: Conversations don't have a management model, so this should always be False.
        """
        assert conversation.is_managed_by(conversation_owner) is False

    def test_is_shared_with_always_returns_false(self, other_user, conversation):
        """
        Test that is_shared_with always returns False for conversations.

        Conversation sharing is handled through dedicated share endpoints
        (/v1/share/conversations/{token}), not through the ability system.

        Expected: is_shared_with should always return False for conversations.
        The share functionality works independently through ShareConversationService.
        """
        assert conversation.is_shared_with(other_user) is False
