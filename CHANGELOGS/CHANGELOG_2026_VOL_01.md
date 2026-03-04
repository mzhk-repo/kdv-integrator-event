# CHANGELOG 2026 VOL 01

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
