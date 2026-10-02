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

Writable `/tmp` must be declared as a `volumes` entry with `type: tmpfs`, target
`/tmp` and a 16 MiB size. Do not replace it with the service-level short `tmpfs`
key: that form did not reach the deployed Swarm service's mounts. Docker documents
that Swarm requires the mount form in its [tmpfs reference](https://docs.docker.com/engine/storage/tmpfs/).
The orchestrator normalizes numeric tmpfs size strings from Compose to integers
required by the legacy Stack schema. Tests validate the converted Stack manifest,
not only Compose output or a separately constructed `docker run` command.

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

For Task 6.1, use a known existing SHA-named asset and verify immutable headers
plus public write-method rejection:

```bash
asset_url="${COVERS_CDN_BASE_URL}/<known-existing-sha>.webp"
curl --fail --silent --show-error --head "$asset_url"
for method in PUT POST DELETE; do
  curl --silent --show-error --request "$method" --output /dev/null \
    --write-out "$method %{http_code}\n" "$asset_url"
done
```

Each write method must return `403` or `405`. Use no request body; these requests
must not alter the asset. A successful GET/cache check does not by itself prove
that public write methods are blocked.

Inspect the CDN task's health and confirm its bind mount is read-only with no
published service ports. From an external machine, if the origin has a public IP,
try its HTTP entrypoint with the CDN Host header and its port 8080 directly. Both
must fail to reach the CDN; a successful tunneled request alone does not prove
the origin is private. If there is no public origin address, record that topology
and its firewall/interface restrictions instead.

### Phase 6 implementation and runtime status (2026-10-01)

Repository configuration accepts only GET/HEAD, serves only content-addressed
WebP paths with one-year immutable caching, and mounts assets read-only in a
non-root nginx task without published ports. Earlier deployed checks confirmed
the synthetic asset's public HTTPS bytes and cache headers; the user confirmed a
Koha cover displayed. On 2026-10-01, user output for an existing public asset
showed HTTP/2 200, `image/webp`, `Cache-Control: public, max-age=31536000, immutable`
and `cf-cache-status: HIT`; PUT, POST and DELETE each returned 405. Task 6.1 is
accepted. The deployment environment was not identified; HTTP-to-HTTPS redirect
and origin isolation remain separate Phase 0 checks.

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

### First deployment failure and correction (2026-09-29)

Live read-only diagnostics found repeated nginx exits with
`mkdir() "/tmp/client_body" failed (30: Read-only file system)`.
`ContainerSpec.Mounts` contained only the assets bind: `/tmp` was absent despite
the short `tmpfs` key in the rendered configuration. The original isolated smoke
used `docker run --tmpfs`, which did not validate Swarm mount delivery.

The corrected declaration uses a tmpfs mount entry. The converted Stack manifest
now retains its target and integer 16 MiB size; 31 targeted tests passed. An
isolated container using `--mount type=tmpfs` passed nginx syntax/startup and HTTP
health checks with a read-only root filesystem and dropped capabilities. Its
actual `/tmp` mount was writable with `nosuid,nodev,noexec` and a 16 MiB limit.
The live stack was not redeployed by the agent. After the user's normal redeploy,
confirm mount delivery and task health before public acceptance:

```bash
docker service inspect kdv_integrator_event_covers-cdn \
  --format '{{json .Spec.TaskTemplate.ContainerSpec.Mounts}}'
docker service ps --no-trunc kdv_integrator_event_covers-cdn
docker service logs --tail 20 kdv_integrator_event_covers-cdn
```

Use the selected environment's stack name if it differs. Mounts must include a
writable tmpfs at `/tmp` and a read-only bind at `/usr/share/nginx/html`.

### Post-redeploy routing verification (2026-09-29)

Live read-only checks after the user's redeploy confirmed a running, healthy
nginx task and the `/tmp` tmpfs mount with a 16 MiB limit. Public HTTPS `/healthz`
returned `200` with `ok`, using curl's normal certificate verification. The same
request through internal `http://traefik:80` with the configured CDN Host header
returned `200`. The root `/` returned nginx's configured `404`, both internally
and publicly; this is expected and does not indicate a missing Traefik route.
No Traefik configuration change was needed.

Public HTTP `/healthz` returned `200` without an HTTPS redirect. Configure the
hostname-scoped Cloudflare redirect and repeat its acceptance check. Full Phase 0
acceptance remains pending that redirect and external origin-isolation evidence.

### Task 4.2 post-redeploy verification (2026-09-30)

User-provided output and subsequent direct read-only Docker checks confirmed
three services at `1/1`, API/CDN on `pinokew`, storage-node placement constraints,
API read-write `/srv/kdv-integrator/koha-covers` at `/data/koha-covers` and CDN
read-only `/srv/kdv-integrator/koha-covers/assets` at `/usr/share/nginx/html`.
Deployed publisher SHA matched the repository. Root/assets/incoming modes were
`0755/0755/0700` on the same device, with no directory symlinks.

The first standalone `docker exec` smoke reported missing `COVERS_STORAGE_PATH`.
This was a diagnostic context issue: entrypoint sources `/run/secrets/app_env_payload`
before execing Gunicorn, and a new Docker exec process does not inherit those
process-local environment changes. Targeted reads of the active Gunicorn master
and worker environments confirmed the configured path. The corrected read-only
diagnostic reads only selected non-secret keys from `/proc/1/environ`.

Inside the deployed API, normalization and publication were tested with temporary
storage under `/tmp`: valid 600x800 WebP, matching SHA, mode `0644`, identical
second publication, unchanged inode/mtime and no remaining staging files passed.
Temporary storage deletion was confirmed. No persistent assets/state changed.
Internal CDN `/healthz` and public HTTPS via curl/requests returned `200 ok`;
the initial urllib public request returned `403`. A subsequent synthetic WebP
publication into the real mount returned status 200 from both the internal CDN
and `https://covers.pinokew.buzz/<sha>.webp`. Both responses matched the source
bytes and reported `image/webp` plus `public, max-age=31536000, immutable`.
Repeated publish retained inode/mtime. The asset SHA is
`c6f0dada6b6dc55ca861938b930d68ee46d79e5bd15f5edc6727a81d2e151499`; mode was
`0644`. The synthetic asset remains in storage; avoid deleting it while the CDN
may cache it for a year unless that URL is purged. The environment was not
identified as dev/prod. This confirms Task 4.2 runtime publishing and CDN delivery
for the synthetic asset; Koha write-back and record workflow recovery belong to Task 4.3.

### Task 4.3 deployment and acceptance

The API startup migration creates additive `pending_cover_work` in the existing
cover DB. Deploy the updated API through the normal procedure in the selected
environment, then confirm the table is present and the deployed core/Koha/state
code matches the repository. Preserve both the cover DB and asset storage.

For an approved test record with MARC `001` UUIDv7 and a binary Drive image in
`956$p`, invoke the existing authenticated integration API or Koha UI and poll
the returned task. Confirm:

- MARC `957$c` equals the final WebP SHA and the filename in mounted `assets`.
- `records.status=ok`, retries are zero and confirmed source ID/SHA match the input.
- `pending_cover_work` has no row for that UID after success.
- The CDN URL returns the expected WebP; the OPAC uses that URL for its image.
- An unchanged repeat returns `noop` without Drive/download/downstream processing.

Koha custom cover display must already be configured for the selected deployment:
`CustomCoverImagesURL=<HTTPS CDN origin>/{957$c}.webp`,
`OPACCustomCoverImages=Show` and, for staff display, `CustomCoverImages=Show`.
The MARC field/subfield placeholder syntax is documented in the
[official Koha manual](https://koha-community.org/manual/25.05/fr/html/enhancedcontentpreferences.html#customcoverimagesurl).
Do not disable/remove legacy LocalCover data until the migration is accepted.

Use local temporary-DB/stub-client tests for the failure scenario:

```bash
.venv/bin/python -m pytest -q tests/test_api_drive_gate.py \
  -k 'external_cover_retry or cover_readback or cover_retry_cutoff or pending_cover_corruption'
```

These tests force write-back/read-back failures after publication, reopen the DB
and verify recovery without another download/normalization/completed DSpace job.
Failures retain asset/checkpoint and pending state until cutoff; operator retry
reset preserves the checkpoint. A corrupted/missing staged asset fails closed.
Source/input/options changes invalidate staged work. DSpace crashes before its
result is checkpointed still need Phase 7 recovery. Live Koha/OPAC acceptance for
On 2026-10-01, the user supplied a Drive PNG test-record run. The task completed,
MARC `957$c` matched the published WebP SHA, state was `ok` with zero retries and
no checkpoint, and the cover displayed in Koha. Live NO-OP and failure recovery
remain covered by local tests rather than this run. The environment was not identified.

### Task 5.1 PDF fallback acceptance

After deploying the updated API in a named environment, use an approved UUIDv7
test record with a binary Drive PDF in `956$u` and no `956$p`. Invoke the existing
Koha UI or authenticated API and poll the task. Verify that `957$c`,
`records.cover_asset_sha256` and `assets/<sha>.webp` agree, the WebP shows the
visible first PDF page, state is `ok` with zero retries and no checkpoint, and
the cover appears in Koha. An unchanged repeat should return `noop`.
The local tests cover corrupt/encrypted PDFs and record-level failure isolation;
do not damage a live test record to exercise that case. For Task 7.2 runtime
acceptance, use an approved test record with an existing DSpace Item, change its
Drive PDF, and force Koha link write-back to fail after upload. Verify the old
bitstream remains available, the pending checkpoint records old/new UUIDs, and
retry does not upload another bitstream. After Koha succeeds, verify both `856$u`
values by read-back, the new primary bitstream, old bitstream deletion, and
completed state. Confirm old deletion by checking that DSpace REST GET for its
UUID returns 404; task success and the DELETE response alone do not prove removal.
A 2026-10-01 user smoke found the old bitstream still present after Koha links
updated, so runtime acceptance of deletion remains pending. Local tests cover
the failure/retry sequence; runtime acceptance requires deployment and a
user-run smoke test.

If `get_primary_bitstream()` returns no primary for an existing Item, inspect
the `ORIGINAL` bundle list. Replacement uses the confirmed previous bitstream
UUID from state even when its filename changes, and verifies its Item/bundle
membership before upload. It also selects same-name versions and an existing
primary. The new PDF becomes primary; selected old UUIDs are deleted only after
Koha link read-back. Historical leftovers from completed faulty cycles require
separate identification; unrelated attachments must not be deleted.

Include a PDF replacement with `source=cover action=noop` and a resumed pending
cycle in runtime acceptance. Both must use durable checkpoint/read-back/cleanup;
an unchanged cover keeps its existing asset. If DELETE fails, retry must reuse
the uploaded bitstream and only repeat write-back/cleanup, not upload again.

The user confirmed a successful PDF/Item run on 2026-10-01 after correcting the
record's DSpace collection. A later run restored a removed `856$u` by linking the
existing Item. Task 7.2 now implements verified changed-PDF bitstream replacement;
the end-to-end behavior still needs runtime acceptance after deployment.

On an unchanged-source run, if either DSpace `856$u` link is missing, the
reconciliation path now reads the Item and primary ORIGINAL bitstream and
rewrites both links (PDF download and Handle) in Koha. Runtime acceptance of
this repair path requires redeployment and read-back verification.

If reading the state DB's Item UUID returns HTTP 404 during link repair, the
workflow forces a Drive metadata/SHA check and resolves DSpace again by
`koha.uid`. If no Item exists, it creates a replacement Item and uploads the
PDF, then writes and reads back the new Koha UUID and links. HTTP 404 is the
only response that triggers recreation; timeout, auth and 5xx errors should
remain retryable failures. The shared integration workflow resolves Items only
by exact `koha.uid` matches. The standalone legacy Nightwalker script is outside
this path and remains unchanged. If UID search is ambiguous, stop and resolve
the duplicate Items before retrying. Verify this path after
redeployment with an approved test record whose DSpace Item was deliberately
removed.

## Rollback

Revert the CDN service/config and corresponding orchestrator changes, then use
the normal deployment procedure for the chosen environment. Preserve storage and
existing assets. Remove external CDN hostname routing only as a separately
authorized Cloudflare change. For Task 4.3 rollback, preserve the additive checkpoint
table and assess converted `957$c` SHA values before restoring older application
code or cover templates. Older CGI/JPEG code must not overwrite converted records.
Restoring previous MARC values or changing Koha display settings is a separate
authorized operation.
