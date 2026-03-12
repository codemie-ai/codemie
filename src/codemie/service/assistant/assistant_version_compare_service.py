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

"""Service for comparing assistant versions"""

from typing import Dict, Any
from deepdiff import DeepDiff

from codemie.rest_api.models.assistant import (
    AssistantConfiguration,
    AssistantVersionCompareResponse,
    AssistantRequest,
)


class AssistantVersionCompareService:
    """Service for comparing assistant versions"""

    @classmethod
    def has_configuration_changes(cls, assistant_id: str, request: AssistantRequest) -> bool:
        """
        Check if the request contains changes to versioned configuration fields.

        This method retrieves the current version configuration and compares it with the
        incoming request to determine if a new version should be created. Only versioned
        fields are compared; metadata fields like name, slug, shared, etc. are ignored.

        If no current version exists (e.g., legacy assistant or creation error),
        returns True to create an initial version.

        Args:
            assistant_id: The assistant ID to check
            request: The assistant update request

        Returns:
            True if versioned fields have changed or no version exists, False otherwise
        """
        current_config = AssistantConfiguration.get_current_version(assistant_id)

        if not current_config:
            return True

        current_dict = cls._prepare_for_comparison(current_config)
        request_dict = {
            'description': request.description or "",
            'system_prompt': request.system_prompt or "",
            'llm_model_type': request.llm_model_type,
            'temperature': request.temperature,
            'top_p': request.top_p,
            'context': [ctx.model_dump() if hasattr(ctx, 'model_dump') else ctx for ctx in request.context],
            'toolkits': [tk.model_dump() if hasattr(tk, 'model_dump') else tk for tk in request.toolkits],
            'mcp_servers': [mcp.model_dump() if hasattr(mcp, 'model_dump') else mcp for mcp in request.mcp_servers],
            'assistant_ids': request.assistant_ids,
            'conversation_starters': request.conversation_starters,
            'bedrock': request.bedrock.model_dump()
            if request.bedrock and hasattr(request.bedrock, 'model_dump')
            else request.bedrock,
            'agent_card': request.agent_card.model_dump()
            if request.agent_card and hasattr(request.agent_card, 'model_dump')
            else request.agent_card,
            'custom_metadata': request.custom_metadata,
        }

        diff = DeepDiff(current_dict, request_dict, ignore_order=False)
        return bool(diff)

    @classmethod
    def compare_versions(
        cls, assistant_id: str, version1: AssistantConfiguration, version2: AssistantConfiguration
    ) -> AssistantVersionCompareResponse:
        """
        Compare two versions of an assistant.

        Args:
            assistant_id: The assistant ID
            version1: First version to compare
            version2: Second version to compare

        Returns:
            Comparison response with differences
        """
        # Convert to dicts for comparison
        dict1 = cls._prepare_for_comparison(version1)
        dict2 = cls._prepare_for_comparison(version2)

        # Perform deep comparison
        diff = DeepDiff(dict1, dict2, ignore_order=True, report_repetition=True, verbose_level=2)

        # Generate human-readable summary
        summary = cls._generate_summary(diff, version1.version_number, version2.version_number)

        # Convert diff to dict and ensure it's JSON serializable
        diff_dict = cls._sanitize_diff_dict(diff.to_dict()) if diff else {}

        return AssistantVersionCompareResponse(
            assistant_id=assistant_id,
            version1=version1,
            version2=version2,
            differences=diff_dict,
            change_summary=summary,
        )

    @classmethod
    def _prepare_for_comparison(cls, config: AssistantConfiguration) -> Dict[str, Any]:
        """
        Prepare configuration for comparison by excluding metadata fields.

        Args:
            config: The configuration to prepare

        Returns:
            Dictionary with only comparable fields
        """
        exclude_fields = {
            'id',
            'assistant_id',
            'version_number',
            'created_date',
            'created_by',
            'change_notes',
            'date',
            'update_date',
        }

        data = {k: v for k, v in config.model_dump().items() if k not in exclude_fields}

        # Remove settings_config from toolkits and tools as it's a computed/enriched field
        if 'toolkits' in data and data['toolkits']:
            data['toolkits'] = cls._remove_settings_config_from_toolkits(data['toolkits'])

        return data

    @classmethod
    def _remove_settings_config_from_toolkits(cls, toolkits: list) -> list:
        def clean_dict(obj: dict) -> dict:
            """Remove settings_config from a dictionary."""
            return {k: v for k, v in obj.items() if k != 'settings_config'}

        def clean_tool(tool):
            """Clean a single tool, removing settings_config if it's a dict."""
            return clean_dict(tool) if isinstance(tool, dict) else tool

        def clean_toolkit(toolkit):
            """Clean a single toolkit and its tools."""
            if not isinstance(toolkit, dict):
                return toolkit

            cleaned = clean_dict(toolkit)

            if tools := cleaned.get('tools'):
                cleaned['tools'] = [clean_tool(tool) for tool in tools]

            return cleaned

        return [clean_toolkit(toolkit) for toolkit in toolkits]

    @classmethod
    def _sanitize_diff_dict(cls, diff_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize DeepDiff dictionary to ensure JSON serializability.

        DeepDiff can include type objects and other non-serializable items.
        This method recursively converts them to strings.

        Args:
            diff_dict: The DeepDiff dictionary

        Returns:
            Sanitized dictionary safe for JSON serialization
        """

        def sanitize_value(value: Any) -> Any:
            """Recursively sanitize a value."""
            if isinstance(value, type):
                # Convert type objects to their string representation
                return str(value)
            if isinstance(value, dict):
                return {k: sanitize_value(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [sanitize_value(item) for item in value]
            if isinstance(value, set):
                return [sanitize_value(item) for item in value]
            # Return primitive types as-is
            return value

        return sanitize_value(diff_dict)

    @classmethod
    def _generate_summary(cls, diff: DeepDiff, version1_num: int, version2_num: int) -> str:
        """
        Generate human-readable summary of differences.

        Args:
            diff: The DeepDiff result
            version1_num: First version number
            version2_num: Second version number

        Returns:
            Human-readable summary string
        """
        if not diff:
            return f"No differences between version {version1_num} and version {version2_num}"

        summary_parts = [f"Changes from version {version1_num} to version {version2_num}:"]

        # Type changes (e.g., settings added/removed: null -> dict or dict -> null)
        if 'type_changes' in diff:
            summary_parts.append(f"- {len(diff['type_changes'])} field(s) with type changes")
            for path, change in list(diff['type_changes'].items())[:3]:
                field = cls._extract_field_path(path)
                old_type = str(change.get('old_type', 'unknown')).replace("<class '", "").replace("'>", "")
                new_type = str(change.get('new_type', 'unknown')).replace("<class '", "").replace("'>", "")
                summary_parts.append(f"  • {field} type changed: {old_type} → {new_type}")

        # Values changed
        if 'values_changed' in diff:
            summary_parts.append(f"- {len(diff['values_changed'])} field(s) modified")
            for path, _change in list(diff['values_changed'].items())[:3]:
                field = cls._extract_field_path(path)
                summary_parts.append(f"  • {field} changed")

        # Items added
        if 'iterable_item_added' in diff:
            summary_parts.append(f"- {len(diff['iterable_item_added'])} item(s) added")

        # Items removed
        if 'iterable_item_removed' in diff:
            summary_parts.append(f"- {len(diff['iterable_item_removed'])} item(s) removed")

        # Dictionary items added
        if 'dictionary_item_added' in diff:
            summary_parts.append(f"- {len(diff['dictionary_item_added'])} new field(s)")

        # Dictionary items removed
        if 'dictionary_item_removed' in diff:
            summary_parts.append(f"- {len(diff['dictionary_item_removed'])} field(s) removed")

        return "\n".join(summary_parts)

    @classmethod
    def _extract_field_path(cls, deepdiff_path: str) -> str:
        """
        Extract a human-readable field path from a DeepDiff path string.

        DeepDiff paths have format like:
        - "root['field_name']" -> "field_name"
        - "root['list_field'][0]" -> "list_field[0]"
        - "root['nested']['field']" -> "nested.field"

        Args:
            deepdiff_path: The DeepDiff path string (e.g., "root['field'][0]")

        Returns:
            Human-readable field path (e.g., "field[0]")
        """
        # Remove 'root' prefix
        path = deepdiff_path.replace("root", "", 1)

        # Split by brackets to process each segment
        # Example: "['conversation_starters'][0]" -> ["", "'conversation_starters'", "", "0", ""]
        parts = []
        current = ""
        in_brackets = False

        for char in path:
            if char == '[':
                in_brackets = True
                current = ""
            elif char == ']':
                in_brackets = False
                if current:
                    # Remove quotes from string keys
                    cleaned = current.strip('\'"')
                    parts.append(cleaned)
                current = ""
            elif in_brackets:
                current += char

        # Build the readable path
        if not parts:
            return "unknown"

        # First part is the base field name
        result = parts[0]

        # Add subsequent parts with appropriate separators
        for part in parts[1:]:
            # If part is numeric, it's an array index
            if part.isdigit():
                result += f"[{part}]"
            else:
                # Otherwise it's a nested field, use dot notation
                result += f".{part}"

        return result
