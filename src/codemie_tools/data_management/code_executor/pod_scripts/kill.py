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

# Kills the run's script process, but only when it is provably still the same process: the recorded start time
# must equal field 22 of <proc>/<pid>/stat, the command line must carry the sandbox marker, and the process must
# work in the same directory as this exec (the workspace root). The pid and start time files live in a folder the
# script can write to, so they alone must not decide: the directory check keeps a doctored pid from reaching a
# process of another user or conversation, which works in a different workspace. A missing start time (no
# readable /proc when the script started) means no kill. The process table root is the last argument so the
# decision can be exercised against a synthetic one in tests; production always passes PROC_ROOT.
# argv: <run dir> <cmdline marker> <proc root>.

import contextlib
import os
import signal
import sys


def main(argv: list[str]) -> int:
    run_dir = argv[1]
    marker = argv[2].encode()
    proc_root = argv[3]
    workdir = os.path.realpath(os.getcwd())

    def read(name: str) -> str:
        try:
            with open(os.path.join(run_dir, name), "rb") as handle:
                return handle.read(64).decode("ascii").strip()
        except (OSError, UnicodeDecodeError):
            return ""

    pid_text = read("pid")
    recorded = read("start_time")
    if not pid_text.isdigit() or not recorded:
        return 0
    pid = int(pid_text)
    if pid <= 1:
        return 0
    entry = os.path.join(proc_root, str(pid))
    try:
        with open(os.path.join(entry, "stat"), "r") as handle:
            actual = handle.read().rsplit(")", 1)[1].split()[19]
        with open(os.path.join(entry, "cmdline"), "rb") as handle:
            cmdline = handle.read()
        target_cwd = os.path.realpath(os.readlink(os.path.join(entry, "cwd")))
    except (OSError, IndexError):
        return 0
    if actual != recorded or marker not in cmdline or target_cwd != workdir:
        return 0
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
