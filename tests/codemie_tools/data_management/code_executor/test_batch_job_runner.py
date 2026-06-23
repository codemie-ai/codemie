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
from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.exceptions import ApiException
from langchain_core.tools import ToolException

from codemie_tools.data_management.code_executor.batch_job_runner import (
    BatchJobRunner,
    JobResult,
)
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


def _patch_runner_internals(runner: BatchJobRunner, pod_name: str = "sbx-pod-xyz"):
    """Stub the exec helpers so high-level tests don't have to mock stream()."""
    runner._wait_for_pod_running = MagicMock(return_value=pod_name)
    runner._wait_for_sentinel = MagicMock(return_value=None)
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
        # Wrapper command, not raw python -c
        assert container["command"][0] == "bash"
        assert container["command"][1] == "-c"
        assert ".ready" in container["command"][2]
        assert ".pulled" in container["command"][2]
        sec = manifest["spec"]["template"]["spec"]["securityContext"]
        assert sec["runAsUser"] == 1001 and sec["runAsGroup"] == 1001 and sec["fsGroup"] == 1001

        # Job deleted in finally with grace_period_seconds=0
        batch.delete_namespaced_job.assert_called_once()
        del_kwargs = batch.delete_namespaced_job.call_args.kwargs
        assert del_kwargs["body"].grace_period_seconds == 0
        assert del_kwargs["body"].propagation_policy == "Foreground"

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
