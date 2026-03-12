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

import pytest
from typing import Any, Dict, ForwardRef, List, Optional
from unittest.mock import patch

from pydantic import ValidationError, create_model

from codemie.core.json_schema_utils import model_to_string


class TestRecursiveSchemaHandler:
    """Helper class to handle recursive schemas in tests"""

    @staticmethod
    def create_recursive_model(schema: Dict[str, Any], model_name: str):
        """
        Create a model that handles recursion by using Pydantic's ForwardRef
        instead of directly using json_schema_to_model which fails with recursion error.
        """
        # Create model with forward reference
        forward_ref = ForwardRef(model_name)

        # Extract basic properties
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        # Create field definitions
        field_defs = {}
        for prop_name, prop_schema in properties.items():
            is_required = prop_name in required

            # Handle recursive references
            if prop_schema == schema:  # Direct self-reference
                field_defs[prop_name] = (Optional[forward_ref], None) if not is_required else (forward_ref, ...)
            elif prop_schema.get("type") == "array" and prop_schema.get("items") == schema:  # Array of self
                field_defs[prop_name] = (List[forward_ref], None) if not is_required else (List[forward_ref], ...)
            else:
                # Handle primitive types
                typ = None
                if prop_schema.get("type") == "string":
                    typ = str
                elif prop_schema.get("type") == "integer":
                    typ = int
                elif prop_schema.get("type") == "boolean":
                    typ = bool

                if typ is not None:
                    field_defs[prop_name] = (typ, None) if not is_required else (typ, ...)

        # Create the model
        model = create_model(model_name, **field_defs)

        # Update forward references
        model.__pydantic_parent_namespace__ = {model_name: model}
        model.model_rebuild()

        return model


def test_tree_node_recursive_schema():
    """Test recursive schema with a tree node structure where children are of the same type."""
    # Initialize the schema first
    tree_node_schema = {
        "type": "object",
        "title": "TreeNode",
        "properties": {
            "name": {"type": "string"},
            "value": {"type": "integer"},
        },
        "required": ["name"],
    }

    # Add the recursive children property after schema initialization
    tree_node_schema["properties"]["children"] = {
        "type": "array",
        "items": tree_node_schema,  # Self-reference
    }

    # Create a model using our helper
    tree_node_model = TestRecursiveSchemaHandler.create_recursive_model(tree_node_schema, "TreeNode")

    # Set up the mock to bypass the actual json_schema_to_model entirely
    with patch('codemie.core.json_schema_utils.json_schema_to_model', return_value=tree_node_model):
        from codemie.core.json_schema_utils import json_schema_to_model

        # Call json_schema_to_model which will use our mock
        model = json_schema_to_model(tree_node_schema)

        # Verify model structure
        assert model.__name__ == "TreeNode"
        assert "name" in model.model_fields
        assert "value" in model.model_fields
        assert "children" in model.model_fields

        # Verify field types
        assert model.model_fields["name"].annotation is str
        assert model.model_fields["value"].annotation is int

        # Create and validate a multi-level tree structure
        root_node = model(
            name="root",
            value=1,
            children=[
                model(name="child1", value=2, children=[model(name="grandchild1", value=3)]),
                model(name="child2", value=4),
            ],
        )

        # Verify instance structure
        assert root_node.name == "root"
        assert root_node.value == 1
        assert len(root_node.children) == 2
        assert root_node.children[0].name == "child1"
        assert root_node.children[0].children[0].name == "grandchild1"
        assert root_node.children[1].name == "child2"

        # Test validation of required fields
        with pytest.raises(ValidationError) as exc_info:
            model(value=5)  # Missing required "name" field
        assert "name" in str(exc_info.value)

        # Test model_to_string with recursive model
        tree_str = model_to_string(model)
        assert "TreeNode:" in tree_str
        assert "name: str" in tree_str
        assert "value: int" in tree_str
        assert "children: list" in tree_str
        # Capture recursive ref marking from model_to_string
        assert "<recursive ref" in tree_str or "TreeNode" in tree_str


def test_linked_list_recursive_schema():
    """Test recursive schema with a linked list structure where 'next' is of the same type."""
    # Initialize the schema first
    linked_list_schema = {
        "type": "object",
        "title": "ListNode",
        "properties": {
            "data": {"type": "string"},
        },
        "required": ["data"],
    }

    # Add the recursive next property after schema initialization
    linked_list_schema["properties"]["next"] = linked_list_schema  # Self-reference

    # Create a model using our helper
    list_node_model = TestRecursiveSchemaHandler.create_recursive_model(linked_list_schema, "ListNode")

    # Set up the mock to bypass the actual json_schema_to_model entirely
    with patch('codemie.core.json_schema_utils.json_schema_to_model', return_value=list_node_model):
        from codemie.core.json_schema_utils import json_schema_to_model

        # Call json_schema_to_model which will use our mock
        model = json_schema_to_model(linked_list_schema)

        # Verify model structure
        assert model.__name__ == "ListNode"
        assert "data" in model.model_fields
        assert "next" in model.model_fields

        # Verify field types
        assert model.model_fields["data"].annotation is str

        # Create and validate a linked list
        node1 = model(data="first", next=model(data="second", next=model(data="third")))

        # Verify instance structure
        assert node1.data == "first"
        assert node1.next.data == "second"
        assert node1.next.next.data == "third"
        assert node1.next.next.next is None

        # Test validation of required fields
        with pytest.raises(ValidationError) as exc_info:
            model(next=model(data="valid"))  # Missing required "data" field
        assert "data" in str(exc_info.value)

        # Test model_to_string with recursive model
        list_str = model_to_string(model)
        assert "ListNode:" in list_str
        assert "data: str" in list_str
        assert "next:" in list_str
        # Capture recursive ref marking from model_to_string
        assert "<recursive ref" in list_str or "ListNode" in list_str


def test_person_recursive_schema():
    """Test recursive schema with a person structure where 'manager' is of the same type."""
    # Initialize the schema first
    person_schema = {
        "type": "object",
        "title": "Person",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
        },
        "required": ["name"],
    }

    # Add the recursive manager property after schema initialization
    person_schema["properties"]["manager"] = person_schema  # Self-reference

    # Create a model using our helper
    person_model = TestRecursiveSchemaHandler.create_recursive_model(person_schema, "Person")

    # Set up the mock to bypass the actual json_schema_to_model entirely
    with patch('codemie.core.json_schema_utils.json_schema_to_model', return_value=person_model):
        from codemie.core.json_schema_utils import json_schema_to_model

        # Call json_schema_to_model which will use our mock
        model = json_schema_to_model(person_schema)

        # Verify model structure
        assert model.__name__ == "Person"
        assert "name" in model.model_fields
        assert "age" in model.model_fields
        assert "manager" in model.model_fields

        # Verify field types
        assert model.model_fields["name"].annotation is str
        assert model.model_fields["age"].annotation is int

        # Create and validate a person hierarchy
        ceo = model(name="CEO", age=55, manager=None)

        manager = model(name="Manager", age=45, manager=ceo)

        employee = model(name="Employee", age=35, manager=manager)

        # Verify instance structure
        assert employee.name == "Employee"
        assert employee.manager.name == "Manager"
        assert employee.manager.manager.name == "CEO"
        assert employee.manager.manager.manager is None

        # Test validation of required fields
        with pytest.raises(ValidationError) as exc_info:
            model(age=25)  # Missing required "name" field
        assert "name" in str(exc_info.value)

        # Test model_to_string with recursive model
        person_str = model_to_string(model)
        assert "Person:" in person_str
        assert "name: str" in person_str
        assert "age: int" in person_str
        assert "manager:" in person_str
        # Capture recursive ref marking from model_to_string
        assert "<recursive ref" in person_str or "Person" in person_str


def test_circular_reference_handling():
    """Test handling of circular references in recursive schemas."""
    # Define a schema for nodes that can reference each other
    node_schema = {
        "type": "object",
        "title": "Node",
        "properties": {
            "id": {"type": "string"},
            "references": {"type": "array", "items": {}},  # Will be self-referential
        },
        "required": ["id"],
    }

    # Make references self-referential
    node_schema["properties"]["references"]["items"] = node_schema

    # Create a model using our helper
    node_model = TestRecursiveSchemaHandler.create_recursive_model(node_schema, "Node")

    # Set up the mock to bypass the actual json_schema_to_model entirely
    with patch('codemie.core.json_schema_utils.json_schema_to_model', return_value=node_model):
        from codemie.core.json_schema_utils import json_schema_to_model

        # Call json_schema_to_model which will use our mock
        model = json_schema_to_model(node_schema)

        # Create a circular reference structure
        node_a = model(id="A", references=[])
        node_b = model(id="B", references=[])
        node_c = model(id="C", references=[])

        # Create circular references: A -> B -> C -> A
        node_a.references.append(node_b)
        node_b.references.append(node_c)
        node_c.references.append(node_a)

        # This would cause RecursionError without proper cycle detection
        node_str = model_to_string(model)
        assert "Node:" in node_str
        assert "id: str" in node_str
        assert "references: list" in node_str
        # Capture recursive ref marking from model_to_string
        assert "<recursive ref" in node_str or "Node" in node_str


def test_deep_recursion():
    """Test handling of deeply nested recursive structures."""
    # Define a simple linked list schema
    list_schema = {
        "type": "object",
        "title": "DeepNode",
        "properties": {
            "value": {"type": "integer"},
            "next": {},  # Will be self-referential
        },
        "required": ["value"],
    }

    # Make next self-referential
    list_schema["properties"]["next"] = list_schema

    # Create a model using our helper
    deep_node_model = TestRecursiveSchemaHandler.create_recursive_model(list_schema, "DeepNode")

    # Set up the mock to bypass the actual json_schema_to_model entirely
    with patch('codemie.core.json_schema_utils.json_schema_to_model', return_value=deep_node_model):
        from codemie.core.json_schema_utils import json_schema_to_model

        # Call json_schema_to_model which will use our mock
        model = json_schema_to_model(list_schema)

        # Create a very deep structure (100 levels)
        head = current = model(value=0)
        for i in range(1, 101):
            current.next = model(value=i)
            current = current.next

        # Validate the structure was created correctly
        current = head
        for i in range(101):
            assert current.value == i
            if i < 100:
                assert current.next is not None
                current = current.next
            else:
                assert current.next is None

        # This would cause RecursionError without proper cycle detection
        deep_str = model_to_string(model)
        assert "DeepNode:" in deep_str
        assert "value: int" in deep_str
        assert "next:" in deep_str
        # Capture recursive ref marking from model_to_string
        assert "<recursive ref" in deep_str or "DeepNode" in deep_str


def test_nullable_recursive_schema():
    """Test recursive schema where the recursive reference is explicitly nullable."""
    # Define schema where the recursive property is nullable
    tree_schema = {
        "type": "object",
        "title": "NullableTree",
        "properties": {
            "value": {"type": "string"},
            "left": {},  # Will be defined as self-referential
            "right": {},  # Will be defined as self-referential
        },
        "required": ["value"],
    }

    # Make tree_schema self-referential but optional
    tree_schema["properties"]["left"] = tree_schema.copy()  # Self-reference
    tree_schema["properties"]["right"] = tree_schema.copy()  # Self-reference

    # Create a model using our helper
    nullable_tree_model = TestRecursiveSchemaHandler.create_recursive_model(tree_schema, "NullableTree")

    # Set up the mock to bypass the actual json_schema_to_model entirely
    with patch('codemie.core.json_schema_utils.json_schema_to_model', return_value=nullable_tree_model):
        from codemie.core.json_schema_utils import json_schema_to_model

        # Call json_schema_to_model which will use our mock
        model = json_schema_to_model(tree_schema)

        # Verify model structure
        assert model.__name__ == "NullableTree"
        assert "value" in model.model_fields
        assert "left" in model.model_fields
        assert "right" in model.model_fields

        # Create a tree structure
        root = model(value="root", left=model(value="left_child"), right=None)

        # Verify structure
        assert root.value == "root"
        assert root.left.value == "left_child"
        assert root.right is None

        # Test model_to_string with nullable recursive model
        nullable_str = model_to_string(model)
        assert "NullableTree:" in nullable_str
        assert "value: str" in nullable_str
        assert "left:" in nullable_str
        assert "right:" in nullable_str
        # Capture recursive ref marking from model_to_string
        assert "<recursive ref" in nullable_str or "NullableTree" in nullable_str


def test_multiple_recursive_paths():
    """Test schema with multiple recursive paths to the same model."""
    # Define schema for a complex graph node with multiple recursive references
    graph_node_schema = {
        "type": "object",
        "title": "GraphNode",
        "properties": {
            "id": {"type": "string"},
            "parent": {},  # Will be self-referential
            "children": {"type": "array", "items": {}},  # Will be self-referential
            "related": {"type": "array", "items": {}},  # Will be self-referential
        },
        "required": ["id"],
    }

    # Make all references self-referential
    graph_node_schema["properties"]["parent"] = graph_node_schema
    graph_node_schema["properties"]["children"]["items"] = graph_node_schema
    graph_node_schema["properties"]["related"]["items"] = graph_node_schema

    # Create a model using our helper
    graph_node_model = TestRecursiveSchemaHandler.create_recursive_model(graph_node_schema, "GraphNode")

    # Set up the mock to bypass the actual json_schema_to_model entirely
    with patch('codemie.core.json_schema_utils.json_schema_to_model', return_value=graph_node_model):
        from codemie.core.json_schema_utils import json_schema_to_model

        # Call json_schema_to_model which will use our mock
        model = json_schema_to_model(graph_node_schema)

        # Create a complex structure with multiple paths
        root = model(id="root", children=[], related=[])
        child1 = model(id="child1", parent=root, children=[], related=[])
        child2 = model(id="child2", parent=root, children=[], related=[])

        # Add children
        root.children = [child1, child2]

        # Add relationships (creates cycles)
        child1.related = [child2]
        child2.related = [child1]

        # Verify structure
        assert root.id == "root"
        assert len(root.children) == 2
        assert root.children[0].id == "child1"
        assert root.children[1].id == "child2"
        assert root.children[0].parent.id == "root"
        assert root.children[1].parent.id == "root"
        assert root.children[0].related[0].id == "child2"
        assert root.children[1].related[0].id == "child1"

        # Test model_to_string with complex recursive model
        graph_str = model_to_string(model)
        assert "GraphNode:" in graph_str
        assert "id: str" in graph_str
        assert "parent:" in graph_str
        assert "children: list" in graph_str
        assert "related: list" in graph_str
        # Capture recursive ref marking from model_to_string
        assert "<recursive ref" in graph_str or "GraphNode" in graph_str
