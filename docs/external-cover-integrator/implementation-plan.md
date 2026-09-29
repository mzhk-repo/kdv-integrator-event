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
- `MARCOverlayRules` захищають `956`/`856` від затирання під час звичайного MARC-імпорту (окрім самого Integrator).

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

**Опис:** Налаштувати `CustomCoverImages = Show`, `OPACCustomCoverImages = Show`, `CustomCoverImagesURL = ${COVERS_CDN_BASE_URL}/{956$c}.webp` (розділ 15).

**Acceptance criteria:**
- Обидва системні preference активні.
- URL-шаблон використовує env-змінну, а не хардкоджений домен.
- Тестовий запис із заповненим `956$c` показує обкладинку в OPAC.

**Validation:**
```bash
koha-mysql <instance> -e \
  "SELECT variable, value FROM systempreferences
   WHERE variable IN ('CustomCoverImages','OPACCustomCoverImages','CustomCoverImagesURL');"

curl -s "${KOHA_OPAC_URL}/cgi-bin/koha/opac-detail.pl?biblionumber=<test_id>" \
  | grep -o "${COVERS_CDN_BASE_URL}/[a-f0-9]\+\.webp"
```

### Задача 1.3 — Захист integration-managed полів (MARCOverlayRules)

**Опис:** Налаштувати `MARCOverlayRules` так, щоб звичайний каталогізаторський bulk-імпорт не перезаписував `956`/`856`, керовані Integrator-ом (розділ 26).

**Acceptance criteria:**
- Bulk-імпорт запису без Integrator-джерела не змінює наявні `956$c`/`856$u`, якщо вони вже виставлені Integrator-ом.
- Сам Integrator (окремий процес/user-agent) як і раніше може оновлювати ці поля.

**Validation:**
```bash
# До імпорту: зберегти поточні 956/856
koha-mysql <instance> -e "SELECT biblio_metadata.metadata FROM biblio_metadata WHERE biblionumber=<test_id>;" > before.xml

# Виконати bulk-імпорт тестового файлу без Integrator-полів
misc/migration_tools/bulkmarcimport.pl -file catalog_update.mrc -match 001 -v

# Порівняти 956/856 до і після — мають лишитись незмінними
koha-mysql <instance> -e "SELECT biblio_metadata.metadata FROM biblio_metadata WHERE biblionumber=<test_id>;" > after.xml
diff <(grep -A2 'tag="956"' before.xml) <(grep -A2 'tag="956"' after.xml)
```

---

## Фаза 2 — Integrator: core engine

**Мета:** побудувати перевикористовуваний "двигун" Integrator — state DB, статуси, retry/backoff/`max_retry_count` — **без** зовнішніх інтеграцій (Drive/Koha/DSpace тут ще mock).

**Deliverables:**
- SQLite state DB розгорнута в режимі `WAL`, схема відповідає розділу 19.
- Реалізований генерик-модуль стану `ok/pending/failed` + `retry_count` + `${MAX_RETRY_COUNT}` cutoff (розділ 19, 22), покритий unit-тестами.

### Задача 2.1 — Схема state DB

**Опис:** Створити SQLite-схему з полями `record_uid, cover_source_id, cover_source_sha256, cover_asset_sha256, file_source_id, file_source_sha256, dspace_item_uuid, dspace_bitstream_uuid, status, retry_count, updated_at` (розділ 19).

**Acceptance criteria:**
- Схема застосована через міграцію (не ручний SQL за замовчуванням у продакшені).
- `PRAGMA journal_mode` повертає `wal`.
- Індекс на `record_uid` (унікальний) та на `status`.

**Validation:**
```bash
sqlite3 state.db "PRAGMA journal_mode;"                 # очікується: wal
sqlite3 state.db ".schema records"
sqlite3 state.db "SELECT sql FROM sqlite_master WHERE type='index';"
```

### Задача 2.2 — State machine: `ok`/`pending`/`failed` + `${MAX_RETRY_COUNT}`

**Опис:** Реалізувати модуль, що приймає результат кроку (success/error), оновлює `status`/`retry_count`, і після досягнення `${MAX_RETRY_COUNT}` виключає запис з активного retry-скану (розділ 19, 22).

**Acceptance criteria:**
- Успішний цикл: `status → ok`, `retry_count → 0`.
- Помилка: `status → failed` (або лишається `pending`), `retry_count += 1`.
- Досягнення `${MAX_RETRY_COUNT}`: запис не потрапляє у вибірку "до обробки" наступного запуску.
- Ручний reset `retry_count = 0` повертає запис у обробку.

**Validation:**
```bash
# Unit-тести стану (приклад, pytest)
pytest tests/test_state_machine.py -v

# Інтеграційна перевірка: N штучних помилок поспіль мають зупинити retry на N=MAX_RETRY_COUNT
sqlite3 state.db "SELECT record_uid, status, retry_count FROM records WHERE record_uid='test-uid-1';"
# після ручного reset:
sqlite3 state.db "UPDATE records SET retry_count=0 WHERE record_uid='test-uid-1';"
sqlite3 state.db "SELECT retry_count FROM records WHERE record_uid='test-uid-1';"  # очікується 0
```

---

## Фаза 3 — Google Drive dirty-check

**Мета:** підключити реальний Google Drive API до вже готового core engine.

**Deliverables:**
- Fast dirty-check за File ID (розділ 5).
- Metadata + `sha256Checksum` fetch з коректною обробкою відсутнього checksum як permanent failure (розділ 6).

### Задача 3.1 — Fast dirty-check за Drive File ID

**Опис:** Порівняння `incoming_file_id` зі `stored_file_id`; NO-OP без жодного додаткового API-виклику, якщо збігається.

**Acceptance criteria:**
- Незмінений File ID не спричиняє жодного виклику Google Drive API (перевіряється лічильником викликів/логом).
- Змінений File ID коректно позначається як "потребує SHA-перевірки".

**Validation:**
```bash
# Запустити Integrator на наборі з 100% незмінених record'ів
python -m integrator.run --dry-run --input fixtures/unchanged.json
grep -c "drive_api_call" integrator.log   # очікується 0
```

### Задача 3.2 — Metadata + sha256Checksum, обробка відсутнього checksum

**Опис:** При зміні File ID отримати metadata, порівняти `sha256Checksum`. Якщо поле відсутнє (Google Doc/shortcut) — `status = failed`, без спроби подальшої обробки (розділ 6).

**Acceptance criteria:**
- Реальний binary-файл: SHA коректно отримано і порівняно.
- Google Doc/shortcut як source: запис переходить у `status = failed` з зрозумілою причиною в лозі, а не трактується як "без змін".
- Помилка мережі/quota під час цього кроку: `retry_count += 1`, а не NO-OP (розділ 22).

**Validation:**
```bash
python -m integrator.run --record test-uid-shortcut
sqlite3 state.db "SELECT status FROM records WHERE record_uid='test-uid-shortcut';"  # очікується failed
grep "test-uid-shortcut" integrator.log | grep -i "missing sha256Checksum"

# Симуляція мережевої помилки (напр. через mock/toxiproxy)
python -m integrator.run --record test-uid-network-fail --simulate-drive-error
sqlite3 state.db "SELECT status, retry_count FROM records WHERE record_uid='test-uid-network-fail';"
```

---

## Фаза 4 — Cover pipeline + Koha write-back

**Мета:** повний цикл: зміна в Drive → WebP → content-addressed asset → `956$c` у Koha.

**Deliverables:**
- Download → normalize → WebP (розділ 9, 10).
- Atomic publish у content-addressed сховище (розділ 11, 14).
- Запис `956$c` у Koha через `pending → ok` патерн (розділ 15, 22).

### Задача 4.1 — Download, normalize, WebP

**Опис:** Завантажити файл із Drive, привести до WebP (якість ~82, розмір за конвенцією розділу 10).

**Acceptance criteria:**
- Вихідний WebP валідний і відповідає заданим розмірам/якості.
- Локально порахований SHA завантаженого файла збігається з `sha256Checksum` із Drive (захист від пошкодженого download).

**Validation:**
```bash
python -m integrator.cover_pipeline --record test-uid-1 --stop-after normalize
file output/test-uid-1.webp   # очікується: RIFF...WebP
sha256sum downloaded/test-uid-1.src | diff - expected_drive_sha.txt
```

### Задача 4.2 — Content-addressed atomic publish

**Опис:** Ім'я файла = SHA-256 вмісту; публікація через `os.replace()` (temp → final), без часткових/пошкоджених файлів у сховищі.

**Acceptance criteria:**
- Два записи з однаковим вмістом обкладинки фізично використовують один файл (dedup).
- Примусове переривання процесу під час публікації не залишає пошкоджений файл за фінальним іменем.

**Validation:**
```bash
sha256sum /data/koha-covers/assets/*.webp | sort | uniq -c -w64 | awk '$1>1'   # приклад дублів
# Chaos-тест: kill -9 процесу публікації посеред запису, перевірити відсутність .tmp/побитих файлів
ls /data/koha-covers/assets/ | grep -E '\.tmp$|\.partial$'   # очікується порожньо
```

### Задача 4.3 — Koha `956$c` write-back через `pending → ok`

**Опис:** Перед записом у Koha — `status = pending` у state DB; після підтвердженого успіху REST API — `status = ok` (розділ 15, 22).

**Acceptance criteria:**
- Успішний цикл: OPAC показує нову обкладинку, `status = ok`, `stored_file_id` оновлено.
- Симуляція збою Koha REST API після публікації asset: `status` лишається `pending`, повторний запуск **не** перезавантажує/не переконвертує asset повторно, а лише повторює запис у Koha.

**Validation:**
```bash
curl -s -X PUT "https://${KOHA_STAFF_BASE_URL}/api/v1/biblios/<biblio_id>" \
  -H "Authorization: Bearer ${KOHA_API_TOKEN}" -H "Content-Type: application/marc-in-json" \
  -d @payload_956.json -o /dev/null -w "%{http_code}\n"

sqlite3 state.db "SELECT status FROM records WHERE record_uid='test-uid-1';"  # очікується ok

# Симуляція збою Koha API після успішної публікації asset
python -m integrator.run --record test-uid-2 --simulate-koha-write-fail
sqlite3 state.db "SELECT status FROM records WHERE record_uid='test-uid-2';"  # очікується pending
ls /data/koha-covers/assets/ | grep "$(sqlite3 state.db "SELECT cover_asset_sha256 FROM records WHERE record_uid='test-uid-2';")"
# файл має існувати — повторного download/convert при наступному запуску не буде (перевірити за логом)
```

---

## Фаза 5 — Cover із PDF

**Мета:** автогенерація обкладинки з першої сторінки PDF, якщо окремого cover-джерела немає (розділ 13).

**Deliverables:** записи лише з `file_source` (PDF) отримують згенеровану обкладинку тим самим content-addressed шляхом.

### Задача 5.1 — Генерація cover з першої сторінки PDF

**Опис:** Рендер першої сторінки PDF → WebP → той самий publish/write-back pipeline, що й у Фазі 4.

**Acceptance criteria:**
- Обкладинка коректно згенерована для тестового PDF (перевірка розміру/формату).
- Помилка рендерингу (пошкоджений/захищений паролем PDF) не блокує решту черги — окремий запис переходить у `status = failed`, інші обробляються.

**Validation:**
```bash
python -m integrator.pdf_cover --record test-uid-pdf --stop-after render
file output/test-uid-pdf.webp

python -m integrator.run --record test-uid-corrupt-pdf
sqlite3 state.db "SELECT status FROM records WHERE record_uid='test-uid-corrupt-pdf';"  # очікується failed
python -m integrator.run --batch fixtures/mixed_batch.json
sqlite3 state.db "SELECT status, COUNT(*) FROM records GROUP BY status;"  # інші записи не failed через сусіда
```

---

## Фаза 6 — Static cover service (production-grade)

**Мета:** перевести `covers-cdn` з тестового skeleton (Фаза 0) у продакшн-режим.

**Deliverables:**
- Кешування `Cache-Control: public, max-age=31536000, immutable` (розділ 18).
- Read-only доступ ззовні; запис лише від Integrator-процесу.

### Задача 6.1 — Immutable-кешування та read-only serving

**Опис:** Виставити правильні cache-заголовки, заборонити будь-які методи запису через публічний вхід.

**Acceptance criteria:**
- `GET` на існуючий asset повертає `Cache-Control: public, max-age=31536000, immutable`.
- `PUT`/`POST`/`DELETE` через публічний домен — заборонені (405/403).

**Validation:**
```bash
curl -sI "${COVERS_CDN_BASE_URL}/<sha>.webp" | grep -i cache-control
curl -s -X PUT "${COVERS_CDN_BASE_URL}/<sha>.webp" -o /dev/null -w "%{http_code}\n"  # очікується 403/405
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

**Acceptance criteria:**
- Новий item у DSpace створюється з коректними метаданими, пов'язаними з `record_uid` (UUIDv7).
- Handle стабільний і не змінюється при повторних запусках без реальної зміни джерела.

**Validation:**
```bash
curl -s "https://${DSPACE_BASE_URL}/server/api/core/items?query=record_uid:test-uid-1" \
  -H "Authorization: Bearer ${DSPACE_API_TOKEN}" | jq '.embedded.items | length'  # очікується 1
```

### Задача 7.2 — Безпечна заміна PDF (upload → verify → swap)

**Опис:** Новий bitstream завантажується і перевіряється до видалення старого; кроки upload/verify/update-links виконуються під `status = pending` (розділ 21, 22).

**Acceptance criteria:**
- Старий bitstream доступний до підтвердженого завантаження нового.
- Симуляція збою на кроці "update Koha links": `status = pending`, старий bitstream **не** видалено, наступний запуск не перезавантажує вже завантажений новий bitstream повторно.

**Validation:**
```bash
python -m integrator.dspace_pipeline --record test-uid-3 --simulate-koha-link-fail
sqlite3 state.db "SELECT status FROM records WHERE record_uid='test-uid-3';"  # очікується pending
curl -s "https://${DSPACE_BASE_URL}/server/api/core/bitstreams/<old_bitstream_uuid>" \
  -H "Authorization: Bearer ${DSPACE_API_TOKEN}" -o /dev/null -w "%{http_code}\n"  # очікується 200 (ще існує)
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
- Зміна source одного з них не змінює `956$c` інших.

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
- Rollback повертає `956$c` до попереднього значення.
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
- Усі legacy covers мігровані у content-addressed сховище з відповідним `956$c`.
- `LocalCoverImages`/`OPACLocalCoverImages` вимкнені лише після підтвердженої міграції.

### Задача 11.1 — Скрипт міграції legacy covers

**Acceptance criteria:**
- Кожен legacy BLOB конвертовано у WebP, опубліковано content-addressed, `956$c` заповнено.
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
