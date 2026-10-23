# Local verification — EPMCDME-16087

Run against the local stack: uvicorn backend from `repos/codemie/src` (`--reload`), executor Jobs in the local k3s cluster (`CODE_EXECUTOR_SANDBOX_MODE` default `sandbox-jobs`), image `codemie-code-executor:2.58.0`.

Workflows (local, user admin@codemie.ai):
- Reporter repro: `92b5ad34-ddb1-40e1-8b36-b9434905089a` (user's original failing execution `2f0ce360-8e81-45e8-babd-e4bc94e3f325`).
- Verification: `9ba60777-746d-4ec3-b19c-669abccdee9f` "EPMCDME-16087 lxml guard verification" — step `lxml_checks` (script below) then `pdf_export` (matplotlib one-page + 3-page PdfPages, `export_files: [one_page.pdf, multi_page.pdf]`).

## Before the fix (branch EPMCDME-16087 at origin/main, dfffcbe6f)

### Reporter repro — execution `e8fa75ff-4051-45e2-9816-a6f43530fb3c`
```
execution e8fa75ff-4051-45e2-9816-a6f43530fb3c [before] overall_status=Failed
--- tool_1 [Succeeded]
Start
--- xml_parser [Failed]
Code execution failed.

STDERR:
Traceback (most recent call last):
  File "/home/codemie/639b4cef-a93a-4f26-a41d-9dab11813b3d/script.py", line 934, in <module>
    exec(compile('from lxml import etree\n\nclass DemoParser(etree.XMLParser):\n    pass\n\nprint("XMLParser subclass OK")', '<customer_code>', 'exec'), {'__name__': '__main__'})
  File "<customer_code>", line 3, in <module>
TypeError: function() argument 'code' must be code, not str
--- font_Tools [Failed]
Code execution failed.

STDERR:
Traceback (most recent call last):
  File "/home/codemie/639b4cef-a93a-4f26-a41d-9dab11813b3d/script.py", line 934, in <module>
    exec(compile('from fontTools.misc import etree\nprint("FontTools import OK")', '<customer_code>', 'exec'), {'__name__': '__main__'})
  File "<customer_code>", line 1, in <module>
  File "/opt/venv/lib/python3.12/site-packages/fontTools/misc/etree.py", line 55, in <module>
TypeError: function() argument 'code' must be code, not str
--- iterparse [Failed]
Code execution failed.

STDERR:
Traceback (most recent call last):
  File "/home/codemie/639b4cef-a93a-4f26-a41d-9dab11813b3d/script.py", line 934, in <module>
    exec(compile('from lxml import etree\n\nclass DemoIterparse(etree.iterparse):\n    pass\n\nprint("iterparse subclass OK")', '<customer_code>', 'exec'), {'__name__': '__main__'})
  File "<customer_code>", line 3, in <module>
TypeError: function() argument 'code' must be code, not str
--- AI/Run Thoughts [Failed]
Code execution failed.

STDERR:
Traceback (most recent call last):
  File "/home/codemie/639b4cef-a93a-4f26-a41d-9dab11813b3d/script.py", line 934, in <module>
    exec(compile('from fontTools.misc import etree\nprint("FontTools import OK")', '<customer_code>', 'exec'), {'__name__': '__main__'})
  File "<customer_code>", line 1, in <module>
  File "/opt/venv/lib/python3.12/site-packages/fontTools/misc/etree.py", line 55, in <module>
TypeError: function() argument 'code' must be code, not str```

### Verification — execution `93bc671b-64ed-454c-a608-ea0ca16a4f29`
```
execution 93bc671b-64ed-454c-a608-ea0ca16a4f29 [verify_before] overall_status=Failed
--- lxml_checks [Succeeded]
LParser subclass -> TypeError: function() argument 'code' must be code, not str
FAIL iterparse subclass -> TypeError: function() argument 'code' must be code, not str
FAIL XMLParser isinstance -> TypeError: isinstance() arg 2 must be a type, a tuple of types, or a union
FAIL iterparse isinstance -> TypeError: isinstance() arg 2 must be a type, a tuple of types, or a union
PASS plain XMLParser parse -> 1
PASS plain iterparse -> ['b', 'a']
FAIL import lxml.html -> TypeError: function() argument 'code' must be code, not str
FAIL import fontTools.misc.etree -> TypeError: function() argument 'code' must be code, not str
FAIL import matplotlib backend_pdf -> TypeError: function() argument 'code' must be code, not str
PASS XXE default fromstring -> no leak
PASS XXE unsafe-kwargs parser -> no leak
FAIL XXE FontTools-style subclass -> TypeError: function() argument 'code' must be code, not str
FAIL XXE user subclass w/ unsafe kwargs -> TypeError: function() argument 'code' must be code, not str
PASS XXE iterparse -> no leak
PASS billion laughs -> rejected (Maximum entity amplification factor exceeded, see xmlCtxtSetMaxAmplification., line 1, column 4 (<string>, line 1))
SUMMARY: 6/15 passed
--- pdf_export [Failed]
e.pdf")\nplt.close(fig)\n\nwith PdfPages("multi_page.pdf") as pdf:\n    for page in range(3):\n        fig, ax = plt.subplots()\n        ax.bar(["a", "b", "c"], [page + 1, page + 2, page + 3])\n        ax.set_title(f"EPMCDME-16087 page {page + 1}")\n        pdf.savefig(fig)\n        plt.close(fig)\n\nfor name in ("one_page.pdf", "multi_page.pdf"):\n    print(name, os.path.getsize(name), "bytes", open(name, "rb").read(5))\n', '<customer_code>', 'exec'), {'__name__': '__main__'})
  File "<customer_code>", line 7, in <module>
  File "/opt/venv/lib/python3.12/site-packages/matplotlib/backends/backend_pdf.py", line 44, in <module>
  File "/opt/venv/lib/python3.12/site-packages/matplotlib/backends/_backend_pdf_ps.py", line 8, in <module>
  File "/opt/venv/lib/python3.12/site-packages/fontTools/subset/__init__.py", line 19, in <module>
  File "/opt/venv/lib/python3.12/site-packages/fontTools/varLib/__init__.py", line 47, in <module>
  File "/opt/venv/lib/python3.12/site-packages/fontTools/designspaceLib/__init__.py", line 19, in <module>
  File "/opt/venv/lib/python3.12/site-packages/fontTools/misc/etree.py", line 55, in <module>
TypeError: function() argument 'code' must be code, not str
--- AI/Run Thoughts [Failed]
e.pdf")\nplt.close(fig)\n\nwith PdfPages("multi_page.pdf") as pdf:\n    for page in range(3):\n        fig, ax = plt.subplots()\n        ax.bar(["a", "b", "c"], [page + 1, page + 2, page + 3])\n        ax.set_title(f"EPMCDME-16087 page {page + 1}")\n        pdf.savefig(fig)\n        plt.close(fig)\n\nfor name in ("one_page.pdf", "multi_page.pdf"):\n    print(name, os.path.getsize(name), "bytes", open(name, "rb").read(5))\n', '<customer_code>', 'exec'), {'__name__': '__main__'})
  File "<customer_code>", line 7, in <module>
  File "/opt/venv/lib/python3.12/site-packages/matplotlib/backends/backend_pdf.py", line 44, in <module>
  File "/opt/venv/lib/python3.12/site-packages/matplotlib/backends/_backend_pdf_ps.py", line 8, in <module>
  File "/opt/venv/lib/python3.12/site-packages/fontTools/subset/__init__.py", line 19, in <module>
  File "/opt/venv/lib/python3.12/site-packages/fontTools/varLib/__init__.py", line 47, in <module>
  File "/opt/venv/lib/python3.12/site-packages/fontTools/designspaceLib/__init__.py", line 19, in <module>
  File "/opt/venv/lib/python3.12/site-packages/fontTools/misc/etree.py", line 55, in <module>
TypeError: function() argument 'code' must be code, not str```

### Shared path (temporary backend on :8081, `CODE_EXECUTOR_SANDBOX_MODE=sandbox-shared`)

Blocked by two pre-existing local-environment defects, unrelated to the lxml guard:

1. Windows host only: `llm_sandbox.py:157` builds the pod-side directory with `str(Path(dest).parent)`, which yields a backslash path on Windows, so the script is extracted to the wrong place (`can't open file .../SANDBOX_*.py`). Worked around in the temporary launcher only (pod paths mapped to `PurePosixPath`); the repo is untouched.
2. Shared pod: `/tmp` is read-only (the pod mounts its `tmp` emptyDir at `/tmp/runtime`), but `llm_sandbox`'s plot capture (always on for `ArtifactSandboxSession`) runs `os.makedirs('/tmp/sandbox_plots')`, so every shared execution fails, including a plain `print("Start")`.

Decision (user): shared-path verification **not run**. Rationale: the guard script is built once by the backend and shipped identically to Jobs and shared pods; each shared execution is a fresh Python process; the shared pod uses the same `codemie-code-executor:2.58.0` image (lxml 6.1.0, `XMLParser`/`iterparse` are real types). In the local shared pod, user code runs in `.sandbox-venv` (created from `/usr/bin/python3.12` with `--system-site-packages`), which cannot see `/opt/venv` packages — `import lxml` raises `ModuleNotFoundError`, so the guard's lxml block is skipped there. With `CODE_EXECUTOR_SKIP_ENVIRONMENT_SETUP=true` the shared pod uses `/opt/venv`, i.e. the same interpreter and packages as Jobs.

Verification workflow — execution `7f6e6baa-45a1-40d0-82cc-eec9f4b0883d`:
```
execution 7f6e6baa-45a1-40d0-82cc-eec9f4b0883d [shared_before] overall_status=Failed
--- lxml_checks [Failed]
Code execution failed.

STDERR:
Traceback (most recent call last):
  File "/home/codemie/639b4cef-a93a-4f26-a41d-9dab11813b3d/SANDBOX_15a6ef4a548c4fb1bd537280a477d582.py", line 9, in <module>
    os.makedirs('/tmp/sandbox_plots', exist_ok=True)
  File "<frozen os>", line 225, in makedirs
OSError: [Errno 30] Read-only file system: '/tmp/sandbox_plots'
--- AI/Run Thoughts [Failed]
Code execution failed.

STDERR:
Traceback (most recent call last):
  File "/home/codemie/639b4cef-a93a-4f26-a41d-9dab11813b3d/SANDBOX_15a6ef4a548c4fb1bd537280a477d582.py", line 9, in <module>
    os.makedirs('/tmp/sandbox_plots', exist_ok=True)
  File "<frozen os>", line 225, in makedirs
OSError: [Errno 30] Read-only file system: '/tmp/sandbox_plots'
```

Reporter workflow — execution `894e4f5b-58a9-4d82-90e0-c7dde76e4bc3`:
```
execution 894e4f5b-58a9-4d82-90e0-c7dde76e4bc3 [shared_reporter_before] overall_status=Failed
--- tool_1 [Failed]
Code execution failed.

STDERR:
Traceback (most recent call last):
  File "/home/codemie/639b4cef-a93a-4f26-a41d-9dab11813b3d/SANDBOX_5ba96895746b49caa8e99551629a1b7d.py", line 9, in <module>
    os.makedirs('/tmp/sandbox_plots', exist_ok=True)
  File "<frozen os>", line 225, in makedirs
OSError: [Errno 30] Read-only file system: '/tmp/sandbox_plots'
--- AI/Run Thoughts [Failed]
Code execution failed.

STDERR:
Traceback (most recent call last):
  File "/home/codemie/639b4cef-a93a-4f26-a41d-9dab11813b3d/SANDBOX_5ba96895746b49caa8e99551629a1b7d.py", line 9, in <module>
    os.makedirs('/tmp/sandbox_plots', exist_ok=True)
  File "<frozen os>", line 225, in makedirs
OSError: [Errno 30] Read-only file system: '/tmp/sandbox_plots'
```

## After the fix — early run (Jobs path, 180d0d2a6, before the CR-001 review fix)

The local backend (`uvicorn --reload`) picked up the branch code; executions ran as fresh k3s Jobs on `codemie-code-executor:2.58.0`.
The verification workflow gained one check after the baseline run: "XXE subclass skipping hardened `__init__`" (calls the original lxml `__init__` with unsafe options; must be replaced by the `fromstring` wrapper).

### Reporter repro — execution `17a84443-fcf2-4ca9-854e-6782c0ec6a8f`
```
execution 17a84443-fcf2-4ca9-854e-6782c0ec6a8f [after] overall_status=Succeeded
--- tool_1 [Succeeded]
Start
--- font_Tools [Succeeded]
FontTools import OK
--- xml_parser [Succeeded]
XMLParser subclass OK
--- iterparse [Succeeded]
iterparse subclass OK
--- iterparse_typecheck [Succeeded]
True
```

### Verification — execution `6cf38943-36e7-40b4-8e1d-535f01374129`
```
execution 6cf38943-36e7-40b4-8e1d-535f01374129 [verify_after] overall_status=Succeeded
--- lxml_checks [Succeeded]
PASS XMLParser subclass -> DemoParser
PASS iterparse subclass -> ['root']
PASS XMLParser isinstance
PASS iterparse isinstance
PASS plain XMLParser parse -> 1
PASS plain iterparse -> ['b', 'a']
PASS import lxml.html -> hi
PASS import fontTools.misc.etree -> 4.66.1
PASS import matplotlib backend_pdf -> 3.9.1
PASS XXE default fromstring -> no leak
PASS XXE unsafe-kwargs parser -> no leak
PASS XXE FontTools-style subclass -> no leak
PASS XXE user subclass w/ unsafe kwargs -> no leak
PASS XXE subclass skipping hardened __init__ -> no leak
PASS XXE iterparse -> no leak
PASS billion laughs -> rejected (Maximum entity amplification factor exceeded, see xmlCtxtSetMaxAmplification., line 1, column 4 (<string>, line 1))
SUMMARY: 16/16 passed
--- pdf_export [Succeeded]
one_page.pdf 10357 bytes b'%PDF-'
multi_page.pdf 12447 bytes b'%PDF-' File 'one_page.pdf', URL `sandbox:/v1/files/MTV+YXBwbGljYXRpb24vcGRmMzZ+NjM5YjRjZWYtYTkzYS00ZjI2LWE0MWQtOWRhYjExODEzYjNkNDl+NDRkYTAyZDItOWYyMS00MzZiLTgwZjMtYzFlN2VjY2JiYWUzX29uZV9wYWdlLnBkZg==`,  File 'multi_page.pdf', URL `sandbox:/v1/files/MTV+YXBwbGljYXRpb24vcGRmMzZ+NjM5YjRjZWYtYTkzYS00ZjI2LWE0MWQtOWRhYjExODEzYjNkNTF+MGM0MjdkMjEtMjlkNS00ZDdkLWJlYWYtYWU2YzRlMDc2MmYwX211bHRpX3BhZ2UucGRm`
```

## After the fix — final (Jobs path, branch EPMCDME-16087 at c73a8072c)

Final code after the code-review fix (identity-based parser tracking). Fresh k3s Jobs on `codemie-code-executor:2.58.0`.

### Reporter repro — execution `ec630744-a6a7-4a4d-83c0-7d03849c0afb`
```
execution ec630744-a6a7-4a4d-83c0-7d03849c0afb [final] overall_status=Succeeded
--- tool_1 [Succeeded]
Start
--- font_Tools [Succeeded]
FontTools import OK
--- xml_parser [Succeeded]
XMLParser subclass OK
--- iterparse [Succeeded]
iterparse subclass OK
--- iterparse_typecheck [Succeeded]
True
```

### Verification — execution `f6b7a441-0f1e-4cd0-9611-5588ff7e5e1a`
```
execution f6b7a441-0f1e-4cd0-9611-5588ff7e5e1a [verify_final] overall_status=Succeeded
--- lxml_checks [Succeeded]
PASS XMLParser subclass -> DemoParser
PASS iterparse subclass -> ['root']
PASS XMLParser isinstance
PASS iterparse isinstance
PASS plain XMLParser parse -> 1
PASS plain iterparse -> ['b', 'a']
PASS import lxml.html -> hi
PASS import fontTools.misc.etree -> 4.66.1
PASS import matplotlib backend_pdf -> 3.9.1
PASS XXE default fromstring -> no leak
PASS XXE unsafe-kwargs parser -> no leak
PASS XXE FontTools-style subclass -> no leak
PASS XXE user subclass w/ unsafe kwargs -> no leak
PASS XXE subclass skipping hardened __init__ -> no leak
PASS XXE iterparse -> no leak
PASS billion laughs -> rejected (Maximum entity amplification factor exceeded, see xmlCtxtSetMaxAmplification., line 1, column 4 (<string>, line 1))
SUMMARY: 16/16 passed
--- pdf_export [Succeeded]
one_page.pdf 10357 bytes b'%PDF-'
multi_page.pdf 12447 bytes b'%PDF-' File 'one_page.pdf', URL `sandbox:/v1/files/MTV+YXBwbGljYXRpb24vcGRmMzZ+NjM5YjRjZWYtYTkzYS00ZjI2LWE0MWQtOWRhYjExODEzYjNkNDl+Zjg0NmViYmMtNTUwZC00NDliLTkxZGMtNzUxODQ4ZDU0NDUxX29uZV9wYWdlLnBkZg==`,  File 'multi_page.pdf', URL `sandbox:/v1/files/MTV+YXBwbGljYXRpb24vcGRmMzZ+NjM5YjRjZWYtYTkzYS00ZjI2LWE0MWQtOWRhYjExODEzYjNkNTF+ZDUyNDNmYmUtMmY3My00ZTIzLThiZTQtMzhiYTU2MzIxMjI1X211bHRpX3BhZ2UucGRm`
```

## Verification scripts

### lxml_checks
```python
import io

results = []


def check(name, fn):
    try:
        detail = fn()
        results.append(f"PASS {name}" + (f" -> {detail}" if detail is not None else ""))
    except BaseException as exc:  # noqa: BLE001
        results.append(f"FAIL {name} -> {type(exc).__name__}: {exc}")


from lxml import etree

SECRET = "root:"
XXE = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
LAUGHS = (
    b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa">'
    + b"".join(f'<!ENTITY {chr(98 + i)} "{("&" + chr(97 + i) + ";") * 10}">'.encode() for i in range(8))
    + b"]><l>&i;</l>"
)


def no_leak(fn):
    try:
        out = fn()
    except etree.XMLSyntaxError as exc:
        return f"rejected ({exc})"
    text = out if isinstance(out, str) else etree.tostring(out).decode()
    assert SECRET not in text, "secret file content leaked"
    return "no leak"


def xmlparser_subclass():
    class DemoParser(etree.XMLParser):
        pass

    return type(DemoParser()).__name__


def iterparse_subclass():
    class DemoIterparse(etree.iterparse):
        pass

    return [el.tag for _, el in DemoIterparse(io.BytesIO(b"<root/>"))]


def xmlparser_isinstance():
    assert isinstance(etree.XMLParser(), etree.XMLParser)


def iterparse_isinstance():
    assert isinstance(etree.iterparse(io.BytesIO(b"<root/>")), etree.iterparse)


def plain_parse():
    return etree.fromstring(b"<a><b>1</b></a>", etree.XMLParser()).findtext("b")


def plain_iterparse():
    return [el.tag for _, el in etree.iterparse(io.BytesIO(b"<a><b/></a>"))]


def import_lxml_html():
    import lxml.html

    return lxml.html.fromstring("<p>hi</p>").text


def import_fonttools():
    import fontTools
    from fontTools.misc import etree as ft_etree  # noqa: F401

    return fontTools.version


def import_backend_pdf():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.backends.backend_pdf  # noqa: F401

    return matplotlib.__version__


def fonttools_style_subclass_xxe():
    class FtParser(etree.XMLParser):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("resolve_entities", False)
            super().__init__(*args, **kwargs)

    return no_leak(lambda: etree.fromstring(XXE, FtParser()))


def user_subclass_unsafe_kwargs_xxe():
    class Evil(etree.XMLParser):
        def __init__(self):
            super().__init__(resolve_entities=True, load_dtd=True, no_network=False)

    return no_leak(lambda: etree.fromstring(XXE, Evil()))


def subclass_skipping_hardened_init_xxe():
    original = etree.XMLParser.__bases__[0]

    class Sneaky(etree.XMLParser):
        def __init__(self):
            original.__init__(self, resolve_entities=True, load_dtd=True, no_network=False)

    return no_leak(lambda: etree.fromstring(XXE, Sneaky()))


check("XMLParser subclass", xmlparser_subclass)
check("iterparse subclass", iterparse_subclass)
check("XMLParser isinstance", xmlparser_isinstance)
check("iterparse isinstance", iterparse_isinstance)
check("plain XMLParser parse", plain_parse)
check("plain iterparse", plain_iterparse)
check("import lxml.html", import_lxml_html)
check("import fontTools.misc.etree", import_fonttools)
check("import matplotlib backend_pdf", import_backend_pdf)
check("XXE default fromstring", lambda: no_leak(lambda: etree.fromstring(XXE)))
check("XXE unsafe-kwargs parser", lambda: no_leak(lambda: etree.fromstring(XXE, etree.XMLParser(resolve_entities=True))))
check("XXE FontTools-style subclass", fonttools_style_subclass_xxe)
check("XXE user subclass w/ unsafe kwargs", user_subclass_unsafe_kwargs_xxe)
check("XXE subclass skipping hardened __init__", subclass_skipping_hardened_init_xxe)
check("XXE iterparse", lambda: no_leak(lambda: next(iter(etree.iterparse(io.BytesIO(XXE), resolve_entities=True)))[1]))
check("billion laughs", lambda: no_leak(lambda: etree.fromstring(LAUGHS)))

print("\n".join(results))
print(f"SUMMARY: {sum(r.startswith('PASS') for r in results)}/{len(results)} passed")
```

### pdf_export
```python
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

fig, ax = plt.subplots()
ax.plot([1, 2, 3], [3, 1, 2])
ax.set_title("EPMCDME-16087 one-page")
fig.savefig("one_page.pdf")
plt.close(fig)

with PdfPages("multi_page.pdf") as pdf:
    for page in range(3):
        fig, ax = plt.subplots()
        ax.bar(["a", "b", "c"], [page + 1, page + 2, page + 3])
        ax.set_title(f"EPMCDME-16087 page {page + 1}")
        pdf.savefig(fig)
        plt.close(fig)

for name in ("one_page.pdf", "multi_page.pdf"):
    print(name, os.path.getsize(name), "bytes", open(name, "rb").read(5))
```
