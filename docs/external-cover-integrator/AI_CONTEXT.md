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
- DSpace replacement: upload new -> verify -> update Koha links -> delete old -> finalize state.
- UID -> Koha resolver uses existing Elasticsearch `control-number` search; no separate resolver service.
- Protect Integrator-managed `957$c`, `957$3`, and `856` from ordinary MARC overlay with `MARCOverlayRules`.

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
config name are derived by the orchestrator. API cover/state consumers remain
future work. On 2026-09-29 the user confirmed folder-only service-account access;
public CDN HTTPS, redirect and origin isolation await the user's deployment.

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
Required `MAX_RETRY_COUNT` has no implicit default. Success clears retries;
errors increment them, partial failures may retain `pending`, and cutoff forces
`failed` and excludes retries. Backoff is 1, 2, 4, ... seconds from `updated_at`;
zero retries (including manual reset) are immediately eligible. Existing resource
columns survive transitions. SQLite writes are transactional, but selection is
not a worker claim; the future pipeline must serialize each complete record cycle.
39 focused state/schema/export tests passed on temporary DBs on 2026-09-30.
External pipeline wiring remains future work.

Task 3.1 adds read-only `StateMachine.check_source(record_uid, incoming_file_id,
source="cover"|"file")` before `mark_pending` and external client construction.
Use the existing `GoogleDriveUrlParser` for URL-to-ID extraction. Results:
`noop` only for matching ID + `ok`; `needs_sha_check` for new/changed IDs;
`resume` for matching IDs in unfinished cycles; `no_source` for empty sources
(no deletion). Check cover/PDF independently and preserve retry cutoff/backoff.
38 dirty-check/state/schema tests passed on 2026-09-30; a 100-record fixture
confirmed 200 source-level NO-OPs, zero mock Drive/downstream calls and unchanged
state. Task 3.2 consumes this gate; legacy runtime is not wired.

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
Task 3.2 gate acceptance is complete; automatic API workflow invocation remains
pending. The target environment was not identified as dev/prod.

## Important Documents

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

2026-09-30
