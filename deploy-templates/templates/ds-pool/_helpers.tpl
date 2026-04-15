{{/*
Fully qualified name for the ds-pool workload.
*/}}
{{- define "codemie.ds-pool.fullname" -}}
{{- printf "%s-ds-pool" (include "codemie.fullname" .) }}
{{- end }}

{{/*
Selector labels for the ds-pool workload (distinct from main selectorLabels).
*/}}
{{- define "codemie.ds-pool.selectorLabels" -}}
app.kubernetes.io/name: {{ include "codemie.name" . }}-ds-pool
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
