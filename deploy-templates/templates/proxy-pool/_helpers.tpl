{{/*
Fully qualified name for the proxy-pool workload.
*/}}
{{- define "codemie.proxy-pool.fullname" -}}
{{- printf "%s-proxy-pool" (include "codemie.fullname" .) }}
{{- end }}

{{/*
Selector labels for the proxy-pool workload (distinct from main selectorLabels).
*/}}
{{- define "codemie.proxy-pool.selectorLabels" -}}
app.kubernetes.io/name: {{ include "codemie.name" . }}-proxy-pool
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
