# Runbook: scripts (kdv-integrator-event)

## `scripts/deploy-orchestrator-swarm.sh` (Swarm orchestrator)

### Бізнес-логіка
- Головний orchestration-скрипт для `ORCHESTRATOR_MODE=swarm`.
- Виконує pre-deploy перевірки: `healthcheck.sh` і `import src.config`.
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
- Стартовий wrapper контейнера: експортує secrets з `/run/secrets/*` у змінні оточення і запускає основний процес (`exec "$@"`).

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

### Ручний запуск
```bash
docker compose exec kdv-api python3 -m src.robot
```
