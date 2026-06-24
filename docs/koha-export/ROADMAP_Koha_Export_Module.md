# ROADMAP: Імплементація модуля експорту Koha → XLSX
## Адаптовано для AI-агента Codex

> **Версія роадмапи:** 1.2
> **На основі PRD:** v2.3
> **Мова реалізації:** Python 3.11+
> **Загальний обсяг:** 6 фаз · 23 завдання · ~5–7 тижнів
> **Changelog v1.1:** Roadmap синхронізовано з PRD v2.2: Google Drive export через rclone-mounted `/mnt/drive`, email через Microsoft Graph API, dry-run тільки через CLI `--dry-run`, keyset pagination по `biblionumber > last_seen_id`, staged-idempotency замість спрощеного двофазного commit. У Завданні 1.1 додано статичні XLSX-колонки та словник перекодування Koha Authorized values.
> **Changelog v1.2:** Додано optional CLI range export за inclusive діапазоном `biblionumber` для KohaApiClient, orchestration/filtering, CLI і тестового чекліста.

---

## Інструкція для агента

Цей документ є покроковим планом імплементації. Кожне завдання містить:

- **Мету** — що саме треба зробити та навіщо.
- **Контракт** — очікувані сигнатури, типи, поведінка.
- **Acceptance criteria** — умови, за яких завдання вважається виконаним.
- **Заборони** — що явно не можна робити у цьому завданні.

**Загальні правила для агента:**

1. Завдання виконуються строго у порядку фаз. Фаза N не починається до завершення Фази N-1.
2. Кожне завдання завершується тестами до переходу до наступного.
3. Якщо завдання позначено `[BLOCKER]` — воно є передумовою для решти модуля.
4. Код пишеться з type hints для всіх публічних функцій та методів.
5. Усі зовнішні виклики або side effects мають mock-реалізацію для тестів: Koha API, filesystem copy у `/mnt/drive`, Microsoft Graph API.
6. `run_id: str = str(uuid4())` генерується один раз на початку `ExportOrchestrator.run()` і прокидається через весь pipeline.
7. Dry-run вмикається тільки CLI прапорцем `--dry-run`; `EXPORT_DRY_RUN` у env не використовується.
8. Optional export range задається тільки CLI-прапорцями `--biblionumber-from` / `--biblionumber-to`; env-змінні для range не вводяться.
9. Секрети не логуються і не потрапляють у репозиторій. У документації й `.env.example` дозволені тільки placeholders на кшталт `REDACTED`.

---

## Цільова структура проєкту

```text
src/
├── export_module/
│   ├── __init__.py
│   ├── __main__.py              # CLI entrypoint
│   ├── orchestrator.py          # ExportOrchestrator
│   ├── config.py                # ExportConfig + RuntimeOptions
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   ├── schema.py            # DDL + MigrationManager
│   │   └── repository.py        # ExportRepository
│   │
│   ├── koha/
│   │   ├── __init__.py
│   │   ├── client.py            # KohaApiClient з keyset pagination
│   │   └── filters.py           # filter_exportable_biblios()
│   │
│   ├── marc/
│   │   ├── __init__.py
│   │   ├── parser.py            # defensive MARC parsing
│   │   └── mapping_loader.py    # YAML + JSON Schema + dictionaries
│   │
│   ├── xlsx/
│   │   ├── __init__.py
│   │   └── generator.py         # XLSXGenerator — openpyxl
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── drive_mount_service.py   # ExportDriveMountService
│   │   └── graph_email_service.py   # GraphEmailService
│   │
│   └── observability/
│       ├── __init__.py
│       ├── logger.py            # JSON structured logger
│       └── metrics.py           # Prometheus Pushgateway, optional
│
config/
├── marc_mapping.yaml            # MARC → XLSX + static columns + transforms
└── export_dictionaries.yaml     # Authorized values та інші словники експорту

tests/
├── unit/
├── mock_services/
│   ├── test_drive_mount_service.py
│   └── test_graph_email_service.py
└── integration/
    └── test_pipeline.py
```

---

## Фаза 0 — Критичні передумови `[BLOCKER]`

> **Мета фази:** закласти state tracking, конфігурацію та repository contract для staged-idempotency.
> **Тривалість:** 3–4 дні.

### Завдання 0.1 — Схема БД для staged-idempotency `[BLOCKER]`

**Мета:**
Створити SQLite-схему, яка дозволяє безпечно відновлювати pipeline після crash між XLSX generation, copy у Google Drive mount, MS Graph sendMail і final commit.

**Файл:** `src/export_module/db/schema.py`

**Контракт — DDL:**

```python
SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS exported_records (
    biblionumber          INTEGER NOT NULL,
    run_id                TEXT    NOT NULL,
    status                TEXT    NOT NULL DEFAULT 'pending'
                          CHECK(status IN (
                              'pending',
                              'xlsx_generated',
                              'gdrive_uploaded',
                              'email_sent',
                              'completed',
                              'failed'
                          )),
    exported_at           TIMESTAMP,
    last_attempt_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    retry_count           INTEGER NOT NULL DEFAULT 0,
    failed_reason         TEXT,
    xlsx_filename         TEXT,
    gdrive_file_path      TEXT,
    gdrive_folder_path    TEXT,
    email_sent_at         TIMESTAMP,
    email_message_id      TEXT,

    PRIMARY KEY (biblionumber, run_id)
);

CREATE INDEX IF NOT EXISTS idx_status_retry
    ON exported_records(status, retry_count);

CREATE UNIQUE INDEX IF NOT EXISTS idx_biblionumber_completed
    ON exported_records(biblionumber)
    WHERE status = 'completed';
"""
```

**Acceptance criteria:**

- [ ] `MigrationManager.migrate()` ідемпотентний на порожній і вже створеній БД.
- [ ] `CHECK(status IN (...))` відхиляє некоректний статус.
- [ ] `UNIQUE INDEX idx_biblionumber_completed` не дозволяє два completed записи для одного `biblionumber`.
- [ ] Є тест `test_schema_accepts_staged_statuses`.
- [ ] Є тест `test_schema_rejects_invalid_status`.

**Заборони:**

- Не використовувати `DROP TABLE`.
- Не зберігати Graph secrets або raw OAuth tokens у SQLite.

---

### Завдання 0.2 — Реалізувати ExportRepository `[BLOCKER]`

**Мета:**
Інкапсулювати всі операції зі state DB в одному класі. Pipeline не звертається до SQLite напряму.

**Файл:** `src/export_module/db/repository.py`

**Контракт:**

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class ExportRecord:
    biblionumber: int
    run_id: str
    status: str
    retry_count: int
    failed_reason: Optional[str] = None
    xlsx_filename: Optional[str] = None
    gdrive_file_path: Optional[str] = None
    gdrive_folder_path: Optional[str] = None
    email_sent_at: Optional[str] = None
    email_message_id: Optional[str] = None

class ExportRepository:
    def __init__(self, db_path: str) -> None: ...

    def get_completed_biblionumbers(self) -> set[int]: ...

    def get_retry_eligible(self, max_retries: int) -> list[ExportRecord]: ...

    def get_recoverable_runs(self) -> list[ExportRecord]:
        """Повертає записи у проміжних станах: xlsx_generated, gdrive_uploaded, email_sent."""

    def insert_pending(self, biblionumbers: list[int], run_id: str) -> None: ...

    def mark_xlsx_generated(self, run_id: str, xlsx_filename: str) -> None: ...

    def mark_gdrive_uploaded(self, run_id: str, file_path: str, folder_path: str) -> None: ...

    def mark_email_sent(self, run_id: str, message_id: str | None = None) -> None: ...

    def mark_completed(self, run_id: str) -> None:
        """Оновлює тільки записи WHERE run_id=:run_id AND status='email_sent'."""

    def mark_failed(self, run_id: str, reason: str) -> None: ...

    def reset_stuck_pending(self, run_id: str) -> int: ...
```

**Acceptance criteria:**

- [ ] `insert_pending()` ідемпотентний для того самого `run_id`.
- [ ] `mark_gdrive_uploaded()` не зачіпає інші `run_id`.
- [ ] `mark_completed()` працює тільки зі статусу `email_sent`.
- [ ] `mark_failed()` збільшує `retry_count`.
- [ ] `get_recoverable_runs()` повертає проміжні стани для runbook/recovery.

---

### Завдання 0.3 — ExportConfig та RuntimeOptions `[BLOCKER]`

**Мета:**
Централізовано завантажити env-конфіг і відокремити runtime flags від env. Dry-run приходить тільки з CLI.

**Файл:** `src/export_module/config.py`

**Контракт:**

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class ExportConfig:
    enabled: bool

    koha_base_url: str
    koha_api_user: str
    koha_api_password: str
    koha_page_size: int = 100

    export_gdrive_root_path: str = "/mnt/drive/KohaExports"

    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    graph_sender_user_id: str = ""
    graph_to: str = ""

    max_retries: int = 3
    max_attachment_bytes: int = 15 * 1024 * 1024

    db_path: str = "/data/kdv_export_state/export_state.db"
    marc_mapping_path: str = "config/marc_mapping.yaml"
    export_dictionaries_path: str = "config/export_dictionaries.yaml"
    pushgateway_url: Optional[str] = None

    @classmethod
    def from_env(cls) -> "ExportConfig": ...

    def validate(self) -> None:
        """
        Перевіряє обов'язкові env, існування mapping/dictionary файлів,
        доступність db directory, а також що export_gdrive_root_path всередині /mnt/drive
        і каталог існує або може бути створений.
        """

@dataclass
class RuntimeOptions:
    dry_run: bool = False
```

**Acceptance criteria:**

- [ ] `EXPORT_DRY_RUN` не читається і не впливає на поведінку.
- [ ] Відсутній `GRAPH_CLIENT_SECRET` дає `ConfigValidationError` без друку значення інших secret.
- [ ] `EXPORT_GDRIVE_ROOT_PATH` поза `/mnt/drive` відхиляється.
- [ ] `--dry-run` встановлює `RuntimeOptions(dry_run=True)` у CLI-тесті.

**Заборони:**

- Не додавати `GDRIVE_SERVICE_ACCOUNT_FILE` у export config.
- Не додавати SMTP env (`SMTP_HOST`, `SMTP_PASSWORD`, тощо).
- Не логувати `GRAPH_CLIENT_SECRET`.

---

## Фаза 1 — Фундамент: маппінг, словники, логування

> **Мета фази:** підготувати декларативний mapping contract, включно зі статичними XLSX-колонками та перекодуванням Koha Authorized values.
> **Тривалість:** 2–3 дні.

### Завдання 1.1 — `marc_mapping.yaml`, static columns та Authorized values dictionaries `[BLOCKER]`

**Мета:**
Описати всі XLSX-колонки декларативно. Частина колонок мапиться з MARC, частина є статичними/spec columns для імпорту в іншу бібліотеку і поки не має MARC-джерела. Також потрібно мати окремий словничок перекодування Koha Authorized values: коди зі спадних списків Koha мають експортуватися у XLSX людськими словами кирилицею, наприклад `BOOK -> Книга`.

**Файли:**

- `config/marc_mapping.yaml`
- `config/export_dictionaries.yaml`
- `src/export_module/marc/mapping_loader.py`

**Контракт — `config/marc_mapping.yaml`:**

```yaml
version: 1
columns:
  - name: "ID Запису"
    sources:
      - field: "001"

  - name: "Назва книги"
    sources:
      - field: "245"
        subfields: ["a", "b"]
        join: " "
        strip_chars: " /:"

  - name: "Тип документа"
    sources:
      - field: "942"
        subfields: ["c"]
        transform: "authorized_value"
        dictionary: "itemtypes"

static_columns:
  - name: "Бібліотека-отримувач"
    value: "REDACTED_LIBRARY_NAME"
    reason: "Потрібно для імпорту в іншу бібліотеку, MARC-джерела поки немає"

  - name: "Статус імпорту"
    value: "Новий"
    reason: "Фіксоване значення для downstream import"

required_columns:
  - "ID Запису"
  - "Назва книги"
  - "Тип документа"
  - "Бібліотека-отримувач"
```

**Контракт — `config/export_dictionaries.yaml`:**

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
  authorized_value: "keep_code"  # keep_code | empty | fail
```

**Контракт — MappingLoader:**

```python
@dataclass
class StaticColumn:
    name: str
    value: str
    reason: str = ""

@dataclass
class AuthorizedValueDictionary:
    name: str
    values: dict[str, str]

@dataclass
class ExportDictionaries:
    authorized_values: dict[str, AuthorizedValueDictionary]
    unknown_policy: dict[str, str]

@dataclass
class MARCMapping:
    columns: list[ColumnMapping]
    static_columns: list[StaticColumn]
    required_columns: list[str]
    dictionaries: ExportDictionaries
```

**Синхронізація словників:**

- `dictionary` у `marc_mapping.yaml` має посилатися на ключ у `export_dictionaries.yaml`.
- Невідомий dictionary id є помилкою валідації.
- `required_columns` має бути subset від `columns + static_columns`.
- Якщо Koha Authorized values змінюються, `export_dictionaries.yaml` має оновлюватися в тій самій ітерації, що й mapping.
- У PR/CHANGELOG потрібно явно вказувати, які authorized values додано/змінено.

**Acceptance criteria:**

- [ ] Валідний mapping зі static columns завантажується без помилок.
- [ ] Static columns потрапляють у XLSX навіть без MARC source.
- [ ] `BOOK` через dictionary `itemtypes` перетворюється на `Книга`.
- [ ] Невідомий `dictionary: "missing"` дає `MappingValidationError`.
- [ ] `required_columns` з неіснуючою колонкою дає `MappingValidationError`.
- [ ] Тест `test_static_columns_are_loaded`.
- [ ] Тест `test_authorized_value_dictionary_maps_code_to_label`.
- [ ] Тест `test_unknown_dictionary_id_raises`.
- [ ] Тест `test_required_columns_must_exist`.

**Заборони:**

- Не хардкодити `BOOK -> Книга` у Python-коді.
- Не змішувати словники export-модуля з runtime Koha config без явної синхронізації.

---

### Завдання 1.2 — JSON logger та run_id correlation

**Мета:**
Усі log-повідомлення export-модуля мають бути JSON-compatible і містити `run_id`.

**Файл:** `src/export_module/observability/logger.py`

**Acceptance criteria:**

- [ ] Кожен log-рядок містить `timestamp`, `level`, `event`, `run_id`.
- [ ] `run_id` береться з `contextvars`.
- [ ] Логи не містять `GRAPH_CLIENT_SECRET`, token payload або raw secrets.
- [ ] Події для Google Drive mount називаються `gdrive_copy_*`, не `gdrive_upload_*`.
- [ ] Події для email називаються `graph_email_*`, не `smtp_*`.

---

### Завдання 1.3 — Prometheus metrics, optional

**Мета:**
Публікувати метрики після кожного запуску, якщо `PUSHGATEWAY_URL` задано.

**Контракт:**

```python
@dataclass
class RunMetrics:
    run_id: str
    records_exported: int
    records_failed: int
    duration_seconds: float
    errors_by_stage: dict[str, int]
    # stage keys: koha_fetch | marc_parse | xlsx_gen | gdrive_copy | graph_email | db_commit
```

**Acceptance criteria:**

- [ ] `MetricsPublisher(None).push(...)` — no-op.
- [ ] Недоступний Pushgateway логує WARNING і не валить export.
- [ ] Stage labels не містять secrets або raw file URLs.

---

## Фаза 2 — Ядро: Koha API, MARC parsing, XLSX

> **Мета фази:** отримати кандидатів з Koha, спарсити MARCXML, згенерувати XLSX з урахуванням static columns і Authorized values.
> **Тривалість:** 3–4 дні.

### Завдання 2.1 — KohaApiClient з keyset pagination

**Мета:**
Реалізувати пріоритетну keyset pagination по `biblionumber > last_seen_id` зі стабільним `biblionumber ASC`. Offset pagination дозволена тільки як fallback, якщо цільовий Koha endpoint не підтримує потрібний filter contract.

**Файл:** `src/export_module/koha/client.py`

**Контракт:**

```python
from typing import Iterator
import requests

class KohaApiClient:
    def __init__(self, base_url: str, username: str, password: str, page_size: int = 100) -> None: ...

    def fetch_all_biblios_keyset(
        self,
        biblionumber_from: int | None = None,
        biblionumber_to: int | None = None,
    ) -> Iterator[dict]:
        """
        Ітерує всі записи за biblionumber ASC.
        На кожній сторінці запитує biblionumber > last_seen_id.
        Конкретний синтаксис фільтра має бути підтверджений contract-тестом для цільової Koha.
        Якщо задано range, повертає тільки records у inclusive межах.
        """

    def fetch_all_biblios_offset_fallback(
        self,
        biblionumber_from: int | None = None,
        biblionumber_to: int | None = None,
    ) -> Iterator[dict]:
        """Fallback через _per_page/_offset, якщо keyset недоступний."""

    def fetch_biblio_marcxml(self, biblionumber: int) -> str: ...
```

**Acceptance criteria:**

- [ ] `fetch_all_biblios_keyset()` при 3 сторінках по 10 записів ітерує 30 записів.
- [ ] `last_seen_id` оновлюється за max `biblionumber` із поточної сторінки.
- [ ] Є contract-тест/fixture для фактичного Koha filter syntax.
- [ ] Offset fallback покритий окремим тестом.
- [ ] Не робити `_per_page=99999`.
- [ ] `biblionumber_from=1000`, `biblionumber_to=1250` повертає тільки records у межах `1000..1250`.
- [ ] Keyset range стартує з `last_seen_id = biblionumber_from - 1`.
- [ ] Некоректний range (`from > to`, непозитивні значення) відхиляється validation error.

---

### Завдання 2.2 — filter_exportable_biblios()

**Мета:**
Відокремити фільтрацію за state DB від MARC parsing.

**Acceptance criteria:**

- [ ] Completed biblionumber виключається.
- [ ] Retry-eligible biblionumber включається.
- [ ] Recoverable staged runs не створюють дубльований export.
- [ ] Якщо задано runtime range, records поза `biblionumber_from..biblionumber_to` не потрапляють у candidate list.

---

### Завдання 2.3 — MARCParser з transforms і Authorized values

**Мета:**
Перетворити MARCXML у плоский dict колонок, застосувати transforms, static columns і authorized value dictionaries.

**Контракт:**

```python
class MARCParser:
    def __init__(self, mapping: MARCMapping) -> None: ...

    def parse_record(self, marcxml: str) -> dict[str, str | None] | None: ...

    def apply_authorized_value(self, dictionary_id: str, raw_code: str | None) -> str | None: ...
```

**Acceptance criteria:**

- [ ] Відсутні MARC-поля повертають `None`, не exception.
- [ ] Static columns додаються після MARC extraction.
- [ ] Authorized value `BOOK` експортується як `Книга`.
- [ ] Unknown authorized value поводиться згідно `unknown_policy`.
- [ ] Malformed MARCXML логує warning і повертає `None`.

---

### Завдання 2.4 — XLSXGenerator

**Мета:**
Згенерувати XLSX у `/tmp` з колонками в порядку `columns + static_columns`, визначеному mapping contract.

**Acceptance criteria:**

- [ ] Файл створено у `/tmp`.
- [ ] Ім'я відповідає `export_Koha_YYYY-MM-DD_HHMMSS_{run_id[:8]}.xlsx`.
- [ ] Static columns присутні в XLSX.
- [ ] Authorized values потрапляють у XLSX як кириличні labels.
- [ ] При `records=[]` файл містить header row.
- [ ] Не використовувати `pandas`.

---

## Фаза 3 — Зовнішні side effects та оркестрація

> **Мета фази:** реалізувати copy у rclone-mounted Google Drive, MS Graph email і staged pipeline.
> **Тривалість:** 3–4 дні.

### Завдання 3.1 — ExportDriveMountService

**Мета:**
Копіювати XLSX у Google Drive, який уже змонтований в контейнері як `/mnt/drive`, без Google API і без service account у export-модулі.

**Файл:** `src/export_module/services/drive_mount_service.py`

**Контракт:**

```python
from dataclasses import dataclass

@dataclass
class DriveMountCopyResult:
    file_path: str
    folder_path: str
    file_name: str
    was_skipped: bool = False

class ExportDriveMountService:
    def __init__(self, export_root_path: str) -> None: ...

    def copy_to_mount(self, xlsx_path: str, run_id: str) -> DriveMountCopyResult:
        """
        1. Визначити поточний рік.
        2. Створити {export_root_path}/{year} через os.makedirs(..., exist_ok=True).
        3. Якщо фінальний XLSX із run_id вже існує — повернути was_skipped=True.
        4. Копіювати в .part.
        5. fsync file, де можливо.
        6. os.replace(.part, .xlsx).
        7. При помилці прибрати .part.
        """
```

**Acceptance criteria:**

- [ ] Річний каталог створюється ідемпотентно.
- [ ] Повторний run із тим самим `run_id` не копіює файл повторно.
- [ ] Copy використовує `.part` + `os.replace()`.
- [ ] При exception `.part` прибирається.
- [ ] `export_root_path` поза `/mnt/drive` відхиляється конфігом.
- [ ] Жодного `googleapiclient`, `google-auth`, `drive.file` у цьому сервісі.

---

### Завдання 3.2 — GraphEmailService

**Мета:**
Надсилати email через Microsoft Graph API `sendMail`.

**Файл:** `src/export_module/services/graph_email_service.py`

**Контракт:**

```python
@dataclass
class GraphEmailSendResult:
    recipient: str
    attachment_included: bool
    attachment_size_bytes: int
    message_id: str | None = None

class GraphEmailService:
    def __init__(self, config: ExportConfig) -> None: ...

    def send_via_graph(
        self,
        records: list[dict],
        drive_result: DriveMountCopyResult,
        xlsx_path: str,
        run_id: str,
    ) -> GraphEmailSendResult: ...
```

**Behavior:**

- Для small XLSX (`<= MAX_ATTACHMENT_BYTES`) додати Graph file attachment.
- Для large XLSX надіслати лист без вкладення, але з `drive_result.file_path`/операторським посиланням, якщо воно доступне.
- Retry на Graph `429`, `500`, `502`, `503`, `504`.
- Не логувати access token, client secret, raw response із sensitive headers.

**Acceptance criteria:**

- [ ] Small file надсилається з attachment.
- [ ] Large file надсилається без attachment і з warning у HTML.
- [ ] Graph 429 retry працює.
- [ ] Відсутній `GRAPH_CLIENT_SECRET` відхиляється в config validation.
- [ ] Є тест, що secret/token не потрапляє в logs.

---

### Завдання 3.3 — ExportOrchestrator staged pipeline

**Мета:**
Скоординувати DB state, Koha, MARC, XLSX, drive mount copy, Graph email і metrics.

**Контракт:**

```python
class ExportOrchestrator:
    def run(self, options: RuntimeOptions) -> int:
        """
        1. run_id = str(uuid4()); set_run_id(run_id)
        2. config.validate()
        3. candidates = filter_exportable_biblios(...)
        4. Якщо candidates порожній → return 0
        5. records = marc_parser.parse_all(...)
        6. xlsx_path = xlsx_generator.generate(records, run_id)
        7. Якщо options.dry_run → preserve dry copy, log would-do, return 0 без DB writes
        8. db.insert_pending(candidates, run_id)
        9. db.mark_xlsx_generated(run_id, basename)
        10. drive_result = drive_mount_service.copy_to_mount(xlsx_path, run_id)
        11. db.mark_gdrive_uploaded(run_id, drive_result.file_path, drive_result.folder_path)
        12. email_result = graph_email_service.send_via_graph(...)
        13. db.mark_email_sent(run_id, email_result.message_id)
        14. db.mark_completed(run_id)
        15. metrics.push(...)
        16. return 0
        """
```

**Acceptance criteria:**

- [ ] Happy path завершується `completed`.
- [ ] Drive copy failure → `failed`, Graph не викликається.
- [ ] Graph failure after copy → збережено `gdrive_uploaded`, retry продовжує з email stage.
- [ ] Crash after Graph success → recovery завершує `completed`, не надсилаючи email повторно.
- [ ] Temp XLSX видаляється у `finally`.

---

## Фаза 4 — CLI та операційні режими

> **Мета фази:** реалізувати entrypoint, dry-run, health-check і reset/recovery команди.
> **Тривалість:** 1–2 дні.

### Завдання 4.1 — `__main__.py` та CLI

**Файл:** `src/export_module/__main__.py`

```python
# Використання:
#   python -m src.export_module
#   python -m src.export_module --dry-run
#   python -m src.export_module --biblionumber-from 1000 --biblionumber-to 1250
#   python -m src.export_module --dry-run --biblionumber-from 1000 --biblionumber-to 1250
#   python -m src.export_module --reset-pending RUN_ID
#   python -m src.export_module --health-check
```

**Acceptance criteria:**

- [ ] `--dry-run` встановлює `RuntimeOptions(dry_run=True)`.
- [ ] `--biblionumber-from` / `--biblionumber-to` встановлюють runtime range options.
- [ ] Range flags є optional, inclusive і не мають env-аналогів.
- [ ] CLI відхиляє `from > to` і непозитивні значення.
- [ ] `EXPORT_DRY_RUN` у env ігнорується.
- [ ] `--health-check` перевіряє config, DB dir, mapping, dictionaries, `/mnt/drive` root path.
- [ ] `--reset-pending RUN_ID` повертає кількість оновлених записів.

---

### Завдання 4.2 — Dry-run без side effects

**Мета:**
Dry-run генерує XLSX і зберігає копію в `/tmp/dry_run`, але не змінює SQLite, `/mnt/drive` і MS Graph.

**Acceptance criteria:**

- [ ] `--dry-run` → SQLite не містить нових export rows.
- [ ] `--dry-run` → `ExportDriveMountService.copy_to_mount` не викликається.
- [ ] `--dry-run` → `GraphEmailService.send_via_graph` не викликається.
- [ ] `/tmp/dry_run/export_Koha_*.xlsx` існує після run.
- [ ] Логи містять `would_copy_to_gdrive_mount`, `would_send_graph_email`, `db_not_modified`.

---

## Фаза 5 — Тестування, Docker, Runbook, docs

> **Мета фази:** закрити production-ready перевірки, documentation і regression tests.
> **Тривалість:** 3–4 дні.

### Завдання 5.1 — Integration-тести pipeline

**Обов'язкові тести:**

- [ ] `test_happy_path_five_records`
- [ ] `test_drive_mount_copy_fail_marks_failed`
- [ ] `test_graph_fail_after_drive_copy_keeps_gdrive_uploaded`
- [ ] `test_recovery_after_graph_success_marks_completed_without_resend`
- [ ] `test_dry_run_no_side_effects`
- [ ] `test_zero_candidates_returns_zero`
- [ ] `test_keyset_pagination_all_pages_processed`
- [ ] `test_biblionumber_range_export_only_requested_records`
- [ ] `test_cli_rejects_invalid_biblionumber_range`
- [ ] `test_part_file_cleanup_on_copy_error`
- [ ] `test_no_duplicate_export_on_second_run`
- [ ] `test_static_columns_and_authorized_values_in_xlsx`

**Acceptance criteria:**

- [ ] Усі integration tests проходять.
- [ ] Coverage `orchestrator.py` ≥ 90%.

---

### Завдання 5.2 — requirements.txt та Docker

**Мета:**
Додати тільки потрібні залежності export-модуля.

**Рекомендовані залежності:**

```text
openpyxl>=3.1.5
PyYAML>=6.0.2
jsonschema>=4.23.0
tenacity>=8.3.0
python-json-logger>=2.0.7
prometheus-client>=0.20.0
pytest-cov>=5.0.0
```

**Примітки:**

- `requests` уже є в поточному `requirements.txt` і може використовуватись для Koha та MS Graph.
- `google-api-python-client` і `google-auth` не потрібні для export copy, бо Google Drive доступний через `/mnt/drive`.
- `httpx` не додавати без окремої причини; KISS і поточний стек схиляють до `requests`.

**Acceptance criteria:**

- [ ] `python -m src.export_module --health-check` не падає через ImportError.
- [ ] `docker build` успішний.
- [ ] `test_all_imports_succeed` проходить.

---

### Завдання 5.3 — Runbook Koha Export

**Файл:** `docs/RUNBOOK_KOHA_EXPORT.md`

**Обов'язкові розділи:**

1. Stuck `pending` після SIGKILL/OOM.
2. Recovery зі станів `gdrive_uploaded` і `email_sent`.
3. `/mnt/drive` не змонтований або read-only.
4. `.part` файл залишився після copy failure.
5. MS Graph auth failure: tenant/client/sender/access policy.
6. MS Graph `429`/throttling.
7. XLSX > 15 MB — перевірка link-only email.
8. Примусовий re-export конкретного `biblionumber`.
9. Dry-run smoke через `--dry-run`.
10. Range smoke через `--biblionumber-from <FROM> --biblionumber-to <TO>`.
10. Синхронізація Authorized values dictionary після змін у Koha.

---

### Завдання 5.4 — Документація та changelog

**Мета:**
Оновити README/ARCHITECTURE або окремий docs entrypoint, а також активний changelog.

**Обов'язково зафіксувати:**

- Export працює як CLI/batch, не Flask endpoint.
- Google Drive export пише у `/mnt/drive`, без Google API/service account для copy.
- Email через MS Graph API.
- Dry-run тільки через `--dry-run`.
- Keyset pagination як пріоритет.
- Optional `biblionumber` range тільки через CLI runtime flags, не через env.
- Static columns і Authorized values dictionaries.

---

## Матриця залежностей між завданнями

```text
Фаза 0:  0.1 ──► 0.2 ──► 0.3
                              │
Фаза 1:                       └──► 1.1 ──► 1.2 ──► 1.3
                                                        │
Фаза 2:                                                 └──► 2.1 ──► 2.2 ──► 2.3 ──► 2.4
                                                                                           │
Фаза 3:                                                                                    └──► 3.1 ──► 3.2 ──► 3.3
                                                                                                                     │
Фаза 4:                                                                                                              └──► 4.1 ──► 4.2
                                                                                                                                        │
Фаза 5:                                                                                                                                 └──► 5.1 ──► 5.2 ──► 5.3 ──► 5.4
```

---

## Загальний checklist готовності до production

- [ ] Фаза 0: staged-idempotency schema/repository/config готові й протестовані.
- [ ] Фаза 1: mapping, static columns і Authorized values dictionaries валідовані JSON Schema.
- [ ] Фаза 2: Koha keyset pagination має contract-тест для цільового endpoint.
- [ ] Фаза 2/4: Optional `biblionumber` range має unit/integration тести для valid та invalid меж.
- [ ] Фаза 2: `parse_record()` не падає на брудному MARCXML.
- [ ] Фаза 2: XLSX містить static columns і кириличні labels з Authorized values.
- [ ] Фаза 3: retry/backoff реалізовано для `gdrive_copy` та `graph_email`.
- [ ] Фаза 3: `.part` cleanup працює при copy failure.
- [ ] Фаза 3: temp XLSX видаляється у `finally`.
- [ ] Фаза 4: `--dry-run` не змінює SQLite, `/mnt/drive`, MS Graph.
- [ ] Фаза 5: `/mnt/drive` mount health-check описаний і протестований.
- [ ] Фаза 5: Graph permissions/mailbox access policy описані в runbook.
- [ ] Фаза 5: integration-тест `test_no_duplicate_export_on_second_run` проходить.
- [ ] Coverage ≥ 80% для всього модуля (`pytest --cov=src/export_module`).

---

*Кінець роадмапи. Версія 1.1. На основі PRD v2.2.*
