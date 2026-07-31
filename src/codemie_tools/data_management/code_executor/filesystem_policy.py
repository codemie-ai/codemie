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

from __future__ import annotations

import json
import textwrap

DENIAL_MARKER = "__CODEMIE_FS_DENIED__"
CUSTOMER_DENIAL_MESSAGE = "Filesystem access denied: path is outside the execution workspace"


def render_runtime_prelude(workspace_root: str) -> str:
    template = r"""
        import builtins
        import io
        import json
        import os
        import re
        import shutil
        import sqlite3
        import stat
        import sys
        import tempfile
        import threading
        import urllib.parse

        DENIAL_MARKER = __CODEMIE_DENIAL_MARKER__
        CUSTOMER_DENIAL_MESSAGE = __CODEMIE_DENIAL_MESSAGE__
        WORKSPACE_ROOT = os.path.realpath(__CODEMIE_WORKSPACE_ROOT__)
        WORKSPACE_TMP = os.path.join(WORKSPACE_ROOT, ".tmp")
        _GUARD_SCRIPT_FILE = os.path.realpath(__file__) if "__file__" in dir() else None
        _POLICY_STATE = threading.local()
        _PLATFORM_OS = __import__(os.name)
        _PRISTINE_REALPATH = os.path.realpath
        _PRISTINE_LSTAT = os.lstat
        _INTERNAL_IMPORT_PREFIXES = tuple(
            dict.fromkeys(
                os.path.realpath(path)
                for path in (sys.prefix, sys.base_prefix, *sys.path)
                if isinstance(path, str) and path and os.path.isabs(path)
            )
        )
        _PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
        _PROCESS_CREATION_EVENTS = frozenset(
            {
                "os.exec",
                "os.fork",
                "os.forkpty",
                "os.posix_spawn",
                "os.spawn",
                "os.system",
                "pty.spawn",
                "subprocess.Popen",
            }
        )
        _NATIVE_LIBRARY_EVENTS = frozenset(
            {
                "ctypes.dlopen",
                "ctypes.dlsym",
            }
        )


        def _event_text(value, limit):
            text = str(value)
            if len(text) > limit:
                return text[:limit]
            return text


        def _write_denial_marker(exc):
            if getattr(exc, "_marker_emitted", False):
                return
            event = {
                "operation": _event_text(exc.operation, 128),
                "path": _event_text(exc.path, 4096),
                "reason": _event_text(exc.reason, 128),
            }
            sys.stderr.write(DENIAL_MARKER + json.dumps(event, separators=(",", ":")) + "\n")
            exc._marker_emitted = True


        class FilesystemAccessDenied(PermissionError):
            def __init__(self, operation, path, reason):
                self.operation = operation
                self.path = path
                self.reason = reason
                self._marker_emitted = False
                super().__init__(CUSTOMER_DENIAL_MESSAGE)
                _write_denial_marker(self)


        def _emit_denial(exc):
            _write_denial_marker(exc)
            sys.stderr.write(CUSTOMER_DENIAL_MESSAGE + "\n")


        def _normalize_path_input(operation, path_value):
            try:
                raw_value = os.fspath(path_value)
            except TypeError as exc:
                raise FilesystemAccessDenied(
                    operation,
                    repr(path_value),
                    "unsupported_path_type",
                ) from exc

            if isinstance(raw_value, bytes):
                try:
                    text_value = raw_value.decode(sys.getfilesystemencoding(), "strict")
                except UnicodeDecodeError as exc:
                    raise FilesystemAccessDenied(
                        operation,
                        repr(raw_value),
                        "undecodable_bytes",
                    ) from exc
            elif isinstance(raw_value, str):
                text_value = raw_value
            else:
                raise FilesystemAccessDenied(
                    operation,
                    repr(raw_value),
                    "unsupported_path_type",
                )

            if "\x00" in text_value:
                raise FilesystemAccessDenied(operation, text_value, "embedded_nul")
            if text_value == "":
                raise FilesystemAccessDenied(operation, text_value, "empty_path")
            return raw_value, text_value


        def _is_within(root, candidate):
            try:
                return os.path.commonpath([root, candidate]) == root
            except ValueError:
                return False


        def _resolve_path(path_value):
            previous_depth = _authorized_depth()
            _POLICY_STATE.authorized_depth = previous_depth + 1
            try:
                return _PRISTINE_REALPATH(path_value)
            finally:
                _POLICY_STATE.authorized_depth = previous_depth


        def _is_symlink(path_value):
            try:
                mode = _PRISTINE_LSTAT(path_value).st_mode
            except (OSError, ValueError):
                return False
            return stat.S_ISLNK(mode)


        def _restore_path_shape(raw_value, text_value):
            if isinstance(raw_value, bytes):
                return os.fsencode(text_value)
            return text_value


        def _authorize_path(operation, path_value):
            raw_value, text_value = _normalize_path_input(operation, path_value)
            if os.path.isabs(text_value):
                raise FilesystemAccessDenied(operation, text_value, "absolute_path")

            lexical_candidate = os.path.abspath(os.path.join(WORKSPACE_ROOT, text_value))
            if not _is_within(WORKSPACE_ROOT, lexical_candidate):
                raise FilesystemAccessDenied(operation, text_value, "outside_workspace")

            if operation == "os.readlink" and _is_symlink(lexical_candidate):
                raise FilesystemAccessDenied(operation, text_value, "symlink_target_not_exposed")

            if operation == "os.lstat":
                if lexical_candidate != WORKSPACE_ROOT:
                    parent_candidate = _resolve_path(os.path.dirname(lexical_candidate))
                    if not _is_within(WORKSPACE_ROOT, parent_candidate):
                        raise FilesystemAccessDenied(operation, text_value, "outside_workspace")
                return _restore_path_shape(raw_value, lexical_candidate)

            resolved_candidate = _resolve_path(lexical_candidate)
            if not _is_within(WORKSPACE_ROOT, resolved_candidate):
                raise FilesystemAccessDenied(operation, text_value, "outside_workspace")

            return _restore_path_shape(raw_value, resolved_candidate)


        def _authorize_pair(operation, src_value, dst_value):
            return _authorize_path(operation, src_value), _authorize_path(operation, dst_value)


        def _authorize_symlink(operation, src_value, dst_value):
            src_raw, src_text = _normalize_path_input(operation, src_value)
            dst_authorized = _authorize_path(operation, dst_value)
            if os.path.isabs(src_text):
                raise FilesystemAccessDenied(operation, src_text, "absolute_path")

            destination_directory = os.path.dirname(os.fsdecode(dst_authorized))
            resolved_target = _resolve_path(os.path.join(destination_directory, src_text))
            if not _is_within(WORKSPACE_ROOT, resolved_target):
                raise FilesystemAccessDenied(operation, src_text, "outside_workspace")
            return _restore_path_shape(src_raw, src_text), dst_authorized


        def _is_import_context():
            frame = sys._getframe(1)
            saw_workspace_frame = False
            while frame is not None:
                module_name = frame.f_globals.get("__name__")
                if (
                    module_name in (
                        "importlib._bootstrap",
                        "importlib._bootstrap_external",
                        "_frozen_importlib",
                        "_frozen_importlib_external",
                    )
                    and frame.f_code.co_filename.startswith("<frozen importlib")
                ):
                    return not saw_workspace_frame
                if (
                    frame.f_code.co_filename not in ("<string>", "<customer_code>")
                    and module_name not in ("__main__", "runpy")
                    and _is_within(WORKSPACE_ROOT, _resolve_path(frame.f_code.co_filename))
                ):
                    saw_workspace_frame = True
                frame = frame.f_back
            return False


        def _is_runpy_context():
            frame = sys._getframe(1)
            while frame is not None:
                if frame.f_globals.get("__name__") == "runpy":
                    return True
                frame = frame.f_back
            return False


        def _is_read_only_open(operation, args, kwargs):
            if operation in ("open", "io.open"):
                mode = args[0] if args else kwargs.get("mode", "r")
                if not isinstance(mode, str):
                    return False
                return all(flag not in mode for flag in ("w", "a", "x", "+"))
            if operation == "os.open":
                flags = args[0] if args else kwargs.get("flags", 0)
                if not isinstance(flags, int):
                    return False
                write_flags = 0
                for name in ("O_WRONLY", "O_RDWR", "O_APPEND", "O_CREAT", "O_TRUNC", "O_EXCL"):
                    write_flags |= getattr(os, name, 0)
                return (flags & write_flags) == 0
            return operation in (
                "os.access",
                "os.listdir",
                "os.lstat",
                "os.readlink",
                "os.scandir",
                "os.stat",
            )


        def _is_installed_library_caller():
            # Returns True when the first non-guard, non-frozen-importlib frame on
            # the call stack originated from within an installed package or the
            # standard library (i.e. its co_filename resolves to a path under one
            # of _INTERNAL_IMPORT_PREFIXES). This covers two cases that are NOT
            # detectable via _is_import_context():
            #   1. Installed package code reading its own data files at runtime
            #      (e.g. pptx opening templates/default.pptx from pptx/api.py).
            #   2. Python's linecache/traceback machinery opening .py source files
            #      inside site-packages to render exception tracebacks.
            #
            # Guard helper functions live in the guard script file
            # (_GUARD_SCRIPT_FILE) which is outside WORKSPACE_ROOT; skip those
            # frames exactly like workspace frames so the walk reaches the first
            # truly external (library) caller.
            frame = sys._getframe(1)
            while frame is not None:
                fn = frame.f_code.co_filename
                module_name = frame.f_globals.get("__name__")
                if fn.startswith("<"):
                    frame = frame.f_back
                    continue
                if (
                    module_name in (
                        "importlib._bootstrap",
                        "importlib._bootstrap_external",
                        "_frozen_importlib",
                        "_frozen_importlib_external",
                    )
                    and fn.startswith("<frozen importlib")
                ):
                    frame = frame.f_back
                    continue
                resolved = _resolve_path(fn)
                if _is_within(WORKSPACE_ROOT, resolved):
                    # workspace code — keep walking
                    frame = frame.f_back
                    continue
                if _GUARD_SCRIPT_FILE and resolved == _GUARD_SCRIPT_FILE:
                    # guard helper frame — keep walking
                    frame = frame.f_back
                    continue
                if any(_is_within(prefix, resolved) for prefix in _INTERNAL_IMPORT_PREFIXES):
                    return True
                return False
            return False


        def _allow_library_workspace_access(operation, path_value, args=(), kwargs=None):
            # Installed libraries need absolute-path access in three cases:
            #
            # 1. Read-only probes (stat, access, read-only open) on arbitrary
            #    absolute paths — e.g. mimetypes opening /etc/mime.types,
            #    openpyxl stat-ing its temp file before deletion.  These carry
            #    no data out of the sandbox.
            #
            # 2. Write/mutate operations (mkdir, remove, unlink, chmod, …) on
            #    files inside WORKSPACE_ROOT — e.g. openpyxl atexit deleting
            #    its temp file, numba writing a JIT cache entry.
            #
            # Open calls are only allowed when read-only (no write flags).
            if kwargs is None:
                kwargs = {}
            try:
                raw_value = os.fspath(path_value)
            except TypeError:
                return False
            if not isinstance(raw_value, (str, bytes)) or not os.path.isabs(raw_value):
                return False
            if not _is_installed_library_caller():
                return False
            # Open from an installed library: read-only is allowed on any absolute
            # path; write/append is only allowed inside the workspace (e.g.
            # matplotlib writing its font cache into WORKSPACE_ROOT/.matplotlib/).
            if operation in ("open", "io.open", "os.open"):
                if _is_read_only_open(operation, args, kwargs):
                    return True
                candidate = _resolve_path(os.fsdecode(raw_value))
                return _is_within(WORKSPACE_ROOT, candidate)
            # Non-open read-only operations (stat, access, lstat) — allow anywhere.
            if _is_read_only_open(operation, args, kwargs):
                return True
            # Write/mutate operations — only allowed inside the workspace.
            candidate = _resolve_path(os.fsdecode(raw_value))
            return _is_within(WORKSPACE_ROOT, candidate)


        def _allow_import_access(operation, path_value, args, kwargs):
            if not _is_read_only_open(operation, args, kwargs):
                return False
            try:
                raw_value = os.fspath(path_value)
            except TypeError:
                return False
            if not isinstance(raw_value, (str, bytes)) or not os.path.isabs(raw_value):
                return False

            candidate = _resolve_path(os.fsdecode(raw_value))
            if _is_within(WORKSPACE_ROOT, candidate):
                return True
            # os.lstat is called by Python's realpath() on every path component —
            # both ancestor directories of and files within the interpreter/venv
            # paths. Allow it for any path that overlaps with a known import prefix
            # in either direction (ancestor-of or descendant-of). User code lstat
            # on unrelated absolute paths (e.g. /etc/passwd) is still denied since
            # /etc/passwd overlaps with no import prefix in either direction.
            if operation == "os.lstat" and any(
                _is_within(prefix, candidate) or _is_within(candidate, prefix)
                for prefix in _INTERNAL_IMPORT_PREFIXES
            ):
                return True
            if not _is_within_import_prefixes(candidate):
                return False
            return _is_import_context() or _is_runpy_context() or _is_installed_library_caller()


        def _is_within_import_prefixes(candidate):
            return any(_is_within(prefix, candidate) for prefix in _INTERNAL_IMPORT_PREFIXES)


        def _reject_dir_fd(operation, kwargs):
            for name in ("dir_fd", "src_dir_fd", "dst_dir_fd"):
                if kwargs.get(name) is not None:
                    raise FilesystemAccessDenied(
                        operation,
                        "<" + name + ">",
                        "dir_fd_not_supported",
                    )


        def _authorized_depth():
            return getattr(_POLICY_STATE, "authorized_depth", 0)


        def _call_authorized(original, *args, **kwargs):
            previous_depth = _authorized_depth()
            _POLICY_STATE.authorized_depth = previous_depth + 1
            try:
                return original(*args, **kwargs)
            finally:
                _POLICY_STATE.authorized_depth = previous_depth


        def _single_path_wrapper(
            operation,
            original,
            default_path=None,
            authorize_path=_authorize_path,
            allow_import_access=_allow_import_access,
            allow_library_workspace_access=_allow_library_workspace_access,
            authorized_depth=_authorized_depth,
            call_authorized=_call_authorized,
            reject_dir_fd=_reject_dir_fd,
        ):
            def wrapped(path_value=default_path, *args, **kwargs):
                if authorized_depth():
                    return original(path_value, *args, **kwargs)
                # open(fd, ...) wraps an already-open file descriptor — no path to validate.
                if isinstance(path_value, int):
                    return call_authorized(original, path_value, *args, **kwargs)
                reject_dir_fd(operation, kwargs)
                if allow_import_access(operation, path_value, args, kwargs):
                    return call_authorized(original, path_value, *args, **kwargs)
                if allow_library_workspace_access(operation, path_value, args, kwargs):
                    return call_authorized(original, path_value, *args, **kwargs)
                authorized = authorize_path(operation, path_value)
                return call_authorized(original, authorized, *args, **kwargs)

            return wrapped


        def _pair_path_wrapper(
            operation,
            original,
            authorize_pair=_authorize_pair,
            authorized_depth=_authorized_depth,
            call_authorized=_call_authorized,
            reject_dir_fd=_reject_dir_fd,
        ):
            def wrapped(src_value, dst_value, *args, **kwargs):
                if authorized_depth():
                    return original(src_value, dst_value, *args, **kwargs)
                reject_dir_fd(operation, kwargs)
                src_authorized, dst_authorized = authorize_pair(
                    operation,
                    src_value,
                    dst_value,
                )
                return call_authorized(
                    original,
                    src_authorized,
                    dst_authorized,
                    *args,
                    **kwargs,
                )

            return wrapped


        def _symlink_wrapper(
            operation,
            original,
            authorize_symlink=_authorize_symlink,
            authorized_depth=_authorized_depth,
            call_authorized=_call_authorized,
            reject_dir_fd=_reject_dir_fd,
        ):
            def wrapped(src_value, dst_value, *args, **kwargs):
                if authorized_depth():
                    return original(src_value, dst_value, *args, **kwargs)
                reject_dir_fd(operation, kwargs)
                src_authorized, dst_authorized = authorize_symlink(
                    operation,
                    src_value,
                    dst_value,
                )
                return call_authorized(
                    original,
                    src_authorized,
                    dst_authorized,
                    *args,
                    **kwargs,
                )

            return wrapped


        def _chdir_wrapper(operation, authorize_path=_authorize_path):
            def wrapped(path_value):
                authorize_path(operation, path_value)
                return None

            return wrapped


        def _fchdir_wrapper(operation):
            def wrapped(fd):
                raise FilesystemAccessDenied(
                    operation,
                    repr(fd),
                    "file_descriptor_not_supported",
                )

            return wrapped


        def _fdopen_wrapper(operation):
            def wrapped(fd, *args, **kwargs):
                raise FilesystemAccessDenied(
                    operation,
                    repr(fd),
                    "file_descriptor_not_supported",
                )

            return wrapped


        def _temporary_file_wrapper(original, authorize_path=_authorize_path):
            def wrapped(*args, **kwargs):
                if "dir" not in kwargs or kwargs["dir"] is None:
                    kwargs["dir"] = ".tmp"
                else:
                    authorized = authorize_path("tempfile.NamedTemporaryFile", kwargs["dir"])
                    kwargs["dir"] = os.path.relpath(os.fsdecode(authorized), WORKSPACE_ROOT)
                return original(*args, **kwargs)

            return wrapped


        def _validate_percent_escapes(operation, database):
            if _PERCENT_ESCAPE.search(database):
                raise FilesystemAccessDenied(operation, database, "malformed_file_uri")


        def _authorize_sqlite_database(operation, database):
            try:
                raw_database = os.fspath(database)
            except TypeError as exc:
                raise FilesystemAccessDenied(
                    operation,
                    repr(database),
                    "unsupported_path_type",
                ) from exc

            if isinstance(raw_database, bytes):
                try:
                    text_database = raw_database.decode(
                        sys.getfilesystemencoding(),
                        "strict",
                    )
                except UnicodeDecodeError as exc:
                    raise FilesystemAccessDenied(
                        operation,
                        repr(raw_database),
                        "undecodable_bytes",
                    ) from exc
            elif isinstance(raw_database, str):
                text_database = raw_database
            else:
                raise FilesystemAccessDenied(
                    operation,
                    repr(raw_database),
                    "unsupported_path_type",
                )

            if text_database in ("", ":memory:"):
                return raw_database
            if not text_database.startswith("file:"):
                return _authorize_path(operation, database)

            _validate_percent_escapes(operation, text_database)
            parsed = urllib.parse.urlsplit(text_database)
            if parsed.netloc or parsed.fragment:
                raise FilesystemAccessDenied(
                    operation,
                    text_database,
                    "unsupported_file_uri",
                )
            try:
                decoded_path = urllib.parse.unquote(parsed.path, errors="strict")
            except UnicodeDecodeError as exc:
                raise FilesystemAccessDenied(
                    operation,
                    text_database,
                    "malformed_file_uri",
                ) from exc
            if decoded_path == ":memory:":
                return text_database

            authorized_path = os.fsdecode(_authorize_path(operation, decoded_path))
            encoded_path = urllib.parse.quote(authorized_path, safe="/")
            authorized_uri = "file:" + encoded_path
            if parsed.query:
                authorized_uri += "?" + parsed.query
            return authorized_uri


        def _sqlite_connect_wrapper(
            original,
            authorize_sqlite_database=_authorize_sqlite_database,
            authorized_depth=_authorized_depth,
            call_authorized=_call_authorized,
        ):
            def wrapped(database, *args, **kwargs):
                if authorized_depth():
                    return original(database, *args, **kwargs)
                authorized = authorize_sqlite_database("sqlite3.connect", database)
                return call_authorized(original, authorized, *args, **kwargs)

            return wrapped


        _ORIGINALS = {}


        def _patch_single(module, module_name, name, default_path=None):
            original = getattr(module, name, None)
            if original is None:
                return
            key = module_name + "." + name
            _ORIGINALS[key] = original
            setattr(
                module,
                name,
                _single_path_wrapper("os." + name, original, default_path),
            )


        def _patch_pair(module, module_name, name):
            original = getattr(module, name, None)
            if original is None:
                return
            key = module_name + "." + name
            _ORIGINALS[key] = original
            setattr(module, name, _pair_path_wrapper("os." + name, original))


        def install_guard():
            if _ORIGINALS:
                return

            os.makedirs(WORKSPACE_ROOT, exist_ok=True)
            os.makedirs(WORKSPACE_TMP, exist_ok=True)
            os.chdir(WORKSPACE_ROOT)
            os.environ["TMPDIR"] = WORKSPACE_TMP
            os.environ["TEMP"] = WORKSPACE_TMP
            os.environ["TMP"] = WORKSPACE_TMP
            os.environ["HOME"] = WORKSPACE_ROOT
            tempfile.tempdir = WORKSPACE_TMP

            # Suppress .pyc / __pycache__ writes to installed package dirs.
            # Without this, importing any site-package triggers os.mkdir on the
            # absolute __pycache__ path, flooding stderr with denial markers.
            sys.dont_write_bytecode = True

            # Libraries that need a writable config/cache dir (e.g. matplotlib,
            # numba) derive those paths from HOME or XDG env vars.  Setting them
            # to relative paths ensures the resolved absolute path stays inside
            # the workspace (cwd is WORKSPACE_ROOT after the chdir above).
            os.environ.setdefault("MPLCONFIGDIR", ".matplotlib")
            os.environ.setdefault("NUMBA_CACHE_DIR", ".numba")
            os.environ.setdefault("XDG_CACHE_HOME", ".cache")
            os.environ.setdefault("XDG_CONFIG_HOME", ".config")

            _ORIGINALS["builtins.open"] = builtins.open
            builtins.open = _single_path_wrapper("open", builtins.open)

            _ORIGINALS["io.open"] = io.open
            io.open = _single_path_wrapper("io.open", io.open)

            for module, module_name in ((os, "os"), (_PLATFORM_OS, os.name)):
                for name, default_path in (
                    ("open", None),
                    ("listdir", "."),
                    ("scandir", "."),
                    ("stat", None),
                    ("lstat", None),
                    ("access", None),
                    ("readlink", None),
                    ("mkdir", None),
                    ("remove", None),
                    ("unlink", None),
                    ("rmdir", None),
                    ("chmod", None),
                    ("utime", None),
                    ("truncate", None),
                ):
                    _patch_single(module, module_name, name, default_path)

                for name in ("rename", "replace", "link"):
                    _patch_pair(module, module_name, name)

                original_symlink = getattr(module, "symlink", None)
                if original_symlink is not None:
                    _ORIGINALS[module_name + ".symlink"] = original_symlink
                    setattr(
                        module,
                        "symlink",
                        _symlink_wrapper("os.symlink", original_symlink),
                    )

                if hasattr(module, "chdir"):
                    _ORIGINALS[module_name + ".chdir"] = getattr(module, "chdir")
                    setattr(module, "chdir", _chdir_wrapper("os.chdir"))
                if hasattr(module, "fchdir"):
                    _ORIGINALS[module_name + ".fchdir"] = getattr(module, "fchdir")
                    setattr(module, "fchdir", _fchdir_wrapper("os.fchdir"))

            for name in ("makedirs", "removedirs"):
                original = getattr(os, name)
                _ORIGINALS["os." + name] = original
                setattr(os, name, _single_path_wrapper("os." + name, original))

            _ORIGINALS["os.fdopen"] = os.fdopen
            os.fdopen = _fdopen_wrapper("os.fdopen")

            _ORIGINALS["tempfile.NamedTemporaryFile"] = tempfile.NamedTemporaryFile
            tempfile.NamedTemporaryFile = _temporary_file_wrapper(
                tempfile.NamedTemporaryFile,
            )

            for name in ("copy", "copy2", "copyfile", "copytree", "move"):
                original = getattr(shutil, name)
                _ORIGINALS["shutil." + name] = original
                setattr(
                    shutil,
                    name,
                    _pair_path_wrapper("shutil." + name, original),
                )

            _ORIGINALS["shutil.rmtree"] = shutil.rmtree
            shutil.rmtree = _single_path_wrapper("shutil.rmtree", shutil.rmtree)

            _ORIGINALS["sqlite3.connect"] = sqlite3.connect
            sqlite3.connect = _sqlite_connect_wrapper(sqlite3.connect)

            audit_authorized_depth = _authorized_depth
            audit_allow_import_access = _allow_import_access
            audit_allow_library_workspace_access = _allow_library_workspace_access
            audit_authorize_path = _authorize_path
            audit_authorize_pair = _authorize_pair
            audit_authorize_symlink = _authorize_symlink
            audit_authorize_sqlite_database = _authorize_sqlite_database
            audit_denied = FilesystemAccessDenied
            audit_process_creation_events = _PROCESS_CREATION_EVENTS
            audit_native_library_events = _NATIVE_LIBRARY_EVENTS
            audit_workspace_root = WORKSPACE_ROOT

            def audit_hook(event, args):
                if audit_authorized_depth():
                    return
                if event in audit_process_creation_events:
                    raise audit_denied(
                        event,
                        repr(args[0]) if args else "<unknown>",
                        "process_creation_not_supported",
                    )
                if event in audit_native_library_events:
                    if event == "ctypes.dlopen":
                        # dlopen(None) opens the main process handle (RTLD_DEFAULT) —
                        # no external file is loaded; numpy/pandas do this during C
                        # extension init and it must be allowed.
                        if not args or not args[0]:
                            return
                        try:
                            lib_path = os.path.realpath(os.fsdecode(os.fspath(args[0])))
                        except (TypeError, ValueError):
                            lib_path = None
                        if lib_path and any(
                            _is_within(prefix, lib_path)
                            for prefix in _INTERNAL_IMPORT_PREFIXES
                        ):
                            return
                    raise audit_denied(
                        event,
                        repr(args[0]) if args else "<unknown>",
                        "native_library_loading_not_supported",
                    )
                if event == "open" and args:
                    # Integer first arg means fd-based open — no path to validate.
                    if isinstance(args[0], int):
                        return
                    if audit_allow_import_access("open", args[0], args[1:], {}):
                        return
                    # Absolute paths inside the workspace are always allowed — e.g.
                    # matplotlib writing its font cache to WORKSPACE_ROOT/.matplotlib/.
                    # _allow_library_workspace_access relies on frame-stack inspection
                    # which is unavailable from the audit hook (the hook fires at the C
                    # level before Python frames for the actual open call are visible).
                    try:
                        _audit_path = os.fsdecode(os.fspath(args[0]))
                        if os.path.isabs(_audit_path) and _is_within(
                            audit_workspace_root,
                            _PRISTINE_REALPATH(_audit_path),
                        ):
                            return
                    except (TypeError, ValueError):
                        pass
                    if audit_allow_library_workspace_access("open", args[0], args[1:], {}):
                        return
                    audit_authorize_path("open", args[0])
                elif event == "os.chdir" and args and args[0] != audit_workspace_root:
                    raise audit_denied(
                        event,
                        repr(args[0]) if args else "<unknown>",
                        "current_directory_change_not_supported",
                    )
                elif event in ("os.rename", "os.replace", "os.link") and len(args) >= 2:
                    audit_authorize_pair(event, args[0], args[1])
                elif event == "os.symlink" and len(args) >= 2:
                    audit_authorize_symlink(event, args[0], args[1])
                elif event in (
                    "os.remove",
                    "os.unlink",
                    "os.mkdir",
                    "os.rmdir",
                    "os.listdir",
                    "os.scandir",
                    "os.stat",
                    "os.lstat",
                    "os.access",
                    "os.readlink",
                    "os.chmod",
                    "os.utime",
                    "os.truncate",
                ) and args:
                    path_value = args[0] if args[0] is not None else "."
                    if not audit_allow_library_workspace_access(event, path_value):
                        audit_authorize_path(event, path_value)
                elif event == "shutil.copyfile" and len(args) >= 2:
                    audit_authorize_pair(event, args[0], args[1])
                elif event == "shutil.rmtree" and args:
                    audit_authorize_path(event, args[0])
                elif event == "sqlite3.connect" and args:
                    audit_authorize_sqlite_database(event, args[0])

            sys.addaudithook(audit_hook)
    """
    return (
        textwrap.dedent(template)
        .replace("__CODEMIE_DENIAL_MARKER__", repr(DENIAL_MARKER))
        .replace("__CODEMIE_DENIAL_MESSAGE__", repr(CUSTOMER_DENIAL_MESSAGE))
        .replace("__CODEMIE_WORKSPACE_ROOT__", repr(workspace_root))
        .strip()
    )


def extract_denial_events(stderr: str) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for line in stderr.splitlines():
        if not line.startswith(DENIAL_MARKER):
            continue
        payload = line.removeprefix(DENIAL_MARKER)
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events
