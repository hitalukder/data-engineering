#!/usr/bin/env bash
# Remove the Milvus release. PVCs and the namespace are kept by default
# so you don't accidentally lose data — pass --purge to wipe everything.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

[[ -f .env ]] && { set -a; source .env; set +a; }
: "${NAMESPACE:=milvus}"

PURGE=false
[[ "${1:-}" == "--purge" ]] && PURGE=true

echo "==> Deleting Routes (if any)"
oc -n "$NAMESPACE" delete route milvus-api milvus-rest milvus-grpc milvus-metrics --ignore-not-found

echo "==> Uninstalling helm release 'milvus' from $NAMESPACE"
helm -n "$NAMESPACE" uninstall milvus || true

if $PURGE; then
  echo "==> Deleting PVCs in $NAMESPACE"
  oc -n "$NAMESPACE" delete pvc --all --ignore-not-found
  echo "==> Deleting credential + TLS secrets"
  oc -n "$NAMESPACE" delete secret milvus-s3-credentials milvus-tls --ignore-not-found
  echo "==> Deleting project $NAMESPACE"
  oc delete project "$NAMESPACE" --ignore-not-found
  echo "==> Purge complete. Note: data in IBM COS bucket is NOT deleted."
  echo "    Local cert at certs/ is also preserved — delete manually if desired."
else
  echo "==> Release removed. PVCs/secret/project preserved."
  echo "    Run with --purge to wipe everything."
fi
