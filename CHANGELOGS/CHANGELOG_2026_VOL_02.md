## 2026-04-09 — Docker Secrets migration: Крок 1 (розділення змінних)

- **Context:** Розпочато інкрементальну міграцію на Docker Swarm secrets; потрібно відокремити non-secret конфіг від чутливих даних без змін бізнес-логіки застосунку.
- **Change:** Додано файл `.env.public` із публічними змінними (`routing`, `URLs`, `auth mode`, `batch controls`, `image/network`, `mount/path`) на основі фактичного використання у `docker-compose.yml`, `src/config.py`, `src/wait_for_drive.sh`, `scripts/robot.py`, `scripts/nightwalker.py`.
- **Verification:** Виконано інвентаризацію ENV через `rg`/читання конфігів; окремо зафіксовано набір чутливих ключів для винесення в Docker secrets на наступному кроці.
- **Risks:** До завершення кроків 2-3 сервіс усе ще читає секрети зі старого `.env`; повний security-effect буде після підключення `entrypoint.sh` + `secrets:` у swarm compose.
- **Rollback:** Видалити `.env.public` та відкотити цей запис через `git revert <commit_sha>`.

## 2026-04-09 — Docker Secrets migration: Крок 2 (`entrypoint.sh`)

- **Context:** Для сумісності з поточним кодом потрібен wrapper, який перетворює Docker Swarm secrets з `/run/secrets/*` у звичайні ENV-перемінні перед стартом процесу.
- **Change:** Додано `scripts/entrypoint.sh` (читання всіх файлів із `/run/secrets` + `export` + `exec "$@"`). У `Dockerfile` оновлено `RUN chmod +x` для `scripts/entrypoint.sh` разом із `src/wait_for_drive.sh`.
- **Verification:** Файл скрипта створено у репозиторії; права executable для образу забезпечуються під час build через `chmod +x`.
- **Risks:** Перехід сервісу на цей wrapper ще не активований у swarm compose (це буде крок 3), тому поточний runtime не змінено.
- **Rollback:** Видалити `scripts/entrypoint.sh` і відкотити зміну `Dockerfile` через `git revert <commit_sha>`.

## 2026-04-09 — Docker Secrets migration: Крок 3 (Swarm compose + Ansible mapping)

- **Context:** Потрібно активувати Docker Secrets у Swarm без переписування застосунку, зберігши сумісність через `entrypoint` wrapper.
- **Change:** Оновлено `docker-compose.swarm.yml`: сервіс `kdv-api` переведено на `entrypoint: /app/scripts/entrypoint.sh`, `command` подано у list-формі для запуску `/app/src/wait_for_drive.sh` + `gunicorn`, `env_file` переведено на `.env.public`, додано сервісний блок `secrets` для всіх secret ENV (`KDV_API_TOKEN`, `CF_ACCESS_*`, `KOHA_*`, `DSPACE_*`) і глобальний блок `secrets` з `external: true` та мапінгом на swarm secret names. Додатково внесено зміни в `/opt/Ansible/ansible/inventories/dev/group_vars/all/swarm_secrets.vault.yml` (vault-змінні для нових секретів) та `/opt/Ansible/ansible/inventories/dev/group_vars/all/vars.yml` (розширено `docker_secrets` mapping).
- **Verification:** `docker compose -f docker-compose.yml -f docker-compose.swarm.yml config` проходить успішно; у згенерованій конфігурації присутні новий `entrypoint`, сервісний `secrets` і глобальний `secrets` mapping із `external: true`.
- **Risks:** Фактичний runtime-перехід відбудеться тільки після створення/оновлення secrets у Swarm через Ansible та повторного deploy stack; до цього можливі помилки запуску через відсутні external secrets.
- **Rollback:** Відкотити зміни у `docker-compose.swarm.yml` та ansible-файлах (`swarm_secrets.vault.yml`, `vars.yml`) через `git revert <commit_sha>` (або вручну прибрати додані secret entries).

## 2026-04-09 — Docker Secrets: deploy stack + runtime verification

- **Context:** Під час першого деплою після кроку 3 сервіс `kdv-api` у Swarm падав, бо поточний GHCR image не містив `/app/scripts/entrypoint.sh`.
- **Change:** Для сумісності без rebuild/push image у `docker-compose.swarm.yml` додано bind-mount ` /opt/kdv-integrator/kdv-integrator-event/scripts/entrypoint.sh:/app/scripts/entrypoint.sh:ro` (manager-only через існуючий placement constraint) та надано executable-права `scripts/entrypoint.sh` на хості. Через Ansible (`playbooks/swarm.yml --tags secrets`) застосовано `docker_secrets` у Swarm.
- **Verification:** `docker secret ls` містить усі потрібні секрети (`kdv_api_token`, `cf_access_*`, `koha_*`, `dspace_*`); `docker service ls` для `kdv_integrator_event_kdv-api` показує `1/1`; `docker exec <cid> ls /run/secrets` містить 9 expected secret files; auth-check із токеном з `/run/secrets/KDV_API_TOKEN` повернув `HTTP=404 {"status":"not_found"}` (не `401`), що підтверджує коректну auth-конфігурацію; `GET /kdv/api/health` і `GET /kdv/api/ready` всередині контейнера повернули `HTTP=200`.
- **Risks:** Деплой виконувався через rendered config (`docker compose ... config | sed '/^name:/d' | docker stack deploy -c - ...`) через несумісність `pull_policy`/`name` з `docker stack deploy`; при релізі нового image бажано прибрати bind-mount `entrypoint.sh` і покладатися на файл у самому образі.
- **Rollback:** Відкотити останні зміни `docker-compose.swarm.yml` і повернути попередній `service update`/`stack deploy` або виконати `git revert <commit_sha>` для цієї правки.

## 2026-04-09 — Auth loop fix: runtime працював у `legacy` через втрату public ENV

- **Context:** Після міграції на secrets зʼявився цикл Cloudflare авторизації: після успішного login API продовжував вимагати авторизацію. Runtime-лог показував `Auth denied: legacy mode...`, хоча очікувався `dual`.
- **Root cause:** Під час deploy використовувався pipeline `docker compose ... config | docker stack deploy -c - ...`; у згенерованому manifest `env_file` не передавався в stack runtime, тому `KDV_AUTH_MODE` та інші public ENV не потрапляли в процес. У результаті застосунок підхоплював fallback/старі значення (`legacy`) і не приймав CF cookie flow так, як очікувалось.
- **Change:** У `docker-compose.swarm.yml` додано явний `environment:` блок для ключових public ENV (`KDV_AUTH_MODE`, `KDV_CORS_ALLOWLIST`, `KOHA_*`, `DSPACE_*`, batch controls, `TZ`, `INTEGRATOR_MOUNT_PATH`). Stack перезадеплоєно через `docker compose --env-file .env.public -f docker-compose.yml -f docker-compose.swarm.yml config | sed '/^name:/d' | docker stack deploy -c - kdv_integrator_event`.
- **Verification:** `docker service ls` -> `kdv_integrator_event_kdv-api 1/1`; `/proc/1/environ` містить `KDV_AUTH_MODE` (`len=4`), `KDV_CORS_ALLOWLIST`, `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`; логи після запиту без auth показують `Auth denied: dual mode ...`; token-auth (`X-KDV-TOKEN` з `/run/secrets/KDV_API_TOKEN`) повертає `HTTP=404` на тестовий `status/nonexistent` endpoint (тобто auth пройдено).
- **Risks:** Команда deploy для цього проєкту тепер має запускатися саме з `--env-file .env.public`; без цього можливий повтор drift по public ENV.
- **Rollback:** Відкотити `environment`-блок у `docker-compose.swarm.yml` та повернути попередній deploy flow через `git revert <commit_sha>`.

## 2026-04-09 — Docs update: lessons learned по Docker Secrets + новий runbook

- **Context:** Після практичної міграції на Docker Secrets зʼявились важливі lessons learned (несумісність `stack deploy` з окремими compose-полями, runtime drift для `KDV_AUTH_MODE`, відсутній `entrypoint.sh` у старому image), які потрібно закріпити в документації для наступних репозиторіїв.
- **Change:** Повністю оновлено `docs/DOCKER-SECRETS.md` у форматі практичного migration guide з розділом типових помилок і перевіреним deploy-пайплайном (`--env-file .env.public`, rendered config, явний `environment`). Додано новий документ `docs/RUNBOOK_DOCKER_SECRETS.md` з покроковою інструкцією додавання нового секрета (Ansible vault/vars mapping, swarm compose wiring, deploy, verification, troubleshooting, rollback).
- **Verification:** Перевірено наявність і читабельність обох документів у `docs/`; структура узгоджена з існуючими runbook-доками репозиторію.
- **Risks:** Без синхронного використання рекомендованих deploy-команд у CI/CD можливий повтор runtime drift (зокрема по public ENV).
- **Rollback:** Відкотити зміни документації через `git revert <commit_sha>` або вручну повернути попередні версії `docs/DOCKER-SECRETS.md` і видалити `docs/RUNBOOK_DOCKER_SECRETS.md`.

## 2026-04-09 — Docs: `DOCKER-SECRETS.md` зроблено універсальним

- **Context:** Потрібна інструкція, яку можна переносити між різними репозиторіями без згадок конкретного проєкту, доменів, stack-name або абсолютних шляхів.
- **Change:** `docs/DOCKER-SECRETS.md` переписано як універсальний migration guide: прибрано repo-specific приклади; додано нейтральні шаблони `compose`, `Ansible`, deploy-команд і верифікації; залишено лише переносимі практики та anti-patterns.
- **Verification:** Перевірено, що документ не містить прив'язок до конкретного репозиторію/шляху і придатний як шаблон для інших проєктів.
- **Risks:** Для конкретного проєкту все одно треба підставити власні імена сервісів, secret names, шляхи та stack name.
- **Rollback:** Відкотити зміни `docs/DOCKER-SECRETS.md` через `git revert <commit_sha>`.
