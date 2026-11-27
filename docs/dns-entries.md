# DNS entries

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

An on-prem deployment needs internal DNS records for the stack's public
hostnames and for the remote GPU host. This page lists every record, which
profile needs it, and how to generate and check the records from the
configured names. Create the records before the first start, together with
the TLS certificate, which must cover the same names (see
[Getting started](getting-started.md#2-deliver-tls-certificates)).

No hostname is hard-coded. Each public name is a `*_HOST` variable in `.env`
or `.env.optional` and defaults to `<service>.${STACK_DOMAIN}`. Use the
resolved values, never the defaults shown below, in the DNS records and the
certificate SANs:

```sh
eval "$(python3 scripts/maintenance.py stack-env)"
env | grep -E '^(STACK_DOMAIN|[A-Z_]+_HOST)=' | sort
```

## Records for the stack host

Every public name points at the private IP of the Docker host that runs
Traefik. Traefik listens on ports 80 and 443, redirects HTTP to HTTPS, and
routes each request to its service by hostname, so all names share the one
address. Use an `A` record for each name, or a `CNAME` to the host's own
name.

| Variable | Default name | Needed for | Used by |
| --- | --- | --- | --- |
| `LITELLM_PROD_HOST` | `llm.${STACK_DOMAIN}` | `prod` profile | API clients and the production admin UI; also the image URL in LiteLLM emails (`EMAIL_LOGO_URL`) |
| `LITELLM_DEV_HOST` | `llm-dev.${STACK_DOMAIN}` | `dev` profile | API clients and the development admin UI |
| `OPENSEARCH_DASHBOARDS_HOST` | `opensearch.${STACK_DOMAIN}` | Always (core stack) | Operators using OpenSearch Dashboards |
| `GRAFANA_HOST` | `grafana.${STACK_DOMAIN}` | `grafana` profile | Operators using Grafana |
| `LANGFUSE_WEB_HOST` | `langfuse.${STACK_DOMAIN}` | `langfuse` profile | Users of the Langfuse web UI |
| `LANGFUSE_S3_HOST` | `langfuse-s3.${STACK_DOMAIN}` | `langfuse` profile | Signed media upload URLs that Langfuse hands out (`LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT`) |
| `LENS_HOST` | `lens.${STACK_DOMAIN}` | `lens` profile | Users of LiteLLM Lens |
| `DOCS_HOST` | `gateway-deployment-docs.${STACK_DOMAIN}` | `docs` profile | Readers of the deployment's documentation site |

The `prod`, `dev`, and `OPENSEARCH_DASHBOARDS_HOST` variables are required in
`.env`: Compose refuses to render without them. The others are set in
`.env.optional` and matter only when their profile runs; you may still create
their records ahead of time. Publish the records only in the internal DNS
zone that the stack's clients use. The documentation site has no sign-in, so
keep `DOCS_HOST` on the private network; see
[Serve the site from the stack](documentation-site.md#serve-the-site-from-the-stack).

## Records for the remote GPU host

D3-mini and BGE-M3 run on a separate GPU host
([GPU host guide](../gpu-host/README.md)). Their names come from
`gpu-host/.env` and must match the names in the gateway's `.env`:

| GPU host variable | Example | Gateway variable that uses it | Port |
| --- | --- | --- | --- |
| `D3_SERVER_NAME` | `d3-mini.example.test` | `D3_DECISION_API_BASE` (`https://d3-mini.${STACK_DOMAIN}:8443`) | `D3_PUBLIC_PORT`, default `8443` |
| `BGE_M3_SERVER_NAME` | `bge-m3.example.test` | `BGE_M3_API_BASE` (`https://bge-m3.${STACK_DOMAIN}:9443/v1`) | `BGE_M3_PUBLIC_PORT`, default `9443` |

Point both names at the GPU host's private IP. These records must be in DNS:
the LiteLLM containers resolve names through DNS, and entries in the stack
host's `/etc/hosts` are not visible inside them. The GPU host's certificate,
issued by its own mkcert CA, covers the same names, and the gateway trusts it
through `GPU_HOST_CA_BUNDLE`
([Trust the remote GPU host](getting-started.md#trust-the-remote-gpu-host)).

With the optional on-host `d3` profile, `D3_DECISION_API_BASE` is
`http://d3-mini:8000`, a container name, and D3-mini needs no DNS record; see
[On-host D3-mini](d3-mini-on-host.md). The `dev` profile still reaches BGE-M3
on the GPU host.

## Names that need no record

- **Container names**, such as `litellm-prod`, `postgres`, `opensearch`, or
  `d3-mini`, resolve through Docker's embedded DNS on the stack's own
  networks.
- **`host.docker.internal`** is mapped by `extra_hosts: host-gateway` in both
  gateway services, so they can reach a model server on the Docker host, such
  as the vLLM server for `PII_ASSIST_API_BASE`.
- **The Xberg sandbox** has its own resolver, `xberg-dns`, which answers only
  for allowlisted names; see
  [Xberg sandbox](edge-and-abuse-protection.md#xberg-sandbox).

Every other name the containers look up, such as the cloud provider endpoints
of the models in `.env`, goes to the Docker host's resolvers. The stack host's
DNS must therefore resolve those provider names as well as the internal zone.

## Generate the records

Print zone-file lines for the stack host from the configured names. Replace
`STACK_IP` with the Docker host's private IP and drop the lines of profiles
you do not run:

```sh
eval "$(python3 scripts/maintenance.py stack-env)"
STACK_IP=10.0.0.10   # the stack host's private IP
for name in "$LITELLM_PROD_HOST" "$LITELLM_DEV_HOST" "$OPENSEARCH_DASHBOARDS_HOST" \
  "$GRAFANA_HOST" "$LANGFUSE_WEB_HOST" "$LANGFUSE_S3_HOST" "$LENS_HOST" "$DOCS_HOST"; do
  printf '%s. IN A %s\n' "$name" "$STACK_IP"
done
```

Add the GPU host's two names with the GPU host's IP in the same way. On a
single workstation without internal DNS, use `/etc/hosts` instead, as in
[Resolve the hostnames](getting-started.md#3-resolve-the-hostnames); that
works for the stack's own names but not for the GPU host's names, which the
containers must resolve through DNS.

## Check the records

From a client machine, every stack name must resolve to the stack host:

```sh
eval "$(python3 scripts/maintenance.py stack-env)"
for name in "$LITELLM_PROD_HOST" "$LITELLM_DEV_HOST" "$OPENSEARCH_DASHBOARDS_HOST"; do
  getent hosts "$name" 2>/dev/null || dscacheutil -q host -a name "$name"
done
```

From inside a gateway container, the GPU host's names must resolve to the GPU
host (replace the example name with `D3_SERVER_NAME`):

```sh
docker compose exec -T litellm-prod python3 -c \
  'import socket, sys; print(socket.gethostbyname(sys.argv[1]))' d3-mini.example.test
```

Then confirm that the certificate covers the same names:

```sh
openssl x509 -in "${TRAEFIK_TLS_CERT_FILE:-traefik/certs/litellm-stack.pem}" \
  -noout -ext subjectAltName
```

After a hostname changes, update the DNS record, the certificate, and the
`*_HOST` variable together, then recreate Traefik and the services whose
routes changed (`docker compose up -d --force-recreate traefik <service>`).
