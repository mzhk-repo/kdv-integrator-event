# Архітектурна концепція зберігання обкладинок і цифрових файлів

**Koha 25.05 + DSpace + kdv-integrator + Google Drive + Traefik + Cloudflare**

## 1. Мета

Архітектура повинна забезпечити:

- зберігання обкладинок поза MariaDB Koha;
- швидку статичну віддачу обкладинок;
- автоматичне визначення, чи змінився файл;
- безпечне масове оновлення MARC-записів;
- відсутність повторної обробки незмінених файлів;
- можливість використання однієї обкладинки багатьма записами;
- стабільний зв'язок між Google Таблицею, Koha, DSpace та Integrator;
- явне розрізнення "успішно оброблено", "помилка" та "незавершений цикл" — без мовчазних припущень;
- просту підтримку без зайвих сервісів і складних workflow.

Основний принцип — **KISS**: якщо ресурс не змінився, Integrator нічого з ним не робить.

### Конфігурація через середовище

Усі значення, специфічні для конкретного інсталяції (домени, base URL Koha/DSpace/CDN, ліміти на кшталт `max_retry_count`), задаються **через змінні середовища/конфіг деплойменту**, а не хардкодяться у коді, MARC-полях чи в цьому документі. У прикладах нижче такі значення позначені як:

```text
${KOHA_OPAC_BASE_URL}     — повний HTTPS base URL OPAC Koha
${KOHA_STAFF_BASE_URL}    — повний HTTPS base URL staff-інтерфейсу Koha
${COVERS_CDN_BASE_URL}    — повний HTTPS base URL covers-cdn без кінцевого /
${MAX_RETRY_COUNT}        — ліміт повторів для Integrator (розділ 19, 22)
```

Конкретні значення (напр. `koha.example.org`) визначаються під час деплойменту (`.env`, secrets manager, Helm values тощо) і не є частиною архітектури.

Контракт цього репозиторію визначений у [environment.md](environment.md): концептуальні `KOHA_OPAC_BASE_URL` і `KOHA_STAFF_BASE_URL` використовують наявні runtime-змінні `KOHA_OPAC_URL` і `KOHA_API_URL` відповідно.

---

# 2. Основний ідентифікатор запису

Поле MARC:

```text
001 = UUIDv7
```

є головним стабільним ідентифікатором бібліографічного запису.
Якщо під час запуску інтеграційного workflow `001` відсутній або порожній,
Integrator генерує UUIDv7, записує його через Koha MARCXML API та перевіряє
read-back до подальшої обробки. Наявне непорожнє значення не перезаписується.

Наприклад:

```text
019f840f-91bd-7ba3-a7bd-a9cae6b9c573
```

Він використовується в:

```text
Google Таблиця
      ↓
   MARCXML
      ↓
     Koha
      ↓
kdv-integrator
      ↓
    DSpace
```

`biblionumber` залишається внутрішнім ID конкретної інсталяції Koha і не використовується як глобальний ідентифікатор.

---

# 3. MARC-поля інтеграції

Використовується така модель:

| Поле | Значення |
|---|---|
| `001` | UUIDv7 запису |
| `956$p` | джерело готової обкладинки |
| `956$u` | джерело PDF |
| `957$c` | значення обкладинки, яке формує Integrator; у новому pipeline — SHA-256 готової WebP |
| `957$3` | UUID Item у DSpace |
| `856$u #1` | публічний URL bitstream у DSpace |
| `856$u #2` | Handle URL DSpace |

`956$p` і `956$u` можуть містити Google Drive URL або підтримуваний локальний шлях.

---

# 4. Джерело та готовий ресурс — різні сутності

Для обкладинки потрібно розділяти:

```text
Google Drive File ID
```

та:

```text
SHA-256 готової WebP
```

Google Drive ID використовується для визначення джерела та швидкої перевірки змін.

SHA-256 WebP використовується як ідентифікатор готової обкладинки у сховищі.

Приклад:

```text
Google Drive:
file_id = 1ABCxyz...

        ↓

kdv-integrator

        ↓

WebP:
sha256 = e8bd42a79c4f...

        ↓

e8bd42a79c4f....webp
```

---

# 5. Fast dirty-check через Google Drive File ID

Основна перевірка повинна бути максимально дешевою.

Integrator витягує Google Drive File ID з:

```text
956$p
```

або:

```text
956$u
```

і порівнює його з останнім успішно обробленим File ID у своїй state DB.

### Якщо ID не змінився

```text
incoming_file_id == stored_file_id
```

результат:

```text
NO-OP
```

Integrator:

- не звертається за checksum;
- не завантажує файл;
- не генерує WebP;
- не запускає PDF optimizer;
- не оновлює DSpace;
- не змінює MARC.

Це буде типовим сценарієм при масовому оновленні записів.

---

# 6. Перевірка при зміні Google Drive ID

Якщо:

```text
incoming_file_id != stored_file_id
```

Integrator отримує metadata нового файла Google Drive.

Зокрема:

```text
sha256Checksum
```

та порівнює його з hash попереднього source.

### Обмеження sha256Checksum

Поле `sha256Checksum` заповнюється Google Drive API лише для файлів, вміст яких фактично збережено в Drive (реально завантажені бінарні файли). Воно **не** заповнюється для:

- файлів Google Docs / Sheets / Slides (нативний формат Google);
- shortcut-файлів (посилань на інший файл).

Якщо для нового `file_id` поле `sha256Checksum` відсутнє або порожнє — це **не** трактується як "контент не змінився". Integrator фіксує:

```text
status = failed
```

і не виконує processing автоматично. Потрібне ручне втручання — перевірка, що посилання в Google Таблиці веде саме на завантажений файл, а не на Google Doc чи shortcut.

### Новий ID, але той самий контент

```text
old:
ID  = ABC
SHA = 111

new:
ID  = XYZ
SHA = 111
```

Результат:

```text
NO-OP для контенту
```

Integrator лише запам'ятовує новий source ID.

Обкладинка або DSpace bitstream не перегенеровуються.

### Новий ID і новий контент

```text
old:
ID  = ABC
SHA = 111

new:
ID  = XYZ
SHA = 222
```

Результат:

```text
RESOURCE CHANGED
```

і запускається відповідний pipeline.

Помилка виклику Google Drive API на будь-якому з цих кроків (timeout, quota, недоступність) — не NO-OP і не RESOURCE CHANGED. Обробка таких помилок винесена в розділ 22 **«Обробка помилок та проміжних станів»**.

---

# 7. Політика Google Drive

Для спрощення системи приймається правило:

> Файли Google Drive вважаються immutable sources.

Якщо потрібно змінити PDF або обкладинку, рекомендований workflow:

```text
не замінювати content існуючого Drive-файла
            ↓
завантажити новий файл
            ↓
отримати новий Drive File ID
            ↓
змінити посилання у Google Таблиці
```

Таким чином Google Drive File ID є швидким індикатором потенційної зміни.

SHA-256 залишається додатковою перевіркою тільки при зміні ID.

---

# 8. Відсутність source не означає видалення

Критичне правило для масових MARC-оновлень:

```text
956$p відсутнє або порожнє
```

означає:

```text
не змінювати існуючу обкладинку
```

Аналогічно:

```text
956$u відсутнє або порожнє
```

означає:

```text
не змінювати існуючий файл DSpace
```

Порожнє поле ніколи автоматично не трактується як команда видалення.

Видалення обкладинки або PDF виконується окремою адміністративною операцією Integrator.

---

# 9. Сховище обкладинок

Обкладинки зберігаються безпосередньо на сервері:

```text
/data/koha-covers/
```

Рекомендована структура:

```text
/data/koha-covers/
├── assets/
└── .incoming/
```

Для великої кількості файлів:

```text
assets/
├── e8/
│   └── bd/
│       └── e8bd42a79c4f....webp
```

Hash sharding не є обов'язковим на першому етапі і може бути доданий пізніше.

---

# 10. Формат обкладинки

Canonical format:

```text
WebP
```

Рекомендована нормалізація:

```text
decode
→ EXIF orientation
→ RGB
→ видалення зайвих metadata
→ width приблизно 600–800 px
→ без upscale
→ WebP quality приблизно 82
```

Після створення WebP Integrator обчислює:

```text
SHA256(final_webp)
```

Hash використовується як filename:

```text
e8bd42a79c4f....webp
```

---

# 11. Content-addressed storage

Однаковий готовий content має один і той самий SHA-256.

Тому:

```text
Record A ─┐
Record B ─┼── e8bd42....webp
Record C ─┘
```

фізично використовують один файл.

Не потрібно окремо реалізовувати shared-cover groups або спеціальний `cover_id`.

Дедуплікація відбувається природно за content hash.

---

# 12. Cover pipeline

## Готова обкладинка

Якщо присутнє:

```text
956$p
```

алгоритм:

```text
956$p
  ↓
extract Drive ID
  ↓
ID == stored ID?
  │
  ├── YES → NO-OP
  │
  └── NO
       ↓
   get Drive SHA
       ↓
SHA == stored SHA?
  │
  ├── YES
  │    ↓
  │ update source ID only
  │
  └── NO
       ↓
     download
       ↓
    normalize
       ↓
      WebP
       ↓
   asset SHA256
       ↓
     publish
       ↓
 update Koha 957$c
```

Помилка на будь-якому кроці (Drive API, download, normalize, запис у Koha) не переводить запис у NO-OP — див. розділ 22.

---

# 13. Cover із PDF

Якщо `956$p` відсутнє, але є:

```text
956$u
```

PDF є fallback source для обкладинки.

Алгоритм dirty-check такий самий:

```text
956$u
  ↓
Drive ID
  ↓
unchanged?
  │
  ├── YES → NO-OP
  │
  └── NO → SHA check
```

Якщо PDF дійсно змінився:

```text
download PDF
→ first page
→ normalize
→ WebP
→ asset SHA
→ publish
```

Так само, як і для обкладинок (розділ 12), помилка на будь-якому кроці — не NO-OP (розділ 22).

---

# 14. Atomic publish

Новий файл спочатку створюється у:

```text
/data/koha-covers/.incoming/
```

після чого виконується:

```text
os.replace()
```

у кінцевий шлях.

Наприклад:

```text
.incoming/tmp123
      ↓
assets/e8bd42....webp
```

Temporary та destination повинні знаходитися на одному filesystem.

Готовий asset після створення не змінюється.

Новий content завжди створює новий hash та новий filename.

---

# 15. Koha та CustomCoverImages

У Koha:

```text
CustomCoverImages = Show
OPACCustomCoverImages = Show
```

`957$c` містить лише hash:

```text
957$c =
e8bd42a79c4f...
```

`CustomCoverImagesURL`:

```text
${COVERS_CDN_BASE_URL}/{957$c}.webp
```

Таким чином домен та структура сховища не записуються у MARC.

---

# 16. LocalCoverImages

Після завершення міграції старих covers:

```text
LocalCoverImages = Don't show
OPACLocalCoverImages = Don't show
```

Нові обкладинки більше не завантажуються через штатний Local Cover механізм Koha.

Legacy BLOB можна видалити з БД лише після перевірки успішної міграції.

---

# 17. Static cover service

Схема:

```text
Internet
   │
   ▼
Cloudflare
   │
Cloudflare Tunnel
   │
   ▼
Traefik
   │
   ▼
covers-cdn
nginx:alpine
   │
   ▼
/data/koha-covers
```

Nginx отримує сховище:

```text
read-only
```

Integrator:

```text
read-write
```

Traefik виконує routing.

Nginx займається лише статичною віддачею WebP.

---

# 18. Кешування

Оскільки filename є SHA-256 контенту:

```text
AAA.webp
```

ніколи не змінює свого вмісту.

Тому можна безпечно використовувати:

```http
Cache-Control: public, max-age=31536000, immutable
```

При заміні cover:

```text
AAA.webp
```

стає:

```text
BBB.webp
```

і браузер автоматично отримує новий URL.

Cloudflare cache purge не потрібний.

Виняток — аварійний ручний override вмісту shared asset (розділ 28): оскільки в цьому випадку той самий URL починає віддавати інші байти, purge кешу для конкретного URL стає **обов'язковим** кроком процедури, а не опційним.

---

# 19. Мінімальна State DB Integrator

Integrator потребує невелику SQLite DB.

Task 2.1 uses the export module's SQLite migration runner, with a separate file
at `${COVER_STATE_DB_PATH}`. `src/cover_state/schema.py` owns the `records` schema;
export retains `${EXPORT_DB_PATH}` and `exported_records`. Apply the cover migration
with `python -m src.cover_state.schema`; it checks WAL and sets cover-state schema version 2.

Не потрібно дублювати в ній всю Koha або DSpace metadata.

Достатньо зберігати технічний стан:

```text
record_uid

cover_source_id
cover_source_sha256
cover_asset_sha256

file_source_id
file_source_sha256

dspace_item_uuid
dspace_bitstream_uuid

status          -- ok | pending | failed
retry_count

updated_at
```

### Призначення status / retry_count

`status` фіксує, чи завершено обробку запису **повністю та підтверджено**, включно із записом усіх MARC-полів назад у Koha:

```text
ok      — останній цикл завершився успішно, усі поля (957$c, 856) підтверджено записаними
pending — обробка розпочата (asset опубліковано і/або bitstream завантажено),
          але запис результату назад у Koha ще не підтверджено повністю
failed  — вичерпано ліміт повторів або отримано permanent error
          (наприклад, відсутній sha256Checksum — розділ 6)
```

`retry_count` — лічильник послідовних невдалих спроб; скидається в `0` при успішному завершенні циклу.

Запис зі `status = pending` або `failed` **не** вважається "успішно обробленим": він перевіряється повторно на наступному запуску, навіть якщо Drive ID не змінився відносно `stored_id`. Це не ускладнює fast dirty-check — лише додає одну умову: `stored_id` довіряємо тільки коли `status = ok`.

### Обмеження на кількість повторів

`retry_count` обмежений конфігурованим `${MAX_RETRY_COUNT}` (наприклад, `5`, задається середовищем — див. розділ 1). Коли:

```text
retry_count >= ${MAX_RETRY_COUNT}
```

Integrator **припиняє автоматичні повтори** для цього запису: `status` лишається `failed`, але запис виключається з активного retry-скану на кожному наступному запуску. Без цього ліміту зависла або постійно помилкова операція (наприклад, некоректний MARC, який Koha REST API стабільно відхиляє) повторювалася б вічно, щоразу навантажуючи Google Drive / Koha / DSpace API.

Повернення запису в обробку — свідома ручна дія після усунення першопричини: скидання `retry_count` адміністратором, наприклад прямим запитом:

```sql
UPDATE records SET retry_count = 0 WHERE record_uid = ?;
```

Записи, що вичерпали ліміт, — явні кандидати на ручний розбір (розділ 22, 36).

### Конкурентний доступ

SQLite працює в режимі `WAL` (Write-Ahead Logging). Обробку записів виконує один writer-процес (або застосовується per-record lock), щоб два паралельні воркери під час масового імпорту не побачили одночасно однаковий `stored_id` і не запустили processing для того самого запису двічі.

Основне призначення БД лишається незмінним:

```text
fast dirty-check
```

---

# 20. DSpace pipeline

Для PDF використовується та сама проста логіка.

```text
956$u
   ↓
extract Drive ID
   ↓
ID unchanged?
   │
   ├── YES → NO-OP
   │
   └── NO
        ↓
     compare SHA
        │
    ┌───┴────┐
    │        │
   same    changed
    │        │
    │        ▼
    │     download
    │        ↓
    │     optimize
    │        ↓
    │   upload DSpace
    │
    ▼
update source ID
```

Помилка на будь-якому кроці — не NO-OP (розділ 22).

---

# 21. Безпечна заміна PDF у DSpace

Якщо PDF дійсно змінився:

```text
1. Download and validate new PDF; optimize if required
2. Upload a new bitstream while the old one remains available
3. Verify uploaded size and checksum against the local file
4. Set the new bitstream as primary in the DSpace ORIGINAL bundle and read it back
5. Persist the DSpace result in the pending-work checkpoint
6. Update both Koha `856$u` links (PDF and Handle) and confirm them by read-back
7. Remove the old bitstream and complete the source ID/SHA state update
```

Старий bitstream не видаляється до підтвердження обох Koha links. Якщо Koha
write-back або read-back завершується помилкою, checkpoint зберігає UUID нового
bitstream; повтор використовує його без повторного upload, а старий лишається.

Увесь цикл виконується під `status = pending` до Koha read-back, видалення
старого bitstream і commit у state DB — див. розділ 22. Runtime acceptance
реалізованого циклу очікує перевірки після deployment.

---

# 22. Обробка помилок та проміжних станів

Це наскрізний (cross-cutting) розділ: він застосовується до кожного pipeline вище (Cover pipeline, Cover із PDF, DSpace pipeline), а не є окремим кроком обробки.

## Помилка API ніколи не є NO-OP

Якщо виклик до Google Drive API (metadata, download), Koha REST API або DSpace REST API завершується помилкою (timeout, rate limit, 5xx, недоступність мережі) — це **ніколи** не трактується як "контент не змінився". Замість цього:

```text
error
  ↓
status = failed (або лишається pending, якщо частина кроків уже виконана)
  ↓
retry_count += 1
  ↓
retry_count >= ${MAX_RETRY_COUNT}?
  │
  ├── NO  → exponential backoff → повторна спроба на наступному запуску
  │
  └── YES → запис виключається з автоматичного retry-скану
            (потребує ручного втручання — розділ 19)
```

`stored_file_id` не оновлюється, доки цикл обробки не завершиться станом `ok`. Це гарантує, що наступний запуск не пропустить реальну зміну через тимчасову недоступність зовнішнього сервісу. `${MAX_RETRY_COUNT}` (розділ 19) гарантує, що постійно помилкова операція не повторюється вічно.

## Атомарність запису кількох MARC-полів назад у Koha

Pipeline може оновлювати декілька полів запису послідовно:

```text
957$c          (cover asset SHA)
856$u #1 / #2  (DSpace bitstream / handle)
```

Якщо перший виклик до Koha REST API успішний, а другий — ні, запис може опинитись у неконсистентному стані (наприклад, оновлена обкладинка, але застарілі DSpace-посилання). Щоб цього уникнути:

```text
1. Публікація/завантаження asset(ів) завершено
        ↓
2. State DB: status = pending
        ↓
3. Запис усіх відповідних полів у Koha (957$c, 856 …)
        ↓
4. Усі виклики підтверджено успішними?
   │
   ├── YES → status = ok, retry_count = 0
   │
   └── NO  → status лишається pending/failed,
             retry_count += 1,
             наступний запуск повторює лише крок 3
             (asset вже опубліковано — повторний download/processing не потрібен)
```

Запис зі `status = pending` або `failed` завжди перевіряється повторно на наступному запуску, незалежно від того, чи змінився Drive ID — це і є механізм reconciliation для незавершених циклів.

## Обмеження sha256Checksum

Див. розділ 6 — відсутність `sha256Checksum` для нового `file_id` трактується як permanent error (`status = failed`), а не як зміна контенту й не як NO-OP.

---

# 23. DSpace identity

DSpace Item зв'язується з Koha через:

```text
001 UUIDv7
```

а не через `biblionumber`.

Рекомендоване поле DSpace:

```text
koha.uid
```

За потреби той самий UUID може додатково зберігатися в:

```text
dc.identifier.other
```

---

# 24. UID → Koha resolver

Окремий resolver-сервіс створювати не потрібно.

Koha вже має Elasticsearch index для MARC `001`:

```text
control-number
```

Тому штатний пошук Koha виконує роль resolver.

Для UID:

```text
019f840f-91bd-7ba3-a7bd-a9cae6b9c573
```

публічне посилання:

```text
${KOHA_OPAC_BASE_URL}/cgi-bin/koha/opac-search.pl?q=control-number:019f840f-91bd-7ba3-a7bd-a9cae6b9c573
```

Це краще за:

```text
?q=UUID
```

оскільки пошук виконується безпосередньо за індексом `001`, а не по всьому бібліографічному запису.

Якщо resolver використовується тільки у Staff interface:

```text
https://${KOHA_STAFF_BASE_URL}/cgi-bin/koha/catalogue/search.pl?q=control-number:<UUID>
```

Таким чином `dc.relation.uri` у DSpace може містити стабільне посилання через UUID і не залежить від `biblionumber`.

При зміні `biblionumber` після міграції Koha URL продовжує знаходити правильний запис через `001`.

---

# 25. Масове оновлення MARC

Google Таблиця:

```text
↓
MARCXML
↓
Koha import
```

може оновлювати бібліографічні метадані:

```text
100
245
264
300
650
700
...
```

без повторної обробки covers та DSpace files.

Після імпорту Integrator бачить:

```text
same 956$p Drive ID
same 956$u Drive ID
```

і завершує processing:

```text
NO-OP
```

---

# 26. Захист integration-managed полів

Поля, які формує Integrator:

```text
957$c
957$3
856
```

не повинні випадково стиратися звичайним MARC overlay.

Для цього використовуються штатні:

```text
MARCOverlayRules
```

Koha.

Захист `957` зберігає обидва Integrator-managed підполя; `956` лишається полем джерел і статусу.
Перед увімкненням правила для `957` наявні `956$c`/`956$3` потрібно окремо перенести
до `957$c`/`957$3` і перевірити. Автоматична масова зміна MARC у межах цієї зміни не виконується.

Джерело бібліографічних metadata та integration state повинні залишатися логічно розділеними.

---

# 27. Shared covers

Ніякої додаткової логіки не потрібно.

Якщо декілька записів після обробки мають однакову cover:

```text
asset SHA = AAA
```

у них буде:

```text
957$c = AAA
```

і всі використовуватимуть:

```text
${COVERS_CDN_BASE_URL}/AAA.webp
```

Фізично файл зберігається один раз.

---

# 28. Заміна shared cover

Зміна source одного запису:

```text
AAA → BBB
```

змінить лише:

```text
957$c
```

цього запису.

Інші записи продовжать використовувати:

```text
AAA.webp
```

Якщо у багатьох записах source замінено на одну й ту саму нову cover, вони природно почнуть використовувати:

```text
BBB.webp
```

### Аварійна ручна заміна вмісту shared asset (override)

Штатний шлях вище вимагає зміни source **в кожному записі** окремо — це коректно, але повільно, якщо потрібно негайно виправити один помилковий shared cover одразу для всіх записів, що на нього посилаються (наприклад, у Google Drive помилково завантажили не те зображення, і воно вже розійшлося по десятках записів).

Для такого випадку доступний вузький операторський скрипт
`scripts/update_cover_asset.py`: він замінює один файл за тим самим
іменем-хешем у сховищі, робить backup, атомарну заміну та точковий Cloudflare
purge. Скрипт не змінює Koha `957$c` чи state DB. Деталі запуску наведені в
[runbook](runbook.md#replace-one-named-cdn-asset).

Операція напряму замінює вміст файла за тим самим іменем-хешем:

```text
/data/koha-covers/assets/AAA.webp   (старий вміст)
        ↓ ручна заміна файла
/data/koha-covers/assets/AAA.webp   (новий вміст, та сама назва)
```

Усі записи з `957$c = AAA` отримують нові байти з CDN після purge без зміни MARC і без проходження pipeline. Browser cache може ще віддавати попередні байти до завершення `max-age`.

Це **свідоме порушення** принципу "immutable asset ніколи не перезаписується" (розділ 35, п. 9), тому застосовується лише як ручна аварійна дія, а не штатна поведінка Integrator. Скрипт перед заміною зберігає backup, робить точковий purge Cloudflare і відновлює попередній asset, якщо purge не підтверджено. Окремий operator/reason audit log і state rollback не реалізовані.

```text
1. Backup старого файла перед перезаписом
   (напр. AAA.webp.bak.<timestamp> — для можливості відкату)

2. Примусовий cache purge саме цього URL на CDN. Browser cache purge не
   виконується, тому раніше закешовані браузером байти можуть лишатися до
   закінчення `max-age`.

3. Обмежені права на запуск override-скрипта й запис у storage
   (лише вузьке коло адміністраторів, не сервісний акаунт Integrator
    у звичайному режимі роботи)
```

Після такого override ім'я файла (`AAA`) формально більше не збігається з SHA-256 його фактичного вмісту — це прийнятий і задокументований компроміс для конкретно цього asset, а не порушення архітектури в цілому: state DB та інші записи, що не зачеплені цим asset, лишаються повністю консистентними.

---

# 29. Rollback

Оскільки assets immutable, старий файл після заміни не потрібно негайно видаляти.

Мінімально достатньо журналювати:

```text
record_uid
old_asset_sha
new_asset_sha
changed_at
run_id
```

Для rollback Integrator повертає:

```text
957$c = old_asset_sha
```

Фізичний asset все ще знаходиться у сховищі.

---

# 30. Garbage Collection

Старі immutable assets не видаляються відразу.

Періодично виконується GC.

Файл може бути видалений, якщо:

```text
жоден Koha record не посилається на його SHA
```

і він старший за встановлений retention period, наприклад:

```text
90 днів
```

GC не є частиною основного processing pipeline і може виконуватись окремо за розкладом.

### Без reference counting

Окремий лічильник посилань (reference count) на asset у state DB не потрібен. На момент GC достатньо простого запиту:

```sql
SELECT 1 FROM records
WHERE cover_asset_sha256 = ? OR file_source_sha256 = ?
LIMIT 1;
```

Якщо жоден рядок не знайдено — файл є кандидатом на видалення (з урахуванням retention period). Це узгоджується з тим, що state DB вже зберігає `cover_asset_sha256`/`file_source_sha256` для кожного запису — окрема структура для підрахунку посилань була б зайвим ускладненням.

---

# 31. Видалення ресурсу

Порожнє:

```text
956$p
```

або:

```text
956$u
```

не використовується як команда delete.

Для видалення використовується окрема функція Integrator:

```text
DELETE /kdv/api/integrate/{biblionumber}/cover
```

Ця дія прибирає `956$p`, `957$c` та cover reference у SQLite state під workflow
lock. Незавершений цикл відхиляється; PDF/DSpace references і WebP файл не
видаляються. WebP стає кандидатом GC після retention period.

Окреме видалення PDF/DSpace resource є незалежною дією і не виконується цим
cover endpoint-ом.

```text
Remove DSpace file
```

Це запобігає випадковому масовому видаленню через MARC import.

---

# 32. Backup

Резервне копіювання розділяється:

```text
Koha
→ MariaDB backup
```

```text
Integrator
→ SQLite state DB
```

```text
Covers
→ /data/koha-covers/assets
```

Оскільки cover assets immutable, вони добре підходять для incremental backup.

### Пріоритет резервного копіювання

Не всі три джерела однаково критичні:

```text
критично, часта періодичність (напр. щоденно):
  SQLite state DB (Integrator)

нижчий пріоритет, incremental, можна рідше:
  /data/koha-covers/assets
```

Для state DB `scripts/backup_cover_state.py` бере `SERVER_ENV` із процесу або,
якщо змінну не передано, із `/etc/environment`. Так обирається зашифрований
env-файл для наявного SOPS loader, з якого читається `COVER_STATE_HOST_PATH`.
З нього також читаються `COVER_STATE_BACKUP_HOST_PATH` (default
`/backups/state-db`), optional `COVER_STATE_CLOUD_BACKUP_HOST_PATH` усередині
вже змонтованого rclone Google Drive, та окремі
`COVER_STATE_LOCAL_RETENTION_DAYS` і `COVER_STATE_CLOUD_RETENTION_DAYS`;
локальний backup відокремлений від live state та cover storage;
`scripts/init-volume.sh` створює цей каталог із mode `0700`.
Скрипт створює узгоджений snapshot через
SQLite online backup API, перевіряє його та оновлює `latest.sqlite3` атомарно.
Retention налаштовується в env (за замовчуванням 30 днів локально та 90 днів у
cloud). Якщо cloud path заданий, скрипт вимагає активний rclone mount.
`scripts/backup_cover_assets.sh` інкрементально копіює assets лише до локальної
`COVER_ASSETS_BACKUP_HOST_PATH`; для нього немає retention чи cloud копії.
Оператор налаштовує щоденний systemd
timer і запускає restore-перевірку на цьому ж середовищі; вона відновлює копію
у тимчасову DB, запускає `quick_check` та читає таблицю `records`.

`assets/` теоретично регенерований: доки оригінали файлів ще існують у Google Drive, Integrator може заново завантажити джерело й перебудувати WebP/PDF-похідні за даними зі state DB (`cover_source_id`, `file_source_id`). Втрата ж MariaDB або state DB незворотна — без них неможливо навіть встановити, які Drive-файли відповідають яким записам.

Це не скасовує backup `assets/` — повна регенерація великого сховища займає час і залежить від доступності Google Drive API (quota) в момент відновлення. Backup `assets/` лишається потрібним, просто з нижчим пріоритетом і частотою порівняно з MariaDB та state DB.

---

# 33. Міграція існуючих Koha covers

Міграція виконується один раз:

```text
Koha LocalCover
      ↓
export image
      ↓
normalize WebP
      ↓
asset SHA256
      ↓
CoverStorage
      ↓
957$c = asset SHA
```

Після перевірки:

```text
CustomCoverImages = Show
OPACCustomCoverImages = Show

LocalCoverImages = Don't show
OPACLocalCoverImages = Don't show
```

Після backup старі BLOB можна видалити з MariaDB.

---

# 34. Підсумкова архітектура

```text
Google Sheet
     │
     ▼
   MARCXML
     │
     ▼
    Koha
     │
     │ 001 = UUIDv7
     │ 956$p = cover source
     │ 956$u = PDF source
     │ 957$c = cover asset SHA
     │
     ▼
kdv-integrator
     │
     │
     ├── Drive ID unchanged
     │       ↓
     │     NO-OP
     │
     └── Drive ID changed
             │
             ▼
         SHA check
          /     \
       same     changed
        │          │
      NO-OP        ▼
                 process
                  /   \
                 /     \
              Cover   DSpace
                │       │
                ▼       ▼
             WebP     PDF
                │
                ▼
           Asset SHA
                │
                ▼
           covers-cdn
                │
                ▼
             Traefik
                │
                ▼
           Cloudflare
```

---

# 35. Ключові принципи

1. **`001 UUIDv7` — canonical record ID.**
2. **Google Drive File ID — fast dirty-check.**
3. **SHA source — додаткова перевірка тільки при зміні ID.**
4. **SHA готової WebP — ID cover asset.**
5. **Незмінний Drive ID → негайний NO-OP.**
6. **Масовий MARC import не запускає повторну обробку незмінених ресурсів.**
7. **Порожнє source-поле не видаляє існуючий ресурс.**
8. **Однакові covers автоматично використовують один asset.**
9. **Immutable asset ніколи не перезаписується автоматично** (окрім задокументованого ручного override для shared cover — розділ 28).
10. **Koha зберігає metadata та reference, а не binary cover.**
11. **DSpace зв'язується з Koha через UUIDv7, а не `biblionumber`.**
12. **Для UID resolver використовується штатний Koha `control-number` search — окремий resolver-сервіс не потрібний.**
13. **Складні операції виконуються лише тоді, коли є фактична зміна source.**
14. **Архітектура повинна залишатися простою: Drive ID → SHA при потребі → processing або NO-OP.**
15. **Помилка зовнішнього API (Drive/Koha/DSpace) ніколи не трактується як NO-OP — лише як `pending`/`failed` із повтором.**
16. **`status`/`retry_count` у state DB гарантують, що незавершений цикл обробки буде повторений, а не мовчки забутий.**

---

# 36. Спостережуваність (окремий scope)

Логування, метрики та алертинг **не є частиною основного pipeline** цього документа і свідомо винесені в окремий scope / фазу впровадження, щоб не ускладнювати MVP.

Мінімум, достатній для MVP, уже описаний у розділі 22: поля `status`/`retry_count` у state DB гарантують, що жодна помилка не "губиться" мовчки — навіть без окремого моніторингу її видно прямим запитом до state DB (`SELECT * FROM records WHERE status != 'ok'`).

Коли цей scope братиметься в роботу окремо, орієнтовний мінімум:

```text
структуровані логи по кожному record:
  record_uid, drive_id, old_sha, new_sha,
  рішення (NO-OP / CHANGED / FAILED), тривалість

метрики (напр. Prometheus):
  кількість оброблених записів
  частка NO-OP
  частка помилок / retry (records зі status != ok)
  тривалість pipeline
  розмір /data/koha-covers

health-check endpoint Integrator

алерти:
  зростання кількості records зі status = failed понад поріг
  заповнення диску /data/koha-covers понад поріг
```

Це доповнення, а не передумова запуску MVP — Integrator коректно працює і без нього, оскільки `status`/`retry_count` уже несуть мінімально необхідну інформацію про здоров'я pipeline.
