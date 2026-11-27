<!-- human-version-of: docs/dns-entries.md sha256: 673c270d28cbcef83f5c719421010ccda5be42b2fda69555f63b0ed19b9b7bbe -->

# DNS entries

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

Use this page when you deploy the stack on-prem and need to ask for, or
create, internal DNS records. It lists every record, says which profile needs
it, and shows how to generate and check them. Do this before the first start,
at the same time as the TLS certificate, which must cover the same names (see
[Getting started](getting-started.md#2-deliver-tls-certificates)).

In short:

- All of the stack's own names point at **one address**: the Docker host that
  runs Traefik.
- The GPU host's two names point at **the GPU host**, and they must be in
  real DNS, not `/etc/hosts`.
- Container names need no records.

Nothing is hard-coded. Each public name is a `*_HOST` variable in `.env` or
`.env.optional`, and defaults to `<service>.${STACK_DOMAIN}`. Put the values
you actually configured into DNS and into the certificate, not the defaults
shown below. Print them with:

```sh
eval "$(python3 scripts/maintenance.py stack-env)"
env | grep -E '^(STACK_DOMAIN|[A-Z_]+_HOST)=' | sort
```

## Records for the stack host

Point every name in this table at the private IP of the Docker host that runs
Traefik. Traefik listens on ports 80 and 443, sends HTTP on to HTTPS, and
picks the service by hostname, so all the names share one address. Use an `A`
record per name, or a `CNAME` to the host's own name.

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

The first three are required in `.env`, and Compose won't render without
them. The rest live in `.env.optional` and only matter when their profile
runs, though you can create their records ahead of time.

Publish these records only in the internal zone the stack's clients use.

!!! warning
    The documentation site has no sign-in. Keep `DOCS_HOST` on the private
    network; see
    [Serve the site from the stack](documentation-site.md#serve-the-site-from-the-stack).

## Records for the remote GPU host

D3-mini and BGE-M3 run on a separate GPU host
([GPU host guide](../gpu-host/README.md)). You set their names in
`gpu-host/.env`, and the gateway's `.env` must use the same names:

| GPU host variable | Example | Gateway variable that uses it | Port |
| --- | --- | --- | --- |
| `D3_SERVER_NAME` | `d3-mini.example.test` | `D3_DECISION_API_BASE` (`https://d3-mini.${STACK_DOMAIN}:8443`) | `D3_PUBLIC_PORT`, default `8443` |
| `BGE_M3_SERVER_NAME` | `bge-m3.example.test` | `BGE_M3_API_BASE` (`https://bge-m3.${STACK_DOMAIN}:9443/v1`) | `BGE_M3_PUBLIC_PORT`, default `9443` |

Point both names at the GPU host's private IP.

!!! warning "DNS, not /etc/hosts"
    The LiteLLM containers resolve names through DNS. They can't see the stack
    host's `/etc/hosts`, so a host-file entry for these names won't work.

The GPU host's certificate comes from its own mkcert CA and covers the same
names. The gateway trusts it through `GPU_HOST_CA_BUNDLE`; see
[Trust the remote GPU host](getting-started.md#trust-the-remote-gpu-host).

If you run D3-mini on the stack host with the optional `d3` profile,
`D3_DECISION_API_BASE` is `http://d3-mini:8000`. That is a container name, so
D3-mini needs no record; see [On-host D3-mini](d3-mini-on-host.md). The `dev`
profile still reaches BGE-M3 on the GPU host.

## Names that need no record

- Container names, such as `litellm-prod`, `postgres`, `opensearch`, or
  `d3-mini`. Docker's embedded DNS resolves them on the stack's own networks.
- `host.docker.internal`. Both gateway services map it with
  `extra_hosts: host-gateway`, so they can reach a model server on the Docker
  host, such as the vLLM server for `PII_ASSIST_API_BASE`.
- Names inside the Xberg sandbox. It has its own resolver, `xberg-dns`, which
  answers only for allowlisted names; see
  [Xberg sandbox](edge-and-abuse-protection.md#xberg-sandbox).

Any other name a container looks up goes to the Docker host's resolvers. That
includes the cloud provider endpoints of the models in `.env`, so the stack
host's DNS has to resolve those provider names as well as your internal zone.

## Generate the records

This prints zone-file lines for the stack host from the names you configured.
Set `STACK_IP` to the Docker host's private IP, and drop the lines for
profiles you don't run:

```sh
eval "$(python3 scripts/maintenance.py stack-env)"
STACK_IP=10.0.0.10   # the stack host's private IP
for name in "$LITELLM_PROD_HOST" "$LITELLM_DEV_HOST" "$OPENSEARCH_DASHBOARDS_HOST" \
  "$GRAFANA_HOST" "$LANGFUSE_WEB_HOST" "$LANGFUSE_S3_HOST" "$LENS_HOST" "$DOCS_HOST"; do
  printf '%s. IN A %s\n' "$name" "$STACK_IP"
done
```

Add the GPU host's two names the same way, with the GPU host's IP.

No internal DNS, just a single workstation? Use `/etc/hosts` as described in
[Resolve the hostnames](getting-started.md#3-resolve-the-hostnames). That
covers the stack's own names, but not the GPU host's names, which the
containers must resolve through DNS.

## Check the records

1. From a client machine, check that the stack names resolve to the stack
   host:

    ```sh
    eval "$(python3 scripts/maintenance.py stack-env)"
    for name in "$LITELLM_PROD_HOST" "$LITELLM_DEV_HOST" "$OPENSEARCH_DASHBOARDS_HOST"; do
      getent hosts "$name" 2>/dev/null || dscacheutil -q host -a name "$name"
    done
    ```

2. From inside a gateway container, check that the GPU host's names resolve
   to the GPU host. Replace the example name with your `D3_SERVER_NAME`:

    ```sh
    docker compose exec -T litellm-prod python3 -c \
      'import socket, sys; print(socket.gethostbyname(sys.argv[1]))' d3-mini.example.test
    ```

3. Check that the certificate covers the same names:

    ```sh
    openssl x509 -in "${TRAEFIK_TLS_CERT_FILE:-traefik/certs/litellm-stack.pem}" \
      -noout -ext subjectAltName
    ```

When a hostname changes, change three things together: the DNS record, the
certificate, and the `*_HOST` variable. Then recreate Traefik and the services
whose routes changed (`docker compose up -d --force-recreate traefik <service>`).
