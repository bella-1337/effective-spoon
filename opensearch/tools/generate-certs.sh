#!/bin/sh
# Create or renew the private TLS material used inside the OpenSearch cluster.
#
# The CA is created once and reused. Node and admin leaf certificates are
# reissued when absent or when they expire within the renewal window, so
# operators can restart this one-shot service to rotate them.
set -eu

umask 077

CA_DIR=/certs/ca-private
PUBLIC_CA_DIR=/certs/ca
NODE_DIR=/certs/node
ADMIN_DIR=/certs/admin
WORK_DIR="$(mktemp -d)"
trap 'rm -rf -- "$WORK_DIR"' EXIT HUP INT TERM

CA_DAYS="${OPENSEARCH_CA_VALIDITY_DAYS:-3650}"
LEAF_DAYS="${OPENSEARCH_CERT_VALIDITY_DAYS:-397}"
RENEW_SECONDS=$(( ${OPENSEARCH_CERT_RENEW_BEFORE_DAYS:-30} * 86400 ))
# OpenSearch compares RFC 2253 DNs, which list the most specific RDN first.
SUBJECT_PREFIX="/O=LiteLLM Stack/OU=OpenSearch"
OPENSEARCH_UID="${OPENSEARCH_UID:-1000}"
TOOLS_UID="${OPENSEARCH_TOOLS_UID:-65532}"

for value in "$CA_DAYS" "$LEAF_DAYS" "$RENEW_SECONDS" "$OPENSEARCH_UID" "$TOOLS_UID"; do
  case "$value" in
    ''|*[!0-9]*) echo "Certificate settings must be non-negative integers" >&2; exit 64 ;;
  esac
done

mkdir -p "$CA_DIR" "$PUBLIC_CA_DIR" "$NODE_DIR" "$ADMIN_DIR"

needs_leaf() {
  [ ! -s "$1" ] || [ ! -s "$2" ] \
    || ! openssl x509 -in "$1" -noout -checkend "$RENEW_SECONDS" >/dev/null 2>&1 \
    || ! openssl verify -CAfile "$CA_DIR/ca.pem" "$1" >/dev/null 2>&1
}

if [ ! -s "$CA_DIR/ca.pem" ] || [ ! -s "$CA_DIR/ca-key.pem" ]; then
  echo "Creating the private OpenSearch certificate authority"
  openssl genpkey -quiet -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$WORK_DIR/ca-key.pem"
  openssl req -x509 -new -sha256 -days "$CA_DAYS" \
    -key "$WORK_DIR/ca-key.pem" \
    -subj "${SUBJECT_PREFIX}/CN=litellm-stack-opensearch-ca" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -out "$WORK_DIR/ca.pem"
  mv -f "$WORK_DIR/ca-key.pem" "$CA_DIR/ca-key.pem"
  mv -f "$WORK_DIR/ca.pem" "$CA_DIR/ca.pem"
  # A new CA invalidates every existing leaf.
  rm -f "$NODE_DIR/node.pem" "$ADMIN_DIR/admin.pem"
fi

if ! openssl x509 -in "$CA_DIR/ca.pem" -noout -checkend "$RENEW_SECONDS" >/dev/null 2>&1; then
  echo "The OpenSearch CA expires within the renewal window; remove the opensearch-ca-private volume to rotate it" >&2
  exit 1
fi

issue_leaf() {
  name="$1"; subject="$2"; extensions="$3"; target_dir="$4"
  openssl genpkey -quiet -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$WORK_DIR/$name-key.pem"
  openssl req -new -sha256 -key "$WORK_DIR/$name-key.pem" -subj "$subject" -out "$WORK_DIR/$name.csr"
  printf '%s\n' "$extensions" > "$WORK_DIR/$name.ext"
  openssl x509 -req -sha256 -days "$LEAF_DAYS" \
    -in "$WORK_DIR/$name.csr" \
    -CA "$CA_DIR/ca.pem" -CAkey "$CA_DIR/ca-key.pem" -CAcreateserial \
    -extfile "$WORK_DIR/$name.ext" \
    -out "$WORK_DIR/$name.pem"
  # OpenSearch requires PKCS#8 private keys.
  openssl pkcs8 -topk8 -nocrypt -in "$WORK_DIR/$name-key.pem" -out "$WORK_DIR/$name-key.pk8"
  mv -f "$WORK_DIR/$name-key.pk8" "$target_dir/$name-key.pem"
  mv -f "$WORK_DIR/$name.pem" "$target_dir/$name.pem"
}

if needs_leaf "$NODE_DIR/node.pem" "$NODE_DIR/node-key.pem"; then
  echo "Issuing the OpenSearch node certificate"
  issue_leaf node "${SUBJECT_PREFIX}/CN=opensearch" \
    "basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=DNS:opensearch,DNS:localhost,IP:127.0.0.1" \
    "$NODE_DIR"
fi

if needs_leaf "$ADMIN_DIR/admin.pem" "$ADMIN_DIR/admin-key.pem"; then
  echo "Issuing the OpenSearch security administrator certificate"
  issue_leaf admin "${SUBJECT_PREFIX}/CN=litellm-stack-opensearch-admin" \
    "basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=clientAuth" \
    "$ADMIN_DIR"
fi

rm -f "$CA_DIR/ca.srl"
cp "$CA_DIR/ca.pem" "$PUBLIC_CA_DIR/ca.pem"
cp "$CA_DIR/ca.pem" "$NODE_DIR/ca.pem"
cp "$CA_DIR/ca.pem" "$ADMIN_DIR/ca.pem"

# The CA private key lives in a volume mounted only by this job; every consumer
# receives only the material it needs through a separate named volume.
chown 0:0 "$CA_DIR/ca-key.pem" && chmod 0400 "$CA_DIR/ca-key.pem"
chmod 0700 "$CA_DIR"
chmod 0444 "$CA_DIR/ca.pem" "$PUBLIC_CA_DIR/ca.pem" "$NODE_DIR/ca.pem" "$ADMIN_DIR/ca.pem" "$NODE_DIR/node.pem" "$ADMIN_DIR/admin.pem"
chmod 0755 "$PUBLIC_CA_DIR" "$NODE_DIR" "$ADMIN_DIR"
chown "$OPENSEARCH_UID:$OPENSEARCH_UID" "$NODE_DIR/node-key.pem" && chmod 0400 "$NODE_DIR/node-key.pem"
chown "$TOOLS_UID:$TOOLS_UID" "$ADMIN_DIR/admin-key.pem" && chmod 0400 "$ADMIN_DIR/admin-key.pem"

echo "OpenSearch TLS material is ready"
