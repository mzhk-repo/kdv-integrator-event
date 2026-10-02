# AI_CONTEXT.md

> Compact context for AI agents. **Detailed source documents are authoritative.**
> If this file conflicts with `SPEC.md`, `ROADMAP.md`, an ADR, `external-cover-integrator.md`, or `implementation-plan.md`, follow the detailed source. If detailed sources conflict, do not invent a resolution; inspect the relevant ADR/specification or request a decision.

## Project Summary

External Cover Integrator (`kdv-integrator`) connects Google Sheet/MARCXML workflows, Koha 25.05, Google Drive, DSpace, and a static cover CDN.

Goals: keep cover binaries outside Koha MariaDB, avoid reprocessing unchanged resources, safely synchronize covers/PDFs, maintain stable cross-system identity, deduplicate covers, and recover cleanly from partial failures.

**KISS rule:** if a source is unchanged and the previous cycle is confirmed `ok`, do nothing.

## Key Decisions

- Canonical record ID: MARC `001 = UUIDv7`; Koha `biblionumber` is local only.
- MARC integration fields:
  - `956$p` cover source; `956$u` PDF source;
  - `957$c` final WebP SHA-256;
  - `957$3` DSpace Item UUID;
  - `856$u` DSpace public/Handle links.
- Google Drive File ID is the fast dirty-check.
  - unchanged ID + `status=ok` => immediate NO-OP, no checksum/download/downstream writes;
  - changed ID => fetch `sha256Checksum`;
  - changed ID + same source SHA => update source identity only;
  - missing checksum for a new source => permanent error, not NO-OP.
- Drive files are treated as immutable sources: changed content should normally be a new Drive file/new File ID.
- Empty source fields never mean delete; deletion is an explicit admin operation.
- Covers are content-addressed: `SHA256(final_webp)` is the asset ID/filename. Identical covers deduplicate automatically.
- Normal assets are immutable and atomically published from `.incoming` using same-filesystem `os.replace()`.
- Emergency overwrite of a shared asset is a documented exception requiring backup, targeted CDN purge, and audit log.
- DSpace replacement: list ORIGINAL bitstreams -> upload and verify -> set/read back primary (POST if absent, PUT if present) -> checkpoint -> update/read back both Koha links -> delete matching old filename bitstreams and verify GET returns 404 -> finalize state. A 2026-10-01 smoke showed the existing Item had no primary bitstream while its previous file remained in DSpace; cleanup now discovers same-name files even without a primary. Runtime acceptance is pending.
- UID -> Koha resolver uses existing Elasticsearch `control-number` search; no separate resolver service.
- Drive PDF replacement runs on `resume` as well as `resource_changed`. An unchanged canonical cover reuses its asset through the same checkpoint/read-back/cleanup cycle; it must not route file work through legacy finalization. User logs exposed both bypasses on 2026-10-01; local orchestration regressions cover the fixes and cleanup retry without re-upload.
- On Drive integration and explicit Item update, missing Koha `957$3`/`957$c` are restored from the matching `records` row and MARC read-back is required. Existing values and other subfields are preserved; no state row means no restoration.
- Intranet archived-record `PUT /kdv/api/integrate/{biblionumber}` queues the shared workflow with `force_file_refresh=True`; it refreshes Drive metadata even for the same file ID, replaces the PDF safely, updates metadata, and returns a task ID for UI polling.
- PDF replacement also considers `records.dspace_bitstream_uuid` regardless of filename, but only uses it for cleanup when its saved Item UUID matches the target and the bitstream is present in that Item's ORIGINAL bundle. A stale/missing identity is ignored so upload can continue safely without deleting it. A 2026-10-01 renamed-source smoke exposed empty same-name candidates; stored-identity forwarding is fixed locally, runtime acceptance remains pending. Historical leftovers from completed faulty cycles need separate identification.
- If unchanged-source DSpace link repair gets HTTP 404 for the saved Item UUID, treat that identity as stale: force Drive metadata/SHA refresh, resolve again by `koha.uid`, and recreate the Item/PDF if absent. The shared integration workflow resolves Items only by exact `koha.uid`; the standalone legacy Nightwalker remains unchanged. Only explicit 404 permits recovery; transport, auth and 5xx failures remain failures. Multiple UID matches fail closed. Runtime acceptance requires redeploy.
- Protect Integrator-managed `957$c`, `957$3`, and `856` from ordinary MARC overlay with `MARCOverlayRules`.
- Before discovery, reuse a saved state/Koha Item UUID only after GET verifies its `koha.uid` equals MARC `001`. Only a saved-Item HTTP 404 allows UID discovery; other errors or UID mismatch fail closed. Discovery must report an explicit zero count before creation; malformed/ambiguous responses cannot be treated as absent. This avoids duplicates when discovery indexing lags.

## Architecture Snapshot

```text
Google Sheet -> MARCXML -> Koha
                         | 001 UUIDv7
                         | 956$p / 956$u
                         v
                   kdv-integrator
                    |          |
                    |          +-> DSpace (PDF/item/bitstream)
                    |
                    +-> cover/PDF page -> WebP -> SHA asset
                         -> /data/koha-covers/assets
                         -> nginx -> Traefik -> Cloudflare Tunnel

State: SQLite (WAL), `ok | pending | failed` + `retry_count`.
```

State DB contains integration state only: record UID, source IDs/SHA values, cover asset SHA, DSpace UUIDs, status/retry data. A stored source ID is trusted for NO-OP only after a fully successful `ok` cycle.

## Tech Stack

- Koha 25.05, MARC21, Elasticsearch
- MariaDB
- Python-based `kdv-integrator` CLI/modules (per implementation examples)
- SQLite WAL
- Google Drive API + service account
- DSpace REST API
- WebP
- nginx, Traefik, Cloudflare Tunnel
- Koha REST API / bulk MARC import tools
- pytest-style tests

Do not assume additional frameworks or infrastructure unless the repository/source docs specify them.

## Security Constraints

- Never commit secrets, tokens, credentials, or service-account material.
- Installation-specific values must come from environment/deployment config (`${ENV_VAR}`); do not hardcode production domains/hosts.
- Restrict the Google service account to the required Drive folder.
- Public cover service is read-only; do not expose write methods.
- Origin should not be directly public; external access goes through Cloudflare Tunnel/Traefik.
- nginx gets cover storage read-only; Integrator gets required write access.
- External API/network errors are errors, never “unchanged”.

## Repository Structure

No canonical repository tree is defined by the supplied sources. Do not invent one as an architectural fact.

Referenced/expected artifacts include `.env.example` (or equivalent), env-variable validation, an `integrator` module/CLI namespace in examples, tests/fixtures, deployment configs, and admin scripts/runbooks.

Task 0.1 now defines the repository-specific contract in `environment.md` and
placeholder values in `.env.example`. Reuse `KOHA_API_URL` / `KOHA_OPAC_URL` for the
conceptual staff / OPAC base URLs. `COVERS_CDN_BASE_URL` is a full HTTPS URL.
The host storage settings are consumed by `scripts/init-volume.sh` before Swarm
deploy. Task 0.2 adds a non-root, read-only `covers-cdn` on the existing proxy
network, pinned to the local storage node. Routing hostname and versioned nginx
config name are derived by the orchestrator. API now mounts cover/state storage;
WebP publication and Koha adoption are implemented for explicit Drive covers;
the user confirmed the positive live Koha display path on 2026-10-01. On
2026-09-29 the user confirmed folder-only service-account access. Public CDN
HTTPS asset delivery was verified for a synthetic asset and the user confirmed
Koha cover display; the external redirect remains a separate Cloudflare task.

Runtime storage is not repository structure:
- `/data/koha-covers/assets/` — published assets;
- `/data/koha-covers/.incoming/` — temporary publish area;
- SQLite `state.db` — separate file configured by `COVER_STATE_DB_PATH`.

Task 2.1 implements `records` schema in `src/cover_state/schema.py`, reusing the
export module's `MigrationManager` with WAL enabled. Run
`python -m src.cover_state.schema` with `COVER_STATE_DB_PATH` (or `--db-path`).
Both Compose files mount `COVER_STATE_HOST_PATH` read-write in API at
`/data/kdv_cover_state`; export and cover state use separate files and tables.
Schema version is 1; `record_uid` is the primary key and `status` has an index.
The API entrypoint runs this idempotent migration after loading runtime secrets
and before starting the server; migration errors fail startup. Tests use temporary
DBs. Post-redeploy read-only checks on 2026-09-30 confirmed the API's read-write
state mount and deployed schema code; at that time the live DB was absent.
After the next deployment, user-run read-only container checks on 2026-09-30
confirmed the persistent read-write bind, existing `/data/kdv_cover_state/state.db`,
WAL, version 1, all 11 columns and unique UID/status indexes. Task 2.1 is accepted;
the environment was not identified as dev/prod.
Since host bind paths are node-local,
API placement must use the prepared node or shared storage.
Task 2.2 implements `StateMachine` in `src/cover_state/state_machine.py`:
`mark_pending`, `record_result`, `get_retry_eligible`, `reset_retry_count`, `get`.
Required `MAX_RETRY_COUNT` has no implicit default. Success clears retry metadata;
errors increment retries, partial failures may retain `pending`, and cutoff
excludes automatic retries. Backoff is 1, 2, 4, ... seconds, persisted as
`next_retry_at` alongside `retry_reason` and `defer_reason` (`backoff` or `cutoff`).
Koha `biblionumber` is stored as a local routing hint; the scheduler verifies
MARC `001` before dispatch. The API scheduler atomically claims due rows with a
one-hour lease and submits the shared integration workflow. TaskManager reports
returned deferred results distinctly; exceptions are failed. Koha shows cutoff
records and provides an authenticated operator retry. State writes remain
transactional, and complete cycles use `.workflow.lock`.

Task 3.1 adds read-only `StateMachine.check_source(record_uid, incoming_file_id,
source="cover"|"file")` before `mark_pending` and external client construction.
Use the existing `GoogleDriveUrlParser` for URL-to-ID extraction. Results:
`noop` only for matching ID + `ok`; `needs_sha_check` for new/changed IDs;
`resume` for matching IDs in unfinished cycles; `no_source` for empty sources
(no deletion). Check cover/PDF independently and preserve retry cutoff/backoff.
38 dirty-check/state/schema tests passed on 2026-09-30; a 100-record fixture
confirmed 200 source-level NO-OPs, zero mock Drive/downstream calls and unchanged
state. Task 3.2 consumes this gate; API/Robot core wiring is implemented locally.

Task 3.2 implements `check_drive_metadata` in `src/cover_state/drive.py` using
lazy `GoogleDriveSource.get_metadata` with explicit `sha256Checksum` fields.
It enforces retry eligibility before network access. Confirmed identical SHA
updates only source ID/timestamp; changed SHA returns metadata/hash and enters
pending without committing unconfirmed identities. Unfinished same-SHA cycles
still resume. Missing/empty/invalid checksum is permanent: failed + retry count
at least at the limit until manual reset. Network/client errors increment once;
API exception text is not logged. Resource keys use the proper request header.
103 related tests passed (15 optimizer tests deselected); the unfiltered run had
115 passes and three existing optimizer DPI failures unrelated to this change.
User-provided container smoke output on 2026-09-30 confirmed deployed code hashes,
live binary SHA retrieval and comparison, same-content identity update, and zero
additional calls for the following NO-OP. Google Doc/shortcut missing checksums
and timeout were mocked as authorized by the user; permanent cutoff and one
retry increment passed. Temporary DB cleanup was confirmed by the smoke output.
Task 3.2 gate acceptance is complete. Automatic API/Robot core wiring now requires
MARC `001` UUIDv7 for Drive, checks retries once per locked cycle, reuses metadata,
verifies downloaded SHA and skips confirmed sources. It commits identities/UUIDs
and `ok` only after confirmed required processing and true Koha write-back.
Local/additional sources keep their processing path. Changed PDFs on existing
DSpace Items use the verified replacement flow described below.
122 related tests passed with 17 tests deselected; two additional existing API
DPI tests fail against the current allowlist. Deployment/API runtime smoke of
this wiring was verified by user-provided post-redeploy output on 2026-09-30:
matching code hashes, active API health/readiness 200, persistent WAL/version 1,
and isolated Flask route -> real background task -> gate -> polling. Temporary
state/stub Koha/DSpace clients produced NO-OP with zero Drive calls and a
same-content identity update using live SHA with no downstream calls; cleanup
completed. No integration POST to active Gunicorn or live downstream writes was
tested. The environment was not identified as dev/prod.

## Important Documents

Task 4.1 provides `src/services/cover_pipeline.py:download_and_normalize` and
`python -m src.services.cover_pipeline --source <Drive URL> --output <temporary WebP>`.
It reuses Drive download/auth and optional gate metadata, checks downloaded SHA,
applies EXIF orientation, RGB, metadata stripping, width <=600 without upscale,
and WebP quality 82. Source SHA and final WebP SHA stay separate. Core shares the
download verifier; Task 4.2 supplies publication and Task 4.3 integrates write-back.
Local real-image/stub-Drive tests cover this stage; live acceptance remains open.

Task 4.2 implements `publish_cover(webp_path, storage_path=None, expected_sha256=...)`
in the same module; CLI `--publish` uses `--storage-path` or required
`COVERS_STORAGE_PATH`. Prepared non-symlink `.incoming`/`assets` must share a
filesystem. A shared flock serializes cleanup/dedup/publish; unique staging files
are synced, chmod 0644 and atomically renamed to SHA-named assets, then both
directories are synced. Identical assets are not rewritten; corrupt existing
assets fail closed. SIGKILL can leave a private reserved staging file until the
next locked publish; final assets stay complete. API gets read-write cover root
in both Compose files and Swarm placement on the CDN's storage node. Post-redeploy
checks on 2026-09-30 confirmed 1/1 services, shared node, actual writer/CDN mounts,
matching publisher SHA, expected directory modes and one filesystem. Active
Gunicorn has `COVERS_STORAGE_PATH`; standalone docker exec does not inherit the
entrypoint's sourced env, so diagnostics must read selected non-secret PID 1
variables or load runtime env in their own process. Deployed publisher passed
isolated `/tmp` normalize/publish/dedup/hash/mode/inode/mtime checks and cleanup.
CDN health was 200 internally and over HTTPS with curl/requests; urllib got 403.
Mounted assets were empty during the initial check. A synthetic WebP was then
published: repeated publication preserved inode/mtime and internal/public CDN
returned identical bytes with `image/webp` and one-year immutable cache headers.
Runtime publish/delivery is confirmed for that asset, not through a Koha record
workflow. The test asset remains and may stay cached for a year; dev/prod was
unidentified.
Task 4.3's positive live Koha path was accepted on 2026-10-01.

Task 4.3 adds additive `pending_cover_work` to schema version 1 without changing
the 11 `records` columns or export DB. Explicit Drive covers use WebP publishing
and SHA-only `957$c`; legacy Drive covers without asset SHA are rebuilt. Published
asset SHA and an input/source/completed-DSpace checkpoint survive pending failures.
Confirmed source columns commit only after true Koha PUT and MARC read-back:
`001`/`957$c`, plus `957$3` and required `856$u` for PDF cycles. Matching eligible
retries validate the asset and reuse completed processing, repeating only write-back;
changed inputs/options invalidate the checkpoint. Success deletes it atomically
with `ok`/zero retries. Cutoff/reset behavior remains; changed-PDF replacement
runtime acceptance is still pending.
On 2026-10-01 the user supplied a test-record task log and read-only state/asset
checks: MARC `957$c` matched the mounted WebP SHA, state was `ok` with zero retries,
the checkpoint was absent, and the cover displayed in Koha. Live NO-OP and failure
recovery were not exercised. The environment was not identified. Crashes before
DSpace result checkpointing remain Phase 7 recovery work.

Task 5.1 routes Drive PDF-only `956$u` records through the same WebP publisher and
Koha write-back. It verifies the downloaded PDF SHA, renders the first CropBox
page at 150 DPI with a 15-second timeout, and normalizes to <=600 px WebP quality
82. Matching checkpoint retries reuse the asset and completed DSpace work;
confirmed unchanged PDFs NO-OP. A PDF with no renderable first page permanently
fails only its record; transient render errors use normal backoff. Local PDF paths
retain the CGI pipeline. User supplied a successful live test on 2026-10-01 after
correcting the DSpace collection: Item/PDF upload completed and the cover showed
in Koha. A missing `856$u` was restored by linking the existing Item. Task 7.2
now implements verified changed-PDF replacement; runtime acceptance still
requires deployment and a user-run smoke test. The environment was not identified.

Task 7.1 adds DSpace Item lookup and identity by MARC `001` UUIDv7 in
`koha.uid`. Retries preserve the Item/Handle and can upload the first PDF
when a prior attempt created the Item but stopped before bitstream upload.
Resolution uses `koha.uid`, first verifying a saved UUID directly and then using
discovery if that UUID is absent. Creation requires an explicit zero search
result; unknown response shapes fail closed. For changed Drive PDFs,
upload and verify the new bitstream by size and checksum, set/read back the
ORIGINAL bundle primary bitstream, then persist the result in
`pending_cover_work` before Koha write-back. Keep the old bitstream until both
Koha `856$u` links pass read-back; then delete it. A failed Koha write retains
the checkpoint and both bitstreams; retry reuses the uploaded UUID. User-provided runtime log on 2026-10-01
confirms a new Item, MARC `001` stored in `koha.uid`, successful PDF upload and
task completion. The user also confirmed a successful repeated lookup returning
the same Item with an unchanged Handle. If a later unchanged-source NO-OP finds
either DSpace link missing from Koha, core resolves the existing DSpace Item
and ORIGINAL bitstream by stored UUIDs, rewrites the two DSpace `856` links
(PDF download and repository Handle), and confirms both by read-back.

- `external-cover-integrator.md` — architecture source of truth: identity, MARC fields, dirty-check, state/error semantics, cover/DSpace pipelines, caching, rollback, GC, backup, migration, observability.
- `implementation-plan.md` — implementation source of truth: phase dependencies, deliverables, acceptance criteria, validation, Definition of Done.
- `environment.md` — repository-specific environment contract and existing secret delivery; distinguishes configuration preparation from external acceptance.
- `runbook.md` — CDN deployment prerequisites, Cloudflare routing and external acceptance; records preparation evidence without claiming deployment.
- `SPEC.md`, `ROADMAP.md`, ADRs — when present, detailed source documents; they override this summary.

Do not reread all documents by default. Open the relevant source section when changing an invariant, external contract, failure behavior, deployment behavior, or acceptance criterion.

## Implementation Rules for AI Agents

1. Preserve KISS; do not add queues, resolver services, reference counters, workflow engines, etc. without a source/ADR requirement.
2. Follow implementation dependency order. A phase is complete only after acceptance criteria and validation pass with current evidence.
3. Keep the fast path cheap: unchanged Drive ID + `status=ok` must cause no Drive API call and no downstream processing.
4. Keep source ID/hash separate from derived asset hash.
5. Use `pending` before multi-step write-back; mark `ok` only after all required Koha fields are confirmed.
6. Retries should resume durable work rather than redownload/reconvert/reupload unnecessarily.
7. API/network failure => retry state; enforce `${MAX_RETRY_COUNT}`. Exhausted failures require deliberate manual reset after fixing the cause.
8. Use one SQLite writer or per-record locking.
9. Never delete the old DSpace bitstream before the replacement and Koha link update are confirmed.
10. Never treat empty source fields as delete commands.
11. Preserve immutable/content-addressed behavior except for the documented emergency override.
12. Test idempotency, recovery, atomic publish, deduplication, and the zero-work unchanged path.
13. Do not hardcode deployment-specific values.
14. Observability is a separate optional/final scope; it must not block MVP correctness.

## Known Assumptions

- Bulk-import matchpoint `001 = UUIDv7` is assumed already configured; Phase 1 validates it.
- Checksum-based Drive sources are uploaded binary files; Google-native docs/shortcuts are not automatically processable.
- Cover normalization: WebP, about 600–800 px wide, no upscale, metadata stripped, quality about 82.
- If `956$p` is absent, `956$u` PDF may be used as the cover fallback.
- SQLite WAL is sufficient with single-writer/per-record locking constraints.
- GC is separate from processing and retains old assets long enough for rollback.
- MariaDB + state DB backups are higher priority than asset backups; assets can use less frequent incremental backup.
- Legacy LocalCover cleanup happens only after verified migration.

## Open Questions

Intentionally unspecified/deployment-specific; do not guess silently:
- canonical repository/package layout;
- deployment-specific secret values and external Cloudflare settings (repository secret delivery is defined in `environment.md`);
- concrete URLs, retry limit, retention values, paths, and credentials;
- whether/when hash-sharded asset directories are needed;
- exact PDF optimization tooling/policy;
- monitoring stack and alert channel;
- optional DSpace duplicate identity metadata;
- deployment/orchestration details beyond nginx + Traefik + Cloudflare Tunnel responsibilities.

Record architecture-affecting choices in the proper source document/ADR, not only in code.

## Last Updated

2026-10-01
