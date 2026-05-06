## 2026-04-24 — Healthcheck env resolution for Swarm orchestrator

- **Context:** Під час ручного запуску `deploy-orchestrator-swarm.sh` з `ORCHESTRATOR_ENV_FILE` pre-deploy `healthcheck.sh` викликав `docker compose ps` без env-файлу, через що Compose логував warning-и для `INTEGRATOR_MOUNT_PATH`, `HOST`, `PATH_PREFIX`, `EXTERNAL_NETWORK` і падав на порожньому bind mount spec.
- **Change:** `scripts/healthcheck.sh` тепер резолвить env-контекст у тому самому порядку, що й orchestrator/config bootstrap: `ORCHESTRATOR_ENV_FILE` -> `SERVER_ENV`/`ENVIRONMENT_NAME` -> `env.dev|prod` -> `env.dev|prod.enc` через `sops` -> `.env`. Усі `docker compose ps` виклики запускаються з `--env-file`, якщо файл знайдено. Оновлено `docs/scripts_runbook.md` для ручного запуску з `ENVIRONMENT_NAME=development`.
- **Verification:** `bash -n scripts/healthcheck.sh`; запуск `ORCHESTRATOR_ENV_FILE=<decrypted env.dev.enc> bash scripts/healthcheck.sh` повернув first-deploy `INFO` без Compose warning-ів; запуск `ENVIRONMENT_NAME=development bash scripts/healthcheck.sh` також пройшов без warning-ів.
- **Risks:** Якщо `env.*.enc` треба розшифрувати автоматично, на хості має бути доступний `sops` і відповідний ключ. У штатному orchestration flow ризик мінімальний, бо вже передається готовий `ORCHESTRATOR_ENV_FILE`.
- **Rollback:** Відкотити зміни `scripts/healthcheck.sh`, `docs/scripts_runbook.md` і цей запис через `git revert <commit_sha>` або вручну повернути попередній прямий виклик `docker compose ps`.

## 2026-05-05 — Rclone Docker volume plugin замість host bind mount

- **Context:** Інтегратор більше не має залежати від host-side `rclone mount` і абсолютного host-шляху в `INTEGRATOR_MOUNT_PATH`; потрібно задавати тільки назву remote з `rclone config`, яку обслуговує встановлений на хості rclone Docker volume plugin. Також bind `entrypoint.sh` для Swarm має задаватися відносно репозиторію, а не абсолютним шляхом.
- **Change:** `docker-compose.yml` переведено з bind mount `${INTEGRATOR_MOUNT_PATH}:/mnt/drive:rslave` на named volume `kdv-drive` з `driver: rclone` і обов'язковим `RCLONE_REMOTE_NAME` у `driver_opts.remote`; `.env` у `env_file` зроблено optional для коректної валідації з `--env-file .env.example`. `docker-compose.swarm.yml` отримав такий самий volume definition і default для `ENTRYPOINT_SCRYPT_PATH` як `./scripts/entrypoint.sh`. `.env.example`, `README.md`, `docs/RUNBOOK_MAYDAY.md` і `docs/RUNBOOK_TESTING.md` оновлено під новий контракт: зовні задається `RCLONE_REMOTE_NAME`, а runtime `INTEGRATOR_MOUNT_PATH` лишається `/mnt/drive` всередині контейнера.
- **Verification:** `docker compose --env-file .env.example -f docker-compose.yml config`; `docker compose --env-file .env.example -f docker-compose.yml -f docker-compose.swarm.yml config`.
- **Risks:** На кожному Docker host/Swarm node, де може стартувати сервіс, має бути встановлений і налаштований rclone Docker volume plugin з доступом до відповідного `rclone.conf`; без цього Docker не створить volume. Якщо remote має іншу назву, потрібно оновити `RCLONE_REMOTE_NAME` в env.
- **Rollback:** Повернути bind mount `${INTEGRATOR_MOUNT_PATH}:/mnt/drive:rslave`, прибрати top-level `volumes.kdv-drive`, повернути host-path у env і відкотити документацію/changelog через `git revert <commit_sha>`.

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

## 2026-04-23 — Scripts refactoring: активовано pre-deploy healthcheck в Swarm orchestrator

- **Context:** У roadmap (`docs/scrypts_refactoring.md`) була неактуальна примітка для цього репозиторію: `deploy-orchestrator-swarm.sh` уже підключений у CI, але `healthcheck.sh` не запускався в оркестраторі перед рендерингом Swarm manifest.
- **Change:** Оновлено `scripts/deploy-orchestrator-swarm.sh`: додано `run_validation_scripts()` з запуском `scripts/healthcheck.sh` у pre-deploy фазі (перед `docker compose config`), оновлено warning для fallback на `.env` за узгодженим формулюванням, додано sanitize `published: "80"` → `published: 80` у deploy-manifest. Виправлено таблицю категоризації в `docs/scrypts_refactoring.md` для `healthcheck.sh` (1а) і `deploy-orchestrator-swarm.sh` (active CI orchestration).
- **Verification:** `bash -n scripts/deploy-orchestrator-swarm.sh`; `rg -n "run_validation_scripts|Fallback на локальний \\.env|published:" scripts/deploy-orchestrator-swarm.sh`; `rg -n "/opt/kdv-integrator/kdv-integrator-event|healthcheck\\.sh|deploy-orchestrator-swarm\\.sh" docs/scrypts_refactoring.md`.
- **Risks:** `healthcheck.sh` залежить від стану локального контейнера/compose-контексту; на першому деплої скрипт штатно пропускає перевірку, але в нестандартному runtime-контексті може зупинити deploy.
- **Rollback:** `git revert <commit_sha>` для цього набору правок або вручну прибрати `run_validation_scripts()` з оркестратора, відкотити warning/sanitize-блок і повернути попередні рядки в `docs/scrypts_refactoring.md`.

## 2026-04-23 — Scripts refactoring: Крок 3 (Категорія 1б) для kdv-integrator-event

- **Context:** Для Кроку 3 потрібно зафіксувати послідовність `1а -> 1б -> docker compose config -> docker stack deploy`. У цьому репозиторії фактичних скриптів Категорії 1б у `scripts/` наразі немає.
- **Change:** У `scripts/deploy-orchestrator-swarm.sh` додано явну фазу `run_deploy_adjacent_scripts()` (no-op з інформаційним логом) і інтегровано її після `run_validation_scripts()`. Таким чином порядок виконання в оркестраторі тепер явно відповідає контракту Кроку 3.
- **Verification:** `bash -n scripts/deploy-orchestrator-swarm.sh`; `rg -n "run_validation_scripts|run_deploy_adjacent_scripts|Rendering Swarm manifest" scripts/deploy-orchestrator-swarm.sh`.
- **Risks:** До появи реальних 1б-скриптів цей етап лише логує пропуск; якщо додавати нові deploy-adjacent скрипти в майбутньому, потрібно підключити їх саме у `run_deploy_adjacent_scripts()`.
- **Rollback:** Відкотити зміни файлу `scripts/deploy-orchestrator-swarm.sh` через `git revert <commit_sha>` або вручну видалити `run_deploy_adjacent_scripts()` і її виклик.

## 2026-04-23 — Config bootstrap: підтримка `SERVER_ENV`/`ORCHESTRATOR_ENV_FILE` + pre-deploy валідація

- **Context:** Потрібно, щоб `src/config.py` працював із dev/prod контекстом (`SERVER_ENV`, `ORCHESTRATOR_ENV_FILE`, `env.dev.enc/env.prod.enc`) та мав ідемпотентний механізм завантаження ENV.
- **Change:** У `src/config.py` додано ідемпотентний bootstrap (`bootstrap_environment()`) з пріоритетом джерел: `ORCHESTRATOR_ENV_FILE` -> `SERVER_ENV` (`env.dev`/`env.prod`) -> `env.dev.enc`/`env.prod.enc` (best-effort decrypt через `sops` + `SOPS_AGE_KEY_FILE`) -> `.env`. Для тимчасово розшифрованого файлу додано `atexit` cleanup. У `scripts/deploy-orchestrator-swarm.sh` додано pre-deploy перевірку `run_python_config_validation()` (`import src.config`) як частину фази 1а.
- **Verification:** `python3 -m py_compile src/config.py`; `bash -n scripts/deploy-orchestrator-swarm.sh`; `KDV_API_TOKEN=x KOHA_API_URL=http://koha.local KOHA_OPAC_URL=http://opac.local KOHA_API_USER=u KOHA_API_PASS=p DSPACE_API_URL=http://dspace.local DSPACE_UI_URL=http://dspace-ui.local DSPACE_API_USER=u DSPACE_API_PASS=p ORCHESTRATOR_ENV_FILE=/tmp/nonexistent SERVER_ENV=dev python3 -c "import src.config; print('ok')"` -> `ok`.
- **Risks:** Якщо для `SERVER_ENV` існує лише `env.<env>.enc`, але на хості немає `sops` або AGE ключа, `config.py` тихо не завантажить цей файл і перейде до наступного fallback (`.env`/`os.environ`); тому для CI/Swarm критично передавати валідний `ORCHESTRATOR_ENV_FILE`.
- **Rollback:** Відкотити коміт через `git revert <commit_sha>` або вручну прибрати bootstrap-блок із `src/config.py` і функцію `run_python_config_validation()` із оркестратора.

## 2026-04-23 — Redeploy + scripts runbook

- **Context:** Потрібно було виконати ручний редеплой через `scripts/deploy-orchestrator-swarm.sh`; після успіху — створити `docs/scripts_runbook.md`.
- **Change:** Виконано редеплой у режимі `swarm` з `ORCHESTRATOR_ENV_FILE`, розшифрованим з `env.dev.enc` у форматі dotenv (`--input-type dotenv --output-type dotenv`). Додано новий документ `docs/scripts_runbook.md` з бізнес-логікою та командами ручного запуску для всіх скриптів у `scripts/`.
- **Verification:** Логи редеплою завершились рядком `Swarm deploy completed`; `docker stack deploy` оновив сервіс `kdv_integrator_event_kdv-api`. Файл `docs/scripts_runbook.md` створено.
- **Risks:** При спробі деплою з fallback на локальний `.env` можливий провал `docker compose config` через відсутність частини swarm-змінних; для операційного запуску використовувати розшифрований `env.dev.enc`/`env.prod.enc`.
- **Rollback:** Відкотити запис та runbook через `git revert <commit_sha>` або вручну видалити `docs/scripts_runbook.md`.

## 2026-05-05 — Swarm deploy verification після `docker stack deploy`

- **Context:** GitHub CI показував успішний deploy, хоча `kdv_integrator_event_kdv-api` залишався `0/1`: `docker service ps` показав `Rejected` з помилкою `No such image: ghcr.io/mzhk-repo/kdv-integrator-event:latest`. Причина в тому, що `docker stack deploy` лише створює/оновлює service object і запускає tasks у фоні, а orchestrator не перевіряв фактичний стан replicas.
- **Change:** У `scripts/deploy-orchestrator-swarm.sh` додано post-deploy перевірку `verify_swarm_service()`, яка після `docker stack deploy` очікує desired replicas для `${STACK_NAME}_kdv-api`, а при таймауті друкує `docker service ls` і `docker service ps --no-trunc` та завершує CI з помилкою.
- **Verification:** `bash -n scripts/deploy-orchestrator-swarm.sh`; поточний Swarm стан підтвердив першопричину: `kdv_integrator_event_kdv-api 0/1`, task `Rejected`, `No such image`.
- **Risks:** Якщо pull образу або старт контейнера займає довше за дефолтні `180s`, потрібно підняти `SWARM_VERIFY_TIMEOUT`; тепер CI коректно падатиме при runtime-проблемах Swarm service.
- **Rollback:** Відкотити правки `scripts/deploy-orchestrator-swarm.sh`, `.github/workflows/main.yml` і цей запис changelog через `git revert <commit_sha>` або вручну прибрати post-deploy verification.

## 2026-05-05 — Локальний Docker build для Swarm deploy без GHCR pull

- **Context:** Повторний ручний redeploy підтвердив, що приватний `ghcr.io/mzhk-repo/kdv-integrator-event:latest` існує, але Docker host/Swarm не має registry-доступу для digest/pull. Для цього інтегратора сервіс прив'язаний до конкретної ноди поруч із Koha/DSpace, тому push/pull через registry перед кожним deploy є зайвим операційним ризиком.
- **Change:** `scripts/deploy-orchestrator-swarm.sh` переведено на дефолтний `ORCHESTRATOR_IMAGE_MODE=local`: перед render manifest виконується `docker build -t kdv-integrator-event:local`, далі експортується `KDV_IMAGE=kdv-integrator-event:local`, а `docker stack deploy` запускається з `--resolve-image never`. Registry path залишено доступним через `ORCHESTRATOR_IMAGE_MODE=registry`. У `.github/workflows/main.yml` повернуто `build_and_push_docker: false`, бо образ тепер збирається на deploy-host.
- **Verification:** `bash -n scripts/deploy-orchestrator-swarm.sh`.
- **Risks:** Локальний образ має існувати саме на ноді, де Swarm запускає task; placement constraint `node.labels.app_zone == manager` лишається критичним. Для multi-node/replicated deploy потрібно повернутися до registry mode або забезпечити image на кожній ноді.
- **Rollback:** Встановити `ORCHESTRATOR_IMAGE_MODE=registry`, прибрати local build/export/`--resolve-image never` із orchestrator і знову ввімкнути registry build/push у workflow.

## 2026-05-05 — Cleanup тимчасових Swarm manifest-файлів

- **Context:** Після перерваного `deploy-orchestrator-swarm.sh` у корені репозиторію могли лишатися `.kdv_integrator_event.stack.raw.*.yml` і `.kdv_integrator_event.stack.deploy.*.yml`.
- **Change:** У `scripts/deploy-orchestrator-swarm.sh` додано глобальний `cleanup()` з `trap cleanup EXIT`, який прибирає поточні temp-файли `RAW_MANIFEST`/`DEPLOY_MANIFEST`/`RUNTIME_ENV_FILE` і stale manifest-файли для поточного `STACK_NAME` незалежно від exit code.
- **Verification:** `bash -n scripts/deploy-orchestrator-swarm.sh`.
- **Risks:** Cleanup видаляє лише файли з pattern `.${STACK_NAME}.stack.raw.*.yml` і `.${STACK_NAME}.stack.deploy.*.yml` у `PROJECT_ROOT`; не зберігати вручну важливі файли з такими іменами.
- **Rollback:** Прибрати `cleanup()`, `trap cleanup EXIT` і повернути локальні `mktemp`/`trap RETURN` для manifest-файлів.

## 2026-05-05 — Env contract для local image mode

- **Context:** Після переходу Swarm deploy на локальний образ `pull_policy: always` у базовому compose став суперечити local-image контракту, а `.env.example` не містив нових orchestrator-змінних.
- **Change:** З `docker-compose.yml` прибрано `pull_policy: always`. У `.env.example` додано `ORCHESTRATOR_IMAGE_MODE` (`local|registry`), `LOCAL_IMAGE`, `SWARM_VERIFY_TIMEOUT` і `SWARM_VERIFY_INTERVAL` з приміткою щодо допустимих режимів.
- **Verification:** `docker compose --env-file .env.example -f docker-compose.yml config`; `docker compose --env-file .env.example -f docker-compose.yml -f docker-compose.swarm.yml config`; `bash -n scripts/deploy-orchestrator-swarm.sh`.
- **Risks:** Для registry-mode pull тепер не форсується через compose `pull_policy`; якщо потрібен примусовий pull у non-Swarm локальному запуску, його треба виконувати явно через `docker compose pull`.
- **Rollback:** Повернути `pull_policy: always` і прибрати нові orchestrator-змінні з `.env.example`.

## 2026-05-06 — Versioned runtime env secret і git-SHA local image

- **Context:** Після зміни `CF_ACCESS_TEAM_DOMAIN` у `env.prod.enc` CI зібрав локальний образ і `docker stack deploy` завершився успішно, але контейнер не перечитав нові секрети. Причина: Docker Swarm secrets immutable, а service spec посилався на те саме external secret name; також статичний local tag `kdv-integrator-event:local` не гарантував rolling update при code-only deploy.
- **Change:** `scripts/deploy-orchestrator-swarm.sh` тепер читає orchestrator-змінні з `ORCHESTRATOR_ENV_FILE`, якщо вони не задані як shell ENV; для local image mode `LOCAL_IMAGE=auto` будує `kdv-integrator-event:<git-sha>`. Перед render manifest створюється versioned Docker secret `${RUNTIME_ENV_SECRET_BASE}_<sha256(env_file)[0:12]>`, експортується `KDV_APP_ENV_PAYLOAD_SECRET_NAME` з новим іменем, і Swarm отримує зміну service spec для rolling update. У `.env.example` додано `RUNTIME_ENV_SECRET_BASE` і змінено `LOCAL_IMAGE=auto`.
- **Verification:** `bash -n scripts/deploy-orchestrator-swarm.sh`.
- **Risks:** Versioned secrets накопичуються в Docker; старі secret-и, які вже не використовуються services, потрібно періодично прибирати окремим maintenance-кроком. Якщо явно задати статичний `LOCAL_IMAGE`, code-only redeploy може потребувати ручного `docker service update --force`.
- **Rollback:** Повернути статичний `LOCAL_IMAGE`, прибрати `prepare_runtime_env_secret()` і знову використовувати стабільний `KDV_APP_ENV_PAYLOAD_SECRET_NAME`.

## 2026-05-06 — Reusable script для versioned Swarm env secret

- **Context:** Логіку створення versioned `app_env_payload` потрібно уніфікувати для повторного використання в інших репозиторіях, а не тримати inline у `deploy-orchestrator-swarm.sh`.
- **Change:** Додано `scripts/render-versioned-env-secret.sh`: скрипт читає `ORCHESTRATOR_ENV_FILE`, визначає `RUNTIME_ENV_SECRET_BASE`, створює Docker secret `${base}_<sha256(env_file)[0:12]>` і друкує shell export `KDV_APP_ENV_PAYLOAD_SECRET_NAME` у `stdout`. `scripts/deploy-orchestrator-swarm.sh` тепер викликає цей скрипт перед render manifest і застосовує export через `eval`. Оновлено `docs/scripts_runbook.md`.
- **Verification:** `bash -n scripts/render-versioned-env-secret.sh`; `bash -n scripts/deploy-orchestrator-swarm.sh`.
- **Risks:** Скрипт створює Docker secrets на поточному Swarm manager; для dry-run потрібен окремий режим або запуск у тестовому Swarm. `stdout` зарезервований під export-рядки, тому додаткові логи в цьому скрипті мають писатися тільки в `stderr`.
- **Rollback:** Повернути inline-функцію створення secret в orchestrator і видалити `scripts/render-versioned-env-secret.sh` та runbook-запис.

## 2026-05-06 — Koha REST auth/server errors стали діагностичними

- **Context:** Під час інтеграції запису `biblionumber=1` лог показував `No 956 field found`, хоча фактична причина була `HTTP 401 {"error":"Basic authentication disabled"}` від Koha REST API. Через повернення `None` з `_get_biblio_xml()` auth failure маскувався під відсутність MARC-поля.
- **Change:** У `src/koha.py` додано `KohaRestError` і sanitized обробку REST-відповідей: `401`, `403` і `5xx` тепер логуються зі status code та короткою причиною без секретів і пробрасываются як виняток. `No 956 field found` лишається для випадку, коли MARCXML отримано, але поле `956` справді відсутнє. Додано unit test на `HTTP 401 Basic authentication disabled`.
- **Verification:** `python3 -m py_compile src/koha.py src/core.py`; `docker run --rm --entrypoint python -v /opt/kdv-integrator/kdv-integrator-event:/work -w /work ... kdv-integrator-event:4cbbab5be27f -m pytest tests/test_core.py -q` -> `4 passed`.
- **Risks:** Runtime тепер одразу показуватиме Koha REST auth/server failure як task error; це змінює текст помилки, але не успішний path.
- **Rollback:** Прибрати `KohaRestError`/`_handle_rest_error()` і повернути попереднє `return None` для non-200 REST-відповідей.
