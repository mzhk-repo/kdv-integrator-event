# PRD: Модуль експорту бібліографічних записів Koha в XLSX
## з Email-сповіщенням та архівацією на Google Drive

> **Версія:** 2.3
> **Статус:** Review-ready
> **Попередня версія:** 1.0 (вихідний PRD)
> **Changelog v2.0:** Виправлено At-Most-Once семантику, додано пагінацію API, усунуто Base64-псевдошифрування, додано retry/backoff, обмеження розміру вкладення, мінімізацію OAuth scopes, Dry-Run режим, декларативний YAML-маппінг, структуровані метрики та exit codes.
> **Changelog v2.1:** Узгоджено PRD з поточним стеком репозиторію: експорт оформлено як окремий CLI/batch-модуль, уточнено offset-based пагінацію Koha, розділено read-only Google Drive source та upload-сервіс експорту, перейменовано шлях до service account на `GDRIVE_SERVICE_ACCOUNT_FILE`, уточнено staged-idempotency замість абсолютної At-Most-Once гарантії для зовнішніх side effects.
> **Changelog v2.2:** Google Drive export переведено з API/service account upload на запис у вже змонтований rclone volume `/mnt/drive`; Email transport змінено зі SMTP на Microsoft Graph API; `EXPORT_DRY_RUN` прибрано з ENV, dry-run запускається тільки CLI прапорцем `--dry-run`.
> **Changelog v2.3:** Додано optional operator mode для експорту тільки записів Koha в inclusive діапазоні `biblionumber` через CLI flags, без env-перемикачів і без зміни staged-idempotency контракту.

---

## Зміст

1. [Загальний опис](#1-загальний-опис)
2. [Сценарії використання](#2-сценарії-використання)
3. [Функціональні вимоги](#3-функціональні-вимоги)
4. [Технічний стек та архітектура](#4-технічний-стек-та-архітектура)
5. [Конфігурація та безпека](#5-конфігурація-та-безпека)
6. [Метрики та SRE-логіка](#6-метрики-та-sre-логіка)
7. [Узгодження з поточним репозиторієм](#7-узгодження-з-поточним-репозиторієм)
8. [Стратегія тестування](#8-стратегія-тестування)

---

## 1. Загальний опис

Цей документ визначає вимоги до окремого CLI/batch-модуля сервісу **KDV Integrator**, призначеного для
періодичного автоматичного експорту бібліографічних записів з Koha ILS у фіксований формат XLSX.

Модуль експорту є ізольованою підсистемою і не замінює поточний runtime
Koha → DSpace архівації (`src/app.py`, `src/core.py`, `src/koha.py`). Його
рекомендований запуск: one-off CLI у контейнері, Swarm wrapper або захищений
асинхронний UI control endpoint, а не довгоживучий Flask endpoint.

Згенеровані звіти підлягають:

- автоматичній архівації у Google Drive з динамічною ротацією каталогів за роками;
- розсилці результатів через Microsoft Graph API (Email);
- обов'язковому локальному трекінгу стану зі staged-idempotency для мінімізації
  дублювання зовнішніх side effects.

---

## 2. Сценарії використання

### Сценарій 1: Автоматичний періодичний експорт

Як **системний адміністратор бібліотеки**,
я хочу, щоб робот періодично аналізував каталог Koha, виявляв нові записи з лінками
на файли репозиторію (`856$u`, де `$y = "Файл"`) та експортував їх у XLSX-таблицю,
щоб надавати актуальні дані для імпорту в сторонню систему без ручного втручання.

### Сценарій 2: Безпечне хмарне збереження з ротацією

Як **SRE-інженер**,
я хочу, щоб згенерований XLSX автоматично завантажувався на Google Drive у папку
поточного року (наприклад, `2026`), яка створюється автоматично при переході на
новий календарний рік,
щоб забезпечити організовану структуру довгострокового зберігання без захаращення
кореневого каталогу.

### Сценарій 3: Гарантія унікальності та сповіщення

Як **оператор системи**,
я хочу отримувати email-сповіщення зі статистикою нових записів (і прикріпленим
XLSX-файлом, якщо розмір дозволяє),
щоб бути впевненим, що кожен бібліографічний запис Koha експортується та
надсилається **лише один раз**, а стан процесу атомарно фіксується у локальній БД
лише після підтвердження від усіх зовнішніх сервісів.

### Сценарій 4: Ручний експорт діапазону `biblionumber`

Як **оператор або SRE-інженер**,
я хочу запустити export-модуль для конкретного inclusive діапазону Koha
`biblionumber`, наприклад `1000..1250`,
щоб перевірити або передати обмежену партію записів без повного обходу каталогу.
Діапазон має задаватися тільки CLI-прапорцями, а не env-змінними, і має
поважати ті самі правила staged-idempotency, retry та dry-run, що й повний export.

---

## 3. Функціональні вимоги

### 3.1. Критерії відбору записів

Робот запитує записи через Koha REST API та фільтрує їх за такими критеріями:

| # | Критерій | Деталі |
|---|----------|--------|
| 1 | Поле `856$u` заповнене | Наявність URL посилання |
| 2 | `856$y == "Файл"` | PDF успішно завантажено в DSpace |
| 3 | `biblionumber` відсутній у `exported_records` зі статусом `completed` | At-Most-Once гарантія |
| 4 | Якщо задано CLI range — `from <= biblionumber <= to` | Optional операторський фільтр діапазону |

> **Увага:** записи зі статусом `pending` або `failed` (з `retry_count < MAX_RETRIES`)
> є кандидатами для повторної обробки при наступному запуску.

### 3.2. Пагінація Koha REST API

Усі запити до Koha REST API **обов'язково** використовують явну пагінацію.
Пріоритетний підхід для export-модуля — **keyset pagination** по стабільному
монотонному ключу `biblionumber`: кожна наступна сторінка запитує записи з
`biblionumber > last_seen_id` і стабільним сортуванням `biblionumber ASC`.

Цей підхід cursor-like і кращий за offset для великих або змінюваних каталогів:
він не залежить від позиції рядка в результаті, краще переносить додавання нових
записів під час експорту і не деградує на великих `_offset`.

Offset-based пагінація через `_per_page` та `_offset` дозволена тільки як fallback,
якщо конкретний Koha endpoint не підтримує фільтр/пошук за `biblionumber > last_seen_id`
або інший офіційно підтверджений cursor/keyset contract.
Запит без явної пагінації є дефектом: за замовчуванням API повертає не більше
100 записів, що призводить до мовчазного пропуску решти каталогу.

```python
def fetch_all_biblios_keyset(koha_client, page_size: int = 100):
    """
    Генератор з keyset pagination. Ітерує каталог за biblionumber ASC.
    last_seen_id оновлюється тільки після непорожньої сторінки.
    """
    last_seen_id = 0
    page = 0
    while True:
        batch = koha_client.get(
            "/api/v1/biblios",
            params={
                "_per_page": page_size,
                "_order_by": "biblionumber",
                "biblionumber": {">": last_seen_id},
            },
        )
        if not batch:
            break
        page += 1
        logger.info(
            "koha_page_fetched",
            page=page,
            count=len(batch),
            last_seen_id=last_seen_id,
        )
        for biblio in batch:
            yield biblio
        last_seen_id = max(int(item["biblionumber"]) for item in batch)
        if len(batch) < page_size:
            break
```

> Синтаксис параметра `biblionumber > last_seen_id` залежить від фактичного Koha
> REST/search endpoint. Під час реалізації потрібно підтвердити підтримуваний
> формат фільтрації на цільовій версії Koha і зафіксувати його contract-тестом.

#### 3.2.1. Optional range export за `biblionumber`

Export-модуль має підтримувати опційне обмеження вибірки inclusive діапазоном
Koha `biblionumber`. Діапазон задається тільки CLI-прапорцями:

```bash
python -m src.export_module --biblionumber-from 1000 --biblionumber-to 1250
python -m src.export_module --dry-run --biblionumber-from 1000 --biblionumber-to 1250
```

Правила:

- `--biblionumber-from` і `--biblionumber-to` мають бути додатними integer.
- Якщо задано обидва прапорці, `from <= to`; інакше CLI завершується validation error.
- Дозволено задавати тільки нижню або тільки верхню межу:
  - тільки `from` — export від `from` до кінця каталогу;
  - тільки `to` — export від початку каталогу до `to`.
- Межі діапазону є inclusive.
- Range filter не обходить staged-idempotency: completed записи все одно
  виключаються, retry/recoverable правила лишаються чинними.
- Range filter не є env-конфігом і не має змінної на кшталт
  `EXPORT_BIBLIONUMBER_FROM`; це operator/runtime option.

Для keyset pagination нижня межа визначає стартовий `last_seen_id` як
`biblionumber_from - 1`. Верхня межа має передаватися в Koha filter, якщо endpoint
це підтримує, або застосовуватися локально після отримання сторінки; у будь-якому
разі records з `biblionumber > biblionumber_to` не мають потрапити в XLSX/SQLite.

```python
def fetch_all_biblios_keyset(
    koha_client,
    page_size: int = 100,
    biblionumber_from: int | None = None,
    biblionumber_to: int | None = None,
):
    last_seen_id = (biblionumber_from - 1) if biblionumber_from else 0
    while True:
        params = {
            "_per_page": page_size,
            "_order_by": "biblionumber",
            "biblionumber": {">": last_seen_id},
        }
        batch = koha_client.get("/api/v1/biblios", params=params)
        if not batch:
            break
        for biblio in batch:
            current_id = int(biblio["biblionumber"])
            if biblionumber_to is not None and current_id > biblionumber_to:
                return
            if biblionumber_from is None or current_id >= biblionumber_from:
                yield biblio
        last_seen_id = max(int(item["biblionumber"]) for item in batch)
        if len(batch) < page_size:
            break
```

Contract-тести мають окремо фіксувати keyset params для range-start і те, що
верхня межа не пропускає records за межами requested діапазону.

### 3.3. Декларативний YAML-маппінг MARC → XLSX

Маппінг полів зберігається у `config/marc_mapping.yaml`. Зміна маппінгу
не вимагає зміни Python-коду. У runtime це має бути або bind-mounted/config-secret
файл, або оновлений image/config artifact залежно від обраного способу деплою.

```yaml
# config/marc_mapping.yaml
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

  - name: "Автор"
    sources:
      - field: "100"
        subfields: ["a"]
      - field: "110"
        subfields: ["a"]

  - name: "Рік видання"
    sources:
      - field: "264"
        subfields: ["c"]
      - field: "260"
        subfields: ["c"]
    transform: "extract_year_regex"   # Regex: \d{4}

  - name: "Видавництво"
    sources:
      - field: "264"
        subfields: ["b"]
      - field: "260"
        subfields: ["b"]

  - name: "Посилання на файл"
    sources:
      - field: "856"
        subfields: ["u"]
        condition:
          subfield: "y"
          equals: "Файл"

  - name: "DSpace Handle"
    sources:
      - field: "856"
        subfields: ["u"]
        condition:
          subfield: "y"
          equals: "Запис в репозиторії"
```

`MappingLoader` валідує файл через JSON Schema при кожному запуску.
Невалідний `marc_mapping.yaml` спричиняє негайне завершення з `exit_code = 2`.

### 3.4. Defensive MARC-парсинг

Усі функції вилучення даних мають явну обробку відсутніх полів.
Відсутнє або порожнє поле **ніколи** не спричиняє виключення — повертається `None`.

```python
def safe_extract_subfield(
    record: MARCRecord,
    tag: str,
    subfield: str,
    fallback=None
) -> str | None:
    """
    Безпечне вилучення субполя. Повертає перше знайдене значення або fallback.
    """
    for field in record.get_fields(tag):
        values = field.get_subfields(subfield)
        if values:
            return values[0].strip()
    return fallback


def extract_year(record: MARCRecord) -> str | None:
    """
    Пріоритет: 264$c > 260$c. Regex \d{4} для очищення.
    Приклади входу: '©2024.', 'c2021', '2019-2020' → '2024', '2021', '2019'.
    """
    for tag in ("264", "260"):
        for field in record.get_fields(tag):
            for value in field.get_subfields("c"):
                match = re.search(r"\d{4}", value)
                if match:
                    return match.group(0)
    return None


def get_file_url(record: MARCRecord) -> str | None:
    """
    Повертає 856$u лише якщо $y == "Файл".
    При наявності кількох 856 — повертає перший відповідний.
    """
    for field in record.get_fields("856"):
        if "Файл" in field.get_subfields("y"):
            urls = field.get_subfields("u")
            if urls:
                return urls[0]
    return None
```

### 3.5. Генерація XLSX та ротація на Google Drive

#### Формат назви файлу

```
export_Koha_{YYYY-MM-DD}_{HHMMSS}_{run_id[:8]}.xlsx
```

Включення `run_id` у назву файлу гарантує ідемпотентність при retry: повторне
копіювання файлу з тим самим `run_id` може бути виявлено та пропущено.

#### Алгоритм ротації та завантаження

```
[ Згенеровано: export_Koha_2026-05-24_224500_a1b2c3d4.xlsx ]
                        │
                        ▼
          Визначення поточного року (2026)
                        │
   Пошук папки "2026" у EXPORT_GDRIVE_ROOT_PATH
                        │
       ┌────────────────┴────────────────┐
       ▼ Існує                           ▼ Не існує
  Використати path                 Створити папку "2026"
       │                                 │
       └────────────────┬────────────────┘
                        │
            Перевірка: чи існує файл
            з цим run_id у папці?
                        │
       ┌────────────────┴────────────────┐
       ▼ Існує (retry)                   ▼ Не існує
  Пропустити copy,                Atomic copy XLSX до папки
  використати наявний path        року в mounted Google Drive
       │                                 │
       └────────────────┬────────────────┘
                        │
               Повернути {file_path,
               folder_path}
                        │
                        ▼
         Видалення temp-файлу у finally{}
```

#### Google Drive через rclone-mounted volume

Для експорту XLSX **не потрібен окремий Google service account у модулі експорту**.
Google Drive вже змонтований всередині контейнера через rclone volume як
`/mnt/drive` (`kdv-integrator-event` → `mnt` → `drive`). Export-модуль працює
з ним як зі звичайною файловою системою:

- root-каталог експорту задається `EXPORT_GDRIVE_ROOT_PATH`, наприклад `/mnt/drive/KohaExports`;
- річний каталог створюється локально через `os.makedirs(..., exist_ok=True)`;
- XLSX спочатку копіюється у `.part` файл у цільовому каталозі;
- після успішного copy/fsync виконується atomic rename `.part` → `.xlsx`;
- idempotency при retry перевіряється за наявністю фінального XLSX з тим самим `run_id`.

> Поточний read-only `GoogleDriveSource` для PDF із `956$u`/`956$q` не використовується
> для export copy. Він лишається окремим source-компонентом архіваційного pipeline.

### 3.6. Email-транспорт через Microsoft Graph API

**Протокол:** Microsoft Graph API `sendMail` через HTTPS. SMTP не використовується.

**Логіка вибору між вкладенням та шляхом/посиланням:**

```python
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024  # 15 MB

if os.path.getsize(xlsx_path) > MAX_ATTACHMENT_BYTES:
    # Надсилати лист лише зі шляхом/посиланням на GDrive mount + попередження у тілі
    send_graph_email_with_link(gdrive_ref, warning=True)
else:
    # Надсилати лист з прикріпленим XLSX
    send_graph_email_with_attachment(xlsx_path, gdrive_ref)
```

**Контент листа (Multipart HTML):**

- Зведений звіт: дата запуску, `run_id`, загальна кількість експортованих записів.
- Інтерактивна таблиця: `biblionumber`, Автор, Назва.
- Статус та шлях до папки Google Drive mount (`folder_path`).
- Попередження, якщо файл перевищує ліміт і не прикріплений.
- Прикріплений XLSX-файл через Graph file attachment (якщо розмір ≤ 15 MB).

### 3.7. Staged-idempotency та транзакційний state tracking

> **Критично:** це виправлення відносно версії 1.0 PRD. Попередній підхід
> (commit після підтвердження email-provider) створював вікно дублювання при збої між отриманням
> підтвердження та записом у БД.

Абсолютну At-Most-Once гарантію для Google Drive filesystem copy та MS Graph sendMail неможливо
забезпечити лише локальним SQLite commit-ом, бо зовнішній сервіс може успішно
виконати side effect, а процес може впасти до локального запису стану. Тому
модуль використовує staged-idempotency:

- кожен запуск має стабільний `run_id`;
- Google Drive filesystem copy шукає файл із тим самим `run_id` перед повторним copy;
- email-відправлення фіксується окремою стадією `email_sent`;
- recovery для `pending`/проміжних станів не повторює підтверджені side effects.

**Правильний порядок операцій:**

```
Фаза 1 — Резервування (до будь-яких зовнішніх викликів):
  ┌─────────────────────────────────────────────────────────┐
  │  BEGIN TRANSACTION                                      │
  │  INSERT INTO exported_records                           │
  │    (biblionumber, run_id, status='pending', ...)        │
  │  COMMIT                                                 │
  └─────────────────────────────────────────────────────────┘
          │
          ▼ Тільки після успішного pending-запису
Фаза 2 — Виконання зовнішніх викликів зі staged state:
  Генерація XLSX → state=xlsx_generated
  GDrive filesystem copy або reuse existing run_id file → state=gdrive_uploaded
  Graph sendMail або skip якщо email_sent уже підтверджено → state=email_sent
          │
          ▼ Тільки після підтвердження від ОБОХ сервісів
Фаза 3 — Підтвердження:
  ┌─────────────────────────────────────────────────────────┐
  │  BEGIN TRANSACTION                                      │
  │  UPDATE exported_records                                │
  │    SET status='completed', exported_at=NOW(),           │
  │        xlsx_filename=..., gdrive_file_path=...,         │
  │        email_sent_at=...                                │
  │    WHERE run_id=:run_id AND status='email_sent'         │
  │  COMMIT                                                 │
  └─────────────────────────────────────────────────────────┘

При помилці у Фазі 2:
  - до `gdrive_uploaded`: запис переходить у `failed`, `retry_count` збільшується;
  - після `gdrive_uploaded`: `gdrive_file_path` зберігається, наступний запуск/recovery
    продовжує з Graph sendMail без повторного copy;
  - після `email_sent`: recovery завершує `completed`, не надсилаючи email повторно.
```

---

## 4. Технічний стек та архітектура

### 4.1. Стек технологій

| Компонент | Технологія | Обґрунтування |
|-----------|-----------|---------------|
| Генерація XLSX | `openpyxl` | Без C-залежностей, легка у Docker-образі |
| БД стану | `SQLite3` | Вбудована в Python, файл на shared volume |
| Retry/backoff | `tenacity` | Декларативний retry з exponential backoff; нова залежність для export-модуля |
| Google Drive export | stdlib `pathlib`/`shutil`/`os.replace` через `/mnt/drive` | Google Drive вже змонтований rclone volume; API/service account для export не потрібні |
| Email | Microsoft Graph API через `requests` або `httpx` | Відправка листів через tenant/application permissions або delegated flow |
| Маппінг | `PyYAML` + `jsonschema` | Декларативний конфіг з валідацією |
| Логування | stdlib `logging` + JSON formatter або `python-json-logger` | Поточний проєкт уже використовує stdlib logging; JSON formatter додається локально для export-модуля |
| Метрики | `prometheus_client` (опційно) | Push до Pushgateway після кожного запуску; нова опційна залежність |

> Поточний `requirements.txt` ще не містить `openpyxl`, `PyYAML`, `jsonschema`,
> `tenacity` та `prometheus_client`. Їх потрібно додати окремою
> імплементаційною ітерацією разом із тестами імпорту.

### 4.2. Схема бази даних SQLite

```sql
CREATE TABLE IF NOT EXISTS exported_records (
    biblionumber  INTEGER NOT NULL,
    run_id        TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'pending'
                          CHECK(status IN (
                              'pending',
                              'xlsx_generated',
                              'gdrive_uploaded',
                              'email_sent',
                              'completed',
                              'failed'
                          )),
    exported_at   TIMESTAMP,
    last_attempt_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    retry_count   INTEGER NOT NULL DEFAULT 0,
    failed_reason TEXT,
    gdrive_file_path TEXT,
    gdrive_folder_path TEXT,
    email_sent_at TIMESTAMP,
    email_message_id TEXT,
    xlsx_filename TEXT,

    PRIMARY KEY (biblionumber, run_id)
);

-- Індекс для швидкого пошуку retry-eligible записів
CREATE INDEX IF NOT EXISTS idx_status_retry
    ON exported_records(status, retry_count);

-- Індекс для перевірки "чи вже є completed запис для biblionumber"
CREATE UNIQUE INDEX IF NOT EXISTS idx_biblionumber_completed
    ON exported_records(biblionumber)
    WHERE status = 'completed';
```

> Файл бази даних розміщується на dedicated host bind mount, доступному `kdv-api`.
> Рекомендований контейнерний шлях: `/data/kdv_export_state/export_state.db`; рекомендований host path: `/srv/kdv-integrator/export-state`.

### 4.3. Retry та backoff для зовнішніх сервісів

Усі виклики до зовнішніх API обгорнуті у `tenacity` retry:

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    retry=retry_if_exception_type((HttpError, TimeoutError, ConnectionError)),
    reraise=True
)
def copy_to_gdrive_mount(source_path: str, export_root_path: str, run_id: str) -> dict:
    ...

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=30, max=120),
    reraise=True
)
def send_email_via_graph(graph_config, message) -> None:
    ...
```

### 4.4. Головний ExportOrchestrator

```python
class ExportOrchestrator:
    def run(self) -> int:
        """
        Повертає exit_code:
          0 — success (навіть якщо 0 нових записів)
          1 — partial failure (деякі записи не оброблено)
          2 — total failure (жодного запису не оброблено, стан не змінено)
        """
        run_id = str(uuid4())
        xlsx_path = None

        try:
            # 1. Отримати нові записи з Koha (з пагінацією)
            candidates = self._fetch_candidates()
            if not candidates:
                logger.info("no_new_records", run_id=run_id)
                return 0

            # 2. ФАЗА 1: зарезервувати в БД (pending)
            self.db.insert_pending(candidates, run_id)

            # 3. Парсинг та генерація XLSX
            records = self.marc_parser.parse_all(candidates)
            xlsx_path = self.xlsx_generator.generate(records, run_id)

            # 4. GDrive filesystem copy/reuse за run_id (з retry)
            gdrive_result = self.gdrive_service.copy_to_mount(xlsx_path)
            self.db.mark_gdrive_uploaded(run_id, gdrive_result)

            # 5. MS Graph sendMail (з retry та логікою розміру), якщо ще не email_sent
            email_result = self.email_service.send_via_graph(records, gdrive_result, xlsx_path)
            self.db.mark_email_sent(run_id, email_result)

            # 6. ФАЗА 3: підтвердити в БД (completed)
            self.db.mark_completed(run_id)

            logger.info("export_success", run_id=run_id, count=len(records))
            return 0

        except Exception as exc:
            logger.error("export_failed", run_id=run_id, error=str(exc))
            self.db.mark_failed(run_id, str(exc))
            return 2

        finally:
            # Видалити temp-файл ЗАВЖДИ, незалежно від результату
            if xlsx_path and os.path.exists(xlsx_path):
                os.unlink(xlsx_path)
```

### 4.5. Dry-Run режим

Dry-run вмикається тільки CLI прапорцем `--dry-run`. Змінна середовища
`EXPORT_DRY_RUN` не використовується, щоб випадкове значення в env не змінювало
поведінку scheduled/export job. У dry-run процес генерує XLSX та логує результат,
але **не виконує** copy у Google Drive mount, MS Graph sendMail та оновлення SQLite.

При запуску з range dry-run використовує ті самі правила, але обробляє тільки
records у заданому inclusive діапазоні:

```bash
python -m src.export_module --dry-run --biblionumber-from 1000 --biblionumber-to 1250
```

```python
if self.config.dry_run:
    dry_run_path = f"/tmp/dry_run/{os.path.basename(xlsx_path)}"
    shutil.copy(xlsx_path, dry_run_path)
    logger.info("[DRY-RUN] Would copy to Google Drive mount",
                filename=os.path.basename(xlsx_path),
                records_count=len(records))
    logger.info("[DRY-RUN] Would send email via MS Graph to",
                recipient=self.config.graph_to)
    logger.info("[DRY-RUN] File preserved for inspection",
                path=dry_run_path)
    return 0
```

---

## 5. Конфігурація та безпека

### 5.1. Змінні середовища (SOPS + age)

Усі конфіденційні дані зберігаються в зашифрованих `env.dev.enc` / `env.prod.enc`.

> **Важливо щодо секретів:** значення зберігаються як **plain text** всередині
> зашифрованого файлу. Base64-кодування **не застосовується** — воно є encoding,
> а не encryption, і створює лише хибне відчуття захисту.

```bash
# Статус модуля
EXPORT_MODULE_ENABLED=true

# Google Drive mounted via rclone volume
EXPORT_GDRIVE_ROOT_PATH=/mnt/drive/KohaExports

# Microsoft Graph Email
GRAPH_TENANT_ID=00000000-0000-0000-0000-000000000000
GRAPH_CLIENT_ID=00000000-0000-0000-0000-000000000000
GRAPH_CLIENT_SECRET=REDACTED
GRAPH_SENDER_USER_ID=reports@example.org
GRAPH_TO=library-target@otherdomain.com

# Retry та ліміти
MAX_RETRIES=3
MAX_ATTACHMENT_BYTES=15728640

# Метрики (опційно)
PUSHGATEWAY_URL=http://pushgateway:9091
```

`EXPORT_GDRIVE_ROOT_PATH` має вказувати на каталог усередині вже змонтованого
rclone volume `/mnt/drive`. Graph secrets зберігаються тільки в зашифрованих
`env.dev.enc` / `env.prod.enc` або Swarm secret payload; у репозиторій потрапляють
лише placeholders на кшталт `REDACTED`.

### 5.2. Microsoft Graph permissions

Модуль email використовує Microsoft Graph `sendMail`. Мінімально потрібні права
залежать від обраного flow:

- application permissions: `Mail.Send` з обмеженням sender mailbox через Exchange Application Access Policy;
- delegated flow: `Mail.Send` від імені авторизованого користувача, якщо operational model це дозволяє.

У production рекомендовано application permissions + обмеження на конкретну mailbox,
щоб секрет застосунку не давав ширший доступ, ніж потрібно для export-розсилки.

---

## 6. Метрики та SRE-логіка

### 6.1. Структуроване JSON-логування

Кожна подія записується у форматі JSON з обов'язковими полями:
`timestamp`, `level`, `event`, `run_id`, `env`.

```json
{"timestamp":"2026-05-24T22:45:00Z","level":"INFO","event":"export_started","run_id":"a1b2c3d4-...","env":"production"}
{"timestamp":"2026-05-24T22:45:01Z","level":"INFO","event":"koha_fetch_done","run_id":"a1b2c3d4-...","pages":3,"total_candidates":247}
{"timestamp":"2026-05-24T22:45:01Z","level":"INFO","event":"pending_reserved","run_id":"a1b2c3d4-...","count":14}
{"timestamp":"2026-05-24T22:45:02Z","level":"INFO","event":"gdrive_folder_resolved","run_id":"a1b2c3d4-...","year":"2026","folder_path":"/mnt/drive/KohaExports/2026"}
{"timestamp":"2026-05-24T22:45:05Z","level":"INFO","event":"gdrive_copy_success","run_id":"a1b2c3d4-...","file_name":"export_Koha_2026-05-24_224500_a1b2c3d4.xlsx","file_path":"/mnt/drive/KohaExports/2026/export_Koha_2026-05-24_224500_a1b2c3d4.xlsx"}
{"timestamp":"2026-05-24T22:45:08Z","level":"INFO","event":"graph_email_sent","run_id":"a1b2c3d4-...","recipient":"library-target@otherdomain.com","attachment":true}
{"timestamp":"2026-05-24T22:45:09Z","level":"INFO","event":"state_committed","run_id":"a1b2c3d4-...","exported_count":14}
{"timestamp":"2026-05-24T22:45:09Z","level":"INFO","event":"export_success","run_id":"a1b2c3d4-...","duration_seconds":9.1}
```

### 6.2. Exit Codes

| Код | Значення | Умова |
|-----|----------|-------|
| `0` | Success | Усі записи успішно оброблено (включно з випадком 0 нових записів) |
| `1` | Partial failure | Деякі записи не оброблено, решта — успішно |
| `2` | Total failure | Жодного запису не оброблено, стан БД не змінено |

Exit codes дозволяють cron, systemd та Kubernetes CronJob коректно алертити
на збої без парсингу логів.

### 6.3. Prometheus-метрики (опційно)

Якщо `PUSHGATEWAY_URL` задано — метрики надсилаються після кожного запуску:

```python
# Метрики
export_records_total          # Counter — кількість успішно експортованих записів
export_duration_seconds       # Histogram — тривалість запуску
export_errors_total{stage}    # Counter — помилки за етапами:
                              #   stage: koha_fetch | marc_parse |
                              #          xlsx_gen | gdrive_copy | graph_email | db_commit
```

Якщо `PUSHGATEWAY_URL` не задано — метрики не надсилаються, застосунок працює нормально.

### 6.4. Режим аварійного відновлення

**Збій copy у Google Drive mount:**
Якщо файл не вдалося скопіювати в `/mnt/drive` — процес переходить до `except`, записує
`status='failed'`, видаляє temp-файл у `finally`. Email **не надсилається**.
При наступному запуску записи з `status='failed'` та `retry_count < MAX_RETRIES`
будуть повторно оброблені.

**Збій MS Graph після успішного copy у Google Drive mount:**
Стан має бути зафіксований як `gdrive_uploaded` з `gdrive_file_path`.
При наступному запуску/recovery модуль повторно використовує файл із тим самим
`run_id` і переходить одразу до MS Graph sendMail, без повторного copy.

**Збій після MS Graph success, але до `completed`:**
Після успішного MS Graph sendMail модуль записує `email_sent` з `email_sent_at` та, якщо
доступний, `email_message_id`. Якщо процес впав до `completed`, recovery не
надсилає лист повторно, а завершує commit у `completed`.

**Зависання у `pending`:**
Якщо процес був вбитий між фазою 1 і фазою 3 (SIGKILL, OOM), записи залишаться
у статусі `pending`. Runbook: вручну перевести їх у `failed` через CLI-команду
для активації retry-механізму.

**Temp-файли:**
Локальний XLSX завжди створюється у `/tmp` і **обов'язково** видаляється у блоці
`finally` незалежно від результату операції.

---

## 7. Узгодження з поточним репозиторієм

### 7.1. Межі інтеграції

Поточний KDV Integrator є Flask/Gunicorn сервісом для архівації одного Koha-запису
в DSpace через `/kdv/api/integrate/<biblionumber>`. Новий export-модуль має бути
окремою CLI/batch-підсистемою:

- не змінює `src/core.py` DSpace workflow;
- не змінює semantics полів `956$u`, `956$p`, `956$q`;
- читає готові `856` після успішної архівації;
- не використовує `GoogleDriveSource`, бо він read-only і призначений для PDF source;
- використовує власний `ExportDriveMountService` для atomic copy XLSX у `/mnt/drive`;
- використовує власний `GraphEmailService` для MS Graph `sendMail`.

### 7.2. Рекомендована структура

```text
src/export_module/
  __main__.py
  orchestrator.py
  config.py
  db/
  koha/
  marc/
  xlsx/
  services/
  observability/
config/marc_mapping.yaml
```

### 7.3. Рекомендований запуск

```bash
python -m src.export_module --health-check
python -m src.export_module --dry-run
python -m src.export_module --biblionumber-from 1000 --biblionumber-to 1250
python -m src.export_module --dry-run --biblionumber-from 1000 --biblionumber-to 1250
python -m src.export_module
python -m src.export_module --reset-pending <RUN_ID>
```

У Docker/Swarm бажано запускати модуль як one-off команду в образі `kdv-api`
або через окремий wrapper-скрипт, який підключає той самий `app_env_payload`
secret і змонтований rclone volume `/mnt/drive`; Google service account secret для export copy не потрібен.

---

## 8. Стратегія тестування

### 8.1. Unit-тести

**Маппінг та MARC-парсинг:**

| Тест | Сценарій |
|------|----------|
| `test_safe_extract_missing_field` | Поле відсутнє → повертає `None`, не падає |
| `test_safe_extract_missing_subfield` | Поле є, субполе відсутнє → `None` |
| `test_extract_year_264c` | `264$c = '©2024.'` → `'2024'` |
| `test_extract_year_260c_fallback` | `264` відсутнє, `260$c = 'c2021'` → `'2021'` |
| `test_get_file_url_correct_subfield_y` | `856$y = 'Файл'` → повертає URL |
| `test_get_file_url_wrong_subfield_y` | `856$y = 'Щось інше'` → `None` |
| `test_full_marc_to_xlsx_row` | Повний MARCXML запис → плоский словник колонок |

**MappingLoader:**

| Тест | Сценарій |
|------|----------|
| `test_valid_mapping_loads` | Валідний YAML → завантажується без помилок |
| `test_invalid_mapping_raises` | Невалідний YAML → `ValidationError` |
| `test_missing_required_field` | Відсутнє обов'язкове поле у YAML → `ValidationError` |

### 8.2. Mock-тести Google Drive mount

| Тест | Mock-сценарій |
|------|---------------|
| `test_year_folder_exists` | Каталог року існує → використати поточний path |
| `test_year_folder_created` | Каталог року відсутній → `os.makedirs(..., exist_ok=True)` |
| `test_copy_uses_part_then_atomic_rename` | XLSX копіюється у `.part`, потім `os.replace()` у фінальний файл |
| `test_copy_idempotent` | Файл з `run_id` вже є → copy пропущено, повернуто існуючий `file_path` |
| `test_copy_failure_cleans_part` | Помилка copy → `.part` прибрано, фінальний XLSX не створено |

### 8.3. Mock-тести Microsoft Graph Email

| Тест | Mock-сценарій |
|------|---------------|
| `test_small_file_has_attachment` | `file_size < 15 MB` → XLSX прикріплений |
| `test_large_file_link_only` | `file_size > 15 MB` → лише посилання + попередження |
| `test_graph_429_retry` | Перший виклик Graph → 429, другий → success → 2 спроби з backoff |
| `test_html_body_required_blocks` | Тіло містить: статистику, таблицю, шлях/посилання на GDrive mount |

### 8.4. Integration-тести staged-idempotency pipeline

| Тест | Сценарій |
|------|----------|
| `test_happy_path` | 5 записів → `pending` → `completed`, XLSX видалено з `/tmp` |
| `test_gdrive_copy_fail_rollback` | Помилка copy у `/mnt/drive` → статус `failed`, SQLite не змінено на `completed` |
| `test_graph_fail_after_gdrive` | MS Graph помилка після GDrive copy success → збережено `gdrive_uploaded`, retry продовжує з Graph sendMail |
| `test_process_crash_recovery` | Симуляція crash між GDrive copy та Graph sendMail → recovery reuse існуючого XLSX без повторного copy |
| `test_dry_run_no_side_effects` | `--dry-run` → жодних змін у SQLite, GDrive mount, MS Graph |
| `test_zero_new_records` | Каталог без нових записів → `exit_code=0`, жодних зовнішніх викликів |
| `test_pagination_three_pages` | Mock Koha повертає 3 сторінки по 10 записів → оброблено 30 |
| `test_biblionumber_range_export` | CLI range `1000..1250` експортує тільки records у цьому inclusive діапазоні |

---

*Кінець документа. Версія 2.2.*
