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

"""Find supported context reads without rendering a Jinja template.

The runtime uses ``Template(text)`` with the default environment. Jinja's symbol
analysis supplies the same lexical bindings, including aliases to outer scopes.
An additional assignment walk distinguishes reads before and after a local write:
an undeclared-name set for the entire template cannot make that distinction.
Only complete Name/Getattr output expressions are requirements. Other expressions
still participate in Jinja's binding analysis, but never add requirements.

A macro call with the same bindings as an earlier one reads the same names, so it is
walked once. The walk count is still bounded: a template that needs more than
`MAX_TEMPLATE_WALKS` scope walks raises `TemplateAnalysisCapReached`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jinja2 import Environment, TemplateSyntaxError, nodes
from jinja2.idtracking import Symbols, VAR_LOAD_ALIAS, VAR_LOAD_RESOLVE

# Scope walks one template may need; past it the template is not analysed.
MAX_TEMPLATE_WALKS = 10_000


class TemplateAnalysisCapReached(Exception):
    """The template needs more than `MAX_TEMPLATE_WALKS` scope walks, so its reads are not known.

    `path` and `location` say which configured template it was, once the caller knows.
    """

    def __init__(self, path: str = "", location: object = None) -> None:
        super().__init__(path)
        self.path = path
        self.location = location


def extract_context_names(text: object, *, max_walks: int = MAX_TEMPLATE_WALKS) -> tuple[str, ...]:
    """Return supported free roots in source order, or none for invalid syntax.

    A parse error follows the runtime's raw-template fallback. Parsing and symbol
    analysis do not compile, evaluate, or render any template expression. Raises
    `TemplateAnalysisCapReached` when the template needs more than `max_walks` walks.
    """
    if not isinstance(text, str) or "{" not in text:
        return ()
    # Analysis only, never renders a template; autoescape does not change how it parses
    environment = Environment(autoescape=True)
    try:
        tree = environment.parse(text)
        if not _valid_declarations(tree):
            return ()
        symbols = Symbols()
        symbols.analyze_node(tree)
        analyzer = _OutputReads(frozenset(environment.globals) | {"self"}, symbols, tree, max_walks)
        analyzer.walk(tree.body, symbols, set(), {})
        analyzer.unresolved_macros()
    except (TemplateSyntaxError, RecursionError):
        return ()
    return tuple(sorted(analyzer.names, key=analyzer.names.__getitem__))


def _valid_declarations(tree: nodes.Template) -> bool:
    """Check declaration errors which Jinja's parser leaves to its compiler.

    Do not compile for validation: Jinja's compiler can evaluate user filters
    while folding constant output expressions, even with optimization disabled.
    """
    blocks = [block.name for block in tree.find_all(nodes.Block)]
    if len(blocks) != len(set(blocks)):
        return False
    for loop in tree.find_all(nodes.For):
        if any(name.name == "loop" and name.ctx == "store" for name in loop.find_all(nodes.Name)):
            return False
    for macro in tree.find_all((nodes.Macro, nodes.CallBlock)):
        args = [arg.name for arg in macro.args]
        if len(args) != len(set(args)):
            return False
    return True


@dataclass(eq=False)
class _Macro:
    node: nodes.Macro
    parent: Symbols
    assigned: set[str]
    bindings: dict[str, _Macro] = field(default_factory=dict)
    called: bool = False
    escaped: bool = False


class _OutputReads:
    def __init__(self, globals_: frozenset[str], root_symbols: Symbols, tree: nodes.Template, max_walks: int) -> None:
        self.globals = globals_
        self.root_symbols = root_symbols
        self.names: dict[str, int] = {}
        self.order = {id(node): index for index, node in enumerate(tree.find_all(nodes.Name))}
        self.macros: list[_Macro] = []
        self.active_macros: set[int] = set()
        self.next_scope = 1
        self.max_walks = max_walks
        self.walks = 0
        # Macro calls already walked, by macro and the bindings the call supplies.
        self.walked_calls: set[tuple[int, frozenset[str], frozenset[tuple[str, int]]]] = set()

    def walk(self, body: list[nodes.Node], symbols: Symbols, assigned: set[str], macros: dict[str, _Macro]) -> None:
        for node in body:
            for node_types, handler in self._HANDLERS:
                if isinstance(node, node_types):
                    getattr(self, handler)(node, symbols, assigned, macros)
                    break

    def _output_node(self, node: nodes.Output, symbols: Symbols, assigned: set[str], macros: dict[str, _Macro]) -> None:
        self._output(node, symbols, assigned)
        for expression in node.nodes:
            self._calls(expression, symbols, assigned, macros)

    def _assign_block(
        self, node: nodes.AssignBlock, symbols: Symbols, assigned: set[str], macros: dict[str, _Macro]
    ) -> None:
        self._scope(node, symbols, assigned, macros)
        self._assign(node.target, symbols, assigned, macros)

    def _for(self, node: nodes.For, symbols: Symbols, assigned: set[str], macros: dict[str, _Macro]) -> None:
        self._calls(node.iter, symbols, assigned, macros)
        self._scope(node, symbols, assigned, macros, parameters=("loop",))
        self._scope(node, symbols, assigned, macros, branch="else")

    def _with(self, node: nodes.With, symbols: Symbols, assigned: set[str], macros: dict[str, _Macro]) -> None:
        for value in node.values:
            self._calls(value, symbols, assigned, macros)
        self._scope(node, symbols, assigned, macros)

    def _macro_definition(
        self, node: nodes.Macro, symbols: Symbols, assigned: set[str], macros: dict[str, _Macro]
    ) -> None:
        reference = symbols.ref(node.name)
        macro = _Macro(node, symbols, assigned.copy())
        macros[reference] = macro
        assigned.add(reference)
        macro.bindings = macros.copy()
        self.macros.append(macro)

    def _call_block(
        self, node: nodes.CallBlock, symbols: Symbols, assigned: set[str], macros: dict[str, _Macro]
    ) -> None:
        self._calls(node.call, symbols, assigned, macros)
        self._scope(node, symbols, assigned, macros, parameters=("caller", "kwargs", "varargs"))

    def _block(self, node: nodes.Block, symbols: Symbols, assigned: set[str], macros: dict[str, _Macro]) -> None:
        # An ordinary block has its own render context. A scoped block
        # receives the enclosing locals through Jinja's derived context.
        self._scope(
            node,
            symbols if node.scoped else None,
            assigned if node.scoped else set(),
            macros,
            parameters=("super",),
            context_locals=tuple(name for name, ref in self.root_symbols.refs.items() if ref in assigned),
        )

    def _plain_scope(self, node: nodes.Node, symbols: Symbols, assigned: set[str], macros: dict[str, _Macro]) -> None:
        self._scope(node, symbols, assigned, macros)

    # Statement kinds the walk follows, checked in order; the first match handles the node.
    # Any other statement adds no reads and no bindings.
    _HANDLERS = (
        (nodes.Output, "_output_node"),
        (nodes.If, "_conditional"),
        (nodes.Assign, "_assignment"),
        (nodes.AssignBlock, "_assign_block"),
        (nodes.For, "_for"),
        (nodes.With, "_with"),
        (nodes.Macro, "_macro_definition"),
        (nodes.CallBlock, "_call_block"),
        (nodes.Block, "_block"),
        (nodes.FilterBlock | nodes.Scope | nodes.ScopedEvalContextModifier, "_plain_scope"),
        (nodes.Import | nodes.FromImport, "_import"),
    )

    def _assignment(self, node: nodes.Assign, symbols: Symbols, assigned: set[str], macros: dict[str, _Macro]) -> None:
        alias = self._macro_name(node.node, symbols, macros)
        if alias is None:
            self._calls(node.node, symbols, assigned, macros)
        self._assign(node.target, symbols, assigned, macros)
        if alias is not None:
            if isinstance(node.target, nodes.Name):
                macros[symbols.ref(node.target.name)] = alias
            else:
                alias.escaped = True

    @staticmethod
    def _import(
        node: nodes.Import | nodes.FromImport, symbols: Symbols, assigned: set[str], macros: dict[str, _Macro]
    ) -> None:
        names = (node.target,) if isinstance(node, nodes.Import) else node.names
        for name in names:
            reference = symbols.ref(name[1] if isinstance(name, tuple) else name)
            assigned.add(reference)
            macros.pop(reference, None)

    def _output(self, node: nodes.Output, symbols: Symbols, assigned: set[str]) -> None:
        for expression in node.nodes:
            root = expression
            while isinstance(root, nodes.Getattr):
                root = root.node
            if (
                isinstance(root, nodes.Name)
                and root.name not in self.globals
                and self._reads_context(symbols.ref(root.name), symbols, assigned)
            ):
                position = self.order[id(root)]
                self.names[root.name] = min(position, self.names.get(root.name, position))

    @staticmethod
    def _macro_name(node: nodes.Node, symbols: Symbols, macros: dict[str, _Macro]) -> _Macro | None:
        return macros.get(symbols.ref(node.name)) if isinstance(node, nodes.Name) else None

    def _calls(self, node: nodes.Node, symbols: Symbols, assigned: set[str], macros: dict[str, _Macro]) -> None:
        if isinstance(node, nodes.Call) and (macro := self._macro_name(node.node, symbols, macros)) is not None:
            for child in node.iter_child_nodes(exclude=("node",)):
                self._calls(child, symbols, assigned, macros)
            self._call_macro(macro, assigned, macros)
        else:
            if (macro := self._macro_name(node, symbols, macros)) is not None:
                macro.escaped = True
            for child in node.iter_child_nodes():
                self._calls(child, symbols, assigned, macros)

    def _call_macro(self, macro: _Macro, assigned: set[str], macros: dict[str, _Macro]) -> None:
        macro.called = True
        if id(macro.node) in self.active_macros:
            return
        call = (id(macro.node), frozenset(assigned), frozenset((name, id(item)) for name, item in macros.items()))
        if call in self.walked_calls:
            return
        self.walked_calls.add(call)
        self.active_macros.add(id(macro.node))
        try:
            self._scope(macro.node, macro.parent, assigned, macros, parameters=("caller", "kwargs", "varargs"))
        finally:
            self.active_macros.remove(id(macro.node))

    def unresolved_macros(self) -> None:
        # A known call supplies the current outer assignments, including writes
        # after the definition. An escaped or unresolved callable may run earlier;
        # keep the definition-time uncertainty for those unsupported call paths.
        for macro in self.macros:
            if not macro.called or macro.escaped:
                self._call_macro(macro, macro.assigned, macro.bindings)

    @staticmethod
    def _reads_context(reference: str, symbols: Symbols, assigned: set[str]) -> bool:
        while reference not in assigned:
            load = symbols.find_load(reference)
            if load is None:
                return False
            action, value = load
            if action == VAR_LOAD_RESOLVE:
                return True
            if action != VAR_LOAD_ALIAS:
                return False
            reference = value
        return False

    def _conditional(self, node: nodes.If, symbols: Symbols, assigned: set[str], macros: dict[str, _Macro]) -> None:
        self._calls(node.test, symbols, assigned, macros)
        for branch in node.elif_:
            self._calls(branch.test, symbols, assigned, macros)
        branches = []
        bindings = []
        for body in (node.body, *(branch.body for branch in node.elif_), node.else_):
            branch_assigned = assigned.copy()
            branch_macros = macros.copy()
            self.walk(body, symbols, branch_assigned, branch_macros)
            branches.append(branch_assigned)
            bindings.append(branch_macros)
        assigned.update(set.intersection(*branches))
        macros.clear()
        macros.update(
            {key: value for key, value in bindings[0].items() if all(item.get(key) is value for item in bindings)}
        )

    @staticmethod
    def _assign(target: nodes.Node, symbols: Symbols, assigned: set[str], macros: dict[str, _Macro]) -> None:
        if isinstance(target, nodes.Name):
            reference = symbols.ref(target.name)
            assigned.add(reference)
            macros.pop(reference, None)
        elif isinstance(target, nodes.Tuple):
            for name in target.find_all(nodes.Name):
                reference = symbols.ref(name.name)
                assigned.add(reference)
                macros.pop(reference, None)

    def _scope(  # noqa: PLR0913 - keyword-only builder/internal helper; a parameter object would add indirection without clarity
        self,
        node: nodes.Node,
        parent: Symbols | None,
        assigned: set[str],
        macros: dict[str, _Macro],
        *,
        parameters: tuple[str, ...] = (),
        branch: str = "body",
        context_locals: tuple[str, ...] = (),
    ) -> None:
        self.walks += 1
        if self.walks > self.max_walks:
            raise TemplateAnalysisCapReached
        # Unique references prevent a caller's locals from being mistaken for
        # locals in a different macro or a different invocation of that macro.
        symbols = Symbols(parent, level=self.next_scope)
        self.next_scope += 1
        symbols.analyze_node(node, for_branch=branch)
        for name in parameters:
            symbols.declare_parameter(name)
        local_assignments = assigned.copy()
        # Root assignments are exported to the render context, even for an
        # unscoped block. Loop and macro locals are not exported there.
        local_assignments.update(ref for name in context_locals if (ref := symbols.find_ref(name)) is not None)
        self.walk(node.else_ if branch == "else" else node.body, symbols, local_assignments, macros.copy())
