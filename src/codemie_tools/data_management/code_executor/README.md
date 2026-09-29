# Code Executor Tool

Secure Python code execution tool with Kubernetes-based sandboxing and comprehensive configuration support.

## Overview

The Code Executor Tool provides a secure, isolated sandbox environment for executing Python code with resource limits, security policies, and complete isolation. It supports Kubernetes-backed sandbox execution only.

## Features

- **Secure Execution**: Production-grade security policy for multi-tenant environments
- **File Upload & Export**: Upload files to sandbox and export generated files with optimized parallel transfer
- **Resource Management**: Configurable CPU and memory limits
- **Timeout Protection**: Automatic timeout for infinite loops and long-running operations
- **Session Management**: Persistent session pooling with health checks
- **Full Configuration**: Environment variables and programmatic configuration
- **Kubernetes Integration**: Supports shared-pod and jobs-based sandbox execution modes

## Execution Mode

Code runs in an isolated Kubernetes sandbox pod. `CODE_EXECUTOR_EXECUTION_MODE`
defaults to `sandbox`, which is the only accepted value, so it does not need to
be set explicitly.

## Configuration

All configuration is managed through environment variables. The tool automatically loads settings on initialization.

### Quick Configuration Reference

**Essential Settings:**
- `CODE_EXECUTOR_EXECUTION_MODE` - Execution mode (default: `sandbox`)
- `CODE_EXECUTOR_SECURITY_THRESHOLD` - Security policy threshold (default: `LOW`)
- `CODE_EXECUTOR_NAMESPACE` - Kubernetes namespace (default: `codemie-code-executor`)
- `CODE_EXECUTOR_EXECUTION_TIMEOUT` - Code timeout in seconds (default: `30.0`)
- `CODE_EXECUTOR_MEMORY_LIMIT` - Pod memory limit (default: `256Mi`)

### Environment Variables

#### Required Execution And Security Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `CODE_EXECUTOR_EXECUTION_MODE` | Execution mode. Code runs in an isolated Kubernetes sandbox pod. | `sandbox` |
| `CODE_EXECUTOR_SECURITY_THRESHOLD` | Security policy: `SAFE`, `LOW`, `MEDIUM`, `HIGH` | `LOW` |

#### Kubernetes Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `CODE_EXECUTOR_NAMESPACE` | Kubernetes namespace for executor pods | `codemie-code-executor` |
| `CODE_EXECUTOR_DOCKER_IMAGE` | Docker image for Python execution environment | `codemie/codemie-python:2.52.0` |
| `CODE_EXECUTOR_MAX_POD_POOL_SIZE` | Maximum number of pods to create dynamically | `5` |
| `CODE_EXECUTOR_POD_NAME_PREFIX` | Prefix for dynamically created pod names | `codemie-executor-` |
| `CODE_EXECUTOR_SANDBOX_MODE` | Sandbox mode: `sandbox-shared` or `sandbox-jobs` | `sandbox-jobs` |

#### Working Directory

| Variable | Description | Default |
|----------|-------------|---------|
| `CODE_EXECUTOR_WORKDIR_BASE` | Base working directory for code execution | `/home/codemie` |

#### Timeout Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `CODE_EXECUTOR_EXECUTION_TIMEOUT` | Code execution timeout in seconds (protects against infinite loops) | `30.0` |
| `CODE_EXECUTOR_SESSION_TIMEOUT` | Session lifetime in seconds | `300.0` |
| `CODE_EXECUTOR_DEFAULT_TIMEOUT` | Default operation timeout in seconds | `30.0` |

#### Resource Limits

| Variable | Description | Default |
|----------|-------------|---------|
| `CODE_EXECUTOR_MEMORY_LIMIT` | Memory limit for executor pods | `256Mi` |
| `CODE_EXECUTOR_MEMORY_REQUEST` | Memory request for executor pods | `256Mi` |
| `CODE_EXECUTOR_CPU_LIMIT` | CPU limit for executor pods | `1` |
| `CODE_EXECUTOR_CPU_REQUEST` | CPU request for executor pods | `500m` |
| `CODE_EXECUTOR_MAX_THREADS` | Maximum number of threads allowed per execution (enforced inside the sandbox process, both modes) | `64` |
| `CODE_EXECUTOR_MAX_OPEN_FILES` | Maximum number of open files allowed per execution (enforced inside the sandbox process, both modes); the Python runtime opens roughly 20 files before user code runs | `256` |

#### Pod Security Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `CODE_EXECUTOR_RUN_AS_USER` | User ID for pod execution | `1001` |
| `CODE_EXECUTOR_RUN_AS_GROUP` | Group ID for pod execution | `1001` |
| `CODE_EXECUTOR_FS_GROUP` | Filesystem group ID for pod execution | `1001` |

#### Other Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `CODE_EXECUTOR_VERBOSE` | Enable verbose logging (`true`/`false`) | `false` |
| `CODE_EXECUTOR_KEEP_TEMPLATE` | Persist template after code execution | `true` |
| `CODE_EXECUTOR_SKIP_ENVIRONMENT_SETUP` | Skip environment setup in sandbox (`true`/`false`) | `false` |
| `CODE_EXECUTOR_YAML_POLICY_PATH` | Optional path to custom YAML policy file | `""` |
| `CODE_EXECUTOR_KUBECONFIG_PATH` | Optional kubeconfig path | `""` |

## Usage

### Basic Usage

```python
from codemie_tools.data_management.code_executor import CodeExecutorTool

tool = CodeExecutorTool(
    file_repository=file_repo,
    user_id="user123"
)

result = tool.execute(code="print('Hello, World!')")
print(result)
```

### With File Upload

```python
from codemie_tools.data_management.code_executor import CodeExecutorTool
from codemie_tools.base.file_object import FileObject

files = [FileObject(name="data.csv", mime_type="text/csv", owner="user", content=...)]
tool = CodeExecutorTool(
    file_repository=file_repo,
    user_id="user123",
    input_files=files
)

code = """
import pandas as pd
df = pd.read_csv('data.csv')
print(f"Loaded {len(df)} rows")
"""
result = tool.execute(code=code)
```

### With File Export

```python
tool = CodeExecutorTool(file_repository=repo, user_id="user")

code = """
import pandas as pd
df = pd.DataFrame({'x': [1, 2, 3], 'y': [4, 5, 6]})
df.to_csv('output.csv', index=False)
print('File created')
"""

result = tool.execute(code=code, export_files=["output.csv"])
```

## Environment Setup Examples

### Local Development Against Sandbox Infrastructure

```bash
export CODE_EXECUTOR_EXECUTION_MODE=sandbox
export CODE_EXECUTOR_SECURITY_THRESHOLD=LOW
export CODE_EXECUTOR_NAMESPACE=dev-runtime
export CODE_EXECUTOR_EXECUTION_TIMEOUT=60
export CODE_EXECUTOR_VERBOSE=true

python app.py
```

### Production (In-Cluster)

```bash
export CODE_EXECUTOR_EXECUTION_MODE=sandbox
export CODE_EXECUTOR_SECURITY_THRESHOLD=LOW
export CODE_EXECUTOR_MEMORY_LIMIT=512Mi
export CODE_EXECUTOR_CPU_LIMIT=2
export CODE_EXECUTOR_EXECUTION_TIMEOUT=120
export CODE_EXECUTOR_MAX_POD_POOL_SIZE=10
export CODE_EXECUTOR_POD_NAME_PREFIX=prod-executor-

python app.py
```

### Docker Compose

```yaml
services:
  app:
    image: your-app
    environment:
      - CODE_EXECUTOR_EXECUTION_MODE=sandbox
      - CODE_EXECUTOR_SECURITY_THRESHOLD=LOW
      - CODE_EXECUTOR_NAMESPACE=docker-runtime
      - CODE_EXECUTOR_EXECUTION_TIMEOUT=45
      - CODE_EXECUTOR_MEMORY_LIMIT=256Mi
```

### Kubernetes Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: codemie-app
spec:
  template:
    spec:
      containers:
      - name: app
        image: your-app
        env:
        - name: CODE_EXECUTOR_EXECUTION_MODE
          value: "sandbox"
        - name: CODE_EXECUTOR_SECURITY_THRESHOLD
          value: "LOW"
        - name: CODE_EXECUTOR_NAMESPACE
          value: "production-runtime"
        - name: CODE_EXECUTOR_EXECUTION_TIMEOUT
          value: "120"
        - name: CODE_EXECUTOR_MEMORY_LIMIT
          value: "512Mi"
        - name: CODE_EXECUTOR_CPU_LIMIT
          value: "2"
        - name: CODE_EXECUTOR_MAX_POD_POOL_SIZE
          value: "10"
        - name: CODE_EXECUTOR_POD_NAME_PREFIX
          value: "prod-exec-"
```

## Security

### Security Policy

The tool implements a production-grade security policy that blocks:
- System operations (os, subprocess, sys manipulation)
- File system operations (shutil, pathlib, glob, tempfile)
- Network operations (socket, urllib, requests, httpx)
- Process/thread manipulation (threading, multiprocessing)
- Code evaluation/compilation (eval, exec, compile)
- Inspection/introspection modules (inspect, importlib)

### Pod Security

Executor pods are configured with:
- Non-root user execution
- Read-only root filesystem, with explicit `emptyDir` mounts for the
  workdir/tmp/cache paths the sandboxed code and its dependencies need
- No privilege escalation
- All capabilities dropped
- Seccomp profile (`RuntimeDefault`) for system call restriction
- No host namespace access

The in-process `FilesystemAccessDenied` guard (`filesystem_policy.py`) is
defense-in-depth only — it does not intercept syscalls made directly by
native/C-extension code. The kernel-level controls above, and gVisor in
JOBS mode, are the actual security boundary against native code.

### User Isolation

Each user gets an isolated working directory based on their sanitized user ID, preventing directory traversal attacks and ensuring data isolation.
