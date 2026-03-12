# codemie

![Version: 0.1.0](https://img.shields.io/badge/Version-0.1.0-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: 0.1.0](https://img.shields.io/badge/AppVersion-0.1.0-informational?style=flat-square)

A Helm chart for AI/Run API

**Homepage:** <https://codemie.lab.epam.com>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| AI/Run | <SpecialEPM-CDMEDevelopmentTeam@epam.com> |  |

## Source Code

* <https://gitbud.epam.com/epm-cdme/codemie.git>

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` | Assign affinity rules to the deployment |
| argoRollout.enabled | bool | `false` | Enable Argo Rollouts instead of standard Kubernetes deployment |
| argoRollout.scaleDownDelaySeconds | int | `120` | The time to wait before scaling down the old replica set Default: 120 seconds = 2 minutes |
| customEnv | list | `[]` | List of custom extra environment variables to be used by the AI/Run API Use this to add or override environment variables |
| extraEnv | list | `[]` | List of extra environment variables to be used by the AI/Run API |
| extraObjects | list | `[]` | Array of extra Kubernetes manifests to deploy |
| extraVolumeMounts | string | `"- name: google-service-account\n  readOnly: true\n  mountPath: \"/secrets/gcp-service-account.json\"\n  subPath: gcp-service-account.json\n- name: codemie-customer-config\n  mountPath: /app/config/customer\n"` | List of extra mounts to add (normally used with extraVolumes) |
| extraVolumes | string | `"- name: google-service-account\n  secret:\n    secretName: google-service-account\n- name: codemie-customer-config\n  configMap:\n    name: codemie-customer-config\n"` | List of extra volumes to add |
| fullnameOverride | string | `""` |  |
| hostAliases | list | `[]` | Mapping between IP and hostnames that will be injected as entries in the pod's hosts files |
| image.pullPolicy | string | `"IfNotPresent"` | Image pull policy for the AI/Run API |
| image.repository | string | `""` | Repository to use for the AI/Run API |
| image.tag | string | `""` | Tag to use for the AI/Run API. Overrides the image tag whose default is the chart appVersion |
| imagePullSecrets | list | `[]` | Secrets with credentials to pull images from a private registry |
| ingress.annotations | object | `{"nginx.ingress.kubernetes.io/auth-response-headers":"X-Auth-Request-Access-Token,Authorization","nginx.ingress.kubernetes.io/auth-signin":"https://$host/oauth2/start?rd=$escaped_request_uri","nginx.ingress.kubernetes.io/auth-url":"http://oauth2-proxy.oauth2-proxy.svc.cluster.local:80/oauth2/auth","nginx.ingress.kubernetes.io/proxy-body-size":"900m","nginx.ingress.kubernetes.io/proxy-buffer-size":"64k","nginx.ingress.kubernetes.io/proxy-read-timeout":"600","nginx.ingress.kubernetes.io/rewrite-target":"/$1"}` | Additional ingress annotations |
| ingress.enabled | bool | `true` | Enable an ingress resource for the AI/Run API |
| ingress.host | string | `"codemie.%%DOMAIN%%"` | AI/Run API hostname |
| ingress.path | string | `"/code-assistant-api/(.*)"` | The path to AI/Run API |
| ingress.pathType | string | `"ImplementationSpecific"` | Ingress path type |
| ingress.tls | list | `[]` |  |
| ingressCallbacksIndex.annotations | object | `{"nginx.ingress.kubernetes.io/rewrite-target":"/v1/callbacks/index/$1"}` | Additional ingress annotations |
| ingressCallbacksIndex.path | string | `"/code-assistant-api/v1/callbacks/index/(.*)"` | The path to AI/Run API for Callbacks Index |
| ingressCallbacksIndex.pathType | string | `"ImplementationSpecific"` | Ingress path type |
| ingressSwagger.annotations | object | `{}` | Additional ingress annotations |
| ingressSwagger.path | string | `"/openapi.json"` | The path to AI/Run API for Swagger |
| ingressSwagger.pathType | string | `"ImplementationSpecific"` | Ingress path type |
| ingressWebHooks.annotations | object | `{"nginx.ingress.kubernetes.io/rewrite-target":"/v1/webhooks/$1"}` | Additional ingress annotations |
| ingressWebHooks.path | string | `"/code-assistant-api/v1/webhooks/(.*)"` | The path to AI/Run API for Webhooks |
| ingressWebHooks.pathType | string | `"ImplementationSpecific"` | Ingress path type |
| livenessProbe.failureThreshold | int | `3` | Minimum consecutive failures for the probe to be considered failed after having succeeded |
| livenessProbe.httpGet.path | string | `"/v1/healthcheck"` |  |
| livenessProbe.httpGet.port | int | `8080` |  |
| livenessProbe.initialDelaySeconds | int | `20` | Number of seconds after the container has started before probe is initiated |
| livenessProbe.periodSeconds | int | `30` | How often (in seconds) to perform the probe |
| livenessProbe.successThreshold | int | `1` | Minimum consecutive successes for the probe to be considered successful after having failed |
| livenessProbe.timeoutSeconds | int | `1` | Number of seconds after which the probe times out |
| nameOverride | string | `""` |  |
| nodeSelector | object | `{}` | Node selector to be added to the AI/Run API pods |
| podAnnotations | object | `{}` | Annotations to be added to AI/Run API pods |
| podLabels | object | `{}` | Labels to be added to AI/Run UI pods. |
| podSecurityContext | object | `{}` | Toggle and define pod-level security context |
| priorityClassName | string | `""` | Priority class for the AI/Run API pods |
| readinessProbe.failureThreshold | int | `3` | Minimum consecutive failures for the probe to be considered failed after having succeeded |
| readinessProbe.httpGet.path | string | `"/v1/healthcheck"` |  |
| readinessProbe.httpGet.port | int | `8080` |  |
| readinessProbe.initialDelaySeconds | int | `20` | Number of seconds after the container has started before probe is initiated |
| readinessProbe.periodSeconds | int | `30` | How often (in seconds) to perform the probe |
| readinessProbe.successThreshold | int | `1` | Minimum consecutive successes for the probe to be considered successful after having failed |
| readinessProbe.timeoutSeconds | int | `1` | Number of seconds after which the probe times out |
| replicaCount | int | `1` | The number of AI/Run API pods to run |
| resources | object | `{"limits":{"cpu":2,"memory":"2048Mi"},"requests":{"cpu":"100m","memory":"1024Mi"}}` | Resource limits and requests for the AI/Run API pods |
| securityContext | object | `{}` | AI/Run API container-level security context |
| service.annotations | object | `{}` | AI/Run API service annotations |
| service.port | int | `8080` | AI/Run API service port |
| service.type | string | `"ClusterIP"` | AI/Run API service type |
| serviceAccount.annotations | object | `{}` | Annotations applied to created service account |
| serviceAccount.create | bool | `false` | Specifies whether a service account should be created |
| serviceAccount.name | string | `""` | Service account name for AI/Run API pod If not set and create is true, a name is generated using the fullname template |
| startupProbe.failureThreshold | int | `60` | Minimum consecutive failures for the probe to be considered failed after having succeeded |
| startupProbe.httpGet.path | string | `"/v1/healthcheck"` |  |
| startupProbe.httpGet.port | int | `8080` |  |
| startupProbe.initialDelaySeconds | int | `20` | Number of seconds after the container has started before probe is initiated |
| startupProbe.periodSeconds | int | `10` | How often (in seconds) to perform the probe |
| tolerations | list | `[]` | Node selector to be added to the AI/Run API pods |
