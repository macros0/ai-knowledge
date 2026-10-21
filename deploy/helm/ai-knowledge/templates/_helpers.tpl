{{- define "okf.image" -}}
{{- if .image.digest -}}
{{- printf "%s@%s" .image.repository .image.digest -}}
{{- else if and (has .tier (list "home" "ci")) .image.tag -}}
{{- printf "%s:%s" .image.repository .image.tag -}}
{{- else -}}
{{- fail "Immutable image digest required (home/ci may use explicit local tags)" -}}
{{- end -}}
{{- end -}}
{{- define "okf.labels" -}}
app.kubernetes.io/part-of: ai-knowledge
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "okf.validate" -}}
{{- if not (kindIs "bool" .Values.runtime.auditDbEnabled) -}}
{{- fail "runtime.auditDbEnabled must be a boolean" -}}
{{- end -}}
{{- if and (not .Values.runtime.auditDbEnabled) (ne .Values.runtime.auditStdoutMode "durable") -}}
{{- fail "Audit cannot be disabled: auditDbEnabled=false requires auditStdoutMode=durable" -}}
{{- end -}}
{{- if and (eq .Values.deployment.environment "prod") (ne .Values.models.mode "live") -}}
{{- fail "PROD requires live models; stub/unavailable forbidden" -}}
{{- end -}}
{{- if and (has .Values.deployment.environment (list "dev" "test" "prod")) (ne .Values.runtime.environment "production") -}}
{{- fail "DEV/TEST/PROD require production application protections" -}}
{{- end -}}
{{- if .Values.trust.existingConfigMap -}}
{{- range $key := list "SSL_CERT_FILE" "REQUESTS_CA_BUNDLE" "NODE_EXTRA_CA_CERTS" "SSL_VERIFY" "NODE_TLS_REJECT_UNAUTHORIZED" -}}
{{- if hasKey $.Values.runtime.config $key -}}{{- fail (printf "Runtime trust controls TLS key %s" $key) -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- range $key, $value := .Values.runtime.config -}}
{{- if and (eq $key "KEYCLOAK_TOKEN_ENDPOINT_AUTH_METHOD") (not (has $value (list "client_secret_basic" "client_secret_post" "none"))) -}}
{{- fail "Invalid OIDC token authentication method" -}}
{{- end -}}
{{- if or (not (regexMatch "^[A-Z][A-Z0-9_]*$" $key)) (and (regexMatch "(?i)(password|secret|token|api_key|database_url)" $key) (ne $key "KEYCLOAK_TOKEN_ENDPOINT_AUTH_METHOD")) (has $key (list "ENVIRONMENT" "DEPLOYMENT_TIER" "MODEL_MODE" "EMBEDDING_PROVIDER" "UVICORN_WORKERS" "STORAGE_MODE" "DATA_DIR" "QDRANT_URL" "AUDIT_DB_ENABLED" "AUDIT_STDOUT_MODE" "AUDIT_STDOUT_FORMAT")) -}}
{{- fail (printf "Runtime config key %s is secret or chart-controlled" $key) -}}
{{- end -}}
{{- end -}}
{{- $request := include "okf.durationMilliseconds" .Values.gateway.requestTimeout | int64 -}}
{{- $backend := include "okf.durationMilliseconds" .Values.gateway.backendRequestTimeout | int64 -}}
{{- if and (gt $request 0) (gt $backend $request) -}}
{{- fail "gateway.backendRequestTimeout exceeds gateway.requestTimeout" -}}
{{- end -}}
{{- if and .Values.gateway.trafficPolicy.enabled (not .Values.gateway.enabled) -}}
{{- fail "gateway.trafficPolicy requires gateway.enabled" -}}
{{- end -}}
{{- if or (eq $request 0) (eq $backend 0) -}}
{{- if not (and .Values.gateway.allowUnboundedTimeouts .Values.gateway.enabled .Values.gateway.trafficPolicy.enabled) -}}
{{- fail "gateway 0s requires allowUnboundedTimeouts, Gateway and finite TrafficPolicy idle" -}}
{{- end -}}
{{- end -}}
{{- if .Values.gateway.enabled -}}
{{- $_ := required "gateway.hostname required" .Values.gateway.hostname -}}
{{- $_ := required "gateway.certificateSecret required" .Values.gateway.certificateSecret -}}
{{- end -}}
{{- end -}}

{{- define "okf.probeTiming" -}}
initialDelaySeconds: {{ .initialDelaySeconds }}
periodSeconds: {{ .periodSeconds }}
timeoutSeconds: {{ .timeoutSeconds }}
failureThreshold: {{ .failureThreshold }}
successThreshold: 1
{{- end -}}

{{- define "okf.durationMilliseconds" -}}
{{- if eq . "0s" -}}0
{{- else -}}
{{- if not (regexMatch "^[0-9]{1,5}(ms|s|m|h)$" .) -}}
{{- fail "Invalid gateway duration; use positive single-unit ms/s/m/h or explicit 0s" -}}
{{- end -}}
{{- $amount := regexFind "^[0-9]+" . | int64 -}}
{{- if le $amount 0 -}}{{- fail "Only 0s is supported as a zero duration" -}}{{- end -}}
{{- $unit := regexFind "(ms|s|m|h)$" . -}}
{{- $factor := get (dict "ms" 1 "s" 1000 "m" 60000 "h" 3600000) $unit | int64 -}}
{{- mul $amount $factor -}}
{{- end -}}
{{- end -}}
