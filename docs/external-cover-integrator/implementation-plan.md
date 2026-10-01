# План імплементації: External Cover Integrator

Базується на `external-cover-integrator.md` (актуальна редакція). Номери розділів у дужках — посилання на відповідний розділ концепції.

## Загальні принципи плану

- **Послідовність фаз відповідає залежностям**, а не порядку розділів концепції: спочатку інфраструктура та Koha data model, потім core engine Integrator, потім pipeline'и, що на нього спираються.
- Кожна фаза **самодостатньо тестована** — не переходимо до наступної, доки acceptance criteria поточної не виконані.
- Матчпоінт `001 = UUIDv7` для bulk-імпорту вважається вже налаштованим (не входить у обсяг плану, лише валідується в Фазі 1).
- Усі значення середовища — тільки через `${ENV_VAR}` (розділ 1 концепції); жодних хардкоджених доменів/хостів у коді чи конфігах, що потрапляють у репозиторій.
- Спостережуваність (метрики/алерти/health-check) — окремий, останній, опційний scope (розділ 36), не блокує жодну попередню фазу.
- Definition of Done для задачі = виконані acceptance criteria **і** пройдені validation-кроки, зафіксовані як актуальний результат (лог/скріншот/вивід команди), а не "має працювати за задумом".

---

## Фаза 0 — Середовище та базова інфраструктура

**Мета:** підготувати конфігураційний контракт і мінімальний working skeleton мережі, без бізнес-логіки.

**Deliverables:**
- Задокументований контракт змінних середовища (`.env.example` або еквівалент) для всіх компонентів.
- Робочий `covers-cdn` (nginx) за Traefik + Cloudflare Tunnel, що віддає тестовий файл за `${COVERS_CDN_BASE_URL}`.
- Підтверджено: жодного хардкодженого домену/хоста в конфігах, що йдуть у репозиторій.

### Задача 0.1 — Контракт змінних середовища та секретів

**Опис:** Визначити повний список env-змінних (`KOHA_OPAC_BASE_URL`, `KOHA_STAFF_BASE_URL`, `COVERS_CDN_BASE_URL`, `MAX_RETRY_COUNT`, шлях до Google service-account credentials, Koha/DSpace API endpoints та credentials) і механізм їх постачання в кожен сервіс (Docker secrets / `.env` / vault — на розсуд деплойменту).

**Стан на 2026-09-29:** конфігураційний контракт і заглушки підготовлено та перевірено в [environment.md](environment.md) і `.env.example`. Користувач підтвердив, що наявний service account має доступ лише до цільової Drive-папки; це підтвердження користувача, а не результат нового API-запиту агента. Концептуальні `KOHA_OPAC_BASE_URL`/`KOHA_STAFF_BASE_URL` відповідають наявним `KOHA_OPAC_URL`/`KOHA_API_URL`; дублікати не вводяться. `COVERS_CDN_BASE_URL` — повний HTTPS origin без кінцевого `/`. Репозиторна частина 0.1 завершена; host-шляхи використовуються у pre-deploy, інші споживачі підключаються у відповідних фазах.

**Acceptance criteria:**
- Існує єдиний документ/файл з переліком усіх env-змінних, їх призначенням і дефолтами (де застосовно).
- Жоден сервіс не має fallback на хардкоджене значення домену чи хоста в коді.
- Google service account створено, обмежений доступом лише до цільової папки Drive.

**Validation:**
```bash
# З кореня репозиторію; перевіряється тільки публічний шаблон, без decrypt/source.
bash -n .env.example
python3 - <<'PY'
from pathlib import Path
import re

template = Path('.env.example').read_text()
contract = Path('docs/external-cover-integrator/environment.md').read_text()
keys = re.findall(r'^([A-Z][A-Z0-9_]*)=', template, re.M)
required = re.findall(r'^\| `([A-Z][A-Z0-9_]*)` \|', contract, re.M)
assert len(keys) == len(set(keys)), 'Duplicate template variables'
assert not (set(required) - set(keys)), 'Missing contract variables'
print(f'OK: {len(required)} contract variables present; no duplicate template keys')
PY
# Переглянути URL consumers без розкриття секретів: endpoints мають бути env-required.
rg -n 'KOHA_API_URL =|KOHA_OPAC_URL =|DSPACE_API_URL =|DSPACE_UI_URL =' src/config.py
```

### Задача 0.2 — Базова мережа: Traefik + Cloudflare Tunnel + covers-cdn skeleton

**Опис:** Підняти nginx (read-only, поки без реального контенту), проксі через Traefik, публічний доступ через Cloudflare Tunnel на `${COVERS_CDN_BASE_URL}`.

**Стан на 2026-09-29:** підготовлено Compose/Swarm service `covers-cdn`, nginx-конфігурацію та orchestrator wiring. Nginx працює без root, з read-only assets і filesystem, без host-портів та secrets. Hostname виводиться з `COVERS_CDN_BASE_URL`; service закріплений за локальним вузлом підготовки storage. Compose validation, цільові тести, `nginx -t` та ізольована HTTP-перевірка пройшли. За вказівкою користувача деплой виконує він сам. Публічний HTTPS, Cloudflare routing/redirect і недоступність origin ще потребують перевірки після деплою; 0.2 та Фаза 0 повністю не закриті. Процедура: [runbook.md](runbook.md).

**Виправлення першого деплою:** фактичний Swarm service не отримав короткий `tmpfs` mount, тому nginx завершувався через read-only `/tmp`. Підготовлено `volumes: type: tmpfs` і нормалізацію його розміру для Stack schema; 31 тест та ізольований запуск з mount-based tmpfs пройшли. Потрібен повторний деплой користувачем і підтвердження `/tmp` у фактичному service spec.

**Перевірка після повторного деплою:** підтверджено healthy nginx, `/tmp` tmpfs та `200 ok` на публічному HTTPS `/healthz` і через внутрішній Traefik із CDN Host header. `404` на `/` — очікувана відповідь nginx. HTTP `/healthz` повертає `200` без redirect: потрібне hostname-scoped правило HTTPS redirect у Cloudflare. Повне приймання ще очікує redirect та зовнішню перевірку origin isolation.

**Acceptance criteria:**
- Тестовий файл (`/healthz` або аналог) доступний ззовні по HTTPS через `${COVERS_CDN_BASE_URL}`.
- Origin-сервер недоступний напряму (тільки через тунель).
- TLS termination коректний (валідний сертифікат, HTTP→HTTPS redirect).

**Validation:**
```bash
curl -sI "${COVERS_CDN_BASE_URL}/healthz" | head -1        # очікується 200
curl -sI "http://${COVERS_CDN_BASE_URL#https://}/healthz" | grep -i location # очікується redirect на https
# Перевірка, що прямий доступ до origin IP не проходить (якщо є публічний IP для тесту)
```

---

## Фаза 1 — Koha data model

**Мета:** підготувати Koha-бік до інтеграції: identity, MARC-поля, sysprefs, захист полів при bulk import.

**Deliverables:**
- Підтверджено: усі активні записи мають `001 = UUIDv7`.
- Налаштовані sysprefs `CustomCoverImages`/`OPACCustomCoverImages`/`CustomCoverImagesURL`.
- `MARCOverlayRules` захищають `957`/`856` від затирання під час звичайного MARC-імпорту (окрім самого Integrator); джерельне поле `956` лишається каталогізаційним.

### Задача 1.1 — Валідація `001 = UUIDv7` та matchpoint

**Опис:** Оскільки matchpoint для bulk-імпорту вже налаштований (поза обсягом цієї фази), тут лише **валідація**, що це справді так на цільовому інстансі.

**Acceptance criteria:**
- 100% (або задокументований відсоток-виняток) біб-записів мають валідний UUIDv7 у полі `001`.
- Тестовий bulk-імпорт з відомим `001` оновлює саме очікуваний запис, а не створює дублікат.

**Validation:**
```bash
koha-mysql <instance> -e \
  "SELECT COUNT(*) AS total,
          SUM(marcxml REGEXP '<controlfield tag=\"001\">[0-9a-f-]{36}</controlfield>') AS with_uuid
   FROM biblio_metadata;"

# Тестовий matchpoint-імпорт одного відомого запису
misc/migration_tools/bulkmarcimport.pl -file test_record.mrc -match 001 -test -v
```

### Задача 1.2 — Sysprefs для CustomCoverImages

**Опис:** Налаштувати `CustomCoverImages = Show`, `OPACCustomCoverImages = Show`, `CustomCoverImagesURL = ${COVERS_CDN_BASE_URL}/{957$c}.webp` (розділ 15).

**Acceptance criteria:**
- Обидва системні preference активні.
- URL-шаблон використовує env-змінну, а не хардкоджений домен.
- Тестовий запис із заповненим `957$c` показує обкладинку в OPAC.

**Validation:**
```bash
koha-mysql <instance> -e \
  "SELECT variable, value FROM systempreferences
   WHERE variable IN ('CustomCoverImages','OPACCustomCoverImages','CustomCoverImagesURL');"

curl -s "${KOHA_OPAC_URL}/cgi-bin/koha/opac-detail.pl?biblionumber=<test_id>" \
  | grep -o "${COVERS_CDN_BASE_URL}/[a-f0-9]\+\.webp"
```

### Задача 1.3 — Захист integration-managed полів (MARCOverlayRules)

**Опис:** Налаштувати `MARCOverlayRules` так, щоб звичайний каталогізаторський bulk-імпорт не перезаписував `957`/`856`, керовані Integrator-ом (розділ 26). `956` містить джерела та статуси і не захищається як цілісне поле.

**Acceptance criteria:**
- Bulk-імпорт запису без Integrator-джерела не змінює наявні `957$c`/`856$u`, якщо вони вже виставлені Integrator-ом.
- Сам Integrator (окремий процес/user-agent) як і раніше може оновлювати ці поля.
- До застосування правила значення з legacy `956$c`/`956$3` перенесені й перевірені у `957$c`/`957$3`; масове оновлення MARC є окремою операцією.

**Validation:**
```bash
# До імпорту: зберегти поточні 957/856
koha-mysql <instance> -e "SELECT biblio_metadata.metadata FROM biblio_metadata WHERE biblionumber=<test_id>;" > before.xml

# Виконати bulk-імпорт тестового файлу без Integrator-полів
misc/migration_tools/bulkmarcimport.pl -file catalog_update.mrc -match 001 -v

# Порівняти 957/856 до і після — мають лишитись незмінними
koha-mysql <instance> -e "SELECT biblio_metadata.metadata FROM biblio_metadata WHERE biblionumber=<test_id>;" > after.xml
diff <(grep -A2 'tag="957"' before.xml) <(grep -A2 'tag="957"' after.xml)
```

---

## Фаза 2 — Integrator: core engine

**Мета:** побудувати перевикористовуваний "двигун" Integrator — state DB, статуси, retry/backoff/`max_retry_count` — **без** зовнішніх інтеграцій (Drive/Koha/DSpace тут ще mock).

**Deliverables:**
- SQLite state DB розгорнута в режимі `WAL`, схема відповідає розділу 19.
- Реалізований генерик-модуль стану `ok/pending/failed` + `retry_count` + `${MAX_RETRY_COUNT}` cutoff (розділ 19, 22), покритий unit-тестами.

### Задача 2.1 — Схема state DB

**Опис:** Створити SQLite-схему з полями `record_uid, cover_source_id, cover_source_sha256, cover_asset_sha256, file_source_id, file_source_sha256, dspace_item_uuid, dspace_bitstream_uuid, status, retry_count, updated_at` (розділ 19).

**Стан на 2026-09-30:** репозиторну частину 2.1 реалізовано у `src/cover_state/schema.py`.
Перевикористовується `MigrationManager` з export-модуля, але файл БД окремий —
`${COVER_STATE_DB_PATH}`; `${EXPORT_DB_PATH}` лишається тільки для export.
Міграція транзакційна та ідемпотентна, фіксує `PRAGMA user_version=1` і перевіряє WAL.
Унікальний індекс `record_uid` створюється через `PRIMARY KEY`; `status` має окремий індекс.
Міграцію перевірено на тимчасових БД: 23 тести schema/export regression пройшли;
CLI підтвердив `wal`, версію 1, 11 колонок та обидва індекси.
Після ручного редеплою користувача 2026-09-30 read-only перевірка стека
`kdv_integrator_event` підтвердила фактичний read-write mount state-директорії в API
на вузлі `pinokew`, збіг коду schema/runner з репозиторієм і HTTP 200 для API
health/readiness, optimizer readiness та CDN health. Dev/prod мітка середовища
не визначена. На момент першої перевірки файл `state.db` був відсутній.
Міграцію підключено до API entrypoint після завантаження runtime env
та перед запуском сервера; помилка міграції зупиняє API і health-gate деплою.
Після наступного деплою користувач виконав read-only перевірку в API-контейнері
та надав вивід 2026-09-30: усі три KDV services мають `1/1`, persistent state bind
має `RW=true`, `/data/kdv_cover_state/state.db` існує, `journal_mode=wal`,
`user_version=1`. Підтверджено всі 11 колонок, defaults і CHECK constraints,
унікальний primary-key індекс `record_uid` та `idx_records_status`.
Runtime-приймання 2.1 завершено; задача 2.2 залишається наступною.

**Acceptance criteria:**
- Схема застосована через міграцію (не ручний SQL за замовчуванням у продакшені).
- `PRAGMA journal_mode` повертає `wal`.
- Індекс на `record_uid` (унікальний) та на `status`.

**Validation:**
```bash
# У вибраному середовищі; спочатку перевірити persistent mount директорії БД.
python -m src.cover_state.schema  # використовує COVER_STATE_DB_PATH, без зовнішніх API
sqlite3 "$COVER_STATE_DB_PATH" "PRAGMA journal_mode;"  # очікується: wal
sqlite3 "$COVER_STATE_DB_PATH" ".schema records"
sqlite3 "$COVER_STATE_DB_PATH" "PRAGMA index_list(records);"
sqlite3 "$COVER_STATE_DB_PATH" "SELECT sql FROM sqlite_master WHERE type='index';"

# Локальна перевірка на тимчасових БД, без production-змін.
.venv/bin/python -m pytest -q tests/test_cover_state_schema.py tests/test_export_schema.py tests/test_export_repository.py tests/test_export_cli.py
```

### Задача 2.2 — State machine: `ok`/`pending`/`failed` + `${MAX_RETRY_COUNT}`

**Опис:** Реалізувати модуль, що приймає результат кроку (success/error), оновлює `status`/`retry_count`, і після досягнення `${MAX_RETRY_COUNT}` виключає запис з активного retry-скану (розділ 19, 22).

**Стан на 2026-09-30:** задачу 2.2 реалізовано та перевірено у
`src/cover_state/state_machine.py`. `StateMachine` використовує окрему WAL DB
та обов'язковий позитивний `${MAX_RETRY_COUNT}` без runtime-default.
`mark_pending()` зберігає retries/resources і відхиляє вичерпані записи;
`record_result()` атомарно фіксує успіх або помилку, може зберігати `pending`
для частково виконаної роботи, але на cutoff переводить у `failed`.
`get_retry_eligible()` виключає `ok` та записи на ліміті й застосовує backoff
1, 2, 4, ... секунд від UTC `updated_at`. Reset до нуля через метод або прямий
SQL повертає запис у вибірку одразу, зберігаючи status/resources.
39 цільових state/schema/export тестів пройшли на тимчасових БД, включно з
persistence, cutoff, backoff, reset та атомарними concurrent increments.
Вибірка не резервує записи: майбутній pipeline має використовувати один
writer-процес або per-record lock на весь цикл. API/Robot core тепер використовує
спільний filesystem workflow lock; metadata gate підключено до цього шляху;
runtime-схема 2.1 прийнята за наданим користувачем виводом. Фаза 2 завершена.

**Acceptance criteria:**
- Успішний цикл: `status → ok`, `retry_count → 0`.
- Помилка: `status → failed` (або лишається `pending`), `retry_count += 1`.
- Досягнення `${MAX_RETRY_COUNT}`: запис не потрапляє у вибірку "до обробки" наступного запуску.
- Ручний reset `retry_count = 0` повертає запис у обробку.

**Validation:**
```bash
# Тимчасові БД; без зовнішніх API та runtime-змін.
# Тести включають N помилок до cutoff, повторне відкриття БД, прямий SQL reset,
# успішне завершення, partial/pending, backoff та concurrent increments.
.venv/bin/python -m pytest -q tests/test_state_machine.py tests/test_cover_state_schema.py tests/test_export_schema.py tests/test_export_repository.py tests/test_export_cli.py
```

---

## Фаза 3 — Google Drive dirty-check

**Мета:** підключити реальний Google Drive API до вже готового core engine.

**Deliverables:**
- Fast dirty-check за File ID (розділ 5).
- Metadata + `sha256Checksum` fetch з коректною обробкою відсутнього checksum як permanent failure (розділ 6).

### Задача 3.1 — Fast dirty-check за Drive File ID

**Опис:** Порівняння `incoming_file_id` зі `stored_file_id`; NO-OP без жодного додаткового API-виклику, якщо збігається.

**Стан на 2026-09-30:** задачу 3.1 реалізовано через read-only
`StateMachine.check_source(record_uid, incoming_file_id, source="cover"|"file")`.
Для витягування ID з URL перевикористовується наявний `GoogleDriveUrlParser`;
gate викликається до `mark_pending` та створення/виклику Drive-клієнта.
Незмінений ID + `ok` → `noop`; новий/змінений ID → `needs_sha_check`;
незмінений ID + `pending/failed` → `resume`. Відсутнє джерело → `no_source`,
без видалення даних. Cover/PDF перевіряються окремо; retry cutoff/backoff
залишаються обов'язковими для retry callers. 38 dirty-check/state/schema тестів
пройшли на тимчасових БД; 100 незмінених записів дали 200 NO-OP для cover/PDF,
нуль mock Drive/downstream calls і незмінну state DB. Приймання 3.1 завершено.
Metadata/SHA-перевірка належить задачі 3.2; API/Robot core тепер підключено
до gate. Локальні тести не є доказом деплою цієї зміни.

**Acceptance criteria:**
- Незмінений File ID + `status=ok` не спричиняє жодного виклику Google Drive API (перевіряється лічильником викликів/логом).
- Змінений File ID коректно позначається як "потребує SHA-перевірки".

**Validation:**
```bash
# Тимчасова SQLite DB; 100 записів з незміненими cover/PDF IDs та status=ok.
# Перевіряє нуль mock Drive/downstream calls і незмінність усіх state-полів;
# також нові/змінені IDs, pending/failed, empty source і наявний URL parser.
.venv/bin/python -m pytest -q tests/test_cover_dirty_check.py tests/test_state_machine.py tests/test_cover_state_schema.py
```

### Задача 3.2 — Metadata + sha256Checksum, обробка відсутнього checksum

**Опис:** При зміні File ID отримати metadata, порівняти `sha256Checksum`. Якщо поле відсутнє (Google Doc/shortcut) — `status = failed`, без спроби подальшої обробки (розділ 6).

**Стан на 2026-09-30:** репозиторну частину 3.2 реалізовано в
`src/cover_state/drive.py:check_drive_metadata()`. Наявний `GoogleDriveSource`
отримує metadata з явним `sha256Checksum`; клієнт створюється тільки після
fast gate та retry eligibility. Однаковий SHA у підтвердженому `ok` циклі
оновлює лише source ID/timestamp; новий SHA повертає `resource_changed` та
`pending`, без передчасного збереження нових source ID/hash. Незавершені цикли
не переводяться в `ok` лише через однаковий SHA. Відсутній/порожній/некоректний
checksum → permanent failure (`failed`, retries щонайменше на ліміті) та
безпечний лог; мережеві/client помилки → один increment і retry/backoff.
Версія схеми лишається 1; manual reset повертає permanent failure у обробку.
Виправлено передачу resource key через HTTP header у metadata/download requests.
103 пов'язаних тести пройшли, 15 optimizer-тестів виключено. Повний цільовий
набір дав 115 passed / 3 failed: наявні optimizer-тести використовують DPI
250/300, яких немає в поточному allowlist 100–150; ці файли не змінювалися.
Реальний SDK перевірено offline з mock transport. Після деплою користувач надав
вивід container smoke 2026-09-30: deployed-код збігається з репозиторієм;
для наданого binary-файла live metadata повернули валідний SHA, початкове
рішення — `resource_changed/pending`. Порівняння з тестовим підтвердженим SHA
дало `same_content`, оновлення source ID та `ok`; наступний виклик — `noop`
з нулем додаткових Drive calls. За дозволом користувача Doc/shortcut перевірено
моками: відсутній checksum дав `failed` та cutoff; mocked timeout дав
`failed/retry_count=1`. Тимчасову state DB видалено. Приймання 3.2 завершено
для metadata gate; live Doc/shortcut не перевірялися. Автоматичний виклик gate
на момент smoke не було підключено; smoke викликав модуль безпосередньо.
Тепер gate підключено в спільному API/Robot core: `001` має бути UUIDv7,
cycle lock серіалізує configured runs, retry eligibility перевіряється один раз
на цикл, metadata перевикористовується при download, байти перевіряються за SHA.
Повністю незмінені Drive-only цикли повертають `noop`, cutoff/backoff — `deferred`.
Зміна лише cover пропускає незмінений PDF та DSpace; local/additional sources
зберігають свій processing path. Source ID/SHA та `ok` фіксуються атомарно лише
після підтверджених required steps і `Koha write-back=True`. Помилки downstream
збільшують retries один раз. Changed/new PDF + лише `linked_existing` у DSpace
відхиляється: поточний workflow не замінює bitstream; replacement/recovery
належать DSpace-фазі. JPEG/CGI pipeline збережено; WebP/CDN — наступні задачі.
122 пов'язаних тести пройшли, 17 виключено (15 optimizer та дві наявні API DPI
перевірки, що не відповідають поточному allowlist). Тести включають реальний
authenticated route → core зі stub клієнтами, NO-OP, same-content, cover-only,
checksum/write-back failures, two-source retry та concurrent requests.
Користувач виконав редеплой та надав smoke-вивід 2026-09-30: deployed hashes
збігаються; активний API health/readiness повертає 200; persistent DB має
WAL/version 1. В окремому процесі deployed-контейнера Flask test client
виконав route → реальну background task → gate → polling з тимчасовою БД
і stub Koha/DSpace: `noop` з нулем Drive calls; same-content порівняння з
live SHA оновило лише тимчасовий source ID без downstream work. Cleanup пройшов.
Підключення gate підтверджено в deployed runtime для цього ізольованого шляху.
Integration POST до активного Gunicorn, live Koha/DSpace writes, Robot runtime
та changed-content write-back цим smoke не перевірялися.
Dev/prod середовище не визначено.

**Acceptance criteria:**
- Реальний binary-файл: SHA коректно отримано і порівняно.
- Google Doc/shortcut як source: запис переходить у `status = failed` з зрозумілою причиною в лозі, а не трактується як "без змін".
- Помилка мережі/quota під час цього кроку: `retry_count += 1`, а не NO-OP (розділ 22).

**Validation:**
```bash
# Тимчасові БД та mock Drive; SDK requests будуються offline без credentials.
.venv/bin/python -m pytest -q tests/test_cover_drive_metadata.py tests/test_cover_dirty_check.py tests/test_state_machine.py tests/test_cover_state_schema.py tests/test_services.py tests/test_core.py -k 'not optimizer'
# Live acceptance у вибраному середовищі: викликати check_drive_metadata()
# з parsed ID/resource key та окремою тестовою state DB для binary/Doc/shortcut.
# Підтвердити binary SHA, failed/cutoff без checksum та safe log; не виводити
# credentials, resource keys або API exception payloads.
```

---

## Фаза 4 — Cover pipeline + Koha write-back

**Мета:** повний цикл: зміна в Drive → WebP → content-addressed asset → `957$c` у Koha.

**Deliverables:**
- Download → normalize → WebP (розділ 9, 10).
- Atomic publish у content-addressed сховище (розділ 11, 14).
- Запис `957$c` у Koha через `pending → ok` патерн (розділ 15, 22).

### Задача 4.1 — Download, normalize, WebP

**Опис:** Завантажити файл із Drive, привести до WebP (якість ~82, розмір за конвенцією розділу 10).

**Стан на 2026-09-30:** етап 4.1 реалізовано у `src/services/cover_pipeline.py`.
`download_and_normalize()` повторно використовує `SourceResolver`/`GoogleDriveSource`
та наявну read-only автентифікацію; приймає metadata від gate або отримує її сам.
Перед декодуванням перевіряє SHA-256 завантажених байтів. Pillow застосовує EXIF
orientation, RGB, ширину до 600 px зі збереженням пропорцій без upscale, видаляє
metadata і зберігає WebP quality 82. Повертає окремі SHA джерела та готового asset.
Наявний API/Robot core використовує спільну перевірку download SHA; WebP етап
доступний як функція/CLI. Atomic publish реалізовано у задачі 4.2; підключення
WebP до Koha workflow залишається задачею 4.3. Локальні тести використовують справжні зображення
і stub Drive; live Drive download нового етапу ще не перевірено.

**Acceptance criteria:**
- Вихідний WebP валідний і відповідає заданим розмірам/якості.
- Локально порахований SHA завантаженого файла збігається з `sha256Checksum` із Drive (захист від пошкодженого download).

**Validation:**
```bash
# Local validation with temporary files and a stub Drive client.
.venv/bin/python -m pytest -q tests/test_cover_pipeline.py
# In an identified environment with existing read-only Drive configuration:
python -m src.services.cover_pipeline --source "$COVER_SOURCE_URL" --output /tmp/test-cover.webp
file /tmp/test-cover.webp  # очікується: RIFF...WebP
# CLI returns source_sha256 and cover_asset_sha256; source SHA is checked before decode.
```

### Задача 4.2 — Content-addressed atomic publish

**Опис:** Ім'я файла = SHA-256 вмісту; публікація через `os.replace()` (temp → final), без часткових/пошкоджених файлів у сховищі.

**Стан на 2026-09-30:** `publish_cover()` реалізовано у
`src/services/cover_pipeline.py`; CLI етапу 4.1 підтримує `--publish` і
`--storage-path` (або обов'язковий `COVERS_STORAGE_PATH`). Функція перевіряє WebP
та очікуваний asset SHA, наявність підготовлених несімлінкових директорій і
спільний filesystem `.incoming`/`assets`. Публікації серіалізовано через
`.incoming/.publish.lock`: унікальний `publish-*.tmp`, flush/fsync, mode `0644`,
`os.replace()` у `assets/<sha256>.webp`, fsync директорій. Ідентичний наявний asset
не перезаписується; пошкоджений asset або destination symlink спричиняє помилку.
Звичайний збій прибирає власний temp; після `SIGKILL` приватний temp може лишитися
до наступної публікації, яка прибере лише зарезервовані `publish-*.tmp` під lock.
У публічній `assets` часткових файлів немає. Обидва Compose-файли монтують cover
root read-write в API; Swarm API і CDN закріплено за тим самим підготовленим вузлом.
Локальні тести перевіряють dedup/concurrency, помилки та справжній `SIGKILL` у трьох
точках. Після редеплою 2026-09-30 user-output і пряма перевірка агента підтвердили
три services `1/1`, спільний вузол `pinokew`, read-write cover root у API і
read-only assets у CDN, збіг deployed SHA коду, права `0755/0755/0700` і спільний
device директорій. Активний Gunicorn має `COVERS_STORAGE_PATH`; початкова помилка
smoke була через окреме оточення `docker exec`, яке не успадковує sourced payload.
Виправлена read-only перевірка читає лише потрібні несекретні змінні PID 1.
Deployed publisher пройшов normalize/publish/dedup/SHA/mode/inode/mtime smoke
на окремому `/tmp` storage; cleanup підтверджено. CDN health внутрішньо та через
HTTPS (`curl`/`requests`) повернув `200 ok`; HTTPS-запит `urllib` отримав `403`.
Окремий synthetic WebP опубліковано у справжній mounted `assets`: SHA/name
збіглися, mode `0644`, повторна публікація зберегла inode/mtime. Внутрішній і
публічний CDN повернули однаковий вміст із `image/webp` та
`public, max-age=31536000, immutable`. Тестовий asset залишено у сховищі; його
CDN-кеш може зберігатися рік. Runtime-публікацію і CDN-віддачу підтверджено для
synthetic asset; Koha/source-record workflow не перевірявся. Dev/prod не
визначено; Koha write-back — задача 4.3.

**Acceptance criteria:**
- Два записи з однаковим вмістом обкладинки фізично використовують один файл (dedup).
- Примусове переривання процесу під час публікації не залишає пошкоджений файл за фінальним іменем.

**Validation:**
```bash
# Temporary storage only; includes SIGKILL mid-write and before/after rename.
.venv/bin/python -m pytest -q tests/test_cover_publish.py tests/test_covers_cdn.py
# In an identified environment after confirming the writer/CDN mounts:
python -m src.services.cover_pipeline --source "$COVER_SOURCE_URL" \
  --output /tmp/test-cover.webp --publish
# Result: file=${COVERS_STORAGE_PATH}/assets/<cover_asset_sha256>.webp.
# A second identical publication retains the same file/inode, with no final .tmp.
# SIGKILL may leave a private .incoming/publish-*.tmp; retry cleans it under lock.
```

### Задача 4.3 — Koha `957$c` write-back через `pending → ok`

**Опис:** Перед записом у Koha — `status = pending` у state DB; після підтвердженого успіху REST API — `status = ok` (розділ 15, 22).

**Стан на 2026-10-01:** репозиторну частину реалізовано у спільному
`src/core.py:process_integration_logic()` для API/Robot. Explicit Drive `956$p`
використовує download → normalize → WebP → atomic publish і записує SHA у `957$c`,
без CGI upload. Cover-only зміна не обробляє незмінний PDF. Старі confirmed Drive
covers без asset SHA перебудовуються у WebP. Для Drive PDF fallback тепер діє
задача 5.1; локальні джерела зберігають legacy шлях.

У cover SQLite DB додано idempotent `pending_cover_work` (версія 1 і 11 колонок
`records` збережені). Після publish атомарно зберігаються asset SHA у pending record
та checkpoint: fingerprint input IDs/collection/additional inputs/DPI/options,
source IDs/SHA, потреба PDF work і завершений результат DSpace. Підтверджені source
колонки `records` до успіху не змінюються. Повторний eligible запуск із тими самими
inputs перевіряє immutable asset і повторює лише Koha write-back, якщо PDF робота
вже завершена. Checkpoint переживає повторне відкриття DB; зміна inputs робить його
непридатним для цього запиту. Retry backoff/cutoff і ручний reset збережені.

Після true Koha PUT виконується MARC read-back: `001` і `957$c`, а для PDF циклу
також `957$3` та потрібні `856$u`. Лише після цього `complete_cycle()` атомарно
фіксує source IDs/SHA, UUIDs, `ok`/retry=0 і видаляє checkpoint. Збій PUT/read-back
залишає `pending` до cutoff; asset/checkpoint зберігаються. Збій DSpace також не
підтверджує цикл. Crash між DSpace upload і збереженням його результату все ще
потребує recovery Фази 7; наявний fail-closed guard не дозволяє прийняти простий
`linked_existing` за replacement нового/зміненого PDF.

Локальні перевірки: 152 passed, 14 deselected (непов'язані optimizer/DPI cases,
включно з існуючим Robot payload test, який передає 200 DPI поза allowlist 100–150).
Тести перевіряють restart/retry без повторного download/normalize/DSpace, source
commit після read-back, cutoff/reset, input invalidation, corrupted asset,
additive migration і legacy regressions.

**Приймання 2026-10-01:** користувач запустив тестовий Koha запис із Drive PNG
у `956$p`. Логи показали `resource_changed`, download і успішне завершення задачі.
У MARC `957$c` записано SHA `59a0918a906ac75993065e6877e312208142e38945ba5bb1b06134283c700bc6`;
read-only перевірка у контейнері підтвердила `status=ok`, `retry_count=0`,
відсутність checkpoint і byte SHA опублікованого WebP, який збігається з MARC.
Користувач підтвердив показ обкладинки в інтерфейсі Koha. Позитивний live шлях
Фази 4 прийнято; retry failure і NO-OP підтверджені локальними тестами, а не цим
live запуском. Тип оточення (dev/prod) не визначено.

**Acceptance criteria:**
- Успішний цикл: OPAC показує нову обкладинку, `status = ok`, `stored_file_id` оновлено.
- Симуляція збою Koha REST API після публікації asset: `status` лишається `pending`, повторний запуск **не** перезавантажує/не переконвертує asset повторно, а лише повторює запис у Koha.

**Validation:**
```bash
# Temporary DB/storage and stub Koha/Drive/DSpace; includes write-back failure/restart.
.venv/bin/python -m pytest -q tests/test_api_drive_gate.py tests/test_cover_state_schema.py
# After redeployment, in an identified environment and an approved test record:
# use the existing authenticated integration API/Koha UI, then poll its task.
# Compare MARC 957$c with records.cover_asset_sha256 and the CDN SHA-named WebP.
# Confirm records.status=ok, retry_count=0 and no pending_cover_work row.
# Repeat the unchanged request: noop, zero Drive/downstream processing.
# OPAC: CustomCoverImagesURL=<configured HTTPS CDN origin>/{957$c}.webp;
# OPACCustomCoverImages=Show. Check the rendered image URL and actual HTTP bytes.
# Local failure tests reopen SQLite and assert no repeated download/normalize/DSpace.
```

---

## Фаза 5 — Cover із PDF

**Мета:** автогенерація обкладинки з першої сторінки PDF, якщо окремого cover-джерела немає (розділ 13).

**Deliverables:** записи лише з `file_source` (PDF) отримують згенеровану обкладинку тим самим content-addressed шляхом.

### Задача 5.1 — Генерація cover з першої сторінки PDF

**Опис:** Рендер першої сторінки PDF → WebP → той самий publish/write-back pipeline, що й у Фазі 4.

**Стан на 2026-10-01:** для Drive PDF у `956$u` без окремого `956$p` shared
API/Robot core перевіряє source SHA, рендерить першу видиму сторінку з CropBox
(Poppler, 150 DPI, 15 s timeout) і застосовує той самий WebP policy 600 px/quality
82. Далі використовує існуючі atomic publish, `pending_cover_work`, Koha `957$c`
write-back/read-back і `complete_cycle()`. Під час retry з відповідним checkpoint
не завантажує і не рендерить PDF повторно; на незмінному підтвердженому PDF діє
NO-OP, а старий підтверджений PDF cover без asset SHA перебудовується. Некоректний,
порожній або зашифрований PDF із неможливою першою сторінкою переводить лише свій
запис у permanent `failed` з cutoff; наступні задачі виконуються незалежно.
Тимчасова помилка рендеру зберігає звичайний retry/backoff. Local path продовжує
legacy CGI шлях; зміни локальних джерел не відстежуються Drive state DB.

**Live перевірка 2026-10-01:** користувач виправив неправильну DSpace collection
для Koha запису 72. Після цього Item створився, PDF bitstream завантажився, задача
завершилась успішно; користувач підтвердив коректні поля Koha та показ обкладинки.
Для запису 71 повторний запуск відновив видалене `856$u`, під'єднавши вже існуючий
DSpace Item. Заміна `956$u` на інший PDF для запису з існуючим Item завершилась
очікуваною fail-closed помилкою `Changed Drive PDF requires DSpace bitstream
replacement`; безпечна заміна bitstream — задача 7.2. Оточення не ідентифіковане.

**Acceptance criteria:**
- Обкладинка коректно згенерована для тестового PDF (перевірка розміру/формату).
- Помилка рендерингу (пошкоджений/захищений паролем PDF) не блокує решту черги — окремий запис переходить у `status = failed`, інші обробляються.

**Validation:**
```bash
# Local temporary DB/storage, real PDF+Poppler, stub Drive/Koha/DSpace.
.venv/bin/python -m pytest -q tests/test_cover_pipeline.py tests/test_api_drive_gate.py
# After user deployment in an identified environment, use an approved UUIDv7
# Koha record with Drive PDF in 956$u and no 956$p. Poll the existing API task.
# Verify MARC 957$c == records.cover_asset_sha256 == SHA of assets/<sha>.webp,
# status=ok, retries=0, no checkpoint, CDN/Koha display, and unchanged NO-OP.
# Use isolated tests for corrupt/encrypted PDF; avoid deliberately poisoning a
# live record. Changed PDF replacement is covered by Task 7.2; runtime smoke is
# documented in the Task 7.2 acceptance section below.
```

---

## Фаза 6 — Static cover service (production-grade)

**Мета:** перевести `covers-cdn` з тестового skeleton (Фаза 0) у продакшн-режим.

**Deliverables:**
- Кешування `Cache-Control: public, max-age=31536000, immutable` (розділ 18).
- Read-only доступ ззовні; запис лише від Integrator-процесу.

### Задача 6.1 — Immutable-кешування та read-only serving

**Опис:** Виставити правильні cache-заголовки, заборонити будь-які методи запису через публічний вхід.

**Стан на 2026-10-01:** реалізація є в `config/covers-cdn/nginx.conf` та
Compose/Swarm конфігураціях. nginx приймає лише GET/HEAD, віддає плоскі файли
`.webp` з lowercase SHA-256 у назві, для інших шляхів повертає 404, а для asset
виставляє `public, max-age=31536000, immutable`. CDN працює як `nginx` з read-only
root і mount assets, скинутими capabilities, без secrets та опублікованих портів.
Попередня перевірка на розгорнутому synthetic asset підтвердила публічний HTTPS
200, однакові bytes, `image/webp` та immutable cache header; користувач згодом
підтвердив показ реальної обкладинки в Koha. 2026-10-01 користувач надав
публічні заголовки для asset URL: HTTP/2 200, `image/webp`, довжина 16974,
`Cache-Control: public, max-age=31536000, immutable`, `cf-cache-status: HIT`.
Тією ж перевіркою PUT/POST/DELETE повернули 405. Acceptance criteria задачі 6.1
виконані. Оточення не ідентифіковане; перевірки redirect та origin isolation
залишаються частиною Фази 0.

**Acceptance criteria:**
- `GET` на існуючий asset повертає `Cache-Control: public, max-age=31536000, immutable`.
- `PUT`/`POST`/`DELETE` через публічний домен — заборонені (405/403).

**Validation:**
```bash
asset_url="${COVERS_CDN_BASE_URL}/<known-existing-sha>.webp"
curl --fail --silent --show-error --head "$asset_url"  # 200 + immutable Cache-Control
for method in PUT POST DELETE; do
  curl --silent --show-error --request "$method" --output /dev/null \
    --write-out "$method %{http_code}\n" "$asset_url"  # кожен: 403 або 405
done
```

---

## Фаза 7 — DSpace pipeline

**Мета:** аналогічний pipeline для повних файлів через DSpace, з безпечною заміною bitstream і UID-resolver.

**Deliverables:**
- DSpace item/bitstream pipeline (розділ 20).
- Безпечна заміна PDF (upload → verify → swap → delete old) через `pending → ok` (розділ 21, 22).
- UID → Koha resolver на env-змінних доменах (розділ 24).

### Задача 7.1 — DSpace item/bitstream pipeline

**Опис:** Створення/оновлення DSpace item та bitstream за зміненим `file_source`.

Реалізовано в репозиторії: DSpace Item ідентифікується за MARC `001` у
`koha.uid`; при першій обробці UUID записується в metadata. Повторний
запуск знаходить той самий Item і зберігає Handle. Якщо попередній запуск
створив Item, але завершився до першого bitstream, повтор використовує Item і
довантажує PDF. Пошук за наявним Koha `biblionumber` лишається сумісним шляхом
для старих Items. Заміна вже наявного bitstream належить задачі 7.2.
Якщо source незмінний, але відсутній один із DSpace links, fast-path читає
наявний Item і ORIGINAL bitstream за збереженими UUID та переписує пару `856$u`:
URL завантаження PDF і Handle URL. Обидва значення перевіряються read-back;
інші DSpace/Koha операції, включно з повторним завантаженням PDF, не запускаються.

Runtime smoke 2026-10-01 підтвердив створення Item, запис MARC `001` у
`koha.uid`, завантаження PDF і успішне завершення task за наданим користувачем
логом. Користувач також підтвердив успішний повторний пошук того самого Item і
незмінність Handle.

**Acceptance criteria:**
- Новий item у DSpace створюється з коректними метаданими, пов'язаними з `record_uid` (UUIDv7).
- Handle стабільний і не змінюється при повторних запусках без реальної зміни джерела.

Обидва acceptance criteria підтверджено runtime свідченнями користувача та
локальними тестами повторного використання Item/Handle. Відновлення видаленого
обох DSpace `856$u` links при незмінному source покрите локальним тестом;
runtime перевірка цього сценарію очікує redeploy. Заміна наявного bitstream
лишається задачею 7.2.

**Validation:**
```bash
record_uid="<approved-uuidv7>"
curl --get --silent --show-error "${DSPACE_API_URL}/discover/search/objects" \
  -H "Authorization: Bearer ${DSPACE_API_TOKEN}" \
  --data-urlencode "query=koha.uid:${record_uid}" \
  --data-urlencode "dsoType=item" --data-urlencode "size=2" \
  | jq '._embedded.searchResults.page.totalElements'  # очікується 1
```

### Задача 7.2 — Безпечна заміна PDF (upload → verify → swap)

**Стан на 2026-10-01:** Реалізовано для Drive PDF, що змінився в уже наявного DSpace Item. Workflow завантажує новий bitstream, звіряє його розмір і checksum із локальним файлом, призначає його primary bitstream у ORIGINAL bundle та перевіряє це читанням назад. Старий bitstream зберігається, доки Koha не підтвердить обидва `856$u` (посилання на файл і Handle) через read-back. Після redeploy користувач двічі підтвердив оновлення PDF/Koha, але старий bitstream залишився; у другому успішному task log немає повідомлення про підтверджене видалення. У коді є GET-перевірка після DELETE, тож видалення або не запускалося (старий primary UUID не визначився), або контейнер виконував старий образ. Додано логи вибору primary та cleanup UUID для розрізнення; runtime acceptance видалення залишається невирішеним.

Повний результат DSpace записується у наявний `pending_cover_work` до Koha write-back. Якщо Koha update/read-back не вдався, checkpoint зберігає UUID нового й старого bitstream; повтор використовує його без повторного upload. Старий bitstream видаляється після успішного read-back посилань; видалення ідемпотентне. Локальні тести моделюють збій Koha update та успішне відновлення. Runtime acceptance після redeploy ще очікує перевірки користувачем.

**Acceptance criteria:**
- Старий bitstream доступний до перевірки нового upload і лишається доступним, поки обидва Koha links не підтверджені read-back.
- Симуляція збою Koha write-back: checkpoint зберігає новий bitstream, запис лишається незавершеним, старий bitstream **не** видалений; повтор не завантажує новий bitstream удруге.
- Після успішного Koha read-back новий bitstream є primary, старий видалений, state збережено як `ok`.

**Validation:**
```bash
# Repository behavior is covered by tests/test_api_drive_gate.py and
# tests/test_core.py. After deployment, repeat against an approved test record:
# fail Koha link write-back, verify old+new bitstreams and pending checkpoint,
# retry, then verify both Koha 856$u values, new primary bitstream and old delete.
```

### Задача 7.3 — UID → Koha resolver

**Опис:** Перевірити штатний Koha search як resolver для `control-number` (розділ 24), без окремого resolver-сервісу.

**Acceptance criteria:**
- Пошук за UUIDv7 через `${KOHA_OPAC_BASE_URL}` повертає рівно один, правильний запис.
- Той самий запит через `${KOHA_STAFF_BASE_URL}` працює для staff-інтерфейсу.

**Validation:**
```bash
curl -s "${KOHA_OPAC_URL}/cgi-bin/koha/opac-search.pl?q=control-number:<uuid>" \
  | grep -c "opac-detail.pl?biblionumber="   # очікується 1
```

---

## Фаза 8 — Shared covers, ручний override, rollback

**Мета:** підтвердити dedup-поведінку та впровадити задокументований "костиль" ручної заміни shared cover разом з rollback-процедурою.

**Deliverables:**
- Підтверджена dedup-поведінка (розділ 27).
- Робочий і протестований runbook аварійного override (backup → заміна → purge → лог) — розділ 28.
- Протестована rollback-процедура (розділ 29).

### Задача 8.1 — Dedup shared covers

**Опис:** Перевірити, що записи з однаковим asset SHA використовують один фізичний файл і що зміна source одного запису не зачіпає інші.

**Acceptance criteria:**
- N записів з однаковим cover → 1 файл у сховищі.
- Зміна source одного з них не змінює `957$c` інших.

**Validation:**
```bash
sqlite3 state.db "SELECT cover_asset_sha256, COUNT(*) FROM records GROUP BY cover_asset_sha256 HAVING COUNT(*)>1;"
ls /data/koha-covers/assets/<shared_sha>.webp
```

### Задача 8.2 — Runbook ручного override shared cover

**Опис:** Реалізувати та задокументувати покрокову процедуру: backup старого файла → заміна вмісту за тим самим іменем → обов'язковий cache purge конкретного URL → журналювання (`asset_sha, overridden_at, operator, reason`) (розділ 28).

**Acceptance criteria:**
- Скрипт/процедура відмовляється виконувати заміну без попереднього backup.
- Після заміни: CDN обов'язково повертає новий вміст (а не закешовану стару версію).
- Подія override записана окремо від автоматичного pipeline-логу.

**Validation:**
```bash
./scripts/manual_cover_override.sh <sha> new_cover.webp --operator "ivan" --reason "wrong scan uploaded"
ls /data/koha-covers/assets/<sha>.webp.bak.*        # backup створено
curl -s "${COVERS_CDN_BASE_URL}/<sha>.webp" | sha256sum                # має збігатись з новим файлом
grep "<sha>" overrides.log | tail -1                                          # запис у лозі override
```

### Задача 8.3 — Rollback

**Опис:** Rollback запису до попереднього asset SHA через журнал (`record_uid, old_asset_sha, new_asset_sha, changed_at, run_id`) (розділ 29).

**Acceptance criteria:**
- Rollback повертає `957$c` до попереднього значення.
- Старий asset фізично доступний у сховищі (не видалений GC на момент rollback-тесту).

**Validation:**
```bash
python -m integrator.rollback --record test-uid-1 --to-run <run_id>
sqlite3 state.db "SELECT cover_asset_sha256 FROM records WHERE record_uid='test-uid-1';"
curl -sI "${COVERS_CDN_BASE_URL}/$(sqlite3 state.db "SELECT cover_asset_sha256 FROM records WHERE record_uid='test-uid-1';").webp" | head -1
```

---

## Фаза 9 — Garbage Collection та видалення ресурсу

**Мета:** прибирання застарілих assets без reference counting; політика видалення ресурсу (розділ 30, 31).

**Deliverables:**
- Запланована GC-задача, що видаляє непотрібні файли старші за retention period.
- Задокументована й реалізована поведінка при видаленні source-посилання.

### Задача 9.1 — GC job

**Опис:** Періодична задача: файл — кандидат на видалення, якщо жоден запис на нього не посилається (`SELECT`, без окремого reference-лічильника) і він старший за `retention period` (розділ 30).

**Acceptance criteria:**
- Файли без жодного посилання та старші retention period видаляються.
- Файли, на які є хоч одне посилання (навіть недавнє), не видаляються, незалежно від віку.

**Validation:**
```bash
python -m integrator.gc --dry-run
python -m integrator.gc --apply
sqlite3 state.db "SELECT cover_asset_sha256 FROM records WHERE cover_asset_sha256='<orphan_sha>';"  # очікується 0 рядків
ls /data/koha-covers/assets/<orphan_sha>.webp   # очікується: No such file
```

### Задача 9.2 — Політика видалення ресурсу

**Опис:** Перевірити поведінку розділу 8 концепції — порожнє source-поле не видаляє наявний ресурс автоматично.

**Acceptance criteria:**
- Очищення source-поля в Google Таблиці не видаляє існуючу обкладинку/файл у Koha/DSpace.
- Явне видалення ресурсу (окрема адмін-дія) коректно прибирає посилання, залишаючи asset для GC.

**Validation:**
```bash
python -m integrator.run --record test-uid-4 --simulate-empty-source
sqlite3 state.db "SELECT cover_source_id, cover_asset_sha256 FROM records WHERE record_uid='test-uid-4';"
# cover_asset_sha256 має лишитись незмінним
```

---

## Фаза 10 — Backup

**Мета:** реалізувати пріоритизовану backup-стратегію (розділ 32).

**Deliverables:**
- Автоматизований, частий backup MariaDB (Koha) та SQLite state DB.
- Менш частий/incremental backup `/data/koha-covers/assets`.
- Підтверджена процедура відновлення (restore-тест) для критичних джерел.

### Задача 10.1 — Backup MariaDB + state DB (критичний контур)

**Acceptance criteria:**
- Щоденний backup виконується автоматично й логується.
- Restore-тест на окремому середовищі успішний.

**Validation:**
```bash
ls -la /backups/mariadb/ | tail -5
ls -la /backups/state-db/ | tail -5
# Restore-тест
koha-mysql <test_instance> < /backups/mariadb/latest.sql
sqlite3 /tmp/restored_state.db < /backups/state-db/latest.sql
sqlite3 /tmp/restored_state.db "SELECT COUNT(*) FROM records;"
```

### Задача 10.2 — Backup assets (нижчий пріоритет, incremental)

**Acceptance criteria:**
- Incremental backup виконується за розкладом (рідше за MariaDB/state DB).
- Задокументована процедура регенерації assets із Google Drive на випадок повної втрати, якщо backup недоступний.

**Validation:**
```bash
rsync -avn /data/koha-covers/assets/ /backups/assets/ | tail -20   # dry-run diff
```

---

## Фаза 11 — Міграція існуючих Koha covers

**Мета:** перенести legacy BLOB-обкладинки на новий pipeline (розділ 33).

**Deliverables:**
- Усі legacy covers мігровані у content-addressed сховище з відповідним `957$c`.
- `LocalCoverImages`/`OPACLocalCoverImages` вимкнені лише після підтвердженої міграції.

### Задача 11.1 — Скрипт міграції legacy covers

**Acceptance criteria:**
- Кожен legacy BLOB конвертовано у WebP, опубліковано content-addressed, `957$c` заповнено.
- Кількість мігрованих записів збігається з кількістю legacy covers "до".
- Legacy BLOB видаляється з БД лише після цієї перевірки.

**Validation:**
```bash
koha-mysql <instance> -e "SELECT COUNT(*) FROM biblioimages;"   # "до"
python -m integrator.migrate_legacy_covers --dry-run
python -m integrator.migrate_legacy_covers --apply
sqlite3 state.db "SELECT COUNT(*) FROM records WHERE cover_asset_sha256 IS NOT NULL;"  # має збігатись
koha-mysql <instance> -e \
  "SELECT variable, value FROM systempreferences WHERE variable IN ('LocalCoverImages','OPACLocalCoverImages');"
```

---

## Фаза 12 — Спостережуваність (окремий scope, опційно/останній)

**Мета:** додати логи/метрики/алерти поверх уже робочого MVP (розділ 36). Не блокує жодну попередню фазу.

**Deliverables:**
- Структуровані логи по кожному record.
- Метрики (частка NO-OP/помилок, тривалість pipeline, розмір сховища).
- Health-check endpoint та алерти на `status = failed` понад поріг і заповнення диску.

### Задача 12.1 — Логи, метрики, health-check, алерти

**Acceptance criteria:**
- Кожен запуск pipeline пише структурований лог-запис (`record_uid, drive_id, old_sha, new_sha, рішення, тривалість`).
- `/healthz` Integrator повертає 200 при нормальній роботі.
- Алерт спрацьовує при штучному перевищенні порогу `status = failed`.

**Validation:**
```bash
tail -1 integrator.log | jq .
curl -s "http://localhost:9090/healthz" -o /dev/null -w "%{http_code}\n"

# Штучно створити > поріг записів зі status=failed і перевірити спрацювання алерту
sqlite3 state.db "SELECT COUNT(*) FROM records WHERE status='failed';"
# перевірити канал алертів (Slack/email/etc.) на отримане повідомлення
```

---

## Матриця залежностей фаз

```text
Фаза 0 (інфра) ─┬─> Фаза 1 (Koha data model)
                └─> Фаза 2 (core engine) ─> Фаза 3 (Drive dirty-check)
                                                │
                              ┌─────────────────┼─────────────────┐
                              ▼                                   ▼
                        Фаза 4 (cover pipeline)             Фаза 7 (DSpace pipeline)
                              │
                              ▼
                        Фаза 5 (cover з PDF)
                              │
                              ▼
                        Фаза 6 (production static service)
                              │
                              ▼
                        Фаза 8 (shared/override/rollback)
                              │
                              ▼
                        Фаза 9 (GC/видалення)
                              │
                              ▼
                        Фаза 10 (backup)
                              │
                              ▼
                        Фаза 11 (міграція legacy)

Фаза 12 (спостережуваність) — паралельно/після будь-якої з Фаз 4+, без блокуючих залежностей.
```
