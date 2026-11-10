# Cloud Integrations

## Provider Packages

Use existing AWS, Azure, GCP, and Kubernetes tool packages for cloud operations.

| Avoid | Prefer |
|---|---|
| Creating a new cloud client path for a single feature | Extend the provider package under `src/codemie_tools/cloud/` |
| Mixing cloud provider code with API routers | Put behavior behind services/toolkits |

Evidence: cloud provider packages exist under `src/codemie_tools/cloud/aws`, `azure`, `gcp`, and `kubernetes`.

## Storage Repositories

Use repository implementations for file storage behavior.

| Avoid | Prefer |
|---|---|
| Service-level branches for S3/Azure/GCP file operations | `AWSFileRepository`, `AzureFileRepository`, or `GCPFileRepository` |
| Duplicated storage config parsing | Existing repository factory/config pattern |

Exception (hosted applications): the hosted adapter reuses only the client setup of `aws_file_repository.py` and `gcp_file_repository.py` (credentials and region from the files-storage settings). It does not use the `FileRepository` classes or their bucket layout. It uses its own dedicated bucket, `HOSTED_APPS_STORAGE_BUCKET`, and never creates buckets.

Evidence: storage repositories exist under `src/codemie/repository/aws_file_repository.py`, `azure_file_repository.py`, and `gcp_file_repository.py`.
