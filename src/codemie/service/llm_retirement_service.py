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

from dataclasses import dataclass
from typing import Any

import yaml
from sqlalchemy import String, func
from sqlmodel import Session, or_, select, text

from codemie.configs.logger import logger
from codemie.core.models import LLMRetirementPair
from codemie.core.workflow_models.workflow_config import WorkflowConfig
from codemie.rest_api.models.assistant import Assistant, AssistantConfiguration


class _LiteralBlockDumper(yaml.SafeDumper):
    """SafeDumper that uses block scalar (|) style for multiline strings.

    Prevents yaml.safe_dump from converting multiline strings (e.g. prompts) to
    double-quoted scalars with \\n escapes, which breaks round-trip editing in the UI.
    """


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_LiteralBlockDumper.add_representer(str, _str_representer)


@dataclass
class LLMRetirementResult:
    assistants_updated: int
    assistant_configurations_updated: int
    workflows_updated: int
    workflows_skipped: int = 0


@dataclass
class LLMBulkRetirementItemResult:
    deprecated_model: str
    replacement_model: str
    success: bool
    assistants_updated: int = 0
    assistant_configurations_updated: int = 0
    workflows_updated: int = 0
    workflows_skipped: int = 0
    error: str | None = None


def _replace_model_in_yaml(node: dict | list | Any, deprecated: str, replacement: str) -> bool:
    """Recursively replace exact ``model`` key matches. Returns True if any change was made."""
    changed = False
    if isinstance(node, dict):
        if node.get("model") == deprecated:
            node["model"] = replacement
            changed = True
        for value in node.values():
            if _replace_model_in_yaml(value, deprecated, replacement):
                changed = True
    elif isinstance(node, list):
        for item in node:
            if _replace_model_in_yaml(item, deprecated, replacement):
                changed = True
    return changed


class LLMRetirementService:
    def retire_model(
        self,
        deprecated_model: str,
        replacement_model: str,
        check_models_existence: bool = True,
    ) -> LLMRetirementResult:
        logger.info(f"Starting LLM model retirement: '{deprecated_model}' -> '{replacement_model}'")

        if check_models_existence:
            self._validate_models_exist(deprecated_model, replacement_model)

        with Session(Assistant.get_engine()) as session:
            assistants_updated, configurations_updated = self._retire_assistants(
                session, deprecated_model, replacement_model
            )
            workflows_updated, workflows_skipped = self._retire_workflows(session, deprecated_model, replacement_model)
            session.commit()

        result = LLMRetirementResult(
            assistants_updated=assistants_updated,
            assistant_configurations_updated=configurations_updated,
            workflows_updated=workflows_updated,
            workflows_skipped=workflows_skipped,
        )

        logger.info(
            f"LLM model retirement complete: deprecated='{deprecated_model}', replacement='{replacement_model}', "
            f"assistants={result.assistants_updated}, "
            f"configurations={result.assistant_configurations_updated}, "
            f"workflows={result.workflows_updated}, "
            f"workflows_skipped={result.workflows_skipped}"
        )
        return result

    @staticmethod
    def _retire_assistants(session: Session, deprecated_model: str, replacement_model: str) -> tuple[int, int]:
        """
        Update llm_model_type in assistants and assistant_configurations via raw SQL.

        Raw SQL is used to bypass BaseModelWithSQLSupport.update() which unconditionally
        sets update_date. Table names come from trusted __tablename__ constants;
        user values go through bind params.

        Returns (assistants_updated, configurations_updated).
        """
        # All rows in assistants that reference the deprecated model
        sql_assistants = text(f"""
            UPDATE {Assistant.__tablename__}
            SET llm_model_type = :replacement_model
            WHERE llm_model_type = :deprecated_model
        """)

        # Only the latest version (MAX version_number) per assistant is updated;
        # historical snapshots are left untouched.
        sql_configurations = text(f"""
            UPDATE {AssistantConfiguration.__tablename__} AS ac
            SET llm_model_type = :replacement_model
            WHERE llm_model_type = :deprecated_model
              AND version_number = (
                  SELECT MAX(ac2.version_number)
                  FROM {AssistantConfiguration.__tablename__} ac2
                  WHERE ac2.assistant_id = ac.assistant_id
              )
        """)

        params = {"deprecated_model": deprecated_model, "replacement_model": replacement_model}

        # session.execute() (not session.exec()) is required to get CursorResult.rowcount
        # for UPDATE statements. session.exec() returns ScalarResult which lacks rowcount.
        r_assistants = session.execute(sql_assistants.bindparams(**params))
        r_configurations = session.execute(sql_configurations.bindparams(**params))

        return r_assistants.rowcount, r_configurations.rowcount

    @staticmethod
    def _validate_models_exist(deprecated_model: str, replacement_model: str) -> None:
        """Raise ExtendedHTTPException(400) if either model name is not known to the LLM service."""
        from codemie.core.exceptions import ExtendedHTTPException
        from codemie.service.llm_service.llm_service import llm_service

        known_names = {m.base_name for m in llm_service.get_all_llm_model_info()}
        if replacement_model not in known_names:
            raise ExtendedHTTPException(
                code=400,
                message="Unknown replacement model",
                details=f"Model '{replacement_model}' is not registered in the LLM service. "
                "Pass checkModelsExistence=false to skip this validation.",
            )

    @staticmethod
    def _retire_workflows(session: Session, deprecated_model: str, replacement_model: str) -> tuple[int, int]:
        # Pre-filter: load only workflows that mention the deprecated model name anywhere.
        # This is a substring pre-filter (LIKE), so it may load false-positive rows.
        # The Python-side helpers below are authoritative for whether a real change occurred.
        query = select(WorkflowConfig).where(
            or_(
                WorkflowConfig.yaml_config.contains(deprecated_model),
                func.cast(WorkflowConfig.assistants, String).contains(deprecated_model),
            )
        )
        workflows = session.exec(query).all()

        updated_count = 0
        skipped_count = 0
        for workflow in workflows:
            try:
                changed = LLMRetirementService._update_assistants_field(workflow, deprecated_model, replacement_model)
                changed |= LLMRetirementService._update_yaml_config_field(workflow, deprecated_model, replacement_model)
                if changed:
                    # session.add() (not .update()) so SQLAlchemy only flushes actually-changed attributes,
                    # excluding update_date which we never assign.
                    session.add(workflow)
                    updated_count += 1
            except yaml.YAMLError as e:
                logger.warning(f"Skipping workflow id={workflow.id} name='{workflow.name}': YAML parse error: {e}")
                session.expunge(workflow)
                skipped_count += 1

        return updated_count, skipped_count

    @staticmethod
    def _update_assistants_field(workflow: WorkflowConfig, deprecated_model: str, replacement_model: str) -> bool:
        """Update the JSONB assistants column. Returns True if any assistant model was replaced."""
        if not workflow.assistants:
            return False
        new_assistants = [
            asst.model_copy(update={"model": replacement_model}) if asst.model == deprecated_model else asst
            for asst in workflow.assistants
        ]
        # strict=True: both lists are always the same length (one-for-one comprehension)
        if any(a.model != b.model for a, b in zip(workflow.assistants, new_assistants, strict=True)):
            workflow.assistants = new_assistants
            return True
        return False

    @staticmethod
    def _update_yaml_config_field(workflow: WorkflowConfig, deprecated_model: str, replacement_model: str) -> bool:
        """Update the TEXT yaml_config column. Returns True if any model key was replaced."""
        if not (workflow.yaml_config and deprecated_model in workflow.yaml_config):
            return False
        parsed = yaml.safe_load(workflow.yaml_config)
        if _replace_model_in_yaml(parsed, deprecated_model, replacement_model):
            # sort_keys=False preserves original key ordering; _LiteralBlockDumper
            # keeps multiline strings as block scalars (|) instead of escaped quotes
            workflow.yaml_config = yaml.dump(
                parsed, Dumper=_LiteralBlockDumper, default_flow_style=False, sort_keys=False, allow_unicode=True
            )
            return True
        return False

    def retire_models_bulk(
        self,
        retirements: list[LLMRetirementPair],
        check_models_existence: bool = True,
    ) -> list[LLMBulkRetirementItemResult]:
        """Retire multiple deprecated models. Each pair runs in its own transaction.
        Failures are captured per-item; remaining pairs continue processing."""
        results = []
        for pair in retirements:
            try:
                result = self.retire_model(
                    deprecated_model=pair.deprecated_model,
                    replacement_model=pair.replacement_model,
                    check_models_existence=check_models_existence,
                )
                results.append(
                    LLMBulkRetirementItemResult(
                        deprecated_model=pair.deprecated_model,
                        replacement_model=pair.replacement_model,
                        success=True,
                        assistants_updated=result.assistants_updated,
                        assistant_configurations_updated=result.assistant_configurations_updated,
                        workflows_updated=result.workflows_updated,
                        workflows_skipped=result.workflows_skipped,
                    )
                )
            except Exception as e:
                logger.error(f"Bulk retirement failed for '{pair.deprecated_model}' -> '{pair.replacement_model}': {e}")
                results.append(
                    LLMBulkRetirementItemResult(
                        deprecated_model=pair.deprecated_model,
                        replacement_model=pair.replacement_model,
                        success=False,
                        error=str(e),
                    )
                )
        return results


llm_retirement_service = LLMRetirementService()
