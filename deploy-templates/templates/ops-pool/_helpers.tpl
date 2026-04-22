{{/*
Fully qualified name for the ops-pool workload.
*/}}
{{- define "codemie.ops-pool.fullname" -}}
{{- printf "%s-ops-pool" (include "codemie.fullname" .) }}
{{- end }}

{{/*
Selector labels for the ops-pool workload (distinct from main selectorLabels).
*/}}
{{- define "codemie.ops-pool.selectorLabels" -}}
app.kubernetes.io/name: {{ include "codemie.name" . }}-ops-pool
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
