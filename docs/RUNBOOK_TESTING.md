# Runbook: Тестування змін (unit + integration + contract)

Мета: простими кроками показати, як безпечно змінювати модулі, додавати мок‑класи і запускати unit/integration/contract тести локально у контейнері або хості.

**Вимоги перед тестуванням**
- Файли проекту оновлені у робочій копії репо.
- `.env` має містити обов'язкові змінні (особливо `KDV_API_TOKEN`) — перевірте [README.md](../README.md).
- Рекомендується запускати тести всередині контейнера `kdv-api` (щоб підхопити ті ж залежності, що у production compose).

**Короткий огляд файлів (корисні посилання)**
- Код ядра: [src/core.py](../src/core.py)
- Менеджер задач: [src/tasks.py](../src/tasks.py)
- Клієнти‑обгортки: [src/clients/koha.py](../src/clients/koha.py), [src/clients/dspace.py](../src/clients/dspace.py)
- Сервіси: [src/services/files.py](../src/services/files.py), [src/services/covers.py](../src/services/covers.py)
- Тести: [tests/test_core.py](../tests/test_core.py), [tests/test_services.py](../tests/test_services.py), [tests/test_contracts.py](../tests/test_contracts.py)
- Ops runbook (інциденти): [docs/RUNBOOK_MAYDAY.md](RUNBOOK_MAYDAY.md)
- Активний changelog: [CHANGELOGS/CHANGELOG_2026_VOL_01.md](../CHANGELOGS/CHANGELOG_2026_VOL_01.md)

**1) Запуск середовища (контейнер)**

Запускаємо контейнер з усіма залежностями (poppler/pdf2image і т.д.):

```bash
docker compose pull
docker compose up -d
```

Потім виконувати команди всередині контейнера, щоб гарантувати сумісність залежностей:

```bash
# healthcheck після старту контейнера
./scripts/healthcheck.sh

# запуск усіх тестів у контейнері
docker exec -e PYTHONPATH=/app kdv-api pytest -q

# запуск тільки contract-тестів (M6)
docker exec -e PYTHONPATH=/app kdv-api pytest tests/test_contracts.py -q

# або окремий тест
docker exec -e PYTHONPATH=/app kdv-api pytest tests/test_core.py::test_parse_marc_rules_basic -q
```

Якщо контейнер уже запущений, але ви змінили `KDV_IMAGE_VERSION`/`KDV_IMAGE` у `.env` — оновіть образ і перезапустіть:

```bash
docker compose pull
docker compose up -d
```

**2) Тестування локально без контейнера (опціонально)**

Якщо ви налаштували локальне `python`/`pip`, встановіть залежності і запускайте pytest:

```bash
python3 -m pip install -r requirements.txt
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

**4.1) Contract-тестування (M6) — що саме перевіряємо**

- `tests/test_contracts.py` перевіряє HTTP-контракти без реальних мережевих викликів (через monkeypatch/mocks).
- DSpace контракт:
    - `GET /pid/find` з правильними query params.
    - `PATCH /core/items/{item_uuid}` із `Content-Type: application/json-patch+json` і коректним JSON Patch body.
- Koha CGI контракт:
    - `_step1_upload_temp`: заголовки `X-Requested-With`, `CSRF-TOKEN`, `Referer`, і multipart field `file`.
    - `_step2_process_attach`: payload поля (`op=cud-process`, `uploadedfileid`, `csrf_token`, `replace`).
    - `_ensure_cgi_login`: ключові назви полів (`login_userid`, `login_password`, `koha_login_context`, `csrf_token`).

**5) Інтеграційні перевірки API**

Щоб симулювати виклик з Koha UI, виконайте запит у контейнері (використовуючи правильний `KDV_API_TOKEN`):

```bash
# знайти токен у середовищі контейнера
docker exec kdv-api env | grep KDV_API_TOKEN
# виклик endpoint всередині контейнера
docker exec kdv-api curl -X POST http://localhost:5000/kdv/api/integrate/12 -H "X-KDV-TOKEN:<token>"
```

Переконайтесь, що контейнер працює на актуальній версії image (`docker compose pull && docker compose up -d` після змін env версії).

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

Поточний орієнтир для повного прогону в контейнері:
- `docker exec -e PYTHONPATH=/app kdv-api pytest -q` -> очікувано `22 passed` (станом на 2026-03-05).

**8) Оновлення CHANGELOG після суттєвих змін**

Кожна суттєва зміна має записуватися у активний том у `CHANGELOGS/`. Використовуйте шаблон:

- **Context:** чому зміна потрібна
- **Change:** що зроблено
- **Verification:** як перевірено (команди)
- **Risks:** можливі ризики
- **Rollback:** як відкотити

Файл активного тому: `CHANGELOGS/CHANGELOG_2026_VOL_01.md`.

**9) Типові помилки та як їх вирішувати**
- `run_dspace_workflow() got an unexpected keyword argument 'koha_client'` — означає, що контейнер працює зі старою версією image. Рішення: оновити `KDV_IMAGE_VERSION` (або `KDV_IMAGE`) і виконати `docker compose pull && docker compose up -d`.
- `Invalid Token` від API — перевірити `KDV_API_TOKEN` у `.env` та середовищі контейнера.
- Проблеми з PDF/Poppler — переконатися, що `poppler-utils` встановлені у образі (включено в Dockerfile) і що файл існує на підмонтованому шляху `INTEGRATOR_MOUNT_PATH`.
- Падає `tests/test_contracts.py::test_koha_cgi_login_contract_payload_field_names` через `login_userid`/`login_password` — тест має порівнюватися з фактичними значеннями `KOHA_USER`/`KOHA_PASS` з runtime env, а не з hardcoded рядками.

**10) Best practices**
- Писати невеликі, атомарні тести (одне твердження — одна логіка).
- Використовувати DI для підміни зовнішніх залежностей замість мережевих викликів.
- Додавати changelog запис під час мерджу змін.
- Переконатися, що `docker compose pull && docker compose up -d` і `./scripts/healthcheck.sh` виконуються перед тестовим прогоном образу.

---

Якщо потрібно, я додам приклади тестів із `monkeypatch` для `app._make_clients()` або приклади CI‑job для GitHub Actions, щоб автоматично збирати образ і запускати `pytest` в контейнері.
