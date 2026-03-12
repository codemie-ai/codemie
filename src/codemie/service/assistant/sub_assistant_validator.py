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

from enum import StrEnum
from typing import Optional

from pydantic.dataclasses import dataclass

from codemie.configs import logger
from codemie.rest_api.models.assistant import Assistant, AssistantBase
from codemie.rest_api.security.user import User

# ============================================================================
# Validation Result Types
# ============================================================================


class ValidationErrorType(StrEnum):
    """Types of validation errors for sub-assistant validation."""

    PROJECT_MISMATCH = "project_mismatch"
    NESTING_VIOLATION = "nesting_violation"
    CIRCULAR_REFERENCE = "circular_reference"
    NOT_FOUND = "not_found"


@dataclass
class ValidationResult:
    """Result of a validation check with structured error information."""

    success: bool = False
    error_type: Optional[ValidationErrorType] = None
    assistant: Optional[AssistantBase] = None
    assistant_id: Optional[str] = None

    @property
    def is_fatal(self) -> bool:
        """Check if this error should stop validation immediately."""
        return self.error_type in (ValidationErrorType.CIRCULAR_REFERENCE, ValidationErrorType.NOT_FOUND)

    @classmethod
    def ok(cls) -> 'ValidationResult':
        """Create a successful validation result."""
        return cls(success=True)

    @classmethod
    def project_mismatch(cls, assistant: AssistantBase) -> 'ValidationResult':
        """Create a project mismatch error result."""
        return cls(
            error_type=ValidationErrorType.PROJECT_MISMATCH,
            assistant=assistant,
        )

    @classmethod
    def nesting_violation(cls, assistant: AssistantBase) -> 'ValidationResult':
        """Create a nesting violation error result."""
        return cls(
            error_type=ValidationErrorType.NESTING_VIOLATION,
            assistant=assistant,
        )

    @classmethod
    def circular_reference(cls) -> 'ValidationResult':
        """Create a circular reference error result."""
        return cls(error_type=ValidationErrorType.CIRCULAR_REFERENCE)

    @classmethod
    def not_found(cls, assistant_id: str) -> 'ValidationResult':
        """Create a not found error result."""
        return cls(error_type=ValidationErrorType.NOT_FOUND, assistant_id=assistant_id)

    def to_error_message(self, parent: Optional[AssistantBase] = None) -> str:
        """
        Convert this validation result to a user-friendly error message.

        Args:
            parent: The parent assistant (needed for project mismatch errors)

        Returns:
            Formatted error message
        """
        if self.success:
            return ""

        match self.error_type:
            case ValidationErrorType.CIRCULAR_REFERENCE:
                return "Circular reference detected: An assistant cannot include itself as an inner assistant"

            case ValidationErrorType.NOT_FOUND:
                assistant_id = self.assistant_id or "unknown"
                return f'Invalid reference: Assistant with ID "{assistant_id}" does not exist in the system'

            case ValidationErrorType.NESTING_VIOLATION:
                name = self.assistant.name if self.assistant else "unknown"
                return f"Nested assistants aren't supported. Assistant '{name}' can't have its own sub-assistants"

            case ValidationErrorType.PROJECT_MISMATCH:
                name = self.assistant.name if self.assistant else "unknown"
                project = self.assistant.project if self.assistant else "unknown"
                parent_info = f"the assistant '{parent.name}'" if parent else "the assistant"
                parent_project = parent.project if parent else "unknown"
                return (
                    f"Sub-assistant '{name}' (project: '{project}') is associated with a different project. "
                    f"However, the expected project for {parent_info} must be '{parent_project}'. "
                    f"Tip: Publish '{name}' to marketplace to use it across projects."
                )

            case _:
                return "Unknown validation error occurred"

    @staticmethod
    def format_aggregated_errors(errors: list['ValidationResult'], parent: AssistantBase) -> Optional[str]:
        """
        Format multiple validation errors into a user-friendly message.

        Args:
            errors: List of ValidationResult objects to aggregate
            parent: The parent assistant (for context in error messages)

        Returns:
            Formatted error message or None if no errors
        """
        if not errors:
            return None

        # Group errors by type
        project_mismatches = [e for e in errors if e.error_type == ValidationErrorType.PROJECT_MISMATCH]
        nesting_violations = [e for e in errors if e.error_type == ValidationErrorType.NESTING_VIOLATION]

        # Format project mismatches
        if project_mismatches:
            if len(project_mismatches) == 1:
                return project_mismatches[0].to_error_message(parent)
            else:
                # Multiple project mismatches - aggregate into single message
                names_and_projects = [
                    (e.assistant.name, e.assistant.project) for e in project_mismatches if e.assistant
                ]
                mismatch_details = ", ".join(
                    [f"'{name}' (project: '{project}')" for name, project in names_and_projects]
                )
                return (
                    f"Sub-assistants {mismatch_details} are associated with different projects. "
                    f"However, the expected project for the assistant '{parent.name}' must be '{parent.project}'. "
                    f"Tip: Publish these assistants to marketplace to use them across projects."
                )

        # Format nesting violations
        if nesting_violations:
            if len(nesting_violations) == 1:
                return nesting_violations[0].to_error_message()
            else:
                # Multiple nesting violations - aggregate into single message
                names = [e.assistant.name for e in nesting_violations if e.assistant]
                return (
                    'Nested assistants not supported. Inner assistants '
                    + f'({", ".join(names)}) cannot contain their own inner assistants'
                )

        return None


class SubAssistantValidator:
    def validate_sub_assistants(self, parent: AssistantBase, sub_assistant_ids: list[str], user: User) -> Optional[str]:
        """
        Validate all sub-assistants for a parent assistant.

        Args:
            parent: The parent assistant
            sub_assistant_ids: List of sub-assistant IDs to validate
            user: The user performing the operation

        Returns:
            None if valid, error message string if invalid

        This method aggregates validation errors and returns detailed messages
        about circular references, project mismatches, and nesting violations.
        """
        if not sub_assistant_ids:
            return None

        # Initialize stateful error collector
        self._validation_errors: list[ValidationResult] = []

        for asst_id in sub_assistant_ids:
            # Validate single sub-assistant and collect errors
            if error := self._validate_single_sub_assistant(parent, asst_id, user):
                return error

        # Return aggregated errors if any
        if self._validation_errors:
            error_types = [e.error_type for e in self._validation_errors]
            failed_ids = [e.assistant.id if e.assistant else e.assistant_id for e in self._validation_errors]
            logger.error(
                f"Sub-assistant validation completed with {len(self._validation_errors)} error(s). "
                f"parent_id={parent.id or 'new'}, parent_name={parent.name}, "
                f"error_types={error_types}, failed_sub_assistant_ids={failed_ids}, "
                f"user_id={user.id}"
            )

        return ValidationResult.format_aggregated_errors(self._validation_errors, parent)

    def _validate_single_sub_assistant(self, parent: AssistantBase, asst_id: str, user: User) -> Optional[str]:
        """
        Validate a single sub-assistant and aggregate non-fatal errors.

        Args:
            parent: The parent assistant
            asst_id: The sub-assistant ID to validate
            user: The user performing the operation

        Returns:
            Fatal error message or None

        Side effects:
            Updates self._validation_errors with non-fatal errors
        """
        # Check existence - fatal error
        sub = Assistant.find_by_id(asst_id)
        if not sub:
            logger.error(
                f"Sub-assistant validation failed: assistant not found. "
                f"parent_id={parent.id or 'new'}, parent_name={parent.name}, "
                f"sub_assistant_id={asst_id}, user_id={user.id}"
            )
            return ValidationResult.not_found(asst_id).to_error_message()

        # Validate using appropriate rules (admin or default user)
        if user.is_admin:
            result = self._apply_admin_validation_rules(parent, sub)
        else:
            result = self._apply_default_user_validation_rules(parent, sub)

        if result.success:
            return None

        # Return fatal errors immediately
        if result.is_fatal:
            logger.error(
                f"Sub-assistant validation failed: {result.error_type}. "
                f"parent_id={parent.id or 'new'}, parent_name={parent.name}, "
                f"sub_assistant_id={sub.id}, sub_assistant_name={sub.name}, "
                f"user_id={user.id}"
            )
            return result.to_error_message(parent)

        # Collect non-fatal errors for batch reporting
        self._validation_errors.append(result)
        return None

    def _apply_admin_validation_rules(self, parent: AssistantBase, sub: AssistantBase) -> ValidationResult:
        """
        Apply validation rules for admin users.

        Admin users have relaxed restrictions:
        - Can bypass project mismatch restrictions

        But admins still must respect:
        - No circular references (cannot add assistant to itself)
        - No nesting (sub-assistants cannot have their own sub-assistants)

        Args:
            parent: The parent assistant
            sub: The sub-assistant to add

        Returns:
            ValidationResult indicating success or specific error type
        """
        # RULE 1: No circular references
        if sub.id == parent.id:
            return ValidationResult.circular_reference()

        # RULE 2: No nesting depth
        if sub.assistant_ids:
            return ValidationResult.nesting_violation(sub)

        # RULE 3: Admins bypass project mismatch restrictions
        return ValidationResult.ok()

    def _apply_default_user_validation_rules(self, parent: AssistantBase, sub: AssistantBase) -> ValidationResult:
        """
        Apply validation rules for default (non-admin) users.

        Default user restrictions:
        1. No circular references allowed
        2. No nesting allowed (sub-assistants cannot have their own sub-assistants)
        3. Marketplace assistants (is_global=True) can always be used across projects
        4. Private assistants must belong to the same project

        Args:
            parent: The parent assistant
            sub: The sub-assistant to add

        Returns:
            ValidationResult indicating success or specific error type
        """
        # RULE 1: No circular references
        if sub.id == parent.id:
            return ValidationResult.circular_reference()

        # RULE 2: No nesting depth
        if sub.assistant_ids:
            return ValidationResult.nesting_violation(sub)

        # RULE 3: Marketplace assistants are always allowed
        if sub.is_global:
            return ValidationResult.ok()

        # RULE 4: Private assistants require project match
        if sub.project != parent.project:
            return ValidationResult.project_mismatch(sub)

        return ValidationResult.ok()
