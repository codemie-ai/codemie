{{/*
Expand the name of the chart.
*/}}
{{- define "codemie.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "codemie.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "codemie.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "codemie.labels" -}}
helm.sh/chart: {{ include "codemie.chart" . }}
{{ include "codemie.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "codemie.selectorLabels" -}}
app.kubernetes.io/name: {{ include "codemie.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "codemie.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "codemie.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Return the appropriate apiVersion for ingress
*/}}
{{- define "codemie.ingress.apiVersion" -}}
  {{- if and (.Capabilities.APIVersions.Has "networking.k8s.io/v1") (semverCompare ">= 1.19-0" .Capabilities.KubeVersion.Version) -}}
      {{- print "networking.k8s.io/v1" -}}
  {{- else if .Capabilities.APIVersions.Has "networking.k8s.io/v1beta1" -}}
    {{- print "networking.k8s.io/v1beta1" -}}
  {{- else -}}
    {{- print "extensions/v1beta1" -}}
  {{- end -}}
{{- end -}}

{{/*
Return if ingress is stable.
*/}}
{{- define "codemie.ingress.isStable" -}}
  {{- eq (include "codemie.ingress.apiVersion" .) "networking.k8s.io/v1" -}}
{{- end -}}

{{/*
Return if ingress supports ingressClassName.
*/}}
{{- define "codemie.ingress.supportsIngressClassName" -}}
  {{- or (eq (include "codemie.ingress.isStable" .) "true") (and (eq (include "codemie.ingress.apiVersion" .) "networking.k8s.io/v1beta1") (semverCompare ">= 1.18-0" .Capabilities.KubeVersion.Version)) -}}
{{- end -}}

{{/*
Return if ingress supports pathType.
*/}}
{{- define "codemie.ingress.supportsPathType" -}}
  {{- or (eq (include "codemie.ingress.isStable" .) "true") (and (eq (include "codemie.ingress.apiVersion" .) "networking.k8s.io/v1beta1") (semverCompare ">= 1.18-0" .Capabilities.KubeVersion.Version)) -}}
{{- end -}}

{{/*
Merge two or more lists of dicts by the "name" field (last-wins on collision).
Items without a "name" field are skipped.
Insertion order from the first list is preserved; new names from later lists are appended.

Usage:
  {{- include "codemie.mergeEnvLists" (list <list1> <list2> [...]) | nindent 2 }}

Example:
  env:
    {{- include "codemie.mergeEnvLists" (list .Values.baseEnv .Values.extraEnv) | nindent 4 }}
*/}}
{{/*
Metrics env vars list when metrics.enabled is true.
Returns a YAML array parsed by callers via fromYamlArray.
*/}}
{{- define "codemie.metrics.envList" -}}
{{- if .Values.metrics.enabled }}
- name: PROMETHEUS_ENABLED
  value: "true"
- name: PROMETHEUS_METRICS_PORT
  value: {{ .Values.metrics.port | quote }}
{{- end }}
{{- end }}

{{- define "codemie.mergeEnvLists" -}}
{{- $index := dict -}}
{{- $order := list -}}
{{- range . -}}
  {{- range . -}}
    {{- if hasKey . "name" -}}
      {{- $name := .name -}}
      {{- if hasKey $index $name -}}
        {{- $_ := set $index $name (. | deepCopy) -}}
      {{- else -}}
        {{- $_ := set $index $name (. | deepCopy) -}}
        {{- $order = append $order $name -}}
      {{- end -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- $result := list -}}
{{- range $order -}}
  {{- $result = append $result (get $index .) -}}
{{- end -}}
{{- if $result }}
{{ $result | toYaml }}
{{- end }}
{{- end }}

{{/*
Hosted applications validator name
*/}}
{{- define "codemie.hostedValidator.name" -}}
codemie-hosted-apps-validator
{{- end }}

{{/*
Hosted applications validator selector labels
*/}}
{{- define "codemie.hostedValidator.selectorLabels" -}}
app.kubernetes.io/name: {{ include "codemie.hostedValidator.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Hosted applications validator common labels
*/}}
{{- define "codemie.hostedValidator.labels" -}}
helm.sh/chart: {{ include "codemie.chart" . }}
{{ include "codemie.hostedValidator.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Hosted applications clamd name
*/}}
{{- define "codemie.hostedClamd.name" -}}
codemie-hosted-apps-clamd
{{- end }}

{{/*
Hosted applications clamd selector labels
*/}}
{{- define "codemie.hostedClamd.selectorLabels" -}}
app.kubernetes.io/name: {{ include "codemie.hostedClamd.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Hosted applications clamd common labels
*/}}
{{- define "codemie.hostedClamd.labels" -}}
helm.sh/chart: {{ include "codemie.chart" . }}
{{ include "codemie.hostedClamd.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Name of the ConfigMap with the clamd configuration
*/}}
{{- define "codemie.hostedClamd.configMapName" -}}
{{- $hostedApps := index .Values.features.tools "hosted-apps" -}}
{{- $hostedApps.clamdConfig.existingConfigMap | default (include "codemie.hostedClamd.name" .) -}}
{{- end }}
