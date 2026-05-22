# 🏗️ KDV Integrator

> **Middleware-сервіс для автоматизованої синхронізації Koha ILS із цифровим репозиторієм DSpace 7/8.** Отримує запит на архівацію → завантажує PDF і метадані до DSpace → генерує обкладинку → синхронізує посилання назад у MARC-запис Koha.

[![Status](https://img.shields.io/badge/status-production--ready-brightgreen)]()
[![Version](https://img.shields.io/badge/version-0.4.0--m8-blue)]()
[![Tests](https://img.shields.io/badge/tests-pytest-brightgreen)]()
[![Security](https://img.shields.io/badge/security-Cloudflare%20Zero%20Trust-blueviolet)]()
[![License](https://img.shields.io/badge/license-internal-lightgrey)]()

---

## 📋 Зміст

- [Поточний статус](#-поточний-статус)
- [Про проєкт](#-про-проєкт)
- [Архітектура стеку](#-архітектура-стеку)
- [Топологія репозиторію](#-топологія-репозиторію)
- [Топологія системи](#-топологія-системи)
- [Середовища](#-середовища)
- [Безпека](#-безпека)
- [Локальний запуск](#-локальний-запуск)
- [Деплой](#-деплой)
- [API & Інтеграції](#-api--інтеграції)
- [Моніторинг & Алерти](#-моніторинг--алерти)
- [Ченджлог](#-ченджлог)

---

## 🚦 Поточний статус

| Параметр | Значення |
|---|---|
| **Поточна версія** | `v0.4.0-m8` |
| **Стадія** | Production-ready |
| **Останній реліз** | `2026-05-17` |
| **Поточний мілстоун** | `M8` — PDF optimizer, post-prod analytics & scale-out → [Roadmap](docs/ROADMAP.md), [PDF optimizer roadmap](docs/pdf-optimizer/roadmap-optimizer.md) |
| **Відомі критичні баги** | `0` |
| **Технічний борг** | 🟢 Низький |

### Останні зміни (2026-05-17)
- Додано ізольований сервіс `kdv-optimizer` для автоматичної оптимізації важких PDF перед upload у DSpace.
- `docker-compose.yml`/Swarm deploy тепер піднімають два сервіси: `kdv-api` та внутрішній `kdv-optimizer` зі shared volume `kdv_optimize_data`.
- `/integrate` backward-compatible: старі клієнти без JSON body працюють, новий payload підтримує `skip_optimization`.
- `scripts/robot.py` підтримує `--skip-optimization`, `--parallelism`, `--max-wait`.
- Додано [RUNBOOK_PDF_OPTIMIZER.md](docs/RUNBOOK_PDF_OPTIMIZER.md) для конфігурації, health/readiness, troubleshooting і rollback.

### Попередні помітні зміни (2026-03-13)
- Міграція домену інтегратора на `repo.pinokew.buzz` (Cloudflare Access Application URL: `/kdv/api/*`).
- Додано базовий endpoint `GET /kdv/api` (service index) та alias `GET /kdv/api/readiness`.
- Оновлено `IntranetUserJS.js`: визначення "вже архівовано" тепер працює не лише по домену, а й по шаблонах DSpace-лінків (`/handle/`, `/items/`) і 856-контенту на сторінці.

### Що зараз в роботі (M8)
- [x] Ізольований PDF optimizer service (`kdv-optimizer`)
- [x] Shared volume, Swarm deploy і readiness перевірки optimizer-а
- [x] Koha UI/API/Robot skip optimization controls
- [ ] Post-prod SLO baseline: p95 latency, fallback rate, queue depth
- [ ] Тюнінг rate-limit/parallelism параметрів за даними prod-трафіку

---

## 🎯 Про проєкт

### Проблема та рішення
Бібліотека зберігає книги у Koha ILS (MARC-каталог) та цифровому репозиторії DSpace 7/8. Процес архівації раніше виконувався вручну: завантаження PDF, заповнення метаданих Dublin Core, генерація обкладинки, зворотне оновлення MARC-полів 856/956. KDV Integrator автоматизує цей pipeline повністю — один HTTP-запит запускає весь ланцюжок у фоновому режимі.

### Ключові можливості

- **Async Core** — API миттєво повертає `task_id`, важкі операції виконуються у фоні (захист від Cloudflare 524 Timeout)
- **Fork-Join паралелізм** — завантаження до DSpace та генерація обкладинки відбуваються одночасно (+50% швидкості)
- **Cover Automator** — автоматичне створення JPG-мініатюр з першої сторінки PDF (`pdf2image`) або завантаження готової обкладинки з `956$p`
- **PDF Optimizer** — ізольований `kdv-optimizer` стискає важкі scan-like PDF через Ghostscript перед DSpace upload, із fallback на оригінал
- **CGI Protocol Bypass** — завантаження обкладинок через емуляцію браузерної сесії (обхід обмежень Koha REST API)
- **MARC Enrichment** — зворотній запис двох `856`: прямий download primary bitstream (`$y Файл`) і Handle DSpace (`$y Запис в репозиторії`); `956$p` задає готову обкладинку, `956$q` — additional файли для ORIGINAL
- **Batch & Audit** — `robot.py` для масової архівації, `nightwalker.py` для пошуку "зомбі" (файли без посилань)

### Що НЕ входить у скоуп

- Управління користувачами / RBAC (це відповідальність Koha та Cloudflare Access)
- Редагування метаданих поза MARC-полями, що визначені у `src/mapping.py`
- Цифрове зберігання та резервне копіювання PDF-файлів (відповідає DSpace)

---

## ⚙️ Архітектура стеку

### Зведена таблиця технологій

| Шар | Технологія | Версія | Призначення |
|---|---|---|---|
| **API** | Flask | 3.x | HTTP-сервер, ендпоінти, CORS |
| **WSGI** | gunicorn | — | 1 воркер × 4 потоки (спільна пам'ять task-стану) |
| **Мова** | Python | 3.11+ | Весь backend |
| **Контейнеризація** | Docker + Compose | — | Ізоляція середовища, оркестрація |
| **CI/CD** | GitHub Actions | — | Lint, тести, security scan, деплой |
| **Безпека** | Cloudflare Access + Tailscale | — | Zero Trust: JWT-auth, VPN-tunnel |
| **Тестування** | pytest | — | Unit + integration + contract тести |
| **Linting / Quality** | ruff | — | Статичний аналіз |
| **Dependency Audit** | pip-audit + trivy | — | CVE-сканування |
| **PDF Processing** | pdf2image + Pillow | — | Генерація обкладинок |
| **PDF Optimization** | Ghostscript + poppler-utils | pinned у `kdv-optimizer` image | Оптимізація важких PDF в ізольованому контейнері |

### Принципи архітектури

- **Стиль**: Single-process event-driven (Flask + ThreadPoolExecutor)
- **Паттерни**: Fork-Join, Dependency Injection, Thin-Wrapper Clients, State Machine (задачі)
- **Комунікація**: Sync REST (Koha/DSpace API), CGI emulation (Koha covers upload), internal HTTP `kdv-api` → `kdv-optimizer`
- **Консистентність**: Task ID + ідемпотентний pipeline (перевірка дублікатів у DSpace перед завантаженням)
- **PDF fallback**: будь-яка помилка optimizer-а не валить архівацію, DSpace отримує original PDF із telemetry у `task.result`

### Схема взаємодії компонентів

```
  Koha IntranetUserJS             Operator (robot.py / nightwalker.py)
           │                                      │
           │ POST /kdv/api/integrate/{biblio}      │ HTTP calls
           ▼                                      ▼
  ┌─────────────────────────────────────────────────────┐
  │              Cloudflare Access (Zero Trust)         │
  │         JWT validation · CORS allowlist check       │
  └──────────────────────┬──────────────────────────────┘
                         │
  ┌──────────────────────▼──────────────────────────────┐
  │                Flask API (src/app.py)               │
  │   _make_clients() — Dependency Injection factory    │
  │   TaskManager — in-memory task queue + status       │
  └──────────────────────┬──────────────────────────────┘
                         │ background thread
  ┌──────────────────────▼──────────────────────────────┐
  │             Integration Orchestrator (src/core.py)  │
  │                                                     │
  │   ┌────────────────────┐   ┌─────────────────────┐  │
  │   │  DSpace Workflow   │   │   Cover Workflow     │  │
  │   │  (critical path)   │   │   (best-effort)      │  │
  │   │  ─────────────     │   │   ─────────────      │  │
  │   │  fetch PDF         │   │   pdf2image → JPG    │  │
  │   │  map MARC→DC       │   │   upload via CGI     │  │
  │   │  create DSpace item│   │   write 956$c        │  │
  │   │  optimize PDF       │   │                      │  │
  │   │  upload bitstream  │   │                      │  │
  │   │  write 856 links   │   └─────────────────────┘  │
  │   └────────────────────┘   Fork-Join (ThreadPool)   │
  └─────────────────────────────────────────────────────┘
                         │
         ┌───────────────┴───────────────┐
         ▼                               ▼
  ┌─────────────┐                ┌──────────────┐
  │  Koha ILS   │                │  DSpace 7/8  │
  │  REST + CGI │                │  REST API    │
  └─────────────┘                └──────────────┘
```

---

## 🗂️ Топологія репозиторію

```
kdv-integrator-event/
│
├── 📁 src/                         # Основний код
│   ├── app.py                      # Flask-сервер, DI-фабрика, ендпоінти, CORS
│   ├── core.py                     # Оркестратор pipeline (Fork-Join)
│   ├── tasks.py                    # TaskManager (in-memory черга + статуси)
│   ├── koha.py                     # Koha Client: REST API + CGI emulation
│   ├── dspace.py                   # DSpace Client: REST 7+, Items, Bitstreams
│   ├── config.py                   # ENV-конфігурація, валідація обов'язкових змінних
│   ├── mapping.py                  # MARC → Dublin Core (regex, type conversion)
│   ├── clients/
│   │   ├── koha.py                 # KohaClientWrapper (thin wrapper для DI/тестів)
│   │   └── dspace.py               # DSpaceClientWrapper (thin wrapper для DI/тестів)
│   └── services/
│       ├── covers.py               # CoverService: pdf2image → JPG, retry policy
│       ├── files.py                # FileService: versioning, Processed/Error folders
│       └── pdf.py                  # PDFOptimizerClient + size/page/disk heuristics
│
├── 📁 kdv-optimizer/               # Ізольований PDF optimizer mini-service
│   ├── optimizer_app.py            # Flask API: /optimize, /health, /ready
│   ├── Dockerfile                  # Ghostscript/poppler pinned image, non-root user
│   └── kdv_optimizer/              # OptimizerConfig, PDFOptimizerService, TTLJanitor
│
├── 📁 scripts/                     # Утилітні скрипти
│   ├── robot.py                    # Пакетна архівація (Batch Processing)
│   ├── nightwalker.py              # Аудит каталогу: пошук "зомбі", sync метаданих
│   ├── healthcheck.sh              # Curl-перевірка /health для Docker HEALTHCHECK
│   └── poc_optimizer.py            # R&D benchmark PDF рушіїв
│
├── 📁 tests/                       # Тести
│   ├── test_core.py                # Unit-тести оркестратора (DI, Fork-Join)
│   ├── test_services.py            # FileService, CoverService, PDF optimizer heuristics
│   ├── test_clients.py             # Клієнти-обгортки
│   ├── test_state_machine.py       # State machine + ідемпотентність
│   ├── test_app.py                 # Flask endpoints (smoke)
│   ├── test_contracts.py           # Contract-тести: Koha CGI fields, DSpace /pid/find
│   └── manual_smoke.py             # Ручний smoke зі stub-класами
│
├── 📁 docs/                        # Документація
│   ├── ROADMAP.md                  # Milestones M0–M8 з DoD-чеклістами
│   ├── ARCHITECTURE.md             # Детальна архітектура, security, workflow
│   ├── RELEASE.md                  # Процес релізу: staging → prod, canary, rollback
│   ├── RUNBOOK_TESTING.md          # Тестування: моки, DI, порядок запуску
│   ├── RUNBOOK_MAYDAY.md           # Incident response: Cloudflare/Koha/DSpace/mount
│   ├── RUNBOOK_NIGHTWALKER.md      # Інструкція: аудит каталогу через nightwalker.py
│   ├── RUNBOOK_ROBOT.md            # Інструкція: масова архівація через robot.py
│   └── RUNBOOK_PDF_OPTIMIZER.md    # Інструкція: конфігурація, перевірка, rollback optimizer-а
│
├── 📁 docs/changelogs/
│   └── CHANGELOG_2026_VOL_03.md   # Активний том changelog (Context/Change/Verification)
│
├── 📁 archive/                     # Застарілі версії файлів (reference only)
├── docker-compose.yml              # Запуск сервісу в контейнері
├── Dockerfile                      # Образ Flask + gunicorn
├── gunicorn.ctl                    # gunicorn параметри (1w × 4t)
├── requirements.txt                # Python-залежності
├── .env.example                    # Шаблон усіх ENV змінних
├── candidates.txt                  # Список biblionumber для robot.py
└── IntranetUserJS.js               # Koha UI script (кнопка "Архівувати")
```

### Ключові файли

| Файл / Директорія | Призначення |
|---|---|
| `src/core.py` | Серце системи — Fork-Join оркестратор |
| `src/app.py` | DI-фабрика клієнтів, ендпоінти, auth middleware |
| `src/mapping.py` | Контракт MARC → Dublin Core (змінювати обережно) |
| `docs/RUNBOOK_MAYDAY.md` | Першочергово при production-інциденті |
| `docs/RUNBOOK_PDF_OPTIMIZER.md` | Використання, конфігурація, health/readiness, rollback `kdv-optimizer` |
| `docs/RELEASE.md` | Обов'язково перед будь-яким деплоєм |
| `.env.example` | Документація всіх ENV змінних |
| `candidates.txt` | Вхідні дані для robot.py (biblionumber per line) |
| `kdv-optimizer/` | Ізольований сервіс оптимізації PDF |

---

## 🌍 Топологія системи

```
  Бібліотекар (браузер)
         │
         │ HTTPS (IntranetUserJS.js)
         ▼
  ┌──────────────────────────────────────┐
  │     Koha Staff Interface (Intranet)  │
  │     Cloudflare Access Tunnel         │
  └──────────────┬───────────────────────┘
                 │ POST /kdv/api/integrate/{biblio}
                 │ Header: Cf-Access-Jwt-Assertion
                 ▼
  ┌──────────────────────────────────────┐
  │   KDV Integrator (Docker / gunicorn) │
  │   ← Tailscale VPN ←                 │
  └──────────────┬───────────────────────┘
        ┌────────┴────────┐
        ▼                 ▼
  ┌──────────┐     ┌─────────────┐
  │ Koha ILS │     │  DSpace 7/8 │
  │ (intra)  │     │  (repo)     │
  └──────────┘     └─────────────┘
        ▲
        │ 956$c (cover URL) + 856$u (file + handle)
        └── MARC update (зворотній запис)

  ┌──────────────────────────────────────┐
  │ kdv-optimizer (internal only)        │
  │ /health /ready /optimize             │
  │ shared volume: /data/kdv_optimize    │
  └──────────────────────────────────────┘
```

`kdv-optimizer` не публікує порт назовні. `kdv-api` звертається до нього як `http://kdv-optimizer:5001` через Docker network і передає тільки `job_id`; тимчасові PDF лежать у shared volume `kdv_optimize_data`.

---

## 🌍 Середовища

| Середовище | Хост | Деплой | Призначення |
|---|---|---|---|
| **Local** | `localhost:5000` | `docker compose up -d` | Розробка |
| **Staging** | Tailscale hostname | GitHub Actions (auto) | QA, canary перевірка |
| **Production** | Tailscale hostname | GitHub Actions + ручне підтвердження | Живий трафік |

Усі ENV-змінні задокументовані у `.env.example`. Секрети (паролі Koha/DSpace, API-токен) зберігаються лише в `.env` на сервері — **ніколи не комітьте `.env`**.

Образ сервісу запускається з GHCR через env:
- `KDV_IMAGE_REPOSITORY` (наприклад, `ghcr.io/pinokew/kdv-integrator-event`)
- `KDV_IMAGE_VERSION` (наприклад, `dev`, `main`, `v1.2.3`, `latest`, `sha-...`)
- опційно `KDV_IMAGE` (повний image reference), якщо треба явно зафіксувати digest/tag одним значенням
- `KDV_OPTIMIZER_IMAGE_REPOSITORY`, `KDV_OPTIMIZER_VERSION`, опційно `KDV_OPTIMIZER_IMAGE` для `kdv-optimizer`

Основні ENV для PDF optimizer:

| Змінна | Default | Призначення |
|---|---|---|
| `OPTIMIZER_URL` | `http://kdv-optimizer:5001` | Internal URL для `kdv-api` |
| `OPTIMIZER_TIMEOUT` | `130` | HTTP timeout клієнта, має бути > `GS_TIMEOUT` |
| `DATA_DIR` | `/data/kdv_optimize` | Shared volume root всередині контейнерів |
| `INPUT_DIR` | `/data/kdv_optimize/input` | Tmp input PDF для optimizer jobs |
| `OUTPUT_DIR` | `/data/kdv_optimize/output` | Tmp optimized PDF |
| `GS_TIMEOUT` | `120` | Timeout Ghostscript process |
| `TMP_TTL_SECONDS` | `86400` | TTL cleanup orphan temp-файлів |

Деталі: [`docs/RUNBOOK_PDF_OPTIMIZER.md`](docs/RUNBOOK_PDF_OPTIMIZER.md).

---

## 🔒 Безпека

### Модель автентифікації

| Режим (`KDV_AUTH_MODE`) | Механізм | Застосування |
|---|---|---|
| `legacy` | `X-KDV-TOKEN` (Bearer) | Внутрішні інтеграції без CF Access |
| `dual` | `X-KDV-TOKEN` **або** `Cf-Access-Jwt-Assertion` | Перехідний період |
| `cf-only` | `Cf-Access-Jwt-Assertion` (JWT RS256) | Production / Zero Trust |

### Захист мережі

```
Internet → Cloudflare Access (JWT) → Tailscale VPN → KDV Integrator → Koha / DSpace (internal)
```

- Cloudflare Access: JWT-валідація (`CF_ACCESS_TEAM_DOMAIN` + `CF_ACCESS_AUD`)
- Tailscale: zero-config VPN між сервісами (без відкритих портів)
- CORS: strict allowlist (`KDV_CORS_ALLOWLIST`), відхиляє будь-які невідомі origin
- `IntranetUserJS.js`: токен більше не є хардкодом — передається через захищений шаблон Koha (`window.KDV_TOKEN`)

### Інструменти Security

| Інструмент | Тип | Коли запускається |
|---|---|---|
| `ruff` | SAST / Linting | CI (PR) + pre-push |
| `pip-audit` | SCA (залежності) | CI (кожен build) |
| `trivy` | Container scan | CI (docker image build), включно з `kdv-optimizer` image |

**Incident Response:** [`docs/RUNBOOK_MAYDAY.md`](docs/RUNBOOK_MAYDAY.md)

---

## 🚀 Локальний запуск

### Передумови

| Інструмент | Перевірка |
|---|---|
| Docker + Compose v2 | `docker compose version` |
| Python 3.11+ (для тестів локально) | `python3 --version` |

### Швидкий старт

```bash
# 1. Налаштування змінних середовища
cp .env.example .env
# Заповніть: KOHA_*, DSPACE_*, KDV_API_TOKEN, RCLONE_REMOTE_NAME
# Для optimizer перевірте: OPTIMIZER_URL, DATA_DIR, INPUT_DIR, OUTPUT_DIR

# 2. Завантаження потрібних образів і запуск сервісів
docker compose pull
docker compose up -d

# 3. Перевірка
curl http://localhost:5000/kdv/api/health
curl http://localhost:5000/kdv/api/readiness

# 4. Перевірка optimizer-а через Docker network
docker exec kdv-api curl -fsS http://kdv-optimizer:5001/health
docker exec kdv-api curl -fsS http://kdv-optimizer:5001/ready
```

### Запуск тестів

```bash
# В контейнері (рекомендовано — всі залежності вже є)
docker exec -e PYTHONPATH=/app:/app/kdv-optimizer kdv-api pytest -q

# Конкретний рівень
docker exec -e PYTHONPATH=/app:/app/kdv-optimizer kdv-api pytest tests/test_contracts.py -q   # contract
docker exec -e PYTHONPATH=/app:/app/kdv-optimizer kdv-api pytest tests/test_core.py -q        # core
docker exec -e PYTHONPATH=/app:/app/kdv-optimizer kdv-api pytest tests/test_services.py -q    # optimizer/service

# Локально (pip + pytest)
PYTHONPATH=$(pwd):$(pwd)/kdv-optimizer pytest -q
```

Поточний baseline перевіряється через `pytest -q`; focused optimizer checks описані в [`docs/RUNBOOK_TESTING.md`](docs/RUNBOOK_TESTING.md).

### Типові проблеми

<details>
<summary>Readiness endpoint повертає 503</summary>

```bash
# Перевірте, що rclone Docker volume plugin встановлений і remote існує
docker plugin ls
rclone listremotes | grep "^${RCLONE_REMOTE_NAME}:$"
```
</details>

<details>
<summary>ModuleNotFoundError при локальному pytest</summary>

```bash
# Обов'язково задайте PYTHONPATH
PYTHONPATH=$(pwd):$(pwd)/kdv-optimizer pytest -q
```
</details>

---

## 📦 Деплой

### CI/CD Пайплайн

```
Push / PR → ruff (lint) → pytest → pip-audit → trivy (api + optimizer images)
                                                                   │
                                                         Push image to registry
                                                                   │
                                                   ┌─── Staging (auto) ────┐
                                                   │   smoke · healthcheck  │
                                                   └──────────┬─────────────┘
                                                              │ Manual approve
                                                         ┌────▼─────┐
                                                         │   PROD   │
                                                         └──────────┘
```

Детальна процедура: [`docs/RELEASE.md`](docs/RELEASE.md) — включно з canary flow та rollback через `docker tag` / image digest.

### Rollback

```bash
# Через версію образу в env
export KDV_IMAGE_VERSION=v0.2.1
docker compose pull
docker compose up -d --remove-orphans

# Через digest
export KDV_IMAGE=ghcr.io/pinokew/kdv-integrator-event@sha256:<prev-digest>
export KDV_OPTIMIZER_IMAGE=ghcr.io/OWNER/kdv-optimizer@sha256:<prev-digest>
docker compose pull
docker compose up -d --remove-orphans
```

---

## 🔌 API & Інтеграції

### Ендпоінти

| Метод | URL | Опис |
|---|---|---|
| `POST` | `/kdv/api/integrate/{biblionumber}` | Запустити async-інтеграцію → `202 + {task_id}` |
| `GET` | `/kdv/api/status/{task_id}` | Статус задачі: `queued / processing / success / error` |
| `PUT` | `/kdv/api/integrate/{biblionumber}` | Sync-оновлення метаданих у DSpace з Koha |
| `GET` | `/kdv/api` | Service index (base route) |
| `GET` | `/kdv/api/health` | Liveness probe → `200 OK` |
| `GET` | `/kdv/api/ready` / `/kdv/api/readiness` | Readiness probe (перевіряє mount path) → `200 / 503` |

Авторизація: `X-KDV-TOKEN: <token>` (legacy/dual) або `Cf-Access-Jwt-Assertion` (cf-only).

### Зовнішні інтеграції

| Сервіс | Протокол | Конфіг |
|---|---|---|
| Koha ILS | REST API + CGI emulation | `KOHA_API_URL`, `KOHA_OPAC_URL` |
| DSpace 7/8 | REST API (JSON Patch) | `DSPACE_API_URL`, `DSPACE_UI_URL` |
| Cloudflare Access | JWT RS256 | `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD` |
| PDF Optimizer | Internal HTTP + shared volume | `OPTIMIZER_URL`, `OPTIMIZER_TIMEOUT`, `/data/kdv_optimize` |

### Batch-утиліти

| Скрипт | Призначення | Інструкція |
|---|---|---|
| `scripts/robot.py` | Масова архівація зі списку `candidates.txt`, підтримує `--skip-optimization` | [`docs/RUNBOOK_ROBOT.md`](docs/RUNBOOK_ROBOT.md) |
| `scripts/nightwalker.py` | Аудит: пошук файлів без посилань | [`docs/RUNBOOK_NIGHTWALKER.md`](docs/RUNBOOK_NIGHTWALKER.md) |

**Batch controls (ENV):**

| Змінна | Default | Опис |
|---|---|---|
| `ROBOT_PARALLELISM` | `1` | Кількість паралельних запитів |
| `ROBOT_BATCH_DELAY` | `5` | Затримка між батчами (сек) |
| `ROBOT_POLL_INTERVAL` | `3` | Інтервал опитування статусу (сек) |
| `ROBOT_MAX_WAIT` | `900` | Timeout очікування задачі (сек) |
| `NIGHTWALKER_AUTO_DELAY` | `0.05` | Затримка між записами в auto-режимі (сек) |
| `NIGHTWALKER_RANGE_DELAY` | `0.10` | Затримка в range-режимі (сек) |

---

## 📊 Моніторинг & Алерти

### Health Probes

| Endpoint | Призначення | Очікувана відповідь |
|---|---|---|
| `GET /kdv/api/health` | Liveness (сервіс живий?) | `200 {"status": "ok"}` |
| `GET /kdv/api/ready` / `GET /kdv/api/readiness` | Readiness `kdv-api` (можна приймати трафік?) | `200` / `503` якщо mount недоступний |
| `GET http://kdv-optimizer:5001/health` | Liveness optimizer-а всередині Docker network | `200 {"status":"ok"}` |
| `GET http://kdv-optimizer:5001/ready` | Readiness optimizer-а: volume, write access, `gs`, `pdfinfo` | `200 {"status":"ready"}` або `503` з причиною |

### Structured Logs

Кожен запит логує: `task_id`, `biblionumber`, `status`, `elapsed_ms`. Лог-файли в `logs/`.

Для PDF optimizer у `task.result` додаються поля `pdf_optimized`, `pdf_fallback_reason`, `pdf_original_mb`, `pdf_final_mb`, `pdf_optimization_time_ms`, `pdf_thread_wait_ms`, `pdf_disk_free_mb`.

### Ключові SLI/SLO (цільові, M8)

| Метрика | SLO |
|---|---|
| API Availability | ≥ 99% |
| P95 Integration Time без оптимізації | ≤ 60s |
| P95 Integration Time з оптимізацією | ≤ 240s |
| Optimizer fallback rate | ≤ 10% за добу |
| Error Rate | ≤ 5% |

**On-call / Incident Response:** [`docs/RUNBOOK_MAYDAY.md`](docs/RUNBOOK_MAYDAY.md)

---

## 📜 Ченджлог

> Детальний ченджлог (Context / Change / Verification / Risks / Rollback): [`CHANGELOG.md`](CHANGELOG.md), активний том [`docs/changelogs/CHANGELOG_2026_VOL_03.md`](docs/changelogs/CHANGELOG_2026_VOL_03.md)

### v0.4.0-m8 — 2026-05-17 (PDF optimizer)
- ✅ Ізольований `kdv-optimizer`: Flask mini-API, Ghostscript, ProcessPoolExecutor(1), TTL cleanup
- ✅ `kdv-api` інтегрує optimizer перед DSpace upload і завжди fallback-ить на original PDF
- ✅ Compose/Swarm: shared volume `kdv_optimize_data`, local image build, verification обох сервісів
- ✅ Koha UI/API/Robot: `skip_optimization` controls
- ✅ Runbook: [`docs/RUNBOOK_PDF_OPTIMIZER.md`](docs/RUNBOOK_PDF_OPTIMIZER.md)

### v0.3.0 — 2026-03-05 (M5–M7)
- ✅ M5: Health/readiness probes, structured logs, RUNBOOK_MAYDAY + RUNBOOK_TESTING
- ✅ M6: Contract-тести Koha CGI + DSpace API (22 тести разом)
- ✅ M7: Release plan staging→prod, canary flow, rollback, batch parallelism/rate-limit controls

### v0.2.1 — 2026-02 (M3–M4)
- ✅ M3: GitHub Actions CI/CD: lint + pytest + pip-audit + trivy
- ✅ M4: Cloudflare Access JWT (legacy/dual/cf-only modes), strict CORS allowlist

### v0.1.0 — 2026-01 (M1–M2)
- ✅ M1: State machine, ідемпотентність, fork-join паралелізм
- ✅ M2: SOLID refactoring, Dependency Injection, thin-wrapper clients, тестова база

---

## 📚 Документація

| Документ | Призначення |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Детальна архітектура, security, workflow, рішення |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Milestones M0–M8 з DoD та статусом |
| [docs/RELEASE.md](docs/RELEASE.md) | Release gate, canary flow, rollback |
| [docs/RUNBOOK_TESTING.md](docs/RUNBOOK_TESTING.md) | Тестування: моки, DI, порядок запуску |
| [docs/RUNBOOK_MAYDAY.md](docs/RUNBOOK_MAYDAY.md) | Incident response (production) |
| [docs/RUNBOOK_ROBOT.md](docs/RUNBOOK_ROBOT.md) | Масова архівація: robot.py |
| [docs/RUNBOOK_PDF_OPTIMIZER.md](docs/RUNBOOK_PDF_OPTIMIZER.md) | PDF optimizer: конфігурація, використання, health/readiness, rollback |
| [docs/RUNBOOK_NIGHTWALKER.md](docs/RUNBOOK_NIGHTWALKER.md) | Аудит каталогу: nightwalker.py |
