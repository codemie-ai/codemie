# Workflow AI Refine and Revert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `POST /v1/workflows/{id}/refine` (stateless AI-powered YAML suggestions) and `POST /v1/workflows/{id}/revert` (restore last history snapshot) to the workflow API.

**Architecture:** New `WorkflowGeneratorService` uses `PromptGeneratorChain` + LangChain structured output for stateless AI refinement; revert is a thin wrapper over existing `update_workflow` that reads `yaml_config_history[0]` and re-applies it. Both endpoints follow the `POST /v1/skills/refine` pattern for LLM context setup.

**Tech Stack:** FastAPI, Pydantic v2, LangChain `PromptGeneratorChain` (from `assistant_generator_service.py`), SQLModel + JSONB (`yaml_config_history`), pytest + `AsyncClient`.

## Global Constraints

- All new code must pass `make lint` (Ruff) without errors.
- No Alembic migration required — `change_type` is added to a JSONB column (`yaml_config_history`), deserialized as `Optional[str] = None` for existing rows.
- Import `PromptGeneratorChain` from `codemie.service.assistant_generator_service` — it lives there, not in a separate module.
- `WorkflowRefineDetails` (structured output model) belongs in the prompt template file, not the service file.
- Router endpoints use `raw_request: Request` to get `request_id = raw_request.state.uuid`.
- `set_logging_info` and `set_llm_context` are called at the top of every LLM-calling endpoint.
- `request_summary_manager.clear_summary(request_id)` must be called in the `finally` block of `WorkflowGeneratorService.refine_workflow`.
- `emit_llm_token_metric` on success, `send_log_metric` on failure — same as `SkillGeneratorService`.
- Tests use `patch("codemie.service.workflow_service.WorkflowService.<method>")` for service patches and `patch("codemie.service.workflow_generator_service.WorkflowGeneratorService.refine_workflow")` for generator patches.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `src/codemie/core/workflow_models/workflow_config.py:57` | Add `change_type: Optional[str] = None` to `YamlConfigHistory` |
| Modify | `src/codemie/service/monitoring/metrics_constants.py` | Add two new metric name constants |
| Modify | `src/codemie/service/monitoring/workflow_monitoring_service.py` | Add `send_workflow_ai_refine_metric` and `send_workflow_revert_metric` |
| Create | `src/codemie/templates/agents/workflow_generator_prompt.py` | `WORKFLOW_REFINE_TEMPLATE` + `WorkflowRefineDetails` structured-output model |
| Create | `src/codemie/rest_api/models/workflow_generator.py` | `WorkflowRefineRequest`, `WorkflowRefineResponse`, `WorkflowRevertRequest` |
| Create | `src/codemie/service/workflow_generator_service.py` | `WorkflowGeneratorService.refine_workflow()` |
| Modify | `src/codemie/service/workflow_service.py:651` | Add `change_type` param to `_update_workflow_values`; add `revert_workflow()` |
| Modify | `src/codemie/rest_api/routers/workflow.py` | Two new endpoints: `refine` and `revert` |
| Modify | `tests/codemie/service/test_workflow_service.py` | Tests for `revert_workflow` and `change_type` recording |
| Create | `tests/codemie/service/test_workflow_generator_service.py` | Unit tests for `WorkflowGeneratorService` |
| Modify | `tests/codemie/rest_api/routers/test_workflow.py` | Router tests for refine and revert endpoints |

---

### Task 1: Add `change_type` to `YamlConfigHistory`

**Files:**
- Modify: `src/codemie/core/workflow_models/workflow_config.py:57-61`
- Test: `tests/codemie/service/test_workflow_service.py` (new test added in Task 5)

**Interfaces:**
- Produces: `YamlConfigHistory(yaml_config, date, created_by, change_type=None)` — used in Tasks 5, 6, 7

**Test-first: yes — `test_yaml_config_history_default_change_type_is_none` (added in Task 5 alongside `revert` tests to keep commits cohesive)**

- [ ] **Step 1: Open the model file**

  File: `src/codemie/core/workflow_models/workflow_config.py`, lines 57–61.

  Current:
  ```python
  class YamlConfigHistory(BaseModel):
      yaml_config: str
      date: datetime
      created_by: Optional[UserEntity] = None
  ```

- [ ] **Step 2: Add the field**

  ```python
  class YamlConfigHistory(BaseModel):
      yaml_config: str
      date: datetime
      created_by: Optional[UserEntity] = None
      change_type: Optional[str] = None
  ```

- [ ] **Step 3: Verify lint passes**

  Run: `cd /Users/yevhen_slyva/codemie-dev/codemie && make lint`
  Expected: no errors.

- [ ] **Step 4: Commit**

  ```bash
  git add src/codemie/core/workflow_models/workflow_config.py
  git commit -m "feat(EPMCDME-12616): add change_type field to YamlConfigHistory"
  ```

---

### Task 2: Add metric constants and monitoring methods

**Files:**
- Modify: `src/codemie/service/monitoring/metrics_constants.py`
- Modify: `src/codemie/service/monitoring/workflow_monitoring_service.py`

**Interfaces:**
- Produces:
  - `WORKFLOW_AI_REFINE_TOTAL_METRIC = "codemie_workflow_ai_refine_total"` — imported in Task 4
  - `WORKFLOW_REVERT_TOTAL_METRIC = "codemie_workflow_revert_total"` — imported in Task 5
  - `WorkflowMonitoringService.send_workflow_ai_refine_metric(user_id, user_name, workflow_id, workflow_name, project, success, llm_model=None, mode=WorkflowMode.SEQUENTIAL)`
  - `WorkflowMonitoringService.send_workflow_revert_metric(user_id, user_name, workflow_id, workflow_name, project, success, mode=WorkflowMode.SEQUENTIAL)`

**Test-first: no** — monitoring methods follow an established pattern that is already tested by the existing metric infra. No new test file warranted here.

- [ ] **Step 1: Add constants to `metrics_constants.py`**

  Append after `WORKFLOW_OUTPUT_CHANGE_ERRORS_METRIC` (around line 54):
  ```python
  WORKFLOW_AI_REFINE_TOTAL_METRIC = "codemie_workflow_ai_refine_total"
  WORKFLOW_REVERT_TOTAL_METRIC = "codemie_workflow_revert_total"
  ```

- [ ] **Step 2: Add imports to `workflow_monitoring_service.py`**

  In `workflow_monitoring_service.py`, add to the existing `from codemie.service.monitoring.metrics_constants import MetricsAttributes` import:
  ```python
  from codemie.service.monitoring.metrics_constants import (
      MetricsAttributes,
      WORKFLOW_AI_REFINE_TOTAL_METRIC,
      WORKFLOW_REVERT_TOTAL_METRIC,
  )
  ```

- [ ] **Step 3: Add the two new methods to `WorkflowMonitoringService`**

  Append after `send_delete_workflow_metric` (before `_build_workflow_attributes`):

  ```python
  @classmethod
  def send_workflow_ai_refine_metric(
      cls,
      user_id: str,
      user_name: str,
      workflow_id: str,
      workflow_name: str,
      project: str,
      success: bool,
      llm_model: Optional[str] = None,
      mode: WorkflowMode = WorkflowMode.SEQUENTIAL,
      additional_attributes: Optional[dict] = None,
  ):
      attributes = cls._build_workflow_attributes(
          project, success, user_id, user_name, workflow_id, workflow_name, mode
      )
      if llm_model:
          attributes[MetricsAttributes.LLM_MODEL] = llm_model
      if additional_attributes:
          attributes.update(additional_attributes)
      cls.send_count_metric(name=WORKFLOW_AI_REFINE_TOTAL_METRIC, attributes=attributes)

  @classmethod
  def send_workflow_revert_metric(
      cls,
      user_id: str,
      user_name: str,
      workflow_id: str,
      workflow_name: str,
      project: str,
      success: bool,
      mode: WorkflowMode = WorkflowMode.SEQUENTIAL,
      additional_attributes: Optional[dict] = None,
  ):
      attributes = cls._build_workflow_attributes(
          project, success, user_id, user_name, workflow_id, workflow_name, mode
      )
      if additional_attributes:
          attributes.update(additional_attributes)
      cls.send_count_metric(name=WORKFLOW_REVERT_TOTAL_METRIC, attributes=attributes)
  ```

- [ ] **Step 4: Verify lint**

  Run: `make lint`
  Expected: no errors.

- [ ] **Step 5: Commit**

  ```bash
  git add src/codemie/service/monitoring/metrics_constants.py \
          src/codemie/service/monitoring/workflow_monitoring_service.py
  git commit -m "feat(EPMCDME-12616): add workflow_ai_refine and workflow_revert metric methods"
  ```

---

### Task 3: Prompt template and `WorkflowRefineDetails` model

**Files:**
- Create: `src/codemie/templates/agents/workflow_generator_prompt.py`

**Interfaces:**
- Produces:
  - `WORKFLOW_REFINE_TEMPLATE: PromptTemplate` (input variables: `yaml_config`, `refine_prompt`)
  - `WorkflowRefineDetails(BaseModel)` with field `yaml_config: str`
  - Both are imported in Task 4

**Test-first: no** — prompt templates contain no logic; exercised end-to-end by Task 4's service tests.

- [ ] **Step 1: Create the file**

  ```python
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

  from langchain_core.prompts import PromptTemplate
  from pydantic import BaseModel, Field


  class WorkflowRefineDetails(BaseModel):
      """Structured output from the workflow refine LLM chain."""

      yaml_config: str = Field(
          description="The complete revised workflow YAML. Must be valid YAML. No markdown fencing."
      )


  WORKFLOW_REFINE_TEMPLATE = PromptTemplate.from_template(
      """You are an expert workflow engineer. Your task is to improve a CodeMie workflow YAML configuration.

  Current workflow YAML:
  ```yaml
  {yaml_config}
  ```

  Refinement instructions:
  {refine_prompt}

  Rules:
  - Return the COMPLETE revised workflow YAML. Do not summarize or truncate.
  - Preserve the overall workflow structure (states, transitions, assistants).
  - Apply only the changes described in the refinement instructions. If instructions are empty, apply general improvements (clarity, robustness, best practices).
  - The output must be valid YAML. Do not wrap it in markdown code fences or add any commentary.
  - Do not change the workflow id, name, or project fields.
  """
  )
  ```

- [ ] **Step 2: Verify lint**

  Run: `make lint`
  Expected: no errors.

- [ ] **Step 3: Commit**

  ```bash
  git add src/codemie/templates/agents/workflow_generator_prompt.py
  git commit -m "feat(EPMCDME-12616): add workflow refine prompt template"
  ```

---

### Task 4: `WorkflowGeneratorService` with `refine_workflow()`

**Files:**
- Create: `src/codemie/service/workflow_generator_service.py`
- Create: `tests/codemie/service/test_workflow_generator_service.py`

**Interfaces:**
- Consumes:
  - `PromptGeneratorChain.from_prompt_template(WORKFLOW_REFINE_TEMPLATE, request_id, llm_model)` from `codemie.service.assistant_generator_service`
  - `WorkflowRefineDetails` from `codemie.templates.agents.workflow_generator_prompt`
  - `WORKFLOW_AI_REFINE_TOTAL_METRIC` from `codemie.service.monitoring.metrics_constants`
  - `emit_llm_token_metric`, `send_log_metric` from `codemie.service.monitoring.base_monitoring_service`
  - `request_summary_manager` from `codemie.service.request_summary_manager`
- Produces:
  - `WorkflowGeneratorService.refine_workflow(yaml_config, refine_prompt, user, llm_model, request_id) -> WorkflowRefineResponse`
  - `WorkflowRefineResponse` is imported from `codemie.rest_api.models.workflow_generator` (created in Task 5 — Task 4 imports it lazily or Task 5 is created first; see note below)

> **Note on ordering:** `WorkflowGeneratorService.refine_workflow()` returns `WorkflowRefineResponse` from `codemie.rest_api.models.workflow_generator`. Create the models file (Task 5) before writing this service, OR use an inline `dict` return and patch the import in tests. The plan steps below assume Task 5's models file is created in parallel — if following sequentially, create the models file from Task 5 Step 1 first, then come back and write this service.

**Test-first: yes — write tests in `test_workflow_generator_service.py` before writing the service**

- [ ] **Step 1: First create `src/codemie/rest_api/models/workflow_generator.py`** (Task 5's model file, needed here as a dependency)

  ```python
  # Copyright 2026 EPAM Systems, Inc. ("EPAM")
  #
  # Licensed under the Apache License, Version 2.0 (the "License")
  # ... (same Apache header)

  from typing import Optional
  from pydantic import BaseModel


  class WorkflowRefineRequest(BaseModel):
      yaml_config: str
      refine_prompt: Optional[str] = None
      llm_model: Optional[str] = None
      project: Optional[str] = None


  class WorkflowRefineResponse(BaseModel):
      yaml_config: str


  class WorkflowRevertRequest(BaseModel):
      project: Optional[str] = None
  ```

- [ ] **Step 2: Write failing tests in `tests/codemie/service/test_workflow_generator_service.py`**

  ```python
  # Copyright 2026 EPAM Systems, Inc. ("EPAM")
  # ... (Apache header)

  from unittest.mock import patch, MagicMock
  import pytest
  import yaml

  from codemie.core.exceptions import ExtendedHTTPException
  from codemie.rest_api.models.workflow_generator import WorkflowRefineResponse
  from codemie.rest_api.security.user import User

  EXAMPLE_PROJECT = "example_project"
  EXAMPLE_YAML = "name: my-workflow\nstates:\n  - id: state1\n"
  REFINED_YAML = "name: my-workflow\nstates:\n  - id: state1\n    description: improved\n"


  @pytest.fixture
  def user():
      return User(id="123", username="testuser", name="Test User", project_names=[EXAMPLE_PROJECT])


  @patch("codemie.service.workflow_generator_service.request_summary_manager")
  @patch("codemie.service.workflow_generator_service.emit_llm_token_metric")
  @patch("codemie.service.workflow_generator_service.PromptGeneratorChain")
  def test_refine_workflow_returns_revised_yaml(mock_chain_cls, mock_emit, mock_summary, user):
      mock_chain = MagicMock()
      mock_chain_cls.from_prompt_template.return_value = mock_chain
      from codemie.templates.agents.workflow_generator_prompt import WorkflowRefineDetails
      mock_chain.invoke_with_model.return_value = WorkflowRefineDetails(yaml_config=REFINED_YAML)

      from codemie.service.workflow_generator_service import WorkflowGeneratorService
      result = WorkflowGeneratorService.refine_workflow(
          yaml_config=EXAMPLE_YAML,
          refine_prompt="improve descriptions",
          user=user,
          llm_model="gpt-4o",
          request_id="req-1",
      )

      assert isinstance(result, WorkflowRefineResponse)
      assert result.yaml_config == REFINED_YAML
      mock_emit.assert_called_once()
      mock_summary.clear_summary.assert_called_once_with("req-1")


  @patch("codemie.service.workflow_generator_service.request_summary_manager")
  @patch("codemie.service.workflow_generator_service.send_log_metric")
  @patch("codemie.service.workflow_generator_service.PromptGeneratorChain")
  def test_refine_workflow_raises_on_invalid_yaml(mock_chain_cls, mock_send_log, mock_summary, user):
      mock_chain = MagicMock()
      mock_chain_cls.from_prompt_template.return_value = mock_chain
      from codemie.templates.agents.workflow_generator_prompt import WorkflowRefineDetails
      mock_chain.invoke_with_model.return_value = WorkflowRefineDetails(yaml_config=": invalid: yaml: [")

      from codemie.service.workflow_generator_service import WorkflowGeneratorService
      with pytest.raises(ExtendedHTTPException) as exc_info:
          WorkflowGeneratorService.refine_workflow(
              yaml_config=EXAMPLE_YAML,
              refine_prompt=None,
              user=user,
              llm_model=None,
              request_id="req-2",
          )
      assert exc_info.value.status_code == 400
      mock_summary.clear_summary.assert_called_once_with("req-2")


  @patch("codemie.service.workflow_generator_service.request_summary_manager")
  @patch("codemie.service.workflow_generator_service.send_log_metric")
  @patch("codemie.service.workflow_generator_service.PromptGeneratorChain")
  def test_refine_workflow_raises_500_on_chain_failure(mock_chain_cls, mock_send_log, mock_summary, user):
      mock_chain_cls.from_prompt_template.side_effect = RuntimeError("LLM unavailable")

      from codemie.service.workflow_generator_service import WorkflowGeneratorService
      with pytest.raises(ExtendedHTTPException) as exc_info:
          WorkflowGeneratorService.refine_workflow(
              yaml_config=EXAMPLE_YAML,
              refine_prompt=None,
              user=user,
              llm_model=None,
              request_id="req-3",
          )
      assert exc_info.value.status_code == 500
      mock_send_log.assert_called_once()
      mock_summary.clear_summary.assert_called_once_with("req-3")
  ```

- [ ] **Step 3: Run tests to confirm RED**

  Run: `cd /Users/yevhen_slyva/codemie-dev/codemie && python -m pytest tests/codemie/service/test_workflow_generator_service.py -v 2>&1 | tail -20`
  Expected: `ModuleNotFoundError` or `ImportError` — `workflow_generator_service` does not exist yet.

- [ ] **Step 4: Create `src/codemie/service/workflow_generator_service.py`**

  ```python
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

  from typing import Optional

  import yaml

  from codemie.configs.logger import current_user_email, logger, logging_user_id
  from codemie.core.dependecies import get_project_for_metric
  from codemie.core.exceptions import ExtendedHTTPException
  from codemie.rest_api.models.workflow_generator import WorkflowRefineResponse
  from codemie.rest_api.security.user import User
  from codemie.service.assistant_generator_service import PromptGeneratorChain
  from codemie.service.monitoring.base_monitoring_service import emit_llm_token_metric, send_log_metric
  from codemie.service.monitoring.metrics_constants import (
      WORKFLOW_AI_REFINE_TOTAL_METRIC,
      MetricsAttributes,
  )
  from codemie.service.request_summary_manager import request_summary_manager
  from codemie.templates.agents.workflow_generator_prompt import (
      WORKFLOW_REFINE_TEMPLATE,
      WorkflowRefineDetails,
  )

  _HELP_MESSAGE = "Try again with a different instruction or model."


  class WorkflowGeneratorService:
      @classmethod
      def refine_workflow(
          cls,
          yaml_config: str,
          refine_prompt: Optional[str],
          user: User,
          llm_model: Optional[str],
          request_id: Optional[str],
      ) -> WorkflowRefineResponse:
          try:
              chain = PromptGeneratorChain.from_prompt_template(WORKFLOW_REFINE_TEMPLATE, request_id, llm_model)
              result: WorkflowRefineDetails = chain.invoke_with_model(
                  WorkflowRefineDetails,
                  {
                      "yaml_config": yaml_config,
                      "refine_prompt": refine_prompt or "",
                  },
              )

              try:
                  yaml.safe_load(result.yaml_config)
              except yaml.YAMLError as parse_err:
                  raise ExtendedHTTPException(
                      code=400,
                      message="LLM returned invalid YAML",
                      details=str(parse_err),
                      help=_HELP_MESSAGE,
                  )

              emit_llm_token_metric(
                  name=WORKFLOW_AI_REFINE_TOTAL_METRIC,
                  request_id=request_id,
                  base_attributes={
                      MetricsAttributes.LLM_MODEL: llm_model or "default",
                      MetricsAttributes.USER_ID: logging_user_id.get("-"),
                      MetricsAttributes.USER_NAME: current_user_email.get("-"),
                      MetricsAttributes.PROJECT: get_project_for_metric(),
                  },
              )

              return WorkflowRefineResponse(yaml_config=result.yaml_config)

          except ExtendedHTTPException:
              raise
          except Exception as e:
              logger.error(f"Failed to refine workflow: {e}", exc_info=True)
              send_log_metric(
                  name=WORKFLOW_AI_REFINE_TOTAL_METRIC + "_errors",
                  attributes={
                      MetricsAttributes.LLM_MODEL: llm_model or "default",
                      MetricsAttributes.USER_ID: logging_user_id.get("-"),
                      MetricsAttributes.USER_NAME: current_user_email.get("-"),
                      MetricsAttributes.PROJECT: get_project_for_metric(),
                  },
              )
              raise ExtendedHTTPException(
                  code=500,
                  message="Failed to refine workflow",
                  details=f"An error occurred while refining the workflow: {str(e)}",
                  help=_HELP_MESSAGE,
              )
          finally:
              if request_id:
                  request_summary_manager.clear_summary(request_id)
  ```

- [ ] **Step 5: Run tests to confirm GREEN**

  Run: `python -m pytest tests/codemie/service/test_workflow_generator_service.py -v 2>&1 | tail -20`
  Expected: all 3 tests PASS.

- [ ] **Step 6: Verify lint**

  Run: `make lint`
  Expected: no errors.

- [ ] **Step 7: Commit**

  ```bash
  git add src/codemie/service/workflow_generator_service.py \
          src/codemie/rest_api/models/workflow_generator.py \
          src/codemie/templates/agents/workflow_generator_prompt.py \
          tests/codemie/service/test_workflow_generator_service.py
  git commit -m "feat(EPMCDME-12616): add WorkflowGeneratorService with refine_workflow"
  ```

---

### Task 5: `WorkflowService.revert_workflow()` and `change_type` threading

**Files:**
- Modify: `src/codemie/service/workflow_service.py:651-680`
- Modify: `tests/codemie/service/test_workflow_service.py`

**Interfaces:**
- Consumes: `YamlConfigHistory.change_type` (Task 1), `update_workflow` (existing)
- Produces:
  - `WorkflowService.revert_workflow(stored_config: WorkflowConfig, user: User) -> WorkflowConfig`
  - `WorkflowService._update_workflow_values(stored_config, updated_config, user, change_type=None)` — backward-compatible signature change

**Test-first: yes — write failing tests before modifying the service**

- [ ] **Step 1: Write failing tests in `tests/codemie/service/test_workflow_service.py`**

  Add these tests after the existing fixtures:

  ```python
  from datetime import datetime
  from codemie.core.exceptions import ExtendedHTTPException
  from codemie.core.workflow_models.workflow_config import YamlConfigHistory


  def test_yaml_config_history_default_change_type_is_none():
      entry = YamlConfigHistory(yaml_config="yaml: true", date=datetime.now())
      assert entry.change_type is None


  def test_yaml_config_history_accepts_revert_change_type():
      entry = YamlConfigHistory(yaml_config="yaml: true", date=datetime.now(), change_type="revert")
      assert entry.change_type == "revert"


  @patch("codemie.service.workflow_service.WorkflowService._update_workflow_values")
  def test_revert_workflow_calls_update_with_history_yaml(mock_update, user, workflow_config):
      previous_yaml = "name: old-workflow\n"
      workflow_config.yaml_config_history = [
          YamlConfigHistory(yaml_config=previous_yaml, date=datetime.now())
      ]

      service = WorkflowService()
      service.revert_workflow(workflow_config, user)

      mock_update.assert_called_once()
      call_kwargs = mock_update.call_args
      # Third positional arg is updated_config; check its yaml_config
      updated_config_arg = call_kwargs[0][1]
      assert updated_config_arg.yaml_config == previous_yaml
      # change_type kwarg must be "revert"
      assert call_kwargs[1].get("change_type") == "revert"


  def test_revert_workflow_raises_when_no_history(user, workflow_config):
      workflow_config.yaml_config_history = []

      service = WorkflowService()
      with pytest.raises(ExtendedHTTPException) as exc_info:
          service.revert_workflow(workflow_config, user)
      assert exc_info.value.status_code == 400
  ```

- [ ] **Step 2: Run tests to confirm RED**

  Run: `python -m pytest tests/codemie/service/test_workflow_service.py -k "revert or change_type" -v 2>&1 | tail -20`
  Expected: `AttributeError: 'WorkflowService' object has no attribute 'revert_workflow'` or similar.

- [ ] **Step 3: Add `change_type` parameter to `_update_workflow_values`**

  In `src/codemie/service/workflow_service.py`, locate `_update_workflow_values` at line ~651. Change its signature and the history entry construction:

  Before:
  ```python
  def _update_workflow_values(
      self, stored_config: WorkflowConfig, updated_workflow_config: WorkflowConfig, user: User
  ) -> None:
      new_history_entry = YamlConfigHistory(
          yaml_config=stored_config.yaml_config,
          date=datetime.now(),
          created_by=user.as_user_model(),
      )
  ```

  After:
  ```python
  def _update_workflow_values(
      self,
      stored_config: WorkflowConfig,
      updated_workflow_config: WorkflowConfig,
      user: User,
      change_type: Optional[str] = None,
  ) -> None:
      new_history_entry = YamlConfigHistory(
          yaml_config=stored_config.yaml_config,
          date=datetime.now(),
          created_by=user.as_user_model(),
          change_type=change_type,
      )
  ```

- [ ] **Step 4: Add `revert_workflow()` method to `WorkflowService`**

  Add after `update_workflow` (around line 174):

  ```python
  def revert_workflow(self, stored_config: WorkflowConfig, user: User) -> WorkflowConfig:
      if not stored_config.yaml_config_history:
          raise ExtendedHTTPException(
              code=400,
              message="No history available",
              details="The workflow has no saved history entries to revert to.",
              help="Save the workflow at least once before using revert.",
          )
      previous = stored_config.yaml_config_history[0]
      reverted_config = stored_config.model_copy(update={"yaml_config": previous.yaml_config})
      self._update_workflow_values(stored_config, reverted_config, user, change_type="revert")
      return stored_config
  ```

  Also ensure `Optional` is imported at the top of `workflow_service.py` (it already is).

- [ ] **Step 5: Run tests to confirm GREEN**

  Run: `python -m pytest tests/codemie/service/test_workflow_service.py -k "revert or change_type" -v 2>&1 | tail -20`
  Expected: all 4 new tests PASS.

- [ ] **Step 6: Run full workflow service test suite**

  Run: `python -m pytest tests/codemie/service/test_workflow_service.py -v 2>&1 | tail -20`
  Expected: all tests PASS (no regressions from the `change_type=None` default).

- [ ] **Step 7: Verify lint**

  Run: `make lint`
  Expected: no errors.

- [ ] **Step 8: Commit**

  ```bash
  git add src/codemie/service/workflow_service.py \
          tests/codemie/service/test_workflow_service.py
  git commit -m "feat(EPMCDME-12616): add revert_workflow and change_type threading to WorkflowService"
  ```

---

### Task 6: Router endpoints for refine and revert

**Files:**
- Modify: `src/codemie/rest_api/routers/workflow.py`
- Modify: `tests/codemie/rest_api/routers/test_workflow.py`

**Interfaces:**
- Consumes:
  - `WorkflowGeneratorService.refine_workflow(yaml_config, refine_prompt, user, llm_model, request_id)` → `WorkflowRefineResponse`
  - `WorkflowService.revert_workflow(stored_config, user)` → `WorkflowConfig`
  - `WorkflowRefineRequest`, `WorkflowRevertRequest` from `codemie.rest_api.models.workflow_generator`
  - `WorkflowRefineResponse` from `codemie.rest_api.models.workflow_generator`
  - `set_logging_info` from `codemie.configs.logger`
  - `set_llm_context` from `codemie.service.llm_service.utils`
- Produces: `POST /v1/workflows/{workflow_id}/refine` → `WorkflowRefineResponse`; `POST /v1/workflows/{workflow_id}/revert` → `BaseResponseWithData`

**Test-first: yes — write router tests before adding endpoints**

- [ ] **Step 1: Write failing router tests**

  Add to `tests/codemie/rest_api/routers/test_workflow.py`:

  ```python
  from codemie.rest_api.models.workflow_generator import WorkflowRefineResponse
  from codemie.core.workflow_models.workflow_config import YamlConfigHistory
  from datetime import datetime


  @pytest.mark.asyncio
  async def test_refine_workflow_success(workflow_config, request_header):
      refined_yaml = "name: refined-workflow\n"
      with (
          patch(
              "codemie.service.workflow_service.WorkflowService.get_workflow",
              return_value=workflow_config,
          ),
          patch("codemie.core.ability.Ability.can", return_value=True),
          patch(
              "codemie.service.workflow_generator_service.WorkflowGeneratorService.refine_workflow",
              return_value=WorkflowRefineResponse(yaml_config=refined_yaml),
          ) as mock_refine,
      ):
          transport = ASGITransport(app=app)
          async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
              response = await ac.post(
                  f"/v1/workflows/{workflow_config.id}/refine",
                  json={"yaml_config": "name: old\n", "refine_prompt": "improve it"},
                  headers=request_header,
              )
          assert response.status_code == 200
          assert response.json()["yaml_config"] == refined_yaml
          mock_refine.assert_called_once()


  @pytest.mark.asyncio
  async def test_refine_workflow_access_denied(workflow_config, request_header):
      with (
          patch(
              "codemie.service.workflow_service.WorkflowService.get_workflow",
              return_value=workflow_config,
          ),
          patch("codemie.core.ability.Ability.can", return_value=False),
      ):
          transport = ASGITransport(app=app)
          async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
              response = await ac.post(
                  f"/v1/workflows/{workflow_config.id}/refine",
                  json={"yaml_config": "name: old\n"},
                  headers=request_header,
              )
          assert response.status_code == 403


  @pytest.mark.asyncio
  async def test_refine_workflow_not_found(request_header):
      with patch(
          "codemie.service.workflow_service.WorkflowService.get_workflow",
          side_effect=Exception("not found"),
      ):
          transport = ASGITransport(app=app)
          async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
              response = await ac.post(
                  "/v1/workflows/nonexistent/refine",
                  json={"yaml_config": "name: old\n"},
                  headers=request_header,
              )
          assert response.status_code == 404


  @pytest.mark.asyncio
  async def test_revert_workflow_success(workflow_config, request_header):
      workflow_config.yaml_config_history = [
          YamlConfigHistory(yaml_config="name: prev\n", date=datetime.now())
      ]
      with (
          patch(
              "codemie.service.workflow_service.WorkflowService.get_workflow",
              return_value=workflow_config,
          ),
          patch("codemie.core.ability.Ability.can", return_value=True),
          patch(
              "codemie.service.workflow_service.WorkflowService.revert_workflow",
              return_value=workflow_config,
          ) as mock_revert,
          patch(
              "codemie.service.guardrail.guardrail_service.GuardrailService.sync_guardrail_assignments_for_entity"
          ),
          patch(
              "codemie.service.guardrail.guardrail_service.GuardrailService.get_entity_guardrail_assignments",
              return_value=[],
          ),
      ):
          transport = ASGITransport(app=app)
          async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
              response = await ac.post(
                  f"/v1/workflows/{workflow_config.id}/revert",
                  json={},
                  headers=request_header,
              )
          assert response.status_code == 200
          body = response.json()
          assert body["message"] == "Workflow reverted successfully"
          mock_revert.assert_called_once()


  @pytest.mark.asyncio
  async def test_revert_workflow_no_history(workflow_config, request_header):
      from codemie.core.exceptions import ExtendedHTTPException
      with (
          patch(
              "codemie.service.workflow_service.WorkflowService.get_workflow",
              return_value=workflow_config,
          ),
          patch("codemie.core.ability.Ability.can", return_value=True),
          patch(
              "codemie.service.workflow_service.WorkflowService.revert_workflow",
              side_effect=ExtendedHTTPException(
                  code=400, message="No history available", details="", help=""
              ),
          ),
      ):
          transport = ASGITransport(app=app)
          async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
              response = await ac.post(
                  f"/v1/workflows/{workflow_config.id}/revert",
                  json={},
                  headers=request_header,
              )
          assert response.status_code == 400
  ```

- [ ] **Step 2: Run tests to confirm RED**

  Run: `python -m pytest tests/codemie/rest_api/routers/test_workflow.py -k "refine or revert" -v 2>&1 | tail -20`
  Expected: `404 Not Found` or `405 Method Not Allowed` — endpoints don't exist yet.

- [ ] **Step 3: Add imports to the router**

  In `src/codemie/rest_api/routers/workflow.py`, add to the existing imports block:

  ```python
  from fastapi import Request
  from codemie.rest_api.models.workflow_generator import (
      WorkflowRefineRequest,
      WorkflowRefineResponse,
      WorkflowRevertRequest,
  )
  from codemie.service.workflow_generator_service import WorkflowGeneratorService
  ```

  Note: `Request` may not be imported yet — check the top of the file. The existing imports include `APIRouter, status, Depends, Query, BackgroundTasks` from `fastapi`, so add `Request` to that import.

- [ ] **Step 4: Add the `refine` endpoint**

  Add after the `update_workflow` endpoint (after line ~375):

  ```python
  @router.post(
      "/workflows/{workflow_id}/refine",
      status_code=status.HTTP_200_OK,
      response_model=WorkflowRefineResponse,
  )
  def refine_workflow(
      workflow_id: str,
      request: WorkflowRefineRequest,
      raw_request: Request,
      user: User = Depends(authenticate),
  ):
      from codemie.configs.logger import set_logging_info
      from codemie.service.llm_service.utils import set_llm_context

      try:
          workflow = workflow_service.get_workflow(workflow_id)
      except Exception:
          raise_not_found(resource_id=workflow_id, resource_type="Workflow")

      if not Ability(user).can(Action.WRITE, workflow):
          raise_access_denied("refine")

      request_id = raw_request.state.uuid
      set_logging_info(uuid=request_id, user_id=user.id, user_email=user.username)
      set_llm_context(None, user.current_project, user)

      return WorkflowGeneratorService.refine_workflow(
          yaml_config=request.yaml_config,
          refine_prompt=request.refine_prompt,
          user=user,
          llm_model=request.llm_model,
          request_id=request_id,
      )
  ```

- [ ] **Step 5: Add the `revert` endpoint**

  Add immediately after the `refine` endpoint:

  ```python
  @router.post(
      "/workflows/{workflow_id}/revert",
      status_code=status.HTTP_200_OK,
      response_model=BaseResponseWithData,
      response_model_by_alias=True,
  )
  def revert_workflow(
      workflow_id: str,
      request: WorkflowRevertRequest,
      user: User = Depends(authenticate),
  ):
      try:
          workflow = workflow_service.get_workflow(workflow_id)
      except Exception:
          raise_not_found(resource_id=workflow_id, resource_type="Workflow")

      if not Ability(user).can(Action.WRITE, workflow):
          raise_access_denied("revert")

      reverted_workflow = workflow_service.revert_workflow(workflow, user)

      GuardrailService.sync_guardrail_assignments_for_entity(
          user=user,
          entity_type=GuardrailEntity.WORKFLOW,
          entity_id=str(reverted_workflow.id),
          entity_project_name=reverted_workflow.project,
          guardrail_assignments=None,
      )

      reverted_workflow.guardrail_assignments = GuardrailService.get_entity_guardrail_assignments(
          user,
          GuardrailEntity.WORKFLOW,
          str(reverted_workflow.id),
      )

      return {"message": "Workflow reverted successfully", "data": reverted_workflow}
  ```

- [ ] **Step 6: Run router tests to confirm GREEN**

  Run: `python -m pytest tests/codemie/rest_api/routers/test_workflow.py -k "refine or revert" -v 2>&1 | tail -20`
  Expected: all 5 new tests PASS.

- [ ] **Step 7: Run full router test suite to check regressions**

  Run: `python -m pytest tests/codemie/rest_api/routers/test_workflow.py -v 2>&1 | tail -30`
  Expected: all tests PASS.

- [ ] **Step 8: Verify lint**

  Run: `make lint`
  Expected: no errors.

- [ ] **Step 9: Commit**

  ```bash
  git add src/codemie/rest_api/routers/workflow.py \
          tests/codemie/rest_api/routers/test_workflow.py
  git commit -m "feat(EPMCDME-12616): add POST /workflows/{id}/refine and /revert endpoints"
  ```

---

### Task 7: Full test run and final validation

**Files:** No new files — validation only.

**Test-first: N/A**

- [ ] **Step 1: Run the full test suite for all touched areas**

  ```bash
  python -m pytest \
    tests/codemie/service/test_workflow_service.py \
    tests/codemie/service/test_workflow_generator_service.py \
    tests/codemie/rest_api/routers/test_workflow.py \
    -v 2>&1 | tail -40
  ```
  Expected: all tests PASS.

- [ ] **Step 2: Run lint**

  Run: `make lint`
  Expected: no errors.

- [ ] **Step 3: Confirm no uncommitted changes**

  Run: `git status`
  Expected: clean working tree.
