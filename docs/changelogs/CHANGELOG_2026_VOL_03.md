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
