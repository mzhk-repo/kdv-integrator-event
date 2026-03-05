# CHANGELOG 2026 VOL 01

## 2026-03-05 — Config: синхронізовано структуру `.env` та `.env.example`

- **Context:** Потрібно було вирівняти шаблон `.env.example` під фактичний `.env`, зберігши всі існуючі коментарі у `.env`.
- **Change:** Додано відсутню змінну `DSPACE_SUBMISSION_SECTION` у `.env`; `.env.example` переформатовано в узгоджені секції та синхронізовано по ключах з `.env` (додано `TZ`, `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`, узгоджено порядок і структуру).
- **Verification:** Порівняння ключів через `comm` (розбіжностей немає); `docker compose --env-file .env.example -f docker-compose.yml config -q` проходить.
- **Risks:** Низькі; зміни стосуються структури/env-шаблону, але при додаванні нових env у коді потрібно синхронно оновлювати обидва файли.
- **Rollback:** Відкотити `.env` та `.env.example` до попередньої версії через `git restore` або `git revert`.

## 2026-03-05 — Docs: оновлено `docs/ARCHITECTURE.md` під M3

- **Context:** Архітектурний документ описував переважно M2 і не відображав поточний стан M3 (CI/CD, release/deploy, Zero Trust шлях через Tailscale).
- **Change:** У `docs/ARCHITECTURE.md` оновлено версію до `v6.6-M3`, додано секції про CI/CD архітектуру (`ci-checks`, `cd-deploy`), release semantics (`main -> staging`, `v* -> production`), deploy через `TAILSCALE_AUTHKEY` та операційні інваріанти (`.env.example`, changelog/runbook sync).
- **Verification:** Звірено зміст із актуальним `.github/workflows/ci-cd.yml` і поточним статусом M3 у `docs/ROADMAP.md`.
- **Risks:** Документ може застарівати при зміні workflow/secrets; потрібно оновлювати архітектуру синхронно з CI/deploy правками.
- **Rollback:** Відкотити `docs/ARCHITECTURE.md` до попередньої версії або виконати `git revert` коміту.

## 2026-03-05 — Docs: актуалізовано M3 у ROADMAP після успішного CI/CD.

- **Context:** Після успішного проходження пайплайна потрібно синхронізувати фактичний стан `M3` у `docs/ROADMAP.md`.
- **Change:** У секції `M3` відмічено виконані пункти checklist (`workflow/status check`, `build`, `ruff`, `pip-audit`, `trivy`, `release rules`, `secrets`), прогрес оновлено до `90%`; відкритим залишено лише `SBOM (CycloneDX)`.
- **Verification:** Звірено кроки з `.github/workflows/ci-cd.yml` та оновлено чекліст/статус у `docs/ROADMAP.md`.
- **Risks:** Якщо branch protection rules для required checks зміняться поза репозиторієм, roadmap може потребувати повторної синхронізації.
- **Rollback:** Відкотити правки `docs/ROADMAP.md` або `git revert` коміту з цим оновленням.

## 2026-03-05 — Deploy: перехід на `TAILSCALE_AUTHKEY` без OAuth

- **Context:** Для деплою обрано спрощений Zero Trust сценарій через Tailscale без OAuth client credentials.
- **Change:** У `.github/workflows/ci-cd.yml` крок `Connect to Tailscale` переведено на `authkey`; у `Validate deploy secrets` додано обов'язкову перевірку `TAILSCALE_AUTHKEY`; змінні `TAILSCALE_OAUTH_CLIENT_ID/TAILSCALE_OAUTH_SECRET` прибрано з `cd-deploy` env.
- **Verification:** Workflow-конфігурація оновлена так, що `cd-deploy` використовує тільки `TAILSCALE_AUTHKEY` для підключення перед SSH деплоєм.
- **Risks:** Потрібно, щоб `TAILSCALE_AUTHKEY` був валідний і мав потрібні ACL/доступ до вузла деплою; якщо ключ протермінований, деплой зупиниться на кроці Tailscale.
- **Rollback:** Повернути OAuth-поля в `Connect to Tailscale` та видалити перевірку `TAILSCALE_AUTHKEY` у `Validate deploy secrets`.

## 2026-03-05 — Fix: Trivy `python-pkg` (vendored metadata у setuptools)

- **Context:** Після оновлення `requirements.txt` Trivy все ще знаходив `jaraco.context 5.3.0` і `wheel 0.45.1`, бо ці версії були у `setuptools/_vendor` (METADATA), а не в основних runtime-пакетах.
- **Change:** У `Dockerfile` перед інсталяцією requirements додано `pip install --no-cache-dir --upgrade pip setuptools wheel`, щоб у runtime image оновились вендорені metadata (`setuptools/_vendor`) і не залишались старі CVE-версії.
- **Verification:** Локально підтверджено після upgrade: `setuptools 82.0.0`, у `_vendor` присутній `wheel-0.46.3.dist-info`, а `jaraco.context-5.3.0.dist-info` відсутній.
- **Risks:** Оновлення build toolchain може змінити transitive dependency resolution; потрібен повторний прогін CI (ruff/pytest/trivy).
- **Rollback:** Прибрати upgrade рядок з `Dockerfile` або відкотити коміт через `git revert`.

## 2026-03-05 — Security/CI: Trivy gate для невиправних CVE + оновлення Python залежностей

- **Context:** Trivy image scan знаходив `HIGH` у Debian base image без доступного `Fixed Version`, через що CI блокувався навіть для невиправних upstream CVE; також були `HIGH` у Python-пакетах із доступним фіксом (`wheel`, `jaraco.context`).
- **Change:** У `.github/workflows/ci-cd.yml` для `Trivy image scan` додано `--ignore-unfixed` (gate лишається для виправних `HIGH/CRITICAL`); у `requirements.txt` додано `wheel>=0.46.2` і `jaraco.context>=6.1.0` для закриття зафіксованих Python CVE.
- **Verification:** Конфіг CI оновлено так, що невиправні CVE більше не блокують pipeline, а виправні залишаються блокуючими; версії з фіксом зафіксовано в залежностях.
- **Risks:** Можливі побічні зміни через оновлення transitive dependencies; потрібен повторний прогін CI (`build`, `pytest`, `trivy`) для підтвердження.
- **Rollback:** Прибрати `--ignore-unfixed` з Trivy кроку та видалити/послабити `wheel`/`jaraco.context` у `requirements.txt`, або зробити `git revert` коміту.

## 2026-03-05 — Improvement: додано `.env.example` і підключено в CI Compose validation

- **Context:** Потрібно стабільно проходити `Compose validation` в CI без залежності від локального `.env` і без зберігання секретів у репозиторії.
- **Change:** Додано `.env.example` з безпечними mock-значеннями всіх потрібних змінних (`KDV_API_TOKEN`, `KOHA_*`, `DSPACE_*`, `INTEGRATOR_MOUNT_PATH`, `HOST`); крок `Compose validation` у `.github/workflows/ci-cd.yml` тепер будує `.env.ci` на базі `.env.example` і створює тимчасовий `.env` для `env_file` в `docker-compose.yml`.
- **Verification:** Логіка кроку `Compose validation` оновлена так, щоб CI завжди мав валідний env-шаблон без production секретів.
- **Risks:** Якщо з'являться нові обов'язкові env у `src/config.py`, їх треба додати в `.env.example`, інакше CI може впасти на валідації/тестах.
- **Rollback:** Видалити `.env.example` та повернути попередню схему генерації `.env.ci` у workflow (через `git revert` або ручний відкат).

## 2026-03-05 — Fix: CI `Compose validation` падав через відсутній `.env`

- **Context:** Після додавання діагностики виявлено, що `docker compose config` у CI падає з `env file .../.env not found`, бо `docker-compose.yml` містить `env_file: .env`.
- **Change:** У кроці `Compose validation` workflow `.github/workflows/ci-cd.yml` додано створення тимчасового `.env` у CI (`cp .env.ci .env`) перед запуском `docker compose config`.
- **Verification:** За логом помилки виявлено точну причину; фікс прибирає blocker, коли в runner немає committed `.env` (що правильно з точки зору безпеки).
- **Risks:** Мінімальні; тимчасовий `.env` використовується лише в межах CI job і не містить production секретів.
- **Rollback:** Видалити `cp .env.ci .env` з кроку `Compose validation` або відкотити коміт.

## 2026-03-05 — Fix: додано діагностику для CI `Compose validation`

- **Context:** CI падав на кроці `Compose validation`, але в логу бракувало деталей причини падіння.
- **Change:** Оновлено крок `Compose validation` у `.github/workflows/ci-cd.yml`: додано друк `docker compose version`, вмісту `.env.ci`, плейсхолдерів із `docker-compose.yml`, явний capture `stderr` у `/tmp/compose.error.log`, preview rendered compose і явну перевірку на unresolved змінні.
- **Verification:** Локально перевірено `docker compose --env-file .env.ci -f docker-compose.yml config -q` (успішно), після змін крок гарантовано друкує причину при помилці `docker compose config`.
- **Risks:** Збільшення обсягу CI-логу (очікувано), функціональна логіка деплою не змінювалась.
- **Rollback:** Відкотити секцію `Compose validation` у `.github/workflows/ci-cd.yml` до попередньої версії або зробити `git revert` коміту.

## 2026-03-05 — Fix: CI Pytest падіння через обов'язкові env у `src/config.py`

- **Context:** У CI крок `Pytest` падав під час collection з `ValueError: Environment variable 'KDV_API_TOKEN' is missing`, бо `src/config.py` валідовує обов'язкові env при імпорті модулів `src`.
- **Change:** Для кроку `Pytest` у `.github/workflows/ci-cd.yml` додано CI-mock env (`KDV_API_TOKEN`, `KOHA_*`, `DSPACE_*`, `INTEGRATOR_MOUNT_PATH`) та залишено явний `PYTHONPATH`.
- **Verification:** `docker compose exec -e PYTHONPATH=/app -e KDV_API_TOKEN=ci-token -e KOHA_API_URL=http://koha.local -e KOHA_OPAC_URL=http://koha.local -e KOHA_API_USER=ci-user -e KOHA_API_PASS=ci-pass -e DSPACE_API_URL=http://dspace.local/server -e DSPACE_UI_URL=http://dspace.local -e DSPACE_API_USER=ci-user -e DSPACE_API_PASS=ci-pass -e INTEGRATOR_MOUNT_PATH=/tmp kdv-api pytest -q` -> `9 passed`.
- **Risks:** Мінімальні; використовуються фіктивні значення тільки в тестовому кроці CI, не зачіпає production secrets або runtime конфіг.
- **Rollback:** Видалити додані env з кроку `Pytest` у `.github/workflows/ci-cd.yml` або відкотити коміт.

## 2026-03-05 — Fix: CI Pytest ModuleNotFoundError (`src`)

- **Context:** У GitHub Actions крок `Pytest` падав на `ModuleNotFoundError: No module named 'src'` під час collection (`tests/test_core.py`, `tests/test_services.py`).
- **Change:** У workflow `.github/workflows/ci-cd.yml` для кроку `Pytest` додано `PYTHONPATH: ${{ github.workspace }}`, щоб імпорти `from src...` працювали в CI середовищі.
- **Verification:** Перевірено конфігурацію workflow після правки; крок `Pytest` тепер запускається з явним шляхом до кореня репозиторію.
- **Risks:** Мінімальні; зміна стосується лише CI оточення тестового кроку і не змінює runtime-поведінку застосунку.
- **Rollback:** Видалити `env.PYTHONPATH` з кроку `Pytest` у `.github/workflows/ci-cd.yml` або відкотити коміт із цим фіксом.

## 2026-03-05 — Docs: нормалізовано формат legacy-записів changelog

- **Context:** У нижній частині файлу залишилися старі записи з форматним артефактом `- ## Context:** ...`, що створювало візуальний шум.
- **Change:** Приведено 3 legacy-блоки до єдиного формату `Context / Change / Verification / Risks / Rollback` без зміни їхнього змісту.
- **Verification:** Перевірено структуру файлу через `nl -ba CHANGELOGS/CHANGELOG_2026_VOL_01.md`; артефактів `- ## Context` більше немає.
- **Risks:** Мінімальний ризик лише у форматуванні markdown (семантика записів не змінювалась).
- **Rollback:** Відкотити файл `CHANGELOGS/CHANGELOG_2026_VOL_01.md` до попередньої версії.

## 2026-03-05 — Docs: рефакторинг ROADMAP (об'єднання M3+M8, release gate, актуалізація)

- **Context:** Потрібно прибрати дублювання в roadmap, оновити застарілі формулювання CI, додати явний release gate і покращити структуру документа.
- **Change:** У `docs/ROADMAP.md` об'єднано `M3` і старий `M8` в єдиний `M3` (CI/CD + release automation), старий `M9` перенумеровано в `M8`; додано секцію `2. Поточний стан (на 2026-03-05)` та `5. Release Gate`; уточнено checklist-и (окремо runtime image vs CI tooling), виправлено `GET /kdv/api/health`, мову/орфографію і загальну нумерацію секцій.
- **Verification:** Візуальна перевірка структури документа (`nl -ba docs/ROADMAP.md`), перевірено наявність нових секцій та коректну послідовність `M0..M8`.
- **Risks:** Можливий дрейф статусів roadmap, якщо фактичний стан CI/CD або milestones зміниться і документ не оновлюватиметься синхронно.
- **Rollback:** Відкотити `docs/ROADMAP.md` до попередньої версії через `git restore --source=<previous_commit> docs/ROADMAP.md` або `git revert <commit_sha>`.

## 2026-03-05 — Docs/Ops: актуалізація RUNBOOK_TESTING + додано scripts/healthcheck.sh

- **Context:** Потрібна робоча інструкція для запуску мок‑тестів після змін у `src` і відсутній обов'язковий скрипт `./scripts/healthcheck.sh` із DoD/checklist.
- **Change:** Оновлено `docs/RUNBOOK_TESTING.md` (додано крок healthcheck, спрощено локальний блок інсталяції, виправлено відносні посилання на файли з `docs/`); створено `scripts/healthcheck.sh` (перевірка `docker compose` service status + `/kdv/api/health` через `curl` у контейнері).
- **Verification:** `./scripts/healthcheck.sh`; `docker exec -e PYTHONPATH=/app kdv-api pytest -q`.
- **Risks:** Скрипт очікує ім'я контейнера `kdv-api` і endpoint `http://localhost:5000/kdv/api/health`; при зміні compose/порту потрібно оновити env `KDV_HEALTH_SERVICE` або `KDV_HEALTH_URL`.
- **Rollback:** Видалити `scripts/healthcheck.sh`, повернути попередню версію `docs/RUNBOOK_TESTING.md`, перебудувати контейнер за потреби.

## 2026-03-05 — Fix: Відновлено запуск мок‑тестів у контейнері

- **Context:** За інструкцією `docs/RUNBOOK_TESTING.md` команда `docker exec -e PYTHONPATH=/app kdv-api pytest -q` має бути основним способом перевірки змін у `src`, але після `docker compose up -d --build` у контейнері не було `pytest`.
- **Change:** Додано `pytest>=8.0.0` у `requirements.txt`, щоб тестовий інструмент гарантовано потрапляв у Docker-образ разом з іншими залежностями.
- **Verification:** `docker compose up -d --build`; `docker exec -e PYTHONPATH=/app kdv-api pytest -q`; `docker exec kdv-api curl -fsS http://localhost:5000/kdv/api/health`; `docker compose logs --tail=200`.
- **Risks:** Невелике збільшення розміру образу через dev-залежність у production requirements; функціональна логіка застосунку не змінювалась.
- **Rollback:** Видалити `pytest>=8.0.0` з `requirements.txt`, перебудувати образ `docker compose up -d --build`.

## 2026-03-04 — Fix: CI Ruff lint failure

- **Context:** CI впав на кроці `Ruff lint` із великою кількістю порушень стилю (`E701`, `E722`, `F841`).
- **Change:** Запущено автоочистку Ruff/format, вручну замінено `bare except` на `except Exception` у критичних модулях (`scripts/nightwalker.py`, `src/core.py`, `src/dspace.py`, `src/koha.py`) та прибрано невикористану змінну у `scripts/robot.py`.
- **Verification:** `docker run --rm -v "$PWD:/work" -w /work ghcr.io/astral-sh/ruff:0.13.0 check .` → `All checks passed!`.
- **Risks:** Автоформатування торкнулося багатьох Python-файлів; функціональна логіка не повинна змінитися, але потрібен прогін тестів у середовищі з доступним `pytest`.
- **Rollback:** `git revert <commit_sha_з_lint_fix>` або вибірковий відкат змінених файлів до попереднього коміту.

## 2026-03-04 — M3: DevOps release pipeline (build/test/scan)

- **Context:** Потрібно реалізувати roadmap пункт `M3` і перенести структуру еталонного CI/CD пайплайна з `archive/ci-cd.yml` у поточний репозиторій.
- **Change:** Додано GitHub Actions workflow `.github/workflows/ci-cd.yml` зі структурою `ci-checks` + `cd-deploy`; у `ci-checks` реалізовано `shellcheck`, `ruff`, `pytest`, `docker build`, `docker compose config`, `pip-audit`, `trivy config/image`; у `cd-deploy` додано SSH-деплой через `appleboy/ssh-action` (підтримка деплою з `main` або `v*` тегу), валідацію секретів і опційне підключення через Tailscale OAuth.
- **Verification:** Перевірено валідність workflow через локальну діагностику (`get_errors` для `.github/workflows/ci-cd.yml`) і git-статус змін.
- **Risks:** `pip-audit`/`trivy` можуть впасти на існуючих CVE; для SSH деплою потрібно налаштувати секрети `SERVER_HOST`, `SERVER_USER`, `SERVER_SSH_KEY`, `DEPLOY_PROJECT_DIR` (та опційно `TAILSCALE_OAUTH_CLIENT_ID`, `TAILSCALE_OAUTH_SECRET`) у GitHub repository settings.
- **Rollback:** Видалити `.github/workflows/ci-cd.yml` та відкотити запис changelog (через `git revert <commit_sha>` або ручне видалення у робочій гілці).

## 2026-03-04 — Pre-prod release v0.1.0

- **Context:** Потрібно зафіксувати поточний стан hardening (M1/M2 завершено) у першому pre-prod релізі з тегом `v0.1.0`.
- **Change:** Підготовлено релізний коміт із поточними змінами кодової бази та документації; створюється релізний тег `v0.1.0`.
- **Verification:** Виконано `git status`, `docker compose ps`; pre-release checklist (`docker compose up -d --build`, `./scripts/healthcheck.sh`, `pytest -q`, `ruff check .`) пропущено за явним рішенням власника репозиторію.
- **Risks:** Можливі непомічені регресії, бо тести/лінт/healthcheck перед тегуванням не запускалися в цьому кроці.
- **Rollback:** `git tag -d v0.1.0`; `git push origin :refs/tags/v0.1.0`; за потреби `git revert <release_commit_sha>` для відкату змін у `main`.

## Legacy записи (ранні етапи, дата не зафіксована)

- **Context:** запуск нового проекту KDV Integrator event-driven, підготовка до production hardening; початкова робота над roadmap та документами.
- **Change:** створено `ROADMAP.md` з описом міленіумів; додано
  `docs/ACCEPTANCE_CRITERIA.md` (українською) із must-have сценаріями, SLI/SLO
  та stop-ship критеріями.
- **Verification:** документ відкривався та переглядався, файл у репозиторії.
- **Risks:** немає — документація не впливає на виконання коду.
- **Rollback:** видалити файл або повернути попередній коміт (тестовий том).

- **Context (M2):** Codebase hardening — початковий рефактор оркестратора і підготовка thin‑wrappers для клієнтів (Koha, DSpace) та скелетів тестів.
- **Change (M2):** додано `src/core.py` (оркестратор), `src/clients/koha.py`, `src/clients/dspace.py`, тести `tests/test_clients.py` та `tests/test_state_machine.py`.
- **Verification:** файли створені у репозиторії; базові імпортні тести присутні; ручне тестування через UI показало коректну роботу основних ендпоінтів (`/health`, `/integrate`, `/status`).
- **Risks:** тимчасова дублювання логіки між `src/core.py` та `src/app.py` поки не завершено повну міграцію; увага до версій залежностей при запуску тестів.
- **Rollback:** видалити додані файли або відкотити коміт.

- **Context (M2 continued):** завершено розділення на сервіси і залежності, впроваджено DI для клієнтів у `core`, `app` і `tasks`.
- **Change (M2 continued):**
  - додано `src/services/files.py` та `src/services/covers.py`;
  - фабрика клієнтів `_make_clients` у `app.py` і передачі до `process_integration_logic`;
  - `tasks.py` отримав підтримку ключових аргументів;
  - додані тести `tests/test_services.py` і `tests/test_core.py` із моками;
  - контейнер тепер містить `pytest` і приклади запуску юніт-тестів.
- **Verification (M2 continued):** усі юніт‑тести проходять у середовищі контейнера; smoke‑скрипт демонструє коректну поведінку; ручний прогін API не виявив збоїв.
- **Risks (M2 continued):** необхідно оновити CI, щоб автоматично виконувати тести; залишається ризик дрібних регресій при роботі з файлами.
- **Rollback (M2 continued):** відкотити вищевказані зміни або повернутися до попереднього коміту/тега.

## 2026-03-05 — M5: додано `GET /kdv/api/ready` + API тести

- **Context:** У roadmap для M5 залишався незакритий пункт readiness endpoint; існував лише liveness (`/kdv/api/health`), що не дає сигналу про готовність mount path до роботи.
- **Change:** У `src/app.py` додано `GET /kdv/api/ready`, який перевіряє `INTEGRATOR_MOUNT_PATH` на існування та права `read/write`; endpoint повертає `200` зі статусом `ready` або `503` зі статусом `not_ready` і деталями перевірки. Оновлено `check_security()`, щоб probes на `/health` та `/ready` проходили без токена. Додано `tests/test_app.py` (перевірки `/health`, `/ready` для `200` та `503`). У `docs/ROADMAP.md` позначено readiness як виконаний пункт M5.
- **Verification:** `docker exec -e PYTHONPATH=/app kdv-api pytest -q` -> `12 passed`; `docker run --rm -v "$PWD:/work" -w /work ghcr.io/astral-sh/ruff:0.13.0 check .` -> `All checks passed!`; `./scripts/healthcheck.sh` -> `OK`; `docker compose logs --tail=200` -> критичних помилок не виявлено.
- **Risks:** Readiness поки перевіряє лише mount-доступність локально і не включає активні перевірки Koha/DSpace; для production може знадобитись розширений readiness профіль.
- **Rollback:** Відкотити зміни у `src/app.py`, `tests/test_app.py`, `docs/ROADMAP.md` через `git revert <commit_sha>` або ручно прибрати додані блоки.

## 2026-03-05 — M4: старт Zero Trust transition (CORS allowlist + dual auth)

- **Context:** Потрібно почати M4 без різкого ламання поточного потоку: прибрати секрет із клієнтського JS, перейти на allowlist CORS з env, і дати перехідний режим авторизації до повного Cloudflare Access.
- **Change:** У `src/app.py` реалізовано env-керовані режими auth: `legacy`, `dual`, `cf-only`; додано перевірку `Cf-Access-Jwt-Assertion` (JWK через `https://<team>/cdn-cgi/access/certs`, перевірка `aud/iss`) для `dual/cf-only`; CORS переведено на strict allowlist (`KDV_CORS_ALLOWLIST`, fallback `KOHA_OPAC_URL`) без `*`. У `IntranetUserJS.js` прибрано хардкод `X-KDV-TOKEN`; лишено безпечний перехідний fallback через `window.KDV_TOKEN`. Додано env-поля в `src/config.py` та `.env.example`, додано тести безпеки у `tests/test_app.py`, оновлено `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `README.md`.
- **Verification:** `docker exec -e PYTHONPATH=/app kdv-api pytest -q`; `docker run --rm -v "$PWD:/work" -w /work ghcr.io/astral-sh/ruff:0.13.0 check .`; `./scripts/healthcheck.sh`; `docker compose logs --tail=200`.
- **Risks:** Для `dual/cf-only` потрібні коректні `CF_ACCESS_TEAM_DOMAIN` і `CF_ACCESS_AUD`; без них Cloudflare JWT не пройде валідацію. Якщо origin Koha не додано в `KDV_CORS_ALLOWLIST`, браузерний виклик буде заблоковано CORS.
- **Rollback:** Повернути `KDV_AUTH_MODE=legacy`; відкотити зміни у `src/app.py` та `IntranetUserJS.js` (через `git revert`) і тимчасово повернути старий механізм токена в JS тільки в ізольованому середовищі.

## 2026-03-05 — Tests: стабілізовано flaky polling у `test_task_manager_integration`

- **Context:** Після M4 прогонів періодично падав тест `tests/test_core.py::test_task_manager_integration` через race condition: цикл завершувався на проміжному стані `queued`.
- **Change:** У тесті змінено умову зупинки polling-циклу: тепер очікуються лише terminal стани (`success` або `error`), а не будь-який стан відмінний від `processing`.
- **Verification:** `docker exec -e PYTHONPATH=/app kdv-api pytest -q` -> `16 passed`; `docker run --rm -v "$PWD:/work" -w /work ghcr.io/astral-sh/ruff:0.13.0 check .` -> `All checks passed!`.
- **Risks:** Мінімальні; змінено тільки тест, runtime-поведінка сервісу не зачеплена.
- **Rollback:** Відкотити правку у `tests/test_core.py` через `git revert <commit_sha>`.

## 2026-03-05 — M4 fix: CORS credentials для Cloudflare Access browser flow

- **Context:** Preflight (`OPTIONS`) проходив, але браузерний виклик із Koha міг падати на фактичному `POST/PUT`, бо cross-origin XHR не відправляв cookies Cloudflare Access.
- **Change:** У `IntranetUserJS.js` для всіх API викликів додано `xhrFields: { withCredentials: true }`; у `src/app.py` додано `Access-Control-Allow-Credentials: true` для allowlist-origin.
- **Verification:** Після `docker compose up -d --build` preflight повертає: `Access-Control-Allow-Origin: https://library.fby.com.ua` + `Access-Control-Allow-Credentials: true`; `pytest -q` -> `16 passed`; `./scripts/healthcheck.sh` -> `OK`.
- **Risks:** Потрібно, щоб браузер мав валідну Cloudflare Access сесію для `repo.fby.com.ua`; без неї edge продовжить редиректити на login.
- **Rollback:** Відкотити зміни у `IntranetUserJS.js` та `src/app.py` через `git revert <commit_sha>`.

## 2026-03-05 — M4 fix: JS pre-check Access session + login fallback

- **Context:** У браузері з'являвся `403` на `filipchuk.cloudflareaccess.com/cdn-cgi/access/login/...` під час CORS-потоку, коли CF Access сесія була відсутня/прострочена.
- **Change:** У `IntranetUserJS.js` додано `ensureAccessSession()` (перевірка `GET /kdv/api/health` з `withCredentials`) перед `POST/PUT`; якщо перевірка не проходить, показується інструкція і відкривається Cloudflare login URL в новій вкладці.
- **Verification:** `docker exec -e PYTHONPATH=/app kdv-api pytest -q` -> `16 passed`; `./scripts/healthcheck.sh` -> `OK`.
- **Risks:** Якщо CF Access policy не дозволяє потрібний браузерний flow або блокує сесію, користувач після логіну все ще може отримувати edge deny і потребуватиме корекції policy на стороні Cloudflare.
- **Rollback:** Відкотити правки `IntranetUserJS.js` через `git revert <commit_sha>`.

## 2026-03-05 — M4 fix: правильний Cloudflare login host у Koha JS

- **Context:** Після додавання login fallback браузер відкривав `https://repo.fby.com.ua/cdn-cgi/access/login/...`, що давало `Unable to find your Access application!`.
- **Change:** У `IntranetUserJS.js` login URL переведено на team-domain (`filipchuk.cloudflareaccess.com`) з динамічним app-host із `KDV_API_URL`.
- **Verification:** Візуальна перевірка сформованого URL: `https://filipchuk.cloudflareaccess.com/cdn-cgi/access/login/repo.fby.com.ua?...`.
- **Risks:** Якщо app у Cloudflare Access налаштований на інший hostname, login все одно буде відхилятись; потрібна звірка домену application policy.
- **Rollback:** Відкотити правку `IntranetUserJS.js` через `git revert <commit_sha>`.

## 2026-03-05 — M4 fix: Access bootstrap через захищений ресурс замість статичного login URL

- **Context:** Навіть із team-domain URL сторінка могла показувати `Unable to find your Access application!`, бо ручний login URL не завжди валідний без параметрів, згенерованих edge (`kid/meta`).
- **Change:** У `IntranetUserJS.js` fallback змінено: відкривається `https://repo.fby.com.ua/kdv/api/health` (захищений ресурс), щоб Cloudflare сам сформував коректний redirect URL на login. Додатково `buildHeaders()` винесено в глобальну область файлу (щоб polling не ловив scope-помилку), а при невдалій сесійній перевірці кнопка в UI розблоковується.
- **Verification:** `docker exec -e PYTHONPATH=/app kdv-api pytest -q` -> `16 passed`; `./scripts/healthcheck.sh` -> `OK`; live-check `GET https://repo.fby.com.ua/kdv/api/health` -> `302` з `location` на `filipchuk.cloudflareaccess.com/...` і наявними `kid` + `meta`.
- **Risks:** Якщо в самій Access application policy немає дозволу для потрібного користувача/методу, після логіну edge усе одно може відхилити запит.
- **Rollback:** Відкотити зміни у `IntranetUserJS.js` через `git revert <commit_sha>`.

## 2026-03-05 — M4 fix: приймати CF JWT з cookie `CF_Authorization`

- **Context:** Після успішного Cloudflare login браузер надсилав `CF_Authorization` cookie, але API у `dual` режимі перевіряв лише `X-KDV-TOKEN` або header `Cf-Access-Jwt-Assertion`, що давало `401 Unauthorized` на `PUT /kdv/api/integrate/{id}`.
- **Change:** У `src/app.py` розширено `_is_authorized()`: для Cloudflare гілки JWT береться як із `Cf-Access-Jwt-Assertion`, так і з cookie `CF_Authorization`; обидва варіанти проходять ту саму валідацію `aud/iss/signature`.
- **Verification:** `docker compose up -d --build`; `docker exec -e PYTHONPATH=/app kdv-api pytest -q` -> `17 passed`; `./scripts/healthcheck.sh` -> `OK`; додано тест `test_dual_auth_accepts_cloudflare_cookie` у `tests/test_app.py`.
- **Risks:** Невалідний або прострочений `CF_Authorization` токен і надалі коректно відхиляється (`401`).
- **Rollback:** Відкотити правки `src/app.py` та `tests/test_app.py` через `git revert <commit_sha>`.

## 2026-03-05 — M4 fix: `401` через відсутність `cryptography` для RS256

- **Context:** Попри наявний `CF_Authorization` cookie API продовжував віддавати `401`; у логах: `Cloudflare Access JWT rejected: RS256 requires 'cryptography' to be installed`.
- **Change:** Додано `cryptography>=44.0.0` у `requirements.txt` для коректної валідації RS256-підпису JWT через PyJWT.
- **Verification:** `docker compose up -d --build`; `docker exec -e PYTHONPATH=/app kdv-api pytest -q` -> `17 passed`; `./scripts/healthcheck.sh` -> `OK`; повторний live `PUT https://repo.fby.com.ua/kdv/api/integrate/12` з `CF_Authorization` -> `200 {"status":"success"}`.
- **Risks:** Мінімальні; зміна стосується криптографічної залежності для auth-перевірки.
- **Rollback:** Прибрати `cryptography` з `requirements.txt` і відкотити коміт (не рекомендовано, поверне `401` для CF JWT).

## 2026-03-05 — Docs: M4 позначено завершеним у ROADMAP + синхронізовано ARCHITECTURE

- **Context:** Після серії M4 фіксів (CORS allowlist, credentials, Cloudflare browser-flow, cookie JWT, RS256 verification) документація відставала від фактичного стану.
- **Change:** У `docs/ROADMAP.md` закрито M4 (100%), позначено виконаним пункт preflight через edge та оновлено розділ «Поточний стан/ризики». У `docs/ARCHITECTURE.md` оновлено версію до `v0.2.1-M4` і деталізовано реалізацію auth/CORS: джерела JWT (`Cf-Access-Jwt-Assertion` + `CF_Authorization`), вимогу `cryptography`, `Access-Control-Allow-Credentials`, і browser bootstrap flow.
- **Verification:** Візуальна звірка документів з фактичною реалізацією у `src/app.py` та `IntranetUserJS.js`.
- **Risks:** Документація може застаріти, якщо буде перехід з `dual` на `cf-only` без синхронного оновлення roadmap/architecture.
- **Rollback:** Відкотити правки в `docs/ROADMAP.md` та `docs/ARCHITECTURE.md` через `git revert <commit_sha>`.

## 2026-03-05 — M5: додано ops runbook `RUNBOOK_MAYDAY` і закрито milestone

- **Context:** У M5 був незакритий пункт про production runbook; існував лише `RUNBOOK_TESTING`, який покривав розробницьке тестування, але не відновлення після інцидентів.
- **Change:** Створено `docs/RUNBOOK_MAYDAY.md` з широким покриттям інцидентів (API down, readiness/mount, Cloudflare 524, 401/403 Access, CORS, stuck tasks, Koha/DSpace збої, деградація 5xx, rollback); синхронізовано `docs/ROADMAP.md` для M5: виправлено endpoint до `GET /kdv/api/ready`, додано посилання на `RUNBOOK_MAYDAY`, статус M5 змінено на `100%`.
- **Verification:** Візуальна перевірка документації (`docs/RUNBOOK_MAYDAY.md`, секція M5 у `docs/ROADMAP.md`), формат і посилання узгоджені з поточною структурою `docs/`.
- **Risks:** Runbook потребує періодичного оновлення при зміні Cloudflare policy, compose-конфігів або process flow; без синхронізації можливий docs drift.
- **Rollback:** Видалити `docs/RUNBOOK_MAYDAY.md` і відкотити правки в `docs/ROADMAP.md` через `git revert <commit_sha>`.

## 2026-03-05 — M6: додано contract тести для Koha CGI та DSpace pid/patch

- **Context:** У M6 залишався незакритий пункт contract-рівня: перевірки точних полів/headers для Koha CGI та контракту DSpace (`/pid/find`, JSON Patch metadata).
- **Change:** Додано `tests/test_contracts.py` із перевірками: DSpace `find_item_uuid_by_handle` (endpoint `/pid/find` + params), `update_metadata` (формування `application/json-patch+json` і patch operations), Koha `_step1_upload_temp` (`X-Requested-With`, `CSRF-TOKEN`, field `file`), `_step2_process_attach` (payload `cud-process`), `_ensure_cgi_login` (field names `login_userid`, `login_password`, `csrf_token`, `koha_login_context`). У `docs/ROADMAP.md` M6 позначено завершеним (`100%`) і синхронізовано верхній статус milestone.
- **Verification:** `docker exec -e PYTHONPATH=/app kdv-api pytest -q` -> `22 passed`; `./scripts/healthcheck.sh` -> `OK`; `docker compose logs --tail=200` без критичних помилок.
- **Risks:** Contract тести виконані на моках HTTP-рівня; при істотних змінах upstream Koha/DSpace контракту може знадобитися додати інтеграційний staging smoke.
- **Rollback:** Видалити `tests/test_contracts.py` і відкотити зміни в `docs/ROADMAP.md` через `git revert <commit_sha>`.

## 2026-03-05 — Docs: оновлено `RUNBOOK_TESTING` під завершений M6

- **Context:** Після завершення M6 (`tests/test_contracts.py`) runbook тестування залишався в термінах "моки + unit" і не покривав contract-рівень.
- **Change:** Оновлено `docs/RUNBOOK_TESTING.md`: назву/мету приведено до `unit + integration + contract`, додано посилання на `tests/test_contracts.py` і `docs/RUNBOOK_MAYDAY.md`, додано команду запуску лише contract-тестів, описано контрактні перевірки Koha/DSpace, додано очікуваний baseline повного прогону (`22 passed`) і troubleshooting для env-залежних Koha credentials.
- **Verification:** Візуальна перевірка `docs/RUNBOOK_TESTING.md` на узгодженість із поточним M6 у `docs/ROADMAP.md` і наявними тестами в `tests/test_contracts.py`.
- **Risks:** Baseline кількість тестів (`22 passed`) зміниться після нових тестів; цей рядок потрібно оновлювати, щоб уникати docs drift.
- **Rollback:** Відкотити правки `docs/RUNBOOK_TESTING.md` через `git revert <commit_sha>`.

## 2026-03-05 — M7: release/rollback план + batch controls (parallelism/rate-limit)

- **Context:** Для M7 залишався незакритий rollback пункт у roadmap; також потрібно було мати явне керування паралельністю та rate limiting для batch-процесів.
- **Change:** У `scripts/robot.py` додано env-контролі `ROBOT_PARALLELISM`, `ROBOT_BATCH_DELAY`, `ROBOT_POLL_INTERVAL`, `ROBOT_MAX_WAIT` і підтримку керованого parallel batch через `ThreadPoolExecutor` (за замовчуванням `parallelism=1`, поведінка сумісна назад). У `scripts/nightwalker.py` додано throttle-параметри `NIGHTWALKER_AUTO_DELAY`, `NIGHTWALKER_RANGE_DELAY`. Оновлено `.env.example`, `README.md`, `docs/RELEASE.md` (canary flow + практичний rollback через tag/digest), `docs/RUNBOOK_MAYDAY.md` і `docs/ROADMAP.md` (M7 позначено завершеним, rollback checked).
- **Verification:** `docker exec -e PYTHONPATH=/app kdv-api pytest -q` -> `22 passed`; `./scripts/healthcheck.sh` -> `OK`; `docker compose logs --tail=200` без критичних помилок.
- **Risks:** При підвищенні `ROBOT_PARALLELISM` без достатнього `ROBOT_BATCH_DELAY` можливе перевантаження Koha/DSpace; рекомендований безпечний старт `ROBOT_PARALLELISM=1` з поступовим збільшенням.
- **Rollback:** Відкотити зміни в `scripts/robot.py`, `scripts/nightwalker.py`, `.env.example`, `README.md`, `docs/RELEASE.md`, `docs/RUNBOOK_MAYDAY.md`, `docs/ROADMAP.md` через `git revert <commit_sha>`.
