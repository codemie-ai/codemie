# Technical Research

**Task**: code_executor sandbox_guard filesystem_policy lxml xml-hardening
**Generated**: 2026-10-21
**Research path**: filesystem

---

## 1. Original Context

User request (verbatim): "fix https://jiraeu.epam.com/browse/EPMCDME-16087. i managed to reproduce locally: http://localhost:5173/workflows/92b5ad34-ddb1-40e1-8b36-b9434905089a/workflow-executions/2f0ce360-8e81-45e8-babd-e4bc94e3f325. research, identify the issue and fix"

Ticket EPMCDME-16087 (Bug, Major): "Code executor runtime guard breaks lxml XMLParser and iterparse class APIs, causing FontTools/Matplotlib PDF import failures"
Summary: In CodeMie 2.58.0 the injected code-executor runtime guard (src/codemie_tools/data_management/code_executor/filesystem_policy.py, ~lines 798-822 and 867-878 at tag 2.58.0; line numbers may have moved) replaces two public lxml classes with plain functions:
  - lxml.etree.XMLParser -> _hardened_xmlparser_cls (factory forcing resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False, and adding the instance to a _hardened_parsers tracking set)
  - lxml.etree.iterparse -> _hardened_iterparse (same kwargs forcing, returns _original_iterparse(...))
Construction still works, but subclassing (`class X(etree.XMLParser)`) fails with "TypeError: function() argument 'code' must be code, not str" and isinstance(parser, etree.XMLParser) fails with "TypeError: isinstance() arg 2 must be a type...". FontTools' fontTools/misc/etree.py line 55 subclasses XMLParser at import time, so `import matplotlib.backends.backend_pdf` fails inside the code executor (Jobs, shared-pod and workspace-script execution all use the same guard). Other referenced files: sandbox_guard.py (installs guard before customer code), code_executor_tool.py (build_guarded_python_script, constructs guarded script before Jobs/shared dispatch), models.py.
Requested fix: preserve class-compatible public APIs for BOTH symbols — e.g. hardened subclasses instead of plain factory functions — while retaining XML protections and the existing parser tracking/handling (_hardened_parsers is presumably consulted by wrapped parse functions fromstring/XML/parse to decide whether a caller-supplied parser is trusted).
Acceptance criteria:
  1. Both public symbols support subclass definitions and isinstance checks.
  2. Ordinary XMLParser construction and iterparse iteration continue to work.
  3. FontTools's XML-shim import succeeds with the guard enabled.
  4. Matplotlib PDF backend import, one-/multi-page PDF generation and artifact export succeed with the guard enabled.
  5. XML protections remain enforced: entity resolution disabled, DTD loading disabled, network disabled, huge_tree disabled. Wrapped parse functions and caller-supplied/untracked parser cases retain their intended protection (including subclasses that pass their own kwargs, e.g. FontTools' setdefault("resolve_entities", False) then super().__init__).
  6. Existing workspace-protection regression checks continue to pass.
  7. Validate through a real executor path, not only constructor-level unit tests.
  8. (Process, not code) document backport/hotfix decision.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie_tools/data_management/code_executor/filesystem_policy.py` (1005 lines). `render_runtime_prelude()` returns one large `r"""` template string (lines 49-981). It is dedented, has placeholders substituted, and is executed as source inside the sandbox. The lxml block sits inside `install_guard()`:
  - Lines 792-795: `import lxml.etree as _lxml_etree`, skipped on ImportError.
  - Lines 798-822: stores `_ORIGINALS["lxml.etree.XMLParser"]`, creates `_hardened_parsers: set = set()` with strong refs (the comment says XMLParser instances are not weak-referenceable), and defines the plain function `_hardened_xmlparser_cls(*args, **kwargs)`. That function overwrites the 4 kwargs, builds the original class, adds the instance to the set, and is assigned to `_lxml_etree.XMLParser`.
  - Lines 824-845: `_hardened_default_parser()` calls `_hardened_xmlparser_cls()`. `_force_hardened_parser(parser)` returns `parser` only if it is in `_hardened_parsers`. Otherwise it returns a fresh hardened parser, and it also handles an unhashable arg through `TypeError`.
  - Lines 847-865: wraps `fromstring`, `XML` and `parse`. The positional `parser` (args[1]) is pulled out and passed through `_force_hardened_parser`.
  - Lines 867-878: `_hardened_iterparse(*args, **kwargs)` is a plain function that forces the same 4 kwargs and is assigned to `_lxml_etree.iterparse`.
- `sandbox_guard.py:27-49`: `build_guarded_python_script()` = prelude + `install_guard()` + `exec(compile(customer_code))`. `build_guarded_workspace_script()` (67-94) wraps runpy of a workspace script through the same function.
- Callers: `code_executor_tool.py:466` (before the Jobs/shared dispatch) and `workspace/workspace_script_runner.py:107`.

### Architecture and Layers Affected
- Agent tool / sandbox runtime layer only. The prelude is generated by the **backend** and shipped as script text, so a fix needs no rebuild of the executor image (`models.py:393` default `codemie/codemie-code-executor:2.58.0`, env `CODE_EXECUTOR_DOCKER_IMAGE`).

### Integration Points
- Executor image `codemie-code-executor:2.58.0` (verified in local k3s): **lxml 6.1.0, fontTools 4.66.1, matplotlib 3.9.1**. fontTools 4.66.1 `fontTools/misc/etree.py` does `_XMLParser = XMLParser; class XMLParser(_XMLParser): def __init__(self,*a,**k): k.setdefault("resolve_entities", False); super(XMLParser,self).__init__(*a,**k)` at import time.
- Backend venv (`.venv`, Python 3.12.15): lxml 6.1.0 (`pyproject.toml:173 lxml = "^6.1.0"`), fontTools 4.59.1, which predates the subclass and so cannot reproduce the bug, and matplotlib 3.10.8.
- `tools_vars.py:29,41` advertise `matplotlib` and `lxml` as available sandbox libraries.

### Patterns and Conventions
- Every patched symbol records its original in `_ORIGINALS[...]`. `install_guard()` is idempotent through `if _ORIGINALS: return`.
- Prelude code is plain Python inside a raw string. Helper names are `_`-prefixed and closures capture originals.

**Verified reproduction (real executor image, guarded script built by `build_guarded_python_script`, run in k3s through `ctr run`):**
- `class X(etree.XMLParser)`: TypeError `function() argument 'code' must be code, not str`. `isinstance(p, etree.XMLParser)`: TypeError. `class Y(etree.iterparse)`: same TypeError. `import fontTools.misc.etree`: FAIL. `import matplotlib.backends.backend_pdf`: FAIL.
- **Also broken: `import lxml.html`**, because it defines `XHTMLParser(etree.XMLParser)` at import. This impact is not in the ticket. `lxml.objectify`, `docx`, `openpyxl`, `pptx`, `bs4` and `pandas` import fine.

**Verified type facts (lxml 6.1.0):** `XMLParser` and `iterparse` are both real `type`s and both are subclassable. Python subclass instances of XMLParser **are weak-referenceable** (`weakref.ref(Subclass())` works), so a `weakref.WeakSet` becomes possible. The no-weakref comment at 800-806 applies only to the raw Cython type. Neither has an introspectable signature. iterparse's docstring signature is `iterparse(source, events=("end",), tag=None, attribute_defaults=False, dtd_validation=False, load_dtd=False, no_network=True, ..., resolve_entities='internal', ..., huge_tree=False, ...)`.

**Prototype validated in the executor image** (standalone, not committed): `class XMLParser(_orig)` with `__init__` that does `k.update(SAFE); super().__init__(*a,**k); tracked.add(self)`, plus `class iterparse(_orig_it)` that forces the same kwargs. Results: unsafe kwargs give a tracked parser and XXE text `None`. The FontTools-style subclass is an instance of XMLParser and is tracked. iterparse XXE gives `[None]`. iterparse subclass and isinstance work. `fontTools.misc.etree` imports. A 2-page `PdfPages` PDF was written (7267 bytes).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/testing/local-verification.md`: harness sanity run plus live API calls. `.ai-run/guides/quality-gates.md`: `make test`, `make verify`, and `make test-harness`, whose output the MR description requires.
- `code_executor/README.md` does not mention lxml/XML hardening.

### Architectural Decisions
- Module docstring (`filesystem_policy.py:15-31`): the guard is **defense-in-depth only**. The real boundary is non-root, dropped caps, a read-only rootfs and gVisor (JOBS). The lxml hardening forces safe defaults but does not restrict what gets parsed.
- The lxml hardening came from EPMCDME-13899 (commit 40b76f1ae, first released in 2.43.0). The bug surfaced in 2.58.0 because the executor image's fontTools moved to a version that subclasses XMLParser.

### Derived Conventions
- Security regression tests run the guarded script in a real subprocess and assert on stdout and returncode.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie_tools/data_management/code_executor/test_filesystem_policy.py` (47 tests). Helpers `_run_guarded()`, `_run_guarded_in_workspace()` (`python -c script`) and `_run_guarded_as_file_in_workspace()` (writes script.py, which mirrors production). The lxml tests at lines 537-660 cover: XXE via default `fromstring`, unsafe caller kwargs, positional parser to `fromstring`/`parse`, the `type(etree.XMLParser())` bypass (595-602), an unhashable parser arg, iterparse XXE, and billion-laughs.
- `test_sandbox_guard.py` has no lxml content.

### Testing Framework and Patterns
- pytest with `-n 3` (xdist), `--import-mode=importlib`, `pythonpath = src` (`pytest.ini`). Coverage env vars are stripped for subprocesses (`_clean_env`).

### Coverage Gaps
- No test for subclassing, isinstance, fontTools import, `lxml.html` import or matplotlib PDF under the guard.
- **The guard tests cannot run on Windows**: the prelude does `import resource` (ModuleNotFoundError), and `python -c <script>` exceeds the Windows command-line limit (WinError 206). They must run on Linux (CI, WSL or a container). The backend venv's fontTools 4.59.1 cannot reproduce the fontTools failure. A plain `class X(etree.XMLParser)` or `import lxml.html` test reproduces it independently of the fontTools version.

---

## 5. Configuration and Environment

### Environment Variables
- `CODE_EXECUTOR_DOCKER_IMAGE` (`models.py:393`). The prelude sets `MPLCONFIGDIR=.matplotlib` and the `XDG_*` defaults (`filesystem_policy.py:715-718`).

### Configuration Files
- `default_security_policies.yaml`, `docker/k3s/manifests/code-executor.yaml`, `deploy-templates/values.yaml`. None governs lxml.

### Feature Flags and Deployment Concerns
- No flag for the lxml hardening. The fix is backend-only because the prelude is injected at runtime.

---

## 6. Risk Indicators

- `filesystem_policy.py:595-602`: the existing test `type(etree.XMLParser())` currently yields the raw class. After the change it yields the hardened subclass, which still forces safe kwargs. The test should keep passing, but its meaning shifts. `etree.XMLParser.__mro__[1]` / `__bases__[0]` will expose the original. That gives no new capability, because untracked instances are replaced in the parse wrappers.
- Speculative: the subclass `__init__` must overwrite the kwargs after a user subclass's `setdefault` (FontTools), which the prototype's `k.update` does. Tracking can switch to `weakref.WeakSet`, which removes the unbounded strong-ref set described at lines 800-810.
- **Pre-existing bypass, not introduced by the fix**: calling the original Cython `__init__` on an already-tracked instance re-initialises it with unsafe settings. In the prototype `_orig.__init__(p, resolve_entities=True, ...)` then `fromstring(xxe, p)` read `/etc/passwd`. Today's code has the same exposure through `type(p).__init__`. Accepted under the defense-in-depth classification; flag it, don't widen the scope.
- `XMLPullParser()` (an lxml-internal subclass of the original XMLParser) is not an instance of the hardened subclass (`isinstance` False) and is not patched today. iterparse's original type stays reachable through `__bases__` or `_ORIGINALS`, as it already is today.
- Speculative: positional args that collide with forced kwargs (for example iterparse `load_dtd` passed positionally) raise `TypeError: multiple values`. This behaviour already exists today.
- The guard tests are Linux-only, so local verification on this Windows host needs WSL or a container (for example running the guarded script in the `codemie-code-executor:2.58.0` image through k3s `ctr run`, as done here).
- matplotlib emits an `fc-list` `subprocess.Popen` denial marker in stderr during font discovery. It is non-fatal but shows up in the denial events of PDF runs.
- Process: AC 8 (backport/hotfix decision) is not a code artifact. The regression affects all releases whose executor image ships fontTools with the subclass shim, which includes 2.58.0.

---

## 7. Summary for Complexity Assessment

The change is confined to one block of the runtime prelude template in `filesystem_policy.py` (lines ~797-878), with regression tests in `test_filesystem_policy.py`. No API, DB, config or image change is involved: the backend injects the prelude at dispatch (`code_executor_tool.py:466`, `workspace_script_runner.py:107`), so all three execution paths (Jobs, shared pod, workspace script) pick up the fix together. The approach is already proven in the real 2.58.0 executor image: replace the two plain functions with hardened subclasses of `lxml.etree.XMLParser` and `lxml.etree.iterparse`, keep tracking instances (now possible with a `WeakSet`), and leave the `fromstring`/`XML`/`parse` wrappers unchanged. With that, subclassing, isinstance, the fontTools import, `lxml.html` import and multi-page PdfPages output all work, and XXE stays blocked.

Novelty is low; the code is about 20 lines inside a string template. The real risk lies in security semantics. The existing lxml regression suite (8 subprocess tests) must keep passing. New tests are needed for subclass and isinstance on both symbols, a FontTools-style `setdefault` subclass that still blocks XXE, `import lxml.html`, and a matplotlib PDF write. Testing has one practical friction point: the guard tests run only on Linux (`resource` module, command-line length), and the backend venv's fontTools 4.59.1 lacks the subclass shim. A real-executor validation (AC 7) is easiest through the local k3s executor image or a live workflow run.

The ticket understates the impact: `import lxml.html` is broken by the same cause. A pre-existing re-initialisation bypass (calling the original `__init__` on a tracked parser) remains and is out of scope given the module's defense-in-depth classification.

---

## 8. External References

- Ticket URL `https://jiraeu.epam.com/browse/EPMCDME-16087`: content supplied verbatim in task_context; not fetched.
- Local repro URL (workflow execution `2f0ce360-...`): not inspected. The failure was reproduced directly in the `docker.io/codemie/codemie-code-executor:2.58.0` image instead.
- `fontTools/misc/etree.py` (fontTools 4.66.1 inside the executor image), verbatim excerpt: `_XMLParser = XMLParser` / `class XMLParser(_XMLParser):` / `def __init__(self, *args, **kwargs): kwargs.setdefault("resolve_entities", False); super(XMLParser, self).__init__(*args, **kwargs)`. The comment cites lxml bug 2165901 (external parameter entities fetched with `resolve_entities="internal"` up to lxml 6.1.2).
