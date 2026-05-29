# Runbook: Koha Export Module (`export_module`)

Мета: описати повне операторське використання, конфігурацію, troubleshooting і відновлення модуля `src/export_module` — CLI/batch-підсистеми для експорту бібліографічних записів Koha у XLSX з архівацією на Google Drive та email-розсилкою через Microsoft Graph API.

**Пов'язані документи**
- PRD: [docs/koha-export/PRD_Koha_Export_Module.md](koha-export/PRD_Koha_Export_Module.md)
- Roadmap: [docs/koha-export/ROADMAP_Koha_Export_Module.md](koha-export/ROADMAP_Koha_Export_Module.md)
- Codex Context: [docs/koha-export/CODEX_CONTEXT_Koha_Export_Module.md](koha-export/CODEX_CONTEXT_Koha_Export_Module.md)
- Incident Response: [docs/RUNBOOK_MAYDAY.md](RUNBOOK_MAYDAY.md)
- Testing: [docs/RUNBOOK_TESTING.md](RUNBOOK_TESTING.md)

---

## Зміст

1. [Архітектура одним поглядом](#1-архітектура-одним-поглядом)
2. [Інструкція для оператора (починаємо тут)](#2-інструкція-для-оператора-починаємо-тут)
   - 2.1 [Передумови і перевірка оточення](#21-передумови-і-перевірка-оточення)
   - 2.2 [CLI-команди — повний довідник](#22-cli-команди--повний-довідник)
   - 2.3 [Типові сценарії запуску](#23-типові-сценарії-запуску)
   - 2.4 [Exit codes — що означають](#24-exit-codes--що-означають)
3. [Конфігурування](#3-конфігурування)
   - 3.1 [Змінні середовища](#31-змінні-середовища)
   - 3.2 [Словник itemtypes і ccodes: export_dictionaries.yaml](#32-словник-itemtypes-і-ccodes-export_dictionariesyaml)
   - 3.3 [Маппінг MARC → XLSX: marc_mapping.yaml](#33-маппінг-marc--xlsx-marc_mappingyaml)
   - 3.4 [Порядок завантаження конфігурації](#34-порядок-завантаження-конфігурації)
4. [Staged-idempotency: як працює захист від дублювань](#4-staged-idempotency-як-працює-захист-від-дублювань)
5. [Кастомний експорт: конкретні biblionumber і діапазони](#5-кастомний-експорт-конкретні-biblionumber-і-діапазони)
6. [Troubleshooting](#6-troubleshooting)
   - 6.1 [Stuck `pending` після SIGKILL/OOM](#61-stuck-pending-після-sigkilloом)
   - 6.2 [Recovery зі стану `gdrive_uploaded`](#62-recovery-зі-стану-gdrive_uploaded)
   - 6.3 [Recovery зі стану `email_sent`](#63-recovery-зі-стану-email_sent)
   - 6.4 [/mnt/drive не змонтований або read-only](#64-mntdrive-не-змонтований-або-read-only)
   - 6.5 [.part файл залишився після copy failure](#65-part-файл-залишився-після-copy-failure)
   - 6.6 [MS Graph auth failure](#66-ms-graph-auth-failure)
   - 6.7 [MS Graph 429 / throttling](#67-ms-graph-429--throttling)
   - 6.8 [XLSX > 15 MB — link-only email](#68-xlsx--15-mb--link-only-email)
   - 6.9 [Примусовий re-export конкретного biblionumber](#69-примусовий-re-export-конкретного-biblionumber)
7. [Smoke-тести перед запуском в production](#7-smoke-тести-перед-запуском-в-production)
   - 7.1 [Dry-run smoke](#71-dry-run-smoke)
   - 7.2 [Range smoke](#72-range-smoke)
8. [Синхронізація Authorized values після змін у Koha](#8-синхронізація-authorized-values-після-змін-у-koha)
9. [Logs — що читати і де шукати](#9-logs--що-читати-і-де-шукати)
10. [Prometheus-метрики](#10-prometheus-метрики)
11. [Rollback і відновлення після критичного збою](#11-rollback-і-відновлення-після-критичного-збою)
12. [Чеклист готовності до production](#12-чеклист-готовності-до-production)

---

## 1. Архітектура одним поглядом

```text
Koha REST API
     │
     │  keyset pagination (biblionumber > last_seen_id)
     ▼
KohaApiClient ──► filter_exportable_biblios()
                         │
                         │  виключає completed biblionumbers
                         │  включає retry-eligible (failed, retry_count < MAX_RETRIES)
                         ▼
                   MARCParser  ──► mapping: config/marc_mapping.yaml
                         │         dictionaries: config/export_dictionaries.yaml
                         │
                         ▼
                   XLSXGenerator ──► /tmp/export_Koha_YYYY-MM-DD_HHMMSS_{run_id[:8]}.xlsx
                         │
                         ▼
              ┌─────────────────────────┐
              │  SQLite staged states   │
              │  pending →              │
              │  xlsx_generated →       │
              │  gdrive_uploaded →      │
              │  email_sent →           │
              │  completed              │
              └─────────────────────────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
   ExportDriveMountService    GraphEmailService
   /mnt/drive/KohaExports/    MS Graph sendMail
   {year}/export_*.xlsx       GRAPH_TO=...
```

**Ключові обмеження архітектури:**
- Модуль є **CLI/batch**, не Flask endpoint. Не викликайте його через HTTP.
- Google Drive доступний через rclone volume `/mnt/drive`. Google API/service account для copy **не потрібен**.
- Email тільки через **Microsoft Graph API**. SMTP не використовується.
- Dry-run вмикається **тільки** прапорцем `--dry-run`. Змінної `EXPORT_DRY_RUN` не існує.
- Модуль **не змінює** Koha → DSpace pipeline (`src/core.py`).

---

## 2. Інструкція для оператора (починаємо тут)

### 2.1 Передумови і перевірка оточення

Перед першим запуском переконайтеся:

**1. Перевірити, чи `/mnt/drive` змонтовано та доступно для запису:**

```bash
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" ls -la /mnt/drive/
# Очікується: каталог KohaExports або порожній /mnt/drive
docker exec "$KDV_API_CID" touch /mnt/drive/.write_check && \
  docker exec "$KDV_API_CID" rm /mnt/drive/.write_check && \
  echo "MOUNT OK"
```

**2. Перевірити health-check модуля:**

```bash
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --health-check
```

Модуль виконає перевірку:
- `EXPORT_MODULE_ENABLED=true` → якщо `false`, вийде з `exit code 0` без дій.
- `EXPORT_GDRIVE_ROOT_PATH` знаходиться всередині `/mnt/drive`.
- Файл `EXPORT_DB_PATH` доступний або може бути створений.
- Конфіги `marc_mapping.yaml` і `export_dictionaries.yaml` валідні.

**3. Перевірити змінні середовища:**

```bash
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
printf "%s\n" \
  "EXPORT_MODULE_ENABLED=${EXPORT_MODULE_ENABLED:-}" \
  "EXPORT_GDRIVE_ROOT_PATH=${EXPORT_GDRIVE_ROOT_PATH:-}" \
  "EXPORT_DB_PATH=${EXPORT_DB_PATH:-}" \
  "MAX_RETRIES=${MAX_RETRIES:-}" \
  "MAX_ATTACHMENT_BYTES=${MAX_ATTACHMENT_BYTES:-}" \
  "GRAPH_TENANT_ID=${GRAPH_TENANT_ID:+SET}" \
  "GRAPH_CLIENT_ID=${GRAPH_CLIENT_ID:+SET}" \
  "GRAPH_CLIENT_SECRET=${GRAPH_CLIENT_SECRET:+SET}" \
  "GRAPH_SENDER_USER_ID=${GRAPH_SENDER_USER_ID:+SET}" \
  "GRAPH_TO=${GRAPH_TO:+SET}"
'
```

> Значення `GRAPH_CLIENT_SECRET` та інших секретів **не мають відображатися у логах** — вони логуються як `***REDACTED***`.

---

### 2.2 CLI-команди — повний довідник

```bash
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"

# Перевірка готовності (без запуску export)
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --health-check

# Пробний запуск: XLSX генерується, але нічого не записується/не надсилається
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --dry-run

# Звичайний запуск: повний export всіх нових/retry записів
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module

# Кастомний діапазон: export тільки biblionumber 1000..1250
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --biblionumber-from 1000 --biblionumber-to 1250

# Тільки від biblionumber 500 до кінця каталогу
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --biblionumber-from 500

# Тільки від початку до biblionumber 200
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --biblionumber-to 200

# Dry-run з діапазоном (комбінація)
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --dry-run --biblionumber-from 1000 --biblionumber-to 1250

# Скинути stuck pending записи для конкретного run_id
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --reset-pending <RUN_ID>
```

> **Де знайти `<RUN_ID>`:** дивіться поле `run_id` у JSON-логах або у SQLite:
> ```bash
> sqlite3 /data/kdv_optimize/export/export_state.db \
>   "SELECT DISTINCT run_id, status, COUNT(*) FROM exported_records GROUP BY run_id, status;"
> ```

---

### 2.3 Типові сценарії запуску

#### Сценарій A: Заплановий щоденний запуск (cron/Swarm)

```bash
# Запуск у production через Swarm task-контейнер
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module
echo "Exit code: $?"
```

Що відбувається:
1. Завантажуються конфіги і env.
2. Koha API обходиться keyset pagination; відбираються нові + retry-eligible записи.
3. MARC-записи парсяться у плоский dict (MARC поля + static columns + Authorized values).
4. Генерується XLSX у `/tmp`.
5. XLSX копіюється у `/mnt/drive/KohaExports/{year}/` через `.part` → atomic rename.
6. Надсилається email через MS Graph (`GRAPH_TO`).
7. SQLite → `completed`.
8. `/tmp` XLSX видаляється у `finally`.

#### Сценарій B: Smoke перед production

```bash
# 1. Dry-run — перевірити, що XLSX генерується без помилок
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --dry-run
# Знайти згенерований файл у /tmp/dry_run/
docker exec "$KDV_API_CID" ls -lh /tmp/dry_run/

# 2. Range smoke — переконатися, що pipeline працює на малій вибірці
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --dry-run --biblionumber-from 1 --biblionumber-to 10
```

#### Сценарій C: Ручний одноразовий export партії

```bash
# Потрібно передати конкретну партію biblionumber 5000..5100 для іншої бібліотеки
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --biblionumber-from 5000 --biblionumber-to 5100
```

---

### 2.4 Exit codes — що означають

| Code | Значення | Дія оператора |
|------|----------|---------------|
| `0` | Успіх (включно з "0 нових записів") | Нічого |
| `1` | Partial failure: деякі записи не оброблені | Перевірити логи, знайти `failed` записи в SQLite |
| `2` | Total failure: жоден запис не оброблений | Перевірити логи на помилку конфігурації або підключення |

```bash
# Автоматична реакція на помилку у скрипті:
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module
EXIT=$?
if [ $EXIT -ne 0 ]; then
  echo "Export failed with code $EXIT — check logs"
fi
```

---

## 3. Конфігурування

### 3.1 Змінні середовища

Усі змінні зберігаються у зашифрованих `env.dev.enc` / `env.prod.enc` (SOPS + age).

> Щоб розшифрувати для перегляду (тільки в dev):
> ```bash
> sops --decrypt env.dev.enc
> ```

**Повний перелік змінних export-модуля:**

```bash
# ── Статус ──────────────────────────────────────────────────────────────────
EXPORT_MODULE_ENABLED=true          # false = модуль вимкнено, exit 0 без дій

# ── Стан: SQLite ─────────────────────────────────────────────────────────────
EXPORT_DB_PATH=/data/kdv_optimize/export/export_state.db
#   Файл має знаходитись на shared volume, доступному з контейнера.
#   Каталог створюється автоматично, якщо не існує.

# ── Google Drive mount ────────────────────────────────────────────────────────
EXPORT_GDRIVE_ROOT_PATH=/mnt/drive/KohaExports
#   ОБОВ'ЯЗКОВО: шлях має бути всередині /mnt/drive
#   Підкаталоги за роком (2026/, 2027/...) створюються автоматично.

# ── Microsoft Graph Email ─────────────────────────────────────────────────────
GRAPH_TENANT_ID=00000000-0000-0000-0000-000000000000   # Azure AD Tenant ID
GRAPH_CLIENT_ID=00000000-0000-0000-0000-000000000000   # App (client) ID
GRAPH_CLIENT_SECRET=REDACTED                            # Client secret (зберігати в SOPS)
GRAPH_SENDER_USER_ID=reports@example.org                # Mailbox, від якого надсилається
GRAPH_TO=library-target@otherdomain.com                 # Отримувач звіту

# ── Ліміти ────────────────────────────────────────────────────────────────────
MAX_RETRIES=3                        # Кількість повторів для failed записів
MAX_ATTACHMENT_BYTES=15728640        # 15 MB. Якщо XLSX більший — email без вкладення

# ── Метрики (опційно) ─────────────────────────────────────────────────────────
PUSHGATEWAY_URL=http://pushgateway:9091
#   Не задано → метрики не пушаться, модуль працює нормально.
```

**Що не можна робити:**
- ❌ `EXPORT_DRY_RUN=true` — ця змінна не існує і не читається. Dry-run тільки через `--dry-run`.
- ❌ `GDRIVE_SERVICE_ACCOUNT_FILE` для export-модуля — Google API не потрібний.
- ❌ `SMTP_HOST`, `SMTP_PASSWORD` — SMTP не використовується.
- ❌ `EXPORT_BIBLIONUMBER_FROM/TO` — range тільки через CLI flags.

---

### 3.2 Словник itemtypes і ccodes: `export_dictionaries.yaml`

Файл: `config/export_dictionaries.yaml`

Цей файл визначає **перекодування Koha Authorized values** у зрозумілі людині рядки для XLSX. Python-код не знає про конкретні значення; всі коди → мітки описані тут.

**Поточний стан:**

```yaml
version: 1
authorized_values:
  itemtypes:
    BOOK: "Книга"
    BK: "Книга"
    CR: "Періодика"
    VM: "Відеоматеріал"

  ccodes:
    FICTION: "Художня література"
    SCIENCE: "Наукова література"

unknown_policy:
  authorized_value: "keep_code"
  # "keep_code"  — якщо код не знайдений у словнику, залишити оригінальний код у XLSX
  # "empty"      — залишити порожній рядок
  # "error"      — підняти виключення (для строгого контролю)
```

**Як додати новий тип документа:**

1. У Koha Admin перевірити код нового itemtype (наприклад, `AV` для аудіовізуального матеріалу).
2. Відкрити `config/export_dictionaries.yaml`, додати рядок:
   ```yaml
   itemtypes:
     AV: "Аудіовізуальний матеріал"
   ```
3. Перевірити, що `marc_mapping.yaml` посилається на `dictionary: "itemtypes"` для відповідного поля.
4. Запустити тести маппінгу:
   ```bash
   pytest tests/test_export_mapping_loader.py -q
   ```
5. Запустити dry-run smoke для перевірки:
   ```bash
   KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
   docker exec "$KDV_API_CID" sh -lc '
   set -a
   . /run/secrets/app_env_payload
   set +a
   exec "$@"
   ' -- python -m src.export_module --dry-run
   ```

> **Важливо:** `unknown_policy: keep_code` означає, що невідомий код потрапить у XLSX як є (наприклад, `AV`). Якщо це небажано для цільової бібліотеки — змініть на `empty` або додайте код у словник.

---

### 3.3 Маппінг MARC → XLSX: `marc_mapping.yaml`

Файл: `config/marc_mapping.yaml`

Визначає **які MARC-поля** потрапляють у XLSX, в якому порядку і з якими перетвореннями.

**Поточна структура:**

```yaml
version: 1

columns:
  - name: "ID Запису"           # Назва колонки в XLSX (заголовок)
    sources:
      - field: "001"            # MARC тег

  - name: "Назва книги"
    sources:
      - field: "245"
        subfields: ["a", "b"]  # Об'єднуємо кілька субполів
        join: " "               # Роздільник при об'єднанні
        strip_chars: " /:"      # Символи, які обрізаємо з країв

  - name: "Тип документа"
    sources:
      - field: "942"
        subfields: ["c"]
        transform: "authorized_value"   # Застосувати перекодування
        dictionary: "itemtypes"         # Ключ зі словника export_dictionaries.yaml

static_columns:
  # Колонки без MARC-джерела — фіксовані значення для кожного рядка XLSX
  - name: "Бібліотека-отримувач"
    value: "REDACTED_LIBRARY_NAME"     # Замінити реальним значенням в env/secret
    reason: "Потрібно для імпорту в іншу бібліотеку"

  - name: "Статус імпорту"
    value: "Новий"
    reason: "Фіксоване значення для downstream import"

required_columns:
  # Якщо жоден з required_columns відсутній у mapping — запуск завершиться з помилкою
  - "ID Запису"
  - "Назва книги"
  - "Тип документа"
  - "Бібліотека-отримувач"
```

**Як додати нову колонку MARC:**

1. Визначити тег і субполя у форматі MARC21 (наприклад, `100$a` для автора).
2. Додати до `columns` у `marc_mapping.yaml`:
   ```yaml
   - name: "Автор"
     sources:
       - field: "100"
         subfields: ["a"]
       - field: "110"              # Fallback: корпоративний автор
         subfields: ["a"]
   ```
3. За потреби — додати до `required_columns`.
4. Перевірити валідацію:
   ```bash
   pytest tests/test_export_mapping_loader.py -q
   ```

**Як додати static column:**

```yaml
static_columns:
  - name: "Фонд"
    value: "КДВ-2026"
    reason: "Ідентифікатор фонду для downstream системи"
```

**Доступні transforms:**

| Transform | Опис |
|-----------|------|
| `authorized_value` | Перекодувати через словник з `export_dictionaries.yaml` |
| `extract_year_regex` | Витягти 4-значний рік регулярним виразом |
| (без transform) | Взяти значення субполя як є |

> **Обмеження:** після зміни `marc_mapping.yaml` новий образ не потрібен, якщо файл bind-mounted. Але якщо конфіг упакований в image — потрібен rebuild.

---

### 3.4 Порядок завантаження конфігурації

```text
1. ORCHESTRATOR_ENV_FILE (якщо задано) → явний шлях до env-файлу
2. SERVER_ENV=dev  → шукає env.dev або env.dev.enc (SOPS decrypt)
   SERVER_ENV=prod → шукає env.prod або env.prod.enc (SOPS decrypt)
3. Fallback: .env у корені проєкту (без override вже встановлених env)
4. Вже встановлені змінні середовища (docker-compose, Swarm secret) мають пріоритет
```

Перевірка завантаження:

```bash
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -c "
from src.export_module.config import ExportConfig
cfg = ExportConfig.from_env()
cfg.validate()
print('Config OK:', cfg.export_gdrive_root_path)
"
```

---

## 4. Staged-idempotency: як працює захист від дублювань

Модуль використовує **staged-idempotency** — підхід, при якому кожен зовнішній side effect (copy на диск, надсилання email) фіксується у SQLite окремим статусом. Це дозволяє безпечно відновлювати pipeline після будь-якого збою.

**Стани запису:**

```
pending          → запис відібраний, починаємо обробку
xlsx_generated   → XLSX файл готовий у /tmp
gdrive_uploaded  → файл скопійовано у /mnt/drive (незворотно зовні)
email_sent       → email надіслано через MS Graph (незворотно зовні)
completed        → все підтверджено, запис не буде оброблятися повторно
failed           → помилка, retry_count збільшено; буде повторено при наступному запуску
```

**Правила відновлення:**

| Стан при збої | Що відбувається при наступному запуску |
|---|---|
| `pending` | Повторна повна обробка (XLSX → copy → email) |
| `xlsx_generated` | Повторна повна обробка |
| `gdrive_uploaded` | **Reuse** існуючого файлу на Google Drive; тільки email |
| `email_sent` | **Тільки** `mark_completed`; email повторно **не надсилається** |
| `completed` | Пропускається назавжди (partial unique index у SQLite) |
| `failed` + `retry_count < MAX_RETRIES` | Повторна повна обробка |
| `failed` + `retry_count >= MAX_RETRIES` | Пропускається; потрібне ручне втручання |

**Гарантії:**
- Один `biblionumber` не може мати двох `completed` записів (UNIQUE INDEX у SQLite).
- `completed` запис не буде видалено або перетворено автоматично.
- Для примусового re-export потрібне явне ручне видалення з SQLite (div. [розділ 6.9](#69-примусовий-re-export-конкретного-biblionumber)).

---

## 5. Кастомний експорт: конкретні biblionumber і діапазони

### Базовий range export

```bash
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"

# Inclusive діапазон: від 1000 до 1250 включно
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --biblionumber-from 1000 --biblionumber-to 1250

# Від 500 до кінця каталогу
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --biblionumber-from 500

# Від початку до 200 включно
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --biblionumber-to 200
```

### Правила range

- `--biblionumber-from` і `--biblionumber-to` — тільки позитивні integer.
- Якщо `from > to` — CLI завершується з `exit code 2` (validation error).
- Range **не обходить** staged-idempotency: `completed` записи все одно виключаються.
- Range **не обходить** retry-логіку: `failed` з `retry_count < MAX_RETRIES` включаються.

### Dry-run з range для smoke-тесту

```bash
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"

# Перевірити 10 конкретних записів без реальних side effects
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --dry-run --biblionumber-from 1000 --biblionumber-to 1010
# XLSX збережеться у /tmp/dry_run/ для ручного огляду
docker exec "$KDV_API_CID" ls -lh /tmp/dry_run/
```

### Export конкретного одного запису

```bash
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"

# Один biblionumber = from == to
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --biblionumber-from 12345 --biblionumber-to 12345
```

> **Примітка:** якщо запис вже `completed` у SQLite, він буде **пропущений** навіть при range export. Для примусового re-export — дивіться [розділ 6.9](#69-примусовий-re-export-конкретного-biblionumber).

---

## 6. Troubleshooting

### 6.1 Stuck `pending` після SIGKILL/OOM

**Симптом:** після OOM-kill або `docker kill` у SQLite лишилися записи зі статусом `pending`, які більше не обробляються автоматично.

**Діагностика:**

```bash
sqlite3 /data/kdv_optimize/export/export_state.db \
  "SELECT run_id, COUNT(*), MIN(last_attempt_at) FROM exported_records
   WHERE status='pending' GROUP BY run_id ORDER BY MIN(last_attempt_at);"
```

**Рішення — скинути через CLI:**

```bash
# Перевести конкретний run_id зі stuck pending у failed (активує retry)
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --reset-pending <RUN_ID>
```

`--reset-pending` виконує `reset_stuck_pending()`: переводить `pending` записи у `failed` з причиною `reset_stuck_pending`. При наступному запуску вони стануть retry-кандидатами.

**Якщо потрібно скинути всі старі pending (аварійний варіант):**

```bash
sqlite3 /data/kdv_optimize/export/export_state.db \
  "UPDATE exported_records SET status='failed', failed_reason='manual_reset_stuck_pending'
   WHERE status='pending';"
```

> Після ручного UPDATE запустіть модуль знову: він підхопить ці записи як retry-eligible.

---

### 6.2 Recovery зі стану `gdrive_uploaded`

**Симптом:** процес завершився після copy у `/mnt/drive`, але до надсилання email (наприклад, `SIGTERM` між `mark_gdrive_uploaded` і `send_via_graph`).

**Поведінка модуля:** при наступному запуску для цього `run_id` файл на Google Drive буде **перевикористано** (пошук за `run_id` в імені файлу), copy повторно **не виконується**, перейде одразу до MS Graph email.

**Перевірити стан:**

```bash
sqlite3 /data/kdv_optimize/export/export_state.db \
  "SELECT run_id, status, gdrive_file_path FROM exported_records
   WHERE status='gdrive_uploaded' LIMIT 10;"
```

**Дія:** просто запустити модуль знову. Він автоматично підхопить recoverable runs:

```bash
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module
```

---

### 6.3 Recovery зі стану `email_sent`

**Симптом:** MS Graph підтвердив відправку, але процес впав до `mark_completed` — запис залишається в `email_sent`.

**Поведінка модуля:** при наступному запуску email **повторно надісланий не буде**. Модуль лише виконає `mark_completed`.

**Дія:** запустити модуль знову — відновлення відбудеться автоматично:

```bash
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module
```

---

### 6.4 `/mnt/drive` не змонтований або read-only

**Симптом:** помилки типу `OSError: [Errno 30] Read-only file system` або `No such file or directory: /mnt/drive`.

**Діагностика:**

```bash
# Перевірити монтування
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" df -h /mnt/drive

# Перевірити права запису
docker exec "$KDV_API_CID" touch /mnt/drive/.test_write && \
  docker exec "$KDV_API_CID" rm /mnt/drive/.test_write && echo "RW OK"

# Перевірити стан rclone
docker exec "$KDV_API_CID" rclone lsd /mnt/drive/ 2>&1 | head -20
```

**Рішення:**

1. Перевірити, чи запущений rclone mount service (якщо окремий контейнер/process).
2. Перевірити `RCLONE_REMOTE_NAME` у env — має збігатися з реальним remote name.
3. Якщо `EXPORT_GDRIVE_ROOT_PATH` вказує на неіснуючий каталог всередині mount:
   ```bash
   KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
   docker exec "$KDV_API_CID" mkdir -p /mnt/drive/KohaExports
   ```
4. Перезапустити Swarm service task після виправлення mount:
   ```bash
   docker service update --force kdv_integrator_event_kdv-api
   ```

---

### 6.5 `.part` файл залишився після copy failure

**Симптом:** у `/mnt/drive/KohaExports/{year}/` є файли з суфіксом `.part`.

**Причина:** процес впав між `copy → .part` і `os.replace(.part → .xlsx)`. Модуль автоматично видаляє `.part` при помилці, але якщо контейнер вбито жорстко (SIGKILL) — `.part` може залишитися.

**Перевірити:**

```bash
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" find /mnt/drive/KohaExports -name "*.part" -ls
```

**Очистити вручну:**

```bash
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" find /mnt/drive/KohaExports -name "*.part" -delete
echo "Cleaned .part files"
```

Після очищення повторний запуск модуля виконає copy заново.

---

### 6.6 MS Graph auth failure

**Симптоми:** помилки `401 Unauthorized`, `403 Forbidden`, `AADSTS700016`, `AADSTS65001`.

**Чеклист діагностики:**

| Перевірка | Команда / Дія |
|-----------|---------------|
| `GRAPH_TENANT_ID` правильний | Порівняти з Azure AD → Overview → Tenant ID |
| `GRAPH_CLIENT_ID` правильний | Azure AD → App registrations → Application (client) ID |
| `GRAPH_CLIENT_SECRET` не прострочений | Azure AD → App registrations → Certificates & secrets → перевірити дату закінчення |
| App має permission `Mail.Send` | Azure AD → App registrations → API permissions → Microsoft Graph → `Mail.Send` → **Admin consent granted** |
| `GRAPH_SENDER_USER_ID` — реальна mailbox | Exchange Admin → Recipients → Mailboxes |
| Exchange Application Access Policy не блокує | Exchange Admin PowerShell: `Get-ApplicationAccessPolicy` |

**Оновити прострочений secret:**

1. Azure Portal → App registrations → вибрати app → Certificates & secrets → New client secret.
2. Скопіювати нове значення.
3. Оновити у SOPS-зашифрованому `env.prod.enc`:
   ```bash
   sops env.prod.enc
   # Знайти GRAPH_CLIENT_SECRET, замінити значення, зберегти
   ```
4. Перезапустити Swarm service task:
   ```bash
   docker service update --force kdv_integrator_event_kdv-api
   ```

---

### 6.7 MS Graph 429 / throttling

**Симптом:** у логах `graph_email_retry`, `status_code=429`, `Retry-After: N`.

**Поведінка модуля:** автоматично повторює запит з `tenacity` exponential backoff (min 30s, max 120s, 3 спроби). Якщо всі спроби вичерпано — запис переходить у `failed`.

**Якщо 429 повторюється систематично:**

1. Перевірити частоту запусків scheduler/cron: MS Graph має ліміт ~10,000 requests/10min.
2. Зменшити частоту planned запусків export-модуля.
3. Перевірити, чи не запущено кілька одночасних інстансів:
   ```bash
   KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
   docker exec "$KDV_API_CID" ps aux | grep "export_module"
   ```
4. Звернутися до адміністратора Azure tenant для підвищення ліміту, якщо навантаження об'єктивно велике.

---

### 6.8 XLSX > 15 MB — link-only email

**Поведінка:** якщо згенерований XLSX перевищує `MAX_ATTACHMENT_BYTES` (15 MB за замовчуванням), модуль надсилає email **без вкладення** — тільки з посиланням на шлях у Google Drive mount та попередженням у тілі листа.

**Перевірити поточний ліміт:**

```bash
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
echo "MAX_ATTACHMENT_BYTES=${MAX_ATTACHMENT_BYTES:-}"
'
```

**Як отримати файл при link-only email:**

Отримувач має доступ до Google Drive через web-інтерфейс або desktop app. Шлях до файлу вказаний в тілі email у вигляді `gdrive_folder_path`.

**Збільшити ліміт (якщо потрібно):**

У `env.prod.enc` змінити:
```bash
MAX_ATTACHMENT_BYTES=31457280  # 30 MB
```

> Зверніть увагу на ліміти MS Graph для вкладень: стандартний ліміт через `sendMail` — 4 MB; через upload session (не реалізовано) — до 150 MB. Поточний ліміт у 15 MB є консервативним для `sendMail`.

---

### 6.9 Примусовий re-export конкретного biblionumber

**Ситуація:** запис `biblionumber=12345` вже `completed`, але потрібно його перезапустити (наприклад, MARC-дані змінились, попередній XLSX виявився некоректним).

**Кроки:**

```bash
# 1. Переконатися, що запис дійсно completed
sqlite3 /data/kdv_optimize/export/export_state.db \
  "SELECT biblionumber, run_id, status, exported_at FROM exported_records
   WHERE biblionumber=12345;"

# 2. Видалити completed запис для цього biblionumber (ручна операція!)
sqlite3 /data/kdv_optimize/export/export_state.db \
  "DELETE FROM exported_records WHERE biblionumber=12345 AND status='completed';"

# 3. Запустити range export тільки для цього biblionumber
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --biblionumber-from 12345 --biblionumber-to 12345
```

> ⚠️ **Увага:** видалення `completed` запису означає, що отримувач отримає повторний email для цього запису. Повідомте команду перед виконанням у production.

---

## 7. Smoke-тести перед запуском в production

### 7.1 Dry-run smoke

```bash
# Запустити без --biblionumber-from/to — обходить весь каталог у dry-run
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --dry-run

# Перевірити exit code
echo "Exit: $?"

# Переглянути згенерований XLSX
docker exec "$KDV_API_CID" ls -lh /tmp/dry_run/

# Перевірити логи на ключові events
docker service logs kdv_integrator_event_kdv-api --tail=100 | grep -E '"event":'
```

**Очікувані log events у dry-run:**

```json
{"event": "export_started", "dry_run": true}
{"event": "koha_fetch_done", "total_candidates": N}
{"event": "xlsx_generated", "path": "/tmp/export_Koha_...xlsx"}
{"event": "dry_run_would_copy_to_gdrive_mount", "filename": "..."}
{"event": "dry_run_would_send_graph_email", "recipient": "..."}
{"event": "dry_run_db_not_modified"}
```

**Перевірити, що SQLite не змінився:**

```bash
sqlite3 /data/kdv_optimize/export/export_state.db \
  "SELECT COUNT(*) FROM exported_records WHERE status='pending';"
# Має бути 0 після dry-run
```

---

### 7.2 Range smoke

```bash
# Взяти перші 5 biblionumber для smoke
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -m src.export_module --dry-run --biblionumber-from 1 --biblionumber-to 5

echo "Exit: $?"

# Переконатися, що XLSX містить не більше 5 рядків (+ header)
docker exec "$KDV_API_CID" sh -lc '
set -a
. /run/secrets/app_env_payload
set +a
exec "$@"
' -- python -c "
import openpyxl
wb = openpyxl.load_workbook(sorted(__import__('glob').glob('/tmp/dry_run/*.xlsx'))[-1])
ws = wb.active
print(f'Rows: {ws.max_row}, Cols: {ws.max_column}')
print([cell.value for cell in ws[1]])  # Headers
"
```

---

## 8. Синхронізація Authorized values після змін у Koha

Коли в Koha Admin змінюються Authorized values (наприклад, додається новий itemtype або перейменовується існуючий) — словник у `config/export_dictionaries.yaml` потрібно синхронізувати вручну.

**Процедура:**

1. **Отримати поточні Authorized values у Koha:**
   ```
   Koha Admin → Administration → Authorized values → ITEM_TYPES
   ```
   Або через REST API:
   ```bash
   curl -s -u "$KOHA_USER:$KOHA_PASS" \
     "$KOHA_BASE_URL/api/v1/authorised_value_categories/ITEM_TYPE/authorised_values" \
     | python3 -m json.tool | grep '"authorised_value\|description"'
   ```

2. **Оновити `config/export_dictionaries.yaml`:**
   ```yaml
   itemtypes:
     BOOK: "Книга"
     BK: "Книга"
     CR: "Періодика"
     VM: "Відеоматеріал"
     AV: "Аудіовізуальний матеріал"   # ← новий код
   ```

3. **Перевірити mapping_loader:**
   ```bash
   pytest tests/test_export_mapping_loader.py -q
   ```

4. **Dry-run для перевірки в XLSX:**
   ```bash
   KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
   docker exec "$KDV_API_CID" sh -lc '
   set -a
   . /run/secrets/app_env_payload
   set +a
   exec "$@"
   ' -- python -m src.export_module --dry-run
   # Відкрити /tmp/dry_run/*.xlsx, перевірити колонку "Тип документа"
   ```

5. **Зафіксувати зміни у changelog:**
   ```bash
   # Додати запис до docs/changelogs/CHANGELOG_2026_VOL_XX.md
   ```

> **Правило:** `export_dictionaries.yaml` і `marc_mapping.yaml` оновлюються **в одній ітерації**. Не допускайте стану, де mapping посилається на `dictionary: "newdict"`, якого немає у словнику.

---

## 9. Logs — що читати і де шукати

**Читати логи в реальному часі:**

```bash
docker service logs -f kdv_integrator_event_kdv-api | grep -v '"event": "koha_page_fetched"'
# Фільтруємо шумні pagination events
```

**Читати логи конкретного run:**

```bash
# Знайти run_id за часом
docker service logs kdv_integrator_event_kdv-api --since="2026-05-29T10:00:00" | \
  python3 -c "
import sys, json
for line in sys.stdin:
    try:
        d = json.loads(line.strip())
        if d.get('event') in ('export_started', 'export_success', 'export_failed'):
            print(json.dumps(d, ensure_ascii=False))
    except: pass
"
```

**Ключові events у JSON logs:**

| Event | Рівень | Значення |
|-------|--------|---------|
| `export_started` | INFO | Початок запуску, є `run_id` |
| `koha_fetch_done` | INFO | Отримано кандидатів, є `total_candidates` |
| `pending_reserved` | INFO | Записи зарезервовано у SQLite |
| `xlsx_generated` | INFO | Файл готовий, є `path` |
| `gdrive_copy_success` | INFO | Файл на Google Drive |
| `gdrive_copy_skipped` | INFO | Файл вже існує (retry з тим самим run_id) |
| `graph_email_sent` | INFO | Email надіслано, є `recipient`, `attachment` |
| `graph_email_retry` | WARNING | Retry після помилки MS Graph |
| `state_committed` | INFO | SQLite → `completed` |
| `export_success` | INFO | Успішне завершення |
| `export_failed` | ERROR | Критична помилка, є `error` |
| `dry_run_*` | INFO | Dry-run events (без реальних side effects) |

> Поле `run_id` є у **кожному** log-рядку — завдяки `contextvars`. Це дозволяє фільтрувати всі події одного запуску:
> ```bash
> docker service logs kdv_integrator_event_kdv-api | grep '"run_id": "a1b2c3d4'
> ```

---

## 10. Prometheus-метрики

Якщо `PUSHGATEWAY_URL` задано — метрики надсилаються після кожного запуску.

**Доступні метрики:**

| Метрика | Тип | Опис |
|---------|-----|------|
| `export_records_total` | Counter | Кількість успішно експортованих записів |
| `export_duration_seconds` | Histogram | Тривалість запуску |
| `export_errors_total{stage}` | Counter | Помилки за етапами |

**Stages для `export_errors_total`:**
`koha_fetch` · `marc_parse` · `xlsx_gen` · `gdrive_copy` · `graph_email` · `db_commit`

**Перевірити, чи метрики надсилаються:**

```bash
# Переглянути поточний стан у Pushgateway
curl -s http://pushgateway:9091/metrics | grep "export_"
```

**Якщо `PUSHGATEWAY_URL` не задано:** метрики тихо пропускаються, модуль працює нормально.

---

## 11. Rollback і відновлення після критичного збою

### Зупинити export (якщо щось пішло не так)

```bash
# Зупинити поточний запуск (якщо висить)
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" pkill -f "src.export_module"

# Або перезапустити service task
docker service update --force kdv_integrator_event_kdv-api
```

### Відкотити зміни у Google Drive

Якщо згенерований XLSX неправильний і його треба прибрати:

```bash
# Знайти файл
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "$KDV_API_CID" find /mnt/drive/KohaExports -name "*<run_id[:8]>*"

# Видалити (необоротно! підтвердіть двічі)
docker exec "$KDV_API_CID" rm /mnt/drive/KohaExports/2026/export_Koha_..._<run_id[:8]>.xlsx
```

Після видалення файлу скинути SQLite запис:

```bash
sqlite3 /data/kdv_optimize/export/export_state.db \
  "UPDATE exported_records SET status='failed', failed_reason='manual_gdrive_rollback'
   WHERE run_id='<RUN_ID>';"
```

### Відкотити конфігурацію

```bash
# Якщо marc_mapping.yaml або export_dictionaries.yaml зламали pipeline
git diff config/marc_mapping.yaml config/export_dictionaries.yaml
git checkout -- config/marc_mapping.yaml config/export_dictionaries.yaml

# Перевірити
pytest tests/test_export_mapping_loader.py -q
```

---

## 12. Чеклист готовності до production

**Інфраструктура:**

- [ ] `/mnt/drive/KohaExports` змонтовано та доступно для запису (`touch` тест проходить).
- [ ] `EXPORT_DB_PATH` доступний або каталог `export/` існує на shared volume.
- [ ] `EXPORT_MODULE_ENABLED=true` у `env.prod.enc`.

**Graph / Email:**

- [ ] `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET` задані коректно.
- [ ] App має permission `Mail.Send` у Azure AD з admin consent.
- [ ] Exchange Application Access Policy дозволяє send від `GRAPH_SENDER_USER_ID`.
- [ ] `GRAPH_SENDER_USER_ID` — реальна активна mailbox.
- [ ] `GRAPH_TO` — email отримувача звітів.

**Конфігурація модуля:**

- [ ] `config/marc_mapping.yaml` валідний (тест `test_export_mapping_loader` проходить).
- [ ] `config/export_dictionaries.yaml` містить всі itemtypes, що є у Koha.
- [ ] `required_columns` у mapping відповідають реальним вимогам downstream системи.

**Smoke-тести:**

- [ ] Swarm health-check із секції 2.2 (`--health-check`) → `exit 0`.
- [ ] Swarm dry-run із секції 2.2 (`--dry-run`) → `exit 0`, XLSX у `/tmp/dry_run/` виглядає коректно.
- [ ] Swarm range dry-run із секції 2.2 (`--dry-run --biblionumber-from 1 --biblionumber-to 10`) → `exit 0`.
- [ ] Після dry-run SQLite чистий (0 `pending`/`completed` записів).

**Перший production run:**

- [ ] Запустити з range обмеженням для пілоту:
  ```bash
  KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
  docker exec "$KDV_API_CID" sh -lc '
  set -a
  . /run/secrets/app_env_payload
  set +a
  exec "$@"
  ' -- python -m src.export_module --biblionumber-from 1 --biblionumber-to 100
  ```
- [ ] Перевірити лог на `export_success` без `export_failed`.
- [ ] Підтвердити, що файл з'явився у `/mnt/drive/KohaExports/{year}/`.
- [ ] Підтвердити, що email отримано на `GRAPH_TO`.
- [ ] SQLite: 100 записів у `completed`.
