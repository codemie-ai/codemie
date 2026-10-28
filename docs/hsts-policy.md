# HTTP Strict Transport Security (HSTS)

The AI/Run API (`/code-assistant-api/*`) adds the following header to every response
(including error responses):

```
Strict-Transport-Security: max-age=31536000; includeSubDomains
```

It is set by the `add_hsts_header` middleware in `src/codemie/rest_api/main.py`.
Browsers ignore the header on plain-HTTP connections, so it is safe to emit when TLS is
terminated at the ingress. Redirecting HTTP to HTTPS remains an ingress responsibility.
