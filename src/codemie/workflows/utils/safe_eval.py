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

"""AST-based safe expression evaluator for workflow expressions.

Replaces eval() with a whitelist-based AST walker that only permits
safe node types. Any unrecognized or dangerous construct raises SafeEvalError.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

from codemie.workflows.constants import SAFE_EVAL_ALLOWED_FUNCTIONS as ALLOWED_FUNCTIONS


class SafeEvalError(ValueError):
    """Raised when an expression uses disallowed constructs."""


_COMPARE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}

_ALLOWED_FUNCTION_OBJECTS: frozenset = frozenset(ALLOWED_FUNCTIONS.values())

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


class _SafeEvaluator:
    """Recursive AST evaluator that only processes whitelisted node types."""

    def __init__(self, local_vars: dict[str, Any]) -> None:
        self._locals = local_vars
        self._handlers: dict[type, Any] = {
            ast.Expression: self._handle_expression,
            ast.Constant: self._handle_constant,
            ast.Name: self._handle_name,
            ast.UnaryOp: self._handle_unary_op,
            ast.BinOp: self._handle_bin_op,
            ast.BoolOp: self._handle_bool_op,
            ast.Compare: self._handle_compare,
            ast.IfExp: self._handle_if_exp,
            ast.Attribute: self._handle_attribute,
            ast.Subscript: self._handle_subscript,
            ast.List: self._handle_list,
            ast.Tuple: self._handle_tuple,
            ast.Set: self._handle_set,
            ast.Dict: self._handle_dict,
            ast.Call: self._handle_call,
            ast.Slice: self._handle_slice,
            ast.JoinedStr: self._handle_joined_str,
            ast.FormattedValue: self._handle_formatted_value,
        }

    def eval(self, node: ast.AST) -> Any:
        handler = self._handlers.get(type(node))
        if handler is None:
            raise SafeEvalError(f"Disallowed expression type: {type(node).__name__}")
        return handler(node)

    def _handle_expression(self, node: ast.Expression) -> Any:
        return self.eval(node.body)

    def _handle_constant(self, node: ast.Constant) -> Any:
        return node.value

    def _handle_name(self, node: ast.Name) -> Any:
        if node.id in self._locals:
            return self._locals[node.id]
        if node.id in ALLOWED_FUNCTIONS:
            return ALLOWED_FUNCTIONS[node.id]
        raise SafeEvalError(f"Undefined name: '{node.id}'")

    def _handle_unary_op(self, node: ast.UnaryOp) -> Any:
        operand = self.eval(node.operand)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return +operand
        raise SafeEvalError(f"Disallowed unary operator: {type(node.op).__name__}")

    def _handle_bin_op(self, node: ast.BinOp) -> Any:
        op_func = _BIN_OPS.get(type(node.op))
        if op_func is None:
            raise SafeEvalError(f"Disallowed binary operator: {type(node.op).__name__}")
        left = self.eval(node.left)
        right = self.eval(node.right)
        return op_func(left, right)

    def _handle_bool_op(self, node: ast.BoolOp) -> Any:
        if isinstance(node.op, ast.And):
            result = True
            for value in node.values:
                result = self.eval(value)
                if not result:
                    return result
            return result
        if isinstance(node.op, ast.Or):
            result = False
            for value in node.values:
                result = self.eval(value)
                if result:
                    return result
            return result
        raise SafeEvalError(f"Disallowed boolean operator: {type(node.op).__name__}")

    def _handle_compare(self, node: ast.Compare) -> Any:
        left = self.eval(node.left)
        for op, comparator_node in zip(node.ops, node.comparators, strict=False):
            right = self.eval(comparator_node)
            if not self._apply_compare_op(left, right, op):
                return False
            left = right
        return True

    def _apply_compare_op(self, left: Any, right: Any, op: ast.cmpop) -> bool:
        if isinstance(op, ast.In):
            try:
                return left in right
            except TypeError as e:
                raise SafeEvalError(f"Membership test failed: {e}") from None
        if isinstance(op, ast.NotIn):
            try:
                return left not in right
            except TypeError as e:
                raise SafeEvalError(f"Membership test failed: {e}") from None
        op_func = _COMPARE_OPS.get(type(op))
        if op_func is None:
            raise SafeEvalError(f"Disallowed comparison operator: {type(op).__name__}")
        return op_func(left, right)

    def _handle_if_exp(self, node: ast.IfExp) -> Any:
        if self.eval(node.test):
            return self.eval(node.body)
        return self.eval(node.orelse)

    def _handle_attribute(self, node: ast.Attribute) -> Any:
        if node.attr.startswith("_"):
            raise SafeEvalError(f"Access to private/dunder attribute '{node.attr}' is not allowed")
        obj = self.eval(node.value)
        try:
            return getattr(obj, node.attr)
        except AttributeError:
            raise SafeEvalError(f"Object has no attribute '{node.attr}'") from None

    def _handle_subscript(self, node: ast.Subscript) -> Any:
        obj = self.eval(node.value)
        key = self.eval(node.slice)
        try:
            return obj[key]
        except (KeyError, IndexError, TypeError) as e:
            raise SafeEvalError(f"Subscript access failed: {e}") from None

    def _handle_list(self, node: ast.List) -> Any:
        return [self.eval(el) for el in node.elts]

    def _handle_tuple(self, node: ast.Tuple) -> Any:
        return tuple(self.eval(el) for el in node.elts)

    def _handle_set(self, node: ast.Set) -> Any:
        try:
            return {self.eval(el) for el in node.elts}
        except TypeError as e:
            raise SafeEvalError(f"Set element is not hashable: {e}") from None

    def _handle_dict(self, node: ast.Dict) -> Any:
        try:
            return {self.eval(k): self.eval(v) for k, v in zip(node.keys, node.values, strict=False)}
        except TypeError as e:
            raise SafeEvalError(f"Dict key is not hashable: {e}") from None

    def _handle_call(self, node: ast.Call) -> Any:
        if any(isinstance(a, ast.Starred) for a in node.args):
            raise SafeEvalError("Starred arguments are not allowed in function calls")

        args = [self.eval(arg) for arg in node.args]
        kwargs = {kw.arg: self.eval(kw.value) for kw in node.keywords if kw.arg is not None}
        if any(kw.arg is None for kw in node.keywords):
            raise SafeEvalError("**kwargs unpacking is not allowed in function calls")

        if isinstance(node.func, ast.Name):
            if node.func.id not in ALLOWED_FUNCTIONS:
                raise SafeEvalError(f"Function '{node.func.id}' is not allowed")
            return ALLOWED_FUNCTIONS[node.func.id](*args, **kwargs)

        if isinstance(node.func, ast.Attribute):
            return self._call_method_on_attribute(node.func, args, kwargs)

        raise SafeEvalError("Only direct function calls and method calls are allowed")

    def _call_method_on_attribute(self, func_node: ast.Attribute, args: list, kwargs: dict) -> Any:
        if func_node.attr.startswith("_"):
            raise SafeEvalError(f"Calling private/dunder method '{func_node.attr}' is not allowed")
        obj = self.eval(func_node.value)
        try:
            obj_is_builtin = obj in _ALLOWED_FUNCTION_OBJECTS
        except TypeError:
            obj_is_builtin = False
        if obj_is_builtin:
            raise SafeEvalError(
                f"Method calls via attribute access on built-in types are not allowed: "
                f"{type(obj).__name__}.{func_node.attr}"
            )
        method = getattr(obj, func_node.attr, None)
        if method is None:
            raise SafeEvalError(f"Object has no method '{func_node.attr}'")
        if not callable(method):
            raise SafeEvalError(f"'{func_node.attr}' is not callable")
        return method(*args, **kwargs)

    def _handle_slice(self, node: ast.Slice) -> Any:
        lower = self.eval(node.lower) if node.lower else None
        upper = self.eval(node.upper) if node.upper else None
        step = self.eval(node.step) if node.step else None
        return slice(lower, upper, step)

    def _handle_joined_str(self, node: ast.JoinedStr) -> str:
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            else:
                parts.append(str(self.eval(value)))
        return "".join(parts)

    def _handle_formatted_value(self, node: ast.FormattedValue) -> Any:
        value = self.eval(node.value)
        if node.format_spec:
            fmt = self.eval(node.format_spec)
            return format(value, fmt)
        return value


def safe_eval(expr: str, local_vars: dict[str, Any] | None = None) -> Any:
    """Safely evaluate an expression using AST-based whitelisting.

    Args:
        expr: Expression string to evaluate
        local_vars: Variables available within the expression

    Returns:
        The result of evaluating the expression

    Raises:
        SafeEvalError: If the expression uses disallowed constructs
        SyntaxError: If the expression cannot be parsed
    """
    if not isinstance(expr, str) or not expr.strip():
        raise SafeEvalError("Expression must be a non-empty string")
    tree = ast.parse(expr, mode="eval")
    evaluator = _SafeEvaluator(local_vars or {})
    return evaluator.eval(tree)
