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
from sqlmodel import case, or_

from codemie.core.db_utils import escape_like_wildcards
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
