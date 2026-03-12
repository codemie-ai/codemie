# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""
Performance patch for llm-sandbox Kubernetes file copy operations.

This module monkey-patches the slow copy_to_container method in llm_sandbox.kubernetes
to fix a critical performance issue where resp.update(timeout=1) is called before
every 4KB chunk write, causing 1-second delays per chunk.

Original issue: 2MB file takes 8+ minutes to copy (500 chunks × 1 second delay)
Patched: 2MB file takes ~1-2 seconds

The patch should be applied before creating any sandbox sessions.
"""

import io
import logging
import tarfile
from pathlib import Path
from typing import Any

from kubernetes.stream import stream

logger = logging.getLogger(__name__)

# Store original method for potential restoration
_original_copy_to_container = None


def _patched_copy_to_container(  # NOSONAR
    self: Any, container: Any, src: str, dest: str, **kwargs: Any
) -> None:
    """
    Patched version of KubernetesContainerAPI.copy_to_container with optimized streaming.

    This fixes the performance issue where resp.update(timeout=1) was called before
    every chunk write, causing massive delays for large files.

    Key changes:
    - Write all chunks without update() calls in the main loop
    - Only call update() for reading stderr after all data is written
    - Use larger 64KB chunks instead of 4KB for better throughput
    """
    # Validate source path exists and is accessible
    src_path = Path(src)
    if not (src_path.exists() and (src_path.is_file() or src_path.is_dir())):
        msg = f"Source path {src} does not exist or is not accessible"
        raise FileNotFoundError(msg)

    dest_dir = str(Path(dest).parent)
    container_name = kwargs.get("container_name")

    # Validate container name is provided
    if not container_name:
        msg = "Container name is required for Kubernetes operations but was None/empty"
        logger.error(msg)
        raise ValueError(msg)

    logger.debug(f"Copying to container '{container_name}' in pod '{container}'")

    # Create destination directory
    if dest_dir:
        exec_command = ["mkdir", "-p", dest_dir]
        resp = stream(
            self.client.connect_get_namespaced_pod_exec,
            container,
            self.namespace,
            command=exec_command,
            container=container_name,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False,
        )

        stderr_output = ""
        while resp.is_open():
            resp.update(timeout=1)
            if resp.peek_stderr():
                stderr_output += resp.read_stderr()

        if resp.returncode != 0:
            msg = f"Failed to create directory {dest_dir}: {stderr_output}"
            raise RuntimeError(msg)

    # Create tar archive
    tarstream = io.BytesIO()
    with tarfile.open(fileobj=tarstream, mode="w") as tar:  # NOSONAR
        tar.add(src, arcname=Path(dest).name)
    tarstream.seek(0)

    # Get total size for logging
    tar_size = len(tarstream.getvalue())
    tarstream.seek(0)

    exec_command = ["tar", "xf", "-", "-C", dest_dir]
    resp = stream(
        self.client.connect_get_namespaced_pod_exec,
        container,
        self.namespace,
        command=exec_command,
        container=container_name,
        stderr=True,
        stdin=True,
        stdout=True,
        tty=False,
        _preload_content=False,
    )

    # PERFORMANCE FIX: Write all data as fast as possible without update() calls
    chunk_size = 65536  # 64KB chunks for better performance
    bytes_written = 0

    try:
        while True:
            chunk = tarstream.read(chunk_size)
            if not chunk:
                break
            resp.write_stdin(chunk)
            bytes_written += len(chunk)

        # Signal end of input
        resp.write_stdin("")

        # Now wait for tar to finish processing
        # Only update/read stderr after all data is written
        stderr_output = ""
        for _ in range(10):  # Max 10 seconds wait
            resp.update(timeout=1)
            if resp.peek_stderr():
                stderr_output += resp.read_stderr()
            if not resp.is_open():
                break

    finally:
        resp.close()

    logger.debug(f"Copied {bytes_written} bytes ({tar_size} tar size) from '{src}' to pod '{container}:{dest}'")

    # Check for errors in stderr
    if stderr_output and "error" in stderr_output.lower():
        logger.warning(f"Tar extraction warnings for {dest}: {stderr_output}")


def apply_llm_sandbox_patch() -> None:
    """
    Apply the performance patch to llm-sandbox's Kubernetes backend.

    This should be called once at application startup before creating any sandbox sessions.

    Raises:
        ImportError: If llm-sandbox with Kubernetes support is not installed
    """
    global _original_copy_to_container

    try:
        from llm_sandbox.kubernetes import KubernetesContainerAPI
    except ImportError as e:
        msg = "llm-sandbox with Kubernetes support not installed. Install with: pip install 'llm-sandbox[k8s]'"
        raise ImportError(msg) from e

    # Store original method for potential restoration
    if _original_copy_to_container is None:
        _original_copy_to_container = KubernetesContainerAPI.copy_to_container

    # Apply patch
    KubernetesContainerAPI.copy_to_container = _patched_copy_to_container


def restore_original_copy_to_container() -> None:
    """
    Restore the original copy_to_container method.

    This is primarily for testing purposes.
    """
    global _original_copy_to_container

    if _original_copy_to_container is None:
        logger.warning("No original copy_to_container method stored, cannot restore")
        return

    try:
        from llm_sandbox.kubernetes import KubernetesContainerAPI

        KubernetesContainerAPI.copy_to_container = _original_copy_to_container
        logger.info("Restored original copy_to_container method")
    except ImportError:
        logger.warning("llm-sandbox not available, cannot restore method")
