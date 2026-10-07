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

import json
from datetime import date
from sqlalchemy import func, false, true, select as sa_select, cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import case, or_

from codemie.core.db_utils import escape_like_wildcards
from codemie.core.exceptions import ValidationException
from codemie.service.filter.filter_models import START_DATE_DEFAULT, IndexInfoStatus


def compose_combined_date_range_filter(query, model_class, field_name, filter_value):
    start_date = filter_value.get('start_date', START_DATE_DEFAULT)
    end_date = filter_value.get('end_date', date.today().isoformat())
    return query.where(model_class.get_field_expression(field_name).between(start_date, end_date))


def compose_status_filter(query, model_class, field_name, filter_value):
    completed, error, *rest = field_name
    is_queued = rest[0] if rest else None
    if filter_value == IndexInfoStatus.COMPLETED:
        return compose_term_filter(query, model_class, completed, True)
    elif filter_value == IndexInfoStatus.FAILED:
        query = compose_term_filter(query, model_class, completed, False)
        return compose_term_filter(query, model_class, error, True)
    elif filter_value == IndexInfoStatus.IN_PROGRESS:
        query = compose_term_filter(query, model_class, completed, False)
        return compose_term_filter(query, model_class, error, False)
    elif filter_value == IndexInfoStatus.QUEUED:
        if is_queued is None:
            raise ValueError("QUEUED status filter requires is_queued field to be configured in FILTER_CONFIG")
        return compose_term_filter(query, model_class, is_queued, True)
    else:
        raise ValueError(f"Invalid status: {filter_value}")


def compose_wildcard_filter(query, model_class, field_name, filter_value):
    # Security: Escape LIKE wildcards to prevent information leakage (Story 2, NFR-3.1)
    escaped_value = escape_like_wildcards(filter_value)
    query = query.where(model_class.get_field_expression(field_name).ilike(f"%{escaped_value}%", escape="\\"))
    return query


def compose_multi_field_wildcard_filter(query, model_class, field_name, filter_value, priority_limit=2):
    # Security: Escape LIKE wildcards to prevent information leakage (Story 2, NFR-3.1)
    escaped_value = escape_like_wildcards(filter_value)
    conditions = []
    when_conditions = {}

    for priority, field in enumerate(field_name):
        expr = model_class.get_field_expression(field).ilike(f"%{escaped_value}%", escape="\\")
        conditions.append(expr)
        if priority < priority_limit:  # order by priority, name matches first
            when_conditions[expr] = priority

    query = query.where(or_(*conditions))

    priority_case = case(when_conditions, else_=priority_limit)
    query = query.order_by(priority_case)

    return query


def compose_term_filter(query, model_class, field_name, filter_value):
    if isinstance(filter_value, list):
        query = query.where(model_class.get_field_expression(field_name).in_(filter_value))
    else:
        query = query.where(model_class.get_field_expression(field_name) == filter_value)
    return query


def compose_json_array_filter(query, model_class, field_name, filter_value):
    """
    Filter for JSON array fields, where we need to check if any of the filter values
    exist in the JSON array field.

    For PostgreSQL, this uses the @> operator to check if the JSON array contains
    the specified values.
    """
    json_field = model_class.get_field_expression(field_name)

    if isinstance(filter_value, list):
        conditions = []
        for value in filter_value:
            json_array = json.dumps([value])
            conditions.append(json_field.op('@>')(json_array))

        if conditions:
            query = query.where(or_(*conditions))
    else:
        json_array = json.dumps([filter_value])
        query = query.where(json_field.op('@>')(json_array))

    return query


def compose_credential_type_filter(query, model_class, field_name, filter_value):
    """
    Filter Assistant.toolkits (list[ToolKitDetails]) by integration/credential type.

    toolkits[i].settings.credential_type is only populated when a credential is explicitly
    pinned to the toolkit; toolkits using auto_credentials_lookup (the common case) store
    settings=null, so that path can't identify them. Two matches are OR-combined instead:
      - internal toolkits (Plugin, Git) are additionally matched by toolkit name directly,
        since for their own dedicated toolkit entry the toolkit name IS the credential type
        (see ToolMetadataService.is_internal_toolkit and CredentialValidator's same
        special-casing), and settings.credential_type is never populated for it;
      - every requested credential type — including "Git" and "Plugin" — is also resolved to
        its known tool names via ToolMetadataService and matched against
        toolkits[i].tools[j].name. This is required, not just a fallback: "Git" also covers
        the separate discoverable "VCS" toolkit's github/gitlab tools, distinct from the
        internal "Git" toolkit entry. It also disambiguates toolkits that bundle multiple
        providers under one toolkit name (Jira and Confluence both live under
        "Project Management").
    """
    from codemie.service.tools.tool_metadata_service import INTERNAL_TOOLKITS, ToolMetadataService

    values = [
        v for v in (filter_value if isinstance(filter_value, list) else [filter_value]) if v is not None and v != ""
    ]
    if not values:
        return query

    unsupported = [v for v in values if v not in ToolMetadataService.get_supported_credential_types()]
    if unsupported:
        raise ValidationException(f"integration_type value(s) {unsupported} are not supported for filtering")

    internal_values = [v for v in values if v in INTERNAL_TOOLKITS]
    tool_names = ToolMetadataService.get_tool_names_for_credential_types(values)

    json_field = model_class.get_field_expression(field_name)
    toolkit_elem = func.jsonb_array_elements(json_field).table_valued("value", name="toolkit_elem")
    toolkit_val = cast(toolkit_elem.c.value, JSONB)

    conditions = []
    if internal_values:
        conditions.append(
            sa_select(1).select_from(toolkit_elem).where(toolkit_val["toolkit"].astext.in_(internal_values)).exists()
        )

    if tool_names:
        tool_elem = func.jsonb_array_elements(toolkit_val["tools"]).table_valued("value", name="tool_elem").lateral()
        tool_val = cast(tool_elem.c.value, JSONB)
        conditions.append(
            sa_select(1)
            .select_from(toolkit_elem)
            .join(tool_elem, true())
            .where(tool_val["name"].astext.in_(tool_names))
            .exists()
        )

    if not conditions:
        return query.where(false())

    return query.where(or_(*conditions))


def compose_workflow_integration_type_filter(query, model_class, field_name, filter_value):
    """
    Filter WorkflowConfig by integration type, covering two tool-name locations:
      1. workflows.tools[i].tool — standalone tools (flat name string)
      2. workflows.assistants[i].tools[j].name — assistant-embedded tools

    Both paths are OR-combined. tool_names are resolved from the requested
    integration types via ToolMetadataService (includes GIT_TOOL_NAMES for "Git").
    field_name must be ["tools", "assistants"].

    Raises ValidationException for values that are not supported credential types or are in
    WORKFLOW_UNSUPPORTED_INTEGRATION_TYPES (currently "Plugin") — workflow tool records have no
    toolkit field to match by name, and Plugin tool names are dynamic/per-instance, so there is no
    way to ever match them.
    """
    if not isinstance(field_name, (list, tuple)) or len(field_name) != 2:
        raise ValueError(
            f"compose_workflow_integration_type_filter requires field_name=[tools_field, assistants_field], "
            f"got {field_name!r}"
        )

    from codemie.service.tools.tool_metadata_service import (
        WORKFLOW_UNSUPPORTED_INTEGRATION_TYPES,
        ToolMetadataService,
    )

    values = [
        v for v in (filter_value if isinstance(filter_value, list) else [filter_value]) if v is not None and v != ""
    ]
    if not values:
        return query

    supported = ToolMetadataService.get_supported_credential_types() - WORKFLOW_UNSUPPORTED_INTEGRATION_TYPES
    unsupported = [v for v in values if v not in supported]
    if unsupported:
        raise ValidationException(f"integration_type value(s) {unsupported} are not supported for workflow filtering")

    tool_names = ToolMetadataService.get_tool_names_for_credential_types(values)
    if not tool_names:
        return query.where(false())

    tools_col = model_class.get_field_expression(field_name[0])
    assistants_col = model_class.get_field_expression(field_name[1])

    # Path 1: standalone tools — workflows.tools[i].tool
    tool_elem = func.jsonb_array_elements(tools_col).table_valued("value", name="tool_elem")
    tool_val = cast(tool_elem.c.value, JSONB)
    standalone_exists = sa_select(1).select_from(tool_elem).where(tool_val["tool"].astext.in_(tool_names)).exists()

    # Path 2: assistant-embedded tools — workflows.assistants[i].tools[j].name
    asst_elem = func.jsonb_array_elements(assistants_col).table_valued("value", name="asst_elem")
    asst_val = cast(asst_elem.c.value, JSONB)
    asst_tool_elem = func.jsonb_array_elements(asst_val["tools"]).table_valued("value", name="asst_tool_elem").lateral()
    asst_tool_val = cast(asst_tool_elem.c.value, JSONB)
    embedded_exists = (
        sa_select(1)
        .select_from(asst_elem)
        .join(asst_tool_elem, true())
        .where(asst_tool_val["name"].astext.in_(tool_names))
        .exists()
    )

    return query.where(or_(standalone_exists, embedded_exists))


def compose_comparison_filter(query, model_class, field_name, filter_value):
    """
    Filter for comparison operations (>=, <=, >, <, etc.).

    Args:
        query: The SQLModel query object
        model_class: The model class containing the field
        field_name: The name of the field to filter on
        filter_value: Either a direct value or a dict with operator keys like {">=": value, "<=": value}

    Returns:
        Modified query with comparison filter applied
    """
    field_expr = model_class.get_field_expression(field_name)

    if isinstance(filter_value, dict):
        # Handle dict format with comparison operators
        for operator, value in filter_value.items():
            if operator == ">=":
                query = query.where(field_expr >= value)
            elif operator == "<=":
                query = query.where(field_expr <= value)
            elif operator == ">":
                query = query.where(field_expr > value)
            elif operator == "<":
                query = query.where(field_expr < value)
            elif operator == "==":
                query = query.where(field_expr == value)
            elif operator == "!=":
                query = query.where(field_expr != value)
            else:
                raise ValueError(f"Unsupported comparison operator: {operator}")
    else:
        # Handle direct value - use equality
        query = query.where(field_expr == filter_value)

    return query
