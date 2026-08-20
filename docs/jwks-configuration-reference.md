# JWKS Signature Validation — Configuration Reference

This document is the operator reference for configuring JWT authentication in Codemie. It covers both modes: with and without JWKS cryptographic signature verification.

For end-to-end examples of both modes, see the integration tests under
`tests/codemie/rest_api/security/jwks/`.

---

## Overview

Codemie authenticates inbound requests via a pluggable IDP layer selected by `IDP_PROVIDER`. By default (`IDP_PROVIDER=local`, `JWKS_VALIDATION_ENABLED=false`) no external token verification occurs and no JWKS infrastructure is needed.

The JWKS extension (`JWKS_VALIDATION_ENABLED=true`) adds an opt-in cryptographic layer that verifies the RS256 signature of every inbound JWT against the issuer's public keys before any IDP claim extraction takes place. This is a defence-in-depth measure for deployments where Codemie is directly exposed to external tokens rather than sitting behind a trusted ingress proxy.

---

## IDP Provider Selection

Set `IDP_PROVIDER` to one of the following values:

| `IDP_PROVIDER` | Enterprise pkg | Token read from | When to use |
|---|---|---|---|
| `local` | No | `User-ID` header or local JWT cookie | Development, no external IdP |
| `oidc` | Yes | `Authorization: Bearer` header | OIDC IdP |
| `keycloak` | Yes | `x-auth-request-access-token` header | Keycloak |
| `entraid-oidc` | Yes | `Authorization: Bearer` header | Microsoft Entra ID (Azure AD) |

Signature verification is **not** a separate provider. It is an orthogonal layer
toggled by `JWKS_VALIDATION_ENABLED` (see below) that transparently wraps whichever
non-`local` provider you selected.

> **How JWKS wrapping is applied.** At factory time, when `JWKS_VALIDATION_ENABLED=true`
> and the selected provider is **not** `local`, Codemie wraps the provider in a
> `JwksValidatingIdp` decorator that verifies the RS256 signature before the inner
> provider extracts claims. There is no `jwks-oidc` / `jwks-keycloak` provider value —
> keep `IDP_PROVIDER=oidc` (or `keycloak`) and flip `JWKS_VALIDATION_ENABLED=true`.
> The flag has no effect on `IDP_PROVIDER=local`.

### No-signature mode (`oidc` / `keycloak` without JWKS)

When JWKS is disabled, the IDP providers trust the incoming JWT without verifying its signature:

- **`oidc`** — base64url-decodes the JWT payload and extracts claims (`sub`, `email`, `firstname`, `lastname`, `groups`) directly. No network call to the IdP.
- **`keycloak`** — calls the Keycloak userinfo endpoint using the bearer token. Keycloak itself validates the token, so signature integrity is verified by Keycloak, not by Codemie.

This is safe when Codemie is behind a reverse proxy or API gateway (e.g. OAuth2 Proxy, NGINX, Istio) that has already validated the JWT before forwarding the request. If Codemie receives tokens directly from untrusted clients, enable JWKS validation.

---

## Environment Variables

### IDP selection

| Variable | Type | Default | Description |
|---|---|---|---|
| `IDP_PROVIDER` | string | `local` | Active IDP. One of: `local`, `oidc`, `keycloak`, `entraid-oidc` |

### JWKS validation

| Variable | Type | Default | Required when | Description |
|---|---|---|---|---|
| `JWKS_VALIDATION_ENABLED` | bool | `false` | signature verification wanted | Master switch. Set to `true` to enable RS256 signature verification, transparently wrapping the selected non-`local` provider. |
| `JWKS_TRUSTED_ISSUERS` | JSON string | `""` | JWKS enabled | JSON list of trusted issuer configs (see schema below). Must not be empty when enabled. |
| `JWKS_CACHE_TTL_SECONDS` | int | `300` | JWKS enabled | Seconds JWKS keys are cached. After expiry the client re-fetches. Lower values pick up key rotation faster at the cost of more JWKS requests. |
| `JWKS_HTTP_TIMEOUT_SECONDS` | float | `3.0` | JWKS enabled | HTTP timeout (seconds) for requests to JWKS endpoints. |
| `JWKS_LEEWAY_SECONDS` | int | `30` | JWKS enabled | Clock skew tolerance for `exp` / `iat` claim verification. Allows for minor drift between issuer and Codemie clocks. |

### `JWKS_TRUSTED_ISSUERS` schema

Each entry in the JSON list:

```json
[
  {
    "issuer": "https://auth.example.com",
    "audience": "codemie-platform",
    "jwks_uri": "https://auth.example.com/.well-known/jwks.json",
    "required_claims": ["iss", "sub", "email", "groups", "aud", "exp", "iat"]
  }
]
```

| Field | Required | Description |
|---|---|---|
| `issuer` | Yes | Must match the `iss` claim in incoming JWTs exactly (string equality). |
| `audience` | Yes | Must match the `aud` claim in incoming JWTs. |
| `jwks_uri` | One of | Direct URL to the JWKS JSON endpoint. |
| `discovery_url` | One of | OIDC discovery URL (`.well-known/openid-configuration`). The JWKS URI is resolved from the discovery document once at startup. |
| `required_claims` | No | Claims that must be present in the JWT. Defaults to `["iss","sub","email","groups","aud","exp","iat"]`. Override per-issuer if your IdP omits `groups` (e.g. `["iss","sub","email","aud","exp","iat"]`). |

> `jwks_uri` and `discovery_url` are mutually exclusive per entry. Set exactly one.

Multiple issuers are supported — add one object per issuer to the list.

---

## Deploying Behind an Authenticating Reverse Proxy

`JWKS_VALIDATION_ENABLED=true` makes Codemie accept `Authorization: Bearer <jwt>` — but only for
requests that actually reach Codemie. When an authenticating reverse proxy (OAuth2 Proxy, NGINX
`auth_request`, Istio) fronts the deployment, that proxy sees the request first and, unless
configured otherwise, treats a bearer-only request as an unauthenticated browser session and
redirects it into the IdP login flow.

**Symptom.** The API returns `200` carrying the IdP's HTML login page instead of the expected JSON,
and no Codemie application log line is emitted for the request — because the request never arrived.
A `401` from Codemie means the opposite: the request did arrive, and token validation is what to
debug.

### OAuth2 Proxy

Two settings make OAuth2 Proxy forward bearer tokens instead of intercepting them:

| Setting | Value | Purpose |
|---|---|---|
| `OAUTH2_PROXY_SKIP_JWT_BEARER_TOKENS` | `true` | Pass through a request carrying a valid `Authorization: Bearer` JWT instead of redirecting it into the login flow. |
| `OAUTH2_PROXY_EXTRA_JWT_ISSUERS` | `<issuer_uri>=<audience>` | Additional issuers whose bearer tokens the proxy will accept, beyond its own. Comma-separate for multiple issuers. |

> **These are not part of this chart.** `deploy-templates/` neither deploys nor configures OAuth2
> Proxy — it only points an NGINX `auth-url` annotation at an OAuth2 Proxy deployed separately
> (see `dsPool.ingressApp.annotations` in `deploy-templates/values.yaml`). Both variables belong to
> that proxy's own deployment and have to be set there. No value in this chart substitutes for them.

Whatever issuer you allow on the proxy must also appear in `JWKS_TRUSTED_ISSUERS`, and with the same
audience. Configuring only one of the two layers produces a token that clears the proxy and is then
rejected by Codemie, or vice versa.

### Codemie-side configuration in Kubernetes

The chart exposes no dedicated JWKS values block. Use the generic `customEnv` passthrough, which is
merged into the API deployment's environment after `extraEnv` and therefore overrides it:

```yaml
customEnv:
  - name: IDP_PROVIDER
    value: "oidc"
  - name: JWKS_VALIDATION_ENABLED
    value: "true"
  - name: JWKS_TRUSTED_ISSUERS
    value: |
      [{"issuer": "https://idp.example.com/auth/realms/my-realm",
        "audience": "codemie-platform",
        "discovery_url": "https://idp.example.com/auth/realms/my-realm/.well-known/openid-configuration",
        "required_claims": ["iss", "sub", "email", "aud", "exp", "iat"]}]
```

`extraEnv` ships `IDP_PROVIDER=keycloak` as the chart default; setting it in `customEnv` overrides
that without editing the chart.

### Tokens obtained by exchange (RFC 8693)

A token minted by an external IdP and swapped for a Codemie-scoped one via RFC 8693 token exchange
is, from Codemie's perspective, just a bearer JWT from another issuer. Nothing special is required
beyond the general rules above:

1. Add the issuing realm to `JWKS_TRUSTED_ISSUERS` with the `audience` the exchange actually mints.
   Existing issuers keep working — the list accepts multiple entries.
2. Allow the same issuer and audience on the fronting proxy, as described above.
3. Use `IDP_PROVIDER=oidc`. The `oidc` provider reads the token from the `Authorization` header and
   maps claims straight out of it. The `keycloak` provider resolves the caller against a Keycloak
   userinfo endpoint, so it only accepts tokens Keycloak itself issued — a token from a different
   issuer fails there even when its signature validates correctly.

Verify with a request that has no browser session attached, so nothing but the bearer token can
authenticate it:

```bash
curl -sS -o /dev/null -w '%{http_code} %{content_type}\n' \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  https://codemie.example.com/code-assistant-api/v1/user
```

`200 application/json` means the token reached Codemie and validated. `200 text/html` means the
proxy is still intercepting — revisit `OAUTH2_PROXY_SKIP_JWT_BEARER_TOKENS`. `401` means the request
reached Codemie and the token was rejected — revisit `JWKS_TRUSTED_ISSUERS`.

---

## Startup Behaviour

| Condition | Result |
|---|---|
| `JWKS_VALIDATION_ENABLED=false` | Warmup is a silent no-op. No HTTP calls to JWKS endpoints. Providers are not wrapped — signatures are not verified. |
| `JWKS_VALIDATION_ENABLED=true`, `JWKS_TRUSTED_ISSUERS` empty | Logs error at startup: `JWKS validation is enabled but JWKS_TRUSTED_ISSUERS is empty`. First request returns `401`. |
| `JWKS_VALIDATION_ENABLED=true`, JWKS endpoint unreachable at startup | Logs warning. Warmup is best-effort (non-fatal). First real request triggers an on-demand fetch; if that also fails → `401` (fail-closed). |
| `JWKS_VALIDATION_ENABLED=true`, all issuers reachable | Logs `JWKS warm-up complete`. Keys are pre-fetched and cached; first request has no JWKS round-trip latency. |

---

## Security Properties (JWKS Mode)

When JWKS is enabled, the following checks are enforced on every request **before** any IDP claim extraction:

1. **Algorithm enforcement** — only RS256 tokens are accepted; `alg=none`, HS256, and all other algorithms are rejected immediately (before any JWKS lookup).
2. **Algorithm-confusion protection** — HS256 tokens forged with the RSA public key as the HMAC secret are rejected.
3. **Issuer binding** — the `iss` claim is matched against `JWKS_TRUSTED_ISSUERS`; unknown issuers are rejected before any JWKS network call.
4. **Audience binding** — the `aud` claim must match the configured audience for that issuer.
5. **Required claims** — configurable per issuer; missing claims are rejected.
6. **Expiry and clock skew** — `exp` is checked with `JWKS_LEEWAY_SECONDS` tolerance.
7. **Fail-closed** — if the JWKS endpoint returns a 5xx, times out, or returns malformed JSON, the request is rejected with `401 Token validation unavailable`. Codemie never falls back to unverified claim extraction.

---

## Key Rotation

When a token presents a `kid` (key ID) not found in the cache, the JWKS client performs an immediate force-refresh even if the cache TTL has not expired. This handles key rotation without requiring a Codemie restart or manual cache invalidation.

---

## Example Configurations

### Minimal: local development (no external IdP)

```bash
# No JWKS vars needed — this is the default
IDP_PROVIDER=local
```

### OIDC IdP behind an ingress proxy (no signature check in Codemie)

```bash
IDP_PROVIDER=oidc
# JWKS_VALIDATION_ENABLED not set (defaults to false)
```

Codemie trusts the bearer token forwarded by the proxy. The proxy is responsible for signature verification.

### OIDC IdP with JWKS signature verification

```bash
IDP_PROVIDER=oidc
JWKS_VALIDATION_ENABLED=true
JWKS_TRUSTED_ISSUERS='[{
  "issuer": "https://auth.example.com",
  "audience": "codemie-platform",
  "discovery_url": "https://auth.example.com/.well-known/openid-configuration"
}]'
JWKS_CACHE_TTL_SECONDS=300
JWKS_LEEWAY_SECONDS=30
```

### Keycloak with JWKS signature verification

```bash
IDP_PROVIDER=keycloak
JWKS_VALIDATION_ENABLED=true
JWKS_TRUSTED_ISSUERS='[{
  "issuer": "https://keycloak.example.com/realms/my-realm",
  "audience": "codemie-platform",
  "jwks_uri": "https://keycloak.example.com/realms/my-realm/protocol/openid-connect/certs",
  "required_claims": ["iss", "sub", "email", "aud", "exp", "iat"]
}]'
KEYCLOAK_URL=https://keycloak.example.com
KEYCLOAK_REALM=my-realm
KEYCLOAK_CLIENT_ID=codemie-platform
```

> Note: `required_claims` omits `groups` here because Keycloak does not include it by default. Add a `groups` protocol mapper in Keycloak and add `"groups"` back to the list if your application uses group-based access control.

---

## Cross-references

- **Unit and integration tests**: `tests/codemie/rest_api/security/jwks/`
- **Backward-compatibility tests** (JWKS disabled path): `tests/codemie/rest_api/security/jwks/test_disabled.py`
- **Source — IDP factory**: `src/codemie/rest_api/security/idp/factory.py`
- **Source — JWKS runtime**: `src/codemie/rest_api/security/jwks/runtime.py`
- **Source — config defaults**: `src/codemie/configs/config.py` (lines 226–238)
