#!/usr/bin/env bash
# Deploy Milvus standalone on IBM Cloud OpenShift using Helm + IBM COS.
# Idempotent — safe to re-run for upgrades / config changes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---- Load config ----
if [[ -f .env ]]; then
  set -a; source .env; set +a
else
  echo "ERROR: .env not found. Copy .env.example to .env and fill it in." >&2
  exit 1
fi

: "${NAMESPACE:?NAMESPACE not set}"
: "${COS_ENDPOINT:?COS_ENDPOINT not set}"
: "${COS_BUCKET:?COS_BUCKET not set}"
: "${COS_ACCESS_KEY:?COS_ACCESS_KEY not set}"
: "${COS_SECRET_KEY:?COS_SECRET_KEY not set}"
: "${STORAGE_CLASS:?STORAGE_CLASS not set}"
: "${WAL_SIZE:=50Gi}"
: "${ETCD_SIZE:=10Gi}"
: "${CPU_REQUEST:=1}"
: "${CPU_LIMIT:=4}"
: "${MEM_REQUEST:=4Gi}"
: "${MEM_LIMIT:=8Gi}"
: "${MILVUS_CHART_VERSION:=}"
: "${CREATE_ROUTES:=true}"

# ---- Tool checks ----
for cmd in oc helm envsubst; do
  command -v "$cmd" >/dev/null || { echo "ERROR: $cmd not on PATH" >&2; exit 1; }
done

oc whoami >/dev/null 2>&1 || { echo "ERROR: not logged into OpenShift. Run 'oc login ...' first." >&2; exit 1; }

echo "==> Logged into OpenShift as: $(oc whoami)"
echo "==> Target namespace: $NAMESPACE"

# ---- 1. Project ----
if ! oc get project "$NAMESPACE" >/dev/null 2>&1; then
  echo "==> Creating project $NAMESPACE"
  oc new-project "$NAMESPACE" >/dev/null
else
  echo "==> Project $NAMESPACE already exists"
  oc project "$NAMESPACE" >/dev/null
fi

# ---- 2. SCC: bitnami etcd/kafka images run as fixed UIDs ----
echo "==> Granting anyuid SCC to default SA in $NAMESPACE"
oc adm policy add-scc-to-user anyuid -z default -n "$NAMESPACE" >/dev/null

# ---- 3a. TLS cert for Milvus pod (self-signed, used by passthrough route) ----
# Cert SAN must include the milvus-api Route hostname. OpenShift constructs
# this deterministically as <route-name>-<namespace>.<cluster-domain>.
CLUSTER_APPS_DOMAIN=$(oc get ingresscontroller default -n openshift-ingress-operator \
                     -o jsonpath='{.status.domain}' 2>/dev/null \
                     || oc get -A route -o jsonpath='{.items[0].spec.host}' | sed 's/^[^.]*\.//')
ROUTE_HOST="milvus-api-${NAMESPACE}.${CLUSTER_APPS_DOMAIN}"
CERT_DIR="${SCRIPT_DIR}/certs"

if [[ ! -f "${CERT_DIR}/server.pem" || ! -f "${CERT_DIR}/server.key" ]]; then
  echo "==> Generating self-signed cert for $ROUTE_HOST"
  mkdir -p "$CERT_DIR"
  openssl req -x509 -newkey rsa:4096 -sha256 -days 365 -nodes \
    -keyout "${CERT_DIR}/server.key" -out "${CERT_DIR}/server.pem" \
    -subj "/CN=milvus" \
    -addext "subjectAltName=DNS:${ROUTE_HOST},DNS:milvus,DNS:milvus.${NAMESPACE}.svc.cluster.local,DNS:localhost" \
    >/dev/null 2>&1
else
  echo "==> Reusing existing cert at ${CERT_DIR}/server.pem"
fi

echo "==> Creating/updating milvus-tls secret"
oc -n "$NAMESPACE" create secret generic milvus-tls \
  --from-file=tls.crt="${CERT_DIR}/server.pem" \
  --from-file=tls.key="${CERT_DIR}/server.key" \
  --dry-run=client -o yaml | oc apply -f - >/dev/null

# ---- 3. COS credentials secret ----
# Kept for reference / external tools. The Milvus chart v5.0.x doesn't read
# from existingSecret — credentials are passed via --set below and end up
# in the rendered milvus configmap.
echo "==> Creating/updating IBM COS credentials secret"
oc -n "$NAMESPACE" create secret generic milvus-s3-credentials \
  --from-literal=accesskey="$COS_ACCESS_KEY" \
  --from-literal=secretkey="$COS_SECRET_KEY" \
  --dry-run=client -o yaml | oc apply -f - >/dev/null

# ---- 4. Helm repo ----
echo "==> Adding Milvus helm repo"
helm repo add milvus https://zilliztech.github.io/milvus-helm/ >/dev/null 2>&1 || true
helm repo update milvus >/dev/null

# ---- 5. Render values ----
echo "==> Rendering values-milvus.yaml from template"
export COS_ENDPOINT COS_BUCKET STORAGE_CLASS WAL_SIZE ETCD_SIZE \
       CPU_REQUEST CPU_LIMIT MEM_REQUEST MEM_LIMIT
envsubst < values-milvus.yaml.tmpl > values-milvus.yaml

# ---- 6. Install / upgrade ----
# If a previous release is stuck in pending-* or failed, the next upgrade
# will refuse to proceed. Recover by uninstalling — PVCs survive.
RELEASE_STATUS=$(helm -n "$NAMESPACE" list -o json 2>/dev/null \
                 | python3 -c "import sys,json; r=[x for x in json.load(sys.stdin) if x['name']=='milvus']; print(r[0]['status'] if r else '')" \
                 2>/dev/null || true)
if [[ "$RELEASE_STATUS" =~ ^(pending-install|pending-upgrade|pending-rollback|failed)$ ]]; then
  echo "==> Previous release status is '$RELEASE_STATUS' — uninstalling stale release (PVCs are preserved)"
  helm -n "$NAMESPACE" uninstall milvus --wait || true
  # Also remove the half-applied pulsarv3 leftovers if a prior install enabled them
  oc -n "$NAMESPACE" delete sts,deploy,svc,cm,sa,role,rolebinding \
    -l 'app.kubernetes.io/instance=milvus' --ignore-not-found
fi

HELM_ARGS=(-n "$NAMESPACE" upgrade --install milvus milvus/milvus
           -f values-milvus.yaml --wait --timeout 15m
           --set externalS3.accessKey="$COS_ACCESS_KEY"
           --set externalS3.secretKey="$COS_SECRET_KEY")
if [[ -n "$MILVUS_CHART_VERSION" ]]; then
  HELM_ARGS+=(--version "$MILVUS_CHART_VERSION")
fi

# Note: the --set args contain secrets, redact when echoing.
echo "==> Running: helm upgrade --install milvus milvus/milvus -f values-milvus.yaml [+ inline COS creds]"
helm "${HELM_ARGS[@]}"

# ---- 7. Routes (optional) ----
if [[ "$CREATE_ROUTES" == "true" ]]; then
  echo "==> Creating/updating Routes"
  oc apply -n "$NAMESPACE" -f - <<EOF
# milvus-api: passthrough route. TLS terminates INSIDE Milvus (cert mounted from
# the milvus-tls secret). The router only does TCP/SNI forwarding, so the TLS
# handshake — including ALPN h2 — happens directly client <-> Milvus pod.
# This is the ONLY way real gRPC clients can connect through this cluster's
# router, which has no-alpn on its TLS binds.
#
# REST is disabled at the Milvus level (proxy.http.enabled=false in values).
# Milvus 2.6 won't run REST + gRPC + TLS on the same port and has no
# proxy.http.port to separate them.
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: milvus-api
spec:
  to: { kind: Service, name: milvus }
  port: { targetPort: 19530 }
  tls:
    termination: passthrough
    insecureEdgeTerminationPolicy: None
---
# Metrics/health on the dedicated 9091 port. Plain HTTP/1.1, no TLS in pod.
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: milvus-metrics
spec:
  to: { kind: Service, name: milvus }
  port: { targetPort: 9091 }
  tls:
    termination: edge
    insecureEdgeTerminationPolicy: Redirect
EOF
fi

# ---- 8. Status ----
echo ""
echo "==> Pods:"
oc -n "$NAMESPACE" get pods
echo ""
echo "==> Services:"
oc -n "$NAMESPACE" get svc

API_HOST=""
METRICS_HOST=""
if [[ "$CREATE_ROUTES" == "true" ]]; then
  echo ""
  echo "==> Routes:"
  oc -n "$NAMESPACE" get route milvus-api milvus-metrics 2>/dev/null
  API_HOST=$(oc -n "$NAMESPACE" get route milvus-api -o jsonpath='{.spec.host}' 2>/dev/null || true)
  METRICS_HOST=$(oc -n "$NAMESPACE" get route milvus-metrics -o jsonpath='{.spec.host}' 2>/dev/null || true)
fi

cat <<EOF

================================================================
Milvus standalone is deployed (TLS in pod, REST disabled externally).

In-cluster gRPC    : milvus.${NAMESPACE}.svc.cluster.local:19530 (TLS)
In-cluster metrics : milvus.${NAMESPACE}.svc.cluster.local:9091
EOF

if [[ -n "$API_HOST" ]]; then
cat <<EOF

External gRPC      : https://${API_HOST}  (TLS + h2, passthrough)
External metrics   : https://${METRICS_HOST}/healthz
EOF
fi

cat <<EOF

Default credentials (CHANGE IMMEDIATELY):
  user: root
  pass: Milvus

Cert (self-signed; clients must trust it):
  ${CERT_DIR}/server.pem

Quick test (pymilvus):
  python -c "
from pymilvus import MilvusClient
c = MilvusClient(
    uri='https://${API_HOST}',
    user='root', password='Milvus',
    server_pem_path='${CERT_DIR}/server.pem',
    server_name='${API_HOST}',
    secure=True,
)
print(c.list_collections())"

REST is disabled. To re-enable (loses external gRPC): set
proxy.http.enabled=true AND common.security.tlsMode=0 in the values.

Rotate root password: README.md → "Rotate the root password".
================================================================
EOF
