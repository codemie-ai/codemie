# Class-compatible lxml hardening in the code-executor guard: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the plain-function `lxml.etree.XMLParser` / `iterparse` overrides in the runtime prelude with hardened subclasses, so subclassing, `isinstance`, `lxml.html`, fontTools and the matplotlib PDF backend work, and XXE stays blocked.

**Architecture:** The edit stays inside the lxml block of `install_guard()` in the prelude template (`src/codemie_tools/data_management/code_executor/filesystem_policy.py:792-878`). Tests go in `tests/codemie_tools/data_management/code_executor/test_filesystem_policy.py` and follow the existing subprocess pattern (`_run_guarded*`, assertions on stdout and returncode).

**Spec:** `docs/superpowers/tasks/2026-10-21-epmcdme-16087/spec.md`

Commit per task using the repository's existing convention (`EPMCDME-16087: <summary>`).

## Global Constraints

- Forced kwargs, exactly: `resolve_entities=False`, `load_dtd=False`, `no_network=True`, `huge_tree=False`, applied *after* caller/subclass kwargs.
- Leave unchanged: the `_ORIGINALS[...]` keys, the `fromstring`/`XML`/`parse` wrappers (847-865), and every existing test.
- Out of scope (spec Non-goals): the re-init bypass, `XMLPullParser` and other lxml entry points, hiding `__bases__`/`_ORIGINALS`, image/dependency versions (no `pyproject.toml` change), the `fc-list` denial marker, making guard tests run on Windows.

## Running the guard tests (Linux only)

The prelude imports `resource`, so these tests cannot run on this Windows host. Run them in a container whose versions match the executor image (podman; WSL with the same pip set works too). Refer to this command below as `GUARD_PYTEST <selector>`:

```bash
podman run --rm -v "C:/Users/KostiantynPshenychny/Documents/epm-cdme/repos/codemie:/app" -w /app python:3.12-slim \
  sh -c "pip install -q pytest pytest-xdist pytest-env python-dotenv lxml==6.1.0 fonttools==4.66.1 matplotlib==3.9.1 \
  && python -m pytest -p no:cacheprovider <selector>"
```

Use `fonttools==4.66.1`: the backend venv's 4.59.1 predates the subclass shim, so it cannot reproduce the failure.

## Review Focus

- A raw original-class parser built through `etree.XMLParser.__bases__[0]` is untracked, so the wrappers must still replace it. Task 1 test.
- A tracked parser must be reused as-is, not swapped for the default; a lost `remove_blank_text` shows the swap. Task 1 test.
- `WeakSet.__contains__` on unhashable or non-weakrefable args returns False instead of raising. Covered by the existing unhashable test.
- `lxml.html.fromstring` passes an `HTMLParser` (untracked) through the wrapped `etree.fromstring`, which swaps it for an XML parser. This is pre-existing wrapper behaviour, AC3 asks only for the import, and it is not addressed here.

---

### Task 1: Hardened `XMLParser` subclass with WeakSet tracking

**Files:** Modify `filesystem_policy.py:798-845`. Test: `test_filesystem_policy.py` (append after line ~638).

**Test-first:** yes. The new tests fail today with `TypeError: function() argument 'code' must be code, not str` or `isinstance() arg 2 must be a type`, and with a fontTools import failure for the PDF test.

- [ ] **Step 1: Write the failing tests.** Each asserts `returncode == 0`, plus:
  - `test_guard_lxml_xmlparser_supports_subclass_and_isinstance` (`_run_guarded`): `class X(etree.XMLParser): pass`. Print `isinstance(etree.XMLParser(), etree.XMLParser)`, `isinstance(X(), etree.XMLParser)`, and `etree.fromstring(b'<r>ok</r>', X()).text`. Assert `"True True ok"`.
  - `test_guard_lxml_html_imports` (`_run_guarded`): `import lxml.html` and print `issubclass(lxml.html.XHTMLParser, etree.XMLParser)`. Assert `True`.
  - `test_guard_lxml_fonttools_style_subclass_is_tracked_and_blocks_xxe` (`_run_guarded`): define the shim inline (`_XMLParser = etree.XMLParser`, then `class XMLParser(_XMLParser)` whose `__init__` does `kwargs.setdefault("resolve_entities", False)` and calls `super().__init__`). Build `p = XMLParser(remove_blank_text=True)`. Assert `fromstring(<XXE>, p).text` prints `None`. Assert `fromstring(b'<r>  <a/>  </r>', p).text` prints `None`, which proves `p` was trusted, not replaced. Also `import fontTools.misc.etree` succeeds.
  - `test_guard_lxml_unsafe_user_subclass_and_raw_base_blocked` (`_run_guarded`): a subclass whose `__init__` passes `resolve_entities=True, load_dtd=True, no_network=False, huge_tree=True` to `super()`. `etree.XML(<XXE>, Unsafe())` gives `None`. `etree.parse(BytesIO(<XXE>), etree.XMLParser.__bases__[0](resolve_entities=True, load_dtd=True, no_network=False))` gives root text `None`.
  - `test_guard_matplotlib_writes_single_and_multipage_pdf`: create `workspace_root = tmp_path / "workspace"` and call `_run_guarded_as_file_in_workspace(workspace_root, code)`. Under `matplotlib.use("Agg")`, do `fig.savefig("one.pdf", format="pdf")`, then write 3 pages through `PdfPages("multi.pdf")`. Assert both files exist under `workspace_root` with `st_size > 0`. Do not assert on stderr (the `fc-list` marker is expected).
- [ ] **Step 2:** `GUARD_PYTEST tests/codemie_tools/data_management/code_executor/test_filesystem_policy.py -k "xmlparser or lxml_html or fonttools or unsafe_user_subclass or matplotlib"`. Expected: FAIL (TypeError / import failure).
- [ ] **Step 3: Implement.** Inside `if _lxml_etree is not None:`, keep line 798. Add `import weakref as _weakref` and `_SAFE_XML_KWARGS`, replace `_hardened_parsers: set = set()` with `_hardened_parsers = _weakref.WeakSet()`, and delete the stale no-weakref comment (800-810). Replace `_hardened_xmlparser_cls` (813-822) with the class below. `_hardened_default_parser()` returns `_HardenedXMLParser()`. In the `_force_hardened_parser` comment (828-836), rename the reference to the new class. Leave the logic unchanged.

```python
_SAFE_XML_KWARGS = {"resolve_entities": False, "load_dtd": False, "no_network": True, "huge_tree": False}

class _HardenedXMLParser(_original_xmlparser_cls):
    def __init__(self, *args, **kwargs):
        kwargs.update(_SAFE_XML_KWARGS)
        super().__init__(*args, **kwargs)
        _hardened_parsers.add(self)

_lxml_etree.XMLParser = _HardenedXMLParser
```

- [ ] **Step 4:** Run `GUARD_PYTEST tests/codemie_tools/data_management/code_executor/test_filesystem_policy.py tests/codemie_tools/data_management/code_executor/test_sandbox_guard.py`. Expected: all pass, including the unchanged `..._blocks_type_bypass` and billion-laughs tests.

### Task 2: Hardened `iterparse` subclass

**Files:** Modify `filesystem_policy.py:867-878`. Test: `test_filesystem_policy.py`, after the existing iterparse test (~line 637).

**Interfaces:** Consumes `_SAFE_XML_KWARGS` from Task 1; it is defined earlier in the same `if _lxml_etree is not None:` block.

**Test-first:** yes. `class Y(etree.iterparse)` and `isinstance(..., etree.iterparse)` raise TypeError today.

- [ ] **Step 1: Write the failing test** `test_guard_lxml_iterparse_supports_subclass_and_isinstance`. Run `it = etree.iterparse(io.BytesIO(b'<r><a>1</a></r>'))` and print `isinstance(it, etree.iterparse)` and `[el.tag for _, el in it]`. Assert `True ['a', 'r']`. Then `class Y(etree.iterparse): pass`, iterate `Y(io.BytesIO(<XXE>), load_dtd=True, resolve_entities=True)`, and assert texts `[None]`.
- [ ] **Step 2:** `GUARD_PYTEST ...test_filesystem_policy.py -k iterparse`. Expected: the new test FAILs and the existing one passes.
- [ ] **Step 3: Implement.** Replace `_hardened_iterparse` with `class _HardenedIterparse(_original_iterparse)`. Its `__init__(self, *args, **kwargs)` does `kwargs.update(_SAFE_XML_KWARGS)` and then `super().__init__(*args, **kwargs)`. Assign it to `_lxml_etree.iterparse`. Keep the `_ORIGINALS["lxml.etree.iterparse"]` line.
- [ ] **Step 4:** Run `GUARD_PYTEST` on both test files, as in Task 1 Step 4. Expected: all pass.
