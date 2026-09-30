# External Cover Integrator environment contract

Task 0.1 defines configuration and secret delivery for the existing KDV repository.
`.env.example` is the safe template; its URLs, identities, credentials and host paths
are placeholders. Deployment values belong in `env.dev.enc` or `env.prod.enc`.

## Variables

The cover host paths are consumed by the pre-deploy `scripts/init-volume.sh`.
The CDN URL/image and assets host path are used by the orchestrator and
Compose/Swarm CDN service. Task 2.1 adds an explicit state-schema migration and
bind mount for the API using `COVER_STATE_DB_PATH` and `COVER_STATE_HOST_PATH`;
pipeline consumers remain future work.
Required values have
no implicit fallback to an installation-specific domain or host.

| Variable | Purpose and constraints | Default / delivery |
| --- | --- | --- |
| `KOHA_API_URL` | Existing full HTTPS staff base URL, without `/api/v1`; the client appends API/CGI paths. Implements the concept's `KOHA_STAFF_BASE_URL`. | Required; runtime payload |
| `KOHA_OPAC_URL` | Existing full HTTPS public OPAC base URL. Implements the concept's `KOHA_OPAC_BASE_URL`. | Required; runtime payload |
| `KOHA_API_USER` | Existing Koha integration account. | Required; runtime payload |
| `KOHA_API_PASS` | Existing Koha integration account password. | Required secret; runtime payload |
| `DSPACE_API_URL` | Existing full HTTPS DSpace REST base URL, including `/server/api`. | Required; runtime payload |
| `DSPACE_UI_URL` | Existing full HTTPS public DSpace UI base URL. | Required; runtime payload |
| `DSPACE_API_USER` | Existing DSpace integration account. | Required; runtime payload |
| `DSPACE_API_PASS` | Existing DSpace integration account password. | Required secret; runtime payload |
| `DSPACE_SUBMISSION_SECTION` | Existing submission section used by the DSpace client. | `traditionalpageone`; runtime payload |
| `COVERS_CDN_BASE_URL` | Full public HTTPS origin: hostname only after `https://`, without credentials, port, path, whitespace, query or fragment. Asset URL: `${COVERS_CDN_BASE_URL}/{957$c}.webp`. | Required in cover deployment; runtime payload and deployment config |
| `COVERS_CDN_IMAGE` | Official nginx Alpine image used by the static CDN. Set a tested tag/digest for reproducible deployment. | `nginx:alpine`; deployment config |
| `COVERS_STORAGE_HOST_PATH` | Absolute host bind source for the cover storage root. Prepared by `init-volume.sh`. | Required by Swarm pre-deploy; deployment config |
| `COVERS_STORAGE_PATH` | Absolute Integrator container storage root; contains `assets/` and `.incoming/` on the same filesystem for atomic publication. nginx receives only `assets/`, read-only. | Required in cover deployment; runtime payload and deployment config |
| `COVER_STATE_HOST_PATH` | Absolute host bind source mounted read-write in API at `/data/kdv_cover_state`; separate from Koha Export state. Prepared by `init-volume.sh`. Must be available on every node eligible to run API, or API placement must be constrained to the prepared node. | Required by Swarm pre-deploy; deployment config |
| `COVER_STATE_DB_PATH` | Absolute file path for the separate cover state SQLite DB, normally `/data/kdv_cover_state/state.db`; never reuse `EXPORT_DB_PATH`. The directory must support DB, WAL and SHM files. | Required by Task 2.1 migration CLI; runtime payload |
| `MAX_RETRY_COUNT` | Positive integer limiting failed record cycles. Independent of the export module's `MAX_RETRIES`. | Required by Task 2.2 `StateMachine`; template example `5`, not an implicit runtime default |
| `INTEGRATOR_MOUNT_PATH` | Existing root for supported relative local sources; absolute source paths and traversal remain forbidden. | `/mnt/drive`; runtime payload / existing Swarm mount |
| `GDRIVE_ENABLED` | Existing Google Drive source switch. | `false`; runtime payload |
| `GDRIVE_SERVICE_ACCOUNT_FILE` | Existing container path to the service-account JSON secret. Never inline JSON into dotenv. | Template path `/run/secrets/gdrive_service_account_json`; runtime payload |
| `GDRIVE_TMP_DIR` | Existing container download/cache directory. | Template path `/data/kdv_sources/gdrive`; runtime payload |
| `GDRIVE_ALLOWED_MIME_TYPES` | Existing source MIME allowlist. Current PDF pipeline allows `application/pdf`; image support is added in the cover pipeline phase. | `application/pdf`; runtime payload |
| `GDRIVE_MAX_BYTES` | Existing source download size limit in bytes. | `262144000`; runtime payload |
| `GDRIVE_DOWNLOAD_TIMEOUT` | Existing download timeout in seconds. | `300`; runtime payload |
| `GDRIVE_TMP_TTL_SECONDS` | Existing temporary download retention in seconds. | `86400`; runtime payload |
| `EXTERNAL_NETWORK` | Existing external Swarm network used for Traefik routing. | Template `proxy-net`; deployment config |
| `RUNTIME_ENV_SECRET_BASE` | Existing base name for the versioned dotenv secret. | Repository deploy default `kdv_app_env_payload`; deployment config |
| `KDV_APP_ENV_PAYLOAD_SECRET_NAME` | Actual versioned dotenv secret name mounted as `app_env_payload`. | Resolved by existing deployment tooling |
| `GDRIVE_SECRET_BASE` | Existing base name for the versioned service-account secret. | `gdrive_service_account_json`; deployment config |
| `GDRIVE_VAULT_KEY` | Existing Ansible Vault key holding the service-account JSON. | `vault_rclone_service_account_json`; deployment config |
| `GDRIVE_SERVICE_ACCOUNT_SECRET_NAME` | Actual versioned JSON secret name mounted into the Integrator. | Resolved by existing deployment tooling |

The conceptual Koha `*_BASE_URL` names map to existing variables above; do not
introduce duplicate settings with competing values. General API authentication,
optimizer and Koha Export variables retain their existing `.env.example` contract.
Storage paths and retry values are deployment choices; the example does not create
directories or grant permissions.

`COVERS_CDN_HOST`, `COVERS_SWARM_NODE_ID` and `COVERS_NGINX_CONFIG_NAME` are derived Compose inputs, not new
user-managed dotenv settings. The orchestrator validates the configured HTTPS
origin and extracts its hostname for the Traefik router. It obtains the local
Swarm node ID from Docker and pins the CDN there so the host assets exist on the
selected node. nginx configuration is delivered as a Docker Config named using
its SHA-256 prefix; a configuration change therefore updates the service instead
of leaving an unchanged bind mount with stale loaded configuration. Direct Compose validation
requires these derived inputs explicitly; normal deployment computes them.

`scripts/deploy-orchestrator-swarm.sh` passes both host paths from the process
environment (preferred) or `ORCHESTRATOR_ENV_FILE` into `scripts/init-volume.sh`
before secret rendering, image builds and stack deployment. Missing paths stop
deployment. The script creates or repairs only the storage directories:
cover root and `assets/` use `0755`, `.incoming/` and the state root use `0700`.
Existing ownership and stored files are preserved; new directories belong to the
deployment account. This supports the current root Integrator and read-only nginx
asset access. Asset files must also be published with readable permissions by the
future cover pipeline. A future non-root Integrator needs an explicit ownership
migration before changing its runtime UID/GID.

Host paths must be absolute, below a top-level directory, disjoint and free of
symlinks, including existing ancestor directories and managed cover children.
The deployment account needs permission to create directories and adjust their
modes. The script does not elevate privileges and fails if these permissions are
unavailable. It prepares only this node's host paths; the CDN is pinned to that
node. The API bind mount is also node-local unless the host path is backed by
shared storage. Ensure the API task runs on a node where `COVER_STATE_HOST_PATH`
resolves to the same durable data, by preparing every eligible node or constraining
the API service to the prepared node. Do not allow Swarm to create independent,
empty local directories on different candidate nodes.

## Secret delivery and component access

Use the existing flow: SOPS-encrypted environment file -> existing deployment
orchestrator -> versioned Docker Secret -> `/run/secrets/app_env_payload` ->
`scripts/entrypoint.sh` exports the dotenv values to the Integrator process.
The payload is shell-compatible dotenv; quote values when required and never add
shell commands or command substitutions. Do not log decrypted contents.

Google credentials use the existing separate flow:
`scripts/render-versioned-gdrive-secret.sh` reads `GDRIVE_VAULT_KEY` from Ansible
Vault and prepares the versioned Docker Secret mounted at
`GDRIVE_SERVICE_ACCOUNT_FILE`. Use the existing read-only Google Drive client.

In Phase 0.2, nginx receives public routing/configuration and a read-only asset
mount; it must not receive the Integrator's dotenv payload, Koha/DSpace credentials,
Google secret, state DB or `.incoming` directory. Traefik and Cloudflare Tunnel
reuse their existing deployment configuration; tunnel credentials do not belong
in this contract or the public template.

The CDN runs as the image's `nginx` user on internal port 8080, with a read-only
root filesystem, dropped capabilities and a writable `/tmp` tmpfs. No host ports
are published. `/tmp` uses the long mount form (`volumes: type: tmpfs`) required
for Swarm; the orchestrator converts the serialized size to a Stack-compatible integer.
The existing Tunnel routes the public hostname to Traefik's
internal HTTP entrypoint; public TLS and HTTP redirects are handled at Cloudflare.
See [runbook.md](runbook.md) for the required external setup and acceptance checks.

## Validation and external acceptance

Repository validation for Task 0.1 checks that every variable in the table exists
in `.env.example`, the template is valid Bash dotenv, and Koha/DSpace URLs in
`src/config.py` remain required environment values without hostname fallbacks.
Future consumers must validate required cover values when their feature is used:
HTTPS base URL, absolute storage paths, writable Integrator storage/state,
same-filesystem publication and a positive integer retry limit. Phase 0.2 verifies
mounts/networking; Phase 2 implements state configuration and retry enforcement.

Task 2.1 reuses `src.export_module.db.schema.MigrationManager` for the separate
cover schema in `src/cover_state/schema.py`. `scripts/entrypoint.sh` runs
`python -m src.cover_state.schema` after loading runtime env and before starting
the API. A migration error fails API startup so deployment health checks fail.
The CLI remains available for explicit maintenance with `COVER_STATE_DB_PATH`
or `--db-path`; it does not load dotenv files or require Koha/Drive/DSpace
credentials. Migration creates missing parent directories, checks WAL and records
schema version 1 atomically with the table/index DDL. Export keeps its own schema
and journal mode. Before environment use, provide a
persistent directory mount for the cover DB, including WAL and SHM. Both Compose
files mount the prepared host directory read-write at `/data/kdv_cover_state` in
the API. Confirm node placement and the service's actual mount before running the
migration. Use SQLite's backup API or a coherent backup of the DB and journal files;
copying only a live WAL-mode DB can omit updates.

Task 2.2 `StateMachine()` reads `COVER_STATE_DB_PATH` and `MAX_RETRY_COUNT` directly
from the already-loaded runtime environment. Explicit `db_path` and integer
`max_retry_count` constructor arguments support isolated tests and callers with
existing configuration. Missing/invalid retry limits fail before DB migration;
no default limit is supplied. The API startup migration requires only the DB path;
the API/Robot core uses the state machine when `COVER_STATE_DB_PATH` is configured.
Retry selection applies exponential delays of 1, 2, 4, ... seconds after failures,
without sleeping. An operator reset to zero bypasses the delay while preserving
status and resource identities. Pipeline callers must serialize complete record
cycles; selecting eligible rows does not lock them for processing.

Task 3.2 `check_drive_metadata()` reuses the existing `GDRIVE_ENABLED` and
`GDRIVE_SERVICE_ACCOUNT_FILE` contract through `GoogleDriveSource`; authentication
remains read-only. Tests can inject `drive_source` and its existing `drive_client`.
No new env variables are required. Permanent checksum failures are encoded as
`failed` with `retry_count >= MAX_RETRY_COUNT` in the unchanged version-1 schema.
An explicit reset is required after fixing the source; transient failures increment
once and remain subject to backoff/cutoff. The metadata gate does not download
files itself; the API/Robot core now invokes it before materialization. No new
environment settings are required for wiring. Drive records require MARC `001`
UUIDv7. The state directory must also allow creation of `.workflow.lock`;
all cooperating API/Robot processes must share the same durable state filesystem.
Configured cycles are serialized by this lock. Invocations without a state DB
configuration retain the legacy path; normal API startup requires the configured
DB migration. User-provided post-redeploy output on 2026-09-30 confirmed deployed
code, active API health/readiness and isolated API/task/gate execution with live
Drive metadata and temporary state; live downstream writes were not exercised.

Before marking Task 0.1 fully accepted in an environment, confirm that the selected
service account exists and can read the target Drive folder and a sample binary
file. Review its folder shares and inherited access to ensure it has no unnecessary
access elsewhere. Read-only OAuth scopes do not themselves restrict access to one
folder. Record only a secret-free result and the selected environment; never paste
JSON keys or tokens into evidence. Account creation or permission changes require
separate authorization. Repository placeholders do not prove this criterion.

On 2026-09-29 the user confirmed that the existing service account has access only
to the target Drive folder. This records user-provided acceptance evidence;
the agent did not perform a new Google API or permission audit.

## Transition compatibility

The Integrator now writes its cover value to `957$c`; existing records may still
have legacy values in `956$c` and `956$3`. Migrate and verify those values before
enabling a Koha overlay rule that protects `957` while allowing `956` updates.
The new cover pipeline stores a WebP SHA-256 in `957$c`, so the legacy URL writer
must not run against records after that transition.
