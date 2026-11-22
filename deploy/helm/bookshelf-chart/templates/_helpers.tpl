{{/* The app's name: the chart is published as bookshelf-chart (quay.io/enricobianchi/bookshelf is the image). */}}
{{- define "bookshelf.name" -}}
bookshelf
{{- end }}

{{- define "bookshelf.fullname" -}}
{{- $name := include "bookshelf.name" . }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "bookshelf.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: bookshelf
{{- end }}

{{- define "bookshelf.selectorLabels" -}}
app.kubernetes.io/name: {{ include "bookshelf.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "bookshelf.firebird.fullname" -}}
{{- printf "%s-firebird" (include "bookshelf.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "bookshelf.firebird.selectorLabels" -}}
app.kubernetes.io/name: firebird
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "bookshelf.image" -}}
{{- printf "%s:%s" .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) }}
{{- end }}

{{- define "bookshelf.secretName" -}}
{{- .Values.existingSecret | default (include "bookshelf.fullname" .) }}
{{- end }}

{{- define "bookshelf.dbDsn" -}}
{{- if .Values.firebird.enabled }}
{{- printf "%s:/var/lib/firebird/data/bookshelf.fdb" (include "bookshelf.firebird.fullname" .) }}
{{- else }}
{{- required "database.dsn is required unless firebird.enabled" .Values.database.dsn }}
{{- end }}
{{- end }}

{{/* The app's settings: the ConfigMap and the Secret's keys. */}}
{{- define "bookshelf.env" -}}
envFrom:
  - configMapRef:
      name: {{ include "bookshelf.fullname" . }}
env:
  {{- $keys := list "APP_SECRET_KEY" "APP_DB_USER" "APP_DB_PASSWORD" }}
  {{- if .Values.google.clientId }}
  {{- $keys = append $keys "APP_GOOGLE_CLIENT_SECRET" }}
  {{- end }}
  {{- range $keys }}
  - name: {{ . }}
    valueFrom:
      secretKeyRef:
        name: {{ include "bookshelf.secretName" $ }}
        key: {{ . }}
  {{- end }}
{{- end }}

{{/* appuser in the image */}}
{{- define "bookshelf.podSecurityContext" -}}
runAsUser: 8877
runAsGroup: 8877
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "bookshelf.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end }}

{{/* A one-off app container running a flask CLI command (init-db, ...). */}}
{{- define "bookshelf.cliPodSpec" -}}
restartPolicy: Never
{{- with .root.Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
securityContext:
  {{- include "bookshelf.podSecurityContext" .root | nindent 2 }}
containers:
  - name: {{ .command }}
    image: {{ include "bookshelf.image" .root }}
    imagePullPolicy: {{ .root.Values.image.pullPolicy }}
    command: ["/app/venv/bin/flask", "--app", "bookshelf", {{ .command | quote }}]
    {{- include "bookshelf.env" .root | nindent 4 }}
    securityContext:
      {{- include "bookshelf.containerSecurityContext" .root | nindent 6 }}
    resources:
      requests:
        cpu: 50m
        memory: 128Mi
      limits:
        memory: 256Mi
{{- end }}
