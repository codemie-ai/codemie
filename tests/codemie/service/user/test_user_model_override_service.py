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

"""Tests for UserModelOverrideService.

Covers:
- set_override with validation
- get_override
- delete_override
- get_overrides_by_project
- batch_set_overrides (grant and revoke)
- _validate_override_models
- _validate_default_model
- deprecated models: kept where already listed, rejected when new
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from codemie.core.exceptions import ModelNotSelectableError, ModelNotWhitelistedException, NotFoundException
from codemie.service.user.user_model_override_service import BatchValidationError
from codemie.core.models import Application
from codemie.rest_api.models.user_model_override import UserModelOverride
from codemie.rest_api.models.user_management import UserDB, UserProject
from codemie.service.user.user_model_override_service import UserModelOverrideService

REPO = "codemie.service.user.user_model_override_service.UserModelOverrideRepository"
UNKNOWN_MODEL = "unknown-model"
ACTOR = "admin-1"
ACTOR_EMAIL = "admin-1@example.com"


@pytest.fixture(autouse=True)
def _catalog_knows_every_model_but_one():
    with patch(
        "codemie.service.user.user_model_override_service.is_known_model",
        side_effect=lambda model: model != UNKNOWN_MODEL,
    ):
        yield


@pytest.fixture(autouse=True)
def activity_events():
    """The activity event repository, replaced so the tests can assert what is written."""
    with patch("codemie.service.user.user_model_override_service.activity_event_repository") as repo:
        repo.insert_strict = MagicMock()
        repo.insert_many_strict = MagicMock()
        yield repo


def _make_session(*, user_project=None, app=None, user=True, users=(), members=()) -> MagicMock:
    """AsyncSession stand-in whose execute() answers per selected entity.

    `(session.execute(select(Entity)...)).scalars().first()` returns the value
    configured for that entity (None when not configured). A query for one column of
    UserDB or UserProject (the batch lookups) answers `.scalars().all()` with `users` / `members`.
    """
    values = {
        UserProject: user_project,
        Application: app,
        UserDB: MagicMock(spec=UserDB) if user else None,
    }
    id_columns = {UserDB: list(users), UserProject: list(members)}

    def execute(statement):
        description = statement.column_descriptions[0]
        entity = description["entity"]
        result = MagicMock()
        if description["name"] != entity.__name__:
            result.scalars.return_value.all.return_value = id_columns[entity]
        else:
            result.scalars.return_value.first.return_value = values[entity]
        return result

    session = MagicMock()
    session.execute = MagicMock(side_effect=execute)
    return session


def _app(allowed_models):
    app = MagicMock(spec=Application)
    app.allowed_models = allowed_models
    return app


class TestSetOverride:
    """Tests for set_override method"""

    def test_set_override_success_creates_new(self):
        """Verify set_override creates new override when validation passes."""
        session = _make_session(
            user_project=MagicMock(spec=UserProject),
            app=_app(["gpt-4", "claude-opus", "gpt-3.5"]),
        )
        user_id = str(uuid4())
        allowed_models = ["gpt-4", "claude-opus"]
        mock_override = MagicMock(spec=UserModelOverride)

        with (
            patch(f"{REPO}.get_by_user_and_project", return_value=None),
            patch(f"{REPO}.create", return_value=mock_override) as mock_create,
        ):
            result = UserModelOverrideService.set_override(
                session, user_id, "test-project", allowed_models, actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

        assert result == mock_override
        mock_create.assert_called_once_with(session, user_id, "test-project", allowed_models, None)

    def test_set_override_passes_default_model(self):
        """Verify set_override forwards default_model to the repository."""
        session = _make_session(
            user_project=MagicMock(spec=UserProject),
            app=_app(["gpt-4", "claude-opus"]),
        )
        user_id = str(uuid4())
        mock_override = MagicMock(spec=UserModelOverride)

        with (
            patch(f"{REPO}.get_by_user_and_project", return_value=None),
            patch(f"{REPO}.create", return_value=mock_override) as mock_create,
        ):
            UserModelOverrideService.set_override(
                session,
                user_id,
                "test-project",
                ["gpt-4", "claude-opus"],
                default_model="claude-opus",
                actor_id=ACTOR,
                actor_email=ACTOR_EMAIL,
            )

        mock_create.assert_called_once_with(session, user_id, "test-project", ["gpt-4", "claude-opus"], "claude-opus")

    def test_set_override_default_model_not_in_allowed_models(self):
        """Verify set_override rejects a default_model outside allowed_models."""
        session = _make_session(
            user_project=MagicMock(spec=UserProject),
            app=_app(["gpt-4", "claude-opus"]),
        )

        with pytest.raises(ModelNotWhitelistedException):
            UserModelOverrideService.set_override(
                session,
                str(uuid4()),
                "test-project",
                ["gpt-4"],
                default_model="claude-opus",
                actor_id=ACTOR,
                actor_email=ACTOR_EMAIL,
            )

    def test_set_override_success_updates_existing(self):
        """Verify set_override updates existing override when validation passes."""
        session = _make_session(
            user_project=MagicMock(spec=UserProject),
            app=_app(["gpt-4", "claude-opus"]),
        )
        user_id = str(uuid4())
        mock_override = MagicMock(spec=UserModelOverride)

        with (
            patch(f"{REPO}.get_by_user_and_project", return_value=mock_override),
            patch(f"{REPO}.update", return_value=mock_override) as mock_update,
            patch(f"{REPO}.create") as mock_create,
        ):
            result = UserModelOverrideService.set_override(
                session, user_id, "test-project", ["gpt-4"], actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

        assert result == mock_override
        mock_update.assert_called_once_with(session, user_id, "test-project", ["gpt-4"], None)
        mock_create.assert_not_called()

    def test_set_override_user_not_in_project(self):
        """Verify set_override raises ValueError (HTTP 400) if user is not a project member."""
        session = _make_session(user_project=None, app=_app(["gpt-4"]))

        with pytest.raises(ValueError, match="not a member"):
            UserModelOverrideService.set_override(
                session, str(uuid4()), "test-project", ["gpt-4"], actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

    def test_set_override_user_not_found(self):
        """Verify set_override raises NotFoundException (HTTP 404) if the user does not exist."""
        session = _make_session(user_project=MagicMock(spec=UserProject), app=_app(["gpt-4"]), user=False)

        with pytest.raises(NotFoundException, match="User not found"):
            UserModelOverrideService.set_override(
                session, str(uuid4()), "test-project", ["gpt-4"], actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

    def test_set_override_project_not_found(self):
        """Verify set_override raises NotFoundException if project does not exist."""
        session = _make_session(user_project=MagicMock(spec=UserProject), app=None)

        with pytest.raises(NotFoundException):
            UserModelOverrideService.set_override(
                session, str(uuid4()), "nonexistent-project", ["gpt-4"], actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )


class TestGetOverride:
    """Tests for get_override method"""

    def test_get_override_found(self):
        """Verify get_override returns existing override."""
        session = MagicMock()
        user_id = str(uuid4())
        mock_override = MagicMock(spec=UserModelOverride)

        with patch(f"{REPO}.get_by_user_and_project", return_value=mock_override) as mock_get:
            result = UserModelOverrideService.get_override(session, user_id, "test-project")

        assert result == mock_override
        mock_get.assert_called_once_with(session, user_id, "test-project")

    def test_get_override_not_found(self):
        """Verify get_override returns None if no override exists."""
        session = MagicMock()

        with patch(f"{REPO}.get_by_user_and_project", return_value=None):
            result = UserModelOverrideService.get_override(session, str(uuid4()), "test-project")

        assert result is None


class TestDeleteOverride:
    """Tests for delete_override method"""

    def test_delete_override(self):
        """Verify delete_override calls repository delete."""
        session = MagicMock()
        user_id = str(uuid4())

        with patch(f"{REPO}.delete", return_value=None) as mock_delete:
            UserModelOverrideService.delete_override(
                session, user_id, "test-project", actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

        mock_delete.assert_called_once_with(session, user_id, "test-project")


class TestGetOverridesByProject:
    """Tests for get_overrides_by_project method"""

    def test_get_overrides_by_project_with_pagination(self):
        """Verify get_overrides_by_project calls repository with correct pagination."""
        session = _make_session(app=_app(["gpt-4"]))
        mock_overrides = [MagicMock(spec=UserModelOverride) for _ in range(3)]

        with patch(f"{REPO}.get_by_project", return_value=mock_overrides) as mock_get:
            result = UserModelOverrideService.get_overrides_by_project(session, "test-project", 10, 50)

        assert result == mock_overrides
        mock_get.assert_called_once_with(session, "test-project", 10, 50)

    def test_get_overrides_by_project_unknown_project(self):
        session = _make_session(app=None)

        with patch(f"{REPO}.get_by_project") as mock_get:
            with pytest.raises(NotFoundException):
                UserModelOverrideService.get_overrides_by_project(session, "missing")

        mock_get.assert_not_called()


class TestBatchSetOverrides:
    """Tests for batch_set_overrides method"""

    def test_batch_set_overrides_grant_success(self):
        """Verify batch_set_overrides with grant operation adds overrides for all users."""
        user_ids = [str(uuid4()) for _ in range(3)]
        session = _make_session(app=_app(["gpt-4", "claude-opus"]), users=user_ids, members=user_ids)
        mock_overrides = [MagicMock(spec=UserModelOverride) for _ in user_ids]

        with (
            patch(f"{REPO}.get_by_users_and_project", return_value={}),
            patch(f"{REPO}.upsert_many", return_value=mock_overrides) as mock_upsert,
        ):
            result = UserModelOverrideService.batch_set_overrides(
                session, "test-project", user_ids, "grant", ["gpt-4"], "gpt-4", actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

        assert result == mock_overrides
        mock_upsert.assert_called_once_with(session, "test-project", user_ids, ["gpt-4"], "gpt-4", {})

    def test_batch_set_overrides_grant_updates_existing(self):
        """Verify batch grant hands the users' current overrides to the upsert, which updates them."""
        user_ids = [str(uuid4())]
        session = _make_session(app=_app(["gpt-4"]), users=user_ids, members=user_ids)
        mock_override = _override_row(user_ids[0], ["claude-opus"])
        existing = {user_ids[0]: mock_override}

        with (
            patch(f"{REPO}.get_by_users_and_project", return_value=existing),
            patch(f"{REPO}.upsert_many", return_value=[mock_override]) as mock_upsert,
        ):
            result = UserModelOverrideService.batch_set_overrides(
                session, "test-project", user_ids, "grant", ["gpt-4"], actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

        assert result == [mock_override]
        mock_upsert.assert_called_once_with(session, "test-project", user_ids, ["gpt-4"], None, existing)

    def test_batch_set_overrides_revoke_success(self):
        """Verify batch_set_overrides with revoke operation removes overrides."""
        session = _make_session(app=_app(["gpt-4"]))
        user_ids = [str(uuid4()), str(uuid4())]
        had_override = MagicMock(spec=UserModelOverride)  # the second user had none
        had_override.project_name = "test-project"
        had_override.allowed_models = ["gpt-4"]
        had_override.default_model = None

        with (
            patch(f"{REPO}.get_by_users_and_project", return_value={user_ids[0]: had_override}),
            patch(f"{REPO}.delete_many") as mock_delete,
        ):
            result = UserModelOverrideService.batch_set_overrides(
                session, "test-project", user_ids, "revoke", None, actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

        mock_delete.assert_called_once_with(session, [had_override])
        assert result == [had_override]

    @pytest.mark.parametrize("operation", ["grant", "revoke"])
    def test_batch_set_overrides_unknown_project(self, operation):
        session = _make_session(app=None)

        with patch(f"{REPO}.delete_many") as mock_delete:
            with pytest.raises(NotFoundException):
                UserModelOverrideService.batch_set_overrides(
                    session, "missing", [str(uuid4())], operation, ["gpt-4"], actor_id=ACTOR, actor_email=ACTOR_EMAIL
                )

        mock_delete.assert_not_called()

    def test_batch_set_overrides_grant_user_not_in_project(self):
        """Verify batch_set_overrides fails if any user not in project."""
        user_ids = [str(uuid4()), str(uuid4())]
        session = _make_session(app=_app(["gpt-4", "claude-opus"]), users=user_ids, members=[])

        with pytest.raises(BatchValidationError) as exc_info:
            UserModelOverrideService.batch_set_overrides(
                session, "test-project", user_ids, "grant", ["gpt-4"], actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

        assert exc_info.value.failed_user_id == user_ids[0]
        assert "not a member" in exc_info.value.reason

    def test_batch_set_overrides_names_the_first_failing_user_in_order(self):
        user_ids = [str(uuid4()) for _ in range(3)]
        session = _make_session(app=_app(["gpt-4"]), users=user_ids, members=[user_ids[0], user_ids[2]])

        with pytest.raises(BatchValidationError) as exc_info:
            UserModelOverrideService.batch_set_overrides(
                session, "test-project", user_ids, "grant", ["gpt-4"], actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

        assert exc_info.value.failed_user_id == user_ids[1]

    def test_batch_set_overrides_grant_user_not_found(self):
        """Verify batch grant raises NotFoundException (HTTP 404) if a user does not exist, like the single endpoint."""
        user_ids = [str(uuid4())]
        session = _make_session(app=_app(["gpt-4"]), users=[], members=user_ids)

        with pytest.raises(NotFoundException, match="User not found"):
            UserModelOverrideService.batch_set_overrides(
                session, "test-project", user_ids, "grant", ["gpt-4"], actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

    def test_batch_set_overrides_checks_users_in_the_order_given(self):
        """A non-member listed before a missing user is the failure reported, as with the old per-user loop."""
        user_ids = [str(uuid4()), str(uuid4())]
        session = _make_session(app=_app(["gpt-4"]), users=[user_ids[0]], members=[])

        with pytest.raises(BatchValidationError) as exc_info:
            UserModelOverrideService.batch_set_overrides(
                session, "test-project", user_ids, "grant", ["gpt-4"], actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

        assert exc_info.value.failed_user_id == user_ids[0]

    def test_batch_set_overrides_unknown_operation(self):
        """Verify an unknown operation is rejected instead of silently doing nothing."""
        with pytest.raises(ValueError):
            UserModelOverrideService.batch_set_overrides(
                MagicMock(), "test-project", ["u"], "purge", None, actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

    def test_batch_set_overrides_grant_missing_models(self):
        """Verify batch_set_overrides grant raises ValueError if allowed_models not provided."""
        session = _make_session(app=_app(["gpt-4"]))

        with pytest.raises(ValueError):
            UserModelOverrideService.batch_set_overrides(
                session, "test-project", [str(uuid4())], "grant", None, actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

    @pytest.mark.parametrize("operation", ["grant", "revoke"])
    def test_batch_cost_does_not_grow_with_the_number_of_users(self, operation):
        """One bulk read and one write however many users: the queries the batch issues are the same for 3 and 300."""
        counts = []
        for size in (3, 300):
            user_ids = [f"u{i}" for i in range(size)]
            session = _make_session(app=_app(["gpt-4"]), users=user_ids, members=user_ids)
            rows = {u: _override_row(u, ["claude-opus"]) for u in user_ids}
            with (
                patch(f"{REPO}.get_by_users_and_project", return_value=rows) as read,
                patch(f"{REPO}.upsert_many", return_value=list(rows.values())) as upsert,
                patch(f"{REPO}.delete_many") as delete,
                patch(f"{REPO}.get_by_user_and_project") as per_user_read,
                patch(f"{REPO}.update") as per_user_update,
                patch(f"{REPO}.create") as per_user_create,
                patch(f"{REPO}.delete") as per_user_delete,
            ):
                UserModelOverrideService.batch_set_overrides(
                    session, "test-project", user_ids, operation, ["gpt-4"], actor_id=ACTOR, actor_email=ACTOR_EMAIL
                )

            read.assert_called_once()
            assert (upsert if operation == "grant" else delete).call_count == 1
            for per_user in (per_user_read, per_user_update, per_user_create, per_user_delete):
                per_user.assert_not_called()
            counts.append(session.execute.call_count)

        assert counts[0] == counts[1]


class TestValidateOverrideModels:
    """Tests for _validate_override_models method"""

    def test_validate_override_models_empty_list(self):
        """Verify _validate_override_models raises ValueError for empty models."""
        session = MagicMock()

        with pytest.raises(ValueError):
            UserModelOverrideService._validate_override_models(session, "test-project", [])

    def test_validate_override_models_rejects_model_missing_from_catalog(self):
        """Verify _validate_override_models raises ValueError naming ids the catalog does not know."""
        session = _make_session(app=_app(None))

        with pytest.raises(ValueError, match=UNKNOWN_MODEL):
            UserModelOverrideService._validate_override_models(session, "test-project", ["gpt-4", UNKNOWN_MODEL])

    def test_validate_override_models_project_not_found(self):
        """Verify _validate_override_models raises NotFoundException if project not found."""
        session = _make_session(app=None)

        with pytest.raises(NotFoundException):
            UserModelOverrideService._validate_override_models(session, "nonexistent-project", ["gpt-4"])

    def test_validate_override_models_success(self):
        """Verify _validate_override_models succeeds when models are valid."""
        session = _make_session(
            app=_app(["gpt-4", "claude-opus", "gpt-3.5"]),
        )

        UserModelOverrideService._validate_override_models(session, "test-project", ["gpt-4", "claude-opus"])

    def test_validate_override_models_no_project_whitelist(self):
        """Verify _validate_override_models succeeds when project has no whitelist."""
        session = _make_session(app=_app(None))

        UserModelOverrideService._validate_override_models(session, "test-project", ["any-model"])


class TestValidateDefaultModel:
    """Tests for _validate_default_model method"""

    def test_none_default_model_is_valid(self):
        """Verify no default_model passes validation."""
        UserModelOverrideService._validate_default_model("test-project", ["gpt-4"], None)

    def test_default_model_in_allowed_models_is_valid(self):
        """Verify default_model within allowed_models passes validation."""
        UserModelOverrideService._validate_default_model("test-project", ["gpt-4", "claude-opus"], "claude-opus")

    def test_default_model_outside_allowed_models_raises(self):
        """Verify default_model outside allowed_models is rejected."""
        with pytest.raises(ModelNotWhitelistedException):
            UserModelOverrideService._validate_default_model("test-project", ["gpt-4"], "claude-opus")


def _override_row(user_id, allowed_models, default_model=None, project="test-project"):
    row = MagicMock(spec=UserModelOverride)
    row.user_id = user_id
    row.project_name = project
    row.allowed_models = allowed_models
    row.default_model = default_model
    return row


def _set_event(activity_events):
    activity_events.insert_strict.assert_called_once()
    return activity_events.insert_strict.call_args.args[0]


class TestAuditEvents:
    """Every change to a user's override is recorded in the same transaction, with the acting admin."""

    def test_creating_an_override_records_who_set_what(self, activity_events):
        session = _make_session(user_project=MagicMock(spec=UserProject), app=_app(["gpt-4"]))

        with (
            patch(f"{REPO}.get_by_user_and_project", return_value=None),
            patch(f"{REPO}.create", return_value=_override_row("u1", ["gpt-4"], "gpt-4")),
        ):
            UserModelOverrideService.set_override(
                session, "u1", "test-project", ["gpt-4"], default_model="gpt-4", actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

        event = _set_event(activity_events)
        assert event.domain == "user_management"
        assert event.event_type == "user.model_override.set"
        assert (event.entity_type, event.entity_id, event.actor_id) == ("user", "u1", ACTOR)
        assert event.attributes == {
            "project_name": "test-project",
            "updated_fields": ["allowed_models", "default_model"],
            "previous_allowed_models": None,
            "new_allowed_models": ["gpt-4"],
            "previous_default_model": None,
            "new_default_model": "gpt-4",
        }
        activity_events.insert_many_strict.assert_not_called()

    def test_updating_an_override_records_the_previous_values(self, activity_events):
        session = _make_session(user_project=MagicMock(spec=UserProject), app=_app(["gpt-4"]))
        existing = _override_row("u1", ["gpt-4"], "gpt-4")

        def update(_session, _user_id, _project, allowed_models, default_model):
            existing.allowed_models = allowed_models
            existing.default_model = default_model
            return existing

        with (
            patch(f"{REPO}.get_by_user_and_project", return_value=existing),
            patch(f"{REPO}.update", side_effect=update),
        ):
            UserModelOverrideService.set_override(
                session, "u1", "test-project", ["gpt-4", "claude-opus"], actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

        attributes = _set_event(activity_events).attributes
        assert attributes["updated_fields"] == ["allowed_models", "default_model"]
        assert attributes["previous_allowed_models"] == ["gpt-4"]
        assert attributes["new_allowed_models"] == ["gpt-4", "claude-opus"]
        assert attributes["previous_default_model"] == "gpt-4"
        assert attributes["new_default_model"] is None

    def test_saving_an_identical_override_records_nothing(self, activity_events):
        session = _make_session(user_project=MagicMock(spec=UserProject), app=_app(["gpt-4"]))
        existing = _override_row("u1", ["gpt-4"], "gpt-4")

        with (
            patch(f"{REPO}.get_by_user_and_project", return_value=existing),
            patch(f"{REPO}.update", return_value=existing),
        ):
            UserModelOverrideService.set_override(
                session, "u1", "test-project", ["gpt-4"], default_model="gpt-4", actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

        activity_events.insert_strict.assert_not_called()

    def test_a_failed_audit_write_fails_the_change(self, activity_events):
        """The event shares the change's transaction: a lost audit row must not leave an unaudited change."""
        session = _make_session(user_project=MagicMock(spec=UserProject), app=_app(["gpt-4"]))
        activity_events.insert_strict.side_effect = RuntimeError("audit write failed")

        with (
            patch(f"{REPO}.get_by_user_and_project", return_value=None),
            patch(f"{REPO}.create", return_value=_override_row("u1", ["gpt-4"])),
        ):
            with pytest.raises(RuntimeError, match="audit write failed"):
                UserModelOverrideService.set_override(
                    session, "u1", "test-project", ["gpt-4"], actor_id=ACTOR, actor_email=ACTOR_EMAIL
                )

    def test_deleting_an_override_records_what_was_removed(self, activity_events):
        deleted = _override_row("u1", ["gpt-4", "claude-opus"], "claude-opus")

        with patch(f"{REPO}.delete", return_value=deleted):
            UserModelOverrideService.delete_override(
                MagicMock(), "u1", "test-project", actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

        event = _set_event(activity_events)
        assert event.event_type == "user.model_override.removed"
        assert (event.entity_type, event.entity_id, event.actor_id) == ("user", "u1", ACTOR)
        assert event.attributes == {
            "project_name": "test-project",
            "previous_allowed_models": ["gpt-4", "claude-opus"],
            "previous_default_model": "claude-opus",
        }

    def test_deleting_a_missing_override_records_nothing(self, activity_events):
        with patch(f"{REPO}.delete", return_value=None):
            result = UserModelOverrideService.delete_override(
                MagicMock(), "u1", "test-project", actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

        assert result is None
        activity_events.insert_strict.assert_not_called()

    def test_batch_grant_records_one_event_per_changed_user(self, activity_events):
        user_ids = ["u1", "u2", "u3"]
        session = _make_session(app=_app(["gpt-4"]), users=user_ids, members=user_ids)
        unchanged = _override_row("u2", ["gpt-4"], None)

        def upsert(_session, _project, ids, allowed_models, default_model, existing):
            return [existing.get(u) or _override_row(u, allowed_models, default_model) for u in ids]

        with (
            patch(f"{REPO}.get_by_users_and_project", return_value={"u2": unchanged}),
            patch(f"{REPO}.upsert_many", side_effect=upsert),
        ):
            UserModelOverrideService.batch_set_overrides(
                session, "test-project", user_ids, "grant", ["gpt-4"], actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

        activity_events.insert_many_strict.assert_called_once()
        events = activity_events.insert_many_strict.call_args.args[0]
        assert [e.entity_id for e in events] == ["u1", "u3"]
        assert {e.event_type for e in events} == {"user.model_override.set"}
        assert {e.actor_id for e in events} == {ACTOR}

    def test_batch_grant_event_carries_the_values_the_user_had_before(self, activity_events):
        session = _make_session(app=_app(["gpt-4"]), users=["u1"], members=["u1"])
        row = _override_row("u1", ["claude-opus"], "claude-opus")

        def upsert(_session, _project, ids, allowed_models, default_model, existing):
            row.allowed_models, row.default_model = allowed_models, default_model
            return [row]

        with (
            patch(f"{REPO}.get_by_users_and_project", return_value={"u1": row}),
            patch(f"{REPO}.upsert_many", side_effect=upsert),
        ):
            UserModelOverrideService.batch_set_overrides(
                session, "test-project", ["u1"], "grant", ["gpt-4"], actor_id=ACTOR, actor_email=ACTOR_EMAIL
            )

        attributes = activity_events.insert_many_strict.call_args.args[0][0].attributes
        assert attributes["previous_allowed_models"] == ["claude-opus"]
        assert attributes["new_allowed_models"] == ["gpt-4"]
        assert attributes["previous_default_model"] == "claude-opus"

    def test_batch_revoke_records_only_the_overrides_that_existed(self, activity_events):
        existing = {"u1": _override_row("u1", ["gpt-4"]), "u3": _override_row("u3", ["claude-opus"], "claude-opus")}

        with (
            patch(f"{REPO}.get_by_users_and_project", return_value=existing),
            patch(f"{REPO}.delete_many"),
        ):
            UserModelOverrideService.batch_set_overrides(
                _make_session(app=_app(["gpt-4"])),
                "test-project",
                ["u1", "u2", "u3"],
                "revoke",
                actor_id=ACTOR,
                actor_email=ACTOR_EMAIL,
            )

        events = activity_events.insert_many_strict.call_args.args[0]
        assert [e.entity_id for e in events] == ["u1", "u3"]
        assert {e.event_type for e in events} == {"user.model_override.removed"}
        assert events[1].attributes["previous_default_model"] == "claude-opus"


DEPRECATED = "old-model"
DEPRECATED_DEPLOYMENT = "old-model-deployment"
SERVICE = "codemie.service.user.user_model_override_service"


@pytest.fixture
def deprecated_model():
    """A catalog where DEPRECATED, also named DEPRECATED_DEPLOYMENT, is deprecated and every other model is selectable."""
    names = {DEPRECATED, DEPRECATED_DEPLOYMENT}

    def aliases(model):
        return set(names) if model in names else {model}

    def assert_selectable(model):
        if model in names:
            raise ModelNotSelectableError(model, "model is deprecated")

    with (
        patch(f"{SERVICE}.model_aliases", side_effect=aliases),
        patch(f"{SERVICE}.llm_service.assert_model_selectable", side_effect=assert_selectable),
    ):
        yield


@pytest.fixture
def repo():
    """The override repository's writes, replaced so a test can assert whether anything was written."""
    with (
        patch(f"{REPO}.create") as create,
        patch(f"{REPO}.update") as update,
        patch(f"{REPO}.upsert_many", return_value=[]) as upsert_many,
    ):
        yield MagicMock(create=create, update=update, upsert_many=upsert_many)


@pytest.mark.usefixtures("deprecated_model")
class TestDeprecatedModels:
    """A deprecated model stays where it is already selected but cannot be added to an override."""

    @staticmethod
    def _set(allowed_models, default_model=None, existing=None):
        session = _make_session(user_project=MagicMock(spec=UserProject), app=_app(["gpt-4"]))
        with patch(f"{REPO}.get_by_user_and_project", return_value=existing):
            UserModelOverrideService.set_override(
                session,
                "u1",
                "test-project",
                allowed_models,
                default_model=default_model,
                actor_id=ACTOR,
                actor_email=ACTOR_EMAIL,
            )

    @staticmethod
    def _grant(user_ids, existing):
        session = _make_session(app=_app(["gpt-4"]), users=user_ids, members=user_ids)
        with patch(f"{REPO}.get_by_users_and_project", return_value=existing):
            UserModelOverrideService.batch_set_overrides(
                session,
                "test-project",
                user_ids,
                "grant",
                ["gpt-4", DEPRECATED],
                actor_id=ACTOR,
                actor_email=ACTOR_EMAIL,
            )

    def test_a_new_deprecated_model_is_rejected(self, repo):
        with pytest.raises(ValueError, match=DEPRECATED):
            self._set(["gpt-4", DEPRECATED])

        repo.create.assert_not_called()

    def test_a_new_deprecated_default_is_rejected(self, repo):
        with pytest.raises(ValueError, match="Deprecated"):
            self._set(["gpt-4", DEPRECATED], default_model=DEPRECATED)

        repo.create.assert_not_called()

    def test_a_deprecated_model_the_override_already_lists_is_kept(self, repo):
        existing = _override_row("u1", ["claude-opus", DEPRECATED], DEPRECATED)

        self._set(["gpt-4", DEPRECATED], default_model=DEPRECATED, existing=existing)

        repo.update.assert_called_once()

    def test_a_deprecated_model_kept_under_its_other_catalog_name(self, repo):
        self._set(["gpt-4", DEPRECATED], existing=_override_row("u1", [DEPRECATED_DEPLOYMENT]))

        repo.update.assert_called_once()

    def test_an_existing_override_without_the_model_does_not_keep_it(self, repo):
        with pytest.raises(ValueError, match=DEPRECATED):
            self._set([DEPRECATED], existing=_override_row("u1", ["claude-opus"]))

        repo.update.assert_not_called()

    def test_batch_keeps_a_deprecated_model_every_user_already_lists(self, repo):
        existing = {"u1": _override_row("u1", [DEPRECATED]), "u2": _override_row("u2", ["gpt-4", DEPRECATED])}

        self._grant(["u1", "u2"], existing)

        repo.upsert_many.assert_called_once()

    def test_batch_keeps_a_deprecated_model_users_list_under_different_names(self, repo):
        existing = {"u1": _override_row("u1", [DEPRECATED]), "u2": _override_row("u2", [DEPRECATED_DEPLOYMENT])}

        self._grant(["u1", "u2"], existing)

        repo.upsert_many.assert_called_once()

    @pytest.mark.parametrize(
        "existing",
        [
            pytest.param(
                {"u1": _override_row("u1", [DEPRECATED]), "u2": _override_row("u2", ["gpt-4"])}, id="not-listed"
            ),
            pytest.param({"u1": _override_row("u1", [DEPRECATED])}, id="no-override"),
        ],
    )
    def test_batch_rejects_a_deprecated_model_new_to_any_user(self, existing, repo, activity_events):
        with pytest.raises(ValueError, match=DEPRECATED):
            self._grant(["u1", "u2"], existing)

        repo.upsert_many.assert_not_called()
        activity_events.insert_many_strict.assert_not_called()
