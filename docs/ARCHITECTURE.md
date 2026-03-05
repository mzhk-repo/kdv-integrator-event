Архітектура та Workflow (v0.3.0-M7)

Цей документ описує логіку роботи KDV Integrator v0.3.0 з урахуванням M2 hardening (розділення сервісів, DI, тестованість), M3 (CI/CD pipeline, security gates, release/deploy flow), M4 (Zero Trust + CORS), M5 (ops readiness/runbooks), M6 (contract tests) та M7 (release canary + rollback + batch controls).

🔄 Загальний Workflow (Fork-Join Pattern)

Процес обробки однієї книги розділений на Послідовну фазу (підготовка) та Паралельну фазу (виконання).

graph TD
    User((Koha UI)) -->|POST /integrate| API[Integrator API]
    API -->|Return task_id| User
    API -->|Start Thread| Core[Async Core Logic]
    
    subgraph "Serial Phase (Blocking)"
        Core -->|Check 956$u| FileCheck{File Exists?}
        FileCheck -->|No| Error[Exit & Log Error]
        FileCheck -->|Yes| Rename[Rename & Move to /Processed]
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
- **Task B (critical):** run_dspace_workflow() (метадані, Item, PDF upload). Якщо впада → ERROR, файл в Error folder.

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

856$u — Handle-посилання на репозиторій.

### 7. Протокол "Hybrid CGI" (Cover Upload)

REST API Koha не дозволяє повноцінно працювати з локальними обкладинками. Емулюємо дії людини:

- **Auth:** Логін через POST-форму на mainpage.pl (у CoverService._ensure_cgi_login).
- **AJAX Spoofing:** Завантаження файлу на upload-file.pl з заголовком `X-Requested-With: XMLHttpRequest` (інакше Koha не віддасть JSON).
- **Scraping:** Парсинг HTML сторінки інструментів для знаходження `imagenumber`, щоб сформувати публічне посилання.

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

### 8. CI/CD Архітектура (M3)

Після M3 у проєкті діє workflow `.github/workflows/ci-cd.yml` з двома незалежними шарами: `ci-checks` і `cd-deploy`.

**CI layer (`ci-checks`)**
- Trigger: `pull_request`, `push` у `main`, теги `v*.*.*`, `workflow_dispatch`.
- Gates: `ruff check .`, `pytest -q`, `pip-audit`, `trivy config`, `trivy image`.
- Build: `docker build` + `docker compose config` валідація через `.env.example` (без secrets у репозиторії).
- Security policy: `trivy image` виконується з `--ignore-unfixed`, тому блокуються лише виправні `HIGH/CRITICAL`.

**CD layer (`cd-deploy`)**
- Умова запуску: тільки `push` у `main` або тег `v*` і лише після успішного `ci-checks`.
- Release semantics:
    - `main` -> staging path
    - `vMAJOR.MINOR.PATCH` -> production path
- Deploy transport: `appleboy/ssh-action`.

### 9. Zero Trust Deploy Path (Tailscale)

Деплой працює через tailnet і не покладається на публічний доступ до сервера.

- Перед SSH використовується `tailscale/github-action@v4`.
- Авторизація: `TAILSCALE_AUTHKEY` (GitHub Secret).
- Обов'язкові secrets для deploy: `SERVER_HOST`, `SERVER_USER`, `SERVER_SSH_KEY`, `DEPLOY_PROJECT_DIR`, `TAILSCALE_AUTHKEY`.
- На сервері деплой виконує: `git fetch`, checkout потрібного ref, `docker compose pull`, `docker compose up -d --remove-orphans`, `ps/logs`.

### 10. API Security Path (M4 implemented)

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

Це дозволяє прибирати токен із Koha JS без різкого відключення server-to-server сценаріїв.

### 11. Ops/Docs Invariants

- `.env` з секретами не комітиться; для CI використовується `.env.example` + CI mock values.
- Release Gate синхронізований з `docs/ROADMAP.md` (M3 секція).
- Зміни в CI/deploy мають відображатися в `CHANGELOGS/` і, за потреби, у runbooks.

### 12. Ops readiness (M5)

- Публічні probes:
    - `GET /kdv/api/health` (liveness)
    - `GET /kdv/api/ready` (readiness, перевірка mount path)
- Runbooks:
    - `docs/RUNBOOK_TESTING.md` (dev/testing flow)
    - `docs/RUNBOOK_MAYDAY.md` (production incidents + recovery)

### 13. Test strategy (M6)

- Unit + integration тести працюють у контейнері через `pytest -q`.
- Contract рівень зафіксований у `tests/test_contracts.py`:
    - Koha CGI: exact field/header names для login/upload/attach.
    - DSpace: `/pid/find` params і JSON Patch contract для metadata update.

### 14. Release and rollback (M7)

- Canary flow і release discipline описані в `docs/RELEASE.md`.
- Rollback підтримує два сценарії:
    - повернення на попередній стабільний git tag (`vMAJOR.MINOR.PATCH`),
    - повернення на попередній image digest (якщо deploy працює з registry image).
- Batch rate limiting / parallelism контрольовані env-параметрами:
    - `ROBOT_PARALLELISM`, `ROBOT_BATCH_DELAY`, `ROBOT_POLL_INTERVAL`, `ROBOT_MAX_WAIT`
    - `NIGHTWALKER_AUTO_DELAY`, `NIGHTWALKER_RANGE_DELAY`

---

### Code Organization (M2/M3 — чиста архітектура)

```
src/
├── app.py                    # Flask + фабрика _make_clients + ендпоінти
├── tasks.py                  # TaskManager (in-memory queue) + kwargs support
├── core.py                   # Оркестратор (process_integration_logic, run_dspace_workflow)
├── config.py                 # Env vars + validation
├── mapping.py                # MARC → Dublin Core rules
├── koha.py                   # KohaClient (реальна реалізація)
├── dspace.py                 # DSpaceClient (реальна реалізація)
├── clients/
│   ├── koha.py              # KohaClientWrapper (для DI)
│   └── dspace.py            # DSpaceClientWrapper (для DI)
└── services/
    ├── covers.py            # CoverService (self-contained)
    └── files.py             # FileService (versioning, error-move)
```

**Принципи:**
- **SRP (Single Responsibility):** Кожен модуль робить одне.
- **DIP (Dependency Inversion):** core.py отримує залежності через параметри, не створює їх сам.
- **Testability:** Всім функціям можна передати стільки клієнтів, скільки потрібно для моків.

### Запуск Тестів (M6)

Дивіться [docs/RUNBOOK_TESTING.md](RUNBOOK_TESTING.md) для всіх команд.

Коротко:
```bash
docker compose up -d --build
docker exec -e PYTHONPATH=/app kdv-api pytest -q
```

Станом на 2026-03-05: очікувано `22 passed` (unit + integration + contract).