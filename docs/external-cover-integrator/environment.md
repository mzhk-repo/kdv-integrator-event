# External Cover Integrator environment contract

Task 0.1 defines configuration and secret delivery for the existing KDV repository.
`.env.example` is the safe template; its URLs, identities, credentials and host paths
are placeholders. Deployment values belong in `env.dev.enc` or `env.prod.enc`.

## Variables

The new cover variables are a contract for subsequent implementation phases.
They are not yet consumed by the API or mounted by Compose. Required values have
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
| `COVERS_CDN_BASE_URL` | Full public HTTPS base URL, without a trailing slash, query or fragment. Asset URL: `${COVERS_CDN_BASE_URL}/{956$c}.webp`. | Required in cover deployment; runtime payload and deployment config |
| `COVERS_STORAGE_HOST_PATH` | Absolute host bind source for the cover storage root. | Required in Phase 0.2; deployment config |
| `COVERS_STORAGE_PATH` | Absolute Integrator container storage root; contains `assets/` and `.incoming/` on the same filesystem for atomic publication. nginx receives only `assets/`, read-only. | Required in cover deployment; runtime payload and deployment config |
| `COVER_STATE_HOST_PATH` | Absolute host bind source for durable cover state; separate from Koha Export state. | Required when implementing Phase 2; deployment config |
| `COVER_STATE_DB_PATH` | Absolute container path to the SQLite DB inside the durable state mount. Its directory must support DB, WAL and SHM files. | Required when implementing Phase 2; runtime payload |
| `MAX_RETRY_COUNT` | Positive integer limiting failed record cycles. Independent of the export module's `MAX_RETRIES`. | Required when implementing Phase 2; template example `5`, not an implicit runtime default |
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

## Validation and external acceptance

Repository validation for Task 0.1 checks that every variable in the table exists
in `.env.example`, the template is valid Bash dotenv, and Koha/DSpace URLs in
`src/config.py` remain required environment values without hostname fallbacks.
Future consumers must validate required cover values when their feature is used:
HTTPS base URL, absolute storage paths, writable Integrator storage/state,
same-filesystem publication and a positive integer retry limit. Phase 0.2 verifies
mounts/networking; Phase 2 implements state configuration and retry enforcement.

Before marking Task 0.1 fully accepted in an environment, confirm that the selected
service account exists and can read the target Drive folder and a sample binary
file. Review its folder shares and inherited access to ensure it has no unnecessary
access elsewhere. Read-only OAuth scopes do not themselves restrict access to one
folder. Record only a secret-free result and the selected environment; never paste
JSON keys or tokens into evidence. Account creation or permission changes require
separate authorization. Repository placeholders do not prove this criterion.

## Transition compatibility

The current pipeline writes a cover URL into `956$c`; the new pipeline writes a
WebP SHA-256. Adding configuration does not switch that meaning. Before enabling
the new writer or changing Koha's cover template, the migration phase must prevent
the old URL writer from overwriting hash values and verify migrated records.
