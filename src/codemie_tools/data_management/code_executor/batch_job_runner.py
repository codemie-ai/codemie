# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Batch-Job-based sandbox runner.

Provides the `sandbox-jobs` mode: every code execution is submitted as a
Kubernetes `V1Job`. The container runs a small bash wrapper that waits for
an upload sentinel, runs the user script, signals completion, and waits for
an export pull before exiting with the script's exit code. The pod's main
process therefore exits naturally so `ttlSecondsAfterFinished` reaps the
Job, while `activeDeadlineSeconds` is the hard wall-clock kill switch.

A process-wide BoundedSemaphore caps concurrent Jobs at
config.max_pod_pool_size, matching the ephemeral pods path.
"""

import base64
import io
import json
import logging
import tarfile
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from uuid import uuid4

from kubernetes.client import V1DeleteOptions
from kubernetes.client.exceptions import ApiException
from kubernetes.stream import stream
from langchain_core.tools import ToolException

from codemie_tools.data_management.code_executor.k8s_client_manager import (
    KubernetesClientManager,
)
from codemie_tools.data_management.code_executor.models import CodeExecutorConfig

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 0.5
_DEADLINE_BUFFER_SECONDS = 60.0
_TTL_SECONDS_AFTER_FINISHED = 60
_SCRIPT_NAME = "script.py"
_READY_SENTINEL = ".ready"
_DONE_SENTINEL = ".done"
_PULLED_SENTINEL = ".pulled"
_EXIT_CODE_FILE = ".exit_code"

# Wrapper bash command:
#  1. Wait until the upload tarball has placed `.ready` in the workdir.
#  2. Run the user script.
#  3. Persist the exit code to a file (logs may be truncated; the file is the
#     authoritative signal for the parent to read if needed).
#  4. Touch `.done` so the parent knows it's safe to pull exports.
#  5. Wait for `.pulled` (parent signals exports are out).
#  6. Exit with the user script's exit code so K8s sees a clean termination.
_WRAPPER_SCRIPT = (
    "set -u; "
    "mkdir -p {workdir} && cd {workdir}; "
    "until [ -f {ready} ]; do sleep 0.1; done; "
    "python {script} 2>&1; "
    "echo $? > {exit_code}; "
    "touch {done}; "
    "until [ -f {pulled} ]; do sleep 0.1; done; "
    "exit $(cat {exit_code})"
)


@dataclass
class JobResult:
    """Duck-typed compatible with llm-sandbox `ConsoleOutput`.

    `exported_files` carries already-pulled bytes keyed by the requested
    relative path. The caller is responsible for persisting them via the
    file repository.
    """

    stdout: str
    stderr: str
    exit_code: int
    exported_files: Dict[str, bytes] = field(default_factory=dict)
    changed_files: Dict[str, bytes] = field(default_factory=dict)


class BatchJobRunner:
    """Process-wide singleton that submits one V1Job per execution.

    The semaphore caps concurrent Jobs at config.max_pod_pool_size. State
    is independent of SandboxSessionManager.
    """

    _instance: Optional["BatchJobRunner"] = None
    _instance_lock = threading.Lock()

    def __new__(cls, config: CodeExecutorConfig) -> "BatchJobRunner":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._init(config)
                    cls._instance = instance
        return cls._instance

    def _init(self, config: CodeExecutorConfig) -> None:
        self.config = config
        self.semaphore = threading.BoundedSemaphore(config.max_pod_pool_size)
        self.client_manager = KubernetesClientManager(config.kubeconfig_path)

    @property
    def max_capacity(self) -> int:
        return self.config.max_pod_pool_size

    def run(
        self,
        code: str,
        input_files: Optional[Dict[str, bytes]] = None,
        export_files: Optional[List[str]] = None,
        workdir: str = "/workspace",
        baseline_hashes: Optional[Dict[str, str]] = None,
    ) -> JobResult:
        """Submit a Job, upload inputs, wait, pull exports, return result.

        When `baseline_hashes` is provided, the workdir is snapshotted after
        the user script completes and any files whose sha256 differs from the
        baseline are pulled into `JobResult.changed_files`. This is what the
        workspace-script runner uses to populate `last_execution_files` so the
        agent_workspace service can sync writes back to the workspace.
        """
        self._acquire_slot()
        job_name = f"{self.config.pod_name_prefix}{uuid4().hex[:12]}"
        try:
            self._create_job(job_name, workdir)
            logger.info(
                f"Sandbox Job created: {job_name} "
                f"(capacity {self.max_capacity - self.semaphore._value}/{self.max_capacity})"
            )
            deadline = time.monotonic() + self.config.execution_timeout + _DEADLINE_BUFFER_SECONDS
            pod_name = self._wait_for_pod_running(job_name, deadline)
            self._upload_payload(pod_name, workdir, code, input_files or {})
            self._wait_for_sentinel(pod_name, f"{workdir}/{_DONE_SENTINEL}", deadline)
            exported = self._download_exports(pod_name, workdir, export_files or [])
            changed = (
                self._download_changed_files(pod_name, workdir, baseline_hashes) if baseline_hashes is not None else {}
            )
            self._signal_cleanup(pod_name, workdir)
            exit_code = self._wait_for_completion(job_name, deadline)
            logs = self._read_pod_logs(job_name)
            return JobResult(
                stdout=logs,
                stderr="",
                exit_code=exit_code,
                exported_files=exported,
                changed_files=changed,
            )
        finally:
            self._delete_job(job_name)
            self.semaphore.release()

    def _acquire_slot(self) -> None:
        timeout = self.config.default_timeout
        logger.debug(f"Acquiring Job slot (timeout={timeout}s)")
        if not self.semaphore.acquire(blocking=True, timeout=timeout):
            raise ToolException(
                f"Code executor is at capacity ({self.max_capacity}/{self.max_capacity} Jobs). " "Please retry."
            )

    def _build_manifest(self, job_name: str, workdir: str) -> dict:
        cfg = self.config
        active_deadline = int(cfg.execution_timeout) + int(_DEADLINE_BUFFER_SECONDS)
        wrapper = _WRAPPER_SCRIPT.format(
            workdir=workdir,
            script=_SCRIPT_NAME,
            ready=_READY_SENTINEL,
            done=_DONE_SENTINEL,
            pulled=_PULLED_SENTINEL,
            exit_code=_EXIT_CODE_FILE,
        )
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": job_name,
                "namespace": cfg.namespace,
                "labels": {"app": "codemie-executor", "component": "code-executor"},
            },
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": active_deadline,
                "ttlSecondsAfterFinished": _TTL_SECONDS_AFTER_FINISHED,
                "template": {
                    "metadata": {
                        "labels": {"app": "codemie-executor", "component": "code-executor"},
                    },
                    "spec": {
                        "restartPolicy": "Never",
                        "automountServiceAccountToken": False,
                        "hostNetwork": False,
                        "hostPID": False,
                        "hostIPC": False,
                        "securityContext": {
                            "runAsUser": cfg.run_as_user,
                            "runAsGroup": cfg.run_as_group,
                            "fsGroup": cfg.fs_group,
                            "runAsNonRoot": True,
                            "seccompProfile": {"type": "RuntimeDefault"},
                            "supplementalGroups": [],
                            "fsGroupChangePolicy": "OnRootMismatch",
                        },
                        "containers": [
                            {
                                "name": "executor",
                                "image": cfg.docker_image,
                                "command": ["bash", "-c", wrapper],
                                "securityContext": {
                                    "runAsUser": cfg.run_as_user,
                                    "runAsGroup": cfg.run_as_group,
                                    "runAsNonRoot": True,
                                    "allowPrivilegeEscalation": False,
                                    "capabilities": {"drop": ["ALL"]},
                                    "privileged": False,
                                    "readOnlyRootFilesystem": False,
                                    "seccompProfile": {"type": "RuntimeDefault"},
                                },
                                "resources": {
                                    "requests": {
                                        "cpu": cfg.cpu_request,
                                        "memory": cfg.memory_request,
                                    },
                                    "limits": {
                                        "cpu": cfg.cpu_limit,
                                        "memory": cfg.memory_limit,
                                    },
                                },
                            }
                        ],
                    },
                },
            },
        }

    def _create_job(self, job_name: str, workdir: str) -> None:
        batch = self.client_manager.get_batch_client()
        manifest = self._build_manifest(job_name, workdir)
        try:
            batch.create_namespaced_job(namespace=self.config.namespace, body=manifest)
        except ApiException as e:
            raise ToolException(f"Failed to create sandbox Job {job_name}: {e}") from e

    def _wait_for_pod_running(self, job_name: str, deadline: float) -> str:
        """Block until the Job's pod is Running, then return its name."""
        while time.monotonic() < deadline:
            pod = self._find_job_pod(job_name)
            if pod is not None and pod.status is not None:
                phase = getattr(pod.status, "phase", None)
                if phase == "Running":
                    return pod.metadata.name
                if phase in ("Failed", "Succeeded"):
                    raise ToolException(f"Sandbox Job {job_name} pod entered terminal phase '{phase}' before upload.")
            time.sleep(_POLL_INTERVAL_SECONDS)
        raise ToolException(
            f"Sandbox Job {job_name} pod did not reach Running within "
            f"{self.config.execution_timeout + _DEADLINE_BUFFER_SECONDS:.0f}s."
        )

    def _upload_payload(
        self,
        pod_name: str,
        workdir: str,
        code: str,
        input_files: Dict[str, bytes],
    ) -> None:
        """Stream a single tarball into the pod: script, input files, .ready last."""
        payload: Dict[str, bytes] = {**input_files, _SCRIPT_NAME: code.encode("utf-8")}
        # .ready must be the last entry so the wrapper only proceeds when
        # the rest of the payload is already on disk.
        payload[_READY_SENTINEL] = b""
        self._exec_tar_in(pod_name, workdir, payload)

    def _wait_for_sentinel(self, pod_name: str, path: str, deadline: float) -> None:
        while time.monotonic() < deadline:
            try:
                self._exec(pod_name, ["test", "-f", path])
                return
            except ToolException:
                time.sleep(_POLL_INTERVAL_SECONDS)
        raise ToolException(
            f"Sandbox Job script did not complete within "
            f"{self.config.execution_timeout + _DEADLINE_BUFFER_SECONDS:.0f}s "
            f"(sentinel {path} never appeared)."
        )

    def _download_exports(self, pod_name: str, workdir: str, paths: List[str]) -> Dict[str, bytes]:
        if not paths:
            return {}
        out: Dict[str, bytes] = {}
        for rel_path in paths:
            try:
                content = self._exec_tar_out(pod_name, workdir, rel_path)
                if content is not None:
                    out[rel_path] = content
            except Exception as e:
                logger.warning(f"Failed to export file {rel_path}: {e}")
        return out

    def _download_changed_files(self, pod_name: str, workdir: str, baseline_hashes: Dict[str, str]) -> Dict[str, bytes]:
        snapshot = self._snapshot_workdir(pod_name, workdir)
        changed_paths = [
            rel_path
            for rel_path, content_hash in sorted(snapshot.items())
            if baseline_hashes.get(rel_path) != content_hash
        ]
        return self._download_exports(pod_name, workdir, changed_paths)

    def _snapshot_workdir(self, pod_name: str, workdir: str) -> Dict[str, str]:
        snapshot_code = (
            "import hashlib, json, os, sys\n"
            f"root = {workdir!r}\n"
            "snapshot = {}\n"
            "for current_root, _, files in os.walk(root):\n"
            "    if '__pycache__' in current_root.split(os.sep):\n"
            "        continue\n"
            "    for name in files:\n"
            "        if name.startswith('.') and name in {'.ready', '.done', '.pulled', '.exit_code'}:\n"
            "            continue\n"
            "        path = os.path.join(current_root, name)\n"
            "        rel = os.path.relpath(path, root).replace(os.sep, '/')\n"
            "        if rel.endswith('.pyc'):\n"
            "            continue\n"
            "        with open(path, 'rb') as fh:\n"
            "            snapshot[rel] = hashlib.sha256(fh.read()).hexdigest()\n"
            "sys.stdout.write(json.dumps(snapshot, sort_keys=True))\n"
        )
        try:
            raw = self._exec(pod_name, ["python3", "-c", snapshot_code])
        except ToolException as e:
            logger.warning(f"Workdir snapshot failed for pod {pod_name}: {e}")
            return {}
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as e:
            logger.warning(f"Failed to parse workdir snapshot JSON: {e}")
            return {}

    def _signal_cleanup(self, pod_name: str, workdir: str) -> None:
        try:
            self._exec(pod_name, ["touch", f"{workdir}/{_PULLED_SENTINEL}"])
        except ToolException as e:
            # If we can't signal the pod, the activeDeadlineSeconds safety
            # net will eventually terminate it. Log and let _wait_for_completion
            # decide whether to time out.
            logger.warning(f"Failed to touch {_PULLED_SENTINEL} in pod {pod_name}: {e}")

    def _wait_for_completion(self, job_name: str, deadline: float) -> int:
        """Wait for the Job to reach a terminal state, sharing the run-level budget.

        Receives the same `deadline` threaded through `_wait_for_pod_running` and
        `_wait_for_sentinel` so the total Python-side wait is bounded by a single
        `execution_timeout + _DEADLINE_BUFFER_SECONDS` budget rather than 3x.
        """
        batch = self.client_manager.get_batch_client()
        while time.monotonic() < deadline:
            try:
                status = batch.read_namespaced_job_status(name=job_name, namespace=self.config.namespace).status
            except ApiException as e:
                raise ToolException(f"Failed to read Job status for {job_name}: {e}") from e

            if status is not None and self._is_terminal(status):
                return self._extract_exit_code(job_name)
            time.sleep(_POLL_INTERVAL_SECONDS)

        raise ToolException(
            f"Sandbox Job {job_name} did not complete within "
            f"{self.config.execution_timeout + _DEADLINE_BUFFER_SECONDS:.0f}s."
        )

    @staticmethod
    def _is_terminal(status) -> bool:
        if getattr(status, "succeeded", None):
            return True
        if getattr(status, "failed", None):
            return True
        for cond in getattr(status, "conditions", None) or []:
            if getattr(cond, "type", None) in ("Complete", "Failed") and getattr(cond, "status", None) == "True":
                return True
        return False

    def _extract_exit_code(self, job_name: str) -> int:
        pod = self._find_job_pod(job_name)
        if pod is None:
            logger.warning(f"No pod found for Job {job_name}; defaulting exit_code=1")
            return 1
        statuses = (pod.status.container_statuses if pod.status else None) or []
        for cs in statuses:
            term = getattr(cs.state, "terminated", None) if cs.state else None
            if term is not None:
                return int(getattr(term, "exit_code", 1) or 0)
        logger.warning(f"Pod for Job {job_name} has no terminated container; defaulting exit_code=1")
        return 1

    def _read_pod_logs(self, job_name: str) -> str:
        pod = self._find_job_pod(job_name)
        if pod is None:
            return ""
        core = self.client_manager.get_client()
        try:
            return (
                core.read_namespaced_pod_log(
                    name=pod.metadata.name,
                    namespace=self.config.namespace,
                    container="executor",
                )
                or ""
            )
        except ApiException as e:
            logger.warning(f"Failed to read logs for Job {job_name} pod {pod.metadata.name}: {e}")
            return ""

    def _find_job_pod(self, job_name: str):
        core = self.client_manager.get_client()
        try:
            pods = core.list_namespaced_pod(
                namespace=self.config.namespace,
                label_selector=f"job-name={job_name}",
            ).items
        except ApiException as e:
            logger.warning(f"Failed to list pods for Job {job_name}: {e}")
            return None
        return pods[0] if pods else None

    def _delete_job(self, job_name: str) -> None:
        try:
            batch = self.client_manager.get_batch_client()
            batch.delete_namespaced_job(
                name=job_name,
                namespace=self.config.namespace,
                body=V1DeleteOptions(
                    grace_period_seconds=0,
                    propagation_policy="Foreground",
                ),
            )
            logger.info(f"Sandbox Job deleted: {job_name}")
        except ApiException as e:
            if e.status == 404:
                logger.debug(f"Sandbox Job {job_name} already gone (404).")
                return
            logger.warning(f"Failed to delete sandbox Job {job_name}: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error deleting sandbox Job {job_name}: {e}")

    # ------------------------------------------------------------------ exec

    def _exec(self, pod_name: str, command: List[str], stdin: Optional[bytes] = None) -> bytes:
        """Run a command in the executor container via exec, return stdout bytes.

        Mirrors the proven write_stdin/EOF pattern used by `llm_sandbox._copy_to_container`
        so tar uploads terminate cleanly. Raises ToolException on non-zero exit.
        """
        core = self.client_manager.get_client()
        resp = stream(
            core.connect_get_namespaced_pod_exec,
            pod_name,
            self.config.namespace,
            command=command,
            container="executor",
            stderr=True,
            stdin=stdin is not None,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        stdout_chunks: List[bytes] = []
        stderr_chunks: List[bytes] = []
        try:
            if stdin is not None:
                # Write in 64KB chunks then signal EOF — this is the only
                # reliable way to terminate stdin via kubernetes-python.
                view = memoryview(stdin)
                chunk_size = 65536
                for offset in range(0, len(view), chunk_size):
                    resp.write_stdin(bytes(view[offset : offset + chunk_size]))
                resp.write_stdin("")

            # Drain until the server closes the channel
            while resp.is_open():
                resp.update(timeout=1)
                if resp.peek_stdout():
                    stdout_chunks.append(resp.read_stdout().encode("utf-8", errors="replace"))
                if resp.peek_stderr():
                    stderr_chunks.append(resp.read_stderr().encode("utf-8", errors="replace"))
            rc = resp.returncode
        finally:
            resp.close()
        if rc not in (0, None):
            stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
            raise ToolException(f"exec {command!r} in pod {pod_name} failed (rc={rc}): {stderr_text.strip()}")
        return b"".join(stdout_chunks)

    def _exec_tar_in(self, pod_name: str, workdir: str, files: Dict[str, bytes]) -> None:
        """Push files into the pod via `tar xf - -C <workdir>`.

        `mkdir -p` creates any missing user-id subdirectory under the
        image-provided workdir base (e.g. /home/codemie/<user>). The base
        itself is owned by runAsUser=1001 in the image.
        """
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for rel_path, content in files.items():
                info = tarfile.TarInfo(name=rel_path)
                info.size = len(content)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(content))
        self._exec(
            pod_name,
            ["sh", "-c", 'mkdir -p "$1" && tar xf - -C "$1"', "sh", workdir],
            stdin=buf.getvalue(),
        )

    def _exec_tar_out(self, pod_name: str, workdir: str, rel_path: str) -> Optional[bytes]:
        """Pull a single file out of the pod via `tar c <rel_path> | base64`.

        Base64 is needed because the kubernetes-python WSClient decodes stdout
        as UTF-8 (errors="replace"), which would corrupt arbitrary binary tar
        bytes. We pipe tar through `base64 -w0` in the container and decode on
        the host.
        """
        raw = self._exec(
            pod_name,
            ["sh", "-c", 'tar cf - -C "$1" "$2" | base64 -w0', "sh", workdir, rel_path],
        )
        if not raw:
            return None
        try:
            tar_bytes = base64.b64decode(raw.strip())
        except Exception as e:
            logger.warning(f"Failed to base64-decode tar output for {rel_path}: {e}")
            return None
        buf = io.BytesIO(tar_bytes)
        with tarfile.open(fileobj=buf, mode="r") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                f = tar.extractfile(member)
                if f is not None:
                    return f.read()
        return None


def run_via_jobs(
    config: CodeExecutorConfig,
    code: str,
    input_bytes: Dict[str, bytes],
    export_files: Optional[List[str]],
    workdir: str,
    format_result: Callable[[JobResult], str],
    store_exports: Callable[[Dict[str, bytes]], List[str]],
    log_timing: Callable[[float, float], None],
) -> str:
    """Run a single execution via BatchJobRunner and format the user-facing result.

    Shared chokepoint for both code_executor and execute_workspace_script JOBS-mode
    dispatch, so result formatting and export persistence stay in one place.
    """
    start = time.time()
    result = BatchJobRunner(config).run(
        code,
        input_files=input_bytes,
        export_files=export_files,
        workdir=workdir,
    )
    log_timing(0.0, time.time() - start)
    text = format_result(result)
    urls = store_exports(result.exported_files)
    if urls:
        text += ", ".join(urls)
    return text
