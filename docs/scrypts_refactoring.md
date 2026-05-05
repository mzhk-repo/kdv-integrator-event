# Інструкція для ШІ-агента: Рефакторинг папки scripts/ під архітектуру Swarm + SOPS

## Контекст проєкту

Організація перейшла на єдину кодову базу для dev та prod середовищ у межах усіх репозиторіїв. Конфігурації зберігаються у зашифрованих SOPS-файлах (`env.dev.enc`, `env.prod.enc`). Спільний CI/CD workflow (GitHub Actions, `/opt/shared-workflows/`) підключається до сервера через SSH, розшифровує потрібний файл у `$ORCHESTRATOR_ENV_FILE` (за замовчуванням `/tmp/env.decrypted`) і запускає `scripts/deploy-orchestrator-swarm.sh` з такими змінними оточення:

- `ORCHESTRATOR_ENV_FILE` — шлях до розшифрованого env-файлу (задається workflow)
- `ENVIRONMENT_NAME` — `development` або `production`
- `ORCHESTRATOR_MODE=swarm` — активує swarm-деплой
- `STACK_NAME` — ім'я docker stack

Серверне середовище (`dev` або `prod`) встановлюється Ansible-плейбуком у `/etc/environment` через змінну `SERVER_ENV`. Це єдине джерело істини для автономних скриптів (cron, backup).

**Твоя задача:** провести аудит і рефакторинг папки `scripts/` у поточному репозиторії згідно з таблицею scope нижче. Виконуй кроки послідовно і чекай підтвердження після кожного.

---

## Scope: перелік репозиторіїв

| Репозиторій | Swarm-стек | Цільовий оркестратор | Примітки |
|---|---|---|---|
| `/opt/Traefik/` | ✅ | `deploy-orchestrator-swarm.sh` | Мінімальний набір |
| `/opt/kdv-integrator/kdv-integrator-event/` | ✅ | `deploy-orchestrator-swarm.sh` | перевірити *.py файли які беруть змінні з env (зокрема src/config.py)|
| `/opt/Dspace/DSpace-docker/` | ✅ | `deploy-orchestrator-swarm.sh` | Є setup-configs, patch-скрипти, backup/restore |
| `/opt/Koha/koha-deploy/` | ✅ | `deploy-orchestrator-swarm.sh` | Найбільший набір, складні backup-скрипти |
| `/opt/Matomo-analytics/` | ✅ | `deploy-orchestrator-swarm.sh` | apply-matomo-config потребує окремої уваги |
| `/opt/victoriametrics-grafana/` | ✅ | `deploy-orchestrator-swarm.sh` | render-scrape-config читає `.env` напряму |

---

## Загальні правила (обов'язкові для всіх репозиторіїв)

### Правило 1 — Не чіпати

Ці файли виключені з рефакторингу в усіх репо:

- `scripts/validate_sops_encrypted.py` — вже коректно реалізована, без shell-виконання, змін не потребує
- `scripts/deploy-orchestrator.sh` — legacy-скрипт залишається без змін; усі нові інтеграції йдуть тільки у `deploy-orchestrator-swarm.sh`
- `scripts/entrypoint.sh` — Docker ENTRYPOINT контейнера, не чіпати
- `scripts/nightwalker.py`, `scripts/robot.py` (kdv-integrator) — Python-логіка застосунку, поза scope

### Правило 2 — Безпечне завантаження env (заборона source для ненадійних файлів)

**Заборонено** використовувати `source` або `. "$ENV_FILE"` для файлів, що прийшли з CI (`ORCHESTRATOR_ENV_FILE`), оскільки це еквівалентно виконанню довільного shell-коду.

**Дозволено** читати env лише через:

```bash
# 1. Для скриптів Категорії 1б — передавати через --env-file у docker compose:
docker compose --env-file "${ORCHESTRATOR_ENV_FILE}" ...

# 2. Для скриптів Категорії 1б — читати окремі змінні через grep (без eval):
read_env_var() {
  local key="$1" file="$2"
  grep -m1 "^${key}=" "${file}" | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}
MY_VAR="$(read_env_var "MY_VAR" "${ORCHESTRATOR_ENV_FILE}")"
```

**Виняток:** `source` дозволено тільки у скриптах Категорії 2 (Автономні) для локально розшифрованого файлу в `/dev/shm` (чому — см. Правило 4), та у legacy-скриптах `verify-env.sh` де це є необхідним для перевірки всіх змінних — залишити як є, не рефакторити.

### Правило 3 — Визначення середовища: контракт `resolve_environment()`

Функцію `resolve_environment()` додавати тільки у скрипти Категорії 2 (Автономні). Пріоритет: CLI arg → `$SERVER_ENV` → помилка.

```bash
resolve_environment() {
  local env="${1:-${SERVER_ENV:-}}"
  case "${env}" in
    dev|development)  echo "dev"  ;;
    prod|production)  echo "prod" ;;
    *)
      echo "ERROR: environment unknown: '${env}'." \
           "Set SERVER_ENV in /etc/environment (via Ansible) or pass as \$1." >&2
      exit 1
      ;;
  esac
}
ENVIRONMENT="$(resolve_environment "${1:-}")"
```

`SERVER_ENV` задається Ansible-плейбуком (`/opt/Ansible/`) в `/etc/environment` через задачу у ролі `system_bootstrap`. Cron автоматично читає `/etc/environment`, тому аргумент передавати не треба.

### Правило 4 — Безпечна локальна розшифровка SOPS (Категорія 2)

Використовувати `/dev/shm` замість `/tmp` (RAM, не диск). `shred` — опційно, як best-effort.

```bash
_decrypt_env() {
  local enc_file="$1"
  local age_key_file="${SOPS_AGE_KEY_FILE:-${HOME}/.config/age/keys.txt}"

  if [[ ! -f "${enc_file}" ]]; then
    echo "ERROR: encrypted env file not found: ${enc_file}" >&2
    exit 1
  fi
  if [[ ! -f "${age_key_file}" ]]; then
    echo "ERROR: AGE key file not found: ${age_key_file}" >&2
    exit 1
  fi

  ENV_TMP="$(mktemp /dev/shm/env-XXXXXX)"
  chmod 600 "${ENV_TMP}"
  trap '_cleanup_env_tmp' EXIT

  sops --decrypt --age-key-file "${age_key_file}" "${enc_file}" > "${ENV_TMP}"
}

_cleanup_env_tmp() {
  if [[ -n "${ENV_TMP:-}" && -f "${ENV_TMP}" ]]; then
    command -v shred >/dev/null 2>&1 && shred -u "${ENV_TMP}" 2>/dev/null || rm -f "${ENV_TMP}"
  fi
}
```

Після виклику `_decrypt_env` завантажувати через `source "${ENV_TMP}"` (це вже безпечно — файл власний, у RAM).

### Правило 5 — Fallback на `.env` для локального dev (Категорія 1б)

Існуючий fallback у `deploy-orchestrator-swarm.sh` **зберегти**, але покращити повідомлення щоб явно вказувати причину:

```bash
if [[ ! -f "${ENV_FILE}" ]]; then
  if [[ -f ".env" ]]; then
    ENV_FILE=".env"
    log "WARNING: env.*.enc не знайдено або ORCHESTRATOR_ENV_FILE не передано. Fallback на локальний .env — тільки для dev-середовища."
  else
    log "ERROR: env file not found (${ORCHESTRATOR_ENV_FILE:-/tmp/env.decrypted}) and .env missing"
    exit 1
  fi
fi
```

### Правило 6 — Мінімально-інвазивний рефакторинг складних скриптів

Для скриптів з великою бізнес-логікою (>100 рядків: `backup.sh`, `restore.sh`, `patch-*.sh`):

- **Змінювати лише** блок завантаження env (`load_env()` або аналогічний розділ на початку)
- **Не чіпати** логіку резервного копіювання, перевірки цілісності, PITR, offsite-copy тощо
- Якщо функція `load_env()` відсутня — **виділити** env-завантаження в окрему функцію і замінити тільки її

### Правило 7 — Ідемпотентність

Кожен скрипт після рефакторингу повинен витримувати повторний запуск без помилок і без небажаних side-effects:
- `mkdir -p` замість `mkdir`
- Перевірка наявності запису перед `echo >>` або `sed -i`
- Генерація конфігів через tmp-файл + `cmp` → перезапис тільки при змінах

---

## Класифікація скриптів: три категорії

| Категорія | Назва | Опис | Джерело env |
|---|---|---|---|
| **1а** | Validation | Перевірки без секретів: порти, env-наявність, права | Не потребують |
| **1б** | Deploy-adjacent | Pre-deploy хуки що викликаються з оркестратора | `ORCHESTRATOR_ENV_FILE` (через `--env-file` або `read_env_var`) |
| **2** | Autonomous | Cron/backup/restore, запускаються поза CI | Локальна розшифровка SOPS через `SERVER_ENV` |

**Важливо:** скрипти Категорії 1а **не потребують** SOPS-блоку. Додавати env-завантаження туди **заборонено** — вони є preflight і мають виконуватись до будь-яких секретів.

---

## КРОК 1: Аналіз та Категоризація

1. Перелічи всі файли в `scripts/` поточного репозиторію
2. Для кожного файлу визнач категорію (1а / 1б / 2) або статус (out-of-scope / gap)
3. Перевір відповідність до таблиці нижче:

### Очікувана категоризація по репозиторіях

**`/opt/Traefik/`**

| Файл | Категорія | Примітка |
|---|---|---|
| `init-volumes.sh` | 1б | Читає `VOL_LOGS_PATH` з env-файлу |
| `validate_sops_encrypted.py` | out-of-scope | Не змінювати |
| `deploy-orchestrator.sh` | out-of-scope | Legacy, не змінювати |

**`/opt/kdv-integrator/kdv-integrator-event/`**

| Файл | Категорія | Примітка |
|---|---|---|
| `healthcheck.sh` | 1а | Pre-deploy валідація; викликається з `deploy-orchestrator-swarm.sh` |
| `deploy-orchestrator-swarm.sh` | out-of-scope | Активований у CI (`.github/workflows/main.yml`) як orchestration script |
| `entrypoint.sh` | out-of-scope | Docker ENTRYPOINT |
| `nightwalker.py`, `robot.py` | out-of-scope | Python-логіка застосунку |
| `validate_sops_encrypted.py` | out-of-scope | Не змінювати |

**`/opt/Dspace/DSpace-docker/`**

| Файл | Категорія | Примітка |
|---|---|---|
| `verify-env.sh` | 1а | Перевірка змінних, source дозволено тут |
| `smoke-test.sh`, `test-login.sh` | 1а | Перевірки без секретів |
| `init-volumes.sh` | 1б | Ініціалізація bind-mount директорій |
| `setup-configs.sh` | 1б | Wrapper для patch-скриптів; env наслідує від оркестратора |
| `patch-local.cfg.sh`, `patch-config.yml.sh`, `patch-submission-forms.sh` | 1б | Патчать конфіги; викликаються через setup-configs.sh |
| `backup-dspace.sh` | 2 | Cron |
| `restore-backup.sh`, `run-maintenance.sh`, `sync-user-groups.sh` | 2 | Ручний або cron-запуск |
| `bootstrap-admin.sh` | out-of-scope | One-time setup |
| `entrypoint.sh` | out-of-scope | Docker ENTRYPOINT |
| `validate_sops_encrypted.py`, `deploy-orchestrator.sh` | out-of-scope | Не змінювати |

**`/opt/Koha/koha-deploy/`**

| Файл | Категорія | Примітка |
|---|---|---|
| `check-internal-ports-policy.sh` | 1а | Перевірка портів без секретів |
| `check-ports-policy.sh` | 1а | Аналогічно |
| `check-secrets-hygiene.sh` | 1а | Перевірка без секретів |
| `verify-env.sh` | 1а | source дозволено тут |
| `init-volumes.sh` | 1б | Ініціалізація bind-mount директорій |
| `bootstrap-live-configs.sh` | 1б | Генерація конфігів |
| `koha-lockdown-password-prefs.sh` | 1б | Harden налаштувань |
| `patch/` | 1б | Патч-скрипти |
| `backup.sh` | 2 | **Складний скрипт**: PITR, binlogs, offsite. Змінювати тільки `load_env()` |
| `restore.sh` | 2 | Складний, змінювати тільки env-блок |
| `collect-docker-logs.sh` | 2 | Cron |
| `test-smtp.sh` | 2 | Ручний запуск |
| `install-collect-logs-timer.sh` | out-of-scope | One-time systemd-setup |
| `validate_sops_encrypted.py`, `deploy-orchestrator.sh` | out-of-scope | Не змінювати |

**`/opt/Matomo-analytics/`**

| Файл | Категорія | Примітка |
|---|---|---|
| `verify-env.sh` | 1а | source дозволено тут |
| `check-ports-policy.sh` | 1а | Перевірка без секретів |
| `check-disk.sh` | 1а | Перевірка без секретів |
| `init-volumes.sh` | 1б | Ініціалізація bind-mount директорій |
| `apply-matomo-config.sh` | 1б | **Потребує окремої уваги** — зараз source через $1; замінити на `read_env_var` |
| `backup.sh` | 2 | Змінювати тільки env-блок |
| `restore.sh`, `test-restore.sh` | 2 | Змінювати тільки env-блок |
| `validate_sops_encrypted.py`, `deploy-orchestrator.sh` | out-of-scope | Не змінювати |

**`/opt/victoriametrics-grafana/`**

| Файл | Категорія | Примітка |
|---|---|---|
| `check-internal-ports-policy.sh` | 1а | Перевірка без секретів |
| `init-volumes.sh` | 1б | Ініціалізація bind-mount директорій |
| `render-scrape-config.sh` | 1б | **Потребує окремої уваги** — зараз читає з `.env` напряму; замінити на `ORCHESTRATOR_ENV_FILE` |
| `collect-matomo-archiving-metric.sh`, `collect-matomo-db-size.sh` | 1б | Збір метрик, викликаються з оркестратора |
| `backup-victoriametrics-volume.sh` | 2 | Cron/ручний |
| `restore-victoriametrics-backup.sh`, `test-victoriametrics-restore.sh` | 2 | Ручний запуск |
| `validate_sops_encrypted.py`, `deploy-orchestrator.sh` | out-of-scope | Не змінювати |

**Артефакт Кроку 1:** Надай перелік файлів з категоріями та gap-list (відсутні очікувані скрипти, нестандартні репо). Не переходь до Кроку 2 без підтвердження.

---

## КРОК 2: Рефакторинг Категорії 1а (Validation-скрипти)

Скрипти Категорії 1а не отримують секретів і не потребують env-завантаження. Для них:

1. **Не додавати** SOPS-блок, `source` чи `read_env_var`
2. **Перевірити ідемпотентність** (Правило 7)
3. **Перевірити інтеграцію** в `deploy-orchestrator-swarm.sh`: якщо скрипт є pre-deploy check — він має викликатись **першим**, до Категорії 1б

**Артефакт Кроку 2:** Список скриптів 1а з висновком: вже коректно інтегровані / потребують додавання виклику в оркестратор.

---

## КРОК 3: Рефакторинг Категорії 1б (Deploy-adjacent скрипти)

Для кожного скрипта Категорії 1б:

### 3.1 Стандартні скрипти (init-volumes.sh та подібні)

Замінити пряме читання `.env` на читання з `ORCHESTRATOR_ENV_FILE`:

```bash
# Було: ENV_FILE="${ENV_FILE:-${PROJECT_ROOT}/.env}"
# Стало:
ENV_FILE="${ORCHESTRATOR_ENV_FILE:-}"
if [[ -z "${ENV_FILE}" || ! -f "${ENV_FILE}" ]]; then
  if [[ -f "${PROJECT_ROOT}/.env" ]]; then
    ENV_FILE="${PROJECT_ROOT}/.env"
    echo "[init-volumes] WARNING: ORCHESTRATOR_ENV_FILE не передано. Fallback на .env — тільки для dev." >&2
  else
    echo "[init-volumes] ERROR: env file не знайдено. Передай ORCHESTRATOR_ENV_FILE або поклади .env." >&2
    exit 1
  fi
fi
# Читати змінні через grep, без source:
vol_logs_path="$(read_env_var "VOL_LOGS_PATH" "${ENV_FILE}")"
```

Додати виклик у `deploy-orchestrator-swarm.sh` у функцію `deploy_swarm()`, **після** скриптів 1а і **до** `docker compose config`.

### 3.2 Специфіка для `apply-matomo-config.sh` (Matomo)

Цей скрипт зараз отримує env через `source "$1"`. Після рефакторингу він читає змінні через `read_env_var` з `ORCHESTRATOR_ENV_FILE`, а викликається з оркестратора без аргументів:

```bash
# Замінити:
# ENV_FILE="${1:-.env}"
# source "$ENV_FILE"
# На:
ENV_FILE="${ORCHESTRATOR_ENV_FILE:-}"
if [[ -z "${ENV_FILE}" || ! -f "${ENV_FILE}" ]]; then
  ENV_FILE="${PROJECT_ROOT}/.env"
  echo "[apply-matomo-config] WARNING: fallback на .env" >&2
fi
# Далі читати кожну змінну через read_env_var, не через source
MATOMO_DB_HOST="$(read_env_var "MATOMO_DB_HOST" "${ENV_FILE}")"
# ... і так далі для всіх потрібних змінних
```

### 3.3 Специфіка для `render-scrape-config.sh` (VictoriaMetrics)

Зараз скрипт читає URL-и напряму з `$ROOT_DIR/.env`. Замінити на `ORCHESTRATOR_ENV_FILE`:

```bash
# Замінити блок:
# ENV_FILE="$ROOT_DIR/.env"
# if [[ -z "${KOHA_OPAC_URL:-}" ]] && [[ -f "$ENV_FILE" ]]; then
#   KOHA_OPAC_URL="$(grep '^KOHA_OPAC_URL=' "$ENV_FILE" | ...)"
# На:
ENV_FILE="${ORCHESTRATOR_ENV_FILE:-${ROOT_DIR}/.env}"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[render-scrape-config] WARNING: ORCHESTRATOR_ENV_FILE не передано, fallback на .env" >&2
  ENV_FILE="${ROOT_DIR}/.env"
fi
if [[ -z "${KOHA_OPAC_URL:-}" ]]; then
  KOHA_OPAC_URL="$(read_env_var "KOHA_OPAC_URL" "${ENV_FILE}")"
fi
```

### 3.4 Специфіка для `setup-configs.sh` та `patch-*.sh` (DSpace)

`setup-configs.sh` є wrapper-ом і **не потребує** власного env-завантаження — він викликається з оркестратора, тому успадковує змінні оточення процесу. Patch-скрипти аналогічно.

Переконатись, що `setup-configs.sh` викликається після завантаження env в оркестраторі.

### 3.5 Генерація конфігів (якщо є)

Якщо скрипт генерує файл конфігурації (`.yml`, `.cfg`, `.xml`):

```bash
tmp_file="$(mktemp)"
trap 'rm -f "${tmp_file}"' EXIT
# ... генерація у tmp_file ...
if ! cmp -s "${tmp_file}" "${OUTPUT_FILE}"; then
  mv "${tmp_file}" "${OUTPUT_FILE}"
  echo "Config updated: ${OUTPUT_FILE}"
else
  echo "Config unchanged: ${OUTPUT_FILE}"
fi
```

**Артефакт Кроку 3:**
- Оновлений код кожного скрипта 1б
- Оновлений `deploy-orchestrator-swarm.sh` з викликами в правильному порядку: 1а → 1б → `docker compose config` → `docker stack deploy`
- Bash-команди для ручного тестування кожного скрипта:
  - Розшифровка: `sops --decrypt env.dev.enc > /tmp/env.test && chmod 600 /tmp/env.test`
  - Запуск: `ORCHESTRATOR_ENV_FILE=/tmp/env.test bash scripts/<script>.sh`
  - Перевірка: `diff <expected> <generated>` або `echo $?`

---

## КРОК 4: Рефакторинг Категорії 2 (Автономні скрипти)

Ці скрипти запускаються поза CI (cron, ручний запуск) і не мають доступу до `ORCHESTRATOR_ENV_FILE` від пайплайну.

### 4.1 Шаблон для всіх Автономних скриптів

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- Допоміжні функції ---
log()  { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die()  { log "ERROR: $*" >&2; exit 1; }

resolve_environment() {
  local env="${1:-${SERVER_ENV:-}}"
  case "${env}" in
    dev|development)  echo "dev"  ;;
    prod|production)  echo "prod" ;;
    *)
      die "environment unknown: '${env}'. Set SERVER_ENV in /etc/environment (via Ansible) or pass as \$1."
      ;;
  esac
}

_cleanup_env_tmp() {
  if [[ -n "${ENV_TMP:-}" && -f "${ENV_TMP}" ]]; then
    command -v shred >/dev/null 2>&1 && shred -u "${ENV_TMP}" 2>/dev/null || rm -f "${ENV_TMP}"
  fi
}

_decrypt_env() {
  local enc_file="$1"
  local age_key_file="${SOPS_AGE_KEY_FILE:-${HOME}/.config/age/keys.txt}"
  [[ -f "${enc_file}" ]]    || die "encrypted env file not found: ${enc_file}"
  [[ -f "${age_key_file}" ]] || die "AGE key file not found: ${age_key_file}"

  ENV_TMP="$(mktemp /dev/shm/env-XXXXXX)"
  chmod 600 "${ENV_TMP}"
  trap '_cleanup_env_tmp' EXIT
  sops --decrypt --age-key-file "${age_key_file}" "${enc_file}" > "${ENV_TMP}"
}

# --- Основна логіка ---
ENVIRONMENT="$(resolve_environment "${1:-}")"
ENC_FILE="${PROJECT_ROOT}/env.${ENVIRONMENT}.enc"

_decrypt_env "${ENC_FILE}"
# shellcheck source=/dev/null
set -a; source "${ENV_TMP}"; set +a

# ... далі бізнес-логіка скрипта ...
```

### 4.2 Специфіка для складних скриптів (Правило 6)

Для `backup.sh` (Koha), `restore.sh` та подібних:

- Знайти функцію `load_env()` або блок завантаження env на початку скрипта
- **Замінити тільки цей блок** на `_decrypt_env` + `source`
- Усю іншу логіку (PITR, binlogs, checksum, offsite, retention) **не змінювати**

### 4.3 Налаштування cron

`SERVER_ENV` автоматично читається з `/etc/environment` у cron-сесії:

```cron
# /etc/cron.d/<repo>-backup  (або crontab -e для відповідного користувача)
# Аргумент не потрібен — SERVER_ENV береться з /etc/environment
0 2 * * * /opt/Koha/koha-deploy/scripts/backup.sh >> /var/log/koha-backup.log 2>&1
```

Якщо cron не читає `/etc/environment` (перевірити у конкретній системі):

```cron
0 2 * * * SERVER_ENV=prod /opt/Koha/koha-deploy/scripts/backup.sh >> /var/log/koha-backup.log 2>&1
```

**Артефакт Кроку 4:**
- Оновлений код кожного автономного скрипта (змінений тільки env-блок)
- Команди для ручного тестування: `SERVER_ENV=dev bash scripts/backup.sh` або `bash scripts/backup.sh dev`

---

## КРОК 5: Документація та Ранбуки (Runbooks)

Створити `docs/scripts_runbook.md` у поточному репозиторії. Для **кожного** скрипта (з усіх трьох категорій) два обов'язкові розділи:

### Розділ 1 — Бізнес-логіка (Business Logic)

- Що робить скрипт
- Які файли або конфіги він модифікує
- Яка його роль у lifecycle застосунку (pre-deploy / cron / ручний)

### Розділ 2 — Інструкція з ручного запуску (Manual Execution)

**Для Категорії 1а:**
```bash
# Запустити pre-deploy перевірку вручну:
bash scripts/check-internal-ports-policy.sh
```

**Для Категорії 1б:**
```bash
# 1. Розшифрувати env у тимчасовий файл:
ENV_TMP="$(mktemp /dev/shm/env-XXXXXX)" && chmod 600 "${ENV_TMP}"
sops --decrypt env.dev.enc > "${ENV_TMP}"

# 2. Запустити скрипт:
ORCHESTRATOR_ENV_FILE="${ENV_TMP}" bash scripts/init-volumes.sh

# 3. Знищити тимчасовий файл:
shred -u "${ENV_TMP}" 2>/dev/null || rm -f "${ENV_TMP}"
```

**Для Категорії 2:**
```bash
# Запустити з явним середовищем або через SERVER_ENV:
bash scripts/backup.sh dev
# або якщо SERVER_ENV вже є в оточенні:
bash scripts/backup.sh
```

**Артефакт Кроку 5:** Готовий контент для `docs/scripts_runbook.md`.

---

## Definition of Done (критерії приймання)

Для кожного відрефакторованого скрипта:

- [ ] Перший запуск завершується з `exit 0`
- [ ] Повторний запуск (`2-й раз`) завершується з `exit 0` без помилок та без дублювання (ідемпотентність)
- [ ] Запуск з навмисно відсутнім env-файлом дає зрозуміле повідомлення і `exit 1`
- [ ] Для Категорії 1б: `ORCHESTRATOR_ENV_FILE=/tmp/nonexistent bash scripts/<script>.sh` → помилка з повідомленням
- [ ] Для Категорії 2: запуск з невідомим аргументом середовища → помилка з повідомленням
- [ ] `shellcheck scripts/<script>.sh` не дає нових попереджень порівняно з оригіналом
- [ ] `validate_sops_encrypted.py` та `deploy-orchestrator.sh` не змінені
