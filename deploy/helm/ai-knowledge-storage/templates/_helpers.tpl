{{- define "storage.validate" -}}
{{- if ne .Values.profile "basic" -}}{{- fail "Resilient storage requires qualified multi-domain recipes" -}}{{- end -}}
{{- if not (regexMatch "^[0-9a-f]{32}$" .Values.installationId) -}}{{- fail "Explicit installationId required" -}}{{- end -}}
{{- range $name := list "postgres" "qdrant" "artifacts" -}}
{{- $component := index $.Values $name -}}
{{- if and (eq $component.provider "external") $component.provision -}}{{- fail "External storage must not provision data" -}}{{- end -}}
{{- if eq $component.provider "managed" -}}
{{- $_ := required (printf "%s claim name required" $name) $component.claim.name -}}
{{- $_ := required (printf "%s existingSecret required" $name) $component.existingSecret -}}
{{- if and (eq $name "artifacts") (ne $component.backend "s3") -}}{{- fail "Managed RWX requires a separately approved platform recipe" -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "storage.labels" -}}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: Helm
okf-installation-id: {{ .Values.installationId | quote }}
{{- end -}}
