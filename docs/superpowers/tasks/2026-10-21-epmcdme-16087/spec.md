# Spec: class-compatible lxml hardening in the code-executor guard (EPMCDME-16087)

## Problem

The runtime prelude in `src/codemie_tools/data_management/code_executor/filesystem_policy.py` (lxml block inside `install_guard()`, ~lines 792-878) replaces `lxml.etree.XMLParser` and `lxml.etree.iterparse` with plain functions. Construction still works. Subclassing raises `TypeError: function() argument 'code' must be code, not str`, and `isinstance(x, etree.XMLParser)` raises too. As a result these imports fail inside every executor path (Jobs, shared pod, workspace script):
- `fontTools.misc.etree`, which subclasses `XMLParser` at import (fontTools 4.66.1 in the `codemie-code-executor:2.58.0` image)
- `matplotlib.backends.backend_pdf`, which imports `fontTools.misc.etree`
- `lxml.html`, which defines `XHTMLParser(etree.XMLParser)` at import

## Approach

Replace both plain functions with hardened **subclasses** of the original lxml types, defined in the same prelude block:

- **`XMLParser` subclass.** `__init__` overwrites the four protected kwargs with `resolve_entities=False`, `load_dtd=False`, `no_network=True` and `huge_tree=False`. It does this *after* any caller or subclass defaults, so a FontTools-style `kwargs.setdefault(...)` followed by `super().__init__` is still forced safe. It then calls the original `__init__` and registers the instance as hardened. User subclasses inherit this behaviour automatically.
- **`iterparse` subclass.** It forces the same four kwargs before it delegates to the original type.
- **Parser tracking.** It keeps its current role: `_force_hardened_parser` trusts tracked parsers and replaces untracked or unhashable ones. Storage moves from the strong-ref `set` to a `weakref.WeakSet`. Python subclass instances are weak-referenceable, so the stale no-weakref comment goes away.
- `_ORIGINALS[...]` entries and the `fromstring` / `XML` / `parse` wrappers are unchanged. `_hardened_default_parser()` builds the new subclass.

Validated by a prototype in the real 2.58.0 executor image (see technical-analysis.md §2). No image, config or API change is needed: the backend injects the prelude at dispatch (`code_executor_tool.py:466`, `workspace/workspace_script_runner.py:107`).

## Acceptance criteria

1. `class X(etree.XMLParser)` and `class Y(etree.iterparse)` can be defined under the guard. `isinstance(etree.XMLParser(), etree.XMLParser)` is `True`, and the same holds for an `iterparse` instance.
2. Plain `etree.XMLParser(...)` construction and parsing work, and `for ev, el in etree.iterparse(src)` iterates normally.
3. `import lxml.html` succeeds under the guard.
4. A FontTools-style subclass (`setdefault("resolve_entities", False)` then `super().__init__`) imports and constructs. Its instances are tracked, and XXE through it yields no file content.
5. Under the guard, matplotlib can write a one-page PDF (`savefig(..., format="pdf")`) and a multi-page `PdfPages` PDF to the workspace, and both files are non-empty.
6. XML protections hold. XXE is blocked for: the default parser, a parser built with unsafe kwargs, a user subclass passing unsafe kwargs, an untracked or unhashable parser passed to `fromstring` / `XML` / `parse`, and `iterparse` with unsafe kwargs. Billion-laughs is still rejected.
7. Every existing test in `tests/codemie_tools/data_management/code_executor/test_filesystem_policy.py` and `test_sandbox_guard.py` passes unchanged. That includes the `type(etree.XMLParser())` case, which now yields the hardened subclass.
8. New criteria 1-6 tests follow the existing subprocess pattern (`_run_guarded*` helpers, assertions on stdout and returncode), and they run on Linux.
9. **Local verification on the user's running stack, Jobs path.** The precondition is already met: the "before" evidence is in `local-verification.md` (the reporter workflow fails identically, the verification workflow's `lxml_checks` step scores 6/15 PASS, and its `pdf_export` step fails with the ticket's matplotlib traceback). The stack uses `uvicorn --reload` serving the branch code, with executor Jobs in local k3s running `codemie-code-executor:2.58.0` and the default `CODE_EXECUTOR_SANDBOX_MODE=sandbox-jobs`. After the fix, both local workflows are re-run and the results recorded in `local-verification.md` under "After the fix". This criterion is met when all of the following hold:
   - every step of the reporter workflow (92b5ad34-...) ends Succeeded
   - the verification workflow's `lxml_checks` step reports 15/15 PASS
   - its `pdf_export` step ends Succeeded, with the one-page and the 3-page PDFs both exported as artifacts
10. **Shared path (conditional).** The same verification workflow is re-run against a backend started with `CODE_EXECUTOR_SANDBOX_MODE=sandbox-shared`, with the same pass bar as criterion 9, and the result goes in `local-verification.md`. This needs a second, temporary local backend instance or a restart of the user's backend. Do it only after the user explicitly approves, never silently. If the user declines, record "not run" with the reason.

## Non-goals

- The pre-existing re-init bypass: calling the original Cython `__init__` on a tracked parser re-enables unsafe settings.
- Patching `lxml.etree.XMLPullParser`, or other lxml entry points that are not hardened today.
- Hiding the originals reachable through `__bases__` / `_ORIGINALS`. That gives no new capability, because untracked parsers are still replaced.
- Changes to the executor image, its fontTools/matplotlib versions, or `CODE_EXECUTOR_DOCKER_IMAGE`.
- Suppressing matplotlib's non-fatal `fc-list` `subprocess.Popen` denial marker.
- The backport/hotfix decision (ticket AC 8). This is a process item, recorded in the handoff note and not in code.
- Making the guard tests runnable on Windows.
