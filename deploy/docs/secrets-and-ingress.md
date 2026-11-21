# Secrets, TLS, and ingress

[← Back to the README](../README.md#documentation).

## Credentials

Every credential is read from one Secret through `secretKeyRef`, so none appears
in a pod spec, in `helm get manifest`, or in `kubectl describe` output. The
Secret is named by `secrets.existingSecret` when set, otherwise
`<fullname>-secrets`.

### Existing Secret (recommended for production)

Set `secrets.existingSecret` to a Secret you manage with External Secrets,
Vault, or SOPS. The chart then renders no Secret and ignores `secrets.values`.
The Secret must contain every key listed under `secrets.values` in
`values.yaml`. A missing key leaves the pods that reference it in
`CreateContainerConfigError`.

### Chart-rendered Secret

When `existingSecret` is empty the chart renders a Secret with one entry per key
in `secrets.values`, resolved in this order:

1. the value set in `secrets.values`;
2. the value already stored in the cluster, found with `lookup`, so an upgrade
   never rotates a password the running pods and volumes still expect;
3. a generated value, when `secrets.generateMissing` is true.

Generated values follow the formats the upstream `.env.example` enforces:

| Key | Generated as |
| --- | --- |
| `LITELLM_MASTER_KEY`, `LENS_GATEWAY_API_KEY` | `sk-` followed by 40 lowercase alphanumerics |
| `OPENAI_API_KEY` | The placeholder `replace_with_your_openai_api_key`, so requests fail closed |
| `OPENSEARCH_DATASOURCES_MASTER_KEY` | Exactly 32 characters |
| `LANGFUSE_ENCRYPTION_KEY` | Exactly 64 hexadecimal characters |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | `pk-lf-…` and `sk-lf-…` |
| `LITELLM_LENS_SERVICE_TOKEN`, `LENS_GATEWAY_SECRET`, `LENS_ADMIN_TOKEN` | 64 alphanumerics each |
| `OPENSEARCH_DASHBOARDS_COOKIE_PASSWORD` | 40 alphanumerics |
| `LANGFUSE_NEXTAUTH_SECRET` | 48 alphanumerics |
| `LANGFUSE_INIT_USER_EMAIL` | `admin@<global.domain>` |
| `LANGFUSE_INIT_USER_NAME` | `admin` |
| Everything else | 32 alphanumerics |

Supply your own values when you bring them with you, for example:

```sh
helm upgrade "$RELEASE" "$CHART" --reuse-values \
  --set-string secrets.values.OPENAI_API_KEY="$OPENAI_API_KEY"
```

Prefer a values file or `existingSecret` to `--set` for anything long-lived,
because command lines end up in shell history.

The chart rejects a rendering where `LITELLM_LENS_SERVICE_TOKEN`,
`LENS_GATEWAY_SECRET`, and `LENS_ADMIN_TOKEN` are not three different values.

The rendered Secret carries `helm.sh/resource-policy: keep`. It survives
`helm uninstall`, because the volumes it protects survive too.

### Rotation

Change a value in the Secret (or in `secrets.values`), then restart the workloads
that read it. Passwords for stateful services such as PostgreSQL and OpenSearch
are also stored inside their volumes; rotating the Secret alone does not change
what the database already holds. Follow the upstream
[environment variable reference](https://github.com/bella-1337/effective-spoon/blob/master/docs/environment-variables.md)
for the rotation procedure and change impact of each variable, since the
variable names are the same.

## Hostnames

A public hostname is `<service>.<global.domain>` unless overridden:

| Service | Default host | Override |
| --- | --- | --- |
| Production gateway | `llm.<domain>` | `litellmProd.host` |
| Development gateway | `llm-dev.<domain>` | `litellmDev.host` |
| OpenSearch Dashboards | `opensearch.<domain>` | `ingress.hosts.opensearchDashboards` |
| Grafana | `grafana.<domain>` | `grafana.host` or `ingress.hosts.grafana` |
| Langfuse web | `langfuse.<domain>` | `langfuse.webHost` or `ingress.hosts.langfuse` |
| Langfuse S3 | `langfuse-s3.<domain>` | `langfuse.s3Host` or `ingress.hosts.langfuseS3` |
| Lens | `lens.<domain>` | `lens.host` or `ingress.hosts.lens` |

Only components that are enabled get a host. Use a domain you control and can
issue certificates for; `.test` is reserved for testing.

## Ingress

The chart does not deploy an edge proxy. The upstream Traefik container is
replaced by one Ingress resource that lists every enabled host, so your
cluster's ingress controller terminates TLS.

### TLS

`ingress.tls.enabled` (default `true`) adds every host to a single `tls` entry
using `ingress.tls.secretName`. Provide the certificate Secret out of band, or
annotate the Ingress for cert-manager through `ingress.annotations` and let it
create the Secret. The certificate must cover every enabled host.

### Annotations

`ingress.annotations` defaults to ingress-nginx settings that mirror the upstream
Traefik middleware:

| Annotation | Mirrors |
| --- | --- |
| `ssl-redirect: "true"` | HTTP to HTTPS redirect |
| `proxy-body-size: 10m` | Request body limit |
| `configuration-snippet` with `X-Content-Type-Options`, `Referrer-Policy`, and `Strict-Transport-Security` | `edge-security-headers` |
| `limit-rps: "500"` and `limit-burst-multiplier: "4"` | `edge-rate-limit` |

Two cautions:

- Recent ingress-nginx releases disable `configuration-snippet` unless
  `allow-snippet-annotations` is enabled on the controller. If the Ingress is
  rejected, either enable snippets or move the three headers into the
  controller's own configuration.
- For another controller (Traefik, HAProxy, a cloud load balancer), set
  `ingress.className` and replace `ingress.annotations` with the equivalent
  headers and rate limits. Upstream also removed client addresses and request
  headers from the edge access log; configure the same on your controller.

The upstream stack documented its edge hardening in
[Edge and abuse protection](https://github.com/bella-1337/effective-spoon/blob/master/docs/edge-and-abuse-protection.md).
That guide describes Traefik, but the intent (TLS policy, HSTS, per-client rate
limits) applies to whichever controller you use.
