# Code Executor Tool

Secure Python code execution tool with Kubernetes-based sandboxing and comprehensive configuration support.

## Overview

The Code Executor Tool provides a secure, isolated environment for executing Python code with resource limits, security policies, and complete isolation. It supports both local development and in-cluster deployment configurations.

## Features

- **Secure Execution**: Production-grade security policy for multi-tenant environments
- **File Upload & Export**: Upload files to sandbox and export generated files with optimized parallel transfer
- **Resource Management**: Configurable CPU and memory limits
- **Timeout Protection**: Automatic timeout for infinite loops and long-running operations
- **Session Management**: Persistent session pooling with health checks
- **Full Configuration**: Environment variables and programmatic configuration
- **Kubernetes Integration**: Support for both local and in-cluster Kubernetes configurations

## Configuration

All configuration is managed through environment variables. The tool automatically loads settings on initialization.

### Quick Configuration Reference

**Essential Settings:**
- `CODE_EXECUTOR_NAMESPACE` - Kubernetes namespace (default: `codemie-runtime`)
- `CODE_EXECUTOR_EXECUTION_TIMEOUT` - Code timeout in seconds (default: `30.0`)
- `CODE_EXECUTOR_MEMORY_LIMIT` - Pod memory limit (default: `128Mi`)

### Environment Variables

Complete configuration reference:

#### Kubernetes Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `CODE_EXECUTOR_NAMESPACE` | Kubernetes namespace for executor pods | `codemie-runtime` |
| `CODE_EXECUTOR_DOCKER_IMAGE` | Docker image for Python execution environment | `epamairun/codemie-python:2.2.9` |
| `CODE_EXECUTOR_MAX_POD_POOL_SIZE` | Maximum number of pods to create dynamically | `5` |
| `CODE_EXECUTOR_POD_NAME_PREFIX` | Prefix for dynamically created pod names | `codemie-executor-` |

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
| `CODE_EXECUTOR_MEMORY_LIMIT` | Memory limit for executor pods | `128Mi` |
| `CODE_EXECUTOR_MEMORY_REQUEST` | Memory request for executor pods | `128Mi` |
| `CODE_EXECUTOR_CPU_LIMIT` | CPU limit for executor pods | `1` |
| `CODE_EXECUTOR_CPU_REQUEST` | CPU request for executor pods | `500m` |

#### Security Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `CODE_EXECUTOR_RUN_AS_USER` | User ID for pod execution | `1001` |
| `CODE_EXECUTOR_RUN_AS_GROUP` | Group ID for pod execution | `1001` |
| `CODE_EXECUTOR_FS_GROUP` | Filesystem group ID for pod execution | `1001` |

#### Other Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `CODE_EXECUTOR_VERBOSE` | Enable verbose logging (`true`/`false`) | `true` |
| `CODE_EXECUTOR_SKIP_ENVIRONMENT_SETUP` | Skip environment setup in sandbox (`true`/`false`) | `false` |

## Usage

### Basic Usage

```python
from codemie_tools.data_management.code_executor import CodeExecutorTool

# Initialize tool (loads configuration from environment)
tool = CodeExecutorTool(
    file_repository=file_repo,
    user_id="user123"
)

# Execute code
result = tool.execute(code="print('Hello, World!')")
print(result)  # Output: Hello, World!
```

### With File Upload

```python
from codemie_tools.data_management.code_executor import CodeExecutorTool
from codemie_tools.base.file_object import FileObject

# Provide files to tool
files = [FileObject(name="data.csv", mime_type="text/csv", owner="user", content=...)]
tool = CodeExecutorTool(
    file_repository=file_repo,
    user_id="user123",
    input_files=files
)

# Code can access uploaded files
code = """
import pandas as pd
df = pd.read_csv('data.csv')
print(f"Loaded {len(df)} rows")
"""
result = tool.execute(code=code)
```

### With File Export

```python
# Generate and export files
tool = CodeExecutorTool(file_repository=repo, user_id="user")

code = """
import pandas as pd
df = pd.DataFrame({'x': [1, 2, 3], 'y': [4, 5, 6]})
df.to_csv('output.csv', index=False)
print('File created')
"""

result = tool.execute(code=code, export_files=["output.csv"])
# Returns: "File created\nFile 'output.csv': sandbox:<url>"
```

## Environment Setup Examples

### Local Development

```bash
# Configure settings for local development
export CODE_EXECUTOR_NAMESPACE=dev-runtime
export CODE_EXECUTOR_EXECUTION_TIMEOUT=60
export CODE_EXECUTOR_VERBOSE=true

# Run your application
python app.py
```

### Production (In-Cluster)

```bash
# Configure resource limits for production
export CODE_EXECUTOR_MEMORY_LIMIT=512Mi
export CODE_EXECUTOR_CPU_LIMIT=2
export CODE_EXECUTOR_EXECUTION_TIMEOUT=120

# Configure dynamic pod pool
export CODE_EXECUTOR_MAX_POD_POOL_SIZE=10
export CODE_EXECUTOR_POD_NAME_PREFIX=prod-executor-

# Run your application
python app.py
```

### Docker Compose

```yaml
services:
  app:
    image: your-app
    environment:
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
- Read-only root filesystem
- No privilege escalation
- All capabilities dropped
- Seccomp profile for system call restriction
- No host namespace access

### User Isolation

Each user gets an isolated working directory based on their sanitized user ID, preventing directory traversal attacks and ensuring data isolation.

## Pre-installed Libraries

The sandbox environment includes:

**Data manipulation and analysis:**
- pandas
- numpy

**Plotting and visualization:**
- matplotlib
- seaborn
- plotly

**Document processing:**
- openpyxl (Excel files)
- xlrd (Legacy Excel support)
- python-docx (Word documents)
- python-pptx (PowerPoint)
- PyPDF2 (PDF processing)
- markitdown (Convert various file formats to Markdown)
- pillow (Image processing)
- py7zr (7-Zip archive handling)

**Utilities:**
- tabulate (Pretty tables)

**Standard library** modules (os, sys, json, datetime, pathlib, etc.) are also available.

## File Operations

### File Upload

Files can be uploaded to the sandbox environment before code execution. Files are provided as `FileObject` instances and are automatically transferred to the sandbox's working directory.

**Features:**
- Files uploaded in parallel (up to 3 concurrent transfers) for optimal performance
- Binary mode transfer preserves file integrity (PPTX, DOCX, images, etc.)
- Original filenames preserved, including special characters
- Dynamic tool schema shows available files to LLM before code generation

**Example:**
```python
from codemie_tools.base.file_object import FileObject

# Files provided to constructor
files = [
    FileObject(name="[Video] Template.pptx", mime_type="...", owner="user", content=...),
    FileObject(name="data.csv", mime_type="text/csv", owner="user", content=...)
]
tool = CodeExecutorTool(file_repository=repo, user_id="user", input_files=files)

# Files are automatically available in code with exact names
code = """
from pptx import Presentation
prs = Presentation('[Video] Template.pptx')  # Exact filename required
"""
```

**Important:** When files have special characters (brackets, parentheses, spaces), use the exact filename as provided. The tool's input schema dynamically includes the file list so the LLM knows the correct names before generating code.

### File Export

Generated files can be exported from the sandbox after execution. The tool copies files from the sandbox to the host and stores them in the file repository.

**How it works:**
1. Code generates files in the working directory
2. Specified files are copied from sandbox using `session.copy_from_runtime()`
3. Files are stored in the repository with unique names
4. URLs are returned for accessing the exported files

**Example:**
```python
code = "import pandas as pd; df.to_csv('output.csv')"
export_files = ["output.csv"]
# Returns: "File 'output.csv': sandbox:<new_encoded_url>"
```

### File Format Support

The file upload/export feature supports all file types:
- **Data files**: CSV, Excel (XLS/XLSX), JSON, XML
- **Images**: PNG, JPG, SVG, etc.
- **Documents**: PDF, Word (DOCX), PowerPoint (PPTX)
- **Text files**: TXT, MD, code files
- **Any other binary or text files**

## Architecture

### Session Management

The tool uses a singleton `SandboxSessionManager` that:
- Maintains a pool of reusable sessions mapped to pod names
- Provides thread-safe access with per-pod locking
- Performs automatic health checks and session recreation
- Dynamically discovers and reuses existing pods
- Creates new pods on-demand up to `max_pod_pool_size`

### Pod Lifecycle

1. **Pod Discovery**: List all running pods with `app=codemie-executor` label
2. **Pod Reuse**: Connects to existing healthy pods when available
3. **Pod Creation**: Creates new pods only when needed and under max capacity
4. **Code Validation**: Security policy validation before execution
5. **Execution**: Code runs with timeout protection
6. **File Export**: Optional file export to repository
7. **Session Persistence**: Session kept alive for future requests

## Configuration Object

The `CodeExecutorConfig` class provides:

```python
class CodeExecutorConfig(CodeMieToolConfig):
    workdir_base: str = "/home/codemie"
    namespace: str = "codemie-runtime"
    docker_image: str = "epamairun/codemie-python:2.2.9"
    execution_timeout: float = 30.0
    session_timeout: float = 300.0
    default_timeout: float = 30.0
    memory_limit: str = "128Mi"
    memory_request: str = "128Mi"
    cpu_limit: str = "1"
    cpu_request: str = "500m"
    max_pod_pool_size: int = 5
    pod_name_prefix: str = "codemie-executor-"
    run_as_user: int = 1001
    run_as_group: int = 1001
    fs_group: int = 1001
    verbose: bool = True
    skip_environment_setup: bool = False
```

## Troubleshooting

### Timeout Errors

If you're getting timeout errors:
- Increase `CODE_EXECUTOR_EXECUTION_TIMEOUT` for longer-running code
- Check for infinite loops in your code
- Consider optimizing resource-intensive operations

### Memory Issues

If pods are running out of memory:
- Increase `CODE_EXECUTOR_MEMORY_LIMIT`
- Also increase `CODE_EXECUTOR_MEMORY_REQUEST` to ensure resources are available
- Review code for memory leaks or large data structures

### Pod Connection Issues

**Local Development:**
- Ensure kubectl is configured with correct context
- Verify namespace exists: `kubectl get ns <namespace>`

**In-Cluster Deployment:**
- Verify RBAC permissions for service account
- Check namespace access
- Ensure pods have correct labels

**Common Issues:**
- Check `CODE_EXECUTOR_MAX_POD_POOL_SIZE` if running many concurrent executions
- Verify `CODE_EXECUTOR_NAMESPACE` matches your cluster configuration

### Import Errors

- Only pre-installed libraries are available (see Libraries section)
- Standard library modules work out of the box
- External libraries not in the list will fail with ImportError

### Performance Issues

**Slow Execution:**
- Check `CODE_EXECUTOR_EXECUTION_TIMEOUT` setting
- Review code for inefficient operations
- Consider increasing resource limits (`CODE_EXECUTOR_MEMORY_LIMIT`, `CODE_EXECUTOR_CPU_LIMIT`)

**File Upload Performance:**
- Expected: 2MB file uploads in ~1-2 seconds
- Parallel uploads handle multiple files efficiently (up to 3 concurrent)
- Check network connectivity if uploads are consistently slow

## Contributing

When modifying the Code Executor:
1. Update configuration in `models.py`
2. Update tool logic in `code_executor_tool.py`
3. Run linting: `make ruff-fix`
4. Update this README with any new configuration options
5. Follow patterns in DEV_GUIDE.md

## References

- Main implementation: `code_executor_tool.py`
- Configuration models: `models.py`
- Security policies: `security_policies.py`
- Toolkit integration: `../file_system/toolkit.py`
