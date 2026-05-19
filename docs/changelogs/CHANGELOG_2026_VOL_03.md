# CHANGELOG 2026 VOL 03

## 2026-05-16 — Core integration tests для PDF optimizer cleanup (Фаза 7.2)

- **Context:** Після unit-тестів `PDFOptimizerService` потрібно покрити orchestration layer у `core.py`, особливо cleanup shared-volume tmp-файлів у `finally` і сумісність fallback оптимізації з основною архівацією.
- **Change:** У `tests/test_core.py` додано 4 focused тести Фази 7.2: cleanup при exception в optimizer, cleanup при exception у `dspace.upload_to_item()` з перевіркою `os.remove` для input/output tmp-файлів, fallback `OptimizeResult(success=False, fallback_reason="timeout")` без зриву архівації, а також фіксація поточного hard-limit контракту `LIMIT_ERROR` — файл >250MB зупиняється до запуску optimization path.
- **Verification:** `python3 -m py_compile tests/test_core.py`; локальний `pytest` на хості не запустився через відсутній `pymarc`; контейнерна перевірка `docker run --rm --env-file .env.example --entrypoint python -v /opt/kdv-integrator/kdv-integrator-event:/work -w /work kdv-integrator-event:5f50c2647018 -m pytest tests/test_core.py -q` -> `12 passed`.
- **Risks:** Hard-limit тест документує поточну поведінку, а не змінює порядок limit/optimization; якщо після PoC буде рішення оптимізувати до hard-limit rejection, це має бути окрема задача з оновленням контракту.
- **Rollback:** Прибрати нові тести з `tests/test_core.py`, відкотити checkbox-и roadmap і цей changelog-запис.

## 2026-05-16 — App та Robot tests для skip optimization (Фаза 7.3)

- **Context:** Потрібно зафіксувати backward-compatible поведінку `/integrate` без JSON body і CLI-прапор `--skip-optimization` для batch robot. За рішенням у цій ітерації robot-тести лишаються у канонічному `tests/test_robot.py`; окремий `tests/test_scripts.py` не створюється.
- **Change:** У `docs/pdf-optimizer/roadmap-optimizer.md` уточнено файл robot-тестів на `tests/test_robot.py` і закрито критерії 7.3. Наявні тести `test_integrate_without_payload_defaults_to_optimization` у `tests/test_app.py` та `test_robot_skip_optimization_flag_sets_payload` у `tests/test_robot.py` підтверджують потрібний контракт.
- **Verification:** `python3 -m py_compile tests/test_app.py tests/test_robot.py`; локальний `pytest` на хості не запустився через відсутній `flask`; контейнерна перевірка `docker run --rm --env-file .env.example --entrypoint python -v /opt/kdv-integrator/kdv-integrator-event:/work -w /work kdv-integrator-event:5f50c2647018 -m pytest tests/test_app.py tests/test_robot.py -q` -> `15 passed`.
- **Risks:** Roadmap тепер відображає фактичну структуру тестів: robot CLI покривається через `tests/test_robot.py`; якщо в майбутньому зʼявляться інші scripts-тести, їх можна винести окремо без зміни поточного контракту.
- **Rollback:** Відкотити checkbox-и/уточнення roadmap і цей changelog-запис.

## 2026-05-17 — Runbook для PDF optimizer

- **Context:** Після завершення основних фаз впровадження `kdv-optimizer` потрібен окремий операційний runbook для використання, конфігурації, перевірки, troubleshooting і rollback модуля оптимізації PDF.
- **Change:** Додано `docs/RUNBOOK_PDF_OPTIMIZER.md` на основі `docs/pdf-optimizer/PRD-optimizer.md` і `docs/pdf-optimizer/roadmap-optimizer.md`. Документ описує архітектуру `kdv-api`/`kdv-optimizer`, умови запуску оптимізації, ENV, Docker Compose/Swarm контракт, Koha/API/Robot usage, health/readiness checks, telemetry, deploy/redeploy, rollback, cleanup temp-файлів, troubleshooting, тестування, Ghostscript CVE policy і SLO орієнтири.
- **Verification:** Переглянуто `docs/RUNBOOK_PDF_OPTIMIZER.md` через `sed`; перевірено наявність ключових розділів через `rg` (`DATA_DIR`, `/ready`, rollback, telemetry, Trivy, `skip_optimization`, `kdv_optimize_data`).
- **Risks:** Команди з контейнерами використовують placeholders `<kdv-api-container-id>`/`<kdv-optimizer-container-id>`; оператор має підставити актуальні значення з `docker ps`.
- **Rollback:** Видалити `docs/RUNBOOK_PDF_OPTIMIZER.md` і цей changelog-запис.

## 2026-05-17 — README update для PDF optimizer

- **Context:** Після додавання `kdv-optimizer` і окремого runbook потрібно актуалізувати головний README як вхідну точку для розробників та операторів, не видаляючи існуючі розділи.
- **Change:** Оновлено `README.md`: статус і M8-контекст, ключові можливості, архітектурну схему, topology репозиторію, ENV для optimizer-а, локальний запуск, pytest-команди з `kdv-optimizer` у `PYTHONPATH`, CI/CD опис, rollback, API/інтеграції, health/readiness probes, SLO та документаційні посилання. Додано посилання на `docs/RUNBOOK_PDF_OPTIMIZER.md` і активний changelog `docs/changelogs/CHANGELOG_2026_VOL_03.md`.
- **Verification:** Переглянуто README через `sed`; перевірено ключові згадки через `rg` (`kdv-optimizer`, `RUNBOOK_PDF_OPTIMIZER`, `KDV_OPTIMIZER`, `PYTHONPATH`, `v0.4`).
- **Risks:** README лишається high-level документом; детальні operational команди винесені в runbook-и, насамперед `docs/RUNBOOK_PDF_OPTIMIZER.md`.
- **Rollback:** Відкотити зміни `README.md` і цей changelog-запис.

## 2026-05-17 — Logging для PDF optimizer pipeline

- **Context:** Після успішної UI-архівації важкого PDF файл завантажувався в DSpace вже оптимізованим, але у логах `kdv-api` і `kdv-optimizer` не було зрозумілих подій про передачу job, виконання Ghostscript, success або fallback/error.
- **Change:** Додано `INFO/WARNING` логування в `src/core.py` і `src/services/pdf.py` для skip/fallback/handoff/success сценаріїв інтегратора. Додано stdout logging setup і події прийому, submit, status transition, readiness failure у `kdv-optimizer/optimizer_app.py`. У `kdv-optimizer/kdv_optimizer/services/pdf.py` додано логи disk preflight, queue, старту Ghostscript, успішного завершення та помилок `missing_output`, `empty_output`, `larger_output`, `timeout`, `exception`. Додано focused тести на integrator handoff log і optimizer completion log.
- **Verification:** `python3 -m py_compile src/core.py src/services/pdf.py kdv-optimizer/optimizer_app.py kdv-optimizer/kdv_optimizer/services/pdf.py tests/test_core.py tests/test_services.py`; локальний `pytest` на хості не запустився через відсутній `pymarc`; контейнерна перевірка `docker run --rm --env-file .env.example --entrypoint python -e PYTHONPATH=/work:/work/kdv-optimizer -v /opt/kdv-integrator/kdv-integrator-event:/work -w /work kdv-integrator-event:0b6b996e30b3 -m pytest tests/test_core.py tests/test_services.py -q` -> `22 passed`; `git diff --check -- src/core.py src/services/pdf.py kdv-optimizer/optimizer_app.py kdv-optimizer/kdv_optimizer/services/pdf.py tests/test_core.py tests/test_services.py`. `ruff` не запущено: модуль/команда недоступні в host Python і в образі `kdv-integrator-event:0b6b996e30b3`.
- **Risks:** Логи містять локальні шляхи PDF у контейнері/shared volume та `job_id`, але не містять токенів, паролів або API-ключів. Polling `processing` не логується на кожен запит, щоб не створювати зайвий шум.
- **Rollback:** Відкотити зміни у `src/core.py`, `src/services/pdf.py`, `kdv-optimizer/optimizer_app.py`, `kdv-optimizer/kdv_optimizer/services/pdf.py`, `tests/test_core.py`, `tests/test_services.py` і цей changelog-запис.


## 2026-05-19 — Cover upload з готового файлу через `956$p`

- **Context:** Потрібно дозволити оператору Koha вказати готову обкладинку окремо від PDF книги: якщо в `956$p` є відносний шлях до файлу обкладинки, інтегратор має не генерувати JPG з PDF, а завантажити зазначений файл. Додаткова вимога: спроба upload обкладинки має виконуватися навіть тоді, коли файл книги з `956$u` відсутній на диску або саме підполе `956$u` порожнє.
- **Change:** `KohaClient.get_biblio_metadata()` читає `956$p` як `cover_path`. У `CoverService.process_book()` додано `cover_source_path`: для готової обкладинки пропускається `_generate_image()`, JPEG завантажується напряму, інші формати конвертуються в JPEG. У `process_integration_logic()` додано безпечне резолвлення відносних шляхів від `INTEGRATOR_MOUNT_PATH`; якщо PDF відсутній або `956$u` порожнє, але `956$p` задане, cover upload виконується до фіксації помилки PDF workflow.
- **Verification:** `python3 -m py_compile src/core.py src/koha.py src/services/covers.py tests/test_core.py tests/test_services.py`; `docker run --rm --env-file .env.example --entrypoint python -e PYTHONPATH=/work:/work/kdv-optimizer -v /opt/kdv-integrator/kdv-integrator-event:/work -w /work kdv-integrator-event:c2a5637038a6 -m pytest tests/test_core.py::test_koha_metadata_extracts_external_cover_path tests/test_core.py::test_external_cover_upload_runs_when_pdf_missing tests/test_core.py::test_external_cover_upload_runs_when_pdf_field_empty tests/test_core.py::test_cover_relative_path_cannot_escape_mount tests/test_services.py::test_cover_service_uploads_external_cover_without_pdf_generation -q` -> `5 passed`; `docker run --rm --env-file .env.example --entrypoint python -e PYTHONPATH=/work:/work/kdv-optimizer -v /opt/kdv-integrator/kdv-integrator-event:/work -w /work kdv-integrator-event:c2a5637038a6 -m pytest tests/test_core.py tests/test_services.py -q` -> `27 passed`; `docker run --rm --env-file .env.example --entrypoint python -e PYTHONPATH=/work:/work/kdv-optimizer -v /opt/kdv-integrator/kdv-integrator-event:/work -w /work kdv-integrator-event:c2a5637038a6 -m pytest tests -q` -> `55 passed`; `git diff --check -- src/core.py src/koha.py src/services/covers.py tests/test_core.py tests/test_services.py`.
- **Risks:** `956$p` приймає тільки відносні шляхи всередині `INTEGRATOR_MOUNT_PATH`; absolute path і `..` відхиляються. Для явної обкладинки strict-check на вже наявну Koha cover не застосовується, бо поле `956$p` вважається операторським джерелом істини і CGI upload використовує replace.
- **Rollback:** Відкотити зміни у `src/koha.py`, `src/services/covers.py`, `src/core.py`, `tests/test_core.py`, `tests/test_services.py`, `README.md`, `docs/ARCHITECTURE.md` і цей changelog-запис.
