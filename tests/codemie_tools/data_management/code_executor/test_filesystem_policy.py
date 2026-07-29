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

import os
import subprocess
import sys
from pathlib import Path

import pytest

from codemie_tools.data_management.code_executor.filesystem_policy import (
    DENIAL_MARKER,
    extract_denial_events,
)
from codemie_tools.data_management.code_executor.sandbox_guard import build_guarded_python_script

_COVERAGE_ENV_VARS = frozenset(
    {
        "COVERAGE_PROCESS_START",
        "COVERAGE_FILE",
        "COVERAGE_RUN",
        "COV_CORE_SOURCE",
        "COV_CORE_CONFIG",
        "COV_CORE_DATAFILE",
    }
)


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _COVERAGE_ENV_VARS}


def _run_guarded_in_workspace(
    workspace_root: Path,
    customer_code: str,
    *,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    script = build_guarded_python_script(customer_code, workspace_root=str(workspace_root))
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=cwd or workspace_root,
        env=_clean_env(),
        text=True,
        capture_output=True,
        check=False,
    )


def _run_guarded(tmp_path: Path, customer_code: str) -> subprocess.CompletedProcess[str]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    return _run_guarded_in_workspace(workspace_root, customer_code)


def _run_guarded_as_file_in_workspace(
    workspace_root: Path,
    customer_code: str,
) -> subprocess.CompletedProcess[str]:
    """Mirror production invocation: the guarded script is written to a file
    inside the workspace root and executed as `python <file>`, matching
    batch_job_runner.py writing script.py into workdir and running it there.
    """
    script = build_guarded_python_script(customer_code, workspace_root=str(workspace_root))
    script_path = workspace_root / "script.py"
    script_path.write_text(script)
    return subprocess.run(
        [sys.executable, str(script_path)],
        cwd=workspace_root,
        env=_clean_env(),
        text=True,
        capture_output=True,
        check=False,
    )


def test_guard_allows_relative_write_inside_workspace(tmp_path: Path) -> None:
    result = _run_guarded(
        tmp_path,
        "from pathlib import Path\nPath('ok.txt').write_text('done')\nprint(Path('ok.txt').read_text())",
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "done"


def test_guard_denies_absolute_paths(tmp_path: Path) -> None:
    result = _run_guarded(tmp_path, "open('/etc/passwd').read()")

    assert result.returncode == 1
    assert "Filesystem access denied" in result.stderr
    assert DENIAL_MARKER in result.stderr


def test_guard_denies_absolute_path_inside_workspace(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    result = _run_guarded_in_workspace(
        workspace_root,
        f"open({str(workspace_root / 'inside.txt')!r}, 'w').write('denied')",
    )

    assert result.returncode == 1
    assert not (workspace_root / "inside.txt").exists()
    assert DENIAL_MARKER in result.stderr


def test_guard_denies_traversal_escape(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "secret.txt").write_text("secret")

    result = _run_guarded(tmp_path, "open('../other/secret.txt').read()")

    assert result.returncode == 1
    assert "Filesystem access denied" in result.stderr


def test_guard_denies_sibling_workspace_prefix_confusion(tmp_path: Path) -> None:
    workspace_root = tmp_path / "session-1"
    workspace_root.mkdir()
    sibling = tmp_path / "session-1-evil"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("secret")

    result = _run_guarded_in_workspace(
        workspace_root,
        "open('../session-1-evil/secret.txt').read()",
    )

    assert result.returncode == 1
    assert "secret" not in result.stdout


def test_guard_denies_boundary_crossing_rename(tmp_path: Path) -> None:
    result = _run_guarded(
        tmp_path,
        "from pathlib import Path\nPath('a.txt').write_text('x')\nPath('a.txt').rename('../escape.txt')",
    )

    assert result.returncode == 1
    assert DENIAL_MARKER in result.stderr


def test_guard_denies_boundary_crossing_copy(tmp_path: Path) -> None:
    result = _run_guarded(
        tmp_path,
        "from pathlib import Path\n"
        "import shutil\n"
        "Path('a.txt').write_text('x')\n"
        "shutil.copyfile('a.txt', '../escape.txt')",
    )

    assert result.returncode == 1
    assert DENIAL_MARKER in result.stderr


def test_guard_denies_symlink_escape(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    (workspace_root / "escape").symlink_to(outside, target_is_directory=True)

    result = _run_guarded_in_workspace(workspace_root, "open('escape/secret.txt').read()")

    assert result.returncode == 1
    assert "secret" not in result.stdout


def test_guard_denies_proc_fd_magic_link_escape(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "stdin-link").symlink_to("/proc/self/fd/0")

    result = _run_guarded_in_workspace(workspace_root, "import os\nos.readlink('stdin-link')")

    assert result.returncode == 1
    assert DENIAL_MARKER in result.stderr


def test_guard_denies_ctypes_dlopen_escape(tmp_path: Path) -> None:
    # dlopen(None) is now allowed (RTLD_DEFAULT, required by numpy/pandas),
    # but dlsym (symbol lookup) is still denied, so the exploit is blocked.
    result = _run_guarded(
        tmp_path,
        "import ctypes\nctypes.CDLL(None).printf(b'pwned')\n",
    )

    assert result.returncode == 1
    assert DENIAL_MARKER in result.stderr


def test_guard_allows_ctypes_dlopen_none_for_installed_libraries(tmp_path: Path) -> None:
    # ctypes.dlopen(None) is used by numpy/pandas during C extension init to
    # obtain the RTLD_DEFAULT handle — no external file is loaded. It must be
    # allowed so that installed scientific libraries can be imported.
    result = _run_guarded(
        tmp_path,
        "import ctypes\nlib = ctypes.CDLL(None)\nprint('dlopen_none_ok')\n",
    )

    assert result.returncode == 0
    assert "dlopen_none_ok" in result.stdout


def test_guard_lstat_reports_symlink_without_resolving_target(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "in-workspace-link").symlink_to("/etc/passwd")

    result = _run_guarded_in_workspace(
        workspace_root,
        "import os\n" "print(os.path.islink('in-workspace-link'))\n" "print(os.lstat('in-workspace-link').st_size)\n",
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "True"
    assert lines[1] == str(len("/etc/passwd"))


def test_guard_readlink_denies_only_actual_symlinks(tmp_path: Path) -> None:
    result = _run_guarded(
        tmp_path,
        "from pathlib import Path\n"
        "import os\n"
        "Path('regular.txt').write_text('data')\n"
        "try:\n"
        "    os.readlink('regular.txt')\n"
        "    print('escaped')\n"
        "except OSError as exc:\n"
        "    print(type(exc).__name__)\n",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OSError"
    assert DENIAL_MARKER not in result.stderr


@pytest.mark.parametrize(
    ("api_name", "customer_code"),
    [
        ("listdir", "import os\nos.listdir('/etc')"),
        ("scandir", "import os\nlist(os.scandir('/etc'))"),
        ("stat", "import os\nos.stat('/etc/passwd')"),
        ("lstat", "import os\nos.lstat('/etc/passwd')"),
        ("access", "import os\nos.access('/etc/passwd', os.R_OK)"),
        ("readlink", "import os\nos.readlink('/proc/self/fd/0')"),
    ],
)
def test_guard_denies_required_inspection_apis(
    tmp_path: Path,
    api_name: str,
    customer_code: str,
) -> None:
    result = _run_guarded(tmp_path, customer_code)

    assert result.returncode == 1, api_name
    assert DENIAL_MARKER in result.stderr


def test_guard_denies_posix_chdir_escape_before_relative_open(tmp_path: Path) -> None:
    result = _run_guarded(
        tmp_path,
        "import posix\nposix.chdir('/etc')\nprint(open('hosts').readline())",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert DENIAL_MARKER in result.stderr


def test_guard_denies_fchdir(tmp_path: Path) -> None:
    result = _run_guarded(tmp_path, "import os\nos.fchdir(0)")

    assert result.returncode == 1
    assert DENIAL_MARKER in result.stderr


def test_guard_resolves_relative_file_operations_against_fixed_workspace(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    result = _run_guarded_in_workspace(
        workspace_root,
        "import os\n" "os.mkdir('nested')\n" "os.chdir('nested')\n" "open('root.txt', 'w').write('root')",
    )

    assert result.returncode == 0, result.stderr
    assert (workspace_root / "root.txt").read_text() == "root"
    assert not (workspace_root / "nested" / "root.txt").exists()


def test_guard_supports_bytes_and_custom_pathlike_inputs(tmp_path: Path) -> None:
    result = _run_guarded(
        tmp_path,
        "import os\n"
        "class BytesPath:\n"
        "    def __fspath__(self):\n"
        "        return b'bytes.txt'\n"
        "open(BytesPath(), 'wb').write(b'ok')\n"
        "print(open(b'bytes.txt', 'rb').read().decode())",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


@pytest.mark.parametrize(
    "customer_code",
    [
        "open(b'\\xff', 'w')",
        "open('bad\\x00path', 'w')",
    ],
)
def test_guard_denies_malformed_or_unsupported_paths(tmp_path: Path, customer_code: str) -> None:
    result = _run_guarded(tmp_path, customer_code)

    assert result.returncode == 1
    assert DENIAL_MARKER in result.stderr


def test_guard_allows_integer_fd_open(tmp_path: Path) -> None:
    # io.open(fd, ...) wraps an already-open file descriptor — no path to validate.
    # This is required by matplotlib's font loader and other libraries that use FD-based I/O.
    result = _run_guarded(
        tmp_path,
        "import io, os\n"
        "fd = os.open('allowed.txt', os.O_CREAT | os.O_WRONLY, 0o644)\n"
        "f = io.open(fd, 'w')\n"
        "f.write('fd-write')\n"
        "f.close()\n"
        "print(open('allowed.txt').read())\n",
    )

    assert result.returncode == 0
    assert "fd-write" in result.stdout


def test_guard_allows_os_stat_on_fd(tmp_path: Path) -> None:
    # os.stat(fd) is fstat() — safe metadata query on an already-open descriptor.
    result = _run_guarded(
        tmp_path,
        "import os\n"
        "fd = os.open('probe.txt', os.O_CREAT | os.O_WRONLY, 0o644)\n"
        "st = os.stat(fd)\n"
        "os.close(fd)\n"
        "print('ok', st.st_size)\n",
    )

    assert result.returncode == 0
    assert "ok" in result.stdout


def test_guard_allows_nested_directories_and_default_tempfiles(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    result = _run_guarded_in_workspace(
        workspace_root,
        "from pathlib import Path\n"
        "import tempfile\n"
        "Path('a/b').mkdir(parents=True)\n"
        "Path('a/b/value.txt').write_text('nested')\n"
        "with tempfile.NamedTemporaryFile(mode='w', delete=False) as handle:\n"
        "    handle.write('temporary')\n"
        "    print(handle.name)\n",
    )

    assert result.returncode == 0, result.stderr
    temp_path = Path(result.stdout.strip())
    assert (workspace_root / "a/b/value.txt").read_text() == "nested"
    assert temp_path.is_relative_to(workspace_root)
    assert temp_path.read_text() == "temporary"


def test_guard_allows_workspace_module_imports(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "helper.py").write_text("VALUE = 7\n")
    script = build_guarded_python_script("import helper\nprint(helper.VALUE)", workspace_root=str(workspace_root))

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=workspace_root,
        env=_clean_env(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "7"


def test_guard_allows_normal_import_but_denies_direct_open_of_import_file(tmp_path: Path) -> None:
    result = _run_guarded(
        tmp_path,
        "import fractions\n" "print(fractions.Fraction(1, 2))\n" "open(fractions.__file__).read()",
    )

    assert result.returncode == 1
    assert result.stdout.strip() == "1/2"
    assert DENIAL_MARKER in result.stderr


def test_guard_allows_stdlib_imports_when_script_runs_from_file_in_workspace(tmp_path: Path) -> None:
    """Regression test for production invocation: batch_job_runner.py writes the
    guarded script to script.py inside the workspace and runs `python script.py`,
    unlike the -c invocation used by the other tests in this file. Running the
    guard's own top-level script frame from inside the workspace must not cause
    stdlib imports to be misclassified as workspace-module imports.
    """
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    result = _run_guarded_as_file_in_workspace(
        workspace_root,
        "import csv\n" "import fractions\n" "print(fractions.Fraction(1, 2))\n" "open(fractions.__file__).read()",
    )

    assert result.returncode == 1
    assert result.stdout.strip() == "1/2"
    assert DENIAL_MARKER in result.stderr


def test_guard_allows_stdlib_imports_via_file_invocation_with_frozen_importlib(tmp_path: Path) -> None:
    """Regression test: _frozen_importlib_external (not importlib._bootstrap_external) is
    the actual __name__ of frozen importlib frames. _is_import_context must recognise both
    forms so that the first import of any stdlib or site-packages module is not denied when
    the guarded script is run as a file (script.py) inside the workspace.
    """
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    result = _run_guarded_as_file_in_workspace(
        workspace_root,
        "import csv\nimport fractions\nimport json\nprint('ok')",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
    assert DENIAL_MARKER not in result.stderr


def test_guard_allows_installed_library_to_read_its_own_package_data(tmp_path: Path) -> None:
    """Regression test: installed package code that reads its own data files (not via
    importlib but via ordinary open/os.stat at an absolute path inside site-packages)
    must be allowed. The guard should recognise that the first non-guard caller frame
    originates from within _INTERNAL_IMPORT_PREFIXES and permit read-only access.
    """
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    result = _run_guarded_as_file_in_workspace(
        workspace_root,
        "\n".join(
            [
                "import importlib.metadata",
                "dists = list(importlib.metadata.distributions())",
                "assert len(dists) > 0",
                "print('ok')",
            ]
        ),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
    assert DENIAL_MARKER not in result.stderr


def test_guard_denies_customer_open_from_imported_module_body(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "probe.py").write_text(
        "import fractions\n" "open(fractions.__file__).read()\n",
    )

    result = _run_guarded_in_workspace(workspace_root, "import probe")

    assert result.returncode == 1
    assert DENIAL_MARKER in result.stderr


@pytest.mark.parametrize(
    "database",
    [
        "file:/tmp/outside.db?mode=rwc",
        "file:%2Ftmp%2Foutside.db?mode=rwc",
        "file:../outside.db?mode=rwc",
        "file://server/share.db?mode=rwc",
    ],
)
def test_guard_denies_sqlite_file_uri_escapes(tmp_path: Path, database: str) -> None:
    result = _run_guarded(
        tmp_path,
        f"import sqlite3\nsqlite3.connect({database!r}, uri=True).close()",
    )

    assert result.returncode == 1
    assert DENIAL_MARKER in result.stderr


def test_guard_allows_sqlite_memory_and_relative_workspace_database(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    result = _run_guarded_in_workspace(
        workspace_root,
        "import os\n"
        "import sqlite3\n"
        "sqlite3.connect(':memory:').close()\n"
        "os.mkdir('nested')\n"
        "os.chdir('nested')\n"
        "sqlite3.connect('file:data.db?mode=rwc', uri=True).close()\n",
    )

    assert result.returncode == 0, result.stderr
    assert (workspace_root / "data.db").exists()
    assert not (workspace_root / "nested" / "data.db").exists()


def test_caught_denial_still_emits_single_marker(tmp_path: Path) -> None:
    result = _run_guarded(
        tmp_path,
        "try:\n" "    open('/etc/passwd')\n" "except PermissionError:\n" "    print('caught')\n",
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "caught"
    assert result.stderr.count(DENIAL_MARKER) == 1


def test_unhandled_denial_uses_exact_stable_customer_message_once(tmp_path: Path) -> None:
    result = _run_guarded(tmp_path, "open('/etc/passwd')")

    assert result.returncode == 1
    assert result.stderr.count(DENIAL_MARKER) == 1
    assert result.stderr.count("Filesystem access denied: path is outside the execution workspace") == 1


def test_guard_allows_relative_makedirs_for_output_directories(tmp_path: Path) -> None:
    """os.makedirs with a relative path must succeed — used by matplotlib and
    customer code to create output subdirectories within the workspace."""
    result = _run_guarded(
        tmp_path,
        "import os\n" "os.makedirs('output/charts', exist_ok=True)\n" "print('ok')\n",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ok"
    assert DENIAL_MARKER not in result.stderr


def test_guard_sets_mplconfigdir_to_workspace_relative_path(tmp_path: Path) -> None:
    """install_guard sets MPLCONFIGDIR to a relative path so matplotlib can
    create its config dir inside the workspace without hitting an absolute-path
    denial."""
    result = _run_guarded(
        tmp_path,
        "import os\n"
        "mpldir = os.environ.get('MPLCONFIGDIR', '')\n"
        "print('relative' if not os.path.isabs(mpldir) else 'absolute')\n",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "relative"
    assert DENIAL_MARKER not in result.stderr


def test_guard_allows_matplotlib_png_generation(tmp_path: Path) -> None:
    """matplotlib.pyplot.savefig must complete without filesystem denials when
    MPLCONFIGDIR resolves to an absolute path inside the workspace.  Subprocess
    denials for font-discovery tools (fc-list, system_profiler) are expected
    and must not prevent the chart from being saved."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = _run_guarded_as_file_in_workspace(
        workspace,
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.plot([1, 2, 3], [4, 5, 6])\n"
        "plt.savefig('chart.png')\n"
        "print('saved')\n",
    )
    assert result.returncode == 0
    assert "saved" in result.stdout
    fs_denials = [
        d for d in extract_denial_events(result.stderr) if d.get("reason") != "process_creation_not_supported"
    ]
    assert fs_denials == [], f"Unexpected filesystem denials: {fs_denials}"


def test_guard_allows_openpyxl_to_save_workbook(tmp_path: Path) -> None:
    """openpyxl uses NamedTemporaryFile (absolute path in WORKSPACE_TMP) during
    save and removes it in an atexit handler.  It also calls mimetypes which
    probes /etc/mime.types with a read-only open.  Neither should produce denial
    markers or crash the process."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = _run_guarded_as_file_in_workspace(
        workspace,
        "import openpyxl\n"
        "wb = openpyxl.Workbook()\n"
        "ws = wb.active\n"
        "ws['A1'] = 'test'\n"
        "wb.save('out.xlsx')\n"
        "print('saved')\n",
    )
    assert result.returncode == 0
    assert "saved" in result.stdout
    assert DENIAL_MARKER not in result.stderr
