# Milvus Standalone on IBM Cloud OpenShift

Deploy Milvus (standalone, latest stable) on IBM Cloud Red Hat OpenShift via the
official Helm chart. No Milvus Operator. No cert-manager. IBM Cloud Object
Storage (COS) as the data tier instead of the bundled MinIO.

Vector data lives in COS. Metadata lives in an in-cluster etcd PVC. The
write-ahead log lives in a small PVC on the standalone pod.

## What you get

- Milvus standalone, one pod
- IBM COS as the object-storage backend (S3-compatible)
- User/password auth enabled (`common.security.authorizationEnabled: true`)
- **TLS terminates inside Milvus** (cert mounted from `milvus-tls` secret). Required because this cluster's OpenShift HAProxy router has `no-alpn` on its TLS binds, so edge routes can't negotiate h2 ALPN — and gRPC needs h2.
- **REST is disabled.** Milvus 2.6 won't run REST + gRPC + TLS on the same port, and there's no `proxy.http.port` to separate them. Apps use gRPC (pymilvus, Go/Java SDK).
- Two OpenShift Routes:
  - **`milvus-api`** — **passthrough** (router does TCP forwarding only). TLS handshake + ALPN h2 happens client ↔ Milvus pod directly. This is what makes gRPC work.
  - **`milvus-metrics`** — edge TLS, plain HTTP/1.1, targets pod `:9091` for `/healthz`, `/metrics`.
- Idempotent install/upgrade via a single script (auto-generates self-signed cert on first run)

## Prerequisites

| Tool | Why |
|---|---|
| `oc` (OpenShift CLI) | Cluster access, SCC management |
| `helm` ≥ 3.8 | Chart install |
| `envsubst` (from `gettext`) | Values templating — `brew install gettext` on macOS |
| IBM Cloud account with COS service | Bucket + HMAC credentials |
| Logged-in `oc` session | `oc login --token=... --server=...` |

You also need **cluster-admin** (or at least permission to grant `anyuid`
SCC in the target namespace) for the first install.

## Step 1 — Create an IBM COS bucket and HMAC credentials

A bucket is just blob storage; HMAC creds are what let Milvus's S3 client talk
to it.

```bash
# Pick a COS instance and a unique bucket name
COS_INSTANCE=my-cos-instance
BUCKET=my-milvus-bucket
REGION=us-south

# 1. Bucket
ibmcloud cos bucket-create \
  --bucket "$BUCKET" \
  --class smart \
  --ibm-service-instance-id "$(ibmcloud resource service-instance "$COS_INSTANCE" --output json | jq -r '.[0].guid')" \
  --region "$REGION"

# 2. HMAC credentials (this is the part that's easy to miss — default
#    service keys are IAM tokens, which Milvus's S3 client cannot use)
ibmcloud resource service-key-create milvus-hmac Writer \
  --instance-name "$COS_INSTANCE" \
  --parameters '{"HMAC":true}'

# 3. Read the access_key_id and secret_access_key from the output
ibmcloud resource service-key milvus-hmac --output json \
  | jq '.[0].credentials.cos_hmac_keys'
```

Copy `access_key_id` → `COS_ACCESS_KEY`, `secret_access_key` → `COS_SECRET_KEY`.

> Don't have `ibmcloud` installed? You can do all of the above in the IBM Cloud
> web console: COS service → your instance → **Service credentials** → New,
> with the advanced option **Include HMAC Credential** turned on.

## Step 2 — Configure

```bash
cp .env.example .env
$EDITOR .env   # fill in COS_ENDPOINT, COS_BUCKET, COS_ACCESS_KEY, COS_SECRET_KEY
```

Endpoint hosts by region:

| Region | Public endpoint |
|---|---|
| `us-south` | `s3.us-south.cloud-object-storage.appdomain.cloud` |
| `us-east` | `s3.us-east.cloud-object-storage.appdomain.cloud` |
| `eu-de` | `s3.eu-de.cloud-object-storage.appdomain.cloud` |
| `eu-gb` | `s3.eu-gb.cloud-object-storage.appdomain.cloud` |
| `jp-tok` | `s3.jp-tok.cloud-object-storage.appdomain.cloud` |
| `au-syd` | `s3.au-syd.cloud-object-storage.appdomain.cloud` |

If your cluster and bucket are in the same region, swap `s3.` → `s3.direct.`
to use the private endpoint and avoid egress charges.

Storage class — pick one your cluster has:

```bash
oc get storageclass
```

Common ones: `ibmc-vpc-block-general-purpose`, `ibmc-vpc-block-10iops-tier`,
`ibmc-block-gold` (Classic).

## Step 3 — Deploy

```bash
chmod +x deploy-milvus.sh uninstall-milvus.sh
./deploy-milvus.sh
```

The script:

1. Creates the project if missing.
2. Grants `anyuid` SCC to the namespace's `default` ServiceAccount (the bundled
   bitnami etcd image runs as a fixed UID, which OpenShift's restricted SCC
   blocks).
3. Creates/updates the `milvus-s3-credentials` secret with your HMAC keys.
4. Adds the Milvus Helm repo and renders `values-milvus.yaml` from the
   template.
5. Runs `helm upgrade --install milvus milvus/milvus --wait`.
6. If `CREATE_ROUTES=true` (default), applies the `milvus-rest` and
   `milvus-grpc` Routes.

Re-running picks up `.env` changes. Safe to run repeatedly.

On success it prints both route URLs and a copy-pasteable verification snippet.

## Step 4 — Verify

```bash
oc -n milvus get pods
oc -n milvus get route
```

Grab the route hosts:

```bash
API_HOST=$(oc -n milvus get route milvus-api -o jsonpath='{.spec.host}')
METRICS_HOST=$(oc -n milvus get route milvus-metrics -o jsonpath='{.spec.host}')
```

TLS handshake check (should show `ALPN protocol: h2` and your self-signed CN):

```bash
echo | openssl s_client -connect "$API_HOST:443" -alpn h2,http/1.1 \
  -servername "$API_HOST" 2>/dev/null | grep -E 'ALPN|^subject'
```

Health check (no TLS, plain edge route):

```bash
curl -s "https://$METRICS_HOST/healthz"
# → OK
```

gRPC via pymilvus (clients must trust the self-signed cert):

```bash
pip install pymilvus
python - <<PY
from pymilvus import MilvusClient
c = MilvusClient(
    uri=f"https://$API_HOST",
    user="root", password="Milvus",
    server_pem_path="certs/server.pem",
    server_name="$API_HOST",
    secure=True,
)
print("collections:", c.list_collections())
PY
```

### Test the gRPC endpoint from the command line

Plain `curl` can't meaningfully exercise gRPC — payloads are protobuf, not JSON.
For testing you have three layers, cheapest first:

#### 1. Validate the TLS handshake + ALPN h2 (curl-like; tells you gRPC *can* work)

```bash
echo | openssl s_client \
  -connect "$API_HOST:443" \
  -servername "$API_HOST" \
  -alpn h2,http/1.1 \
  2>/dev/null | grep -E 'ALPN|^subject|Verify'
```

Expected:
```
subject=CN=milvus
ALPN protocol: h2                                    ← gRPC will work
Verify return code: 18 (self-signed certificate)    ← expected
```

If `ALPN protocol: h2` is missing, gRPC won't work regardless of what client you use.

#### 2. Health check (works with normal curl, because metrics is plain HTTPS edge)

```bash
curl -sk "https://$METRICS_HOST/healthz"
# → OK
```

#### 3. Actually call the gRPC API

The simplest working "curl for gRPC" against Milvus is a 4-line **pymilvus**
snippet — Milvus doesn't expose gRPC reflection, so generic tools like
`grpcurl` need the `.proto` files supplied explicitly (see below for that
path). pymilvus already bundles them, so:

```bash
API_HOST=$(oc -n milvus get route milvus-api -o jsonpath='{.spec.host}')

# Positive test:
python -c "
from pymilvus import MilvusClient
c = MilvusClient(uri='https://$API_HOST', user='root', password='Milvus',
    server_pem_path='certs/server.pem', server_name='$API_HOST', secure=True)
print(c.list_databases())
"
# → ['default']

# Negative test (wrong password — should reject):
python -c "
from pymilvus import MilvusClient
try:
    c = MilvusClient(uri='https://$API_HOST', user='root', password='WRONG',
        server_pem_path='certs/server.pem', server_name='$API_HOST', secure=True)
    c.list_databases()
except Exception as e:
    print('rejected:', e)
"
# → rejected: <MilvusException: ... auth check failure ...>
```

#### 3b. Optional: `grpcurl` with explicit proto files

If you really want the curl-style CLI experience, grpcurl works once you give
it the Milvus proto definitions:

```bash
# One-time setup
brew install grpcurl                                            # macOS
git clone --depth=1 https://github.com/milvus-io/milvus-proto

# Then:
grpcurl \
  -proto milvus-proto/proto/milvus.proto \
  -import-path milvus-proto/proto \
  -cacert certs/server.pem \
  -servername "$API_HOST" \
  -H "authorization: $(printf 'root:Milvus' | base64)" \
  -d '{}' \
  "$API_HOST:443" \
  milvus.proto.milvus.MilvusService/ListDatabases
# → { "status": {}, "dbNames": ["default"] }
```

**Flag reference:**

| Flag | Why |
|---|---|
| `-proto <file>` / `-import-path <dir>` | Milvus's gRPC server has no reflection — proto must come from the client side. |
| `-cacert certs/server.pem` | Trust our self-signed cert. |
| `-servername "$API_HOST"` | SNI is what the passthrough router uses to pick the backend. Mandatory. |
| `-H "authorization: <base64(user:pass)>"` | Milvus auth header. **No `Basic ` prefix** — raw base64 only. |
| `-d '{}'` | Empty request payload (grpcurl converts JSON → protobuf). |
| `"$API_HOST:443"` | Passthrough route listens on standard HTTPS 443. |

> **Why passthrough.** This cluster's HAProxy router has `no-alpn` on its
> front-end TLS binds — it never advertises HTTP/2 in ALPN. Real gRPC clients
> (pymilvus, Go, Java) refuse to fall back to HTTP/1.1 and time out on edge
> routes. Passthrough makes the router a pure TCP+SNI forwarder; the TLS
> handshake — including ALPN — happens directly between client and Milvus pod,
> bypassing the router's TLS config entirely.

### What about REST?

REST is **disabled** at the Milvus level. Two ways to enable it:

1. **In-cluster only** (recommended for occasional debugging): use port-forward
   and a separate Milvus build without TLS:
   ```bash
   # not possible against this deployment; would need a separate deployment
   # with proxy.http.enabled=true and tlsMode=0
   ```

2. **Trade off gRPC**: in `values-milvus.yaml.tmpl`, set
   `proxy.http.enabled: true` and `common.security.tlsMode: 0`. Then re-deploy.
   You lose gRPC over the route (back to the original problem) but gain REST
   through an edge route. Use this only if your apps are REST-only.

## Step 5 — Rotate the root password

The chart can't set the initial root password (Milvus boots with the built-in
`root` / `Milvus`). Change it immediately after first connect:

```python
from pymilvus import MilvusClient
c = MilvusClient(
    uri=f"https://$API_HOST",
    user="root", password="Milvus",
    server_pem_path="certs/server.pem",
    server_name="$API_HOST", secure=True,
)
c.update_password("root", "Milvus", "YourStrongNewPassword")
```

Then create scoped users for applications instead of using `root` from app code:

```python
from pymilvus import connections, utility, Role

connections.connect(host="localhost", port="19530",
                    user="root", password="YourStrongNewPassword")
utility.create_user("app_user", "AppStrongPassword")

# Optional: RBAC. Grant a built-in role.
Role("public").add_user("app_user")   # read-only-ish
# or define a custom role with utility.create_role(...) + role.grant(...)
```

## Step 6 — (Optional) Browse with Attu

[Attu](https://github.com/zilliztech/attu) is the official Milvus web UI —
collection browser, schema viewer, query playground, RBAC manager. Easiest way
to run it is as a container on your local machine pointing at the cluster.

### macOS prerequisite

Podman on macOS runs containers inside a managed Linux VM. Start it once:

```bash
podman machine init     # first time only
podman machine start    # each boot
```

(Skip this on Linux — podman runs containers natively.)

### Start Attu with podman

```bash
cd /Users/htalukder/dev/mivlus_openshift
API_HOST=$(oc -n milvus get route milvus-api -o jsonpath='{.spec.host}')

podman run --rm -d --name attu \
  -p 8000:3000 \
  -v "$PWD/certs:/app/certs:ro,Z" \
  -e MILVUS_URL="https://$API_HOST:443" \
  -e ROOT_CERT_PATH=/app/certs/server.pem \
  -e SERVER_NAME="$API_HOST" \
  -e ATTU_LOG_LEVEL=info \
  zilliz/attu:latest

echo "Attu UI: http://localhost:8000"
```

Open <http://localhost:8000> in a browser and log in:

| Field | Value |
|---|---|
| Milvus Address | leave blank (pre-filled from `MILVUS_URL`) — or paste `https://<API_HOST>:443` |
| Authentication | toggle ON |
| Username | `root` |
| Password | `Milvus` (or your rotated password) |
| Database | `default` |

### What the env vars do

| Env var | Purpose |
|---|---|
| `MILVUS_URL` | Backend Milvus endpoint Attu connects to. Use `https://...:443` because we have TLS in the pod. |
| `ROOT_CERT_PATH` | Path *inside the container* to the CA bundle Attu uses to verify the server cert. Points to our self-signed cert. |
| `SERVER_NAME` | SNI / cert verification hostname. Must match a SAN on the cert (it does — the route hostname is in there). |
| `ATTU_LOG_LEVEL` | Optional. `info` is fine; bump to `debug` if you need to see why a connection is failing. |

The `-v "$PWD/certs:/app/certs:ro,Z"` mount makes the cert available; `:ro` is
read-only and `:Z` is for SELinux relabeling on Fedora/RHEL hosts (harmless on
macOS — podman ignores it).

### Stop / restart

```bash
podman stop attu       # stop and (because of --rm) remove the container
podman logs attu       # tail logs if you didn't use --rm
podman restart attu    # only works without --rm
```

To keep Attu running across reboots, drop `--rm` and add `--restart=unless-stopped`.

### Using docker instead of podman

The commands are identical — just swap `podman` for `docker`. Drop the `,Z`
suffix on the volume mount (docker doesn't speak SELinux labels):

```bash
docker run --rm -d --name attu \
  -p 8000:3000 \
  -v "$PWD/certs:/app/certs:ro" \
  -e MILVUS_URL="https://$API_HOST:443" \
  -e ROOT_CERT_PATH=/app/certs/server.pem \
  -e SERVER_NAME="$API_HOST" \
  zilliz/attu:latest
```

### Troubleshooting Attu

| Symptom | Fix |
|---|---|
| Login screen accepts creds but UI shows "Failed to connect" | Backend can't reach the route. Check that you can `curl -k https://$API_HOST/healthz` from your laptop — if not, the route is unreachable (VPN, firewall). |
| "x509: certificate signed by unknown authority" in container logs | `ROOT_CERT_PATH` isn't being read. Confirm the mount: `podman exec attu ls -l /app/certs/server.pem`. |
| "tls: server selected unsupported protocol version" or handshake error | `SERVER_NAME` doesn't match a SAN on the cert. Re-run `openssl x509 -in certs/server.pem -noout -ext subjectAltName` and confirm the route hostname is listed. |
| Login rejected with valid creds | Password was rotated and you're still trying the default. |

## Routes — what the script creates

When `CREATE_ROUTES=true` (default), `deploy-milvus.sh` applies two Routes:

### `milvus-rest` — edge HTTPS

```yaml
spec:
  to: { kind: Service, name: milvus }
  port: { targetPort: 9091 }
  tls:
    termination: edge
    insecureEdgeTerminationPolicy: Redirect   # HTTP → HTTPS
```

Standard HTTPS. Router terminates TLS, forwards plaintext HTTP/1.1 to the pod.

### `milvus-grpc` — edge HTTPS + h2c upstream

```yaml
metadata:
  annotations:
    haproxy.router.openshift.io/h2c: "true"
spec:
  to: { kind: Service, name: milvus }
  port: { targetPort: 19530 }
  tls:
    termination: edge
```

The `h2c` annotation tells HAProxy to speak cleartext HTTP/2 to the pod.
Clients connect with TLS+h2 on `:443` and the router downgrades the transport
(but not the protocol) to h2c for the in-cluster hop. This is the only way to
expose Milvus's gRPC port without putting TLS inside the pod itself.

### Why not passthrough?

Passthrough is the more secure alternative — TLS terminates inside Milvus, the
router just forwards bytes. We didn't pick it because it requires generating
and mounting server certs, configuring `tls.serverPemPath` / `serverKeyPath` in
Milvus, and rotating those certs out-of-band. If you need true end-to-end TLS,
switch to passthrough; the README's git history shows the earlier passthrough
example.

## Day-2

### Upgrade Milvus

Bump `MILVUS_CHART_VERSION` in `.env` and re-run:

```bash
./deploy-milvus.sh
```

### Change resources or storage size

Edit `.env` → re-run `./deploy-milvus.sh`. The script re-renders the values
file and runs `helm upgrade`. PVC resizing requires the storage class to
support expansion (most IBM `ibmc-vpc-block-*` classes do).

### Backup

- **Metadata** — etcd snapshot: `oc -n milvus rsh milvus-etcd-0 etcdctl snapshot save /tmp/snap.db && oc cp milvus/milvus-etcd-0:/tmp/snap.db ./snap.db`
- **Vector data** — already in COS. Enable bucket versioning / cross-region
  replication on the COS side for durability.
- **Application-level** — use `milvus-backup`
  ([github.com/zilliztech/milvus-backup](https://github.com/zilliztech/milvus-backup))
  which orchestrates a consistent snapshot of both tiers.

### Uninstall

```bash
./uninstall-milvus.sh           # keeps PVCs + secret + project (recoverable)
./uninstall-milvus.sh --purge   # wipes everything in-cluster
```

The COS bucket and its contents are **never** touched by the uninstall script
— delete it manually via `ibmcloud cos bucket-delete` if you also want to
discard vector data.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `pods are forbidden: unable to validate against any security context constraint` | The `anyuid` SCC grant didn't take. Re-run `oc adm policy add-scc-to-user anyuid -z default -n milvus`. |
| Standalone crash-loops with `s3: NoSuchBucket` | `COS_BUCKET` or `COS_ENDPOINT` wrong — Milvus does not auto-create the bucket. |
| Standalone crash-loops with `Access Denied` from COS | Most likely the credentials weren't injected — `oc -n milvus get cm milvus -o jsonpath='{.data.default\.yaml}' \| grep -A2 minio:` should show non-empty `accessKeyID` / `secretAccessKey`. If they're empty, the deploy script's `helm --set` lines didn't fire. If they're populated but COS still rejects, your HMAC creds don't have access to that bucket (check the COS service-credential is on the right instance with Writer+ role). |
| Standalone crash-loops with `SignatureDoesNotMatch` | You're using IAM credentials instead of HMAC. Recreate the service key with `--parameters '{"HMAC":true}'`. |
| `pulsarv3` pods stuck on SCC errors | The chart deploys PulsarV3 by default; standalone doesn't need it. Confirm `pulsarv3.enabled: false` is in `values-milvus.yaml`. |
| `Release "milvus" failed/pending-upgrade` blocks further upgrades | The deploy script auto-detects this and re-installs. To do it manually: `helm -n milvus uninstall milvus` (PVCs survive thanks to the chart's `helm.sh/resource-policy: keep`). |
| Route returns `503 Application is not available` for REST | The route is missing the `haproxy.router.openshift.io/h2c: "true"` annotation. Milvus's 19530 listener requires HTTP/2 from the router. |
| REST returns 404 on `/v1/vector/...` | Milvus 2.6 uses the **v2** API: `POST /v2/vectordb/collections/list` with JSON body, not the old v1 GET paths. |
| etcd pod pending on PVC | `STORAGE_CLASS` doesn't exist in this cluster. `oc get storageclass`. |
| `helm upgrade` hangs at "waiting" | `oc -n milvus describe pod ...` — usually image pull, SCC, or PVC binding. |
| Auth not enforced | Inspect `oc -n milvus get cm milvus -o yaml` — `user.yaml` should contain `common.security.authorizationEnabled: true`. Re-run the deploy script. |

## Files in this repo

```
.env.example              # copy to .env, fill in
deploy-milvus.sh          # idempotent install/upgrade
uninstall-milvus.sh       # release removal (and optional purge)
values-milvus.yaml.tmpl   # Helm values, templated with envsubst
values-milvus.yaml        # generated; do not edit by hand
README.md                 # this file
```
