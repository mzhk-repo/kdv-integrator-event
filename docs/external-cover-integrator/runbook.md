# External cover CDN deployment and acceptance

## Prepared service

Task 0.2 adds `covers-cdn` to the existing KDV Compose/Swarm stack. It uses the
official nginx Alpine image as the non-root `nginx` user on internal port 8080.
The root filesystem is read-only, capabilities are dropped, and `/tmp` is tmpfs.
Only `${COVERS_STORAGE_HOST_PATH}/assets` is mounted, read-only. No host ports,
Integrator secrets, state DB or `.incoming` directory are exposed to nginx.

The public route is:

```text
HTTPS client -> Cloudflare -> existing Tunnel -> Traefik web -> covers-cdn:8080
```

The orchestrator derives the Traefik hostname from `COVERS_CDN_BASE_URL` and pins
the CDN to the local Swarm node where `init-volume.sh` prepares storage. nginx
configuration is a Docker Config named using the file hash; configuration updates
replace the service's config reference on the next deploy. Old immutable configs
can remain until separately cleaned up after rollback requirements are satisfied.

`/healthz` returns `200` with `ok` and `Cache-Control: no-store`. Only flat lowercase
SHA-256 filenames (`/<64 hex characters>.webp`) are served as assets; successful
responses use `image/webp` and `public, max-age=31536000, immutable`. Symlinks,
directory listing and other paths are denied. Only GET and HEAD are accepted.

## Before deployment

1. Select the intended environment and update its encrypted dotenv file yourself.
   Set `COVERS_CDN_BASE_URL` to a full HTTPS origin without credentials, port, path,
   trailing slash, query or fragment. Set both host storage paths. Optionally pin
   `COVERS_CDN_IMAGE` to a tested official Alpine tag/digest instead of the template's
   moving `nginx:alpine` tag. See [environment.md](environment.md).
2. Confirm the deployment account can create/chmod the selected storage paths and
   the local node is active and schedulable in Swarm. The CDN is pinned to this
   node; it cannot fail over to another node with unprepared local storage.
3. Confirm the existing Tunnel and Traefik share `EXTERNAL_NETWORK`. The current
   tunnel repository documents the internal public-hostname service target as
   `http://traefik:80`. In Cloudflare Zero Trust, add the selected CDN hostname to
   that existing tunnel target unless an existing wildcard route already covers it.
   Ensure its DNS route points to the tunnel.
4. Configure a hostname-scoped HTTP-to-HTTPS redirect at Cloudflare and ensure the
   hostname has valid edge TLS. The backend remains HTTP on the private overlay;
   do not add a backend redirect based only on nginx's local HTTP scheme, which
   would redirect already-HTTPS clients repeatedly.
5. Ensure the CDN hostname is publicly readable without an interactive Cloudflare
   Access login. Scope any Access policy adjustment to the CDN hostname only.
   Keep the origin unreachable from the public network and do not publish nginx
   or Traefik's HTTP entrypoint on host interfaces for this service.

Cloudflare routing, DNS, redirect and Access settings are external deployment
prerequisites. They are not changed by this repository or by the agent. The user
requested preparation only and will deploy using the existing procedure in
[scripts_runbook.md](../scripts_runbook.md).

## Local configuration validation

From the repository root, these example derived values are sufficient to validate
the safe template without decrypting real configuration:

```bash
COVERS_CDN_HOST=covers.example.org COVERS_SWARM_NODE_ID=examplelocalnode \
  docker compose --env-file .env.example \
  -f docker-compose.yml -f docker-compose.swarm.yml config -q
bash -n scripts/deploy-orchestrator-swarm.sh .env.example
shellcheck scripts/deploy-orchestrator-swarm.sh
.venv/bin/python -m pytest -q tests/test_covers_cdn.py tests/test_init_volume.py
```

Normal deployment derives the hostname, node ID and config name automatically.
The orchestrator verifies replicas for API, optimizer and CDN. Do not deploy the
example hostname/paths as real installation values.

## Acceptance after your deployment

Set `COVERS_CDN_BASE_URL` to the actual non-secret URL in your shell. Run the
following checks without disabling TLS certificate verification:

```bash
# Must return HTTP 200, not a login redirect.
curl --fail --silent --show-error --head "${COVERS_CDN_BASE_URL}/healthz"
# Must return 301/308 and an HTTPS URL on the same CDN hostname.
curl --silent --show-error --output /dev/null \
  --write-out '%{http_code} %{redirect_url}\n' \
  "http://${COVERS_CDN_BASE_URL#https://}/healthz"
# Must return 405.
curl --silent --show-error --request PUT --output /dev/null \
  --write-out '%{http_code}\n' "${COVERS_CDN_BASE_URL}/healthz"
# Must return 404 and never expose storage contents.
curl --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  "${COVERS_CDN_BASE_URL}/.incoming/test.webp"
```

Inspect the CDN task's health and confirm its bind mount is read-only with no
published service ports. From an external machine, if the origin has a public IP,
try its HTTP entrypoint with the CDN Host header and its port 8080 directly. Both
must fail to reach the CDN; a successful tunneled request alone does not prove
the origin is private. If there is no public origin address, record that topology
and its firewall/interface restrictions instead.

Record the selected environment, HTTP statuses, TLS result and origin-access
evidence without secrets. Task 0.2 and Phase 0 are accepted only after these
external checks pass. Repository validation and an isolated nginx smoke are not
evidence of public routing.

## Recorded preparation evidence (2026-09-29)

The user confirmed that the existing Google service account has access only to
the target Drive folder. This is user-provided Task 0.1 acceptance evidence.

Compose rendering, converted Swarm manifest validation, Bash syntax, ShellCheck
and targeted tests passed. `nginx -t` and an isolated HTTP smoke passed using
`nginx:alpine` at digest
`sha256:df221db836e1754089190208cee7eeda94f233197056426eda74a43ab1abeac2`.
The smoke container had no external network or host ports and only temporary
read-only test assets. GET/HEAD health, the exact asset bytes, MIME/cache headers,
write-method rejection, hidden/symlink path rejection and read-only storage were
checked. No Swarm stack or Cloudflare configuration was changed. Public acceptance
remains pending the user's deployment.

## Rollback

Revert the CDN service/config and corresponding orchestrator changes, then use
the normal deployment procedure for the chosen environment. Preserve storage and
existing assets. Remove external CDN hostname routing only as a separately
authorized Cloudflare change. Current Koha cover fields and the legacy writer
are unaffected by this skeleton.
