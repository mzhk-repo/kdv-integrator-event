KDV Integrator (v6.5)

KDV Integrator — це middleware-сервіс для автоматизованої синхронізації бібліотечної системи Koha ILS та цифрового репозиторію DSpace 7/8.

Система забезпечує передачу метаданих, PDF-файлів, автоматичну генерацію обкладинок та зворотну синхронізацію посилань.

🚀 Ключові можливості

Async Core: API миттєво повертає task_id, виконуючи важкі операції у фоновому режимі (захист від Cloudflare Timeout).

Parallel Processing: Генерація обкладинки та завантаження в DSpace відбуваються одночасно (Fork-Join pattern), що прискорює обробку на 50%.

Cover Automator: Автоматичне створення JPG-мініатюр з першої сторінки PDF.

CGI Protocol Bypass: Унікальний механізм завантаження обкладинок через емуляцію браузера (обхід обмежень Koha REST API).

Data Warehouse: Збагачення MARC-записів прямими посиланнями на обкладинки (956$c) та DSpace Handle (856$u).

📂 Структура проєкту (File Map)

Опис призначення кожного файлу для розробників:

src/ — Основний код

Файл

Опис

app.py

Точка входу. Flask-сервер із фабрикою клієнтів `_make_clients()` (DI). Ендпоінти API, CORS.

tasks.py

Task Manager. In-Memory чергу, підтримка kwargs (DI). Статуси: queued, processing, success/error.

core.py

**[НОВЕ, M2]** Оркестратор інтеграції. Fork-Join паралелізм (DSpace critical + Covers best-effort). Dependency Injection для клієнтів.

koha.py

Koha Client. REST API (MARC), CGI Emulation (логін, завантаження, скрапінг).

dspace.py

DSpace Client. REST API 7+, авторизація, Items, Bitstreams, дублікати.

config.py

Конфігурація з .env. Перевірки обов'язкових змінних.

mapping.py

Правила MARC → Dublin Core (Regex, конвертація типів).

services/ (НОВА, M2)

**covers.py:**  Cover Service. pdf2image → JPG, resize, retry policy, Koha upload.  
**files.py:**  File Service. Versioning, rename-first, Processed/Error folders.

clients/ (НОВА, M2)

**koha.py:**  KohaClientWrapper (для тестів).  
**dspace.py:**  DSpaceClientWrapper (для тестів).

scripts/ — Утиліти

robot.py

Масова пакетна обробка (Batch Processing).

nightwalker.py

Аудит: пошук "зомбі" (файли без лінків), синхронізація метаданих.

debug_*.py

Діагностичні скрипти.

tests/ — Unit + інтеграційні тести (НОВЕ, M2)

test_core.py

Тести DI: `parse_marc_details`, `run_dspace_workflow`, task_manager integration (з моками).

test_services.py

Тести FileService (versioning, error-move), CoverService initialization.

test_clients.py, test_state_machine.py

Перевірки клієнтів і state machine.

manual_smoke.py

Smoke‑скрипт із stub‑класами для швидкої перевірки логіки.

docs/ — Документація

ROADMAP.md

Дорожна карта (milestones M1–M7, DoD для кожного).

ARCHITECTURE.md

Архітектура, workflow (Fork-Join), безпека, відмовостійкість.

RUNBOOK_TESTING.md

**[НОВЕ]** Покрокова інструкція щодо тестування змін через мокі. Обов'язково перед модифікацією модулів!

🛠 Налаштування та Запуск

1. Змінні середовища (.env)

Створіть файл .env на основі прикладу:

# KOHA CONFIG
KOHA_API_URL=[http://koha-intra.local](http://koha-intra.local)   # Staff Interface
KOHA_OPAC_URL=[https://biblio.univ.edu](https://biblio.univ.edu)  # Public Interface (для лінків)
KOHA_API_USER=kdv_bot
KOHA_API_PASS=secret

# DSPACE CONFIG
DSPACE_API_URL=[https://repo.univ.edu/server](https://repo.univ.edu/server)
DSPACE_UI_URL=[https://repo.univ.edu](https://repo.univ.edu)
DSPACE_API_USER=admin@univ.edu
DSPACE_API_PASS=secret

# INTEGRATOR CONFIG
KDV_API_TOKEN=your-secure-token
INTEGRATOR_MOUNT_PATH=/mnt/drive
FOLDER_PROCESSED=Processed
FOLDER_ERROR=Error


2. Запуск через Docker

docker compose up -d --build


Команда запуску використовує gunicorn з 1 воркером та 4 потоками для підтримки спільної пам'яті задач:
gunicorn -w 1 --threads 4 -b 0.0.0.0:5000 src.app:app

🔗 API Endpoints

1. Почати інтеграцію (Async)

POST /kdv/api/integrate/{biblionumber}

Headers: X-KDV-TOKEN: ...

Response: 202 Accepted + {"task_id": "..."}

2. Перевірити статус

GET /kdv/api/status/{task_id}

Response: {"status": "processing" | "success" | "error", "result": {...}}

3. Оновити метадані (Sync)

PUT /kdv/api/integrate/{biblionumber}

Оновлює назву/авторів у DSpace на основі змін у Koha.

---

🧪 Запуск тестів

В контейнері (рекомендувано, в ньому є всі залежності):

```bash
docker compose up -d --build
docker exec -e PYTHONPATH=/app kdv-api pytest -q          # усі тести
docker exec -e PYTHONPATH=/app kdv-api pytest tests/test_core.py::test_parse_marc_rules_basic -q  # окремий тест
```

Локально (з pip + pytest):

```bash
python3 -m pip install -r requirements.txt pytest
PYTHONPATH=$(pwd) pytest -q
```

**Щодо моків і DI при тестуванні**, дивіться **[docs/RUNBOOK_TESTING.md](docs/RUNBOOK_TESTING.md)** — там детально описано як писати тести із stub‑клієнтами.