DONE

## Commit
**SHA**: 65938951a

## Files Created

1. **deploy-templates/templates/sync-cronjob.yaml**
   - Kubernetes CronJob Helm template for the Keycloak sync workload
   - Includes all required specifications:
     - `kind: CronJob` with configurable schedule (default: `*/10 * * * *`)
     - `concurrencyPolicy: Forbid` to prevent concurrent jobs
     - `backoffLimit: 2` for reliability
     - `restartPolicy: OnFailure`
     - Container image from `keycloakSync.image` values
     - Admin credentials mounted from Secret (not ConfigMap) using `valueFrom.secretKeyRef`
     - Environment variables for Keycloak URL, realm, dry-run mode, and batch size
     - Conditional rendering: only created if `keycloakSync.enabled=true`

2. **docs/operations/idp-managed-sync.md**
   - Operator guide covering three main sections:
     - **Enabling the feature**: Step-by-step guide to set `IDP_MANAGED_ENABLED=true`, provision Secret credentials, and configure Helm values
     - **Running a dry run first**: Manual CronJob trigger workflow with log review before enabling live writes
     - **Rollback**: Setting `IDP_MANAGED_ENABLED=false` with no database migration required
   - Includes troubleshooting section for common issues

## Quality Checks
- License check passed: `make license-check` ✓
- No Python files modified (no ruff run required)
- Both YAML and Markdown files verified syntactically
