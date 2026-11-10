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

"""Whether chat Code Executor and the workspace script runner can run (standalone prerequisites)."""

from codemie.configs.logger import logger


def code_executor_available(cfg) -> bool:
    """Outside standalone this is exactly the ``dynamicCodeInterpreter`` flag; in standalone it also needs
    the operator switch and a configured cluster connection (no reachability check)."""
    from codemie.configs.customer_config import customer_config  # noqa: PLC0415
    from codemie_tools.data_management.code_executor.k8s_client_manager import (  # noqa: PLC0415
        cluster_connection_configured,
    )
    from codemie_tools.data_management.file_system.toolkit import FileSystemToolkit  # noqa: PLC0415

    flag = customer_config.configured_feature_enabled("dynamicCodeInterpreter")
    if not cfg.STANDALONE:
        return flag
    return flag and FileSystemToolkit._is_code_executor_enabled() and cluster_connection_configured()


def warn_code_executor_unavailable() -> None:
    from codemie.configs.config import config  # noqa: PLC0415
    from codemie.configs.customer_config import customer_config  # noqa: PLC0415
    from codemie_tools.data_management.code_executor.k8s_client_manager import (  # noqa: PLC0415
        cluster_connection_configured,
    )
    from codemie_tools.data_management.file_system.toolkit import FileSystemToolkit  # noqa: PLC0415

    if code_executor_available(config):
        return
    missing = [
        text
        for ok, text in (
            (customer_config.configured_feature_enabled("dynamicCodeInterpreter"), "feature flag off"),
            (FileSystemToolkit._is_code_executor_enabled(), "operator not enabled (CODE_EXECUTOR_ENABLED)"),
            (
                cluster_connection_configured(),
                "no cluster connection (CODE_EXECUTOR_KUBECONFIG_PATH or in-cluster environment)",
            ),
        )
        if not ok
    ]
    logger.warning(
        f"Standalone: chat Code Executor and the workspace script runner are unavailable: {'; '.join(missing)}."
    )
