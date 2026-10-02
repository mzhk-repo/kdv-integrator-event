Архітектура та Workflow (v0.4.0-M8 + Swarm Robot wrapper + Google Drive source + Koha Export Module)

Цей документ описує логіку роботи KDV Integrator v0.4.0 з урахуванням M2 hardening (розділення сервісів, DI, тестованість), M3 (CI/CD pipeline, security gates, release/deploy flow), M4 (Zero Trust + CORS), M5 (ops readiness/runbooks), M6 (contract tests), M7 (release canary + rollback + batch controls), M8 (PDF optimizer + Swarm runtime wrapper для Robot + read-only Google Drive source) та Koha Export Module (CLI/batch-підсистема для побудови XLSX-звітів Koha → Google Drive → MS Graph email, staged-idempotency).

🔄 Загальний Workflow (Fork-Join Pattern)

Процес обробки однієї книги розділений на Послідовну фазу (підготовка) та Паралельну фазу (виконання).

graph TD
    User((Koha UI)) -->|POST /integrate| API[Integrator API]
    API -->|Return task_id| User
    API -->|Start Thread| Core[Async Core Logic]
    
    subgraph "Serial Phase (Blocking)"
        Core -->|Resolve 956$u| SourceResolver{Local path or Google Drive URL?}
        SourceResolver -->|Local| FileCheck{File Exists?}
        SourceResolver -->|Google Drive| GDriveDownload[Read-only download to GDRIVE_TMP_DIR]
        FileCheck -->|No| Error[Exit & Log Error]
        FileCheck -->|Yes| Rename[Rename & Move to /Processed]
        GDriveDownload --> Fork
    end

    subgraph "Parallel Phase (ThreadPoolExecutor)"
        Rename --> Fork((Fork))
        
        Fork -->|Thread A| CoverService[Cover Service]
        CoverService -->|1. Generate JPG| PDF2IMG[pdf2image]
        PDF2IMG -->|2. Upload (CGI)| KohaCGI[Koha Staff (HTML)]
        KohaCGI -->|3. Scrape ID| Scraper[HTML Parser]
        
        Fork -->|Thread B| DSpaceWorkflow[DSpace Workflow]
        DSpaceWorkflow -->|1. Parse MARC| Parser[MARCXML Parser]
        Parser -->|2. Check Duplicates| DSpaceREST[DSpace REST API]
        DSpaceREST -->|3. Create Item & Upload PDF| DSpaceREST
    end

    subgraph "Finalize Phase (Join)"
        Scraper --> Join((Join))
        DSpaceREST --> Join
        Join -->|Update 956 field| KohaREST[Koha REST API]
        KohaREST -->|Write: Handle URL + Cover URL| DB[(Koha DB)]
    end


### External cover CDN skeleton (Task 0.2)

`covers-cdn` joins the existing Swarm proxy network: Cloudflare Tunnel -> Traefik
`web` entrypoint -> non-root nginx on internal port 8080. It publishes no host
ports and receives only the read-only `assets/` directory and a versioned Docker
Config. The orchestrator derives the router hostname from `COVERS_CDN_BASE_URL`
and pins the service to the node where host storage was initialized. Public TLS,
hostname routing and HTTPS redirects belong to the existing Cloudflare setup.
The static service exposes `/healthz` and content-addressed WebP files; migration
and Integrator writer/state changes remain later phases. Deployment and external
acceptance are documented in [the CDN runbook](external-cover-integrator/runbook.md).

### External cover state schema (Task 2.1)

`src/cover_state/schema.py` defines the separate `records` SQLite table at
`COVER_STATE_DB_PATH`. It reuses the export module's `MigrationManager`, with
transactional DDL, schema version 1, required WAL, a unique `record_uid` primary
key and a `status` index. `EXPORT_DB_PATH` and `exported_records` remain separate.
Both Compose files mount `COVER_STATE_HOST_PATH` read-write in `kdv-api` at
`/data/kdv_cover_state`. `scripts/entrypoint.sh` runs
`python -m src.cover_state.schema` after loading runtime secrets and before
starting the API; a migration failure stops API startup and therefore blocks a
successful deployment. The CLI remains available for explicit maintenance with
`COVER_STATE_DB_PATH` or `--db-path`.
Repository schema verification uses temporary databases. Read-only post-redeploy
checks on 2026-09-30 confirmed the API's read-write state mount and deployed schema
code; at that time the live state DB did not exist. After the next deployment,
user-run read-only container checks on 2026-09-30 confirmed the persistent
read-write state bind, `/data/kdv_cover_state/state.db`, WAL, schema version 1,
all 11 columns and both required indexes. Task 2.1 runtime acceptance is complete;
the environment was not identified as dev/prod.
Because the host path is node-local, API
placement must use the node with that path or shared storage with the same durable
data on each eligible node.

### External cover state machine (Task 2.2)

`src/cover_state/state_machine.py` provides `StateMachine` for the separate WAL DB.
It requires `COVER_STATE_DB_PATH` and a positive integer `MAX_RETRY_COUNT` from
the environment, or explicit constructor arguments; it does not import external
API configuration. `mark_pending()` starts/resumes a cycle without clearing
retries or resource columns and rejects exhausted records. `record_result()`
marks confirmed success `ok` with zero retries, increments failures atomically,
and can preserve `pending` for partial work until cutoff forces `failed`.
`get_retry_eligible()` returns only unfinished records below the limit, after
exponential backoff of 1, 2, 4, ... seconds from UTC `updated_at`. Zero retries
bypass backoff, including after a deliberate reset. `reset_retry_count()` retains
status and resource columns; the documented direct SQL reset works too.
Connections close after each operation, and SQLite transactions serialize writes.
Selection does not claim work. The API/Robot core holds a shared filesystem
workflow lock across complete configured cycles; independent callers must also
serialize processing. The state module is tested independently and through the API.

### External cover fast dirty-check (Task 3.1)

`StateMachine.check_source(record_uid, incoming_file_id, source="cover"|"file")`
reads the corresponding `cover_source_id` or `file_source_id` without state writes
or external calls. Call it with a parsed Drive ID before `mark_pending()` and
before constructing/calling Drive clients. The existing `GoogleDriveUrlParser`
in `src/services/sources.py` already parses supported Drive URLs without network
access. An unchanged ID with confirmed `status=ok` returns `noop`; a new/changed
ID returns `needs_sha_check`. An unchanged ID with unfinished status returns
`resume`, preserving reconciliation rather than silently skipping partial work.
An absent source returns `no_source` and never deletes stored resources.
Retry callers still enforce cutoff/backoff through state eligibility; `resume`
does not authorize an exhausted attempt. Cover and PDF are checked separately;
a source-level `noop` does not skip work required by another source. Task 3.2
consumes these decisions for metadata/SHA checks. The shared API/Robot core now
invokes it before Drive materialization.

### External cover Drive metadata/SHA gate (Task 3.2)

`src/cover_state/drive.py:check_drive_metadata()` calls the fast gate first and
constructs `GoogleDriveSource` lazily only when metadata is needed. Its public
`get_metadata()` reuses existing read-only service-account authentication and
requests `sha256Checksum` explicitly. Drive resource keys use the documented
`X-Goog-Drive-Resource-Keys` request header for metadata and download requests.
Confirmed same-content sources update only their corresponding source ID and
timestamp, preserving `ok` and downstream resources. Changed content returns
`resource_changed` with metadata/SHA and enters `pending`, without committing
unconfirmed source IDs/hashes. Matching hashes in unfinished cycles return
`resume`; matching IDs resume durable work without another metadata call.
The gate checks retry eligibility first; cutoff/backoff return `deferred`.
Missing, empty or malformed checksums raise `MissingChecksumError`, log a safe
reason and persist `failed` with the retry count at least at the configured
limit, requiring an explicit reset. This encodes permanent failures using the
existing version-1 schema. API/client failures raise `DriveMetadataError` and
increment retries once; API exception text is not logged. No downloads, asset
processing, DSpace writes or Koha write-back are performed by this gate.
Tests use temporary SQLite DBs, mocks and offline real SDK request construction;
user-run container smoke output on 2026-09-30 confirmed deployed code hashes,
live binary metadata/SHA retrieval, same-content source ID update and a subsequent
NO-OP with zero additional Drive calls. The same smoke used mocks for missing
checksums in Google Doc/shortcut responses and a network timeout; permanent
cutoff and a single retry increment passed. The temporary DB was removed.
This verifies the deployed gate directly, not automatic invocation by the API
workflow at that time. API/Robot core wiring is now implemented and tested
locally. User-provided post-redeploy smoke output confirmed deployment of that
wiring on 2026-09-30; dev/prod was not identified.

### Automatic API/Robot Drive gate

Task 4.2 adds `publish_cover()` and the cover CLI's `--publish` option. The
publisher requires prepared non-symlink storage with `.incoming` and `assets`
on one filesystem, validates WebP bytes/SHA and serializes publication with a
filesystem lock. A unique staging file is synced and moved with `os.replace()`
to `assets/<sha256>.webp`, mode 0644; both directories are synced before success.
Existing identical assets retain their inode/mtime; corruption and symlink
destinations fail closed. Ordinary failures remove staging files; SIGKILL can
leave only private staging leftovers, removed under lock on the next publish.
Both Compose files give API the full cover root read-write at
`COVERS_STORAGE_PATH`; nginx keeps only read-only assets. Swarm API placement
also requires the orchestrator's storage node, preserving its manager-zone
constraint. These are deployment configuration changes; no deployment occurred.
Task 4.3 now implements Koha adoption and durable record-level publish reconciliation
in the repository; deployed Koha acceptance remains pending.

Post-redeploy checks on 2026-09-30 confirmed all three services at 1/1, API/CDN
on `pinokew`, matching deployed publisher SHA, the expected read-write API root
and read-only CDN assets mounts, directory modes 0755/0755/0700 and one device.
The active Gunicorn environment contains `COVERS_STORAGE_PATH`; a separate
`docker exec` process does not inherit environment sourced by the entrypoint.
The corrected diagnostic reads only selected non-secret variables from PID 1.
The deployed publisher passed normalization, publish/dedup/hash/mode and
inode/mtime checks in isolated `/tmp` storage; cleanup succeeded. Internal CDN
health and public HTTPS via curl/requests returned 200; urllib received 403.
A synthetic WebP was then published to mounted assets. Repeated publication
preserved inode/mtime; internal and public CDN responses returned the same bytes,
`image/webp`, and one-year immutable cache headers. This confirms runtime
publication/CDN delivery for that test asset, not a Koha record workflow. The
test asset remains in storage and may remain cached for one year. Dev/prod was
unidentified.

Task 4.1 adds `src/services/cover_pipeline.py`: `download_and_normalize()` reuses
the Drive resolver/download and optional gate metadata, verifies source SHA before
decoding, applies EXIF orientation and RGB, strips metadata, downsizes to at most
600 px wide without upscale and encodes WebP at quality 82. It returns separate
source/asset hashes. The core reuses its download verifier. This callable/CLI
stage writes a temporary normalized output; Task 4.2 publishes it on request,
and Task 4.3 wires it into the explicit Drive cover workflow. Local image/stub-Drive checks do not
establish live Drive or Koha acceptance.

When `COVER_STATE_DB_PATH` is configured, `process_integration_logic()` reads
MARC `001` through `KohaClient.get_biblio_metadata()` and requires a UUIDv7 for
Drive sources. A `.workflow.lock` in the state DB directory serializes complete
configured cycles across threads/processes sharing that filesystem. The core
checks record retry eligibility once before checking both sources, so the first
source's pending timestamp cannot accidentally defer the second source.
The existing authenticated API/task/polling contract and Robot caller use this
same core entry point. An all-confirmed unchanged Drive-only cycle returns
`status=noop`; if either DSpace link is missing from Koha, the
fast path reads the existing Item and ORIGINAL bitstream by their stored UUIDs
and rewrites the DSpace `856` pair: the PDF download URL and repository Handle
URL. It confirms both links by read-back. Exhausted or waiting retries return
`status=deferred` without
downstream work or another retry increment. Local paths and additional files
are not included in the identity gate and retain their processing path.
Drive `956$p` image sources are resolved/materialized with an image MIME allowlist.
Gate metadata is reused during materialization; downloaded bytes must match
source SHA before downstream processing. Unchanged explicit covers are skipped;
cover-only changes can update Koha without materializing an unchanged PDF or
calling DSpace. Task 4.3 uses the WebP/CDN pipeline for explicit Drive covers;
Task 5.1 uses it for Drive PDF first-page fallback when `956$p` is absent.
Local sources retain the legacy CGI path without Drive state identity tracking.
`complete_cycle()` atomically commits source IDs/SHA, returned DSpace UUIDs,
`ok` and zero retries only after confirmed required cover processing and a true
Koha write-back result. Downstream failures retain unconfirmed identities and
increment retries once. For changed PDFs on an existing DSpace Item, the
replacement flow retains the old bitstream until the new primary and both Koha
links are confirmed. A pending checkpoint lets retry reuse the uploaded
bitstream. Post-deployment DSpace/Koha runtime acceptance remains open.
Local API tests cover route-to-core invocation, NO-OP, same-content identity
changes, cover-only work, checksum mismatch, write-back failure, missing UID,
missing checksum, two-source retry and concurrent duplicate requests.
User-run post-redeploy smoke on 2026-09-30 confirmed matching code hashes,
HTTP 200 health/readiness on the active server, and WAL/version 1 in persistent
state. In a separate process inside the deployed container, the Flask test client
invoked the API route, actual background task and polling using temporary state
and stub Koha/DSpace clients: unchanged source returned NO-OP with zero Drive
calls; a seeded same-content identity change used live Drive SHA and updated
only temporary state without downstream work. Cleanup completed. This verifies
deployed route/core execution in isolation, not an integration POST to the running
Gunicorn server or live Koha/DSpace writes. Active external authentication modes,
Robot execution and changed-content write-back were not exercised by this smoke.

### External cover Koha write-back and recovery (Task 4.3)

Explicit Drive `956$p` cycles publish canonical WebP and write only its SHA to
`957$c` through the existing Koha MARC writer. Before write-back, the separate
cover DB durably checkpoints work in additive `pending_cover_work`: input
fingerprint, source IDs/SHA, asset SHA, whether PDF work is required and a completed
DSpace result. `records.cover_asset_sha256` is saved while pending; confirmed
source columns remain unchanged. The existing schema version 1 and record columns
are preserved. API startup's idempotent migration creates the additional table.

For matching inputs, an eligible retry validates the published asset and reuses
completed PDF work, repeating only Koha write-back. Input identity/collection/
additional input/DPI/options changes invalidate the checkpoint. Backoff, cutoff
and manual reset remain active. A true PUT is followed by MARC read-back of
`001`/`957$c` and, for a PDF cycle, `957$3` and required `856$u` links. Only then
are source IDs/SHA, UUIDs and `ok`/zero retries committed and the checkpoint deleted
in one SQLite transaction. Failed writes/read-back remain pending until cutoff.
Previously confirmed Drive covers without an asset SHA are rebuilt as WebP.

Local tests prove recovery after reopened SQLite without another cover download,
normalization or completed DSpace job; corrupt assets and incomplete read-back
fail closed. DSpace crashes before its completed result is checkpointed still need
Phase 7 reconciliation; a mere existing Item link does not prove PDF replacement.

If unchanged-source link repair receives an explicit DSpace Item HTTP 404, the
saved Item UUID is stale. The workflow forces a Drive metadata/SHA refresh,
searches DSpace again by `koha.uid`, and recreates the Item/PDF if no matching
Item exists. Item resolution uses only exact `koha.uid` matches; a missing match
creates a new Item. Only HTTP 404 triggers this recovery; network, authorization
and server errors remain retryable failures. Ambiguous UID matches fail closed.

On 2026-10-01, the user supplied a test-record run: Drive image download and task
completion, MARC `957$c` matching the mounted WebP SHA, state `ok` with zero retries
and no checkpoint, and visible cover in the Koha interface. This confirms the
positive deployed cover path. Live NO-OP and failure recovery were not exercised;
their coverage remains local tests. The environment was not identified.

### PDF first-page cover fallback (Task 5.1)

When `956$p` is absent and `956$u` is a Drive PDF, the shared API/Robot cycle
verifies downloaded bytes against the Drive SHA, renders page one with Poppler's
CropBox at 150 DPI and a 15-second timeout, then uses the same metadata-free
600-pixel/quality-82 WebP normalization and immutable publisher. `957$c` write-back,
MARC read-back, checkpoint reuse and source/asset commit follow Task 4.3. A
confirmed PDF without an asset SHA is rebuilt; unchanged confirmed PDFs skip
work. A PDF with no renderable first page causes a permanent failed state for
that record, while other tasks continue. Transient render errors retain the
normal retry policy. Changed PDFs for existing DSpace Items use the verified
replacement flow described below; local PDF paths retain the CGI path.
Repository tests cover real PDF rendering, corrupt and protected PDF behavior,
failure isolation, asset/retry reuse and NO-OP. On 2026-10-01 the user supplied
a successful test run after correcting the DSpace collection: Item creation and
PDF bitstream upload completed, Koha fields were correct and the cover displayed.
A separate record successfully restored a deleted `856$u` by linking its existing
Item. Changing `956$u` for that existing Item produced the intentional
`Changed Drive PDF requires DSpace bitstream replacement` guard; safe replacement
is Task 7.2. The runtime environment was not identified.

### DSpace Item identity (Task 7.1)

Drive-backed records carry canonical MARC `001` UUIDv7 to DSpace as
`koha.uid`. The shared workflow resolves Items only by that UID, so a retry
reuses the same Item and Handle. If an earlier attempt created the Item but
failed before its first ORIGINAL bitstream, a retry uploads the missing PDF to
that same Item. For a changed
Drive PDF, the workflow uploads and checksum/size verifies a new bitstream,
switches and reads back the ORIGINAL bundle primary bitstream, then checkpoints
the DSpace result before Koha write-back. Both Koha `856$u` links must pass
read-back before the old bitstream is deleted and state can complete. A failed
Koha write retains the old bitstream and checkpoint; retry reuses the new UUID
without uploading again. The DSpace metadata registry and discovery
index must contain `koha.uid`; this repository change does not configure the
DSpace server.

The bitstream UUID stored in state is only a cleanup candidate when its saved
Item UUID matches the UID-resolved target and the bitstream appears in that
Item's ORIGINAL bundle. A stale or absent bitstream identity is ignored; the
new PDF can still upload, while unverified bitstreams are never deleted.
User-provided runtime log on 2026-10-01 confirms the field contains MARC `001`,
the PDF uploaded to the Item, and the task completed successfully. This is
positive first-cycle evidence. The user also confirmed that a repeated search
finds the same Item and its Handle remains unchanged. Task 7.2 local failure and
retry checks pass; runtime replacement acceptance requires deployment and a
user-run smoke test.

⚡ Деталі Реалізації (M2-M7)

### 1. Асинхронність (Async Core) + DI

**Клієнт → app.py → task_manager → core.py (у окремому потоці)**

- **Request:** Клієнт викликає `/kdv/api/integrate/{biblionumber}`. Обробник у app.py викликає фабрику `_make_clients()`, яка створює KohaClientWrapper + DSpaceClientWrapper, і передає їх у `process_integration_logic()` через kwargs.
- **Response:** Миттєво повертається `task_id` (UUID) у статусі 202 Accepted.
- **Processing:** Задача додається в TASKS (In-Memory), запускається в окремому потоці з переданими клієнтами (DI).
- **Polling:** JS‑клієнт в Koha опитує `/kdv/api/status/{task_id}` кожні 2 сек. Отримує статус: queued → processing → success/error.

**Чому DI?** Завдяки цьому у тестах можна підмінити реальні клієнти на stub‑класи і не звертатись до мережі. Дивіться [docs/RUNBOOK_TESTING.md](RUNBOOK_TESTING.md).

### 2. Паралелізація (Concurrency) — Fork-Join у core.py

**src/core.py > process_integration_logic() використовує ThreadPoolExecutor(max_workers=2)**

- **Task A (best-effort):** CoverService.process_book() (генерація JPG). Якщо впада → WARNING, але весь процес не зупиняється.
- **Task B (critical):** run_dspace_workflow() (метадані, Item, PDF upload). Якщо впада → ERROR; local primary переходить в Error folder, Google Drive temp-файл не рухається з `GDRIVE_TMP_DIR`.

**Обидва потоки отримують ті самі залежності (koha_client, dspace_client) через DI.**

### 3. Розділення Сервісів (M2 — SRP)

**src/services/files.py — FileService**

Новий сервіс для файлових операцій:

- `version_and_move(original_path, biblionumber)`: rename-first з версіонуванням (v01, v02...). Створює Processed папку, переміщує файл аж туди.
- `move_to_error(active_path)`: переміщує файл в Error folder при критичній помилці.

**Чому окремо?** Раніше логіка була розкидана по core.py. Тепер це інтерфейс — легше тестувати, легко перенести на S3/cloud storage.

**src/services/covers.py — CoverService (оновлено)**

- Раніше інлайнова логіка у майже 300 рядків. Тепер: self-contained сервіс.
- `process_book(biblionumber, pdf_path, output_dir)`: повний pipeline (check existing → generate JPG → upload via CGI → verify URL).
- Retry policy: 3 спроби на читання PDF, 3 спроби на отримання URL обкладинки.
- Skip-mode: якщо обкладинка вже є в Koha (strict mode) або якщо pdf2image недоступна.
- **Залежність:** отримує `koha_client` як параметр у `__init__()`.

### 4. Thin-Wrapper'и й DI (M2 — для тестів і підміни)

**src/clients/koha.py (KohaClientWrapper)** та **src/clients/dspace.py (DSpaceClientWrapper)**

- Обгортають реальні клієнти (`src/koha.py`, `src/dspace.py`).
- Дозволяють ліниву імпортацію залежностей (якщо тест запускається без мережі, обгортка не кидає помилку).
- У продакшені: `KohaClientWrapper()` → робить реальний `KohaClient()`.
- У тестах: можна заміняти на `StubKoha`, `StubDSpace` без будь-яких змін у core логіці.

**src/app.py — фабрика `_make_clients()`**

```python
def _make_clients():
    """Return a fresh pair of Koha/DSpace clients (wrappers) for glue code.
    In tests we can monkeypatch this function to return stubs.
    """
    return KohaClientWrapper(), DSpaceClientWrapper()
```

- Викликається у кожному HTTP обробнику (`POST /integrate`, `PUT /integrate`). `PUT` запускає примусове оновлення Drive PDF bitstream разом із метаданими та працює через task polling.
- У тестах можна monkeypatch для підміни моків.

### 5. TaskManager з DI (M2 — kwargs support)

**src/tasks.py > TaskManager.start_task(func, *args, **kwargs)**

Раніше: `start_task(func, *args)` прокидав тільки позиційні аргументи.  
**Тепер:** підтримує `kwargs`, які передаються у функцію як ключові аргументи.

**Приклад:**
```python
task_manager.start_task(
    process_integration_logic,
    biblionumber,              # позиційний
    koha_client=koha_cli,      # DI ←
    dspace_client=dspace_cli   # DI ←
)
```

**Як це працює:**
1. `start_task()` створює task_id, зберігає статус у TASKS.
2. Запускає `_wrapper()` у новому потоці.
3. `_wrapper()` викликає `func(task_id, *args, **kwargs)`.
4. Статус: queued → processing → success/error.

### 6. Data Warehouse (Збагачення MARC)

Інтегратор зберігає керовані ним значення окремо від джерел у `956`:


956$y — Статус (imported, error).

956$z — Лог помилки або попередження.

957$c — Значення обкладинки, яке формує Integrator (URL legacy pipeline; hash у зовнішньому cover pipeline).

957$3 — UUID Item у DSpace. Поле `957` відокремлює керовані Integrator-ом підполя від `956`, оскільки `MARCOverlayRules` застосовуються до цілого поля.

956$p — Відносний шлях до готової обкладинки; якщо заданий, cover workflow завантажує цей файл і не генерує JPG з PDF.

956$q — Змішаний список additional джерел через `|`: локальні відносні шляхи або Google Drive URL. Вони завантажуються в ORIGINAL без rename і без `kdv-optimizer`; помилки additional лишаються non-fatal через `additional_files_failed`.

856 #1 `$u` — Пряме посилання на primary bitstream download, `$y` = `Файл`.

856 #2 `$u` — Handle-посилання на репозиторій, `$y` = `Запис в репозиторії`.

### 7. Протокол "Hybrid CGI" (Cover Upload)

REST API Koha не дозволяє повноцінно працювати з локальними обкладинками. Емулюємо дії людини:

- **Auth:** Логін через POST-форму на mainpage.pl (у CoverService._ensure_cgi_login).
- **AJAX Spoofing:** Завантаження файлу на upload-file.pl з заголовком `X-Requested-With: XMLHttpRequest` (інакше Koha не віддасть JSON).
- **Scraping:** Парсинг HTML сторінки інструментів для знаходження `imagenumber`, щоб сформувати публічне посилання.
- **External cover path:** якщо в `956$p` є відносний шлях до зображення, `CoverService` завантажує цей файл напряму; наявність PDF книги не є передумовою для цієї спроби upload.

### 8. SourceResolver і Google Drive source

`src/services/sources.py` ізолює джерела файлів від `core.py`:

- `LocalMountSource` приймає тільки відносні шляхи всередині `INTEGRATOR_MOUNT_PATH`; absolute path і `..` відхиляються.
- `GoogleDriveUrlParser` підтримує `drive.google.com/file/d/<id>/view`, `open?id=<id>`, `uc?id=<id>` і `resourcekey`; folder links і сторонні HTTP/HTTPS URL відхиляються.
- `GoogleDriveSource` працює read-only: читає service account тільки з `GDRIVE_SERVICE_ACCOUNT_FILE`, перевіряє metadata, скачує PDF у `GDRIVE_TMP_DIR` через `.part` і atomic rename, не виконує write/update/delete у Google Drive.
- Deterministic cache path базується на `file_id`, `resourcekey`, `name`, `mimeType`, `size`; якщо завершений `.pdf` валідний, повторний download не виконується.
- Cleanup видаляє тільки старі regular files `.pdf`/`.part` всередині `GDRIVE_TMP_DIR`; інші директорії та suffix-и не чіпає.

Lifecycle:

- local primary `956$u`: `version_and_move()` у `Processed`, при критичній помилці `move_to_error()` у `Error`;
- Google Drive primary `956$u`: temp PDF лишається у `GDRIVE_TMP_DIR`, не переміщується в `Processed/Error`;
- local/GDrive additional `956$q`: без rename, без optimizer, upload у DSpace ORIGINAL; Google additional errors non-fatal.

PDF optimizer зберігає стандартний `pdfwrite /ebook` режим, якщо клієнт не вказав `dpi`. Koha UI та Robot Batch можуть передати одне з `100, 150, 200, 250, 300, 400, 600`; optimizer тоді створює повносторінковий RGB PDF через `pdfimage24`, обмежує output розміром оригіналу й перевіряє незмінність кількості сторінок. Растеризований PDF не містить текстового шару. Фактичний DPI фіксується в `task.result`; за помилки, завеликого output або невідповідного результату DSpace отримує оригінал.

Observability:

- logs містять `source_type=gdrive`, safe `file_id`, `mime_type`, `size`, `duration_ms`, cache hit/miss або failure reason;
- logs не містять service account JSON, OAuth token, повний Google Drive URL або `resourcekey`.

🛡 Безпека та Відмовостійкість (M2/M3)

**Retry Policy**
- 3 спроби на читання PDF (з затримкою 1s між ними).
- 3 спроби на отримання URL обкладинки (також 1s).
- Timeout: 15s на генерацію JPG (poppler guard).

**Rename-First (Файлова гігієна)**
- Файл спочатку перейменовується з версією (v01, v02...).
- Гарантує унікальність і стабільність шляху.
- Реалізовано у FileService.version_and_move().

**Fail Fast + Error Folder**
- При критичній помилці (DSpace падає, MARC невірний):
  1. Файл переміщується в Error folder.
  2. Запис у Koha отримує статус `error` + лог помилки.
  3. Задача отримує статус ERROR, розробник може ручно розібратись.

**DI для Мок-тестування (M2)**
- Все залежності (Koha, DSpace) підміняються у тестах на stub‑класи.
- Дивіться [docs/RUNBOOK_TESTING.md](RUNBOOK_TESTING.md) для прикладів.
- Непотрібно мережевих викликів, тести швидкі та ізольовані.

### 9. CI/CD Архітектура (M3)

Після M3 у проєкті діє workflow `.github/workflows/main.yml`, який викликає reusable pipeline `shared-ci-cd.yml` з двома шарами: `ci-checks` і `cd-deploy`.

**CI layer (`ci-checks`)**
- Trigger: `pull_request`, `push` у `dev/main`, теги `v*.*.*`, `release`.
- Gates: `ruff check .`, `pytest -q`, `pip-audit`, `trivy config`, `trivy image`.
- Build/Publish: збірка і push у GHCR (`ghcr.io/<owner>/kdv-integrator-event`) з тегами `dev/main`, `v*.*.*`, `sha-*`, `latest` (для релізних тегів).
- Compose validation: `docker compose config` через `.env.example` (без secrets у репозиторії).
- Security policy: `trivy image` виконується з `--ignore-unfixed`, тому блокуються лише виправні `HIGH/CRITICAL`.

**CD layer (`cd-deploy`)**
- Умова запуску: тільки події, де в caller передано `deploy=true` (у цьому репозиторії: auto deploy для `dev` push, production deploy для `release` з тегом `v*`).
- Release semantics:
    - `dev` -> development path (tag `dev`)
    - `vMAJOR.MINOR.PATCH` -> production path (semver tag)
- Deploy transport: SSH over Tailscale.

### 10. Zero Trust Deploy Path (Tailscale)

Деплой працює через tailnet і не покладається на публічний доступ до сервера.

- Перед SSH використовується `tailscale/github-action@v4`.
- Авторизація: `TAILSCALE_AUTHKEY` (GitHub Secret).
- Обов'язкові secrets для deploy: `SERVER_HOST`, `SERVER_USER`, `SERVER_SSH_KEY`, `DEPLOY_PROJECT_DIR`, `TAILSCALE_AUTHKEY`.
- На сервері deploy path для Swarm виконує `scripts/deploy-orchestrator-swarm.sh`: render manifest через `docker compose --env-file ... config`, створення versioned runtime env secret і `docker stack deploy`.
- У default local-image режимі збираються `kdv-integrator-event:<git-sha>` та `kdv-optimizer:<git-sha>`, після чого Swarm service spec оновлюється без залежності від registry pull.
- Runtime secrets надходять у контейнер через Swarm secret payload і `scripts/entrypoint.sh`; окремі `docker exec` процеси не успадковують env PID1, тому manual wrappers мають явно передавати env через `docker exec --env-file`.
- Google Drive service account монтується тільки в `kdv-api` як `/run/secrets/gdrive_service_account_json`; `kdv-optimizer` цей secret не отримує. Перевірка виконується через `test -s`, без `cat` або виводу payload.

### 11. API Security Path (M4 implemented)

Після M4 в API використовується керований режим авторизації:

- `KDV_AUTH_MODE=legacy`: тільки `X-KDV-TOKEN` (поточна сумісність).
- `KDV_AUTH_MODE=dual`: приймається або `X-KDV-TOKEN`, або валідний Cloudflare Access JWT.
- `KDV_AUTH_MODE=cf-only`: тільки Cloudflare Access JWT.

Cloudflare Access JWT приймається з двох джерел:

- Header: `Cf-Access-Jwt-Assertion`.
- Cookie: `CF_Authorization` (browser flow через Cloudflare Access).

Валідація JWT виконується через JWK endpoint:

- `https://<CF_ACCESS_TEAM_DOMAIN>/cdn-cgi/access/certs`
- обов'язкові claims: `aud=CF_ACCESS_AUD`, `iss=https://<CF_ACCESS_TEAM_DOMAIN>`.
- вимога runtime: `PyJWT` + `cryptography` для перевірки `RS256`.

CORS працює в strict режимі через allowlist:

- `KDV_CORS_ALLOWLIST` (comma-separated origins),
- fallback: `KOHA_OPAC_URL`.
- `Access-Control-Allow-Credentials: true` для дозволених origin (щоб браузер передавав CF cookies).

Koha JS для browser-flow:

- Використовує `xhrFields.withCredentials=true` для `POST/PUT/GET status`.
- Перед критичними діями робить pre-check сесії через `GET /kdv/api/health`.
- Якщо сесії немає, відкриває захищений endpoint `repo.../kdv/api/health`, після чого Cloudflare сам формує валідний login redirect (`kid/meta`).

Оновлення після доменної міграції на `repo.pinokew.buzz`:

- API має базовий route `GET /kdv/api` (service index), щоб базовий URL не повертав "порожній" 404.
- Readiness доступний у двох сумісних alias: `GET /kdv/api/ready` і `GET /kdv/api/readiness`.
- `IntranetUserJS.js` використовує `detectArchivedRecord()` для перемикання кнопки `Archive` -> `Update`: перевіряє не тільки домен, а й DSpace шаблони `/handle/`, `/items/` та fallback по тексту details-блоку (включно з 856).

Це дозволяє прибирати токен із Koha JS без різкого відключення server-to-server сценаріїв.

### 12. Ops/Docs Invariants

- Plaintext `.env` з секретами не комітиться; штатний runtime/deploy контекст зберігається в `env.dev.enc`/`env.prod.enc` через SOPS/age.
- Для CI використовується `.env.example` + CI mock values.
- Manual/deploy скрипти резолвлять env у пріоритеті `ORCHESTRATOR_ENV_FILE` → `SERVER_ENV`/`ENVIRONMENT_NAME` → `env.<env>.enc` → `.env` fallback тільки для локального dev.
- Release Gate синхронізований з `docs/ROADMAP.md` (M3 секція).
- Зміни в CI/deploy мають відображатися в `CHANGELOGS/` і, за потреби, у runbooks.
- Google Drive source має залишатися read-only/no-writeback: жодних upload/update/delete до Drive API.

### 13. Ops readiness (M5)

- Публічні probes:
    - `GET /kdv/api/health` (liveness)
    - `GET /kdv/api/ready` (readiness, перевірка mount path)
- Runbooks:
    - `docs/RUNBOOK_TESTING.md` (dev/testing flow)
    - `docs/RUNBOOK_MAYDAY.md` (production incidents + recovery)
    - `docs/RUNBOOK_GDRIVE_SOURCE.md` (Google Drive source smoke, troubleshooting, rollback)
    - `docs/RUNBOOK_KOHA_EXPORT.md` (Koha Export CLI, конфігурація, staged-idempotency recovery, troubleshooting)

### 14. Test strategy (M6)

- Unit + integration тести працюють у контейнері через `pytest -q`.
- Contract рівень зафіксований у `tests/test_contracts.py`:
    - Koha CGI: exact field/header names для login/upload/attach.
    - DSpace: `/pid/find` params і JSON Patch contract для metadata update.

### 15. Release and rollback (M7)

- Canary flow і release discipline описані в `docs/RELEASE.md`.
- Rollback підтримує два сценарії:
    - повернення на попередній стабільний git tag (`vMAJOR.MINOR.PATCH`),
    - повернення на попередній image digest (якщо deploy працює з registry image).
- Batch rate limiting / parallelism контрольовані env-параметрами:
    - `ROBOT_PARALLELISM`, `ROBOT_BATCH_DELAY`, `ROBOT_POLL_INTERVAL`, `ROBOT_MAX_WAIT`
    - `NIGHTWALKER_AUTO_DELAY`, `NIGHTWALKER_RANGE_DELAY`
- У Swarm manual runtime оператор запускає Robot через `scripts/run-robot-swarm.sh`, а не напряму через `docker compose exec` або `docker exec scripts/robot.py`. Wrapper:
    - резолвить SOPS/age env-контекст;
    - знаходить локальний task-контейнер `kdv-api` через label `com.docker.swarm.service.name`;
    - передає env у `docker exec --env-file`, щоб `robot.py` бачив `KDV_API_TOKEN`;
    - копіює host `candidates.txt` у контейнер як `/tmp/kdv-candidates.txt`;
    - після завершення синхронізує `/app/logs/robot_batch.log` у host `logs/robot_batch.log`.

---

### Code Organization (M2/M3 — чиста архітектура)

```
src/
├── app.py                    # Flask + фабрика _make_clients + ендпоінти
├── tasks.py                  # TaskManager (in-memory queue) + kwargs support
├── core.py                   # Оркестратор (process_integration_logic, run_dspace_workflow)
├── config.py                 # Env bootstrap: ORCHESTRATOR_ENV_FILE / SERVER_ENV / SOPS env.*.enc / .env fallback
├── mapping.py                # MARC → Dublin Core rules
├── koha.py                   # KohaClient (реальна реалізація)
├── dspace.py                 # DSpaceClient (реальна реалізація)
├── clients/
│   ├── koha.py              # KohaClientWrapper (для DI)
│   └── dspace.py            # DSpaceClientWrapper (для DI)
└── services/
    ├── covers.py            # CoverService (self-contained)
    └── files.py             # FileService (versioning, error-move)

src/export_module/           # Koha Export Module (CLI/batch, ізольований)
├── __main__.py              # CLI entrypoint: --health-check, --dry-run, --reset-pending, --biblionumber-from/to
├── orchestrator.py          # ExportOrchestrator: staged pipeline
├── config.py                # ExportConfig + RuntimeOptions (SOPS bootstrap)
├── db/
│   ├── schema.py            # Export DDL and shared SQLite MigrationManager
│   └── repository.py        # ExportRepository: staged state transitions
├── koha/
│   ├── client.py            # KohaApiClient: keyset pagination, optional range
│   └── filters.py           # filter_exportable_biblios()
├── marc/
│   ├── parser.py            # MARCParser: defensive parsing + transforms
│   └── mapping_loader.py    # MappingLoader: YAML + JSON Schema + dict refs
├── xlsx/
│   └── generator.py         # XLSXGenerator: openpyxl, /tmp, atomic naming
├── services/
│   ├── drive_mount_service.py   # ExportDriveMountService: /mnt/drive atomic copy
│   └── graph_email_service.py   # GraphEmailService: MS Graph sendMail
└── observability/
    └── logger.py            # JSON structured logger, run_id contextvars, secret sanitizer

config/
├── marc_mapping.yaml        # MARC → XLSX column mapping + static columns + transforms
└── export_dictionaries.yaml # Koha Authorized values → кириличні мітки

scripts/
├── deploy-orchestrator-swarm.sh # Swarm deploy orchestration + versioned env secret
├── run-robot-swarm.sh           # Manual Swarm wrapper для Robot: env/container/candidates/log sync
├── robot.py                     # Batch logic, викликається wrapper-ом у Swarm
├── healthcheck.sh               # Pre-deploy/runtime health validation
└── render-versioned-env-secret.sh
```

**Принципи:**
- **SRP (Single Responsibility):** Кожен модуль робить одне.
- **DIP (Dependency Inversion):** core.py отримує залежності через параметри, не створює їх сам.
- **Testability:** Всім функціям можна передати стільки клієнтів, скільки потрібно для моків.

### Запуск Тестів (M6)

Дивіться [docs/RUNBOOK_TESTING.md](RUNBOOK_TESTING.md) для всіх команд.

Коротко:
```bash
docker compose pull
docker compose up -d
docker exec -e PYTHONPATH=/app kdv-api pytest -q
```

Станом на 2026-03-05: очікувано `22 passed` (unit + integration + contract).

Станом на 2026-05-29: повний baseline разом із Koha Export Module — `94 passed` (unit + integration + contract + export pipeline):
```bash
docker exec -e PYTHONPATH=/app:/app/kdv-optimizer kdv-api pytest -q
```

---

### 16. Koha Export Module (ізольована CLI/batch-підсистема)

Koha Export Module — це окрема CLI/batch-підсистема у `src/export_module/`, яка не впливає на основний Koha → DSpace pipeline. Запускається вручну, за розкладом або через захищений асинхронний UI control endpoint.

**Роль:** Періодичний пакетний експорт бібліографічних записів Koha у XLSX → архівація на Google Drive → email-розсилка через Microsoft Graph API.

**Ключові архітектурні рішення:**

| Рішення | Деталь |
|---|---|
| **Транспорт Google Drive** | Записує через rclone-mounted `/mnt/drive`; Google Drive API / service account не потрібні |
| **Email** | MS Graph `sendMail`; SMTP не використовується |
| **State tracking** | SQLite staged-idempotency: `pending → xlsx_generated → gdrive_uploaded → email_sent → completed` |
| **Pagination** | Keyset по `biblionumber > last_seen_id`; offset-based тільки як fallback |
| **Dry-run** | Тільки через `--dry-run` CLI прапорець; env-змінна `EXPORT_DRY_RUN` не існує |
| **Range export** | Тільки через `--biblionumber-from` / `--biblionumber-to`; env-змінних для range нема |
| **Конфіг** | Декларативний YAML: `config/marc_mapping.yaml` + `config/export_dictionaries.yaml` |
| **Ізоляція** | Не змінює `src/core.py`, `src/koha.py`, семантику `956$u/p/q` |

**Staged-idempotency — recovery rules:**

```
pending / xlsx_generated  → повна повторна обробка при наступному запуску
gdrive_uploaded           → reuse існуючого файлу, тільки email
email_sent                → тільки mark_completed, повторний email не надсилається
completed                 → назавжди виключається з обробки
failed (retry_count < MAX_RETRIES)  → повторна обробка
```

**Схема pipeline:**

```
Koha REST API
     │ keyset pagination (biblionumber > last_seen_id)
     ▼
KohaApiClient ──► filter_exportable_biblios()
                         │
                         ▼
                  MARCParser ──► config/marc_mapping.yaml
                         │       config/export_dictionaries.yaml
                         ▼
                  XLSXGenerator ──► /tmp/export_Koha_{date}_{run_id[:8]}.xlsx
                         │
            ┌────────────┴────────────┐
            ▼                         ▼
 ExportDriveMountService      GraphEmailService
 /mnt/drive/.../year/         MS Graph sendMail
 .part → atomic rename        attach ≤15MB / link-only
            │                         │
            └────────────┬────────────┘
                         ▼
                SQLite exported_records
                status → completed
```

**CLI (запуск в контейнері):**

```bash
docker compose exec kdv-api python -m src.export_module --health-check
docker compose exec kdv-api python -m src.export_module --dry-run
docker compose exec kdv-api python -m src.export_module
docker compose exec kdv-api python -m src.export_module --biblionumber-from 1000 --biblionumber-to 1250
docker compose exec kdv-api python -m src.export_module --reset-pending <RUN_ID>
```

**Exit codes:** `0` = success · `1` = partial failure · `2` = total failure / validation error

**Межі інтеграції з основним pipeline:**

- Читає готові `856$u` (де `$y = "Файл"` або `$y = "Запис в репозиторії"`) після успішної архівації.
- `GoogleDriveSource` у `src/services/sources.py` — окремий read-only компонент для PDF source; `ExportDriveMountService` — окремий write-компонент тільки для XLSX copy.

**Документація:** [`docs/RUNBOOK_KOHA_EXPORT.md`](RUNBOOK_KOHA_EXPORT.md) · [`docs/koha-export/PRD_Koha_Export_Module.md`](koha-export/PRD_Koha_Export_Module.md)
