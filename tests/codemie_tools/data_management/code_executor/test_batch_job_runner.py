# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Unit tests for batch_job_runner module."""

import base64
import io
import tarfile
import unittest
from unittest.mock import MagicMock, call, patch

import pytest
from kubernetes.client.exceptions import ApiException
from langchain_core.tools import ToolException

from codemie_tools.data_management.code_executor.batch_job_runner import (
    BatchJobRunner,
    JobResult,
)
from codemie_tools.data_management.code_executor.job_bridge import JobBridgeOptions
from codemie_tools.data_management.code_executor.models import CodeExecutorConfig


def _make_config(**overrides) -> CodeExecutorConfig:
    defaults = {
        "max_pod_pool_size": 2,
        "default_timeout": 1.0,
        "execution_timeout": 5.0,
        "namespace": "test-ns",
        "docker_image": "img:latest",
        "pod_name_prefix": "sbx-",
        "run_as_user": 1001,
        "run_as_group": 1001,
        "fs_group": 1001,
        "cpu_request": "100m",
        "cpu_limit": "1",
        "memory_request": "256Mi",
        "memory_limit": "512Mi",
    }
    defaults.update(overrides)
    return CodeExecutorConfig(**defaults)


def _bridge_at(exchange_dir: str, tool_calling_timeout: float = 120.0) -> JobBridgeOptions:
    return JobBridgeOptions(exchange_dir=exchange_dir, tool_calling_timeout=tool_calling_timeout)


def _bridge_options(tool_calling_timeout: float | None) -> JobBridgeOptions | None:
    if tool_calling_timeout is None:
        return None
    return _bridge_at(".codemie_bridge/ex1", tool_calling_timeout)


def _terminal_status(succeeded=1, failed=0):
    s = MagicMock()
    s.succeeded = succeeded
    s.failed = failed
    s.conditions = None
    return MagicMock(status=s)


def _running_status():
    s = MagicMock()
    s.succeeded = None
    s.failed = None
    s.conditions = None
    return MagicMock(status=s)


def _pod(phase: str = "Running", exit_code: int = 0, name: str = "sbx-pod-xyz"):
    """Build a pod mock with both phase (for wait-for-Running) and terminated state."""
    pod = MagicMock()
    pod.metadata.name = name
    term = MagicMock()
    term.exit_code = exit_code
    cs = MagicMock()
    cs.state = MagicMock(terminated=term)
    pod.status = MagicMock(phase=phase, container_statuses=[cs])
    return pod


def _patch_runner_internals(runner: BatchJobRunner, pod_name: str = "sbx-pod-xyz", stderr_content: bytes = b""):
    """Stub the exec helpers so high-level tests don't have to mock stream()."""
    runner._wait_for_pod_running = MagicMock(return_value=pod_name)
    runner._wait_for_sentinel = MagicMock(return_value=None)
    runner._exec_tar_out = MagicMock(return_value=stderr_content or None)
    runner._signal_cleanup = MagicMock(return_value=None)
    return runner


class TestBatchJobRunnerHappyPath(unittest.TestCase):
    def setUp(self):
        BatchJobRunner._instance = None

    def test_run_returns_job_result_with_stdout_and_exit_code(self):
        config = _make_config()
        batch = MagicMock(name="batch")
        core = MagicMock(name="core")
        batch.read_namespaced_job_status.return_value = _terminal_status(succeeded=1)
        core.list_namespaced_pod.return_value = MagicMock(items=[_pod(phase="Running", exit_code=0, name="the-pod")])
        core.read_namespaced_pod_log.return_value = "hello\n"

        with patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager") as mgr_cls:
            mgr = mgr_cls.return_value
            mgr.get_batch_client.return_value = batch
            mgr.get_client.return_value = core
            runner = BatchJobRunner(config)
            _patch_runner_internals(runner, pod_name="the-pod")
            with (
                patch.object(runner, "_upload_payload") as upload,
                patch.object(runner, "_download_exports", return_value={}) as download,
            ):
                result = runner.run("print('hello')")

        assert isinstance(result, JobResult)
        assert result.stdout == "hello\n"
        assert result.stderr == ""
        assert result.exit_code == 0
        assert result.exported_files == {}

        # Upload was called with code as script.py
        upload.assert_called_once()
        up_args = upload.call_args
        assert up_args.args[0] == "the-pod"
        assert up_args.args[1] == "/workspace"
        assert up_args.args[2] == "print('hello')"

        # No exports requested → empty list passed to download
        download.assert_called_once_with("the-pod", "/workspace", [])

        # Job creation issued with correct manifest shape
        batch.create_namespaced_job.assert_called_once()
        kwargs = batch.create_namespaced_job.call_args.kwargs
        assert kwargs["namespace"] == "test-ns"
        manifest = kwargs["body"]
        assert manifest["apiVersion"] == "batch/v1"
        assert manifest["kind"] == "Job"
        assert manifest["spec"]["backoffLimit"] == 0
        assert manifest["spec"]["ttlSecondsAfterFinished"] == 60
        assert manifest["spec"]["activeDeadlineSeconds"] >= int(config.execution_timeout)
        container = manifest["spec"]["template"]["spec"]["containers"][0]
        assert container["image"] == "img:latest"
        # env -i <vars> bash -c <inner> — no outer shell
        assert container["command"][0] == "env"
        assert container["command"][1] == "-i"
        inner = container["command"][-1]
        assert container["command"][-2] == "-c"
        assert ".ready" in inner
        assert ".pulled" in inner
        pod_spec = manifest["spec"]["template"]["spec"]
        assert pod_spec["runtimeClassName"] == "gvisor"
        sec = pod_spec["securityContext"]
        assert sec["runAsUser"] == 1001 and sec["runAsGroup"] == 1001 and sec["fsGroup"] == 1001

        # Job deleted in finally with grace_period_seconds=0
        batch.delete_namespaced_job.assert_called_once()
        del_kwargs = batch.delete_namespaced_job.call_args.kwargs
        assert del_kwargs["body"].grace_period_seconds == 0
        assert del_kwargs["body"].propagation_policy == "Foreground"

    def _run_capturing_budget(self, tool_calling_timeout: float | None = None) -> tuple[int, float]:
        """Run once with time.monotonic pinned to 1000.0; return (activeDeadlineSeconds, deadline - now)."""
        config = _make_config(execution_timeout=5.0)
        batch = MagicMock(name="batch")
        core = MagicMock(name="core")
        batch.read_namespaced_job_status.return_value = _terminal_status(succeeded=1)
        core.list_namespaced_pod.return_value = MagicMock(items=[_pod(phase="Running", exit_code=0, name="the-pod")])
        core.read_namespaced_pod_log.return_value = ""

        with (
            patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager") as mgr_cls,
            patch("codemie_tools.data_management.code_executor.batch_job_runner.JobToolCallBridge"),
        ):
            mgr = mgr_cls.return_value
            mgr.get_batch_client.return_value = batch
            mgr.get_client.return_value = core
            runner = BatchJobRunner(config)
            _patch_runner_internals(runner, pod_name="the-pod")
            with (
                patch.object(runner, "_upload_payload"),
                patch.object(runner, "_download_exports", return_value={}),
                patch(
                    "codemie_tools.data_management.code_executor.batch_job_runner.time.monotonic", return_value=1000.0
                ),
            ):
                runner.run("print('hello')", bridge=_bridge_options(tool_calling_timeout))

        manifest = batch.create_namespaced_job.call_args.kwargs["body"]
        deadline = runner._wait_for_pod_running.call_args.args[1]
        return manifest["spec"]["activeDeadlineSeconds"], deadline - 1000.0

    def test_tool_calling_timeout_widens_job_deadline(self):
        active_deadline, deadline_offset = self._run_capturing_budget(tool_calling_timeout=120)

        assert active_deadline == 180
        assert deadline_offset == 180.0

    def test_deadline_defaults_to_execution_timeout_without_tool_calling(self):
        active_deadline, deadline_offset = self._run_capturing_budget()

        assert active_deadline == 65
        assert deadline_offset == 65.0

    def test_run_passes_the_widened_budget_to_every_wait_helper(self):
        config = _make_config(execution_timeout=5.0)
        batch = MagicMock(name="batch")
        core = MagicMock(name="core")
        batch.read_namespaced_job_status.return_value = _terminal_status(succeeded=1)
        core.list_namespaced_pod.return_value = MagicMock(items=[_pod(phase="Running", exit_code=0, name="the-pod")])
        core.read_namespaced_pod_log.return_value = ""

        with (
            patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager") as mgr_cls,
            patch("codemie_tools.data_management.code_executor.batch_job_runner.JobToolCallBridge"),
        ):
            mgr_cls.return_value.get_batch_client.return_value = batch
            mgr_cls.return_value.get_client.return_value = core
            runner = BatchJobRunner(config)
            _patch_runner_internals(runner, pod_name="the-pod")
            with (
                patch.object(runner, "_upload_payload"),
                patch.object(runner, "_download_exports", return_value={}),
                patch.object(runner, "_wait_for_completion", return_value=0) as completion,
            ):
                runner.run("print('hello')", bridge=_bridge_options(120))

        assert runner._wait_for_pod_running.call_args.kwargs["budget_seconds"] == 180.0
        assert runner._wait_for_sentinel.call_args.kwargs["budget_seconds"] == 180.0
        assert completion.call_args.kwargs["budget_seconds"] == 180.0

    def test_smaller_tool_calling_timeout_does_not_shrink_deadline(self):
        active_deadline, deadline_offset = self._run_capturing_budget(tool_calling_timeout=2)

        assert active_deadline == 65
        assert deadline_offset == 65.0

    def test_manifest_omits_runtime_class_name_when_none(self):
        config = _make_config(runtime_class_name=None)
        batch = MagicMock(name="batch")
        core = MagicMock(name="core")
        batch.read_namespaced_job_status.return_value = _terminal_status(succeeded=1)
        core.list_namespaced_pod.return_value = MagicMock(items=[_pod(phase="Running", exit_code=0, name="the-pod")])
        core.read_namespaced_pod_log.return_value = ""

        with patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager") as mgr_cls:
            mgr = mgr_cls.return_value
            mgr.get_batch_client.return_value = batch
            mgr.get_client.return_value = core
            runner = BatchJobRunner(config)
            _patch_runner_internals(runner, pod_name="the-pod")
            with (
                patch.object(runner, "_upload_payload"),
                patch.object(runner, "_download_exports", return_value={}),
            ):
                runner.run("print('ok')")

        manifest = batch.create_namespaced_job.call_args.kwargs["body"]
        pod_spec = manifest["spec"]["template"]["spec"]
        assert "runtimeClassName" not in pod_spec

    def test_manifest_includes_tolerations_when_set(self):
        tolerations = [{"key": "dedicated", "operator": "Equal", "value": "codemie", "effect": "NoSchedule"}]
        config = _make_config(tolerations=tolerations)
        batch = MagicMock(name="batch")
        core = MagicMock(name="core")
        batch.read_namespaced_job_status.return_value = _terminal_status(succeeded=1)
        core.list_namespaced_pod.return_value = MagicMock(items=[_pod(phase="Running", exit_code=0, name="the-pod")])
        core.read_namespaced_pod_log.return_value = ""

        with patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager") as mgr_cls:
            mgr = mgr_cls.return_value
            mgr.get_batch_client.return_value = batch
            mgr.get_client.return_value = core
            runner = BatchJobRunner(config)
            _patch_runner_internals(runner, pod_name="the-pod")
            with (
                patch.object(runner, "_upload_payload"),
                patch.object(runner, "_download_exports", return_value={}),
            ):
                runner.run("print('ok')")

        manifest = batch.create_namespaced_job.call_args.kwargs["body"]
        pod_spec = manifest["spec"]["template"]["spec"]
        assert pod_spec["tolerations"] == tolerations

    def test_manifest_omits_tolerations_when_empty(self):
        config = _make_config(tolerations=None)
        batch = MagicMock(name="batch")
        core = MagicMock(name="core")
        batch.read_namespaced_job_status.return_value = _terminal_status(succeeded=1)
        core.list_namespaced_pod.return_value = MagicMock(items=[_pod(phase="Running", exit_code=0, name="the-pod")])
        core.read_namespaced_pod_log.return_value = ""

        with patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager") as mgr_cls:
            mgr = mgr_cls.return_value
            mgr.get_batch_client.return_value = batch
            mgr.get_client.return_value = core
            runner = BatchJobRunner(config)
            _patch_runner_internals(runner, pod_name="the-pod")
            with (
                patch.object(runner, "_upload_payload"),
                patch.object(runner, "_download_exports", return_value={}),
            ):
                runner.run("print('ok')")

        manifest = batch.create_namespaced_job.call_args.kwargs["body"]
        pod_spec = manifest["spec"]["template"]["spec"]
        assert "tolerations" not in pod_spec

    def test_run_creates_job_with_readonly_root_and_workdir_and_tmp_volumes(self):
        config = _make_config()
        batch = MagicMock(name="batch")
        core = MagicMock(name="core")
        batch.read_namespaced_job_status.return_value = _terminal_status(succeeded=1)
        core.list_namespaced_pod.return_value = MagicMock(items=[_pod(phase="Running", exit_code=0, name="the-pod")])
        core.read_namespaced_pod_log.return_value = "hello\n"

        with patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager") as mgr_cls:
            mgr = mgr_cls.return_value
            mgr.get_batch_client.return_value = batch
            mgr.get_client.return_value = core
            runner = BatchJobRunner(config)
            _patch_runner_internals(runner, pod_name="the-pod")
            with (
                patch.object(runner, "_upload_payload"),
                patch.object(runner, "_download_exports", return_value={}),
            ):
                runner.run("print('hello')", workdir="/workspace")

        manifest = batch.create_namespaced_job.call_args.kwargs["body"]
        pod_spec = manifest["spec"]["template"]["spec"]
        container = pod_spec["containers"][0]

        assert pod_spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
        container_sec = container["securityContext"]
        assert container_sec["readOnlyRootFilesystem"] is True
        assert container_sec["seccompProfile"] == {"type": "RuntimeDefault"}

        volumes = pod_spec["volumes"]
        assert any(v["name"] == "workdir" and "emptyDir" in v for v in volumes)
        assert any(v["name"] == "tmp" and "emptyDir" in v for v in volumes)
        mounts = container["volumeMounts"]
        assert any(m["name"] == "workdir" and m["mountPath"] == "/workspace" for m in mounts)
        assert any(m["name"] == "tmp" and m["mountPath"] == "/tmp/runtime" for m in mounts)

    def test_run_extracts_non_zero_exit_code(self):
        config = _make_config()
        batch = MagicMock()
        core = MagicMock()
        batch.read_namespaced_job_status.return_value = _terminal_status(succeeded=0, failed=1)
        core.list_namespaced_pod.return_value = MagicMock(items=[_pod(phase="Running", exit_code=2)])
        core.read_namespaced_pod_log.return_value = ""

        with patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager") as mgr_cls:
            mgr = mgr_cls.return_value
            mgr.get_batch_client.return_value = batch
            mgr.get_client.return_value = core
            runner = BatchJobRunner(config)
            _patch_runner_internals(runner)
            with patch.object(runner, "_upload_payload"), patch.object(runner, "_download_exports", return_value={}):
                result = runner.run("raise SystemExit(2)")

        assert result.exit_code == 2

    def test_run_populates_stderr_from_stderr_file(self):
        config = _make_config()
        batch = MagicMock()
        core = MagicMock()
        batch.read_namespaced_job_status.return_value = _terminal_status(succeeded=1)
        core.list_namespaced_pod.return_value = MagicMock(items=[_pod(phase="Running")])
        core.read_namespaced_pod_log.return_value = "stdout line\n"
        denial = b"__CODEMIE_FS_DENIED__" + b'{"operation":"open","path":"../x","reason":"outside_workspace"}\n'

        with patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager") as mgr_cls:
            mgr = mgr_cls.return_value
            mgr.get_batch_client.return_value = batch
            mgr.get_client.return_value = core
            runner = BatchJobRunner(config)
            _patch_runner_internals(runner, stderr_content=denial)
            with patch.object(runner, "_upload_payload"), patch.object(runner, "_download_exports", return_value={}):
                result = runner.run("print('hi')")

        assert result.stdout == "stdout line\n"
        assert denial.decode() in result.stderr


class TestBatchJobRunnerFiles(unittest.TestCase):
    def setUp(self):
        BatchJobRunner._instance = None

    def test_input_files_uploaded_alongside_script(self):
        config = _make_config()
        batch = MagicMock()
        core = MagicMock()
        batch.read_namespaced_job_status.return_value = _terminal_status(succeeded=1)
        core.list_namespaced_pod.return_value = MagicMock(items=[_pod(phase="Running")])
        core.read_namespaced_pod_log.return_value = ""

        with patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager") as mgr_cls:
            mgr = mgr_cls.return_value
            mgr.get_batch_client.return_value = batch
            mgr.get_client.return_value = core
            runner = BatchJobRunner(config)
            _patch_runner_internals(runner)
            with (
                patch.object(runner, "_exec_tar_in") as tar_in,
                patch.object(runner, "_download_exports", return_value={}),
            ):
                runner.run(
                    "import json; print(json.load(open('data.json')))",
                    input_files={"data.json": b'{"k": 1}'},
                )

        tar_in.assert_called_once()
        pod_name, workdir, payload = tar_in.call_args.args
        assert pod_name
        assert workdir == "/workspace"
        # Payload includes the user input, the script, and the .ready sentinel last
        assert payload["data.json"] == b'{"k": 1}'
        assert payload["script.py"].startswith(b"import json")
        keys = list(payload.keys())
        assert keys[-1] == ".ready"

    def test_exported_files_returned_as_bytes(self):
        config = _make_config()
        batch = MagicMock()
        core = MagicMock()
        batch.read_namespaced_job_status.return_value = _terminal_status(succeeded=1)
        core.list_namespaced_pod.return_value = MagicMock(items=[_pod(phase="Running")])
        core.read_namespaced_pod_log.return_value = ""

        with patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager") as mgr_cls:
            mgr = mgr_cls.return_value
            mgr.get_batch_client.return_value = batch
            mgr.get_client.return_value = core
            runner = BatchJobRunner(config)
            _patch_runner_internals(runner)
            with (
                patch.object(runner, "_upload_payload"),
                patch.object(
                    runner,
                    "_exec_tar_out",
                    side_effect=lambda pod, wd, p: b"OUT-" + p.encode(),
                ),
            ):
                result = runner.run("print('x')", export_files=["out.txt", "log.csv"])

        assert result.exported_files == {"out.txt": b"OUT-out.txt", "log.csv": b"OUT-log.csv"}

    def test_download_changed_files_skips_bridge_folder(self):
        config = _make_config()
        snapshot = {
            "a.txt": "h1",
            ".codemie_bridge/x/req.1.json": "h2",
            ".codemie_bridge_other.txt": "h3",
        }

        with patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager"):
            runner = BatchJobRunner(config)
            with (
                patch.object(runner, "_snapshot_workdir", return_value=snapshot),
                patch.object(runner, "_download_exports", return_value={}) as download,
            ):
                runner._download_changed_files("sbx-pod-xyz", "/workspace", {})

        download.assert_called_once_with("sbx-pod-xyz", "/workspace", [".codemie_bridge_other.txt", "a.txt"])


class TestBatchJobRunnerExecHelpers(unittest.TestCase):
    """tar pack/unpack round-trips without going through stream()."""

    def setUp(self):
        BatchJobRunner._instance = None

    def _runner(self):
        with patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager"):
            return BatchJobRunner(_make_config())

    def test_exec_tar_in_writes_correct_tar_to_stdin(self):
        runner = self._runner()
        captured = {}

        def fake_exec(pod_name, command, stdin=None):
            captured["pod_name"] = pod_name
            captured["command"] = command
            captured["stdin"] = stdin
            return b""

        with patch.object(runner, "_exec", side_effect=fake_exec):
            runner._exec_tar_in("the-pod", "/workspace", {"a.txt": b"hello", "b.bin": b"\x00\x01\x02"})

        assert captured["command"] == [
            "sh",
            "-c",
            'mkdir -p "$1" && tar xf - -C "$1"',
            "sh",
            "/workspace",
        ]
        assert captured["pod_name"] == "the-pod"
        # Parse the tar we sent
        buf = io.BytesIO(captured["stdin"])
        with tarfile.open(fileobj=buf, mode="r") as tar:
            members = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
        assert members == {"a.txt": b"hello", "b.bin": b"\x00\x01\x02"}

    def test_exec_tar_out_extracts_single_file_bytes(self):
        runner = self._runner()
        # Build a tar containing one file as if `tar cf - <path> | base64 -w0` produced it
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name="out.txt")
            payload = b"exported-content"
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        encoded = base64.b64encode(buf.getvalue())

        with patch.object(runner, "_exec", return_value=encoded) as exec_mock:
            content = runner._exec_tar_out("the-pod", "/workspace", "out.txt")

        assert content == b"exported-content"
        exec_mock.assert_called_once_with(
            "the-pod",
            ["sh", "-c", 'tar cf - -C "$1" "$2" | base64 -w0', "sh", "/workspace", "out.txt"],
        )

    def test_exec_tar_out_returns_none_on_empty(self):
        runner = self._runner()
        with patch.object(runner, "_exec", return_value=b""):
            assert runner._exec_tar_out("the-pod", "/workspace", "missing.txt") is None


class TestBatchJobRunnerWaitHelpers(unittest.TestCase):
    def setUp(self):
        BatchJobRunner._instance = None

    def _runner(self):
        with patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager"):
            return BatchJobRunner(_make_config())

    def test_wait_for_pod_running_returns_pod_name_when_running(self):
        runner = self._runner()
        with patch.object(runner, "_find_job_pod", return_value=_pod(phase="Running", name="x")):
            assert runner._wait_for_pod_running("job-x", deadline=1e9) == "x"

    def test_wait_for_pod_running_raises_on_terminal_phase_before_upload(self):
        runner = self._runner()
        with patch.object(runner, "_find_job_pod", return_value=_pod(phase="Failed", name="x")):
            with pytest.raises(ToolException, match="terminal phase"):
                runner._wait_for_pod_running("job-x", deadline=1e9)

    def test_wait_for_sentinel_returns_when_exec_succeeds(self):
        runner = self._runner()
        with patch.object(runner, "_exec", return_value=b""):
            runner._wait_for_sentinel("the-pod", "/workspace/.done", deadline=1e9)

    def test_wait_for_sentinel_times_out_when_file_never_appears(self):
        runner = self._runner()
        with (
            patch.object(runner, "_exec", side_effect=ToolException("nope")),
            patch("codemie_tools.data_management.code_executor.batch_job_runner.time.sleep"),
            patch(
                "codemie_tools.data_management.code_executor.batch_job_runner.time.monotonic",
                side_effect=[0.0] + [1000.0] * 10,
            ),
        ):
            with pytest.raises(ToolException, match="did not complete"):
                runner._wait_for_sentinel("the-pod", "/workspace/.done", deadline=0.0)

    def test_wait_for_pod_running_timeout_reports_the_given_budget(self):
        runner = self._runner()
        with patch.object(runner, "_find_job_pod", return_value=None):
            with pytest.raises(ToolException, match=r"within 180s"):
                runner._wait_for_pod_running("job-x", deadline=0.0, budget_seconds=180.0)

    def test_wait_for_sentinel_timeout_reports_the_given_budget(self):
        runner = self._runner()
        with pytest.raises(ToolException, match=r"within 180s"):
            runner._wait_for_sentinel("the-pod", "/workspace/.done", deadline=0.0, budget_seconds=180.0)

    def test_wait_for_completion_timeout_reports_the_given_budget(self):
        runner = self._runner()
        with pytest.raises(ToolException, match=r"within 180s"):
            runner._wait_for_completion("job-x", deadline=0.0, budget_seconds=180.0)

    def test_timeouts_default_to_the_execution_timeout_budget(self):
        runner = self._runner()
        with pytest.raises(ToolException, match=r"within 65s"):
            runner._wait_for_sentinel("the-pod", "/workspace/.done", deadline=0.0)


class TestBatchJobRunnerErrors(unittest.TestCase):
    def setUp(self):
        BatchJobRunner._instance = None

    def test_create_job_failure_raises_tool_exception(self):
        config = _make_config()
        batch = MagicMock()
        batch.create_namespaced_job.side_effect = ApiException(status=500, reason="boom")

        with patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager") as mgr_cls:
            mgr = mgr_cls.return_value
            mgr.get_batch_client.return_value = batch
            mgr.get_client.return_value = MagicMock()
            with pytest.raises(ToolException, match="Failed to create sandbox Job"):
                BatchJobRunner(config).run("print('x')")

        batch.delete_namespaced_job.assert_called_once()

    def test_capacity_exhausted_raises(self):
        config = _make_config(max_pod_pool_size=1, default_timeout=0.05)
        with patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager"):
            runner = BatchJobRunner(config)
            assert runner.semaphore.acquire(blocking=False) is True
            with pytest.raises(ToolException, match="at capacity"):
                runner.run("print('x')")

    def test_delete_swallows_404(self):
        config = _make_config()
        batch = MagicMock()
        core = MagicMock()
        batch.read_namespaced_job_status.return_value = _terminal_status(succeeded=1)
        core.list_namespaced_pod.return_value = MagicMock(items=[_pod(phase="Running")])
        core.read_namespaced_pod_log.return_value = ""
        batch.delete_namespaced_job.side_effect = ApiException(status=404, reason="Not Found")

        with patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager") as mgr_cls:
            mgr = mgr_cls.return_value
            mgr.get_batch_client.return_value = batch
            mgr.get_client.return_value = core
            runner = BatchJobRunner(config)
            _patch_runner_internals(runner)
            with patch.object(runner, "_upload_payload"), patch.object(runner, "_download_exports", return_value={}):
                result = runner.run("print('ok')")

        assert result.exit_code == 0


class TestSemaphoreReleased(unittest.TestCase):
    def setUp(self):
        BatchJobRunner._instance = None

    def test_semaphore_released_on_success(self):
        config = _make_config(max_pod_pool_size=1)
        batch = MagicMock()
        core = MagicMock()
        batch.read_namespaced_job_status.return_value = _terminal_status(succeeded=1)
        core.list_namespaced_pod.return_value = MagicMock(items=[_pod(phase="Running")])
        core.read_namespaced_pod_log.return_value = ""

        with patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager") as mgr_cls:
            mgr = mgr_cls.return_value
            mgr.get_batch_client.return_value = batch
            mgr.get_client.return_value = core
            runner = BatchJobRunner(config)
            _patch_runner_internals(runner)
            with patch.object(runner, "_upload_payload"), patch.object(runner, "_download_exports", return_value={}):
                runner.run("print('x')")
            assert runner.semaphore.acquire(blocking=False) is True
            runner.semaphore.release()

    def test_semaphore_released_on_failure(self):
        config = _make_config(max_pod_pool_size=1)
        batch = MagicMock()
        batch.create_namespaced_job.side_effect = ApiException(status=500, reason="boom")

        with patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager") as mgr_cls:
            mgr = mgr_cls.return_value
            mgr.get_batch_client.return_value = batch
            mgr.get_client.return_value = MagicMock()
            runner = BatchJobRunner(config)
            with pytest.raises(ToolException):
                runner.run("print('x')")
            assert runner.semaphore.acquire(blocking=False) is True
            runner.semaphore.release()


class TestFindJobPod(unittest.TestCase):
    """Retry semantics for _find_job_pod: recreate the K8s client on
    connection-level errors (ApiException status=0) and retry once."""

    def setUp(self):
        BatchJobRunner._instance = None
        patcher = patch("codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager")
        mgr_cls = patcher.start()
        self.addCleanup(patcher.stop)
        self.mgr = mgr_cls.return_value
        self.core = MagicMock(name="core")
        self.mgr.get_client.return_value = self.core
        self.mgr.recreate_client.return_value = self.core
        self.runner = BatchJobRunner(_make_config())

    def test_returns_pod_on_success(self):
        pod = _pod(name="p1")
        self.core.list_namespaced_pod.return_value = MagicMock(items=[pod])
        assert self.runner._find_job_pod("job-1") is pod
        self.mgr.recreate_client.assert_not_called()

    def test_returns_none_when_no_pods(self):
        self.core.list_namespaced_pod.return_value = MagicMock(items=[])
        assert self.runner._find_job_pod("job-1") is None
        self.mgr.recreate_client.assert_not_called()

    def test_connection_error_recreates_client_and_retries(self):
        pod = _pod(name="p1")
        self.core.list_namespaced_pod.side_effect = [
            ApiException(status=0, reason="Handshake status 200 OK"),
            MagicMock(items=[pod]),
        ]
        assert self.runner._find_job_pod("job-1") is pod
        self.mgr.recreate_client.assert_called_once()
        assert self.core.list_namespaced_pod.call_count == 2

    def test_persistent_connection_error_returns_none_after_single_retry(self):
        self.core.list_namespaced_pod.side_effect = ApiException(status=0, reason="Handshake status 200 OK")
        assert self.runner._find_job_pod("job-1") is None
        assert self.core.list_namespaced_pod.call_count == 2
        # Client is recreated after every corrupted-pool failure (including the
        # final one) so the next poll tick starts with a clean client.
        assert self.mgr.recreate_client.call_count == 2

    def test_api_error_does_not_retry(self):
        self.core.list_namespaced_pod.side_effect = ApiException(status=404, reason="Not Found")
        assert self.runner._find_job_pod("job-1") is None
        self.mgr.recreate_client.assert_not_called()
        assert self.core.list_namespaced_pod.call_count == 1


_BJR = "codemie_tools.data_management.code_executor.batch_job_runner"
_JB = "codemie_tools.data_management.code_executor.job_bridge"


class TestBatchJobRunnerToolCallChannel(unittest.TestCase):
    """The tool-call channel is bound to the Job pod and closed before any download."""

    def setUp(self):
        BatchJobRunner._instance = None

    def _run(
        self,
        *,
        sentinel_error: ToolException | None = None,
        snapshot: dict[str, str] | None = None,
        cleanup_error: Exception | None = None,
        real_exports: bool = False,
        **run_kwargs,
    ):
        events: list[str] = []
        self.events = events
        channel = MagicMock(name="channel")
        self.channel = channel
        channel.start.side_effect = lambda: events.append("start")
        channel.stop.side_effect = lambda: events.append("stop")

        def _cleanup(*, kill: bool) -> None:
            events.append(f"cleanup(kill={kill})")
            if cleanup_error is not None:
                raise cleanup_error

        channel.cleanup.side_effect = _cleanup

        batch = MagicMock(name="batch")
        batch.read_namespaced_job_status.return_value = _terminal_status(succeeded=1)
        core = MagicMock(name="core")
        core.read_namespaced_pod_log.return_value = ""

        with (
            patch(f"{_BJR}.KubernetesClientManager") as mgr_cls,
            patch(f"{_JB}.KubernetesExecRunner") as runner_cls,
            patch(f"{_JB}.ToolCallChannel", return_value=channel) as channel_cls,
        ):
            mgr_cls.return_value.get_batch_client.return_value = batch
            mgr_cls.return_value.get_client.return_value = core
            runner = BatchJobRunner(_make_config(kubeconfig_path=""))
            _patch_runner_internals(runner, pod_name="the-pod")

            def _sentinel(*_args: object, **_kwargs: object) -> None:
                events.append("sentinel")
                if sentinel_error is not None:
                    raise sentinel_error

            def _download_exports(_pod: str, _workdir: str, paths: list[str]) -> dict[str, bytes]:
                events.append("exports")
                return {path: path.encode() for path in paths}

            runner._wait_for_sentinel.side_effect = _sentinel
            if real_exports:
                self.pulled: list[str] = []

                def _tar_out(_pod: str, _workdir: str, rel_path: str) -> bytes | None:
                    self.pulled.append(rel_path)
                    return rel_path.encode()

                runner._exec_tar_out = MagicMock(side_effect=_tar_out)
            exports_patch = (
                patch.object(runner, "_signal_cleanup")
                if real_exports
                else patch.object(runner, "_download_exports", side_effect=_download_exports)
            )
            with (
                patch.object(runner, "_upload_payload", side_effect=lambda *a, **k: events.append("upload")),
                exports_patch,
                patch.object(
                    runner,
                    "_snapshot_workdir",
                    side_effect=lambda *a, **k: events.append("snapshot") or (snapshot or {}),
                ),
                patch.object(runner, "_delete_job", side_effect=lambda *a, **k: events.append("delete_job")),
            ):
                self.result = runner.run("print('x')", baseline_hashes={}, **run_kwargs)
        return events, runner_cls, channel_cls, channel

    def test_channel_started_before_upload_and_closed_before_downloads(self):
        events, runner_cls, channel_cls, channel = self._run(bridge=_bridge_at(".codemie_bridge/ex1"))

        assert events == [
            "start",
            "upload",
            "sentinel",
            "stop",
            "cleanup(kill=False)",
            "exports",
            "snapshot",
            "exports",
            "delete_job",
        ]
        runner_cls.assert_called_once_with(
            pod_name="the-pod",
            container_name="executor",
            namespace="test-ns",
            workdir="/workspace",
            kubeconfig_path=None,
        )
        channel_cls.assert_called_once_with(runner_cls.return_value, ".codemie_bridge/ex1")
        channel.sweep.assert_not_called()

    def test_no_bridge_never_builds_a_channel(self):
        events, runner_cls, channel_cls, _ = self._run()

        assert events == ["upload", "sentinel", "exports", "snapshot", "exports", "delete_job"]
        runner_cls.assert_not_called()
        channel_cls.assert_not_called()

    def test_sentinel_failure_stops_channel_once_before_job_deletion(self):
        with pytest.raises(ToolException, match="sentinel timed out"):
            self._run(bridge=_bridge_at(".codemie_bridge/ex1"), sentinel_error=ToolException("sentinel timed out"))

        assert self.events == ["start", "upload", "sentinel", "stop", "cleanup(kill=False)", "delete_job"]
        self.channel.stop.assert_called_once_with()
        assert self.channel.cleanup.call_args_list == [call(kill=False)]
        self.channel.sweep.assert_not_called()

    def test_happy_path_stops_and_cleans_up_channel_exactly_once(self):
        _, _, _, channel = self._run(bridge=_bridge_at(".codemie_bridge/ex1"))

        channel.stop.assert_called_once_with()
        assert channel.cleanup.call_args_list == [call(kill=False)]
        channel.sweep.assert_not_called()

    def test_bridge_files_never_reach_job_result(self):
        snapshot = {"a.txt": "h1", ".codemie_bridge/x/req.1.json": "h2"}

        self._run(bridge=_bridge_at(".codemie_bridge/x"), snapshot=snapshot, export_files=["out.txt"])

        assert self.result.changed_files == {"a.txt": b"a.txt"}
        assert self.result.exported_files == {"out.txt": b"out.txt"}

    def test_bridge_paths_never_reach_exported_files_when_cleanup_fails(self):
        snapshot = {"a.txt": "h1", ".codemie_bridge/x/req.1.json": "h2"}

        self._run(
            bridge=_bridge_at(".codemie_bridge/x"),
            snapshot=snapshot,
            cleanup_error=RuntimeError("cleanup failed"),
            real_exports=True,
            export_files=["out.txt", ".codemie_bridge/x/req.1.json", "./.codemie_bridge/x", "sub/../.codemie_bridge"],
        )

        assert self.result.exported_files == {"out.txt": b"out.txt"}
        assert self.result.changed_files == {"a.txt": b"a.txt"}
        assert self.pulled == ["out.txt", "a.txt", ".stderr"]
        self.channel.cleanup.assert_called_once_with(kill=False)
        assert self.events[-1] == "delete_job"

    def test_bridge_lookalike_export_is_still_pulled(self):
        self._run(bridge=_bridge_at(".codemie_bridge/x"), real_exports=True, export_files=[".codemie_bridge_other.txt"])

        assert self.result.exported_files == {".codemie_bridge_other.txt": b".codemie_bridge_other.txt"}

    def test_cleanup_failure_does_not_fail_the_run(self):
        self._run(bridge=_bridge_at(".codemie_bridge/x"), cleanup_error=RuntimeError("cleanup failed"))

        assert isinstance(self.result, JobResult)
        self.channel.cleanup.assert_called_once_with(kill=False)
        assert self.events[-1] == "delete_job"
