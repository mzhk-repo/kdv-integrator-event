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

## 2026-05-20 — DSpace filename для optimized PDF зберігає rename-first імʼя

- **Context:** Після rename-first файл на диску має правильну назву `biblio_<id>_vNN.pdf`, але якщо PDF проходив через `kdv-optimizer`, у DSpace bitstream отримував basename тимчасового optimizer output (`<job_id>.pdf`). Неoptimized PDF завантажувалися коректно, бо upload path збігався з rename-first файлом.
- **Change:** `DSpaceClient.upload_to_item()` отримав опційний `upload_name`; multipart filename тепер може відрізнятися від фізичного `file_path`. `run_dspace_workflow()` передає `upload_name=os.path.basename(file_path)`, тобто rename-first імʼя, навіть якщо bytes читаються з optimized tmp-файлу. Оновлено `DSpaceClientWrapper`, ручний smoke stub і regression/contract тести.
- **Verification:** `python3 -m py_compile src/core.py src/dspace.py src/clients/dspace.py tests/test_core.py tests/test_contracts.py tests/manual_smoke.py`; `docker run --rm --env-file .env.example --entrypoint python -e PYTHONPATH=/work:/work/kdv-optimizer -v /opt/kdv-integrator/kdv-integrator-event:/work -w /work kdv-integrator-event:c2a5637038a6 -m pytest tests/test_core.py::test_run_dspace_optimized_upload_keeps_rename_first_filename tests/test_contracts.py::test_dspace_upload_to_item_uses_explicit_upload_name -q` -> `2 passed`; `docker run --rm --env-file .env.example --entrypoint python -e PYTHONPATH=/work:/work/kdv-optimizer -v /opt/kdv-integrator/kdv-integrator-event:/work -w /work kdv-integrator-event:c2a5637038a6 -m pytest tests/test_core.py tests/test_contracts.py -q` -> `25 passed`; `docker run --rm --env-file .env.example --entrypoint python -e PYTHONPATH=/work:/work/kdv-optimizer -v /opt/kdv-integrator/kdv-integrator-event:/work -w /work kdv-integrator-event:c2a5637038a6 -m pytest tests -q` -> `57 passed`; `git diff --check -- src/core.py src/dspace.py src/clients/dspace.py tests/test_core.py tests/test_contracts.py tests/manual_smoke.py`.
- **Risks:** Фізичний шлях optimized файлу лишається tmp/shared-volume path, але назва bitstream у DSpace тепер береться з rename-first PDF. Старі виклики `upload_to_item(item_uuid, file_path)` сумісні: без `upload_name` використовується попередній basename `file_path`.
- **Rollback:** Відкотити зміни у `src/core.py`, `src/dspace.py`, `src/clients/dspace.py`, `tests/test_core.py`, `tests/test_contracts.py`, `tests/manual_smoke.py` і цей changelog-запис.

## 2026-05-20 — Additional files upload у DSpace ORIGINAL через `956$q`

- **Context:** Потрібно дозволити одному Koha-запису завантажувати в DSpace ORIGINAL не лише primary файл з `956$u`, а й додаткові файли. За узгодженим спрощенням additional files не перейменовуються, не проходять через `kdv-optimizer` і завантажуються з поточним basename файлу на диску.
- **Change:** `KohaClient.get_biblio_metadata()` читає `956$q` як `additional_files`. У `core.py` додано parser для списку шляхів через `|`, безпечне резолвлення кожного відносного шляху від `INTEGRATOR_MOUNT_PATH` і non-fatal upload у той самий DSpace item/bundle ORIGINAL після primary upload або для `linked_existing` item. Результат задачі отримує `additional_files_uploaded` і `additional_files_failed`; missing/invalid/upload-failed additional files не валять primary архівацію. Оновлено `README.md` і `docs/ARCHITECTURE.md`.
- **Verification:** `python3 -m py_compile src/core.py src/koha.py tests/test_core.py`; focused Docker pytest для `956$q` сценаріїв -> `4 passed`; `docker run --rm --env-file .env.example --entrypoint python -e PYTHONPATH=/work:/work/kdv-optimizer -v /opt/kdv-integrator/kdv-integrator-event:/work -w /work kdv-integrator-event:c2a5637038a6 -m pytest tests/test_core.py -q` -> `21 passed`; `docker run --rm --env-file .env.example --entrypoint python -e PYTHONPATH=/work:/work/kdv-optimizer -v /opt/kdv-integrator/kdv-integrator-event:/work -w /work kdv-integrator-event:c2a5637038a6 -m pytest tests -q` -> `60 passed`; `git diff --check -- src/core.py src/koha.py tests/test_core.py README.md docs/ARCHITECTURE.md`.
- **Risks:** `956$q` використовує розділювач `|`; якщо файл не існує або шлях небезпечний (`absolute`/`..`), він потрапляє в `additional_files_failed`, але primary інтеграція лишається успішною. Additional files не оптимізуються і не перейменовуються, тому в DSpace зберігається їхній поточний basename.
- **Rollback:** Відкотити зміни у `src/koha.py`, `src/core.py`, `tests/test_core.py`, `README.md`, `docs/ARCHITECTURE.md` і цей changelog-запис.

## 2026-05-21 — Direct primary bitstream link у MARC 856

- **Context:** Інтегратор уже записував Handle DSpace у `856$u`, але Koha також має отримувати прямий download URL primary bitstream на кшталт `/bitstreams/<uuid>/download`. За узгодженим контрактом потрібно створювати два поля `856`: перше для файлу, друге для запису в репозиторії.
- **Change:** `DSpaceClient.upload_to_item()` тепер повертає JSON uploaded bitstream, якщо DSpace його віддав, а `run_dspace_workflow()` формує `primary_download_url` через `DSPACE_UI_URL/bitstreams/<uuid>/download`. Для `linked_existing` item додано `get_primary_bitstream()`, який читає перший bitstream з ORIGINAL bundle. `KohaClient.set_success()` приймає `primary_download_url` і перезаписує `856` у порядку: `856$u=<download_url>, $y=Файл`; `856$u=<handle_url>, $y=Запис в репозиторії`. Оновлено wrappers, smoke stub, README і ARCHITECTURE.
- **Verification:** `python3 -m py_compile src/core.py src/dspace.py src/clients/dspace.py src/koha.py src/clients/koha.py tests/test_core.py tests/test_contracts.py tests/manual_smoke.py`; focused Docker pytest для primary download URL, existing item, DSpace upload payload і двох `856` -> `5 passed`; `docker run --rm --env-file .env.example --entrypoint python -e PYTHONPATH=/work:/work/kdv-optimizer -v /opt/kdv-integrator/kdv-integrator-event:/work -w /work kdv-integrator-event:a9f2b98131cb -m pytest tests/test_core.py tests/test_contracts.py -q` -> `31 passed`; `docker run --rm --env-file .env.example --entrypoint python -e PYTHONPATH=/work:/work/kdv-optimizer -v /opt/kdv-integrator/kdv-integrator-event:/work -w /work kdv-integrator-event:a9f2b98131cb -m pytest tests -q` -> `63 passed`; `git diff --check -- src/core.py src/dspace.py src/clients/dspace.py src/koha.py src/clients/koha.py tests/test_core.py tests/test_contracts.py tests/manual_smoke.py`.
- **Risks:** Для нового upload direct URL зʼявляється тільки якщо DSpace повертає `uuid` uploaded bitstream. Для `linked_existing` береться перший bitstream з ORIGINAL bundle; якщо в item уже кілька файлів і перший не primary, може знадобитися окреме правило вибору primary.
- **Rollback:** Відкотити зміни у `src/core.py`, `src/dspace.py`, `src/clients/dspace.py`, `src/koha.py`, `src/clients/koha.py`, `tests/test_core.py`, `tests/test_contracts.py`, `tests/manual_smoke.py`, `README.md`, `docs/ARCHITECTURE.md` і цей changelog-запис.


## 2026-05-23 — RUNBOOK_ROBOT: запуск через SOPS/age env-контекст

- **Context:** Ручний запуск `docker compose exec kdv-api python3 scripts/robot.py candidates.txt` падав до старту `robot.py`, бо Docker Compose інтерполює `docker-compose.yml` до `exec` і не отримував `RCLONE_REMOTE_NAME` для `kdv-drive` rclone volume. У репозиторії штатний env-контракт уже переведений на `env.dev.enc`/`env.prod.enc` через SOPS/age та `ORCHESTRATOR_ENV_FILE`, тому fallback на plaintext `.env` не має бути основним шляхом для оператора.
- **Change:** `docs/RUNBOOK_ROBOT.md` доповнено кроком підготовки env-контексту: ручна розшифровка `env.dev.enc` або `env.prod.enc` у тимчасовий файл у `/dev/shm`, запуск `docker compose --env-file "${ENV_TMP}" exec ...`, варіант з готовим `ORCHESTRATOR_ENV_FILE`, preflight `docker compose --env-file "${ENV_TMP}" config`, і пояснення помилки `RCLONE_REMOTE_NAME is required`. Приклади з `ROBOT_*` переведено на CLI flags та `docker compose exec -e`, щоб параметри гарантовано потрапляли у процес всередині контейнера.
- **Verification:** Переглянуто оновлений фрагмент `docs/RUNBOOK_ROBOT.md`; перевірка Compose-контракту виконувалась через `docker compose --env-file .env.example config`, що проходить без помилки `RCLONE_REMOTE_NAME`.
- **Risks:** Команди з `env.prod.enc` потрібно виконувати лише на відповідному production host з правильним AGE-ключем і після перевірки цільового середовища. Тимчасовий env-файл видаляється через `shred -u` або `rm -f`, plaintext env не додається в репозиторій.
- **Rollback:** Відкотити зміни у `docs/RUNBOOK_ROBOT.md` і цей changelog-запис.


## 2026-05-23 — RUNBOOK_ROBOT: Swarm runtime замість Compose exec

- **Context:** Після переходу на Swarm команда `docker compose --env-file ... exec kdv-api ...` з ранбуку не запускала `robot.py`: локальний Compose-проєкт не має running service `kdv-api`, хоча Swarm-сервіс `kdv_integrator_event_kdv-api` працює `1/1`. Фактичний runtime-контейнер потрібно знаходити через Docker Swarm label.
- **Change:** `docs/RUNBOOK_ROBOT.md` переведено на Swarm-підключення: `docker service ls --filter name=kdv_integrator_event`, пошук `KDV_API_CID` через `docker ps --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api`, запуск `robot.py` через `docker exec "${KDV_API_CID}" ...`. Уточнено, що SOPS/age env потрібен для deploy/render manifest, а для ручного запуску у вже запущеному Swarm-контейнері runtime env уже завантажений через Swarm secret/entrypoint.
- **Verification:** `docker service ls` показав `kdv_integrator_event_kdv-api 1/1` і `kdv_integrator_event_kdv-optimizer 1/1`; `docker ps --format ...` знайшов локальний контейнер `kdv_integrator_event_kdv-api`; `docker exec <cid> python3 scripts/robot.py --help` успішно показав CLI; `docker exec <cid> curl -fsS http://localhost:5000/kdv/api/health` повернув `status=ok`.
- **Risks:** Якщо task `kdv-api` запущений на іншій Swarm node, локальний `docker ps` не знайде контейнер; тоді команду треба виконувати на node, де розміщений task, або підключатися до тієї node.
- **Rollback:** Повернути попередні Compose-команди в `docs/RUNBOOK_ROBOT.md` і видалити цей changelog-запис.


## 2026-05-23 — Wrapper `run-robot-swarm.sh` для запуску Robot у Swarm

- **Context:** Ручний запуск `robot.py` у Swarm вимагав кількох кроків: знайти runtime-контейнер `kdv-api`, перевірити health, передати host `candidates.txt` у контейнер, запустити `robot.py` з container path. Прямий `docker exec ... scripts/robot.py candidates.txt` падав, бо `candidates.txt` існує в host repo, але не монтується в `/app` Swarm-контейнера.
- **Change:** Додано `scripts/run-robot-swarm.sh`: wrapper резолвить env-контекст (`ORCHESTRATOR_ENV_FILE` → `SERVER_ENV`/`ENVIRONMENT_NAME` → `env.dev.enc`/`env.prod.enc` → `.env`), читає `STACK_NAME`/`SWARM_SERVICE_NAME`, знаходить контейнер за Swarm label, перевіряє `/kdv/api/health`, копіює candidates-файл у `/tmp/kdv-candidates.txt`, валідовує parsing і запускає `scripts/robot.py`. Додано `--dry-run`, прокидання `ROBOT_*` env і підтримку CLI-прапорів `robot.py`. Оновлено `docs/RUNBOOK_ROBOT.md` і `docs/scripts_runbook.md` на запуск через wrapper.
- **Verification:** `bash -n scripts/run-robot-swarm.sh`; `scripts/run-robot-swarm.sh --help`; `SERVER_ENV=dev scripts/run-robot-swarm.sh --dry-run candidates.txt` -> знайдено `kdv_integrator_event_kdv-api 1/1`, health пройшов, `candidates=2 list=['109', '110']`, batch не стартував.
- **Risks:** Wrapper має запускатися на Swarm node, де локально присутній task `kdv-api`; якщо task переїхав на іншу node, скрипт покаже `container ... not found on this node` і `docker service ps` для діагностики.
- **Rollback:** Видалити `scripts/run-robot-swarm.sh`, повернути ручні команди в `docs/RUNBOOK_ROBOT.md`/`docs/scripts_runbook.md` і видалити цей changelog-запис.


## 2026-05-23 — `run-robot-swarm.sh`: передача env у `docker exec`

- **Context:** Після запуску wrapper-а `robot.py` стартував у Swarm-контейнері, але падав на кожному ID з `KDV_API_TOKEN is missing`. Причина: `docker exec` створює новий процес і не успадковує env, який `entrypoint.sh` експортував для основного gunicorn-процесу.
- **Change:** `scripts/run-robot-swarm.sh` тепер передає знайдений/розшифрований env-файл у процес `robot.py` через `docker exec --env-file`. Додано dry-run перевірку `KDV_API_TOKEN` без виводу значення токена. Оновлено `docs/RUNBOOK_ROBOT.md` і `docs/scripts_runbook.md`.
- **Verification:** `bash -n scripts/run-robot-swarm.sh`; `SERVER_ENV=dev scripts/run-robot-swarm.sh --dry-run candidates.txt` -> health OK, candidates parse OK, `Validating robot auth env` OK, batch не стартував.
- **Risks:** `docker exec --env-file` використовує тимчасовий розшифрований env-файл на host; wrapper видаляє його через `shred -u`/`rm -f` у `trap`.
- **Rollback:** Прибрати `--env-file` передачу з `scripts/run-robot-swarm.sh` і цей changelog-запис.


## 2026-05-23 — `run-robot-swarm.sh`: синхронізація Robot log на host

- **Context:** Після успішного запуску `robot.py` лог писався у `/app/logs/robot_batch.log` всередині Swarm-контейнера, але host `logs/robot_batch.log` лишався порожнім, бо корінь репозиторію і `logs/` не змонтовані в service container.
- **Change:** `scripts/run-robot-swarm.sh` після завершення реального batch копіює контейнерний `/app/logs/robot_batch.log` у host `logs/robot_batch.log` через `docker exec ... cat`. Додано `ROBOT_HOST_LOG_PATH` і `ROBOT_CONTAINER_LOG_PATH` для override. Оновлено `docs/RUNBOOK_ROBOT.md` і `docs/scripts_runbook.md`.
- **Verification:** Поточний контейнерний log вручну синхронізовано у host `logs/robot_batch.log`; `tail -20 logs/robot_batch.log` показує успішний batch для `109` і `110`; `bash -n scripts/run-robot-swarm.sh`; `SERVER_ENV=dev scripts/run-robot-swarm.sh --dry-run candidates.txt`.
- **Risks:** Host log є копією стану контейнерного log на момент завершення wrapper-а; для live-tail під час виконання все ще потрібно читати stdout wrapper-а або контейнерний log напряму.
- **Rollback:** Прибрати sync-блок з `scripts/run-robot-swarm.sh`, оновлення документації і цей changelog-запис.


## 2026-05-23 — README update для Swarm Robot wrapper

- **Context:** Після додавання `scripts/run-robot-swarm.sh` головний README ще описував batch robot переважно як прямий `scripts/robot.py` workflow і не пояснював передачу `candidates.txt`/логів у Swarm runtime.
- **Change:** `README.md` точково оновлено: статус останніх змін, topology `scripts/`, опис `candidates.txt` і таблицю Batch-утиліт. Рекомендований Swarm-шлях тепер `scripts/run-robot-swarm.sh`, а `scripts/robot.py` позначено як внутрішню batch-логіку.
- **Verification:** Перевірено релевантні README-згадки через `rg` і перегляд diff; `git diff --check -- README.md docs/changelogs/CHANGELOG_2026_VOL_03.md`.
- **Risks:** README лишається high-level входом; детальні команди, dry-run, troubleshooting і rollback описані в `docs/RUNBOOK_ROBOT.md` та `docs/scripts_runbook.md`.
- **Rollback:** Відкотити зміни `README.md` і цей changelog-запис.


## 2026-05-23 — ARCHITECTURE update для Swarm Robot wrapper

- **Context:** Після впровадження `scripts/run-robot-swarm.sh` архітектурний документ ще описував старий M7/Compose-oriented runtime і не фіксував SOPS/age env resolution, `docker exec --env-file`, передачу `candidates.txt` у Swarm container та синхронізацію `robot_batch.log`.
- **Change:** `docs/ARCHITECTURE.md` точково оновлено: шапка версії до `v0.4.0-M8 + Swarm Robot wrapper`, CD/deploy path під `deploy-orchestrator-swarm.sh`, ops invariants для `env.dev.enc`/`env.prod.enc`, batch/Robot invariants для wrapper-а, а також code organization для `scripts/run-robot-swarm.sh`.
- **Verification:** Переглянуто релевантні секції через `rg`/`sed`; `git diff --check -- docs/ARCHITECTURE.md docs/changelogs/CHANGELOG_2026_VOL_03.md`.
- **Risks:** Документ лишається high-level описом; покрокові операторські команди збережені в `docs/RUNBOOK_ROBOT.md` і `docs/scripts_runbook.md`.
- **Rollback:** Відкотити зміни `docs/ARCHITECTURE.md` і цей changelog-запис.

## 2026-05-23 — Roadmap Google Drive source для `956$u`

- **Context:** Потрібно підготувати впровадження завантаження primary PDF за Google Drive URL з MARC `956$u`, не дублюючи service account secrets у KDV env-файлах і не змінюючи файл на Google Drive.
- **Change:** `docs/url-parcer-roadmap.md` переписано у Codex-friendly контрольований roadmap: зафіксовано read-only/no-writeback policy, runtime secret contract `Ansible Vault -> Swarm secret -> /run/secrets/gdrive_service_account_json`, інтеграцію через `scripts/deploy-orchestrator-swarm.sh`, SourceResolver/GoogleDriveSource архітектуру, ітерації з перевірками та DoD.
- **Verification:** Документ оновлено без додавання реальних secret values; `git diff --check -- docs/changelogs/CHANGELOG_2026_VOL_03.md`; `git diff --no-index --check /dev/null docs/url-parcer-roadmap.md` без whitespace warning-ів.
- **Risks:** Roadmap описує майбутні зміни, але ще не змінює deploy/code runtime; перед реалізацією потрібно підтвердити фактичну структуру ключів у Ansible Vault без виводу значень.
- **Rollback:** Відкотити `docs/url-parcer-roadmap.md` до попередньої концептуальної версії і видалити цей changelog-запис.

## 2026-05-23 — Roadmap Google Drive: `956$q` і ephemeral lifecycle

- **Context:** Після первинного roadmap потрібно уточнити, що Google Drive URL мають підтримуватися не тільки в primary `956$u`, а й у additional списку `956$q`; для файлів, завантажених з Google Drive, не потрібні локальні переміщення в `Processed` або `Error`.
- **Change:** `docs/url-parcer-roadmap.md` оновлено: `956$q` описано як змішаний список local/GDrive джерел через `|`; lifecycle Google Drive файлів змінено на ephemeral у `GDRIVE_TMP_DIR` без `version_and_move()` і без `FileService.move_to_error()`; помилки Google Drive primary логуються через існуючі Koha `956$y`/`956$z`, а помилки additional файлів лишаються non-fatal через `additional_files_failed`.
- **Verification:** `git diff --check -- docs/changelogs/CHANGELOG_2026_VOL_03.md`; `git diff --no-index --check /dev/null docs/url-parcer-roadmap.md` без whitespace warning-ів.
- **Risks:** Roadmap змінює майбутній дизайн, але ще не змінює runtime-код; під час реалізації потрібно розділити lifecycle local-managed і remote-ephemeral у тестах.
- **Rollback:** Відкотити уточнення `docs/url-parcer-roadmap.md` про `956$q`/ephemeral lifecycle і видалити цей changelog-запис.

## 2026-05-23 — Google Drive roadmap: завершення Ітерації 0

- **Context:** Перед змінами deploy/code потрібно підтвердити, що Ansible Vault уже містить Google service account JSON для dev/prod і що секрети можна використати як source of truth для майбутнього Swarm secret.
- **Change:** У `docs/url-parcer-roadmap.md` додано статус Ітерації 0: підтверджено наявність dev/prod `rclone.vault.yml`, ключ `vault_rclone_service_account_json`, валідний повний service account JSON у dev/prod, і вимогу запускати `ansible-vault` з `ANSIBLE_CONFIG=/opt/Ansible/ansible/ansible.cfg` поза Ansible repo.
- **Verification:** `test -f` для dev/prod Vault; `command -v ansible-vault`; filtered `ansible-vault view` з виводом тільки top-level key names; JSON parse з виводом тільки назв JSON-полів; secret values не друкувалися.
- **Risks:** Ітерація 0 не змінює runtime; наступна ітерація має акуратно інтегрувати versioned Swarm secret у `scripts/deploy-orchestrator-swarm.sh` і `docker-compose.swarm.yml` без передачі secret у `kdv-optimizer`.
- **Rollback:** Видалити статус Ітерації 0 з `docs/url-parcer-roadmap.md` і цей changelog-запис.

## 2026-05-23 — Google Drive Swarm secret deploy contract (Ітерація 1)

- **Context:** Після підтвердження Ansible Vault source of truth потрібно підготувати deploy path, який створює versioned Swarm secret для Google service account і монтує його тільки в `kdv-api` без дублювання secret payload у env.
- **Change:** Додано `scripts/render-versioned-gdrive-secret.sh`; `scripts/deploy-orchestrator-swarm.sh` викликає helper перед render Swarm manifest і експортує `GDRIVE_SERVICE_ACCOUNT_SECRET_NAME`; `docker-compose.swarm.yml` монтує external secret `gdrive_service_account_json` тільки в `kdv-api`; `.env.example` доповнено non-secret GDRIVE контрактом.
- **Verification:** `bash -n scripts/deploy-orchestrator-swarm.sh`; `bash -n scripts/render-versioned-gdrive-secret.sh`; `docker compose --env-file .env.example -f docker-compose.yml -f docker-compose.swarm.yml config`; `python3 -c "import yaml"`; `git diff --check` для tracked файлів; `git diff --no-index --check` для нових untracked docs/helper файлів без whitespace warning-ів.
- **Risks:** Реальний deploy і створення Swarm secret не запускалися; перший запуск helper-а створить Docker secret з реального Vault payload, тому його треба виконувати тільки на цільовій Swarm node після підтвердження середовища. `kdv-api` ще не використовує secret у коді до наступних ітерацій.
- **Rollback:** Видалити `scripts/render-versioned-gdrive-secret.sh`, прибрати виклик helper-а з `scripts/deploy-orchestrator-swarm.sh`, прибрати `gdrive_service_account_json` з `docker-compose.swarm.yml`, видалити GDRIVE non-secret ENV з `.env.example` і цей changelog-запис.

## 2026-05-23 — Source abstraction без Google API (Ітерація 2)

- **Context:** Перед Google Drive parser/download потрібно винести локальне резолвлення `956$u`/`956$q` з `core.py` у окремий source layer без зміни поточної поведінки local files.
- **Change:** Додано `src/services/sources.py` з `ResolvedSource`, `SourceResolver`, `LocalMountSource` і `SourceResolutionError`. `src/core.py` тепер резолвить primary `956$u`, cover `956$p` і additional `956$q` через `SourceResolver`; `_resolve_mount_relative_path()` лишено compatibility wrapper. Додано focused тести SourceResolver у `tests/test_services.py`.
- **Verification:** `python3 -m py_compile src/core.py src/services/sources.py tests/test_core.py tests/test_services.py`; host pytest для `tests/test_core.py` не запустився через відсутній `pymarc`; Docker focused pytest для SourceResolver -> `3 passed`; Docker focused core regression -> `4 passed`; Docker `pytest tests/test_core.py tests/test_services.py -q` -> `35 passed`; Docker `pytest tests -q` -> `66 passed`.
- **Risks:** Ітерація не додає Google URL support; `956$q` поки підтримує тільки local paths, але тепер має окремий `local_unmanaged` lifecycle для наступного розширення.
- **Rollback:** Видалити `src/services/sources.py`, повернути inline path resolution у `src/core.py`, прибрати нові SourceResolver тести й цей changelog-запис.

## 2026-05-23 — Google Drive URL parser для `956$u` і `956$q` (Ітерація 3)

- **Context:** Після source abstraction потрібно розпізнавати Google Drive URL у `956$u` і змішаному списку `956$q`, але ще без Google API, credentials або download-логіки.
- **Change:** У `src/services/sources.py` додано pure `GoogleDriveUrlParser` і `GoogleDriveFileRef`. Parser підтримує `drive.google.com/file/d/<file_id>/view`, `drive.google.com/open?id=<file_id>`, `drive.google.com/uc?id=<file_id>` і `resourcekey`. `SourceResolver.resolve_primary()` та `resolve_additional()` повертають `ResolvedSource` з `source_type="gdrive"`, `temporary=True`, `lifecycle_policy="remote_ephemeral"` і diagnostics для `file_id`/`resource_key`; local paths зберігають попередню поведінку. Folder links і сторонні HTTP/HTTPS URL явно відхиляються.
- **Verification:** `python3 -m py_compile src/services/sources.py tests/test_services.py`; `python3 -m pytest tests/test_services.py -q` -> `19 passed`. Parser-тести не виконують мережевих викликів і не потребують Google credentials.
- **Risks:** Ітерація тільки парсить Google Drive URL; primary Google URL поки не створює локальний файл для архівації, а additional Google URL поки не завантажується у DSpace. Download/read-only Google API лишається межами Ітерації 4.
- **Rollback:** Прибрати `GoogleDriveUrlParser`/`GoogleDriveFileRef` і gdrive-гілки з `SourceResolver`, видалити додані parser-тести, прибрати статус Ітерації 3 з `docs/url-parcer-roadmap.md` і цей changelog-запис.

## 2026-05-23 — GoogleDriveSource read-only download (Ітерація 4)

- **Context:** Після parser-а потрібно реально materialize-ити Google Drive URL з `956$u` і `956$q` у локальний read-only temp PDF для DSpace workflow без writeback до Google Drive і без реальних credentials у тестах.
- **Change:** У `requirements.txt` додано `google-api-python-client`, `google-auth`, `google-auth-httplib2`. У `src/services/sources.py` реалізовано `GoogleDriveSource`: lazy readonly Drive client, service account тільки через `GDRIVE_SERVICE_ACCOUNT_FILE`, metadata checks (`name`, `mimeType`, `size`, `capabilities.canDownload`), `GDRIVE_ALLOWED_MIME_TYPES`, `GDRIVE_MAX_BYTES`, `resourcekey`, download у `.part` і atomic rename у `.pdf` з cleanup `.part` при помилці. `SourceResolver.materialize()` підключає download для `gdrive` sources. У `src/core.py` Google primary не проходить `FileService.version_and_move()` і не переміщується в `Error`; `run_dspace_workflow()` отримав `upload_name`, а Google additional у `956$q` download/upload-иться non-fatal через `additional_files_failed`.
- **Verification:** `python3 -m py_compile src/services/sources.py src/core.py tests/test_services.py tests/test_core.py`; host `python3 -m pytest tests/test_services.py -q` -> `24 passed`; host `pytest tests/test_core.py` не запускається через відсутній `pymarc`; Docker `docker run --rm --env-file .env.example --entrypoint python -e PYTHONPATH=/work:/work/kdv-optimizer -v /opt/kdv-integrator/kdv-integrator-event:/work -w /work kdv-integrator-event:0dd51ce18c98 -m pytest tests/test_services.py tests/test_core.py -q` -> `50 passed`; Docker full `pytest tests -q` -> `81 passed`.
- **Risks:** Реальний Google API не викликався в тестах; перша runtime-перевірка потребує `GDRIVE_ENABLED=true`, змонтованого `/run/secrets/gdrive_service_account_json` і доступу service account до файлу. Cleanup старих final temp-файлів у `GDRIVE_TMP_DIR` ще не додано і лишається задачею Ітерації 5.
- **Rollback:** Прибрати Google dependencies з `requirements.txt`, видалити `GoogleDriveSource`/`SourceResolver.materialize()`, повернути local-only primary/additional flow у `src/core.py`, прибрати додані тести, видалити статус Ітерації 4 з `docs/url-parcer-roadmap.md` і цей changelog-запис.

## 2026-05-23 — Google Drive lifecycle і cleanup (Ітерація 5)

- **Context:** Після read-only download потрібно закрити lifecycle-контракт для Google Drive temp-файлів: не повторювати download при валідному cache hit, не вважати `.part` готовим PDF, чистити старі temp-файли тільки в `GDRIVE_TMP_DIR`, і гарантувати, що Google files не потрапляють у локальні `Processed`/`Error`.
- **Change:** `GoogleDriveSource` тепер формує deterministic cache path у `GDRIVE_TMP_DIR` за fingerprint `file_id`/`resourcekey`/metadata (`name`, `mimeType`, `size`) і перевикористовує валідний завершений `.pdf` без повторного download. Перед новим download stale `.part` видаляється, а при download error `.part` прибирається. Додано `cleanup_stale_files()` для видалення старих `.pdf`/`.part` тільки всередині `GDRIVE_TMP_DIR`; додано non-secret ENV `GDRIVE_TMP_TTL_SECONDS=86400` у `.env.example`. Додано regression-тести, що primary download failure/too-large не створює DSpace item і не викликає `move_to_error`, а local primary lifecycle лишається незмінним.
- **Verification:** `python3 -m py_compile src/services/sources.py src/core.py tests/test_services.py tests/test_core.py`; host `python3 -m pytest tests/test_services.py -q` -> `27 passed`; Docker `docker run --rm --env-file .env.example --entrypoint python -e PYTHONPATH=/work:/work/kdv-optimizer -v /opt/kdv-integrator/kdv-integrator-event:/work -w /work kdv-integrator-event:0dd51ce18c98 -m pytest tests/test_services.py tests/test_core.py -q` -> `56 passed`; Docker full `pytest tests -q` -> `87 passed`.
- **Risks:** Cleanup виконується під час `GoogleDriveSource.materialize()` і не є окремим background thread; якщо `kdv-api` довго не отримує Google Drive задач, старі temp-файли дочекаються наступної Google Drive інтеграції або ручного cleanup.
- **Rollback:** Прибрати deterministic cache/cleanup з `GoogleDriveSource`, видалити `GDRIVE_TMP_TTL_SECONDS` з `.env.example`, прибрати додані lifecycle/cache тести, видалити статус Ітерації 5 з `docs/url-parcer-roadmap.md` і цей changelog-запис.

## 2026-05-23 — Google Drive observability і runbook (Ітерація 6)

- **Context:** Після lifecycle/cache потрібно зробити Google Drive source видимим для оператора без витоку секретів: безпечні logs, README/ARCHITECTURE оновлення і runbook для dev smoke, troubleshooting та rollback.
- **Change:** `GoogleDriveSource` отримав safe logging для metadata accepted, cache hit, downloaded і failed подій з `source_type=gdrive`, safe `file_id`, `mime_type`, `size`, `duration_ms`, `reason`, без повного URL, `resourcekey`, OAuth token або service account JSON. Оновлено `README.md` і `docs/ARCHITECTURE.md` з Google Drive source contract, lifecycle local vs remote, no-writeback policy і Swarm secret boundary. Додано `docs/RUNBOOK_GDRIVE_SOURCE.md` з secret check через `test -s`, manual dev smoke, troubleshooting і rollback.
- **Verification:** `python3 -m py_compile src/services/sources.py`; `python3 -m pytest tests/test_services.py -q` -> `28 passed`; Docker full `pytest tests -q` -> `88 passed`; `rg -n "GDRIVE|Google Drive|gdrive_service_account_json" README.md docs .env.example docker-compose.swarm.yml scripts/deploy-orchestrator-swarm.sh`; `git diff --check -- README.md docs src/services/sources.py tests/test_services.py`.
- **Risks:** Observability описує runtime events, але реальний dev smoke з Google Drive service account ще не запускався; це межа Ітерації 7. Logs містять safe file id/hash і metadata size, але не повинні використовуватися як джерело secret або raw URL.
- **Rollback:** Прибрати safe logging helpers з `GoogleDriveSource`, видалити `docs/RUNBOOK_GDRIVE_SOURCE.md`, повернути README/ARCHITECTURE до попереднього опису local/PDF optimizer flow, прибрати статус Ітерації 6 з `docs/url-parcer-roadmap.md` і цей changelog-запис.
