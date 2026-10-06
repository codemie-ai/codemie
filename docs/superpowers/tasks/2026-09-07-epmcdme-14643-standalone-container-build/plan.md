# Plan — EPMCDME-14643: Standalone container build tooling

Commit per task using the repository's existing convention.
No commit tasks are included here; user approves file list before committing.

---

## Acceptance criteria

- `codemie/.gitignore` ignores `standalone-build/ctx/` and `standalone-build/*.log`.
- `codemie/standalone-build/Build-image.ps1` exists; accepts `-BackendRoot`, `-FrontendRoot`, `-PackagingRoot` (all with `$PSScriptRoot`-relative defaults resolved in the script body), `-ImageTag` (mandatory, no default), `-Engine` (podman/docker, validated), `-NoCache` (switch); terminates on missing required inputs/files; warns and skips optional frontend items; fixes CRLF→LF on `entrypoint.sh` with UTF8NoBOM; tees build output to a timestamped log; runs `$Engine build [--no-cache] --format docker -t $ImageTag -f .../Dockerfile .../ctx/`; exits with the captured engine exit code.
- `codemie/standalone-build/README.md` covers prerequisites, usage examples (minimal + full), packaging-repo dependency, ES-free startup failure as a known limitation of specific snapshots — not listed as a deployment prerequisite and without instructions to start Elasticsearch.
- No source repo (`codemie-ui`, `codemie-standalone`) is written to or git-modified by the script.

---

## T1 — Patch `.gitignore`

**Test-first: no — trivial append; verified by `git status` showing no `ctx/` files after a build run.**

Append to `codemie/.gitignore` (after the `docs/superpowers/tasks/**/*.diff` block at line 70):

```
standalone-build/ctx/
standalone-build/*.log
```

---

## T2 — Create `codemie/standalone-build/Build-image.ps1`

**Test-first: no — build scripts validated operationally per analysis; the validation plan in the requirements (defaults, missing-input termination, path-with-spaces, engine validation, `--no-cache` placement, real build run) covers it.**

New file. Full script:

```powershell
#Requires -Version 7.0
[CmdletBinding()]
param(
    [string]$BackendRoot   = (Join-Path $PSScriptRoot ".."),
    [string]$FrontendRoot  = (Join-Path $PSScriptRoot "../../codemie-ui"),
    [string]$PackagingRoot = (Join-Path $PSScriptRoot "../../codemie-standalone"),
    [Parameter(Mandatory)][string]$ImageTag,
    [ValidateSet("podman","docker")][string]$Engine = "podman",
    [switch]$NoCache
)

$ErrorActionPreference = 'Stop'

# Resolve defaults to absolute paths in script body (not inline)
$BackendRoot   = (Resolve-Path $BackendRoot).Path
$FrontendRoot  = (Resolve-Path $FrontendRoot).Path
$PackagingRoot = (Resolve-Path $PackagingRoot).Path

# 1. Validate engine binary
if (-not (Get-Command $Engine -ErrorAction SilentlyContinue)) {
    throw "Engine '$Engine' not found on PATH. Install Podman or Docker and ensure it is in PATH."
}

# 2. Validate repo directories
foreach ($p in @($BackendRoot, $FrontendRoot, $PackagingRoot)) {
    if (-not (Test-Path $p -PathType Container)) { throw "Required directory not found: $p" }
}

# 3. Validate required source files
$required = @(
    (Join-Path $PackagingRoot "Dockerfile"),
    (Join-Path $PackagingRoot "docker/entrypoint.sh"),
    (Join-Path $PackagingRoot "docker/nginx.conf"),
    (Join-Path $BackendRoot   "pyproject.toml"),
    (Join-Path $BackendRoot   "poetry.lock"),
    (Join-Path $BackendRoot   "src"),
    (Join-Path $BackendRoot   "config"),
    (Join-Path $FrontendRoot  "src")
)
foreach ($p in $required) {
    if (-not (Test-Path $p)) { throw "Required source not found: $p" }
}

$optionalDirs  = @("public","scripts",".keycloakify") | ForEach-Object { Join-Path $FrontendRoot $_ }
$optionalFiles = @(
    "package.json","package-lock.json","index.html","index-keycloak.html",
    "vite.config.ts","tsconfig.json","tsconfig.node.json","postcss.config.js",
    "tailwind.config.ts","vitest-env-integration.ts","vitest.workspace.ts",
    ".eslintrc.cjs",".prettierrc.json"
) | ForEach-Object { Join-Path $FrontendRoot $_ }

# 4. Print git info for all three repos
foreach ($repo in @($BackendRoot, $FrontendRoot, $PackagingRoot)) {
    $branch = git -C "$repo" rev-parse --abbrev-ref HEAD 2>&1
    $sha    = git -C "$repo" rev-parse --short HEAD 2>&1
    $dirty  = (git -C "$repo" status --porcelain 2>&1 | Measure-Object -Line).Lines
    Write-Host "[$repo] branch=$branch sha=$sha dirty_lines=$dirty"
}

# 5. Validate and clean context dir (literal path, not from user input)
$ctxDir = Join-Path $PSScriptRoot "ctx"
if (Test-Path $ctxDir) {
    $item = Get-Item $ctxDir -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Context path '$ctxDir' is a reparse point or symlink. Aborting to prevent unintended deletion."
    }
    Remove-Item -Recurse -Force "$ctxDir"
}
$null = New-Item -ItemType Directory "$ctxDir"

# 6-7. Copy packaging assets
Copy-Item (Join-Path $PackagingRoot "Dockerfile")        (Join-Path $ctxDir "Dockerfile")
$null = New-Item -ItemType Directory (Join-Path $ctxDir "docker")
Copy-Item (Join-Path $PackagingRoot "docker/nginx.conf") (Join-Path $ctxDir "docker/nginx.conf")

# 8. Copy entrypoint.sh with CRLF→LF fix (UTF8NoBOM)
$epSrc  = Join-Path $PackagingRoot "docker/entrypoint.sh"
$epDst  = Join-Path $ctxDir "docker/entrypoint.sh"
$text   = [IO.File]::ReadAllText($epSrc).Replace("`r`n", "`n").Replace("`r", "`n")
$utf8nb = New-Object System.Text.UTF8Encoding $false
[IO.File]::WriteAllText($epDst, $text, $utf8nb)

# 9. Copy backend
$beCtx = Join-Path $ctxDir "backend"
$null  = New-Item -ItemType Directory $beCtx
Copy-Item (Join-Path $BackendRoot "pyproject.toml") "$beCtx/"
Copy-Item (Join-Path $BackendRoot "poetry.lock")    "$beCtx/"
Copy-Item (Join-Path $BackendRoot "src")    (Join-Path $beCtx "src")    -Recurse
Copy-Item (Join-Path $BackendRoot "config") (Join-Path $beCtx "config") -Recurse

# 10. Copy frontend: required src + optional dirs and root files
$feCtx = Join-Path $ctxDir "frontend"
$null  = New-Item -ItemType Directory $feCtx
Copy-Item (Join-Path $FrontendRoot "src") (Join-Path $feCtx "src") -Recurse

foreach ($p in $optionalDirs) {
    if (Test-Path $p -PathType Container) {
        Copy-Item "$p" (Join-Path $feCtx (Split-Path $p -Leaf)) -Recurse
    } else { Write-Warning "Optional dir not found, skipping: $p" }
}
foreach ($p in $optionalFiles) {
    if (Test-Path $p -PathType Leaf) {
        Copy-Item "$p" "$feCtx/"
    } else { Write-Warning "Optional file not found, skipping: $p" }
}

# 11. Open timestamped log
$logPath = Join-Path $PSScriptRoot ("build-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")

# 12. Run build (--no-cache immediately after 'build'; --format docker always)
$buildArgs = @("build")
if ($NoCache) { $buildArgs += "--no-cache" }
$buildArgs += "--format","docker","-t",$ImageTag,"-f",(Join-Path $ctxDir "Dockerfile"),"$ctxDir/"

Write-Host "Running: $Engine $($buildArgs -join ' ')"
& $Engine @buildArgs 2>&1 | Tee-Object -FilePath $logPath -Append
$buildExit = $LASTEXITCODE   # captured immediately; no statement between pipe and this line

# 13. Exit with engine exit code
exit $buildExit
```

---

## T3 — Create `codemie/standalone-build/README.md`

**Test-first: no — documentation file; correctness verified by reading.**

New file. Required sections:

1. **Prerequisites** — PowerShell 7+ (`pwsh`); Podman or Docker on PATH; three repos checked out as siblings under a common parent: `codemie/`, `codemie-ui/`, `codemie-standalone/`.
2. **Usage — minimal** (ImageTag required; all paths default from script location):
   ```powershell
   pwsh codemie/standalone-build/Build-image.ps1 -ImageTag localhost/codemie:local
   ```
3. **Usage — full parameter set**:
   ```powershell
   pwsh Build-image.ps1 `
     -BackendRoot   ../codemie `
     -FrontendRoot  ../codemie-ui `
     -PackagingRoot ../codemie-standalone `
     -ImageTag      localhost/codemie:local `
     -Engine        docker `
     -NoCache
   ```
4. **`-NoCache`** — omit for cached build; include to force a full rebuild.
5. **`-Engine docker`** — defaults to `podman`; pass `-Engine docker` for Docker Desktop.
6. **Packaging-repo dependency** — table listing files consumed read-only from `codemie-standalone`:

   | File | Purpose |
   |---|---|
   | `Dockerfile` | Three-stage container build |
   | `docker/entrypoint.sh` | Container startup script |
   | `docker/nginx.conf` | Reverse-proxy config |

7. **Known limitation: ES-free startup failure** — In tested snapshots (`codemie@e9539059e` + `codemie-ui@1bf0010d7`) the container exits at startup: `manage_preconfigured_assistants()` calls `es.indices.create()` unconditionally, raising `ConnectionRefusedError`. This is a known limitation of those snapshots, not an intended standalone deployment requirement. The image itself builds successfully; this failure occurs at runtime.
8. **macOS/Linux** — script uses PS7 cross-platform APIs (`[IO.File]::`, `Copy-Item`, `Join-Path`). Runtime behaviour on macOS/Linux is unverified; report issues if encountered.

---

## Negative-constraint pass

| Constraint | Honoring task(s) | Violation |
|---|---|---|
| Do not fetch, switch branches, or modify source repos | T2: copies files read-only; no `git write` ops | None |
| No automatic per-task commits | No commit task in plan; header note | None |
| Do not fix ES startup failure | T3 documents it as known limitation; T2 has no ES code | None |
| Do not describe ES as a prerequisite or instruct users to deploy it | T3 §7 states it is a known limitation of specific snapshots, not a deployment requirement | None |
| macOS/Linux: portable but unverified | T2 uses PS7 cross-platform APIs; T3 §8 notes unverified | None |
| No new dependencies | T2 uses only PS7 built-ins + `git` CLI | None |
| `--no-cache` after `build` subcommand, only when `-NoCache` set | T2 `$buildArgs = @("build"); if ($NoCache) { $buildArgs += "--no-cache" }` | None |
| `--format docker` always included | T2 always appends `"--format","docker"` | None |
| No `-ErrorAction SilentlyContinue` on `Remove-Item` | T2 omits it; surfaces cleanup errors | None |
| Symlink/reparse-point check before removal | T2: checks `[IO.FileAttributes]::ReparsePoint` before `Remove-Item` | None |
| `$LASTEXITCODE` captured immediately after build | T2: `$buildExit = $LASTEXITCODE` is the very next statement | None |
