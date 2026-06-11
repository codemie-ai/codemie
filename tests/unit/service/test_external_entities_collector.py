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

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import NotFoundException
from codemie.core.workflow_models.workflow_models import WorkflowAssistant
from codemie.service.external_entities_collector import WorkflowExternalEntitiesCollector


@pytest.fixture
def collector() -> WorkflowExternalEntitiesCollector:
    return WorkflowExternalEntitiesCollector()


@pytest.fixture
def mock_user() -> MagicMock:
    user = MagicMock()
    user.id = "u-1"
    return user


def _make_workflow(assistants: list | None = None, tools: list | None = None) -> MagicMock:
    wf = MagicMock()
    wf.assistants = assistants or []
    wf.tools = tools or []
    return wf


def _make_virtual_step(skill_ids: list[str] | None = None) -> MagicMock:
    step = MagicMock(spec=WorkflowAssistant)
    step.assistant_id = None
    step.skill_ids = skill_ids or []
    return step


def _make_external_step(assistant_id: str) -> MagicMock:
    step = MagicMock(spec=WorkflowAssistant)
    step.assistant_id = assistant_id
    return step


def _make_skill(skill_id: str) -> MagicMock:
    skill = MagicMock()
    skill.id = skill_id
    skill.skill_ids = []
    return skill


def _make_assistant(
    assistant_id: str, skill_ids: list[str] | None = None, assistant_ids: list[str] | None = None
) -> MagicMock:
    assistant = MagicMock()
    assistant.id = assistant_id
    assistant.skill_ids = skill_ids or []
    assistant.assistant_ids = assistant_ids or []
    return assistant


# ---------------------------------------------------------------------------
# collect_for_workflow — empty / no external entities
# ---------------------------------------------------------------------------


def test_collect_for_workflow_returns_empty_for_empty_workflow(
    collector: WorkflowExternalEntitiesCollector, mock_user: MagicMock
) -> None:
    workflow = _make_workflow()
    assistants, skills = collector.collect_for_workflow(workflow, mock_user)
    assert assistants == []
    assert skills == []


def test_collect_for_workflow_returns_empty_for_virtual_step_without_skills(
    collector: WorkflowExternalEntitiesCollector, mock_user: MagicMock
) -> None:
    step = _make_virtual_step(skill_ids=[])
    workflow = _make_workflow(assistants=[step])
    assistants, skills = collector.collect_for_workflow(workflow, mock_user)
    assert assistants == []
    assert skills == []


# ---------------------------------------------------------------------------
# collect_for_workflow — virtual step skill collection
# ---------------------------------------------------------------------------


def test_collect_for_workflow_collects_skills_from_virtual_step(
    collector: WorkflowExternalEntitiesCollector, mock_user: MagicMock
) -> None:
    step = _make_virtual_step(skill_ids=["sk-1"])
    workflow = _make_workflow(assistants=[step])
    fake_skill = _make_skill("sk-1")

    with patch("codemie.service.skill_service.SkillService.get_skills_by_ids", return_value=[fake_skill]):
        assistants, skills = collector.collect_for_workflow(workflow, mock_user)

    assert assistants == []
    assert len(skills) == 1
    assert skills[0].id == "sk-1"


def test_collect_for_workflow_deduplicates_skill_ids_across_steps(
    collector: WorkflowExternalEntitiesCollector, mock_user: MagicMock
) -> None:
    step1 = _make_virtual_step(skill_ids=["sk-1"])
    step2 = _make_virtual_step(skill_ids=["sk-1"])
    workflow = _make_workflow(assistants=[step1, step2])
    fake_skill = _make_skill("sk-1")

    with patch("codemie.service.skill_service.SkillService.get_skills_by_ids", return_value=[fake_skill]) as mock_get:
        _, skills = collector.collect_for_workflow(workflow, mock_user)

    # skill should be fetched only once and deduped
    assert len(skills) == 1
    fetched_ids = mock_get.call_args[0][0]
    assert fetched_ids == ["sk-1"]


# ---------------------------------------------------------------------------
# collect_for_workflow — external assistant steps
# ---------------------------------------------------------------------------


def test_collect_for_workflow_collects_external_assistant(
    collector: WorkflowExternalEntitiesCollector, mock_user: MagicMock
) -> None:
    step = _make_external_step("asst-1")
    workflow = _make_workflow(assistants=[step])
    fake_assistant = _make_assistant("asst-1")

    with patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", return_value=[fake_assistant]):
        assistants, skills = collector.collect_for_workflow(workflow, mock_user)

    assert len(assistants) == 1
    assert assistants[0].id == "asst-1"
    assert skills == []


def test_collect_for_workflow_deduplicates_same_assistant_id_across_steps(
    collector: WorkflowExternalEntitiesCollector, mock_user: MagicMock
) -> None:
    step1 = _make_external_step("asst-1")
    step2 = _make_external_step("asst-1")
    workflow = _make_workflow(assistants=[step1, step2])
    fake_assistant = _make_assistant("asst-1")

    with patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", return_value=[fake_assistant]) as mock_get:
        assistants, _ = collector.collect_for_workflow(workflow, mock_user)

    assert len(assistants) == 1
    # dedup happens before the fetch — only one call with one ID
    fetched_ids = mock_get.call_args[0][1]
    assert fetched_ids == ["asst-1"]


# ---------------------------------------------------------------------------
# collect_for_workflow — recursive sub-assistant collection
# ---------------------------------------------------------------------------


def test_collect_for_workflow_recurses_into_sub_assistants(
    collector: WorkflowExternalEntitiesCollector, mock_user: MagicMock
) -> None:
    step = _make_external_step("asst-1")
    workflow = _make_workflow(assistants=[step])

    parent = _make_assistant("asst-1", assistant_ids=["asst-2"])
    child = _make_assistant("asst-2")

    def fake_get_by_ids(user: MagicMock, ids: list[str], parent_assistant: None) -> list[MagicMock]:
        mapping = {"asst-1": parent, "asst-2": child}
        return [mapping[i] for i in ids if i in mapping]

    with patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", side_effect=fake_get_by_ids):
        assistants, skills = collector.collect_for_workflow(workflow, mock_user)

    assert len(assistants) == 2
    ids = {a.id for a in assistants}
    assert ids == {"asst-1", "asst-2"}


def test_collect_for_workflow_collects_skills_from_external_assistant(
    collector: WorkflowExternalEntitiesCollector, mock_user: MagicMock
) -> None:
    step = _make_external_step("asst-1")
    workflow = _make_workflow(assistants=[step])
    assistant = _make_assistant("asst-1", skill_ids=["sk-1"])
    fake_skill = _make_skill("sk-1")

    with (
        patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", return_value=[assistant]),
        patch("codemie.service.skill_service.SkillService.get_skills_by_ids", return_value=[fake_skill]),
    ):
        assistants, skills = collector.collect_for_workflow(workflow, mock_user)

    assert len(assistants) == 1
    assert len(skills) == 1
    assert skills[0].id == "sk-1"


def test_collect_for_workflow_deduplicates_skill_across_assistant_and_virtual_step(
    collector: WorkflowExternalEntitiesCollector, mock_user: MagicMock
) -> None:
    virtual = _make_virtual_step(skill_ids=["sk-1"])
    external = _make_external_step("asst-1")
    workflow = _make_workflow(assistants=[virtual, external])
    assistant = _make_assistant("asst-1", skill_ids=["sk-1"])
    fake_skill = _make_skill("sk-1")

    skill_calls: list[list[str]] = []

    def fake_get_skills(ids: list[str], user: MagicMock) -> list[MagicMock]:
        skill_calls.append(list(ids))
        return [fake_skill] if "sk-1" in ids else []

    with (
        patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", return_value=[assistant]),
        patch("codemie.service.skill_service.SkillService.get_skills_by_ids", side_effect=fake_get_skills),
    ):
        _, skills = collector.collect_for_workflow(workflow, mock_user)

    # sk-1 should appear in only one batch (fetched once), not twice
    all_fetched = [sid for batch in skill_calls for sid in batch]
    assert all_fetched.count("sk-1") == 1
    assert len(skills) == 1


# ---------------------------------------------------------------------------
# collect_for_workflow — NotFoundException propagation
# ---------------------------------------------------------------------------


def test_collect_for_workflow_raises_not_found_for_missing_skill(
    collector: WorkflowExternalEntitiesCollector, mock_user: MagicMock
) -> None:
    step = _make_virtual_step(skill_ids=["sk-missing"])
    workflow = _make_workflow(assistants=[step])

    with patch("codemie.service.skill_service.SkillService.get_skills_by_ids", return_value=[]):
        with pytest.raises(NotFoundException) as exc_info:
            collector.collect_for_workflow(workflow, mock_user)

    assert "sk-missing" in str(exc_info.value)


def test_collect_for_workflow_raises_not_found_for_missing_assistant(
    collector: WorkflowExternalEntitiesCollector, mock_user: MagicMock
) -> None:
    step = _make_external_step("asst-missing")
    workflow = _make_workflow(assistants=[step])

    with patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", return_value=[]):
        with pytest.raises(NotFoundException) as exc_info:
            collector.collect_for_workflow(workflow, mock_user)

    assert "asst-missing" in str(exc_info.value)


# ---------------------------------------------------------------------------
# collect_for_workflow — None guards
# ---------------------------------------------------------------------------


def test_collect_for_workflow_handles_none_assistants(
    collector: WorkflowExternalEntitiesCollector, mock_user: MagicMock
) -> None:
    workflow = _make_workflow(assistants=None, tools=[])
    assistants, skills = collector.collect_for_workflow(workflow, mock_user)
    assert assistants == []
    assert skills == []


def test_collect_for_workflow_handles_none_skill_ids_on_virtual_step(
    collector: WorkflowExternalEntitiesCollector, mock_user: MagicMock
) -> None:
    step = _make_virtual_step(skill_ids=None)
    workflow = _make_workflow(assistants=[step])
    assistants, skills = collector.collect_for_workflow(workflow, mock_user)
    assert assistants == []
    assert skills == []


# ---------------------------------------------------------------------------
# collect_for_workflow — NotFoundException in recursive paths
# ---------------------------------------------------------------------------


def test_collect_for_workflow_raises_not_found_for_missing_skill_on_external_assistant(
    collector: WorkflowExternalEntitiesCollector, mock_user: MagicMock
) -> None:
    step = _make_external_step("asst-1")
    workflow = _make_workflow(assistants=[step])
    assistant = _make_assistant("asst-1", skill_ids=["sk-missing"])

    with (
        patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", return_value=[assistant]),
        patch("codemie.service.skill_service.SkillService.get_skills_by_ids", return_value=[]),
    ):
        with pytest.raises(NotFoundException) as exc_info:
            collector.collect_for_workflow(workflow, mock_user)

    assert "sk-missing" in str(exc_info.value)


def test_collect_for_workflow_raises_not_found_for_missing_sub_assistant(
    collector: WorkflowExternalEntitiesCollector, mock_user: MagicMock
) -> None:
    step = _make_external_step("asst-1")
    workflow = _make_workflow(assistants=[step])
    parent = _make_assistant("asst-1", assistant_ids=["asst-missing"])

    def fake_get_by_ids(user: MagicMock, ids: list[str], parent_assistant: None) -> list[MagicMock]:
        if "asst-1" in ids:
            return [parent]
        return []

    with patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", side_effect=fake_get_by_ids):
        with pytest.raises(NotFoundException) as exc_info:
            collector.collect_for_workflow(workflow, mock_user)

    assert "asst-missing" in str(exc_info.value)


# ---------------------------------------------------------------------------
# collect_for_workflow — cross-level deduplication
# ---------------------------------------------------------------------------


def test_collect_for_workflow_deduplicates_assistant_across_step_and_sub_assistant(
    collector: WorkflowExternalEntitiesCollector, mock_user: MagicMock
) -> None:
    """asst-2 is both a direct workflow step and a sub-assistant of asst-1 — fetched only once."""
    step1 = _make_external_step("asst-1")
    step2 = _make_external_step("asst-2")
    workflow = _make_workflow(assistants=[step1, step2])

    parent = _make_assistant("asst-1", assistant_ids=["asst-2"])
    child = _make_assistant("asst-2")

    fetch_calls: list[list[str]] = []

    def fake_get_by_ids(user: MagicMock, ids: list[str], parent_assistant: None) -> list[MagicMock]:
        fetch_calls.append(list(ids))
        mapping = {"asst-1": parent, "asst-2": child}
        return [mapping[i] for i in ids if i in mapping]

    with patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", side_effect=fake_get_by_ids):
        assistants, _ = collector.collect_for_workflow(workflow, mock_user)

    assert len(assistants) == 2
    all_fetched = [aid for batch in fetch_calls for aid in batch]
    assert all_fetched.count("asst-2") == 1


def test_collect_for_workflow_deduplicates_skill_across_parent_and_child_assistant(
    collector: WorkflowExternalEntitiesCollector, mock_user: MagicMock
) -> None:
    """sk-1 appears on both parent and child assistant — fetched only once."""
    step = _make_external_step("asst-1")
    workflow = _make_workflow(assistants=[step])

    parent = _make_assistant("asst-1", skill_ids=["sk-1"], assistant_ids=["asst-2"])
    child = _make_assistant("asst-2", skill_ids=["sk-1"])
    fake_skill = _make_skill("sk-1")

    def fake_get_by_ids(user: MagicMock, ids: list[str], parent_assistant: None) -> list[MagicMock]:
        mapping = {"asst-1": parent, "asst-2": child}
        return [mapping[i] for i in ids if i in mapping]

    skill_calls: list[list[str]] = []

    def fake_get_skills(ids: list[str], user: MagicMock) -> list[MagicMock]:
        skill_calls.append(list(ids))
        return [fake_skill] if "sk-1" in ids else []

    with (
        patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", side_effect=fake_get_by_ids),
        patch("codemie.service.skill_service.SkillService.get_skills_by_ids", side_effect=fake_get_skills),
    ):
        _, skills = collector.collect_for_workflow(workflow, mock_user)

    all_fetched = [sid for batch in skill_calls for sid in batch]
    assert all_fetched.count("sk-1") == 1
    assert len(skills) == 1
