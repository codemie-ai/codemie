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


from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.project_budget import BulkMemberOverrideItem
from codemie.service.activity.activity_models import BudgetManagementEvent
from codemie.service.budget.budget_enums import AllocationMode
from codemie.service.budget.budget_models import build_shared_project_budget_id
from codemie.service.budget.bulk_member_override import BulkMemberOverrideRunner
from codemie.service.budget.project_budget_service import ProjectBudgetService

HARD_LIMIT_EXCEEDED_MESSAGE = "Personal hard limits of project members cannot exceed the project hard limit."
SERVICE_MODULE = "codemie.service.budget.project_budget_service"
RUNNER_MODULE = "codemie.service.budget.bulk_member_override"


def _item(budget_id="b-1", user_id="u-a", max_budget=10.0, soft_budget=5.0, reason="why"):
    return BulkMemberOverrideItem(
        budget_id=budget_id,
        user_id=user_id,
        allocated_max_budget=max_budget,
        allocated_soft_budget=soft_budget,
        override_reason=reason,
    )


def _indexed(*items):
    return list(enumerate(items))


def _member(user_id, mode=AllocationMode.EQUAL.value, max_budget=0.0, soft_budget=0.0):
    return SimpleNamespace(
        id=f"alloc-{user_id}",
        user_id=user_id,
        allocation_mode=mode,
        allocated_max_budget=max_budget,
        allocated_soft_budget=soft_budget,
        shared_budget_id=None,
    )


def _budget(budget_id="b-1", max_budget=100.0, soft_budget=50.0):
    return SimpleNamespace(
        budget_id=budget_id,
        budget_type="project",
        budget_category="cli",
        max_budget=max_budget,
        soft_budget=soft_budget,
    )


def _loader(mapping):
    async def _get_project_budget(session, budget_id):
        value = mapping[budget_id]
        if isinstance(value, Exception):
            raise value
        return value

    return AsyncMock(side_effect=_get_project_budget)


def _not_found(budget_id):
    return ExtendedHTTPException(code=404, message=f"Project budget not found: {budget_id}")


@pytest.mark.asyncio
async def test_soft_above_max_fails_400_others_ok():
    service = ProjectBudgetService()
    members = [_member("u-a"), _member("u-b")]
    loader = _loader({"b-1": (_budget(), SimpleNamespace(project_name="p"), members)})

    with patch.object(service, "get_project_budget", new=loader):
        failed, accepted, _loaded = await BulkMemberOverrideRunner(service).check(
            AsyncMock(),
            _indexed(_item(user_id="u-a", max_budget=5.0, soft_budget=9.0), _item(user_id="u-b")),
        )

    assert set(failed) == {0}
    assert failed[0].status == "failed"
    assert failed[0].code == 400
    assert failed[0].message == "allocated_soft_budget must be <= allocated_max_budget"
    assert failed[0].budget_id == "b-1"
    assert failed[0].user_id == "u-a"
    assert [index for index, _ in accepted] == [1]


@pytest.mark.asyncio
async def test_missing_member_fails_404_with_message():
    service = ProjectBudgetService()
    loader = _loader({"b-1": (_budget(), SimpleNamespace(project_name="p"), [_member("u-a")])})

    with patch.object(service, "get_project_budget", new=loader):
        failed, accepted, _loaded = await BulkMemberOverrideRunner(service).check(
            AsyncMock(),
            _indexed(_item(user_id="ghost"), _item(user_id="u-a")),
        )

    assert failed[0].code == 404
    assert failed[0].message == "Member allocation not found for user 'ghost'"
    assert [index for index, _ in accepted] == [1]


@pytest.mark.asyncio
async def test_missing_budget_fails_404_all_items():
    service = ProjectBudgetService()
    loader = _loader(
        {
            "gone": _not_found("gone"),
            "b-1": (_budget(), SimpleNamespace(project_name="p"), [_member("u-a")]),
        }
    )

    with patch.object(service, "get_project_budget", new=loader):
        failed, accepted, _loaded = await BulkMemberOverrideRunner(service).check(
            AsyncMock(),
            _indexed(_item(budget_id="gone", user_id="u-a"), _item(budget_id="gone", user_id="u-b"), _item()),
        )

    assert set(failed) == {0, 1}
    assert all(result.code == 404 and result.message == "Project budget not found: gone" for result in failed.values())
    assert [index for index, _ in accepted] == [2]
    assert [call.args[1] for call in loader.await_args_list] == ["gone", "b-1"]


@pytest.mark.asyncio
async def test_order_dependent_example_accepts_all_three():
    service = ProjectBudgetService()
    members = [
        _member("u-a", AllocationMode.FIXED.value, 60.0, 30.0),
        _member("u-b"),
        _member("u-c"),
    ]
    loader = _loader({"b-1": (_budget(max_budget=100.0, soft_budget=50.0), SimpleNamespace(project_name="p"), members)})

    with patch.object(service, "get_project_budget", new=loader):
        failed, accepted, _loaded = await BulkMemberOverrideRunner(service).check(
            AsyncMock(),
            _indexed(
                _item(user_id="u-b", max_budget=50.0, soft_budget=25.0),
                _item(user_id="u-a", max_budget=10.0, soft_budget=5.0),
                _item(user_id="u-c", max_budget=30.0, soft_budget=15.0),
            ),
        )

    assert failed == {}
    assert [index for index, _ in accepted] == [0, 1, 2]
    loader.assert_awaited_once()


@pytest.mark.asyncio
async def test_final_state_over_limit_fails_all_remaining_400_with_message():
    service = ProjectBudgetService()
    members = [_member("u-a"), _member("u-b"), _member("u-c")]
    other_budget_members = [_member("u-z")]
    loader = _loader(
        {
            "b-1": (_budget(max_budget=100.0, soft_budget=50.0), SimpleNamespace(project_name="p"), members),
            "b-2": (_budget("b-2", 100.0, 50.0), SimpleNamespace(project_name="p"), other_budget_members),
        }
    )

    with patch.object(service, "get_project_budget", new=loader):
        failed, accepted, _loaded = await BulkMemberOverrideRunner(service).check(
            AsyncMock(),
            _indexed(
                _item(user_id="u-a", max_budget=80.0, soft_budget=10.0),
                _item(user_id="u-b", max_budget=80.0, soft_budget=10.0),
                _item(user_id="ghost"),
                _item(budget_id="b-2", user_id="u-z", max_budget=20.0, soft_budget=10.0),
            ),
        )

    assert set(failed) == {0, 1, 2}
    assert failed[0].code == 400 and failed[0].message == HARD_LIMIT_EXCEEDED_MESSAGE
    assert failed[1].code == 400 and failed[1].message == HARD_LIMIT_EXCEEDED_MESSAGE
    assert failed[2].code == 404
    assert [index for index, _ in accepted] == [3]


@pytest.mark.asyncio
async def test_phase1_does_not_mutate_orm_rows_or_write():
    service = ProjectBudgetService()
    members = [_member("u-a"), _member("u-b", AllocationMode.FIXED.value, 7.0, 3.0)]
    loader = _loader({"b-1": (_budget(), SimpleNamespace(project_name="p"), members)})
    repo = MagicMock()
    repo.update_member_override = AsyncMock()
    repo.update_member_budget_routing = AsyncMock()
    provider_factory = MagicMock()
    ensure_child = AsyncMock()
    session = AsyncMock()

    with (
        patch.object(service, "get_project_budget", new=loader),
        patch.object(service, "_ensure_override_child_budget", new=ensure_child),
        patch(f"{RUNNER_MODULE}.project_member_budget_assignment_repository", repo),
        patch(f"{SERVICE_MODULE}.get_active_provider", provider_factory),
    ):
        failed, accepted, _loaded = await BulkMemberOverrideRunner(service).check(
            session,
            _indexed(
                _item(user_id="u-a", max_budget=20.0, soft_budget=10.0),
                _item(user_id="u-b", max_budget=9.0, soft_budget=4.0),
            ),
        )

    assert failed == {}
    assert len(accepted) == 2
    assert members[0].allocation_mode == AllocationMode.EQUAL.value
    assert members[0].allocated_max_budget == 0.0
    assert members[1].allocated_max_budget == 7.0
    assert members[1].allocated_soft_budget == 3.0
    repo.update_member_override.assert_not_awaited()
    repo.update_member_budget_routing.assert_not_awaited()
    ensure_child.assert_not_awaited()
    provider_factory.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()


ASSIGNMENT = SimpleNamespace(project_name="proj-a", budget_category="cli")


def _fixed(user_id, max_budget, soft_budget):
    return _member(user_id, AllocationMode.FIXED.value, max_budget, soft_budget)


@contextmanager
def _bulk_harness(loaded, reloaded=None):
    service = ProjectBudgetService()
    session = AsyncMock()
    calls = []
    budgets = {budget_id: value[0] for budget_id, value in loaded.items() if isinstance(value, tuple)}
    loader = _loader(loaded)
    reloaded = reloaded or {}

    async def _reload(session_arg, budget_id, populate_existing=False):
        calls.append(("reload", budget_id, populate_existing))
        return reloaded[budget_id]

    async def _flush():
        calls.append(("flush",))

    async def _refresh(obj):
        calls.append(("refresh", obj.budget_id))

    async def _rebalance(session_arg, budget_id, actor_id):
        calls.append(("rebalance", budget_id, actor_id))

    session.flush = AsyncMock(side_effect=_flush)
    session.refresh = AsyncMock(side_effect=_refresh)
    repo = MagicMock()
    repo.update_member_override = AsyncMock(
        side_effect=lambda session_arg, **kw: SimpleNamespace(id=f"alloc-{kw['user_id']}", shared_budget_id=None)
    )
    repo.update_member_budget_routing = AsyncMock(
        side_effect=lambda session_arg, **kw: SimpleNamespace(id=kw["allocation_id"])
    )
    repo.get_active_by_budget_id = AsyncMock(side_effect=_reload)
    ensure_child = AsyncMock(side_effect=lambda session_arg, **kw: SimpleNamespace(budget_id=f"ovr-{kw['user_id']}"))
    rebalance = AsyncMock(side_effect=_rebalance)
    insert = AsyncMock()
    provider_factory = MagicMock()
    with (
        patch.object(service, "get_project_budget", new=loader),
        patch.object(service, "_ensure_override_child_budget", new=ensure_child),
        patch.object(service, "rebalance_project_budget", new=rebalance),
        patch(f"{RUNNER_MODULE}.project_member_budget_assignment_repository", repo),
        patch(f"{RUNNER_MODULE}.activity_event_repository.async_insert", new=insert),
        patch(f"{SERVICE_MODULE}.get_active_provider", provider_factory),
        patch(f"{RUNNER_MODULE}.logger") as logger,
    ):
        yield SimpleNamespace(
            service=service,
            loader=loader,
            session=session,
            calls=calls,
            budgets=budgets,
            repo=repo,
            ensure_child=ensure_child,
            rebalance=rebalance,
            insert=insert,
            provider_factory=provider_factory,
            logger=logger,
        )


def _two_budget_setup():
    b1, b2 = _budget("b-1"), _budget("b-2")
    loaded = {
        "b-1": (b1, ASSIGNMENT, [_member("u-a"), _member("u-b")]),
        "b-2": (b2, ASSIGNMENT, [_member("u-c")]),
    }
    reloaded = {
        "b-1": [_fixed("u-a", 10.0, 5.0), _member("u-b")],
        "b-2": [_fixed("u-c", 10.0, 5.0)],
    }
    return loaded, reloaded


@pytest.mark.asyncio
async def test_writes_use_same_args_as_patch_and_null_reason_clears_and_sets_fixed():
    loaded = {"b-1": (_budget(), ASSIGNMENT, [_member("u-a")])}
    reloaded = {"b-1": [_fixed("u-a", 12.0, 6.0)]}
    with _bulk_harness(loaded, reloaded) as h:
        results = await h.service.bulk_override_member_allocations(
            h.session, _indexed(_item(user_id="u-a", max_budget=12.0, soft_budget=6.0, reason=None)), "actor-1"
        )

    assert results[0].status == "ok"
    h.repo.update_member_override.assert_awaited_once_with(
        h.session,
        budget_id="b-1",
        user_id="u-a",
        allocated_max_budget=12.0,
        allocated_soft_budget=6.0,
        override_reason=None,
        assigned_by="actor-1",
    )
    h.ensure_child.assert_awaited_once_with(
        h.session,
        main_budget=h.budgets["b-1"],
        project_name="proj-a",
        user_id="u-a",
        actor_id="actor-1",
        allocated_soft_budget=6.0,
        allocated_max_budget=12.0,
    )
    h.repo.update_member_budget_routing.assert_awaited_once_with(
        h.session,
        allocation_id="alloc-u-a",
        shared_budget_id=build_shared_project_budget_id("b-1"),
        override_budget_id="ovr-u-a",
        effective_budget_id="ovr-u-a",
        allocation_mode=AllocationMode.FIXED.value,
        override_reason=None,
    )


@pytest.mark.asyncio
async def test_repeated_user_later_wins_each_item_gets_event():
    loaded = {"b-1": (_budget(), ASSIGNMENT, [_member("u-a")])}
    reloaded = {"b-1": [_fixed("u-a", 30.0, 15.0)]}
    with _bulk_harness(loaded, reloaded) as h:
        results = await h.service.bulk_override_member_allocations(
            h.session,
            _indexed(
                _item(user_id="u-a", max_budget=20.0, soft_budget=10.0),
                _item(user_id="u-a", max_budget=30.0, soft_budget=15.0),
            ),
            "actor-1",
        )

    assert [results[0].status, results[1].status] == ["ok", "ok"]
    written = [call.kwargs["allocated_max_budget"] for call in h.repo.update_member_override.await_args_list]
    assert written == [20.0, 30.0]
    attributes = [call.args[0].attributes for call in h.insert.await_args_list]
    assert [a["allocated_max_budget"] for a in attributes] == [20.0, 30.0]
    h.rebalance.assert_awaited_once()


@pytest.mark.asyncio
async def test_each_budget_loaded_once_across_phases():
    loaded, reloaded = _two_budget_setup()
    with _bulk_harness(loaded, reloaded) as h:
        await h.service.bulk_override_member_allocations(
            h.session,
            _indexed(
                _item(budget_id="b-1", user_id="u-a"),
                _item(budget_id="b-2", user_id="u-c"),
                _item(budget_id="b-1", user_id="u-b"),
            ),
            "actor-1",
        )

    assert [call.args[1] for call in h.loader.await_args_list] == ["b-1", "b-2"]


@pytest.mark.asyncio
async def test_rebalance_once_per_touched_budget_none_for_failed_budget():
    loaded, reloaded = _two_budget_setup()
    loaded["b-3"] = ExtendedHTTPException(code=404, message="Project budget not found: b-3")
    with _bulk_harness(loaded, reloaded) as h:
        results = await h.service.bulk_override_member_allocations(
            h.session,
            _indexed(
                _item(budget_id="b-1", user_id="u-a"),
                _item(budget_id="b-3", user_id="u-x"),
                _item(budget_id="b-2", user_id="u-c"),
                _item(budget_id="b-1", user_id="u-b"),
            ),
            "actor-1",
        )

    assert [results[i].status for i in range(4)] == ["ok", "failed", "ok", "ok"]
    assert results[1].code == 404
    assert [c for c in h.calls if c[0] == "rebalance"] == [
        ("rebalance", "b-1", "actor-1"),
        ("rebalance", "b-2", "actor-1"),
    ]


@pytest.mark.asyncio
async def test_recheck_runs_once_per_touched_budget_with_populate_existing_and_flush_first():
    loaded, reloaded = _two_budget_setup()
    with _bulk_harness(loaded, reloaded) as h:
        await h.service.bulk_override_member_allocations(
            h.session,
            _indexed(
                _item(budget_id="b-1", user_id="u-a"),
                _item(budget_id="b-2", user_id="u-c"),
                _item(budget_id="b-1", user_id="u-b"),
            ),
            "actor-1",
        )

    assert h.calls == [
        ("flush",),
        ("reload", "b-1", True),
        ("refresh", "b-1"),
        ("reload", "b-2", True),
        ("refresh", "b-2"),
        ("rebalance", "b-1", "actor-1"),
        ("rebalance", "b-2", "actor-1"),
    ]


@pytest.mark.asyncio
async def test_recheck_failure_second_budget_raises_400_no_rebalance_no_provider():
    loaded, reloaded = _two_budget_setup()
    reloaded["b-2"] = [_fixed("u-c", 500.0, 5.0)]
    with _bulk_harness(loaded, reloaded) as h:
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await h.service.bulk_override_member_allocations(
                h.session,
                _indexed(_item(budget_id="b-1", user_id="u-a"), _item(budget_id="b-2", user_id="u-c")),
                "actor-1",
            )

    assert exc_info.value.code == 400
    assert exc_info.value.message == HARD_LIMIT_EXCEEDED_MESSAGE
    h.rebalance.assert_not_awaited()
    h.provider_factory.assert_not_called()
    h.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_reloaded_amounts_differ_from_phase1_objects_raises_400_no_rebalance():
    loaded = {"b-1": (_budget(max_budget=100.0, soft_budget=50.0), ASSIGNMENT, [_member("u-a"), _member("u-b")])}
    reloaded = {"b-1": [_fixed("u-a", 10.0, 5.0), _fixed("u-b", 95.0, 5.0)]}
    with _bulk_harness(loaded, reloaded) as h:
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await h.service.bulk_override_member_allocations(
                h.session, _indexed(_item(user_id="u-a", max_budget=10.0, soft_budget=5.0)), "actor-1"
            )

    assert exc_info.value.code == 400
    h.rebalance.assert_not_awaited()
    h.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_event_and_log_only_for_accepted_items():
    loaded = {"b-1": (_budget(), ASSIGNMENT, [_member("u-a"), _member("u-b")])}
    reloaded = {"b-1": [_fixed("u-a", 10.0, 5.0), _member("u-b")]}
    with _bulk_harness(loaded, reloaded) as h:
        results = await h.service.bulk_override_member_allocations(
            h.session,
            _indexed(
                _item(user_id="u-a"),
                _item(user_id="ghost"),
                _item(user_id="u-b", max_budget=1.0, soft_budget=2.0),
            ),
            "actor-1",
        )

    assert [results[i].status for i in range(3)] == ["ok", "failed", "failed"]
    assert h.insert.await_count == 1
    event = h.insert.await_args.args[0]
    assert event.event_type == BudgetManagementEvent.MEMBER_ALLOCATION_OVERRIDDEN
    assert event.entity_id == "b-1"
    assert event.actor_id == "actor-1"
    assert event.attributes == {"user_id": "u-a", "allocated_max_budget": 10.0, "allocated_soft_budget": 5.0}
    messages = [call.args[0] for call in h.logger.info.call_args_list]
    assert len([m for m in messages if "member_allocation_override_completed" in m]) == 1


@pytest.mark.asyncio
async def test_all_items_fail_returns_all_failed_no_writes_rebalance_provider_events():
    loaded = {
        "b-1": (_budget(), ASSIGNMENT, [_member("u-a")]),
        "gone": ExtendedHTTPException(code=404, message="Project budget not found: gone"),
    }
    with _bulk_harness(loaded) as h:
        results = await h.service.bulk_override_member_allocations(
            h.session,
            _indexed(_item(user_id="ghost"), _item(budget_id="gone", user_id="u-a")),
            "actor-1",
        )

    assert sorted(results) == [0, 1]
    assert all(result.status == "failed" for result in results.values())
    h.repo.update_member_override.assert_not_awaited()
    h.ensure_child.assert_not_awaited()
    h.repo.update_member_budget_routing.assert_not_awaited()
    h.session.flush.assert_not_awaited()
    h.rebalance.assert_not_awaited()
    h.provider_factory.assert_not_called()
    h.insert.assert_not_awaited()
