# CHANGELOG 2026 VOL 05

Цей том продовжує `CHANGELOG_2026_VOL_04.md`, який досяг soft limit ротації.

## 2026-10-01 — Verify DSpace old bitstream deletion (Task 7.2)

- **Context:** Після двох redeploy smoke користувач підтвердив, що попередній PDF лишився у DSpace; другий task завершився успішно, але не містив повідомлення про підтверджене видалення.
- **Change:** `DSpaceClient.delete_bitstream()` після DELETE читає bitstream UUID назад і приймає видалення лише коли GET повертає 404; UUID, що досі доступний, спричиняє помилку task. Додано логи вибору primary UUID та cleanup старого/нового UUID, щоб визначити, чи гілка cleanup викликається. Runbook і план відображають фактичний стан acceptance.
- **Verification:** `PYTHONPATH=. .venv/bin/pytest -q tests/test_contracts.py tests/test_api_drive_gate.py -k 'bitstream or replacement'` — 6 passed, 36 deselected; `.venv/bin/python -m compileall -q src/dspace.py` і `git diff --check` пройшли. Нові логи потребують redeploy для runtime діагностики.
- **Risks:** За поточним log неможливо відрізнити старий runtime image від сценарію без визначеного старого primary UUID. Видалення ще не підтверджене runtime.
- **Rollback:** Повернути `delete_bitstream()` до перевірки лише статусу DELETE та відкликати цю зміну документації/тесту.

## 2026-10-01 — Safe DSpace PDF bitstream replacement (Task 7.2)

- **Context:** A changed Drive PDF for an existing DSpace Item must replace its bitstream without losing the old file when upload verification or Koha link write-back fails.
- **Change:** Upload and verify the new bitstream by size/checksum, set and read back the ORIGINAL bundle primary bitstream, then checkpoint the completed DSpace result before Koha write-back. Retain the old bitstream until both Koha `856$u` links pass read-back; then delete it. Retry reuses the checkpointed new UUID and deletion accepts an already absent old bitstream. Updated architecture, implementation plan, AI context and runbook.
- **Verification:** `PYTHONPATH=. .venv/bin/pytest -q tests/test_contracts.py tests/test_core.py tests/test_api_drive_gate.py` — 78 passed. Tests cover upload verification, primary-bitstream switch, Koha write failure, retry without a second upload, and old-bitstream deletion only after link read-back. `python -m compileall -q src/core.py src/dspace.py` and `git diff --check` passed. No live Koha/DSpace records or services were changed; runtime acceptance requires deployment and user smoke.
- **Risks:** DSpace/Koha runtime behavior and deployed REST permissions remain unverified. Preserve the cover state DB/checkpoints during deployment and rollback; do not manually delete either bitstream while a replacement is pending.
- **Rollback:** Revert the replacement flow and related tests/docs through the normal deployment process. Preserve DSpace items/bitstreams and the state DB until pending replacement checkpoints are resolved; no destructive migration is involved.

## 2026-10-01 — Reconcile both DSpace 856 links on unchanged-source runs

- **Context:** User log showed the Drive gate returned `noop` for an unchanged `956$u` after the DSpace Handle `856$u` had been removed from Koha. The early NO-OP return skipped link reconciliation.
- **Change:** On confirmed `ok` records only, if either DSpace link is absent, fetch the existing Item and ORIGINAL bitstream by stored UUIDs and rewrite the two DSpace `856` fields: PDF download and repository Handle. Require successful Koha write and read-back of both links. No Drive download, PDF processing or DSpace upload occurs. Updated architecture, AI context and Task 7.1 plan.
- **Verification:** `PYTHONPATH=. .venv/bin/pytest -q tests/test_api_drive_gate.py tests/test_contracts.py tests/test_core.py` — 73 passed. Coverage includes unchanged-source repair, both DSpace links and Koha `856` rewriting. Python compilation and `git diff --check` passed. No live Koha/DSpace record was changed; runtime verification requires redeployment.
- **Risks:** A failed link repair leaves the state `ok`; a later request can retry the reconciliation. Runtime repair/read-back remains unverified until redeploy and user smoke.
- **Rollback:** Revert the link reconciliation helper, Koha `856` rewrite, tests and documentation; no database migration or remote data change is involved.

## 2026-10-01 — Confirm DSpace UID retry lookup and stable Handle (Task 7.1)

- **Context:** The initial successful runtime cycle confirmed DSpace Item creation, `koha.uid` metadata and PDF upload; repeated lookup and Handle stability were still to be checked.
- **Change:** Recorded the user's runtime acceptance in the implementation plan, architecture and AI context.
- **Verification:** The user confirmed that a repeated DSpace search finds the same Item and its Handle remains unchanged. Together with the previously supplied successful upload log, both Task 7.1 acceptance criteria are confirmed.
- **Risks:** This does not verify changed-PDF bitstream replacement or recovery, which remain Task 7.2.
- **Rollback:** Documentation-only evidence update; no runtime data or services changed by this documentation update.

## 2026-10-01 — Confirm first DSpace Item/bitstream runtime cycle (Task 7.1)

- **Context:** The user ran the updated integration after creating DSpace metadata field `koha.uid`.
- **Change:** Recorded the runtime acceptance evidence in the implementation plan, architecture and AI context.
- **Verification:** User-provided log shows Drive source accepted, DSpace processing for Koha biblio 70, PDF upload to the Item, and successful task completion. The user confirmed `koha.uid` contains MARC `001`. This verifies the first positive cycle; retry lookup and stable Handle across repeated runs were not exercised.
- **Risks:** Existing-bitstream replacement/recovery remains Task 7.2. Runtime Handle stability still needs a repeated-run check.
- **Rollback:** Documentation-only evidence update; no runtime data or services changed by this documentation update.

## 2026-10-01 — DSpace Item UUID identity and first-bitstream retry (Task 7.1)

- **Context:** The DSpace workflow found Items by Koha-local `biblionumber`, and a retry after Item creation but before the first PDF upload only linked the incomplete Item.
- **Change:** Added DSpace lookup by exact `koha.uid` metadata, writes MARC `001` UUIDv7 into new Item metadata, and reuses the matched Item/Handle. An existing Item without an ORIGINAL bitstream receives the initial PDF on retry. DSpace lookup/bundle API failures now fail closed rather than being mistaken for missing state. Legacy `biblionumber` lookup remains as fallback; replacement of an existing bitstream remains Task 7.2. Updated architecture, AI context and implementation plan.
- **Verification:** `PYTHONPATH=. .venv/bin/pytest -q tests/test_contracts.py tests/test_core.py` — 49 passed. Coverage includes exact UID search, UUID metadata, stable existing Item/Handle, same-Item first-upload retry, and existing DSpace/core contracts. `git diff --check` passed. No DSpace server or live record was changed.
- **Risks:** DSpace must have `koha.uid` registered and indexed for discovery search; this repository change does not configure the DSpace metadata registry. Live server acceptance remains pending. Existing bitstream replacement and recovery after changed PDF remain Task 7.2.
- **Rollback:** Revert the UID lookup/metadata and retry behavior with related tests/docs. Preserve DSpace Items and bitstreams; no remote data was changed.

## 2026-10-01 — PDF first-page WebP cover fallback (Task 5.1)

- **Context:** Drive PDF records without a separate `956$p` cover still used the legacy Koha CGI/JPEG generator instead of content-addressed WebP and SHA-only `957$c`.
- **Change:** Render the first CropBox page of verified Drive PDFs with Poppler at 150 DPI and a 15-second timeout, normalize it with the existing 600-pixel/quality-82 WebP policy, then reuse atomic publication, durable checkpoint, Koha write-back/read-back and source commit. Matching retries reuse the published asset and completed DSpace result; confirmed PDF covers without an asset SHA are rebuilt. PDFs with no renderable first page permanently fail only their record; transient render errors use normal retry policy. Updated implementation plan, architecture, AI context, environment contract and runbook.
- **Verification:** Real-PDF/Poppler, corrupt-PDF, protected-PDF mock, API/core/state/publisher/Koha/Robot regression checks: 162 passed, 3 deselected existing optimizer DPI tests. Tests confirm WebP dimensions/hash, CropBox/page-one selection, `failed` isolation, next-record success, reopened-DB retry without repeated PDF download/render/DSpace, unchanged NO-OP, preserved asset inode/mtime and local-cover compatibility. Python compilation and `git diff --check` passed. No deployment or live PDF record was changed.
- **Risks:** Runtime PDF/Koha/OPAC acceptance remains pending. Local PDF paths retain the legacy CGI path because they have no Drive identity tracking. A changed PDF for an existing DSpace Item still fails closed until Phase 7 bitstream replacement. Preserve the cover DB and assets during deployment/rollback.
- **Rollback:** Revert the PDF fallback branch and related tests/docs through the existing deployment process; retain state, checkpoints and published assets. Assess SHA-converted MARC records before restoring an older cover writer.

## 2026-10-01 — Confirm PDF cover runtime and changed-file guard (Task 5.1)

- **Context:** The user supplied test-record logs after correcting its DSpace collection.
- **Change:** Recorded the live PDF/Koha success and the expected boundary for changing the PDF linked to an existing DSpace Item in architecture, implementation plan, AI context and runbook.
- **Verification:** User logs show the PDF bitstream upload and task completion; the user confirmed correct Koha fields and visible cover. Another record restored a removed `856$u` by linking its existing Item. Replacing `956$u` with a different PDF for that Item correctly stopped at `Changed Drive PDF requires DSpace bitstream replacement`. Environment was not identified.
- **Risks:** Existing-item PDF replacement and safe old-bitstream cleanup remain unimplemented until Task 7.2. The failed attempt may have a pending retry/checkpoint; verify state before retrying after future code changes. Do not delete the existing DSpace bitstream manually.
- **Rollback:** Documentation evidence only; preserve the generated cover asset and existing DSpace Item/bitstream.

## 2026-10-01 — Accept public immutable and read-only CDN behavior (Task 6.1)

- **Context:** The user supplied public method and response-header checks for a known SHA-named WebP asset.
- **Change:** Recorded completion of Task 6.1 in the implementation plan and CDN runbook.
- **Verification:** The asset returned HTTP/2 200, `image/webp`, content length 16974, `Cache-Control: public, max-age=31536000, immutable`, and `cf-cache-status: HIT`. Public PUT, POST and DELETE each returned 405. User-provided evidence; deployment environment remains unidentified.
- **Risks:** This confirms the public CDN contract for the supplied asset. HTTP-to-HTTPS redirect and origin isolation remain separate Phase 0 checks.
- **Rollback:** Documentation-only update; no runtime service or asset changed.

## 2026-10-01 — Record static cover service acceptance boundary (Task 6.1)

- **Context:** Phase 6's nginx and Compose/Swarm implementation already enforces immutable WebP delivery and read-only assets, while the implementation plan listed only generic curl checks.
- **Change:** Recorded the current implementation/runtime evidence and added explicit public PUT/POST/DELETE checks against a known asset URL to the phase plan and CDN runbook.
- **Verification:** Repository configuration was inspected: nginx allows only GET/HEAD and matches only lowercase SHA-256 WebP paths; the CDN runs non-root with a read-only asset mount, no secrets or published ports. Prior deployed evidence confirms public HTTPS 200, byte-identical synthetic asset delivery, `image/webp` and one-year immutable cache headers; the user confirmed a real Koha cover displayed. Public write-method responses remain unverified. `git diff --check` passed.
- **Risks:** Phase 6 runtime acceptance remains partial until each public write method returns 403/405. The environment is unidentified; external origin-isolation and redirect checks remain separate Phase 0 acceptance.
- **Rollback:** Documentation-only change; no service or stored asset was changed.

## 2026-10-01 — Confirm live Koha cover write-back (Task 4.3)

- **Context:** The user tested an approved Koha record with a Drive PNG in `956$p` after deployment.
- **Change:** Recorded positive Phase 4 runtime acceptance in architecture, AI context, implementation plan, environment contract and runbook.
- **Verification:** User logs show Drive `resource_changed`, download and task success. MARC `957$c` was `59a0918a906ac75993065e6877e312208142e38945ba5bb1b06134283c700bc6`; user-run read-only container checks showed state `ok`, zero retries, no checkpoint, an existing WebP asset and matching byte SHA. The user confirmed the cover appears in Koha. Live NO-OP and failure recovery were not exercised in this run; local tests cover them. The environment was not identified.
- **Risks:** This evidence covers one positive record; it does not prove changed-PDF replacement or all future records.
- **Rollback:** Documentation evidence only; preserve the live MARC value, state DB and published asset.

## 2026-10-01 — Durable WebP Koha SHA write-back and retry recovery (Task 4.3)

- **Context:** Explicit Drive covers need SHA-only `957$c` write-back and recovery after Koha failures without repeating completed download/normalization/PDF work.
- **Change:** Wired the existing WebP downloader/publisher into the shared API/Robot core for explicit Drive covers. Added idempotent `pending_cover_work` to the cover DB, preserving schema version 1 and all 11 record columns. Published asset SHA and an input/source/completed-DSpace checkpoint persist before Koha write-back; confirmed source IDs/SHA remain unchanged until success. Matching eligible retries validate the asset and reuse completed work. Changed inputs/options invalidate the checkpoint. A true PUT must pass MARC read-back of UID/hash and required DSpace UUID/links before atomic source/UUID/ok completion and checkpoint removal. Previously confirmed Drive covers without asset SHA are rebuilt; local/PDF fallback keeps its legacy path. Updated architecture/context/environment/plan/runbook and acceptance instructions.
- **Verification:** Relevant API/core/Robot/cover/state/Koha-contract/export checks: 152 passed, 14 deselected. Tests cover reopened-DB retry with no repeated download/normalize/completed DSpace job, asset inode/mtime preservation, confirmed source commit, missing read-back values, cutoff/manual reset, changed inputs, corrupt assets, additive migration, old-cover conversion and regressions. An earlier broader run had one existing Robot API payload test fail because it requests 200 DPI outside the unchanged 100–150 allowlist; unrelated optimizer/DPI cases were excluded from the final run. Python compilation and `git diff --check` passed. No deployment or live Koha/Drive/DSpace/state write was performed.
- **Risks:** Runtime acceptance requires user-run redeployment, the additive checkpoint table and an approved test record with configured Koha custom-cover display. A crash between DSpace completion and result checkpointing still needs Phase 7 recovery; a mere existing Item link remains insufficient for changed PDF replacement. Checkpoints describe matching input identities/options; local/additional source contents remain outside Drive identity tracking. Preserve DB/assets and include the checkpoint table in backups. Task 4.3/Phase 4 live acceptance remains open.
- **Rollback:** Revert the new core/checkpoint/read-back logic and related tests/docs through the existing deployment procedure. Preserve state/assets/checkpoint data and assess SHA-converted MARC records before restoring older CGI/JPEG code or cover URL templates; any MARC/config rollback is a separately authorized operation.

## 2026-10-01 — Confirm mounted WebP publication and CDN delivery (Task 4.2)

- **Context:** The user supplied the requested real-mount synthetic asset smoke output after redeployment.
- **Change:** Recorded runtime acceptance in the implementation plan, architecture, AI context, environment contract and CDN runbook.
- **Verification:** User output confirms publication at `/data/koha-covers/assets/<sha>.webp`, mode `0644`, matching asset SHA, deduplication by unchanged inode/mtime, and byte-identical HTTP 200 responses from internal and public CDN. Both set `image/webp` and `public, max-age=31536000, immutable`. `git diff --check` passed.
- **Risks:** This verifies a synthetic asset and the static serving path, not Drive-to-Koha record processing or Koha write-back. The asset remains in mounted storage and may be cached for one year. Deployment environment was not identified.
- **Rollback:** Revert these evidence-only documentation changes. Keep the test asset until its CDN cache is purged or expires.

## 2026-09-30 — Verify deployed publisher and cover storage mounts (Task 4.2)

- **Context:** The user redeployed Task 4.2 and supplied read-only smoke output showing correct mounts/code hashes but a missing `COVERS_STORAGE_PATH` assertion, then authorized agent Docker socket access.
- **Change:** Corrected the temporary diagnostic to read only selected non-secret PID 1 environment variables and recorded deployment/runtime evidence in architecture, context, environment, plan and CDN runbook. Runtime application configuration/code was not changed.
- **Verification:** User output and direct read-only checks confirmed all three services `1/1`, API/CDN on `pinokew`, expected storage-node constraints, API read-write cover root, CDN read-only assets, matching deployed publisher SHA and directory modes 0755/0755/0700 on one device. Active Gunicorn master/worker had the configured storage path; Docker exec's separate environment did not inherit the entrypoint-sourced payload. Corrected diagnostics passed. The deployed publisher passed isolated `/tmp` normalization/publication/dedup/SHA/mode/inode/mtime checks, with cleanup confirmed. Internal CDN health and public HTTPS via requests/curl returned 200; the initial urllib public request returned 403. Bash syntax, ShellCheck for the diagnostic and `git diff --check` passed.
- **Risks:** Persistent assets were empty, so actual mounted publication and CDN asset delivery remain unverified. The smoke used isolated temporary storage; no persistent assets/state or Koha/Drive/DSpace records changed. Dev/prod was not identified. Koha write-back/recovery remains Task 4.3.
- **Rollback:** Revert documentation evidence only; preserve runtime mounts/storage/state. The temporary diagnostic fix does not affect deployed services.

## 2026-09-30 — Content-addressed atomic WebP publication (Task 4.2)

- **Context:** Normalized covers need deduplicated immutable storage with no partial files exposed by the CDN, including during interrupted publication.
- **Change:** Added `publish_cover()` and CLI `--publish`/`--storage-path` to the existing cover pipeline. The publisher validates WebP/SHA and prepared non-symlink storage on one filesystem, serializes writers with flock, writes a unique private staging file, flushes/fsyncs, sets mode 0644 and uses `os.replace()` to publish `assets/<sha256>.webp`, syncing both directories before success. Identical existing assets are reused without inode/mtime changes; corrupt assets fail closed. Normal failures remove their temporary file; the next locked publisher removes reserved staging leftovers after SIGKILL. Both Compose definitions mount the cover root read-write in API; the orchestrator exports the selected container path and Swarm API is pinned to the CDN storage node. Updated architecture, context, environment and plan.
- **Verification:** Publisher, normalization, CDN and volume tests: 47 passed. Real child-process SIGKILL during a partial staging write, before rename and after rename confirmed final files are absent or complete, old assets survive and retry cleans private staging files. Tests also verify parallel deduplication, preserved inode/mtime, permissions, checksum/image/storage rejection, simulated cross-filesystem and sync/rename failures, CLI publication and writer/CDN mounts in Compose and the converted `docker stack config` manifest. Bash syntax, ShellCheck and `git diff --check` passed. Tests used temporary storage and configuration rendering; no deployment occurred.
- **Risks:** SIGKILL cannot run cleanup; private `.incoming/publish-*.tmp` may remain until the next publication, but is never served. All publishers must use the shared lock/naming convention. The selected storage node must also satisfy the existing manager-zone label. Actual runtime mounts and CDN read access after redeployment remain unverified. Koha write-back and durable record-level recovery remain Task 4.3; no live assets, state or external records were changed.
- **Rollback:** Revert publisher/CLI, mount/placement/export changes and related tests/docs through the existing deployment process. Preserve storage assets/state; reverting API placement may require reassessing node-local state availability.

## 2026-09-30 — Drive cover download and WebP normalization (Task 4.1)

- **Context:** Phase 4 needs a verified binary download and canonical WebP stage before content-addressed publication and Koha SHA write-back.
- **Change:** Added callable/CLI `src/services/cover_pipeline.py`, reusing the existing Drive resolver, authentication and downloader with optional gate metadata. Downloaded bytes must match the valid Drive SHA before decoding. Pillow applies EXIF orientation, RGB, metadata removal, proportional width reduction to 600 px without upscale and WebP quality 82. The result returns separate source and asset SHA values. Core reuses the extracted download verifier. Updated architecture, AI context and Task 4.1 validation instructions.
- **Verification:** Temporary-file image/stub-Drive, core and metadata-gate tests: 48 passed, 5 optimizer tests deselected. Checks cover real WebP decoding, orientation, no upscale, RGB, metadata removal, deterministic hashes, real downloader execution with stub Drive, checksum rejection before decode, invalid images and source preservation. API/Drive workflow regressions: 8 passed. CLI help and `git diff --check` passed. Checks used explicit dummy environment settings; the API test token had to match its existing fixture.
- **Risks:** Live Drive download of this stage remains unverified. The stage writes temporary normalized output; atomic asset publication, durable publish recovery and WebP/Koha workflow adoption remain Tasks 4.2–4.3. Existing CGI/JPEG output continues until that integration. No live state, external records or services were changed.
- **Rollback:** Revert the new stage/tests/docs and restore the inline core download verifier; preserve runtime storage and state DB.

## 2026-09-30 — Verify deployed API route and Drive gate after redeployment

- **Context:** The user redeployed the workflow wiring and supplied output from the prepared container smoke after agent Docker exec access was denied.
- **Change:** Recorded deployed-code and isolated API/task/gate execution evidence in architecture, AI context, environment contract and implementation plan.
- **Verification:** User output confirms matching workflow code hashes, HTTP 200 health/readiness on the active API, persistent SQLite WAL/version 1 and live binary checksum availability. A Flask test client in a separate deployed-container process invoked the API route, actual background task and status polling with temporary state/stub Koha/DSpace: NO-OP caused zero Drive calls; a same-content identity change compared live SHA and updated only temporary state with zero downstream calls. Temporary DB cleanup completed. `git diff --check` passed.
- **Risks:** The integration requests ran in the isolated process, not against active Gunicorn. Live Koha/DSpace writes, changed-content write-back, active external authentication modes and Robot execution were not exercised. Dev/prod was not identified; persistent application state and external records were untouched.
- **Rollback:** Revert this documentation evidence only; runtime state is unaffected.

## 2026-09-30 — Connect Drive gate to automatic API/Robot workflow

- **Context:** The metadata gate passed direct deployed smoke checks but was not invoked by the automatic integration workflow.
- **Change:** Wired the shared core entry point used by authenticated API and Robot to the gate using MARC 001 UUIDv7. Added a shared filesystem cycle lock, single eligibility check per cycle, metadata reuse, downloaded SHA verification, Drive cover resolution and selective cover/PDF processing. Confirmed IDs/SHA and DSpace UUIDs commit atomically only after required processing and true Koha write-back. Unchanged cycles return noop; cutoff/backoff return deferred. Changed PDFs that merely link existing DSpace Items fail closed because bitstream replacement is not implemented. Updated architecture/context/environment/plan.
- **Verification:** Related API/gate/core/services/state tests — 122 passed, 17 deselected. Route-to-core invocation, zero-work second request, same-content ID update, cover-only processing, checksum and write-back failures, missing UID/checksum, two-source retries and concurrent requests passed. Two included API DPI tests initially failed due to the existing allowlist mismatch; the 15 optimizer tests were excluded as previously documented. `git diff --check` passed. No deployment, live DB or external write was performed.
- **Risks:** API runtime acceptance requires user-run redeployment and smoke in an identified environment. Global locking serializes configured cycles. Local/additional sources remain outside Drive identity tracking. Existing DSpace PDF replacement/recovery and WebP/CDN publishing require their planned phases; a retry does not substitute for replacement.
- **Rollback:** Revert the workflow wiring/state completion and Drive cover changes with related tests/docs; preserve state DB/resources and assess previously confirmed entries before enabling gate again.

## 2026-09-30 — Verify deployed Drive metadata gate (Task 3.2)

- **Context:** The user supplied a binary Drive file and authorized mocks for Google Doc/shortcut cases, then ran the prepared container smoke after agent Docker exec access was denied.
- **Change:** Recorded user-provided acceptance evidence in architecture, AI context and implementation plan. Task 3.2 metadata gate acceptance is complete; automatic API workflow wiring remains pending.
- **Verification:** User output confirms deployed code matches repository hashes, live binary SHA retrieval, initial resource-changed/pending state, same-content comparison against a seeded test state with source ID update/ok, and a subsequent NO-OP with zero additional Drive calls. Authorized Doc/shortcut mocks produced missing-checksum failed/cutoff states; a mocked timeout produced failed/retry_count=1. Temporary state DB cleanup completed.
- **Risks:** Google Doc/shortcut and network failures were mocked; no live negative-source checks were performed. The smoke invokes the deployed gate directly and does not prove automatic API workflow invocation. Dev/prod was not identified. Persistent application state and Drive files were not modified by the smoke.
- **Rollback:** Revert this documentation evidence only; runtime data is unaffected.

## 2026-09-30 — Drive metadata/SHA gate and permanent checksum failures (Task 3.2)

- **Context:** Changed Drive IDs need checksum comparison; missing checksums must stop processing, while network/quota failures must enter bounded retries.
- **Change:** Added `src/cover_state/drive.py` using the fast gate and existing read-only Drive authentication. Metadata explicitly requests SHA-256. Confirmed same-content updates preserve downstream resources; changed content enters pending without committing unconfirmed source identity/hash. Added atomic permanent-failure cutoff and per-record retry eligibility in the state module. Corrected resource-key delivery through the documented HTTP header in metadata/download requests. Updated architecture/context/environment/plan.
- **Verification:** Related tests with `-k 'not optimizer'` passed: 103 passed, 15 deselected, including checksum comparison, zero-call fast paths, missing/invalid checksums, permanent reset, transient cutoff, safe logs and offline real SDK requests. The unfiltered target suite had 115 passed / 3 failed due to existing optimizer tests requesting 250/300 DPI outside the committed 100–150 allowlist; optimizer code/tests were unchanged. `git diff --check` passed. No live DB, service or Drive file was changed.
- **Risks:** Live Drive binary/Doc/shortcut acceptance remains unverified; Phase 3 runtime acceptance stays open. Permanent failures saturate the retry counter at the limit in schema version 1 and require manual reset. External pipeline wiring remains pending; callers must serialize complete record cycles.
- **Rollback:** Revert the metadata gate, state extensions, Drive request changes, tests and related docs. Preserve existing DB/resources; records already marked permanent require deliberate operator reset before retry.

## 2026-09-30 — Fast Drive File ID dirty-check (Task 3.1)

- **Context:** The external pipeline needs a zero-work gate for unchanged sources, while unfinished cycles must still reconcile even when their Drive ID matches.
- **Change:** Added read-only `StateMachine.check_source()` for cover/PDF IDs. Matching ID plus confirmed `ok` returns `noop`; new/changed IDs require SHA checks; matching unfinished IDs return `resume`; empty sources are skipped without deletion. Existing Drive URL parsing is reused. Updated architecture, AI context and plan with actual validation and invocation order.
- **Verification:** `.venv/bin/python -m pytest -q tests/test_cover_dirty_check.py tests/test_state_machine.py tests/test_cover_state_schema.py` — 38 passed. A temporary-DB batch of 100 records produced 200 cover/PDF NO-OPs, zero mock Drive/downstream calls and identical persisted state. Tests also cover changed/new IDs, unfinished states, exhausted retry guards, empty sources, missing stored IDs, argument validation and supported URL formats. `git diff --check` passed.
- **Risks:** Call the gate before `mark_pending` and Drive client construction. Retry callers must still enforce cutoff/backoff, and each source is checked independently. Metadata/SHA handling is Task 3.2; this gate is not yet wired into the legacy API/downloader. No live service or DB was changed.
- **Rollback:** Revert `check_source`, its tests and related documentation; preserve the unchanged state schema and DB.

## 2026-09-30 — Durable cover state transitions and retry cutoff (Task 2.2)

- **Context:** Phase 2 requires reusable cycle state and bounded retries before adding external Drive/Koha/DSpace processing.
- **Change:** Added `src/cover_state/state_machine.py` with required DB/retry configuration, pending/result transitions, unfinished-record selection, exponential backoff and explicit retry reset. Success clears retries; failures increment atomically; partial failures may remain pending until cutoff forces failed. Transitions preserve resource columns. Updated architecture, environment contract, AI context and plan; Phase 2 acceptance is complete with the previously supplied runtime schema evidence.
- **Verification:** `.venv/bin/python -m pytest -q tests/test_state_machine.py tests/test_cover_state_schema.py tests/test_export_schema.py tests/test_export_repository.py tests/test_export_cli.py` — 39 passed on temporary DBs, including cutoff, persistence, direct SQL/method reset, backoff boundaries, concurrent increments and export regressions. `git diff --check` passed. No live DB or service was changed.
- **Risks:** Retry selection does not reserve work; future pipeline callers must serialize complete record cycles with a single writer or per-record locking. Backoff uses UTC `updated_at` with 1, 2, 4, ... second delays. External pipeline wiring remains later-phase work.
- **Rollback:** Revert the module/tests and related documentation. Preserve the unchanged version-1 state schema, DB and journals.

## 2026-09-30 — Confirm deployed cover-state schema and WAL (Task 2.1)

- **Context:** The user redeployed the API startup migration and supplied output from the requested read-only container checks; direct agent access to Docker was denied.
- **Change:** Updated architecture, AI context and implementation plan with runtime acceptance for Task 2.1. Task 2.2 remains pending.
- **Verification:** User-provided output confirms all three KDV services at `1/1`, a persistent read-write state bind, existing `/data/kdv_cover_state/state.db`, `journal_mode=wal`, `user_version=1`, all 11 columns, expected defaults/CHECK constraints, a unique UID primary-key index and `idx_records_status`. No runtime changes were made by the agent.
- **Risks:** The environment was not identified as dev/prod. Node-local placement and WAL-aware backup requirements still apply; schema acceptance does not verify the future processing state machine.
- **Rollback:** Revert only this documentation update; preserve the state DB and journal files.

## 2026-09-30 — Apply cover state migration during API startup

- **Context:** Post-redeploy inspection confirmed the persistent state mount but found no `state.db`; a separate manual migration would leave future fresh deployments dependent on an operator step.
- **Change:** The API container entrypoint now applies the idempotent cover-state migration after loading runtime secrets and before starting the server. Migration errors fail startup, which the orchestrator's existing API health gate detects. The orchestration host does not run SQLite against a potentially different node-local bind path. Updated deployment and migration documentation.
- **Verification:** `bash -n scripts/entrypoint.sh scripts/deploy-orchestrator-swarm.sh`, Compose rendering for local and Swarm manifests, and `git diff --check` passed. No live stack or database was changed; the next user-run image redeployment will create the DB and is required for runtime schema/WAL confirmation.
- **Risks:** Every API start runs the lightweight migration; SQLite WAL and version checks must succeed on the mounted storage. A migration failure keeps the API task from reaching the desired replica count, so orchestration verification fails.
- **Rollback:** Revert the entrypoint/Dockerfile migration wiring and documentation. Preserve any created state DB and journal files.

## 2026-09-30 — Verify API cover-state mount after redeployment

- **Context:** The user redeployed the stack and authorized Docker API access for verification.
- **Change:** Recorded live read-only evidence for the cover-state mount and schema delivery in the architecture, AI context and implementation plan.
- **Verification:** All three KDV services were at `1/1`; the active API task was on `pinokew`. Container inspection confirmed a read-write bind at `/data/kdv_cover_state`. The directory existed with mode `0700`, and hashes of both deployed schema/runner files matched the repository. Internal API health/readiness, optimizer readiness and CDN health each returned HTTP 200. The configured state DB file did not exist, so its schema/WAL migration is still pending. No live data, service or permissions were changed; the runtime configuration did not identify dev/prod.
- **Risks:** Health endpoints do not prove end-to-end Koha/Drive/DSpace processing. Task 2.1 runtime acceptance requires the explicit migration and subsequent schema/WAL checks. Node-local placement constraints still apply.
- **Rollback:** Remove this documentation evidence only; runtime state is unaffected.

## 2026-09-30 — Integrator SQLite state schema (Task 2.1)

- **Context:** Phase 2 needs durable integration state. The user requested reuse of the export SQLite migration code with a separate `COVER_STATE_DB_PATH` file.
- **Change:** Added `src/cover_state/schema.py` with all 11 `records` columns, `ok/pending/failed` constraints, non-negative retries, timestamp default, unique record UID and status index. Extended the existing export `MigrationManager` with selectable schema and optional required WAL, transactional DDL, schema version 1, newer-version rejection and explicit connection closure. Added a migration CLI using the configured cover path; export keeps its separate schema/file and existing journal mode. Added a read-write bind mount of `COVER_STATE_HOST_PATH` at `/data/kdv_cover_state` to the API in both Compose files. Updated the architecture, environment contract, AI context and plan.
- **Verification:** `.venv/bin/python -m pytest -q tests/test_cover_state_schema.py tests/test_export_schema.py tests/test_export_repository.py tests/test_export_cli.py` — 23 passed, including repeated migration, persistence, export preservation, constraints and transaction rollback. CLI checks using temporary storage confirmed WAL, version 1, 11 columns, unique UID/status indexes and rejection of missing configuration. Compose rendering verified the API mount. `git diff --check` passed. No live DB or service was changed.
- **Risks:** The Compose mount has not been deployed or observed on a running task. Since the host path is node-local and the API can schedule on manager nodes, ensure all eligible nodes provide the same durable directory or constrain API placement before deployment. SQLite backups must account for WAL. State-machine/retry behavior belongs to Task 2.2.
- **Rollback:** Revert the schema/runner changes while preserving state files; no table or DB deletion is included.

## 2026-09-30 — Move Integrator-managed MARC values to field 957

- **Context:** Koha `MARCOverlayRules` protects a whole MARC field, so protecting `956` would also block ordinary updates to its source and status subfields.
- **Change:** The Integrator now reads/writes its DSpace Item UUID in `957$3` and cover value in `957$c`; sources and status remain in `956`. Updated architecture, implementation plan, AI context, environment contract and OPAC mapping. Existing `956$c`/`956$3` values require a separately verified migration before enabling the overlay rule for `957`.
- **Verification:** Not run. Existing focused contract expectations were updated; runtime and Koha overlay behavior remain unverified.
- **Risks:** Legacy records may still have Integrator values in `956`; preserve compatibility when reading `956$3` until migration is complete. Do not enable whole-field `957` protection until existing values are migrated.
- **Rollback:** Revert the `957` writer/reader mapping and related documentation. Do not bulk-move MARC values as part of rollback.

## 2026-09-29 — Verify CDN routing after tmpfs redeployment

- **Context:** The user reported a running nginx task but a public root-path 404 and requested inspection of the CDN and `/opt/Traefik/docker-compose.yml`.
- **Change:** Recorded post-redeploy evidence in the CDN runbook and implementation plan. No runtime configuration was changed: the existing Traefik Swarm provider/web entrypoint and CDN Host rule route correctly, while nginx intentionally returns 404 at `/`.
- **Verification:** Live service inspection confirmed a running CDN task, healthy container, writable `/tmp` tmpfs mount limited to 16 MiB and read-only assets. Public HTTPS `/healthz` returned `200 ok` with normal TLS certificate validation; the internal Traefik request with the configured CDN Host header returned the same. Public and internal `/` returned nginx's expected 404. Public HTTP `/healthz` returned 200 without an HTTPS redirect.
- **Risks:** Task 0.2 acceptance still requires a hostname-scoped Cloudflare HTTPS redirect and external origin-isolation evidence. No Traefik or Cloudflare production settings were changed.
- **Rollback:** Remove only these documentation evidence updates; runtime state is unaffected.

## 2026-09-29 — Preserve CDN writable tmpfs in Swarm deployment

- **Context:** The first CDN deployment stayed at `0/1`. Live nginx logs reported `mkdir() "/tmp/client_body" failed (30: Read-only file system)`; the actual service spec had only the read-only assets bind and no `/tmp` mount. The original short `tmpfs` declaration was not delivered to Swarm. Previous isolated `docker run --tmpfs` checks did not verify service mount conversion.
- **Change:** Replaced the service-level tmpfs declaration with an explicit `volumes` tmpfs mount at `/tmp`, limited to 16 MiB. Extracted the existing manifest normalization pipeline into `normalize_swarm_manifest()` and added conversion of quoted numeric tmpfs sizes to integers required by the Stack schema. Regression checks now require the mount in both Compose output and the converted `docker stack config` result. Updated the contract, plan and CDN runbook with the failure evidence and post-redeploy checks.
- **Verification:** Live read-only service logs/spec inspection established the cause. `.venv/bin/python -m pytest -q tests/test_covers_cdn.py tests/test_init_volume.py` — 31 passed. Bash syntax, ShellCheck and `git diff --check` passed. An isolated non-root container with a read-only root filesystem, dropped capabilities and `--mount type=tmpfs` passed nginx syntax/startup, healthcheck and GET `/healthz`; `/tmp` was writable, 16 MiB, with `nosuid,nodev,noexec` observed in `/proc/mounts`. No live service was changed.
- **Risks:** Live recovery remains unverified until the user redeploys and confirms a `/tmp` tmpfs mount and running task. Public CDN acceptance remains pending. nginx root filesystem and assets remain read-only.
- **Rollback:** Revert the mount/conversion/test changes; this restores the known failed Swarm mount delivery and is unsuitable for service recovery.

## 2026-09-29 — External cover CDN skeleton (Task 0.2)

- **Context:** External covers require a static read-only origin behind the existing Traefik/Cloudflare Tunnel path. The user confirmed folder-only access for the existing Google service account and requested preparation only, with deployment performed by the user.
- **Change:** Added `covers-cdn` using the official nginx Alpine image as a non-root user with a read-only filesystem, dropped capabilities, private port 8080 and read-only assets mount. nginx exposes `/healthz` and SHA-256-named WebP files, applies immutable caching to successful asset responses, blocks writes and rejects other/symlink paths. The orchestrator validates the full HTTPS origin, derives the Traefik hostname, pins the CDN to the local storage node, versions its Docker Config by SHA prefix and verifies CDN replicas. Added the image placeholder, configuration tests and CDN runbook; updated environment/architecture/context/plan documentation with user-confirmed Task 0.1 evidence and pending external Task 0.2 acceptance.
- **Verification:** `.venv/bin/python -m pytest -q tests/test_covers_cdn.py tests/test_init_volume.py` — 30 passed. Compose rendering and `docker stack config` validation passed after the orchestrator's existing CPU-string conversion. Bash syntax, ShellCheck and `git diff --check` passed. `nginx -t` and isolated HTTP smoke passed with the official image digest recorded in the runbook: GET/HEAD health, healthcheck, exact asset bytes, WebP/cache headers, method rejection, hidden/symlink rejection and read-only storage. The container had no external network or host ports; no stack or Cloudflare settings were changed.
- **Risks:** Public hostname/DNS routing, Cloudflare TLS/HTTPS redirect, public read access and origin isolation require setup/verification after the user's deployment. Task 0.2 and Phase 0 remain open until that evidence exists. The CDN is node-local; its pinned storage node must stay schedulable. The example image tag is mutable; deployment can pin a tested tag/digest.
- **Rollback:** Revert the CDN service/config and orchestrator wiring and use the existing deployment procedure. Preserve assets/state; any Cloudflare hostname removal is a separate authorized change. Koha fields and the current legacy cover writer are unchanged.

## 2026-09-29 — Initialize cover host storage before Swarm deployment

- **Context:** Future cover and SQLite state bind mounts need their host directories prepared consistently before deployment.
- **Change:** Added `scripts/init-volume.sh` and called it from the orchestrator's existing deploy-adjacent phase before secret rendering and image builds. Both host paths are required and read from environment overrides or the selected dotenv file without sourcing it. The script creates or repairs cover root/assets modes to `0755` and incoming/state modes to `0700`, preserves existing ownership and files, and rejects missing, relative, top-level, overlapping, non-directory and symlink paths. Updated the environment template, contract, AI context and script runbook.
- **Verification:** `.venv/bin/python -m pytest -q tests/test_init_volume.py` — 15 passed, covering repeated initialization, mode repair, file/ownership preservation, path guards and orchestrator dotenv/override behavior. `bash -n scripts/init-volume.sh scripts/deploy-orchestrator-swarm.sh .env.example`, ShellCheck for both scripts and `git diff --check` passed. Filesystem tests used temporary directories; no live storage or Swarm deployment was changed.
- **Risks:** The deployment account needs creation/chmod permissions; initialization fails without them. Existing encrypted environments must define both host paths before their next Swarm deployment. Directory permissions support the current root Integrator; switching to a non-root writer requires an ownership migration. Host storage is node-local; prepare all eligible nodes or constrain service placement.
- **Rollback:** Remove the init script call and associated script/tests/documentation. Created directories and their contents are preserved; revert directory modes manually only after assessing running services.

## 2026-09-29 — External Cover Integrator environment contract (Task 0.1)

- **Context:** The external cover implementation plan needed a repository-specific environment and secret delivery contract before adding CDN/storage/state components. Conceptual Koha variable names differed from existing runtime settings, and CDN examples treated a base URL as a bare hostname.
- **Change:** Added six placeholder cover settings to `.env.example` for the full HTTPS CDN base URL, host/container cover storage, host/container state storage and record retry limit. Added `docs/external-cover-integrator/environment.md` documenting 29 new/reused parameters, existing SOPS/Vault-to-Docker-Secret delivery, component access boundaries and the legacy URL-to-SHA transition in `956$c`. Updated the concept, AI context and implementation plan with consistent URL examples, executable template validation and the outstanding service-account acceptance criterion.
- **Verification:** `bash -n .env.example` passed. Standard-library checks confirmed all 29 documented parameters exist, template keys are unique, new path placeholders are absolute, the CDN URL is HTTPS and the retry limit is positive. The validation embedded in Task 0.1 ran successfully; local contract links resolved; `src/config.py` inspection confirmed required Koha/DSpace endpoint settings without hostname defaults. `git diff --check` passed.
- **Risks:** These settings are configuration preparation; later phases must implement consumers, mounts and runtime validation. Service-account existence and folder-only access are not verified in an environment, so Task 0.1 is not marked fully accepted. Encrypted env files and deployed services were not changed.
- **Rollback:** Remove the cover placeholder block and environment contract, and revert the related concept/context/plan documentation changes. No runtime or infrastructure rollback is required.

## 2026-09-24 — Detect optimizer volume ownership drift

- **Context:** Two 109.47 MiB PDF jobs with 200 and 100 DPI reached Ghostscript but returned `missing_output`. Live diagnostics showed optimizer UID/GID `10001:10001`, while the existing shared volume and its `input`/`output` directories were `1000:1000` with mode `755`; `/ready` returned 503 while `/health` returned 200. The pre-existing volume masked the image-layer ownership set in the Dockerfile.
- **Change:** The optimizer container healthcheck now calls `/ready`. Before stack deployment, `scripts/deploy-orchestrator-swarm.sh` idempotently sets owner `10001:10001` on only the shared volume root, `input`, and `output` directories through a running task's mounted volume. It fails closed if an existing service has no local running task. Updated optimizer and script runbooks with automated and manual recovery procedures.
- **Verification:** Live read-only checks confirmed optimizer UID/GID `10001:10001`, API UID `0:0`, all three directories at `1000:1000` mode `755`, both write probes failing, `/ready` HTTP 503, `/health` HTTP 200, and both Swarm tasks on node `pinokew`. `bash -n scripts/deploy-orchestrator-swarm.sh`, `docker compose config -q`, and `git diff --check` passed. The deploy flow and volume were not changed on the live stack.
- **Risks:** The automated repair requires the deployment host to have a local running task mounting the volume. A different-node task fails the deployment before stack update so the wrong node-local volume is never changed.
- **Rollback:** Restore the `/health` healthcheck and remove the ownership repair function and runbook updates. The current volume still requires the documented manual ownership repair if this change is reverted.

## 2026-09-24 — Optional PDF rasterization DPI

- **Context:** Koha archival previously used the fixed Ghostscript `/ebook` optimization profile and offered no output-resolution choice.
- **Change:** Added optional allowlisted DPI selection for single-record and Robot Batch archiving. Selected values use full-page RGB `pdfimage24` rasterization at JPEG quality 85 and the PDF CropBox; omitted DPI preserves the existing searchable `pdfwrite /ebook` path. Invalid DPI is rejected before task creation, raster results must preserve page count and stay within the original file size, and failures fall back to the original. Task telemetry now reports requested and confirmed applied DPI. Updated the optimizer architecture, PRD, context, and runbook.
- **Verification:** `PYTHONPATH=.:kdv-optimizer .venv/bin/pytest -q tests/test_app.py tests/test_robot.py tests/test_core.py tests/test_pdf_optimizer_client.py tests/test_services.py` — 107 passed. Manual two-page Ghostscript smoke at 150 DPI produced two 600×800 RGB JPEG page images at 150 PPI (`pdfinfo`, `pdfimages -list`); default mode retained both searchable text strings (`pdftotext`). `node --check IntranetUser.js`, Python compilation, and `git diff --check` passed. Ruff could not run because the installed Snap launcher cannot write its required runtime directories.
- **Risks:** Explicit rasterization removes the text layer and does not run OCR; high DPI can time out and fall back to the original. Deploy the optimizer before the API and Koha UI.
- **Rollback:** Remove the DPI fields and UI controls, API/client propagation, raster Ghostscript path and associated telemetry/tests/docs; the default no-DPI path remains compatible with the previous behavior.

## 2026-09-22 — Cover generation respects PDF CropBox

- **Context:** PDF readers show the visible `CropBox`, but `pdf2image` used the default full `MediaBox`; for scanned PDFs with a larger source canvas this generated a cover outside the page the reader displays.
- **Change:** `CoverService._generate_image()` now passes `use_cropbox=True` to Poppler through `convert_from_path()`, so the generated cover matches the visible first PDF page.
- **Verification:** Focused mocked regression test asserts that the Poppler call enables `use_cropbox`; manual reproduction with the affected PDF and `pdftoppm -cropbox` matched the reader-visible page.
- **Risks:** PDFs whose CropBox intentionally excludes content will now produce the cropped, reader-visible area; this is the required rendering contract.
- **Rollback:** Remove `use_cropbox=True`, the focused regression test, and this changelog entry.
