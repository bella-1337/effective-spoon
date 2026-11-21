{{/*
Common template helpers.

The central rule here is image resolution: every workload calls
litellm-stack.image, which layers the per-image registry, the global in-house
registry override, and the digest/tag choice in one place. No template builds an
image reference by hand.
*/}}

{{- define "litellm-stack.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "litellm-stack.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "litellm-stack.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Selector labels. These must stay stable across upgrades: they are the Service
selectors and the Deployment selector.matchLabels.
*/}}
{{- define "litellm-stack.selectorLabels" -}}
app.kubernetes.io/name: {{ include "litellm-stack.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "litellm-stack.labels" -}}
helm.sh/chart: {{ include "litellm-stack.chart" . }}
{{ include "litellm-stack.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: litellm-stack
{{- end -}}

{{/*
Per-component labels. Call with a dict: (dict "context" $ "component" "litellm-prod").
*/}}
{{- define "litellm-stack.componentLabels" -}}
{{ include "litellm-stack.labels" .context }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "litellm-stack.componentSelectorLabels" -}}
{{ include "litellm-stack.selectorLabels" .context }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
Resolve an image. Call with a dict:
  (dict "context" $ "image" .Values.images.opensearch)

Resolution order for the registry, first non-empty wins:
  1. the image's own registry
  2. global.imageRegistry (the additional in-house registry)
  3. docker.io implicit

A digest is used when useDigest is true and one is set, so a rebuilt tag cannot
change what a release runs. Set useDigest=false to fall back to the tag.
*/}}
{{- define "litellm-stack.image" -}}
{{- $ctx := .context -}}
{{- $image := .image -}}
{{- if not $image -}}
{{- fail "litellm-stack.image: missing image definition" -}}
{{- end -}}
{{- $registry := $image.registry -}}
{{- if $ctx.Values.global.imageRegistry -}}
{{- $registry = $ctx.Values.global.imageRegistry -}}
{{- end -}}
{{- $ref := $image.repository -}}
{{- if $registry -}}
{{- $ref = printf "%s/%s" $registry $image.repository -}}
{{- end -}}
{{- if and $ctx.Values.useDigest $image.digest -}}
{{- printf "%s@%s" $ref $image.digest -}}
{{- else -}}
{{- printf "%s:%s" $ref (toString $image.tag) -}}
{{- end -}}
{{- end -}}

{{/*
The Secret holding every credential. Either the operator-supplied existing
Secret or the one this chart renders.
*/}}
{{- define "litellm-stack.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-secrets" (include "litellm-stack.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
Reference one key of the secrets Secret. Using secretKeyRef everywhere keeps
credentials out of pod specs, out of `helm get manifest`, and out of kubectl
describe output.
*/}}
{{- define "litellm-stack.secretEnv" -}}
{{- $name := include "litellm-stack.secretName" .context -}}
{{- dict "name" .key "valueFrom" (dict "secretKeyRef" (dict "name" $name "key" .key)) -}}
{{- end -}}

{{/*
Public hostname for a service. An explicit host wins, otherwise
<service>.<global.domain>.
Call with a dict: (dict "context" $ "service" "llm" "override" .Values.litellmProd.host).
*/}}
{{- define "litellm-stack.host" -}}
{{- if .override -}}
{{- .override -}}
{{- else -}}
{{- printf "%s.%s" .service .context.Values.global.domain -}}
{{- end -}}
{{- end -}}

{{/*
In-cluster DNS name of a component's Service.
*/}}
{{- define "litellm-stack.serviceHost" -}}
{{- printf "%s-%s" (include "litellm-stack.fullname" .context) .component -}}
{{- end -}}

{{/*
Full image pull secrets, including any global ones.
*/}}
{{- define "litellm-stack.imagePullSecrets" -}}
{{- with .Values.global.imagePullSecrets }}
imagePullSecrets:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
Merge global nodeSelector/tolerations/affinity with a component's own values.
Call with a dict: (dict "context" $ "component" <component values mapping>).
*/}}
{{- define "litellm-stack.nodeSelector" -}}
{{- $merged := merge (deepCopy (.component.nodeSelector | default dict)) .context.Values.global.nodeSelector -}}
{{- with $merged }}
nodeSelector:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "litellm-stack.tolerations" -}}
{{- $merged := concat (.context.Values.global.tolerations | default list) (.component.tolerations | default list) -}}
{{- with $merged }}
tolerations:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "litellm-stack.affinity" -}}
{{- $affinity := .component.affinity | default .context.Values.global.affinity -}}
{{- with $affinity }}
affinity:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
A stable spread across nodes and zones for replicated gateways, so a single node
loss cannot take every proxy replica with it. Only rendered when the component
asks for it and runs more than one replica.
*/}}
{{- define "litellm-stack.topologySpread" -}}
{{- if and .enable (gt (int .replicas) 1) }}
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        {{- include "litellm-stack.componentSelectorLabels" (dict "context" .context "component" .component) | nindent 8 }}
  - maxSkew: 1
    topologyKey: topology.kubernetes.io/zone
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        {{- include "litellm-stack.componentSelectorLabels" (dict "context" .context "component" .component) | nindent 8 }}
{{- end }}
{{- end -}}

{{/*
Effective storage class for a PVC, honoring global.storageClass.
*/}}
{{- define "litellm-stack.storageClass" -}}
{{- $class := .local | default .context.Values.global.storageClass -}}
{{- with $class -}}
storageClassName: {{ . | quote }}
{{- end -}}
{{- end -}}

{{/*
Generate one credential, base64 encoded for a Secret's data field.
Call with a dict: (dict "key" "PG_PASSWORD" "context" $).

Alphabets and lengths follow the upstream .env.example rules so the generated
value passes the same validation the Compose stack applies:
  - OpenSearch passwords accept only [A-Za-z0-9._~-] and need 16+ characters.
  - OPENSEARCH_DATASOURCES_MASTER_KEY is exactly 32 characters.
  - LANGFUSE_ENCRYPTION_KEY is exactly 64 hexadecimal characters.
*/}}
{{- define "litellm-stack.generateSecret" -}}
{{- $key := .key -}}
{{- $value := "" -}}
{{- if eq $key "LITELLM_MASTER_KEY" -}}
{{- $value = printf "sk-%s" (randAlphaNum 40 | lower) -}}
{{- else if eq $key "OPENAI_API_KEY" -}}
{{- /* Fail closed: an obvious placeholder rather than a plausible fake key. */ -}}
{{- $value = "replace_with_your_openai_api_key" -}}
{{- else if eq $key "OPENSEARCH_DATASOURCES_MASTER_KEY" -}}
{{- $value = randAlphaNum 32 -}}
{{- else if eq $key "LANGFUSE_ENCRYPTION_KEY" -}}
{{- /* sha256 renders 32 random bytes as exactly 64 lowercase hex characters. */ -}}
{{- $value = randAlphaNum 32 | sha256sum -}}
{{- else if eq $key "LITELLM_LENS_SERVICE_TOKEN" -}}
{{- $value = randAlphaNum 64 -}}
{{- else if eq $key "LENS_GATEWAY_SECRET" -}}
{{- $value = randAlphaNum 64 -}}
{{- else if eq $key "LENS_ADMIN_TOKEN" -}}
{{- $value = randAlphaNum 64 -}}
{{- else if eq $key "LENS_GATEWAY_API_KEY" -}}
{{- $value = printf "sk-%s" (randAlphaNum 40 | lower) -}}
{{- else if eq $key "LANGFUSE_PUBLIC_KEY" -}}
{{- $value = printf "pk-lf-%s" (randAlphaNum 32) -}}
{{- else if eq $key "LANGFUSE_SECRET_KEY" -}}
{{- $value = printf "sk-lf-%s" (randAlphaNum 32) -}}
{{- else if eq $key "LANGFUSE_INIT_USER_EMAIL" -}}
{{- $value = printf "admin@%s" .context.Values.global.domain -}}
{{- else if eq $key "LANGFUSE_INIT_USER_NAME" -}}
{{- $value = "admin" -}}
{{- else if eq $key "OPENSEARCH_DASHBOARDS_COOKIE_PASSWORD" -}}
{{- $value = randAlphaNum 40 -}}
{{- else if eq $key "LANGFUSE_NEXTAUTH_SECRET" -}}
{{- $value = randAlphaNum 48 -}}
{{- else -}}
{{- $value = randAlphaNum 32 -}}
{{- end -}}
{{- $value | b64enc -}}
{{- end -}}

{{/*
Prometheus scrape configuration.

Upstream ships a static file. Here the target list is derived from the enabled
components, so a release without the dev profile has no permanently failing
`litellm-dev` target and a release without Lens does not scrape for it.
*/}}
{{- define "litellm-stack.prometheusConfig" -}}
global:
  scrape_interval: 15s

scrape_configs:
{{- if .Values.litellmProd.enabled }}
  - job_name: litellm-prod
    metrics_path: /metrics
    static_configs: [{ targets: ["{{ include "litellm-stack.serviceHost" (dict "context" . "component" "litellm-prod") }}:4000"] }]
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/secrets/litellm-master-key
{{- end }}
{{- if .Values.litellmDev.enabled }}
  - job_name: litellm-dev
    metrics_path: /metrics
    static_configs: [{ targets: ["{{ include "litellm-stack.serviceHost" (dict "context" . "component" "litellm-dev") }}:4000"] }]
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/secrets/litellm-master-key
{{- end }}
{{- if .Values.prometheus.exporters.postgres }}
  - job_name: postgres
    static_configs: [{ targets: ["{{ include "litellm-stack.serviceHost" (dict "context" . "component" "postgres-exporter") }}:9187"] }]
{{- end }}
{{- if and .Values.prometheus.exporters.pgvector .Values.litellmDev.enabled }}
  - job_name: pgvector
    static_configs: [{ targets: ["{{ include "litellm-stack.serviceHost" (dict "context" . "component" "pgvector-exporter") }}:9187"] }]
{{- end }}
{{- if .Values.prometheus.nodeExporter.enabled }}
  - job_name: node
    static_configs: [{ targets: ["{{ include "litellm-stack.serviceHost" (dict "context" . "component" "node-exporter") }}:9100"] }]
{{- end }}
{{- if .Values.grafana.enabled }}
  - job_name: langfuse-clickhouse
    static_configs: [{ targets: ["{{ include "litellm-stack.serviceHost" (dict "context" . "component" "langfuse-clickhouse") }}:9363"] }]
{{- end }}
{{- if .Values.lens.enabled }}
  # The readiness probe exists only while Lens resolves, so a release without
  # the lens profile has no failing target.
  - job_name: lens
    metrics_path: /probe
    params: { module: [http_2xx] }
    static_configs:
      - targets: ["http://{{ include "litellm-stack.serviceHost" (dict "context" . "component" "lens") }}:4318/health/ready"]
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: {{ include "litellm-stack.serviceHost" (dict "context" . "component" "blackbox") }}:9115
{{- end }}
{{- if .Values.prometheus.exporters.blackbox }}
  - job_name: blackbox-http
    metrics_path: /probe
    params: { module: [http_2xx] }
    static_configs:
      - targets:
          - http://{{ include "litellm-stack.serviceHost" (dict "context" . "component" "presidio-analyzer") }}:3000/health
          - http://{{ include "litellm-stack.serviceHost" (dict "context" . "component" "presidio-anonymizer") }}:3000/health
          - http://{{ include "litellm-stack.serviceHost" (dict "context" . "component" "searxng") }}:8080/
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: {{ include "litellm-stack.serviceHost" (dict "context" . "component" "blackbox") }}:9115
{{- end }}
{{- if .Values.prometheus.kubernetesTargets.enabled }}
  - job_name: kubernetes-kubelet
    scheme: https
    tls_config:
      ca_file: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
    bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
    kubernetes_sd_configs:
      - role: node
    relabel_configs:
      - action: labelmap
        regex: __meta_kubernetes_node_label_(.+)
{{- end }}
{{- end -}}

{{- /* Shared Langfuse application environment. */}}
{{- define "litellm-stack.langfuseEnv" -}}
{{- $root := .root -}}
{{- $secret := .secret -}}
{{- $webHost := .webHost -}}
{{- $pgHost := .pgHost -}}
{{- $chHost := .chHost -}}
{{- $minioHost := .minioHost -}}
{{- $redisHost := .redisHost -}}
- name: NEXTAUTH_URL
  value: https://{{ $webHost }}
- name: DATABASE_URL
  value: postgresql://langfuse:$(LANGFUSE_POSTGRES_PASSWORD)@{{ $pgHost }}:5432/langfuse
- name: LANGFUSE_POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: LANGFUSE_POSTGRES_PASSWORD
- name: CLICKHOUSE_MIGRATION_URL
  value: clickhouse://{{ $chHost }}:9000
- name: CLICKHOUSE_URL
  value: http://{{ $chHost }}:8123
- name: CLICKHOUSE_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: LANGFUSE_CLICKHOUSE_PASSWORD
- name: LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: LANGFUSE_MINIO_PASSWORD
- name: LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: LANGFUSE_MINIO_PASSWORD
- name: LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT
  value: http://{{ $minioHost }}:9000
- name: LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT
  value: http://{{ $minioHost }}:9000
- name: REDIS_HOST
  value: {{ $redisHost }}
- name: REDIS_AUTH
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: LANGFUSE_REDIS_AUTH
- name: SALT
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: LANGFUSE_SALT
- name: ENCRYPTION_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: LANGFUSE_ENCRYPTION_KEY
- name: TELEMETRY_ENABLED
  value: {{ $root.Values.langfuse.telemetryEnabled | quote }}
- name: LANGFUSE_ENABLE_EXPERIMENTAL_FEATURES
  value: {{ $root.Values.langfuse.enableExperimentalFeatures | quote }}
- name: LANGFUSE_S3_BATCH_EXPORT_ENABLED
  value: {{ $root.Values.langfuse.s3BatchExportEnabled | quote }}
- name: REDIS_TLS_ENABLED
  value: {{ $root.Values.langfuse.redisTlsEnabled | quote }}
- name: HOSTNAME
  value: 0.0.0.0
- name: TZ
  value: UTC
{{- end -}}

{{- /* Pod selector for one component. */ -}}
{{- define "litellm-stack.podSelector" -}}
{{- $labels := include "litellm-stack.componentSelectorLabels" (dict "context" .context "component" .component) -}}
{{- $labels | fromYaml | toYaml -}}
{{- end -}}
{{- /*
DNS egress: any resolver, since the cluster DNS IP is not knowable at render time
and varies per distribution.
*/}}
{{- define "litellm-stack.dnsEgress" -}}
- to:
    - namespaceSelector: {}
      podSelector:
        matchLabels:
          k8s-app: kube-dns
    - namespaceSelector: {}
      podSelector:
        matchLabels:
          k8s-app: coredns
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: kube-system
  ports:
    - protocol: UDP
      port: 53
    - protocol: TCP
      port: 53
{{- end -}}

{{/*
NetworkPolicy peers for one flow.
Call with a dict: (dict "context" $ "peers" "any"|"namespace"|(list "component" ...)).
*/}}
{{- define "litellm-stack.policyPeers" -}}
{{- $peers := .peers -}}
{{- if kindIs "string" $peers -}}
{{- if eq $peers "any" -}}
- ipBlock:
    cidr: 0.0.0.0/0
{{- else if eq $peers "namespace" -}}
- podSelector: {}
{{- else -}}
{{- fail (printf "policyPeers: unknown peer selector %q" $peers) -}}
{{- end -}}
{{- else -}}
{{- range $peers }}
- podSelector:
    matchLabels:
      app.kubernetes.io/name: {{ include "litellm-stack.name" $.context }}
      app.kubernetes.io/instance: {{ $.context.Release.Name }}
      app.kubernetes.io/component: {{ . }}
{{- end }}
{{- end -}}
{{- end -}}

{{/*
NetworkPolicy ports for one flow.
Call with a dict: (dict "ports" (list (dict "port" 3000 "protocol" "TCP"))).
*/}}
{{- define "litellm-stack.policyPorts" -}}
{{- range .ports }}
- protocol: {{ .protocol }}
  port: {{ .port }}
{{- end -}}
{{- end -}}
