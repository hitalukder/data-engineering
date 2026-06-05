#!/usr/bin/env bash
# =============================================================
# deploy.sh — Build, push, and deploy text2sql-service to
#             IBM Code Engine in one command.
#
# Prerequisites:
#   - ibmcloud CLI installed and logged in
#   - Docker running locally
#   - db2jcc4.jar present in this directory
#   - .env file populated (copy from .env.example)
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
# =============================================================
set -euo pipefail

# ── Load local env for script variables ───────────────────────
if [ -f .env ]; then
  # Export only the variables we need for the script itself
  export $(grep -E '^(ICR_NAMESPACE|CE_PROJECT_NAME|CE_APP_NAME|IBM_REGION)' .env | xargs) 2>/dev/null || true
fi

# ── Configuration — edit these or set in .env ─────────────────
IBM_REGION="${IBM_REGION:-us-south}"
ICR_NAMESPACE="${ICR_NAMESPACE:-pm-agents}"
CE_PROJECT_NAME="${CE_PROJECT_NAME:-pm-agents}"
CE_APP_NAME="${CE_APP_NAME:-text2sql-service}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
IMAGE="us.icr.io/${ICR_NAMESPACE}/${CE_APP_NAME}:${IMAGE_TAG}"

echo "======================================================="
echo "  IBM Text-to-SQL Service — Code Engine Deployment"
echo "======================================================="
echo "  Region:      $IBM_REGION"
echo "  Image:       $IMAGE"
echo "  CE Project:  $CE_PROJECT_NAME"
echo "  CE App:      $CE_APP_NAME"
echo "======================================================="

# ── 1. Login checks ───────────────────────────────────────────
echo ""
echo "▶ Step 1/6 — IBM Cloud login"
ibmcloud cr login
ibmcloud cr namespace-add "${ICR_NAMESPACE}" 2>/dev/null || echo "  Namespace already exists, continuing."

# ── 2. Build image ────────────────────────────────────────────
echo ""
echo "▶ Step 2/6 — Building Docker image"
if [ ! -f db2jcc4.jar ]; then
  echo "  ERROR: db2jcc4.jar not found in $(pwd)"
  echo "  Download from: https://www.ibm.com/support/pages/db2-jdbc-driver-versions"
  exit 1
fi
podman build --platform linux/amd64 -t "${IMAGE}" .
echo "  ✓ Image built: ${IMAGE}"

# ── 3. Push image ─────────────────────────────────────────────
echo ""
echo "▶ Step 3/6 — Pushing to IBM Container Registry"
podman push "${IMAGE}"
echo "  ✓ Image pushed"

# ── 4. Select / create Code Engine project ────────────────────
echo ""
echo "▶ Step 4/6 — Setting up Code Engine project"
ibmcloud ce project select --name "${CE_PROJECT_NAME}" 2>/dev/null || \
  ibmcloud ce project create --name "${CE_PROJECT_NAME}"
echo "  ✓ Project: ${CE_PROJECT_NAME}"

# ── 5. Deploy or update application ──────────────────────────
echo ""
echo "▶ Step 5/6 — Deploying application"

# Read env values from .env for Code Engine --env flags
source .env

DEPLOY_CMD="ibmcloud ce application"

if ibmcloud ce application get --name "${CE_APP_NAME}" &>/dev/null; then
  echo "  Application exists — updating..."
  DEPLOY_CMD="${DEPLOY_CMD} update"
else
  echo "  Creating new application..."
  DEPLOY_CMD="${DEPLOY_CMD} create"
fi

${DEPLOY_CMD} \
  --name "${CE_APP_NAME}" \
  --image "${IMAGE}" \
  --port 4050 \
  --min-scale 1 \
  --max-scale 5 \
  --cpu 1 \
  --memory 4G \
  --env IBM_CLOUD_API_KEY="${IBM_CLOUD_API_KEY}" \
  --env WXD_CONTAINER_ID="${WXD_CONTAINER_ID}" \
  --env WXD_CONTAINER_TYPE="${WXD_CONTAINER_TYPE}" \
  --env WXD_MODEL_ID="${WXD_MODEL_ID}" \
  --env WXD_TEXT2SQL_BASE="${WXD_TEXT2SQL_BASE}" \
  --env DB2_HOSTNAME="${DB2_HOSTNAME}" \
  --env DB2_PORT="${DB2_PORT}" \
  --env DB2_DATABASE="${DB2_DATABASE}" \
  --env DB2_SCHEMA="${DB2_SCHEMA}" \
  --env DB2_USERNAME="${DB2_USERNAME}" \
  --env DB2_PASSWORD="${DB2_PASSWORD}" \
  --env APP_API_KEY="${APP_API_KEY}" \
  --env PYTHONUNBUFFERED=1

# ── 6. Retrieve URL and update OpenAPI spec ───────────────────
echo ""
echo "▶ Step 6/6 — Retrieving deployment URL"
sleep 5  # allow a moment for URL to populate
SERVICE_URL=$(ibmcloud ce application get --name "${CE_APP_NAME}" --output json \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['status']['url'])" 2>/dev/null || echo "")

if [ -n "${SERVICE_URL}" ]; then
  echo "  ✓ Service URL: ${SERVICE_URL}"

  # Update .env with the live URL
  if grep -q "SERVICE_BASE_URL=" .env 2>/dev/null; then
    sed -i.bak "s|SERVICE_BASE_URL=.*|SERVICE_BASE_URL=${SERVICE_URL}|" .env
  else
    echo "SERVICE_BASE_URL=${SERVICE_URL}" >> .env
  fi

  echo ""
  echo "  Fetching OpenAPI spec from live service..."
  sleep 10  # wait for first pod to be ready
  curl -sf "${SERVICE_URL}/openapi.json" \
    | python3 -c "
import sys, json
spec = json.load(sys.stdin)
spec['servers'] = [{'url': '${SERVICE_URL}'}]
print(json.dumps(spec, indent=2))
" > openapi.json && echo "  ✓ openapi.json written — ready to import into Orchestrate"

else
  echo "  ⚠ Could not retrieve URL automatically."
  echo "  Run: ibmcloud ce application get --name ${CE_APP_NAME}"
fi

echo ""
echo "======================================================="
echo "  Deployment complete!"
echo ""
echo "  Next steps:"
echo "  1. Import into Orchestrate:"
echo "     orchestrate tools import --kind openapi --file openapi.json"
echo ""
echo "  2. Smoke test:"
echo "     curl -X POST ${SERVICE_URL}/texttosql \\"
echo "       -H 'APP-API-KEY: \${APP_API_KEY}' \\"
echo "       -H 'Content-Type: application/json' \\"
echo "       -d '{\"question\":\"How many active projects are there?\",\"db_execute\":true}'"
echo "======================================================="
