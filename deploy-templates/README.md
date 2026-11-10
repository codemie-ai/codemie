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
| dsPool.argoRollout.enabled | bool | `false` | Enable Argo Rollouts instead of standard Kubernetes Deployment for ds-pool |
| dsPool.argoRollout.scaleDownDelaySeconds | int | `120` | The time to wait before scaling down the old replica set |
| dsPool.enabled | bool | `false` | Enable a separate deployment/rollout for ds-pool processing |
| dsPool.extraEnv | list | `[]` | Additional env vars merged after extraEnv + customEnv (e.g. DATASOURCE_WORKER_ONLY=true) |
| dsPool.hpa.enabled | bool | `false` | Enable the HorizontalPodAutoscaler for the ds-pool rollout/deployment |
| dsPool.hpa.maxReplicas | int | `2` | Maximum number of ds-pool pods |
| dsPool.hpa.minReplicas | int | `1` | Minimum number of ds-pool pods |
| dsPool.hpa.scaleDownPolicyPeriodSeconds | int | `600` | Time window for the scaleDownPolicyPods limit |
| dsPool.hpa.scaleDownPolicyPods | int | `1` | Maximum number of pods the HPA can remove per scaleDownPolicyPeriodSeconds window |
| dsPool.hpa.scaleDownStabilizationWindowSeconds | int | `1800` | Time window CPU must stay below target before pods are removed |
| dsPool.hpa.scaleUpPolicyPeriodSeconds | int | `15` | Time window for the scaleUpPolicyPods limit |
| dsPool.hpa.scaleUpPolicyPods | int | `1` | Maximum number of pods the HPA can add per scaleUpPolicyPeriodSeconds window |
| dsPool.hpa.scaleUpStabilizationWindowSeconds | int | `60` | Time window the CPU target must be exceeded before pods are added |
| dsPool.hpa.targetCPUUtilizationPercentage | int | `70` | Target CPU utilization of the codemie container, as a percentage of its CPU request |
| dsPool.ingress.annotations | object | `{"nginx.ingress.kubernetes.io/proxy-body-size":"900m","nginx.ingress.kubernetes.io/proxy-read-timeout":"600","nginx.ingress.kubernetes.io/rewrite-target":"/v1/index/$1"}` | Additional ingress annotations |
| dsPool.ingress.enabled | bool | `false` | Enable ingress for the ds-pool workload |
| dsPool.ingress.path | string | `"/code-assistant-api/v1/index/(.*)"` | Path routed to the ds-pool service (all /v1/index/* endpoints) |
| dsPool.ingress.pathType | string | `"ImplementationSpecific"` | Ingress path type |
| dsPool.ingressApp.annotations | object | `{"nginx.ingress.kubernetes.io/auth-response-headers":"X-Auth-Request-Access-Token,Authorization","nginx.ingress.kubernetes.io/auth-signin":"https://$host/oauth2/start?rd=$escaped_request_uri","nginx.ingress.kubernetes.io/auth-url":"http://oauth2-proxy.oauth2-proxy.svc.cluster.local:80/oauth2/auth","nginx.ingress.kubernetes.io/proxy-body-size":"900m","nginx.ingress.kubernetes.io/proxy-read-timeout":"600","nginx.ingress.kubernetes.io/rewrite-target":"/v1/application/$1/index$2"}` | Additional ingress annotations |
| dsPool.ingressApp.enabled | bool | `false` | Enable ingress for application index endpoints |
| dsPool.ingressApp.path | string | `"/code-assistant-api/v1/application/(.*)/index(.*)"` | Path routed to the ds-pool service (/v1/application/*/index* endpoints) |
| dsPool.ingressApp.pathType | string | `"ImplementationSpecific"` | Ingress path type |
| dsPool.pdb.enabled | bool | `false` | Enable the PodDisruptionBudget for the ds-pool rollout/deployment |
| dsPool.pdb.maxUnavailable | string | `""` | Maximum number of pods that can be unavailable during a disruption. Mutually exclusive with minAvailable; set only one (leave empty to use minAvailable). |
| dsPool.pdb.minAvailable | int | `1` | Minimum number of pods that must remain available during a disruption. Mutually exclusive with maxUnavailable; set only one. |
| dsPool.replicaCount | int | `1` | Number of ds-pool pods to run |
| dsPool.resources | object | `{"limits":{"cpu":2,"memory":"2048Mi"},"requests":{"cpu":"100m","memory":"1024Mi"}}` | Resource limits and requests for ds-pool pods |
| dsPool.service.annotations | object | `{}` | ds-pool service annotations |
| dsPool.service.port | int | `8080` | ds-pool service port |
| dsPool.service.type | string | `"ClusterIP"` | ds-pool service type |
| enableServiceLinks | bool | `false` | Whether to inject service links into pod environment variables |
| extraEnv | list | `[]` | List of extra environment variables to be used by the AI/Run API |
| extraObjects | list | `[]` | Array of extra Kubernetes manifests to deploy |
| extraVolumeMounts | string | `"- name: google-service-account\n  readOnly: true\n  mountPath: \"/secrets/gcp-service-account.json\"\n  subPath: gcp-service-account.json\n- name: codemie-customer-config\n  mountPath: /app/config/customer\n"` | List of extra mounts to add (normally used with extraVolumes) |
| extraVolumes | string | `"- name: google-service-account\n  secret:\n    secretName: google-service-account\n- name: codemie-customer-config\n  configMap:\n    name: codemie-customer-config\n"` | List of extra volumes to add |
| features.tools.code_executor.namespace.create | bool | `false` | Create the namespace where executor pods run. Set to true only if it doesn't already exist |
| features.tools.code_executor.namespace.name | string | `"codemie-code-executor"` | Namespace where executor pods run (must match CODE_EXECUTOR_NAMESPACE env var) |
| features.tools.code_executor.rbac | object | `{"enabled":false}` | Create Role and RoleBinding in features.tools.code_executor.namespace.name to allow the service account to manage executor pods |
| features.tools.hosted-apps.clamd.affinity | object | `{}` | Affinity rules for the clamd pod |
| features.tools.hosted-apps.clamd.automountServiceAccountToken | bool | `false` | Mount the service account token into the clamd pod |
| features.tools.hosted-apps.clamd.enableServiceLinks | bool | `false` | Inject Service environment variables into the clamd pod |
| features.tools.hosted-apps.clamd.env | list | `[{"name":"TZ","value":"UTC"},{"name":"FRESHCLAM_CHECKS","value":"4"}]` | Environment variables for the clamd container. TZ=UTC is required: clamd reports its signature date in local time and the validator reads it as UTC |
| features.tools.hosted-apps.clamd.image.pullPolicy | string | `"IfNotPresent"` | Image pull policy for ClamAV |
| features.tools.hosted-apps.clamd.image.repository | string | `"clamav/clamav"` | Repository of the ClamAV image |
| features.tools.hosted-apps.clamd.image.tag | string | `"1.5.4_base@sha256:58168cd4f48a118c7d20b76da8ffce97e579b84ed7e6d1e977c6e22b1e989c5a"` | ClamAV image tag, pinned by digest |
| features.tools.hosted-apps.clamd.imagePullSecrets | list | `[]` | Image pull secrets for the clamd pod. Defaults to the top-level imagePullSecrets |
| features.tools.hosted-apps.clamd.livenessProbe | object | `{"exec":{"command":["/bin/sh","-c","clamdscan --config-file=/etc/clamav/clamd.conf --ping=1 || { echo \"clamd is not responding\"; exit 1; }\nlive=0\nfor pid in $(pgrep freshclam); do\n  stat=$(cat /proc/$pid/stat 2>/dev/null) || continue\n  state=$(printf '%s' \"$stat\" | awk '{print $3}')\n  if [ \"$state\" != \"Z\" ]; then\n    live=1\n    break\n  fi\ndone\n[ \"$live\" = \"1\" ] || { echo \"freshclam is not running\"; exit 1; }\n"]},"failureThreshold":3,"initialDelaySeconds":60,"periodSeconds":30,"timeoutSeconds":10}` | Liveness probe for the clamd container. Checks that clamd answers and freshclam is running |
| features.tools.hosted-apps.clamd.nodeSelector | object | `{}` | Node selector for the clamd pod. The pinned upstream image is amd64-only |
| features.tools.hosted-apps.clamd.podSecurityContext | object | `{"seccompProfile":{"type":"RuntimeDefault"}}` | Pod security context for the clamd pod |
| features.tools.hosted-apps.clamd.port | int | `3310` | clamd TCP port (must match TCPSocket in clamd.conf and CLAMD_PORT in validator.env) |
| features.tools.hosted-apps.clamd.priorityClassName | string | `""` | Priority class for the clamd pod |
| features.tools.hosted-apps.clamd.resources | object | `{"limits":{"cpu":"1","memory":"3Gi"},"requests":{"cpu":"1","memory":"1536Mi"}}` | Resources for the clamd container |
| features.tools.hosted-apps.clamd.securityContext | object | `{"allowPrivilegeEscalation":false,"capabilities":{"add":["CHOWN","DAC_OVERRIDE","SETGID","SETUID"],"drop":["ALL"]}}` | Security context for the clamd container |
| features.tools.hosted-apps.clamd.startupProbe | object | `{"exec":{"command":["clamdscan","--config-file=/etc/clamav/clamd.conf","--ping=1"]},"failureThreshold":60,"periodSeconds":10}` | Startup probe for the clamd container. Signature load takes minutes after every start |
| features.tools.hosted-apps.clamd.tolerations | list | `[]` | Tolerations for the clamd pod |
| features.tools.hosted-apps.clamd.volumeMounts | list | `[{"mountPath":"/var/lib/clamav","name":"signatures"},{"mountPath":"/run/clamav","name":"clamd-run"}]` | Extra volume mounts for the clamd container |
| features.tools.hosted-apps.clamd.volumes | list | `[{"emptyDir":{"sizeLimit":"2Gi"},"name":"signatures"},{"emptyDir":{"sizeLimit":"1Mi"},"name":"clamd-run"}]` | Extra volumes for the clamd pod |
| features.tools.hosted-apps.clamdConfig.existingConfigMap | string | `""` | Name of an existing ConfigMap with the clamd configuration under the clamd.conf key. When empty, the chart creates codemie-hosted-apps-clamd |
| features.tools.hosted-apps.enabled | bool | `false` | Enable hosted applications resources |
| features.tools.hosted-apps.ingress.annotations | object | `{}` | Additional ingress annotations |
| features.tools.hosted-apps.ingress.enabled | bool | `true` | Create the hosted applications content Ingress |
| features.tools.hosted-apps.ingress.host | string | `""` | Wildcard host of the content domain, e.g. "*.apps-content.example.net" |
| features.tools.hosted-apps.ingress.ingressClassName | string | `"nginx"` | Ingress class name |
| features.tools.hosted-apps.ingress.tls | list | `[]` | Ingress TLS configuration |
| features.tools.hosted-apps.namespace.create | bool | `false` | Create the namespace for hosted applications resources. Set to true only if it doesn't already exist |
| features.tools.hosted-apps.namespace.name | string | `"codemie-hosted-apps"` | Namespace for hosted applications resources |
| features.tools.hosted-apps.networkPolicy.egress.clamd | list | `[{"ipBlock":{"cidr":"0.0.0.0/0","except":["10.92.249.0/26","10.92.202.160/28","172.20.0.0/16","172.21.0.0/17","172.30.0.0/16","169.254.0.0/16"]}}]` | Where clamd may download signatures from (port 443). Defaults to the internet without the cluster ranges and link-local addresses |
| features.tools.hosted-apps.networkPolicy.egress.dns | list | `[]` | Where the validator and clamd may send DNS queries (port 53). Empty allows any destination, which also covers GKE NodeLocal DNSCache |
| features.tools.hosted-apps.networkPolicy.enabled | bool | `true` | Create NetworkPolicies for the validator pod and clamd egress, plus a default-deny ingress policy when the hosted applications namespace differs from the release namespace. The policy that admits only the validator to clamd's unauthenticated port is created whenever hosted-apps is enabled. Needs a CNI that enforces NetworkPolicy |
| features.tools.hosted-apps.validator.affinity | object | `{}` | Affinity rules for the validator pod |
| features.tools.hosted-apps.validator.automountServiceAccountToken | bool | `false` | Mount the service account token into the validator pod |
| features.tools.hosted-apps.validator.enableServiceLinks | bool | `false` | Inject Service environment variables into the validator pod |
| features.tools.hosted-apps.validator.env | list | `[{"name":"VALIDATOR_BIND_HOST","value":"0.0.0.0"},{"name":"VALIDATOR_SERVICE_KEY","valueFrom":{"secretKeyRef":{"key":"validator-key","name":"codemie-hosted-applications"}}},{"name":"CLAMD_HOST","value":"codemie-hosted-apps-clamd"},{"name":"CLAMD_PORT","value":"3310"},{"name":"CLAMD_CONFIGURATION","value":"/etc/clamav/clamd.conf"}]` | Environment variables for the validator container |
| features.tools.hosted-apps.validator.image.pullPolicy | string | `"IfNotPresent"` | Image pull policy for the validator |
| features.tools.hosted-apps.validator.image.repository | string | `""` | Repository of the hosted applications validator image (built from Dockerfile.validator) |
| features.tools.hosted-apps.validator.image.tag | string | `""` | Validator image tag. Defaults to image.tag (the API image tag), then to the chart appVersion |
| features.tools.hosted-apps.validator.imagePullSecrets | list | `[]` | Image pull secrets for the validator pod. Defaults to the top-level imagePullSecrets |
| features.tools.hosted-apps.validator.livenessProbe | object | `{"httpGet":{"path":"/livez","port":"http"},"initialDelaySeconds":10,"periodSeconds":30,"timeoutSeconds":5}` | Liveness probe for the validator container |
| features.tools.hosted-apps.validator.nodeSelector | object | `{}` | Node selector for the validator pod |
| features.tools.hosted-apps.validator.podSecurityContext | object | `{"seccompProfile":{"type":"RuntimeDefault"}}` | Pod security context for the validator pod |
| features.tools.hosted-apps.validator.priorityClassName | string | `""` | Priority class for the validator pod |
| features.tools.hosted-apps.validator.readinessProbe | object | `{"httpGet":{"path":"/livez","port":"http"},"periodSeconds":10}` | Readiness probe for the validator container |
| features.tools.hosted-apps.validator.resources | object | `{"limits":{"cpu":"1","memory":"2Gi"},"requests":{"cpu":"250m","memory":"512Mi"}}` | Resources for the validator container |
| features.tools.hosted-apps.validator.securityContext | object | `{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]},"readOnlyRootFilesystem":true,"runAsGroup":65532,"runAsNonRoot":true,"runAsUser":65532}` | Security context for the validator container |
| features.tools.hosted-apps.validator.tolerations | list | `[]` | Tolerations for the validator pod |
| features.tools.hosted-apps.validator.volumeMounts | list | `[{"mountPath":"/work","name":"work"}]` | Extra volume mounts for the validator container |
| features.tools.hosted-apps.validator.volumes | list | `[{"emptyDir":{"sizeLimit":"2Gi"},"name":"work"}]` | Extra volumes for the validator pod |
| fullnameOverride | string | `""` |  |
| hostAliases | list | `[]` | Mapping between IP and hostnames that will be injected as entries in the pod's hosts files |
| hpa.enabled | bool | `false` | Enable the HorizontalPodAutoscaler for the codemie-api rollout/deployment |
| hpa.maxReplicas | int | `2` | Maximum number of codemie-api pods |
| hpa.minReplicas | int | `1` | Minimum number of codemie-api pods |
| hpa.scaleDownPolicyPeriodSeconds | int | `600` | Time window for the scaleDownPolicyPods limit |
| hpa.scaleDownPolicyPods | int | `1` | Maximum number of pods the HPA can remove per scaleDownPolicyPeriodSeconds window |
| hpa.scaleDownStabilizationWindowSeconds | int | `1800` | Time window CPU must stay below target before pods are removed |
| hpa.scaleUpPolicyPeriodSeconds | int | `15` | Time window for the scaleUpPolicyPods limit |
| hpa.scaleUpPolicyPods | int | `1` | Maximum number of pods the HPA can add per scaleUpPolicyPeriodSeconds window |
| hpa.scaleUpStabilizationWindowSeconds | int | `60` | Time window the CPU target must be exceeded before pods are added |
| hpa.targetCPUUtilizationPercentage | int | `70` | Target CPU utilization of the codemie container, as a percentage of its CPU request |
| image.pullPolicy | string | `"IfNotPresent"` | Image pull policy for the AI/Run API |
| image.repository | string | `""` | Repository to use for the AI/Run API |
| image.tag | string | `""` | Tag to use for the AI/Run API. Overrides the image tag whose default is the chart appVersion |
| imagePullSecrets | list | `[]` | Secrets with credentials to pull images from a private registry |
| ingress.annotations | object | `{"nginx.ingress.kubernetes.io/proxy-body-size":"900m","nginx.ingress.kubernetes.io/proxy-buffer-size":"64k","nginx.ingress.kubernetes.io/proxy-read-timeout":"600","nginx.ingress.kubernetes.io/rewrite-target":"/$1"}` | Additional ingress annotations |
| ingress.enabled | bool | `true` | Enable an ingress resource for the AI/Run API |
| ingress.host | string | `"codemie.%%DOMAIN%%"` | AI/Run API hostname |
| ingress.path | string | `"/code-assistant-api/(.*)"` | The path to AI/Run API |
| ingress.pathType | string | `"ImplementationSpecific"` | Ingress path type |
| ingress.tls | list | `[]` |  |
| ingressCallbacksIndex.annotations | object | `{"nginx.ingress.kubernetes.io/rewrite-target":"/v1/callbacks/index/$1"}` | Additional ingress annotations |
| ingressCallbacksIndex.path | string | `"/code-assistant-api/v1/callbacks/index/(.*)"` | The path to AI/Run API for Callbacks Index |
| ingressCallbacksIndex.pathType | string | `"ImplementationSpecific"` | Ingress path type |
| ingressOauthClientMetadata.annotations | object | `{"nginx.ingress.kubernetes.io/rewrite-target":"/oauth/client-metadata.json"}` | Additional ingress annotations |
| ingressOauthClientMetadata.path | string | `"/code-assistant-api/oauth/client-metadata.json"` | The path to AI/Run API for OAuth client metadata |
| ingressOauthClientMetadata.pathType | string | `"ImplementationSpecific"` | Ingress path type |
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
| livenessProbe.timeoutSeconds | int | `10` | Number of seconds after which the probe times out |
| metrics | object | `{"enabled":false,"port":9091}` | Prometheus metrics server configuration |
| metrics.enabled | bool | `false` | Enable Prometheus metrics scraping endpoint |
| metrics.port | int | `9091` | The port the metrics server listens on |
| nameOverride | string | `""` |  |
| nodeSelector | object | `{}` | Node selector to be added to the AI/Run API pods |
| pdb.enabled | bool | `false` | Enable the PodDisruptionBudget for the codemie-api rollout/deployment |
| pdb.maxUnavailable | string | `""` | Maximum number of pods that can be unavailable during a disruption. Mutually exclusive with minAvailable; set only one (leave empty to use minAvailable). |
| pdb.minAvailable | int | `1` | Minimum number of pods that must remain available during a disruption. Mutually exclusive with maxUnavailable; set only one. |
| podAnnotations | object | `{}` | Annotations to be added to AI/Run API pods |
| podLabels | object | `{}` | Labels to be added to AI/Run UI pods. |
| podSecurityContext | object | `{}` | Toggle and define pod-level security context |
| priorityClassName | string | `""` | Priority class for the AI/Run API pods |
| proxyPool.argoRollout.enabled | bool | `false` | Enable Argo Rollouts instead of standard Kubernetes Deployment for proxy-pool |
| proxyPool.argoRollout.scaleDownDelaySeconds | int | `120` | The time to wait before scaling down the old replica set |
| proxyPool.enabled | bool | `false` | Enable a separate deployment/rollout for proxy-pool processing |
| proxyPool.extraEnv | list | `[]` | Additional env vars merged after extraEnv + customEnv |
| proxyPool.hpa.enabled | bool | `false` | Enable the HorizontalPodAutoscaler for the proxy-pool rollout/deployment |
| proxyPool.hpa.maxReplicas | int | `2` | Maximum number of proxy-pool pods |
| proxyPool.hpa.minReplicas | int | `1` | Minimum number of proxy-pool pods |
| proxyPool.hpa.scaleDownPolicyPeriodSeconds | int | `600` | Time window for the scaleDownPolicyPods limit |
| proxyPool.hpa.scaleDownPolicyPods | int | `1` | Maximum number of pods the HPA can remove per scaleDownPolicyPeriodSeconds window |
| proxyPool.hpa.scaleDownStabilizationWindowSeconds | int | `1800` | Time window CPU must stay below target before pods are removed |
| proxyPool.hpa.scaleUpPolicyPeriodSeconds | int | `15` | Time window for the scaleUpPolicyPods limit |
| proxyPool.hpa.scaleUpPolicyPods | int | `1` | Maximum number of pods the HPA can add per scaleUpPolicyPeriodSeconds window |
| proxyPool.hpa.scaleUpStabilizationWindowSeconds | int | `60` | Time window the CPU target must be exceeded before pods are added |
| proxyPool.hpa.targetCPUUtilizationPercentage | int | `70` | Target CPU utilization of the codemie container, as a percentage of its CPU request |
| proxyPool.ingress.annotations | object | `{"nginx.ingress.kubernetes.io/proxy-body-size":"900m","nginx.ingress.kubernetes.io/proxy-read-timeout":"600"}` | Additional ingress annotations |
| proxyPool.ingress.enabled | bool | `false` | Enable ingress for the proxy-pool workload |
| proxyPool.ingress.pathType | string | `"ImplementationSpecific"` | Ingress path type |
| proxyPool.ingress.paths | list | `[{"path":"/v1/chat/completions"},{"path":"/chat/completions"},{"path":"/v1/completions"},{"path":"/completions"},{"path":"/v1/messages"},{"path":"/messages"},{"path":"/v1/messages/count_tokens"},{"path":"/messages/count_tokens"},{"path":"/v1/responses"},{"path":"/responses"},{"path":"/v1/embeddings"},{"path":"/embeddings"},{"path":"/v1/health"},{"path":"/health"},{"path":"/v1/models"},{"path":"/models"},{"path":"/v1/models/(.*):generateContent"},{"path":"/models/(.*):generateContent"},{"path":"/v1/models/(.*):streamGenerateContent"},{"path":"/models/(.*):streamGenerateContent"},{"path":"/v1/models/(.*):countTokens"},{"path":"/models/(.*):countTokens"},{"path":"/v1beta/models/(.*):generateContent"},{"path":"/v1beta/models/(.*):streamGenerateContent"}]` | Paths routed to the proxy-pool service (all LiteLLM proxy endpoints) |
| proxyPool.pdb.enabled | bool | `false` | Enable the PodDisruptionBudget for the proxy-pool rollout/deployment |
| proxyPool.pdb.maxUnavailable | string | `""` | Maximum number of pods that can be unavailable during a disruption. Mutually exclusive with minAvailable; set only one (leave empty to use minAvailable). |
| proxyPool.pdb.minAvailable | int | `1` | Minimum number of pods that must remain available during a disruption. Mutually exclusive with maxUnavailable; set only one. |
| proxyPool.replicaCount | int | `1` | Number of proxy-pool pods to run |
| proxyPool.resources | object | `{"limits":{"cpu":2,"memory":"2048Mi"},"requests":{"cpu":"100m","memory":"1024Mi"}}` | Resource limits and requests for proxy-pool pods |
| proxyPool.service.annotations | object | `{}` | proxy-pool service annotations |
| proxyPool.service.port | int | `8080` | proxy-pool service port |
| proxyPool.service.type | string | `"ClusterIP"` | proxy-pool service type |
| readinessProbe.failureThreshold | int | `3` | Minimum consecutive failures for the probe to be considered failed after having succeeded |
| readinessProbe.httpGet.path | string | `"/v1/healthcheck"` |  |
| readinessProbe.httpGet.port | int | `8080` |  |
| readinessProbe.initialDelaySeconds | int | `20` | Number of seconds after the container has started before probe is initiated |
| readinessProbe.periodSeconds | int | `30` | How often (in seconds) to perform the probe |
| readinessProbe.successThreshold | int | `1` | Minimum consecutive successes for the probe to be considered successful after having failed |
| readinessProbe.timeoutSeconds | int | `10` | Number of seconds after which the probe times out |
| replicaCount | int | `1` | The number of AI/Run API pods to run |
| resources | object | `{"limits":{"cpu":2,"memory":"8Gi"},"requests":{"cpu":1,"memory":"8Gi"}}` | Resource limits and requests for the AI/Run API pods |
| securityContext | object | `{}` | AI/Run API container-level security context |
| service.annotations | object | `{}` | AI/Run API service annotations |
| service.port | int | `8080` | AI/Run API service port |
| service.type | string | `"ClusterIP"` | AI/Run API service type |
| serviceAccount.annotations | object | `{}` | Annotations applied to created service account |
| serviceAccount.automountServiceAccountToken | bool | `true` | Toggle automatic mounting of the service account token. |
| serviceAccount.create | bool | `true` | Specifies whether a service account should be created |
| serviceAccount.name | string | `""` | Service account name for AI/Run API pod If not set and create is true, a name is generated using the fullname template |
| startupProbe.failureThreshold | int | `240` | Minimum consecutive failures for the probe to be considered failed after having succeeded |
| startupProbe.httpGet.path | string | `"/v1/healthcheck"` |  |
| startupProbe.httpGet.port | int | `8080` |  |
| startupProbe.initialDelaySeconds | int | `20` | Number of seconds after the container has started before probe is initiated |
| startupProbe.periodSeconds | int | `10` | How often (in seconds) to perform the probe |
| tolerations | list | `[]` | Tolerations to be added to the AI/Run API pods. Applies only to the API Deployment/Rollout; does not affect Code Executor sandbox Job pods, which are configured separately via CODE_EXECUTOR_TOLERATIONS (see the `customEnv` example above) |
