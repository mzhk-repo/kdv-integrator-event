# Roadmap: Google Drive source для `956$u` і `956$q`

Цей документ описує контрольований план впровадження завантаження primary PDF з Google Drive за URL у MARC `956$u`, а також additional файлів з Google Drive URL у MARC `956$q`.

Мета: дозволити оператору вказати Google Drive link у `956$u` або в списку `956$q`, щоб інтегратор скачав файл як read-only source, завантажив його в DSpace/Koha pipeline і не змінював Google Drive.

## Інваріанти

- Google Drive використовується тільки як джерело читання.
- Заборонено завантажувати оброблений файл назад у Google Drive.
- Заборонено переміщувати, перейменовувати, видаляти або змінювати файл на Google Drive.
- Секрети service account не дублюються в `env.dev.enc`, `env.prod.enc`, `.env`, `.env.example` або git.
- Єдиний runtime-контракт секрету: `Ansible Vault -> Swarm secret -> /run/secrets/...`.
- Deploy-шлях інтегрується через `scripts/deploy-orchestrator-swarm.sh`.
- `956$u` з локальним відносним шляхом зберігає поточну backward-compatible поведінку.
- `956$p` лишається локальним відносним шляхом у `INTEGRATOR_MOUNT_PATH` на першій ітерації.
- `956$q` має підтримувати змішаний список через `|`: локальні відносні шляхи або Google Drive URL.
- У логах не друкуються private key, service account JSON, OAuth tokens або повні secret payload.

## Цільова архітектура

```text
Koha 956$u primary source
  |
  v
SourceResolver
  |-- LocalMountSource    -> INTEGRATOR_MOUNT_PATH/<relative path> -> rename-first -> optimizer -> DSpace
  `-- GoogleDriveSource  -> /data/kdv_sources/gdrive/<safe temp file> -> optimizer -> DSpace

Koha 956$q additional sources, split by |
  |
  v
AdditionalSourceResolver
  |-- LocalMountSource    -> DSpace ORIGINAL без rename і без optimizer
  `-- GoogleDriveSource  -> DSpace ORIGINAL без rename і без optimizer
```

`src/core.py` не має знати деталей Google Drive API. Він має отримати уніфікований локальний шлях, тип джерела і lifecycle policy: локальні primary файли можуть переходити в `Processed/Error`, а Google Drive temp-файли не переміщуються в `Processed/Error`.

## Runtime secrets contract

Джерело істини для Google service account:

- dev: `/opt/Ansible/ansible/inventories/dev/group_vars/all/rclone.vault.yml`
- prod: `/opt/Ansible/ansible/inventories/prod/group_vars/all/rclone.vault.yml`

Очікуваний runtime-шлях у контейнері `kdv-api`:

```text
/run/secrets/gdrive_service_account_json
```

ENV без секретів:

```env
GDRIVE_ENABLED=true
GDRIVE_SERVICE_ACCOUNT_FILE=/run/secrets/gdrive_service_account_json
GDRIVE_TMP_DIR=/data/kdv_sources/gdrive
GDRIVE_ALLOWED_MIME_TYPES=application/pdf
GDRIVE_MAX_BYTES=262144000
GDRIVE_DOWNLOAD_TIMEOUT=300
```

Swarm secret має бути доступний тільки сервісу `kdv-api`. `kdv-optimizer` і batch wrappers не повинні отримувати Google credentials напряму.

## Ітерація 0: Передумови і межі

### Завдання

1. Перевірити назви змінних у Ansible Vault для dev/prod без виводу значень.
2. Узгодити, чи Vault уже містить повний service account JSON, чи окремі поля, з яких можна зібрати JSON.
3. Узгодити ім'я Swarm secret: `gdrive_service_account_json`.
4. Узгодити, що підтримується тільки PDF blob-файл Google Drive, не Google Docs export.
5. Узгодити, що `956$q` приймає змішаний список local/GDrive джерел через `|`, а помилки additional файлів лишаються non-fatal.

### Перевірки

```bash
ansible-vault view /opt/Ansible/ansible/inventories/dev/group_vars/all/rclone.vault.yml
ansible-vault view /opt/Ansible/ansible/inventories/prod/group_vars/all/rclone.vault.yml
```

Виводити у відповідь тільки факт наявності потрібних ключів, без значень.

### DoD

- Підтверджено джерело service account для dev/prod.
- Підтверджено, що секрет не дублюється в KDV env-файлах.
- Підтверджено read-only policy і no-writeback до Google Drive.
- Підтверджено `956$q` contract для local/GDrive additional файлів.

### Статус 2026-05-23

- dev Vault існує: `/opt/Ansible/ansible/inventories/dev/group_vars/all/rclone.vault.yml`.
- prod Vault існує: `/opt/Ansible/ansible/inventories/prod/group_vars/all/rclone.vault.yml`.
- `ansible-vault` доступний; для роботи поза `/opt/Ansible/ansible` потрібно явно задавати `ANSIBLE_CONFIG=/opt/Ansible/ansible/ansible.cfg` або `--vault-password-file`.
- В обох Vault підтверджено top-level ключ `vault_rclone_service_account_json`.
- Значення `vault_rclone_service_account_json` є валідним повним service account JSON у dev і prod.
- Значення секретів не виводилися; перевірялись тільки імена ключів і JSON field names.

## Ітерація 1: Swarm secret у deploy path

### Завдання

1. Оновити `scripts/deploy-orchestrator-swarm.sh`, щоб під час deploy він створював або оновлював versioned Swarm secret для Google service account з Ansible Vault.
2. Оновити `docker-compose.swarm.yml`, щоб `kdv-api` отримував secret file як `/run/secrets/gdrive_service_account_json`.
3. Не передавати secret у `kdv-optimizer`.
4. Додати non-secret ENV-контракт у `.env.example`.

### Перевірки

```bash
bash -n scripts/deploy-orchestrator-swarm.sh
docker compose --env-file .env.example -f docker-compose.swarm.yml config
```

Після deploy на target node:

```bash
docker service inspect <stack>_kdv-api --format '{{json .Spec.TaskTemplate.ContainerSpec.Secrets}}'
docker exec <kdv-api-container-id> sh -lc 'test -s /run/secrets/gdrive_service_account_json'
```

Не використовувати `cat /run/secrets/...`.

### DoD

- `kdv-api` бачить secret file.
- `kdv-optimizer` не має цього secret.
- `docker inspect` не містить service account JSON у ENV.
- Rollback deploy не залишає активний застарілий secret у service spec.

### Статус 2026-05-23

- Підготовлено `scripts/render-versioned-gdrive-secret.sh`: helper читає `vault_rclone_service_account_json` з Ansible Vault, валідовує JSON, створює versioned Docker secret з іменем `gdrive_service_account_json_<sha12>` і експортує `GDRIVE_SERVICE_ACCOUNT_SECRET_NAME`.
- `scripts/deploy-orchestrator-swarm.sh` викликає helper перед render Swarm manifest, щоб `docker-compose.swarm.yml` отримував актуальне ім'я external secret.
- `docker-compose.swarm.yml` монтує `gdrive_service_account_json` тільки в `kdv-api` як `/run/secrets/gdrive_service_account_json`; `kdv-optimizer` secret не отримує.
- `.env.example` містить тільки non-secret GDRIVE runtime contract.
- Реальний `docker stack deploy` і створення production/dev Swarm secret не запускалися в цій ітерації.

## Ітерація 2: Source abstraction без Google API

### Завдання

1. Додати `src/services/sources.py`.
2. Реалізувати `ResolvedSource` для primary і additional джерел з полями:
   - `local_path`
   - `source_type`
   - `original_name`
   - `temporary`
   - `cleanup_paths`
   - `diagnostics`
   - `lifecycle_policy`: `local_managed` або `remote_ephemeral`
3. Винести поточний local path resolution з `src/core.py` у `LocalMountSource`.
4. Підключити `SourceResolver` у `process_integration_logic()` для `956$u`.
5. Підключити `AdditionalSourceResolver` для кожного елемента `956$q`, розділеного через `|`.
6. Зберегти поточну поведінку `FileService.version_and_move()` тільки для локального primary PDF з `956$u`.

### Перевірки

```bash
python3 -m py_compile src/core.py src/services/sources.py tests/test_core.py
python3 -m pytest tests/test_core.py -q
```

Якщо host Python не має залежностей, запускати pytest у поточному Docker-образі за існуючим repo-патерном.

### DoD

- Усі існуючі тести проходять.
- Local `956$u` працює як раніше.
- Invalid local path (`..`, absolute path) відхиляється як раніше.
- Local `956$q` additional файли працюють як раніше: без rename, без optimizer, non-fatal при помилці.
- Жодного Google dependency ще не додано.

### Статус 2026-05-23

- Додано `src/services/sources.py` з `ResolvedSource`, `SourceResolver`, `LocalMountSource` і `SourceResolutionError`.
- `src/core.py` використовує `SourceResolver` для primary `956$u`, cover `956$p` і additional `956$q`, але старий `_resolve_mount_relative_path()` лишено як compatibility wrapper.
- Local primary `956$u` зберігає `local_managed` lifecycle і поточний `version_and_move()` path.
- Local additional `956$q` використовує `local_unmanaged` lifecycle: без rename, без optimizer, non-fatal при missing/invalid/upload failure.
- Google URL parsing/download ще не додавалися; Google dependencies не додавалися.

## Ітерація 3: Google Drive URL parser для `956$u` і `956$q`

### Завдання

1. Додати parser для підтримуваних форматів:
   - `https://drive.google.com/file/d/<file_id>/view`
   - `https://drive.google.com/open?id=<file_id>`
   - `https://drive.google.com/uc?id=<file_id>`
   - URL з `resourcekey=<key>`
2. Явно відхиляти folder links:
   - `https://drive.google.com/drive/folders/...`
3. Явно відхиляти не-Google URL, якщо вони не є локальним відносним шляхом.
4. Застосувати parser для primary `956$u` і кожного елемента `956$q`.

### Перевірки

```bash
python3 -m py_compile src/services/sources.py tests/test_services.py
python3 -m pytest tests/test_services.py -q
```

### DoD

- Parser покритий unit-тестами для `956$u` і `956$q`.
- Немає мережевих викликів у parser-тестах.
- `resourcekey` зберігається в parsed result.

### Статус 2026-05-23

- Додано pure parser `GoogleDriveUrlParser` у `src/services/sources.py` без Google dependencies і без мережевих викликів.
- Підтримано `drive.google.com/file/d/<file_id>/view`, `drive.google.com/open?id=<file_id>` і `drive.google.com/uc?id=<file_id>`.
- `resourcekey` зберігається у parsed result і `ResolvedSource.diagnostics`.
- Folder links `drive.google.com/drive/folders/...` і сторонні HTTP/HTTPS URL явно відхиляються через `SourceResolutionError`.
- `SourceResolver.resolve_primary()` і `SourceResolver.resolve_additional()` повертають `source_type="gdrive"` та `lifecycle_policy="remote_ephemeral"` для Google Drive URL; локальні шляхи лишилися backward-compatible.
- Download/read-only Google API ще не реалізовано; це межа Ітерації 4.

## Ітерація 4: GoogleDriveSource read-only download для primary/additional

### Завдання

1. Додати офіційні Google dependencies у `requirements.txt`.
2. Реалізувати `GoogleDriveSource` для primary `956$u` і additional `956$q`:
   - читає service account JSON тільки з `GDRIVE_SERVICE_ACCOUNT_FILE`;
   - перевіряє `GDRIVE_ENABLED`;
   - читає metadata файлу (`name`, `mimeType`, `size`, `capabilities.canDownload`);
   - дозволяє тільки `application/pdf`;
   - перевіряє `GDRIVE_MAX_BYTES`;
   - завантажує у `*.part`;
   - після успішного download робить atomic rename у `.pdf`;
   - не робить write/update/delete у Google Drive.
3. Якщо URL має `resourcekey`, передавати його у Drive API згідно з контрактом Google Drive resource keys.
4. Помилки доступу, відсутності файлу, непідтримуваного mime type і перевищення розміру primary файлу мають давати коротке діагностичне повідомлення для `956$z`.
5. Помилки additional файлів з `956$q` мають потрапляти в `additional_files_failed` і, якщо потрібно, у короткий агрегований лог `956$z`, але не валити primary архівацію.

### Перевірки

```bash
python3 -m py_compile src/services/sources.py src/core.py tests/test_services.py tests/test_core.py
python3 -m pytest tests/test_services.py tests/test_core.py -q
```

Тести мають використовувати stub Google client, без реального Google API.

### DoD

- Немає реальних Google credentials у тестах.
- Partial download не використовується повторно.
- При помилці `.part` прибирається або лишається тільки для TTL cleanup з безпечною назвою.
- У логах немає секретів.

### Статус 2026-05-23

- Додано Google dependencies у `requirements.txt`: `google-api-python-client`, `google-auth`, `google-auth-httplib2`.
- Реалізовано `GoogleDriveSource` у `src/services/sources.py`: lazy readonly Drive client, service account тільки з `GDRIVE_SERVICE_ACCOUNT_FILE`, metadata checks, allowed MIME, max bytes, `capabilities.canDownload`, `resourcekey`, download у `.part` і atomic rename у `.pdf`.
- `GoogleDriveSource` підтримує stub client у тестах, тому parser/download тести не використовують реальний Google API і credentials.
- `process_integration_logic()` materialize-ить Google primary до локального temp PDF, не викликає `FileService.version_and_move()` для `remote_ephemeral` і не переміщує Google temp file у `Error` при критичній помилці.
- `run_dspace_workflow()` отримав `upload_name`, щоб DSpace bitstream для Google primary/additional використовував metadata name, а не temp basename.
- `956$q` Google additional скачується і upload-иться в ORIGINAL; download/upload помилки additional лишаються non-fatal через `additional_files_failed`.
- Cleanup старих final temp-файлів у `GDRIVE_TMP_DIR` ще не реалізовано; це межа Ітерації 5.

## Ітерація 5: Lifecycle, cleanup і error handling

### Завдання

1. Визначити staging-директорію для Google files: `GDRIVE_TMP_DIR`.
2. Додати cleanup для старих `.part` і завислих temp-файлів у `kdv-api`, не в `kdv-optimizer`.
3. Для локального primary `956$u` зберегти поточний lifecycle: `version_and_move()` при старті pipeline і `FileService.move_to_error()` після критичної помилки.
4. Для Google Drive primary `956$u` використовувати ephemeral lifecycle: файл лишається у `GDRIVE_TMP_DIR`, не переміщується в `Processed` і не переміщується в `Error`.
5. Для Google Drive additional `956$q` також використовувати ephemeral lifecycle: без rename, без optimizer, без `Processed/Error`.
6. При помилці Google Drive primary записувати статус/лог через існуючий механізм Koha `956$y=error` і `956$z`, без файлового move.
7. При помилці Google Drive additional записувати результат у `additional_files_failed`; primary workflow не валити, якщо primary успішний.
8. Якщо primary download не завершився, не створювати DSpace item.

### Перевірки

```bash
python3 -m pytest tests/test_core.py tests/test_services.py -q
```

Focused сценарії:

- Google Drive primary download success -> DSpace upload -> Koha success.
- Google Drive additional download success -> DSpace ORIGINAL upload без rename/optimizer.
- Google Drive primary permission denied -> Koha `956$y=error`, `956$z` короткий, без move у `Error`.
- Google Drive additional permission denied -> `additional_files_failed`, primary success не ламається.
- Google Drive primary too large -> error до optimizer, без move у `Error`.
- DSpace upload failure після Google Drive primary download -> Koha `956$y=error`, `956$z` короткий, temp-файл не переноситься в `Error`.

### DoD

- Немає повторного download, якщо існує валідний завершений temp-файл для того самого file id і metadata збігається.
- `.part` ніколи не вважається валідним PDF.
- Cleanup не чіпає файли поза `GDRIVE_TMP_DIR`.
- Google Drive files ніколи не переміщуються у локальні `Processed` або `Error` папки.

### Статус 2026-05-23

- `GoogleDriveSource` використовує deterministic cache path у `GDRIVE_TMP_DIR` на основі `file_id`, `resourcekey`, `name`, `mimeType` і `size`; якщо завершений `.pdf` валідний, повторний download не виконується.
- `.part` перед download прибирається і ніколи не вважається готовим PDF; при помилці download `.part` видаляється.
- Додано `GDRIVE_TMP_TTL_SECONDS=86400` у `.env.example`.
- Додано `GoogleDriveSource.cleanup_stale_files()`: видаляє лише старі regular files з suffix `.pdf`/`.part` всередині `GDRIVE_TMP_DIR`; файли поза директорією і сторонні suffix-и не чіпає.
- Core regression тести підтверджують: primary download failure/too-large не створює DSpace item, не викликає `move_to_error`; local primary lifecycle продовжує переносити файл у `Error` при критичній DSpace помилці.
- Google Drive final temp PDF лишається в `GDRIVE_TMP_DIR` до TTL cleanup і не переміщується в `Processed/Error`.

## Ітерація 6: Observability і runbooks

### Завдання

1. Додати structured logs без секретів:
   - source type;
   - file id hash або короткий safe id;
   - metadata size;
   - download duration;
   - failure reason.
2. Оновити README, ARCHITECTURE і релевантний runbook.
3. Описати manual smoke для dev.

### Перевірки

```bash
rg -n "GDRIVE|Google Drive|gdrive_service_account_json" README.md docs .env.example docker-compose.swarm.yml scripts/deploy-orchestrator-swarm.sh
git diff --check -- README.md docs .env.example docker-compose.swarm.yml scripts/deploy-orchestrator-swarm.sh
```

### DoD

- Оператор бачить, як перевірити secret file без виводу секрету.
- Описано rollback.
- Описано no-writeback policy.

## Ітерація 7: Dev smoke без витоку секретів

### Завдання

1. На dev Swarm node виконати deploy через `scripts/deploy-orchestrator-swarm.sh`.
2. Перевірити наявність secret file через `test -s`, без `cat`.
3. Запустити інтеграцію одного Koha запису з Google Drive PDF у `956$u`.
4. Запустити інтеграцію одного Koha запису зі змішаним `956$q`: локальний файл + Google Drive URL.
5. Перевірити DSpace bitstream, additional ORIGINAL файли, Koha `856`, Koha `956$y`, `956$z`.

### Перевірки

```bash
docker service ls --filter name=kdv_integrator_event
docker exec <kdv-api-container-id> sh -lc 'test -s /run/secrets/gdrive_service_account_json'
curl -fsS http://localhost:5000/kdv/api/health
```

Не друкувати secret file, OAuth token або service account JSON.

### DoD

- PDF з Google Drive у `956$u` завантажено в DSpace.
- Additional файл з Google Drive у `956$q` завантажено в DSpace ORIGINAL.
- Google Drive файли не змінено.
- Koha отримала `856` з DSpace link.
- У логах є source telemetry без секретів.

## Ризики

- Google Drive link може бути shared з користувачем, але не з service account.
- Link-shared файл може потребувати `resourcekey`.
- Google Docs/Sheets не є blob PDF; їх треба відхиляти на першій версії.
- Повторний запуск може знайти існуючий DSpace item і перейти в `linked_existing`; це має лишатися сумісним з поточним workflow.
- Temp-директорія може переповнитися, якщо cleanup не працює.

## Rollback

1. Вимкнути `GDRIVE_ENABLED=false` у runtime env.
2. Redeploy через `scripts/deploy-orchestrator-swarm.sh`.
3. Прибрати Google Drive URL з `956$u`/`956$q` або замінити на локальні відносні шляхи.
4. Переконатися, що `kdv-api` більше не має mounted secret у service spec.
5. Не видаляти Ansible Vault дані без окремого підтвердження.
