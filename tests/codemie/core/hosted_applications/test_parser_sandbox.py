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

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from codemie.hosted_apps_validator.sandbox import _denied


def test_missing_program_is_not_proof_that_execution_is_blocked(tmp_path):
    missing = tmp_path / "program-not-installed"
    assert not _denied(lambda: subprocess.run([str(missing)], check=True, capture_output=True, timeout=1))
    assert not _denied(lambda: subprocess.run([sys.executable, "-c", "pass"], check=True, timeout=1))


@pytest.mark.parametrize("probe", [False, True])
def test_real_linux_parser_isolation(tmp_path, probe):
    image = os.environ.get("HOSTED_TEST_SANDBOX_IMAGE")
    if not image:
        pytest.skip("Set HOSTED_TEST_SANDBOX_IMAGE for real Docker/Linux isolation tests")
    root = Path(__file__).parents[4]
    shared = tempfile.TemporaryDirectory(prefix=".hosted-sandbox-test-", dir=root)
    source = Path(shared.name) / "sample.html"
    source.write_text("<html>Safe static app</html>")
    command = [
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=32",
        "--cpus=1",
        "--memory=1200m",
        "--user=65532:65532",
        "--mount",
        f"type=bind,source={root / 'src'},target=/app/src,readonly",
        "--mount",
        f"type=bind,source={source},target=/input.html,readonly",
        "--tmpfs",
        "/work:rw,nosuid,nodev,noexec,size=600m,uid=65532,gid=65532,mode=700",
        "--env",
        "PYTHONPATH=/app/src",
        image,
        "python",
        "-B",
        "-m",
        "codemie.hosted_apps_validator.sandbox",
        "/input.html",
        "/work/parser/assets",
        "probe" if probe else "html",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=150, check=False)
    shared.cleanup()
    assert result.returncode == 0, result.stderr[-1000:]
    value = json.loads(result.stdout)
    if probe:
        assert value == {
            "network_denied": True,
            "outside_read_denied": True,
            "outside_write_denied": True,
            "environment_empty": True,
            "exec_denied": True,
        }
    else:
        assert value["entries"] == ["index.html"] and value["file_count"] == 1
