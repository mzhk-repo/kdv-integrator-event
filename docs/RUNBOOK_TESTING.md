# Runbook: Тестування змін (моки + unit)

Мета: простими кроками показати, як безпечно змінювати модулі, додавати мок‑класи і запускати юніт/інтеграційні тести локально у контейнері або хості.

**Вимоги перед тестуванням**
- Файли проекту оновлені у робочій копії репо.
- `.env` має містити обов'язкові змінні (особливо `KDV_API_TOKEN`) — перевірте [README.md](README.md).
- Рекомендується запускати тести всередині контейнера `kdv-api` (щоб підхопити ті ж залежності, що у production compose).

**Короткий огляд файлів (корисні посилання)**
- Код ядра: [src/core.py](src/core.py)
- Менеджер задач: [src/tasks.py](src/tasks.py)
- Клієнти‑обгортки: [src/clients/koha.py](src/clients/koha.py), [src/clients/dspace.py](src/clients/dspace.py)
- Сервіси: [src/services/files.py](src/services/files.py), [src/services/covers.py](src/services/covers.py)
- Тести: [tests/test_core.py](tests/test_core.py), [tests/test_services.py](tests/test_services.py)
- Активний changelog: [CHANGELOGS/CHANGELOG_2026_VOL_01.md](CHANGELOGS/CHANGELOG_2026_VOL_01.md)

**1) Запуск середовища (контейнер)**

Запускаємо контейнер з усіма залежностями (poppler/pdf2image і т.д.):

```bash
docker compose up -d --build
```

Потім виконувати команди всередині контейнера, щоб гарантувати сумісність залежностей:

```bash
# інтерактивний запуск тестів у контейнері
docker exec -e PYTHONPATH=/app kdv-api pytest -q

# або окремий тест
docker exec -e PYTHONPATH=/app kdv-api pytest tests/test_core.py::test_parse_marc_rules_basic -q
```

Якщо контейнер уже запущений, але ви змінили код — перезапустіть/перебудуйте образ:

```bash
docker compose up -d --build
```

**2) Тестування локально без контейнера (опціонально)**

Якщо ви налаштували локальне `python`/`pip`, встановіть залежності і запускайте pytest:

```bash
python3 -m pip install -r requirements.txt
python3 -m pip install pytest
PYTHONPATH=$(pwd) pytest -q
```

> Примітка: на деяких хостах краще використовувати віртуальне середовище.

**3) Як писати тест з моками (шаблон)**

- Використовуйте thin‑wrappers у `src/clients` або створюйте прості stub‑класи у тесті.
- Завдяки DI в `src/core.py` ви можете передавати `koha_client`/`dspace_client` прямо у `run_dspace_workflow` або `process_integration_logic`.

Приклад (в тесті):

```python
class StubKoha:
    def get_biblio_metadata(self, biblio):
        return {"file_path":"missing.pdf","collection_uuid":"coll-1"}
    def _get_biblio_xml(self, biblio):
        return '<record>...</record>'
    def set_status(self, biblio, status, msg=None):
        self.logged = (biblio,status,msg)

class StubDSpace:
    def find_item_by_biblionumber(self, b): return None
    def create_item_direct(self, coll, md): return {"uuid":"u1","handle":"1/2"}
    def upload_to_item(self, uuid, path): return True

# виклик прямо в тесті
res = run_dspace_workflow(1, '/tmp/f.pdf', {'collection_uuid': 'c'}, koha_client=StubKoha(), dspace_client=StubDSpace())
assert 'handle' in res
```

**4) Тестування flow через `task_manager`**

`TaskManager.start_task` зараз приймає `kwargs` і прокидає їх у функцію. Щоб перевірити асинхронну поведінку у тесті:

- Запускайте задачу через `task_manager.start_task(...)` і чекайте на зміну статусу з `processing` на `success/error` з таймаутом (наприклад, 2s). Приклад у `tests/test_core.py`.

**5) Інтеграційні перевірки API**

Щоб симулювати виклик з Koha UI, виконайте запит у контейнері (використовуючи правильний `KDV_API_TOKEN`):

```bash
# знайти токен у середовищі контейнера
docker exec kdv-api env | grep KDV_API_TOKEN
# виклик endpoint всередині контейнера
docker exec kdv-api curl -X POST http://localhost:5000/kdv/api/integrate/12 -H "X-KDV-TOKEN:<token>"
```

Переконайтесь, що контейнер містить актуальний код (зробіть `docker compose up -d --build` після змін).

**6) Локальні мок‑інтеграції для `app`**

`app.py` надає фабрику `_make_clients()` — у тестах можна змінити або підмінити її (monkeypatch) щоб повністю контролювати поведінку при HTTP‑виклику.

Приклад у pytest:

```python
def test_http_flow(monkeypatch):
    def fake_make():
        return StubKoha(), StubDSpace()
    monkeypatch.setattr('src.app._make_clients', fake_make)
    # Викликати шляхом тестового клієнта Flask або docker curl
```

**7) Як додати тест і перевірити coverage**

- Додайте файл у `tests/` з префіксом `test_`.
- Запустіть `pytest` в контейнері або локально.
- Для швидкого локального дебагу використовуйте `tests/manual_smoke.py`.

**8) Оновлення CHANGELOG після суттєвих змін**

Кожна суттєва зміна має записуватися у активний том у `CHANGELOGS/`. Використовуйте шаблон:

- **Context:** чому зміна потрібна
- **Change:** що зроблено
- **Verification:** як перевірено (команди)
- **Risks:** можливі ризики
- **Rollback:** як відкотити

Файл активного тому: `CHANGELOGS/CHANGELOG_2026_VOL_01.md`.

**9) Типові помилки та як їх вирішувати**
- `run_dspace_workflow() got an unexpected keyword argument 'koha_client'` — означає, що контейнер працює зі старою версією коду. Рішення: `docker compose up -d --build`.
- `Invalid Token` від API — перевірити `KDV_API_TOKEN` у `.env` та середовищі контейнера.
- Проблеми з PDF/Poppler — переконатися, що `poppler-utils` встановлені у образі (включено в Dockerfile) і що файл існує на підмонтованому шляху `INTEGRATOR_MOUNT_PATH`.

**10) Best practices**
- Писати невеликі, атомарні тести (одне твердження — одна логіка).
- Використовувати DI для підміни зовнішніх залежностей замість мережевих викликів.
- Додавати changelog запис під час мерджу змін.
- Переконатися, що `docker compose up -d --build` виконується у CI перед тестовим прогоном образу.

---

Якщо потрібно, я додам приклади тестів із `monkeypatch` для `app._make_clients()` або приклади CI‑job для GitHub Actions, щоб автоматично збирати образ і запускати `pytest` в контейнері.