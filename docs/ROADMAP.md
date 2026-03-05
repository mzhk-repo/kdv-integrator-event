### 1. Мета (Production Goal)

Підготувати KDV Integrator (event-driven, async) до стабільної експлуатації в production з мінімально необхідними DevSecOps практиками, прозорими критеріями готовності та керованим релізом.

### 2. Поточний стан (на 2026-03-05)

- ✅ `M0`, `M1`, `M2` завершені (критерії приймання, коректність event-driven flow, DI/SOLID hardening).
- ⏳ У фокусі `M3`: довести CI/CD до стабільного автоматичного gate (build/lint/tests/scan/release).
- ⏳ `M4`, `M5`, `M6`, `M7`, `M8` у прогресі.
- ⚠️ Основні ризики: неповне security hardening (Cloudflare Access/CORS), відсутність `ready` endpoint, неповні contract тести Koha/DSpace.

### 3. Принципи (коротко)

#### DevOps

- **Release discipline**: відтворювані збірки, чітка схема версій, швидкий rollback.
- **Observability-first**: структуровані логи та базові метрики до релізу.
- **Runbooks**: мінімальні інструкції “що робити якщо…”.

#### Мінімальний DevSecOps

- **Secrets out of client**: жодних токенів у JS.
- **Zero Trust at the edge**: Cloudflare Access як периметр.
- **Least privilege**: мінімальні права для інтеграційних обліковок.
- **Supply-chain minimum**: сканування залежностей + образу, базові лінтери.

#### ООП/SOLID (практично)

- **SRP**: оркестрація процесу окремо від клієнтів Koha/DSpace, файлової гігієни, mapping.
- **DIP/DI**: клієнти за інтерфейсами або thin-wrapper’ами для тестів.
- **Fail fast + safe rollback**: критичні помилки мають переводити запис/файл у керований стан (Error folder, статуси Koha).

---

### 4. Milestones (етапи) + Definition of Done

Нижче — етапи у форматі, який зручно переносити в GitHub Issues/Projects.

#### ✅ M0 — Acceptance Criteria (узгодження “що таке прод-готовність”)

На цьому етапі потрібно конкретно сформулювати, що саме ми вважаємо «готовим до проду» — щоб команда й аудит могли послатися на документ.

**DoD:**

- Сформований і зафіксований список основних (must‑have) сценаріїв, які обов’язково працюють перед релізом:
    - `POST /integrate/{id} -> 202 + task_id` (без помилок, правильний код відповіді).
    - `GET /status/{task_id}` повертає коректний стан і стійко працює.
    - Механізм fork/join: DSpace імпорт повинен бути critical-path, покриття обкладинки — best‑effort.
    - Файлова гігієна: кожне оброблене/необроблене ім’я файлу перейменовується (rename‑first), існують папки `Processed`/`Error`.
    - Операції batch-робота й нічного «nightwalker» на завантажених файлах.
- Визначені та записані критерії «stop‑ship» — категорії дефектів, які неодмінно блокують випуск.

**Що треба зробити (checklist):**

1. Описати у документації кожен з must‑have сценаріїв, бажано із прикладами (curl-поклики або кейси).
2. Встановити мінімальні SLI/SLO:
   - доступність API (наприклад, ≥99.5 % за хвилину);
   - p95 latency для `POST /integrate` (напр. <500 мс);
   - частка помилок 4xx/5xx (напр. <1 %).
3. Перелічити та зафіксувати «stop‑ship» критерії — наприклад, невірний task_id, втрата даних, корупція обкладинок, критичні помилки з Koha/DSpace.
4. Перевірити, що ці критерії зрозумілі для QA/рев’ю й можуть використовуватися під час приймання релізу.

> **Примітка:** цей етап не вимагає коду; достатньо документу та, за потреби, GitHub issues/PRs, які прив’язані до кожного пункту.

#### ✅ M1 — Event-driven correctness (state machine, idempotency, restart behavior)

**DoD:**

- ✅ Є явна модель станів (task + book integration), описана в README/docs.
- ✅ Ідемпотентність: повторні запити не створюють хаос і не породжують дублікати.
- ✅ Після рестарту сервіс поводиться прогнозовано (task_id може загубитись, але система відновлювана через Koha/DSpace).

**Checklist:**

- [x]  Описати state machine (tasks: queued/processing/success/error; book: processing/imported/linked_existing/error/warning).
- [x]  Описати правила повторного `POST /integrate/{id}` (already processed / already processing).
- [x]  Додати correlation id (task_id) у всі логи.
- [x]  Зафіксувати behavior після рестарту (in-memory task store): як реагують Koha JS та robot/nightwalker.

**Статус (на 2026-03-04):** ✅ **ЗАВЕРШЕНО**

#### ✅ M2 — Codebase hardening (SOLID рефактор без “перебудови світу”)

**DoD:**

- ✅ Оркестратор інтеграції відділений від клієнтів (в `src/core.py`).
- ✅ Модулі тестовані із підміною клієнтів (DI via kwargs у `process_integration_logic` та `run_dspace_workflow`).
- ✅ Конфіги винесені в env та конфіг‑модуль без секретів у коді.

**Checklist (орієнтовна структура модулів):**

- [x]  `src/core.py`: orchestration (workflow, fork/join, статуси).
- [x]  `src/clients/koha.py` (wrapper): Koha з DI.
- [x]  `src/clients/dspace.py` (wrapper): DSpace з DI.
- [x]  `src/services/covers.py`: covers pipeline (best-effort).
- [x]  `src/services/files.py`: rename-first, Processed/Error, versioning.
- [x]  `src/mapping.py`: metadata extraction/mapping.
- [x]  Thin interfaces / dependency injection для клієнтів в `app.py` (_make_clients).
- [x]  `src/tasks.py`: підтримка kwargs за допомогою _wrapper.
- [x]  Unit‑тести з моками в `tests/test_core.py` та `tests/test_services.py`.
- [x]  `docs/RUNBOOK_TESTING.md`: інструкція щодо тестування змін.

**Статус (на 2026-03-04):** ✅ **ЗАВЕРШЕНО**

Завершено розділення компонентів, впроваджено DI і написано покрокові тести з моками. Все сумісно з Koha UI через контейнер (поточна версія працює без keyword-аргумент помилок).

#### ✅ M3 — DevOps release pipeline (CI/CD + build/test/scan + release automation)

**DoD:**

- Є CI для `push`/`pull_request`, який: збирає, лінтить, тестить, сканує залежності та Docker image.
- Є release automation для тегів `v*` (публікація артефакту/релізні кроки) і зрозумілі правила `main -> staging`, `v* -> prod`.
- Є відтворюваний артефакт і release notes.

**Checklist:**

- [x]  Активувати workflow `.github/workflows/ci-cd.yml` як обов’язковий status check для PR.
- [x]  Build етап: `docker build` або `docker compose build --pull`.
- [x]  Лінтер: `ruff check .` у CI job (окремо від runtime-контейнера сервісу).
- [x]  Тести: `docker exec -e PYTHONPATH=/app kdv-api pytest -q` (мінімум unit).
- [x]  Dependency scan: `pip-audit`.
- [x]  Image scan: `trivy`.
- [x]  Release rules: `main -> staging`, `v* tag -> prod`.
- [x]  Secrets для CI/CD (GitHub Secrets) налаштовані та провалідовані.

**Статус (на 2026-03-05): **ВИКОНАНО**

- ✅ pytest встановлено і працює в контейнері (див. [RUNBOOK_TESTING.md](RUNBOOK_TESTING.md)).
- ✅ CI/CD workflow працює на `push`/`pull_request` і проходить mandatory gates (`ruff`, `pytest`, `pip-audit`, `trivy`).
- ✅ Налаштовано release path: `main -> staging`, `v* -> production` (через `cd-deploy`).


#### M4 — Security: Zero Trust + CORS (must-have для прод)

**DoD:**

- У клієнтському Koha JS **немає секретів**.
- Доступ до API контролюється Cloudflare Access.
- CORS allowlist обмежений доменом Koha.

**Checklist:**

- [ ]  Прибрати `X-KDV-TOKEN` з Koha `IntranetUserJS`.
- [ ]  Перейти на Cloudflare Access headers (`Cf-Access-Jwt-Assertion`) або інший trusted-header механізм.
- [ ]  Залишити `X-KDV-TOKEN` тільки для server-to-server (robot/nightwalker), якщо потрібно.
- [ ]  Налаштувати Strict CORS: `Access-Control-Allow-Origin` = домен Koha (не `*`).
- [ ]  Переконатися, що `OPTIONS` (preflight) проходить через edge (CF Access) без поломок.

#### M5 — Observability + Ops readiness (мінімум)

**DoD:**

- Є liveness/readiness endpoints.
- Є структуровані логи й мінімальні метрики.
- Є runbooks.

**Checklist:**

- [x]  `GET /kdv/api/health` (liveness). — реалізовано в `src/app.py`.
- [ ]  `GET /ready` (readiness): mount готовий, конфіги валідні.
- [x]  Структуровані логи з полями: `task_id`, `biblionumber`. — у `core.py` та `tasks.py`.
- [x]  Runbooks: [docs/RUNBOOK_TESTING.md](RUNBOOK_TESTING.md) (тестування змін). / Для ops потрібні ще "Cloudflare 524", "drive not mounted" тощо.

**Статус (на 2026-03-04):** ⏳ **В ПРОГРЕСІ (60%)**

- ✅ Health endpoint + структуровані логи.
- ✅ Runbook щодо тестування (для розробників).
- ⏳ потрібен ops runbook для production.

#### M6 — Test strategy (unit + integration + contract)

**DoD:**

- Є unit тести для mapping/files/state.
- Є інтеграційний прогін у docker compose.
- Є контрактні перевірки для Koha CGI та DSpace PID/patch.

**Checklist:**

- [x]  Unit: metadata mapping (`test_parse_marc_details`), file versioning (`test_files_*`), state machine (`test_task_manager_*`).
- [x]  Integration: task_manager → process_integration_logic → success/error (див. `test_task_manager_integration`).
- [ ]  Contract: Koha CGI (headers/field names exact match), DSpace pid resolution + JSON Patch.

**Статус (на 2026-03-04):** ⏳ **В ПРОГРЕСІ (70%)**

- ✅ Unit тести з моками (`tests/test_core.py`, `tests/test_services.py`).
- ✅ Integration через task_manager.
- ⏳ Contract тести для CGI та DSpace полів.

Дивіться [docs/RUNBOOK_TESTING.md](RUNBOOK_TESTING.md) для деталей про запуск та написання нових тестів.

#### M7 — Release plan (staging -> prod)

**DoD:**

- Є canary rollout.
- Є rollback plan.
- Є контроль паралельності й rate limiting для batch.

**Checklist:**

- [ ]  Staging прогін на контрольній вибірці записів.
- [ ]  Canary доступ (1–2 користувачі).
- [ ]  Вікно спостереження 24–48 год.
- [ ]  Rollback: повернення до попереднього image digest/tag.

#### M8 — Post‑prod analytics & Scale‑out

**DoD:**

- Збираються базові метрики доступності, latency, обсягів обробки.
- Система може запускати декілька worker-ів/потоків, є rate‑limit конфігурація.

**Checklist:**

- [ ]  Метрики p95, success rate, черга задач (prometheus? simple log parsing).
- [ ]  Документація “як додати новий worker/збільшити threads”.
- [ ]  Rate-limit параметри для batch-роботи (щоб Koha/DSpace не перевантажувалися).

### 5. Release Gate (must pass перед тегом `v*`)

Мінімальний gate перед production release:

- [ ]  `docker compose up -d --build`
- [ ]  `./scripts/healthcheck.sh`
- [ ]  `docker compose logs --tail=200` (без критичних помилок сервісу)
- [ ]  `docker exec -e PYTHONPATH=/app kdv-api pytest -q`
- [ ]  `ruff check .` (якщо лінтер увімкнено в репозиторії)
- [ ]  Оновлено активний том `CHANGELOGS/` за шаблоном `Context/Change/Verification/Risks/Rollback`
- [ ]  Якщо зміни торкаються security/ops/network: оновлено `ARCHITECTURE.md` та відповідні runbook-и

---

> **Roadmap is a living document:**
> оновлюйте його перед кожним релізом, додавайте рядки про завершені пункти та нові ідеї.

---

### 6. Як цим користуватись з Copilot/Codex

- Кожен milestone = окремий GitHub Epic/Project column.
- Кожен checkbox = Issue.
- Для задач, що змінюють поведінку API/безпеку: в Issue додавати секцію **DoD** (копіювати з цього файлу).
