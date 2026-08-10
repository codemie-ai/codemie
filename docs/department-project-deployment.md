# Department project deployment

Deployments that use department projects exclusively should set:

```env
PERSONAL_PROJECTS_ENABLED=false
GLOBAL_ASSISTANTS_ENABLED=false
```

`PERSONAL_PROJECTS_ENABLED` defaults to `true` for upstream compatibility. When
disabled, registration and authentication do not create email-named personal
projects. `GLOBAL_ASSISTANTS_ENABLED` also defaults to `true`; when disabled,
only administrators and maintainers can publish marketplace assistants.

Assistants created through the API must include a project that already exists
and is accessible to the requesting user. Department administrators should
enable member spend enforcement on every department project budget so that the
equal member allocations are applied by the budget provider.
