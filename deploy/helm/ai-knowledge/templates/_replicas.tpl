{{- define "okf.replicaDeployment" -}}
{{- $root := .root -}}
{{- $role := .role -}}
{{- $port := ternary 8000 8001 (eq $role "api") -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ $role }}
  labels:
    {{- include "okf.labels" $root | nindent 4 }}
spec:
  replicas: {{ (index $root.Values $role).replicas }}
  strategy:
    type: RollingUpdate
    rollingUpdate: {maxSurge: 1, maxUnavailable: 0}
  selector:
    matchLabels: {okf-role: {{ $role }}}
  template:
    metadata:
      labels:
        okf-role: {{ $role }}
        {{- include "okf.labels" $root | nindent 8 }}
      annotations:
        checksum/config: {{ include (print $root.Template.BasePath "/config.yaml") $root | sha256sum }}
    spec:
      serviceAccountName: application
      automountServiceAccountToken: false
      terminationGracePeriodSeconds: {{ $root.Values.terminationGracePeriodSeconds }}
      securityContext:
        runAsNonRoot: true
        runAsUser: {{ $root.Values.security.uid }}
        runAsGroup: {{ $root.Values.security.gid }}
        fsGroup: {{ $root.Values.security.gid }}
        seccompProfile: {type: RuntimeDefault}
      {{- with $root.Values.images.pullSecrets }}
      imagePullSecrets: {{ toYaml . | nindent 8 }}
      {{- end }}
      {{- with $root.Values.nodeSelector }}
      nodeSelector: {{ toYaml . | nindent 8 }}
      {{- end }}
      {{- with $root.Values.tolerations }}
      tolerations: {{ toYaml . | nindent 8 }}
      {{- end }}
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: {{ $root.Values.placement.topologyKey | quote }}
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels:
              okf-role: {{ $role }}
              app.kubernetes.io/instance: {{ $root.Release.Name }}
      containers:
        - name: {{ $role }}
          image: {{ include "okf.image" (dict "image" $root.Values.images.backend "tier" $root.Values.deployment.environment) | quote }}
          imagePullPolicy: {{ $root.Values.images.backend.pullPolicy }}
          {{- if eq $role "worker" }}
          command: [python, -m, app.worker]
          args: [--health-port, "8001", --shutdown-timeout, {{ sub $root.Values.terminationGracePeriodSeconds 10 | quote }}]
          {{- end }}
          securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: [ALL]}}
          ports: [{name: {{ $role }}, containerPort: {{ $port }}}]
          envFrom:
            - secretRef: {name: {{ $root.Values.secrets.existingSecret | quote }}}
            - configMapRef: {name: runtime}
          env:
            - {name: APP_ROLE, value: {{ $role | quote }}}
            - {name: OKF_DEPLOYMENT_SCHEMA, value: replicas-v1}
            - {name: DEPLOYMENT_MODE, value: distributed}
            - name: DEPLOYMENT_NAMESPACE
              valueFrom: {fieldRef: {fieldPath: metadata.namespace}}
            - {name: UVICORN_WORKERS, value: "1"}
            - {name: DATA_DIR, value: /scratch/data}
            - {name: DIAGNOSTICS_DIR, value: /scratch/diagnostics}
            - {name: ARTIFACT_SCRATCH_ROOT, value: /scratch/artifacts}
            - {name: RUNTIME_IDENTITY_FILE, value: /run/okf-identity/identity.json}
            {{- if eq $root.Values.models.mode "stub" }}
            - {name: LLM_PROFILE, value: local_qwen}
            - {name: LLM_BASE_URL, value: "http://model-stub:8000/v1"}
            - {name: LLM_MODEL, value: openai/test-stub}
            - {name: LLM_CHAT_MODEL, value: openai/test-stub}
            - {name: SEARCH_MODE_DEFAULT, value: bm25}
            {{- else if eq $root.Values.models.mode "unavailable" }}
            - {name: LLM_BASE_URL, value: "http://127.0.0.1:1/v1"}
            - {name: EMBEDDING_API_BASE, value: "http://127.0.0.1:1"}
            {{- end }}
            {{- if eq $root.Values.replicaStorage.artifacts.backend "rwx" }}
            - {name: ARTIFACT_ROOT, value: /artifacts}
            {{- end }}
            {{- if $root.Values.trust.existingConfigMap }}
            - {name: SSL_CERT_FILE, value: /etc/okf/trust/ca-bundle.crt}
            - {name: REQUESTS_CA_BUNDLE, value: /etc/okf/trust/ca-bundle.crt}
            {{- end }}
          volumeMounts:
            - {name: scratch, mountPath: /scratch}
            - {name: runtime-identity, mountPath: /run/okf-identity, readOnly: true}
            {{- if eq $root.Values.replicaStorage.artifacts.backend "rwx" }}
            - {name: artifacts, mountPath: /artifacts}
            {{- end }}
            {{- if $root.Values.trust.existingConfigMap }}
            - {name: runtime-trust, mountPath: /etc/okf/trust, readOnly: true}
            {{- end }}
          startupProbe:
            httpGet: {path: /health/live, port: {{ $port }}}
            {{- include "okf.probeTiming" $root.Values.probes.backend.startup | nindent 12 }}
          livenessProbe:
            httpGet: {path: /health/live, port: {{ $port }}}
            {{- include "okf.probeTiming" $root.Values.probes.backend.liveness | nindent 12 }}
          readinessProbe:
            httpGet: {path: /health/ready, port: {{ $port }}}
            {{- include "okf.probeTiming" $root.Values.probes.backend.readiness | nindent 12 }}
          resources: {{ toYaml (index $root.Values.resources $role) | nindent 12 }}
      volumes:
        - name: scratch
          emptyDir: {sizeLimit: {{ $root.Values.scratch.sizeLimit | quote }}}
        - name: runtime-identity
          configMap:
            name: {{ required "Distributed runtime.identityConfigMap is required" $root.Values.runtime.identityConfigMap | quote }}
            items: [{key: identity.json, path: identity.json}]
        {{- if eq $root.Values.replicaStorage.artifacts.backend "rwx" }}
        - name: artifacts
          persistentVolumeClaim:
            claimName: {{ required "RWX persistence.data.existingClaim is required" $root.Values.persistence.data.existingClaim | quote }}
        {{- end }}
        {{- if $root.Values.trust.existingConfigMap }}
        - name: runtime-trust
          configMap:
            name: {{ $root.Values.trust.existingConfigMap | quote }}
            items: [{key: ca-bundle.crt, path: ca-bundle.crt}]
        {{- end }}
{{- end -}}
