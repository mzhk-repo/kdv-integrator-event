# Runbook: MAYDAY (Production інциденти)

Мета: дати простий і швидкий план дій, коли "щось зламалось" у production.

Цей документ про відновлення сервісу, а не про розробку фіч.

## 1) Швидкий старт (перші 5-10 хвилин)

1. Зафіксувати симптом: що саме не працює (`/integrate`, `/status`, Koha UI, Cloudflare доступ).
2. Перевірити стан контейнерів:

```bash
docker compose ps
```

3. Перевірити liveness/readiness:

```bash
./scripts/healthcheck.sh
```

4. Переглянути останні логи сервісу:

```bash
docker compose logs --tail=200
```

5. Якщо після діагностики сервіс явно в битому стані, зробити відновлення:

```bash
docker compose pull
docker compose up -d
```

## 2) Базова діагностика (що дивимось завжди)

- `GET /kdv/api/health`: чи живий процес API.
- `GET /kdv/api/ready`: чи готовий mount path (`INTEGRATOR_MOUNT_PATH`) до читання/запису.
- Помилки в логах: `ERROR`, `Traceback`, `Unauthorized`, `timeout`, `Connection refused`.
- Чи не зник/не відвалився підмонтований диск.

## 3) Каталог інцидентів і відновлення

### 3.1 API не відповідає або healthcheck падає

Ознаки:
- `./scripts/healthcheck.sh` повертає помилку.
- `docker compose ps` показує `Restarting` або `Exited`.

Дії:
1. Подивитись причину в логах: `docker compose logs --tail=200`.
2. Перезапустити на актуальному образі: `docker compose pull && docker compose up -d`.
3. Повторити healthcheck.

Якщо не допомогло:
1. Перевірити `.env` (обов'язкові змінні, особливо `KDV_API_TOKEN`, `RCLONE_REMOTE_NAME`, `KOHA_*`, `DSPACE_*`).
2. Відкотитись на попередній стабільний тег (див. секцію rollback).

### 3.1.1 Swarm: `kdv-api` у `0/1`, public health повертає 404

**Scope:** цей сценарій для Swarm stack `kdv_integrator_event`. Public `https://repo.pinokew.buzz/kdv/api/health` без Cloudflare Access сесії штатно повертає `302` на login; `404` або інша помилка після авторизації може означати, що `kdv-api` не має живого task.

Діагностика:

```bash
docker service ls
docker service ps kdv_integrator_event_kdv-api --no-trunc
docker plugin ls
```

1. Якщо `docker service ps` показує `missing plugin`, перевірити rclone volume plugin і remote, не виводячи вміст `rclone.conf`:

```bash
docker plugin inspect rclone:latest --format 'enabled={{.Enabled}} reference={{.PluginReference}}'
docker service inspect kdv_integrator_event_kdv-api --format '{{json .Spec.TaskTemplate.ContainerSpec.Mounts}}'
rclone --config /var/lib/docker-plugins/rclone/config/rclone.conf listremotes
```

Service очікує `DriverConfig.Options.remote` (у production — `gdrive-library:`). Цей remote має бути в plugin-конфігурації. Якщо plugin disabled, а `rclone.conf`/cache збережені, відновлення виконує оператор з правами Docker і доступом до `/var/lib/docker-plugins`:

```bash
docker plugin disable rclone:latest
docker plugin rm rclone:latest
docker plugin install --disable rclone/docker-volume-rclone:amd64 --alias rclone --grant-all-permissions args="-v"
docker plugin enable rclone:latest
docker plugin ls
```

Перед `docker plugin rm` обов'язково зробити резервні копії `/var/lib/docker-plugins/rclone/config` і `/var/lib/docker-plugins/rclone/cache`. Не копіювати та не публікувати вміст `rclone.conf`: він може містити OAuth tokens.

2. Якщо plugin `ENABLED true`, але task відхилено з `No such image: kdv-integrator-event:<tag>`, відтворити саме image tag зі service spec з checkout того ж commit:

```bash
docker service inspect kdv_integrator_event_kdv-api --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}'
git rev-parse --short=12 HEAD
docker build -t kdv-integrator-event:<tag-from-service-spec> .
docker service update --force kdv_integrator_event_kdv-api
docker service ps kdv_integrator_event_kdv-api --no-trunc
```

`<tag-from-service-spec>` має дорівнювати short SHA поточного checkout. Якщо це не так, не збирати образ з іншого commit: спочатку переключити checkout на commit, з якого було зроблено deploy, або виконати штатний `scripts/deploy-orchestrator-swarm.sh` для узгодженого релізу.

Перевірка після відновлення:

```bash
docker ps --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api
docker exec <container-id> curl -i http://127.0.0.1:5000/kdv/api/health
```

Очікувано: Swarm task у стані `Running`, локальний endpoint повертає `HTTP/1.1 200` і JSON зі `status: "ok"`. Публічний URL без Access сесії має повертати `302` на Cloudflare Access; після успішного входу — health JSON.

### 3.2 Readiness = not_ready (drive/mount недоступний)

Ознаки:
- `/kdv/api/ready` повертає `503`.
- В payload видно, що mount path не існує або нема прав `read/write`.

Дії:
1. Перевірити, що rclone Docker volume plugin встановлений на Docker host.
2. Перевірити, що `RCLONE_REMOTE_NAME` збігається з remote у `rclone config`.
3. Перевірити значення `INTEGRATOR_MOUNT_PATH` у runtime env: для контейнера має бути `/mnt/drive`.
4. Перевірити права доступу для користувача контейнера.
5. Після виправлення перезапустити `docker compose pull && docker compose up -d`.

### 3.3 Cloudflare 524 / великі таймаути з браузера

Ознаки:
- Користувачі бачать 524.
- Локально контейнер може бути живим, але відповіді до edge не доходять вчасно.

Дії:
1. Перевірити, що API живий локально через `./scripts/healthcheck.sh`.
2. Перевірити логи на довгі операції (зависання на Koha/DSpace або файловій операції).
3. Перевірити доступність зовнішніх залежностей (Koha/DSpace) і mount path.
4. Для термінового відновлення: зменшити навантаження (тимчасово зупинити масовий batch/nightwalker), дочекатися очищення черги.
	- Для batch використовуйте менші значення `ROBOT_PARALLELISM` (зазвичай `1`) і більші `ROBOT_BATCH_DELAY`.

### 3.4 401/403 через Cloudflare Access

Ознаки:
- У браузері `Unauthorized` або `Forbidden`.
- У логах відмови в auth-перевірці.

Дії:
1. Підтвердити режим auth (`KDV_AUTH_MODE`) і очікувану поведінку (`legacy`/`dual`/`cf-only`).
2. Перевірити валідність `CF_ACCESS_TEAM_DOMAIN` і `CF_ACCESS_AUD`.
3. Перевірити, що користувач має валідну Access-сесію.
4. У `dual` режимі перевірити server-to-server виклики з `X-KDV-TOKEN`.

### 3.5 CORS помилки в Koha UI

Ознаки:
- У консолі браузера помилки CORS/preflight.

Дії:
1. Перевірити `KDV_CORS_ALLOWLIST` (домен Koha має бути в allowlist).
2. Перевірити, що `OPTIONS` проходить і повертає коректний `Access-Control-Allow-Origin`.
3. Для browser-flow перевірити `withCredentials` і доступність Cloudflare Access cookie.

### 3.6 Інтеграції застрягають у `processing` або падають масово

Ознаки:
- Багато задач довго не переходять у terminal state.
- Масові `error` по Koha/DSpace.

Дії:
1. Подивитись `docker compose logs --tail=200` і знайти `task_id` проблемних задач.
2. Визначити вузол проблеми: Koha, DSpace, файловий доступ, або cover pipeline.
3. Якщо проблема у зовнішньому сервісі: обмежити/поставити на паузу batch до стабілізації.
4. Після стабілізації виконати контрольний інтеграційний запуск на 1-2 записах.

### 3.7 Koha інциденти (CGI/REST)

Ознаки:
- Помилки автентифікації до Koha API.
- Немає оновлення полів 856/956 або обкладинка не прикріплюється.

Дії:
1. Перевірити `KOHA_API_URL`, `KOHA_OPAC_URL`, `KOHA_API_USER`, `KOHA_API_PASS`.
2. Перевірити у логах, чи падає REST update чи CGI upload.
3. Якщо падає лише обкладинка: пам'ятати, що covers best-effort, critical-path це DSpace workflow.
4. Зафіксувати кейси для наступного контрактного тесту (M6).

### 3.8 DSpace інциденти (pid/patch/upload)

Ознаки:
- Падає імпорт у DSpace, немає handle, файли йдуть у Error folder.

Дії:
1. Перевірити `DSPACE_API_URL`, `DSPACE_UI_URL`, `DSPACE_API_USER`, `DSPACE_API_PASS`.
2. Перевірити мережевий доступ до DSpace і помилки авторизації/таймаутів у логах.
3. Після виправлення зробити повторний тест на 1 записі і переконатися, що 856/956 оновлюються.

### 3.9 Різкий ріст 5xx або деградація швидкодії

Ознаки:
- Збільшення помилок API, ріст часу відповіді.

Дії:
1. Перевірити останні деплой-зміни і конфіги.
2. Перевірити чергу задач і зовнішні залежності (Koha/DSpace/mount).
3. Тимчасово зменшити batch-навантаження.
4. Якщо є регресія після релізу, виконати rollback.

## 4) Rollback (безпечне повернення)

Коли робити rollback:
- Після деплою з'явився стабільний `5xx`, `401/403`, або масові помилки інтеграцій.
- Немає швидкого локального фіксу в межах інциденту.

Базовий план:
1. Вибрати попередній стабільний git тег (`vMAJOR.MINOR.PATCH`).
2. На сервері перемкнутись на цей тег (або на попередній image digest, якщо deploy через registry image).
3. Запустити:

```bash
docker compose pull
docker compose up -d
./scripts/healthcheck.sh
docker compose logs --tail=200
```

4. Підтвердити відновлення ключового сценарію: `POST /integrate/{id}` + polling `GET /status/{task_id}`.

Нотатка:
- Детальний release/canary/rollback flow див. у `docs/RELEASE.md`.

## 5) Післяінцидентні дії (обов'язково)

1. Додати запис у активний `CHANGELOGS/CHANGELOG_2026_VOL_01.md`:
- **Context**
- **Change**
- **Verification**
- **Risks**
- **Rollback**

2. Якщо інцидент торкався security/ops/network:
- оновити `docs/ARCHITECTURE.md` та/або цей runbook.

3. Якщо виявлено прогалину тестів:
- додати issue на M6 (contract tests) або M8 (метрики/аналітика).

## 6) Швидкі команди (шпаргалка)

```bash
git status
docker compose ps
docker compose pull
docker compose up -d
./scripts/healthcheck.sh
docker compose logs --tail=200
pytest -q
ruff check .
```
