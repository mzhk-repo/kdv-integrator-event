# Runbook: scripts (kdv-integrator-event)

## `scripts/deploy-orchestrator-swarm.sh` (Swarm orchestrator)

### Бізнес-логіка
- Головний orchestration-скрипт для `ORCHESTRATOR_MODE=swarm`.
- Виконує pre-deploy перевірки: `healthcheck.sh` і `import src.config`.
- Runs `scripts/init-volume.sh` with the two required cover host paths before secret rendering and image builds; initialization failure stops deployment.
- Validates `COVERS_CDN_BASE_URL`, derives its Traefik hostname, pins `covers-cdn` to the local storage node and versions its Docker Config by content hash. Verifies CDN replicas after deployment; public TLS/redirect/origin acceptance follows [the CDN runbook](external-cover-integrator/runbook.md).
- Викликає `scripts/render-versioned-env-secret.sh` перед render manifest, щоб Swarm service отримував versioned runtime env secret з актуального `ORCHESTRATOR_ENV_FILE`.
- Перед оновленням стека ідемпотентно виправляє owner каталогів shared optimizer volume на `10001:10001` через локальний Swarm task; якщо чинний task відсутній на вузлі запуску, зупиняє deploy.
- Рендерить swarm manifest через `docker compose config` і виконує `docker stack deploy`.

### Ручний запуск
```bash
ENV_TMP="$(mktemp /tmp/env.redeploy.XXXXXX)"
chmod 600 "${ENV_TMP}"
sops --decrypt --input-type dotenv --output-type dotenv env.dev.enc > "${ENV_TMP}"
ORCHESTRATOR_MODE=swarm ENVIRONMENT_NAME=development ORCHESTRATOR_ENV_FILE="${ENV_TMP}" bash scripts/deploy-orchestrator-swarm.sh
echo $?
rm -f "${ENV_TMP}"
```

## `scripts/init-volume.sh` (host storage preparation)

Creates `COVERS_STORAGE_HOST_PATH`, its `assets/` and `.incoming/` directories,
and `COVER_STATE_HOST_PATH`. Repeated runs repair directory modes without changing
existing ownership or stored files: cover root and `assets/` use `0755`;
`.incoming/` and state use `0700`. New directories belong to the caller. The current
Integrator runs as container root; nginx will receive only `assets/`, read-only.

Both paths are required, absolute, disjoint and below a top-level directory.
Symlinks in the paths and managed children are rejected. The deployment account
must be able to create directories and change their modes. No privilege escalation
is performed. The orchestrator reads these values from its environment or selected
dotenv file without sourcing that file.

For an explicitly selected test environment, pass its actual storage paths:

```bash
COVERS_STORAGE_HOST_PATH=/tmp/kdv-cover-smoke/covers \
COVER_STATE_HOST_PATH=/tmp/kdv-cover-smoke/state \
  bash scripts/init-volume.sh
```

Initialization affects only the node running the script. Initialize every eligible
Swarm node or constrain future cover/state services to the prepared node. This
script prepares storage; Compose mounts and the CDN are implemented separately.

## `scripts/render-versioned-env-secret.sh` (deploy-adjacent, reusable)

### Бізнес-логіка
- Створює immutable Docker Swarm secret із dotenv-файла `ORCHESTRATOR_ENV_FILE`.
- Ім'я secret формується як `${RUNTIME_ENV_SECRET_BASE}_<sha256(env_file)[0:12]>`.
- У `stdout` друкує тільки shell export для orchestrator: `export KDV_APP_ENV_PAYLOAD_SECRET_NAME=...`.
- Логи пише у `stderr`, щоб результат можна було безпечно підхопити через `eval`.

### Ручний запуск
```bash
ENV_TMP="$(mktemp /tmp/env.secret.XXXXXX)"
chmod 600 "${ENV_TMP}"
sops --decrypt --input-type dotenv --output-type dotenv env.dev.enc > "${ENV_TMP}"
ORCHESTRATOR_ENV_FILE="${ENV_TMP}" RUNTIME_ENV_SECRET_BASE=kdv_app_env_payload scripts/render-versioned-env-secret.sh
rm -f "${ENV_TMP}"
```

## `scripts/healthcheck.sh` (Категорія 1а, validation)

### Бізнес-логіка
- Перевіряє статус сервісу `kdv-api` та endpoint `/kdv/api/health`.
- На першому деплої, коли контейнер ще не створений, повертає `exit 0` і пропускає перевірку.
- Для `docker compose ps` використовує той самий env-контекст, що й orchestrator: `ORCHESTRATOR_ENV_FILE`, або `SERVER_ENV`/`ENVIRONMENT_NAME` (`dev|development`, `prod|production`) з `env.*.enc`.

### Ручний запуск
```bash
ENVIRONMENT_NAME=development bash scripts/healthcheck.sh
echo $?
```

## `scripts/run-robot-swarm.sh` (manual Swarm wrapper для Robot)

### Бізнес-логіка
- Операційний wrapper для запуску `scripts/robot.py` у Docker Swarm runtime.
- Резолвить env-контекст у стилі `src/config.py`: `ORCHESTRATOR_ENV_FILE` → `SERVER_ENV`/`ENVIRONMENT_NAME` → `env.dev.enc`/`env.prod.enc` → `.env`.
- Передає env у `docker exec` через `--env-file`, щоб процес `robot.py` бачив `KDV_API_TOKEN` та інші runtime-змінні.
- Читає `STACK_NAME`/`SWARM_SERVICE_NAME` з env або використовує default `kdv_integrator_event_kdv-api`.
- Знаходить локальний контейнер `kdv-api` через label `com.docker.swarm.service.name`.
- Перевіряє health endpoint усередині контейнера.
- Копіює host candidates-файл у контейнер через `docker exec -i` як `/tmp/kdv-candidates.txt`.
- Запускає `python3 scripts/robot.py /tmp/kdv-candidates.txt` і прокидає додаткові CLI-параметри (`--skip-optimization`, `--parallelism`, `--max-wait`).
- Після реального запуску синхронізує `/app/logs/robot_batch.log` з контейнера у host `logs/robot_batch.log`.

### Ручний запуск
```bash
# Безпечна перевірка без старту batch:
SERVER_ENV=dev scripts/run-robot-swarm.sh --dry-run candidates.txt

# Реальний запуск batch:
SERVER_ENV=dev scripts/run-robot-swarm.sh candidates.txt --parallelism 1

# Production-контекст:
SERVER_ENV=prod scripts/run-robot-swarm.sh candidates.txt --parallelism 1 --max-wait 1800
```

## `scripts/validate_sops_encrypted.py` (out-of-scope, guard script)

### Бізнес-логіка
- Валідує, що env-файл дійсно зашифрований SOPS (`ENC[...]`, metadata) і не містить plaintext env-рядків.

### Ручний запуск
```bash
python3 scripts/validate_sops_encrypted.py env.dev.enc env.prod.enc
echo $?
```

## `scripts/entrypoint.sh` (out-of-scope, container entrypoint)

### Бізнес-логіка
- Стартовий wrapper контейнера: спочатку розгортає dotenv payload `/run/secrets/app_env_payload` у runtime ENV, потім зберігає сумісність зі старими one-secret-per-env файлами з `/run/secrets/*` і запускає основний процес (`exec "$@"`).

### Ручний запуск
```bash
bash scripts/entrypoint.sh env | rg '^KDV_'
```

## `scripts/nightwalker.py` (out-of-scope, audit/sync utility)

### Бізнес-логіка
- Нічний аудит Koha/DSpace:
- виявляє проблемні записи;
- перевіряє синхронізацію та за потреби оновлює metadata.

### Ручний запуск
```bash
# Авто-режим
docker compose exec kdv-api python3 -m src.nightwalker

# Діапазон
docker compose exec kdv-api python3 -m src.nightwalker 5000 5100
```

## `scripts/robot.py` (out-of-scope, batch integration utility)

### Бізнес-логіка
- Масовий запуск інтеграції бібліографічних записів через API (`/integrate/{id}`) з polling статусу задач.
- Підтримує контроль паралелізму та таймаутів через env (`ROBOT_*`).
- У Swarm runtime напряму не запускається оператором; для ручного запуску використовувати `scripts/run-robot-swarm.sh`.

### Ручний запуск
```bash
SERVER_ENV=dev scripts/run-robot-swarm.sh candidates.txt --parallelism 1
```
