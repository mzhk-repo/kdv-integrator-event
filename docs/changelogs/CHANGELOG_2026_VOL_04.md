
## 2026-05-27 — Koha Export docs: optional `biblionumber` range

- **Context:** Потрібно дозволити оператору запускати Koha Export не тільки для всього каталогу, а й для обмеженого inclusive діапазону Koha `biblionumber`, наприклад для перевірки або передачі окремої партії записів.
- **Change:** `docs/koha-export/PRD_Koha_Export_Module.md` оновлено до v2.3: додано сценарій range export, criteria filter, CLI examples `--biblionumber-from` / `--biblionumber-to`, правила inclusive range, keyset старт `from - 1`, заборону env-перемикачів для range і тестовий сценарій. `docs/koha-export/ROADMAP_Koha_Export_Module.md` оновлено до v1.2: range support додано в задачі 2.1, 2.2, 4.1, integration checklist і Definition of Done.
- **Verification:** Документаційна зміна без runtime-коду; перевірено ключові згадки через `rg` (`biblionumber-from`, `biblionumber-to`, `range export`, `v2.3`, `v1.2`); `git diff --check -- docs/koha-export/PRD_Koha_Export_Module.md docs/koha-export/ROADMAP_Koha_Export_Module.md docs/changelogs/CHANGELOG_2026_VOL_03.md` без whitespace-зауважень.
- **Risks:** `KohaApiClient` уже підтримує range для задачі 2.1, але CLI/`RuntimeOptions`, filtering/orchestrator і end-to-end range тести ще мають бути додані в наступних задачах.
- **Rollback:** Повернути PRD v2.2/roadmap v1.1 формулювання без range export і видалити цей changelog-запис.

## 2026-05-27 — Koha Export filter_exportable_biblios (Завдання 2.2)

- **Context:** Після `KohaApiClient` з keyset pagination потрібен окремий filter layer, який відділяє state DB filtering від майбутнього MARC parsing і не створює дублікати для вже завершених або recoverable runs.
- **Change:** Додано `src/export_module/koha/filters.py` з `filter_exportable_biblios()`: completed `biblionumber` виключаються, retry-eligible failed records включаються, failed records із вичерпаним retry limit не проходять повторно, recoverable staged states (`xlsx_generated`, `gdrive_uploaded`, `email_sent`) блокують дубльований export, optional inclusive range `biblionumber_from` / `biblionumber_to` застосовується до candidate list. `ExportRepository` доповнено read-only helper `get_failed_biblionumbers()` для коректного розрізнення retry-eligible і retry-exhausted failed records. Додано focused тести `tests/test_export_koha_filters.py`.
- **Verification:** `python3 -m py_compile src/export_module/db/repository.py src/export_module/koha/filters.py tests/test_export_koha_filters.py`; `python3 -m pytest tests/test_export_repository.py tests/test_export_koha_filters.py -q` -> `15 passed`; `python3 -m pytest tests/test_export_schema.py tests/test_export_repository.py tests/test_export_config.py tests/test_export_mapping_loader.py tests/test_export_logger.py tests/test_export_koha_client.py tests/test_export_koha_filters.py -q` -> `45 passed`.
- **Risks:** `filter_exportable_biblios()` поки не підключений до `ExportOrchestrator`, бо orchestration layer буде реалізовано в наступних фазах; CLI range flags також лишаються для задачі 4.1.
- **Rollback:** Видалити `src/export_module/koha/filters.py`, `tests/test_export_koha_filters.py`, прибрати `get_failed_biblionumbers()` із `src/export_module/db/repository.py` і цей changelog-запис.


## 2026-05-27 — Koha Export MARCParser з transforms і Authorized values (Завдання 2.3)

- **Context:** Після Koha API client і candidate filtering export-модулю потрібен defensive MARCXML parser, який перетворює MARC records у плоскі XLSX-ready dict-и без падіння на відсутніх полях або битому XML.
- **Change:** Додано `src/export_module/marc/parser.py` з `MARCParser`: extraction controlfield `001`, datafield/subfield extraction з `join` і `strip_chars`, transform `authorized_value` через `ExportDictionaries`, static columns після MARC extraction, unknown authorized values за `unknown_policy` і warning + `None` для malformed MARCXML. Додано focused тести `tests/test_export_marc_parser.py`.
- **Verification:** `python3 -m py_compile src/export_module/marc/parser.py tests/test_export_marc_parser.py`; `python3 -m pytest tests/test_export_mapping_loader.py tests/test_export_marc_parser.py -q` -> `11 passed`; `python3 -m pytest tests/test_export_schema.py tests/test_export_repository.py tests/test_export_config.py tests/test_export_mapping_loader.py tests/test_export_logger.py tests/test_export_koha_client.py tests/test_export_koha_filters.py tests/test_export_marc_parser.py -q` -> `52 passed`.
- **Risks:** Parser використовує стандартний `xml.etree.ElementTree`, бо `pymarc` заявлений у `requirements.txt`, але не встановлений у поточному runtime; якщо production image матиме `pymarc`, поточний parser все одно лишається без додаткової залежності й читає MARC21 slim XML за namespace/local-name.
- **Rollback:** Видалити `src/export_module/marc/parser.py`, `tests/test_export_marc_parser.py` і цей changelog-запис.
