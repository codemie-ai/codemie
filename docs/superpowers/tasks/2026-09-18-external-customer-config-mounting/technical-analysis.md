# Technical Analysis: External customer configuration for standalone

**Task:** EPMCDME-14347 external `customer-config.yaml` mounting

**Repository:** `codemie`

**Scope:** standalone deployment assets and documentation

## Objective

Allow operators to replace the `customer-config.yaml` bundled in the standalone image with a
user-supplied, version-matched full YAML file. The external file must be mounted read-only at
`/app/backend/config/customer/customer-config.yaml`, with no YAML merge, overlay, or URL download.
Bundled defaults must remain the zero-configuration behavior.

## Existing behavior and integration point

`src/codemie/configs/config.py` resolves `CUSTOMER_CONFIG_DIR` to `config/customer` under the
backend root. In the standalone image that root is `/app/backend`, because the Dockerfile copies
`config/` to `/app/backend/config`. Consequently, the backend already reads the exact mount target:

```text
/app/backend/config/customer/customer-config.yaml
```

`CustomerConfig` loads the file once during process initialization. Replacing the mounted file
therefore requires a container restart or recreation. The loader consumes one complete YAML file;
it does not combine an external document with the file bundled in the image.

The existing generic `FEATURE_<ID>` environment override remains unchanged. In particular, an
explicitly set `FEATURE_CLI_ANALYTICS` can still override `features:cliAnalytics.enabled` after the
YAML has been loaded, even though that variable is no longer advertised in the standalone env
example as the normal enablement mechanism.

## Final implementation

### Product files

- `standalone/docker-compose.external-config.yml` is a small Compose option layered on either base
  stack. It adds one read-only bind mount to the `codemie` service and uses Compose's required
  variable expression to fail clearly when `CODEMIE_EXTERNAL_CONFIG` is unset.
- `standalone/docker-compose.standalone.yml` and
  `standalone/docker-compose.standalone.analytics.yml` retain their bundled-default behavior and
  point users to the external-config file instead of requiring edits to the base files.
- `standalone/.env.standalone.example` removes the advertised `FEATURE_CLI_ANALYTICS` toggle. It
  keeps the ClickHouse and OTel connection values needed by the analytics stack, because the
  backend defaults (`localhost`, `otelcol`, user `default`) do not match that stack's service names
  and credentials.
- `standalone/README.md` documents bundled and external modes for Docker and Podman, the canonical
  YAML source, full-replacement semantics, read-only behavior, and restart/recreation requirements.

### Canonical configuration source

No second customer-config example is added to the final branch. Operators copy the complete
`config/customer/customer-config.yaml` from the same source revision used to build the image and
edit only the intended values. This avoids maintaining a second full copy and prevents a partial
example from being mistaken for a merge overlay.

The bundled canonical file keeps `features:cliAnalytics` disabled. To enable CLI Analytics for an
external deployment, the operator changes that component in the copied YAML and starts the
analytics Compose stack with the external-config file layered on top.

## Compose usage

Bundled defaults:

```bash
docker compose -f standalone/docker-compose.standalone.yml up -d
```

External configuration:

```bash
export CODEMIE_EXTERNAL_CONFIG=/absolute/path/customer-config.yaml
docker compose \
  -f standalone/docker-compose.standalone.yml \
  -f standalone/docker-compose.external-config.yml \
  up -d
```

The same external-config file is layered on
`standalone/docker-compose.standalone.analytics.yml` for the CLI Analytics stack. Podman uses the
same file combination through `podman compose`.

## Validation evidence

The implementation run recorded the following checks:

- minimal stack Compose validation without the external-config file;
- minimal stack validation with the external-config file and an absolute source path;
- analytics stack validation in both bundled and external modes;
- fail-fast behavior when `CODEMIE_EXTERNAL_CONFIG` is absent;
- a local startup smoke test using the bundled configuration;
- a local startup smoke test with the external file mounted at the expected target;
- confirmation that the bind mount is read-only;
- cleanup of the smoke-test containers and network.

No full repository test suite is required for this packaging-only change. The application loader,
feature override mechanism, and canonical bundled configuration are not modified.

## Risks and mitigations

- **Wrong source revision:** the external YAML may drift from the image. Documentation directs the
  operator to the canonical file from the matching repository revision.
- **Partial YAML mistaken for an overlay:** omitted components are not inherited. Documentation
  repeatedly describes full replacement and no partial example is shipped.
- **Unset or relative host path:** Compose fails immediately when the variable is unset; the README
  requires an absolute path to avoid engine- and shell-dependent resolution.
- **Stale running configuration:** the config is loaded once. Documentation requires restart or
  recreation after edits.
- **CLI Analytics connectivity:** the standalone env example supplies the service names and
  credentials matching the analytics Compose stack.
- **Generic feature override surprise:** README notes that an explicitly set `FEATURE_CLI_ANALYTICS`
  still takes precedence, because the generic backend override mechanism is deliberately retained.

## Final scope

The product diff is limited to five standalone files: four modifications and one new Compose file.
No backend application code, canonical customer config, ignored local `.env.standalone`, UI code,
or SDK code is changed by this subtask.
