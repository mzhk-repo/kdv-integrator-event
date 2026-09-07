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

- Викликається у кожному HTTP обробнику (`POST /integrate`, `PUT /integrate`).
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

Структура полів залишилась незмінною, але реалізація тепер у `core.py` з чистою DI:


956$y — Статус (imported, error).

956$z — Лог помилки або попередження.

956$3 — UUID елемента в DSpace (для дедуплікації).

956$c — Пряме посилання на обкладинку (опак-image.pl?imagenumber=...).

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
│   ├── schema.py            # DDL: exported_records, SCHEMA_V1, MigrationManager
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
