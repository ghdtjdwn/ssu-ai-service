{{- define "ssu-ai-service.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ssu-ai-service.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- include "ssu-ai-service.name" . -}}
{{- end -}}
{{- end -}}

{{- define "ssu-ai-service.labels" -}}
app.kubernetes.io/name: {{ include "ssu-ai-service.fullname" . }}
app.kubernetes.io/part-of: ssuai
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "ssu-ai-service.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ssu-ai-service.fullname" . }}
{{- end -}}
