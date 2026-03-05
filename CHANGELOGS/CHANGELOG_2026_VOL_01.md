# CHANGELOG 2026 VOL 01

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

- ## Context:** запуск нового проекту KDV Integrator event-driven, підготовка до
  production hardening; початкова робота над roadmap та документами.
- **Change:** створено `ROADMAP.md` з описом міленіумів; додано
  `docs/ACCEPTANCE_CRITERIA.md` (українською) із must-have сценаріями, SLI/SLO
  та stop-ship критеріями.
- **Verification:** документ відкривався та переглядався, файл у репозиторії.
- **Risks:** немає — документація не впливає на виконання коду.
- **Rollback:** видалити файл або повернути попередній коміт (тестовий том).

- ## Context (M2):** Codebase hardening — початковий рефактор оркестратора і підготовка thin‑wrappers для клієнтів (Koha, DSpace) та скелетів тестів.
- **Change (M2):** додано `src/core.py` (оркестратор), `src/clients/koha.py`, `src/clients/dspace.py`, тести `tests/test_clients.py` та `tests/test_state_machine.py`.
- **Verification:** файли створені у репозиторії; базові імпортні тести присутні; ручне тестування через UI показало коректну роботу основних ендпоінтів (`/health`, `/integrate`, `/status`).
- **Risks:** тимчасова дублювання логіки між `src/core.py` та `src/app.py` поки не завершено повну міграцію; увага до версій залежностей при запуску тестів.
- **Rollback:** видалити додані файли або відкотити коміт.

- ## Context (M2 continued):** завершено розділення на сервіси і залежності, впроваджено DI для клієнтів у `core`, `app` і `tasks`.
- **Change (M2 continued):**
  - додано `src/services/files.py` та `src/services/covers.py`;
  - фабрика клієнтів `_make_clients` у `app.py` і передачі до `process_integration_logic`;
  - `tasks.py` отримав підтримку ключових аргументів;
  - додані тести `tests/test_services.py` і `tests/test_core.py` із моками;
  - контейнер тепер містить `pytest` і приклади запуску юніт-тестів.
- **Verification (M2 continued):** усі юніт‑тести проходять у середовищі контейнера; smoke‑скрипт демонструє коректну поведінку; ручний прогін API не виявив збоїв.
- **Risks (M2 continued):** необхідно оновити CI, щоб автоматично виконувати тести; залишається ризик дрібних регресій при роботі з файлами.
- **Rollback (M2 continued):** відкотити вищевказані зміни або повернутися до попереднього коміту/тега.
