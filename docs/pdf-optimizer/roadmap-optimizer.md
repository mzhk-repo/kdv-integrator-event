# 🤖 Roadmap для Codex: Модуль оптимізації PDF (KDV Integrator v0.4.0)

**Призначення документу:** Покрокові інструкції для AI-агента Codex. Кожна задача є самодостатньою одиницею роботи з явним контекстом, переліком файлів, конкретними інструкціями та критеріями прийняття (Definition of Done).

**Базовий документ:** PRD: Модуль автоматичної оптимізації PDF v0.4.0-rev3  
**Мова коду:** Python 3.11+, Flask, Docker  
**Загальна кількість задач:** 18  

---

## Правила виконання для Codex

1. **Виконувати задачі суворо в порядку фаз.** Фаза 0 є release gate — без неї Фаза 1+ не починається.
2. **Кожна задача — один PR/commit.** Не об'єднувати задачі з різних фаз.
3. **Перед написанням коду** — прочитати розділ "Контекст та обмеження" задачі повністю.
4. **Після виконання** — пройти чекліст "Критерії прийняття". Якщо хоч один пункт не виконано — задача не завершена.
5. **Глобальне правило безпеки:** ніколи не передавати довільні file paths між сервісами. Тільки `job_id` (UUID). Шляхи будуються виключно на стороні `kdv-optimizer` всередині `/data/kdv_optimize/`.
6. **`run_ghostscript`** завжди є top-level функцією модуля, не методом класу — вимога `pickle`-сумісності для `ProcessPoolExecutor`.
7. **Фактичні repo-контракти:** GitHub Actions workflow у цьому репозиторії — `.github/workflows/main.yml`; canonical batch script — `scripts/robot.py`; Koha UI script живе поза цим репозиторієм у `/opt/Koha/koha-deploy/IntranetUserJS.js`.

---

## 📋 Фаза 0 — R&D Benchmark (Release Gate)

> ⛔ Без успішного проходження Фази 0 робота над Фазами 1–5 не починається.
> Результат: JSON-звіти у `scripts/benchmark_results/` з `quality_ok: true` для scan-like файлів.

---

### Задача 0.1 — Створити `scripts/poc_optimizer.py`

**Контекст та обмеження:**
Ізольований benchmark-скрипт для порівняння PDF-рушіїв на еталонному датасеті. Не є частиною production-коду — запускається вручну розробником. Скрипт має тестувати 4 рушії (`ghostscript`, `pymupdf`, `pikepdf`, `qpdf`) на 5 еталонних файлах і генерувати JSON-звіт для кожної пари. Поле `quality_ok` залишається `null` — заповнюється вручну після візуального контролю.

**Файли для створення/зміни:**
- `scripts/poc_optimizer.py` — новий файл
- `scripts/benchmark_results/.gitkeep` — директорія для звітів

**Детальні інструкції:**

Реалізувати функцію `run_benchmark(engine: str, pdf_path: str) -> dict` яка:
- Замірює час виконання (`time.perf_counter`)
- Замірює пікове споживання RAM через `tracemalloc` або `psutil.Process().memory_info().rss`
- Ловить будь-який виняток у `exception` поле
- Перевіряє чи `output_larger = optimized_size > original_size`
- Повертає словник відповідно до контракту:

```json
{
  "engine": "ghostscript",
  "file": "heavy_scan_100mb.pdf",
  "original_mb": 103.4,
  "optimized_mb": 18.2,
  "reduction_pct": 82.4,
  "pages": 50,
  "time_s": 67.3,
  "peak_ram_mb": 312,
  "output_larger": false,
  "exception": null,
  "quality_ok": null
}
```

Кожен рушій реалізується як окрема функція:
- `_run_ghostscript(input_path, output_path)` — через `subprocess.run` з `nice -n 15`, `ionice -c 3`, прапорами `-dPDFSETTINGS=/ebook -dCompatibilityLevel=1.4 -dSAFER`
- `_run_pymupdf(input_path, output_path)` — через `fitz.open()` + `doc.save(output, garbage=4, deflate=True)`
- `_run_pikepdf(input_path, output_path)` — через `pikepdf.open()` + `pdf.save(output, compress_streams=True)`
- `_run_qpdf(input_path, output_path)` — через `subprocess.run(["qpdf", "--linearize", input_path, output_path])`

Підрахунок сторінок через `pdfinfo` (не `pypdf`) з timeout=10s.

Головна функція: `main()` — читає `DATASET_DIR` з ENV або аргументу, ітерує файли × рушії, зберігає `benchmark_results/{engine}_{filename}.json`.

Фінальний рядок у stdout: `cat scripts/benchmark_results/*.json | jq -s 'sort_by(.reduction_pct) | reverse'`

**Критерії прийняття:**
- [x] Скрипт запускається без помилок на будь-якому з 5 тестових файлів
- [x] При пошкодженому PDF: `exception` заповнено, скрипт не крашиться, переходить до наступного файлу
- [x] При `output_larger=true`: зафіксовано у JSON, файл не видаляється (потрібен для аналізу)
- [x] JSON-файли зберігаються у `scripts/benchmark_results/`
- [x] Поле `quality_ok` завжди `null` у згенерованих файлах

---

### Задача 0.2 — Валідація результатів PoC (ручна + автоматична)

**Контекст та обмеження:**
Після запуску `poc_optimizer.py` на реальному датасеті людина заповнює `quality_ok`. Codex додає автоматичний validation скрипт, що перевіряє виконання критеріїв успіху R&D.

**Файли для створення/зміни:**
- `scripts/validate_poc_results.py` — новий файл

**Детальні інструкції:**

Скрипт `validate_poc_results.py` читає всі JSON з `scripts/benchmark_results/`, перевіряє:
1. Для файлів де `file` містить `scan`: `reduction_pct >= 50`
2. Для всіх файлів де `quality_ok` не null: `quality_ok == true`
3. Для всіх файлів: `peak_ram_mb <= 500`
4. Для всіх файлів: `time_s < 120`
5. Для файлу `already_optimized`: `output_larger == false`

Виводить таблицю у stdout. Завершується з `sys.exit(1)` якщо будь-який критерій не виконано — це блокує CI gate.

**Критерії прийняття:**
- [x] `sys.exit(0)` при всіх виконаних критеріях
- [x] `sys.exit(1)` з повідомленням при будь-якому провалі
- [x] Скрипт обробляє відсутні `quality_ok` (null) як "не перевірено" — не як провал

---

## 🔧 Фаза 1 — Мінісервіс `kdv-optimizer`

> Передумова: Фаза 0 завершена, рушій `ghostscript_ebook` обрано як основний і єдиний.

---

### Задача 1.1 — Структура директорії та залежності

**Контекст та обмеження:**
Створити каркас нового мінісервісу. `kdv-optimizer` — окремий Flask-застосунок з мінімальними залежностями. Не використовувати спільний `requirements.txt` з `kdv-api`.

**Файли для створення:**
- `kdv-optimizer/` — нова директорія сервісу
- `kdv-optimizer/kdv_optimizer/` — Python package без дефіса
- `kdv-optimizer/requirements.txt`
- `kdv-optimizer/kdv_optimizer/__init__.py` — порожній
- `kdv-optimizer/kdv_optimizer/config.py`

**Детальні інструкції:**

`requirements.txt`:
```
flask==3.0.*
gunicorn==21.*
structlog==24.*
```

`kdv_optimizer/config.py` — клас `OptimizerConfig` зчитує з ENV:
```python
OPTIMIZER_PORT: int = 5001
DATA_DIR: str = "/data/kdv_optimize"
INPUT_DIR: str  # = DATA_DIR + "/input"
OUTPUT_DIR: str  # = DATA_DIR + "/output"
GS_TIMEOUT: int = 120
QPDF_ENABLED: bool = True
TMP_TTL_SECONDS: int = 86400  # 24 години
```

**Критерії прийняття:**
- [x] `from kdv_optimizer.config import OptimizerConfig` працює без помилок
- [x] Всі значення зчитуються з ENV зі sensible defaults

---

### Задача 1.2 — `PDFOptimizerService`: евристика та оптимізація

**Контекст та обмеження:**
Ядро бізнес-логіки оптимізатора. `needs_optimization()` ніколи не кидає виняток назовні. `run_ghostscript()` — top-level функція (не метод), pickle-сумісна. `ProcessPoolExecutor(max_workers=1)` — глобальний синглтон модуля, ініціалізується один раз при імпорті.

**Файли для створення:**
- `kdv-optimizer/kdv_optimizer/services/pdf.py`

**Детальні інструкції:**

```python
# Глобальний pool — ініціалізується один раз
_optimizer_pool = ProcessPoolExecutor(max_workers=1)

def build_job_paths(job_id: str) -> tuple[str, str]:
    """Валідує job_id як UUID, будує безпечні шляхи."""
    safe_id = str(uuid.UUID(job_id))  # ValueError якщо не UUID
    return (
        f"{config.INPUT_DIR}/{safe_id}.pdf",
        f"{config.OUTPUT_DIR}/{safe_id}.pdf",
    )

def needs_optimization(filepath: str, skip: bool) -> bool:
    """Ніколи не кидає виняток. При помилці підрахунку сторінок — True (консервативний fallback)."""

def _count_pages_with_pdfinfo(filepath: str) -> int:
    """subprocess pdfinfo з timeout=10s."""

def _check_disk_space(filepath: str) -> bool:
    """shutil.disk_usage. Потрібно 2.5x від розміру файлу."""

# ===== TOP-LEVEL функції (pickle-сумісні) =====

def run_ghostscript(input_path: str, output_path: str) -> None:
    """nice -n 15, ionice -c 3, gs -dSAFER -dPDFSETTINGS=/ebook, timeout=120."""

def run_qpdf_linearize(input_path: str, output_path: str) -> None:
    """qpdf --linearize."""

# ===== Сервісний клас =====

class PDFOptimizerService:
    def submit_job(self, job_id: str) -> Future:
        """Валідує job_id, перевіряє disk space, submit до _optimizer_pool."""

    def get_job_status(self, job_id: str, future: Future) -> dict:
        """Повертає status: processing|done|error, stats."""
```

Логіка оптимізації (два етапи): `run_ghostscript` → потім `run_qpdf_linearize` якщо `QPDF_ENABLED=true`. Перевірка після кожного етапу: якщо `output_size > input_size` — зупинити, повернути оригінал.

**Критерії прийняття:**
- [x] `needs_optimization()` повертає `True`/`False` при будь-якому вхідному файлі, включно з порожнім та пошкодженим
- [x] `build_job_paths()` кидає `ValueError` при не-UUID рядку
- [x] `run_ghostscript` і `run_qpdf_linearize` — top-level функції, не методи класу
- [x] `ProcessPoolExecutor` ініціалізується рівно один раз на рівні модуля

---

### Задача 1.3 — TTL Janitor (cleanup orphan-файлів)

**Контекст та обмеження:**
`kdv-api` гарантує cleanup через `finally`, але якщо `kdv-api` впав або task thread помер — файли залишаться. `kdv-optimizer` запускає фоновий потік-janitor при старті.

**Файли для створення:**
- `kdv-optimizer/kdv_optimizer/services/janitor.py`

**Детальні інструкції:**

`class TTLJanitor(threading.Thread)`:
- `daemon=True` — не блокує shutdown
- Запускається в `optimizer_app.py` при старті (`janitor.start()`)
- Кожні `TTL_CHECK_INTERVAL_SECONDS` (default: 3600) сканує `INPUT_DIR` та `OUTPUT_DIR`
- Видаляє файли старші за `TMP_TTL_SECONDS` (default: 86400 = 24h)
- Логує кожне видалення як `structlog WARNING` з полями: `file`, `age_s`, `size_mb`
- Startup-cleanup: при ініціалізації одразу видаляє файли старші за TTL (закриває сценарій після рестарту контейнера)

**Критерії прийняття:**
- [x] `TTLJanitor` є `daemon=True` thread — не заважає graceful shutdown
- [x] Startup-cleanup виконується синхронно до `janitor.start()`
- [x] Не видаляє файли, які ще обробляються (вік < TTL)
- [x] Логує кожну операцію видалення

---

### Задача 1.4 — Flask API `optimizer_app.py`

**Контекст та обмеження:**
Мінімальний Flask-застосунок. Три ендпоінти: `POST /optimize`, `GET /optimize/<job_id>`, `GET /health`, `GET /ready`. Стан активних задач зберігається у `dict` (in-memory, достатньо для `max_workers=1`). `POST /optimize` повертає `202` і не блокується — фактична робота у process pool.

**Файли для створення:**
- `kdv-optimizer/optimizer_app.py`

**Детальні інструкції:**

```python
# In-memory store: {job_id: {"future": Future, "submitted_at": float, "input_path": str}}
_jobs: dict[str, dict] = {}

@app.post("/optimize")
def start_optimize():
    """
    Body: {"job_id": "<uuid>"}
    Валідує job_id через build_job_paths() — ValueError → 400.
    Перевіряє що input файл існує — 404 якщо ні.
    Submit до ProcessPoolExecutor.
    Повертає 202 {"job_id": ..., "status": "processing"}.
    """

@app.get("/optimize/<job_id>")
def get_optimize_status(job_id):
    """
    Повертає {"status": "processing|done|error", "output_path": ..., "stats": {...}}
    При done: перевіряє що output існує і не порожній і не більший за input.
    При larger/empty: status="error", reason="larger_output"|"empty_output".
    """

@app.get("/health")
def health():
    """200 {"status": "ok"} завжди, якщо Flask живий."""

@app.get("/ready")
def ready():
    """
    200 якщо: input/output dirs існують і writeable, 
              ghostscript/qpdf/pdfinfo доступні (subprocess --version).
    503 {"status": "not_ready", "reason": [...]} якщо ні.
    """
```

**Критерії прийняття:**
- [x] `POST /optimize` з невалідним UUID → `400`
- [x] `POST /optimize` з неіснуючим input файлом → `404`
- [x] `GET /optimize/<id>` для невідомого job_id → `404`
- [x] `GET /ready` → `503` якщо `gs` не встановлений
- [x] `GET /health` → `200` завжди (не залежить від стану залежностей)

---

### Задача 1.5 — Dockerfile для `kdv-optimizer`

**Контекст та обмеження:**
Образ не повинен запускатися як `root`. Ghostscript  — pinned версії. Trivy має проходити без CRITICAL.

**Файли для створення:**
- `kdv-optimizer/Dockerfile`

**Детальні інструкції:**

```dockerfile
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    ghostscript=10.02.1~dfsg-1 \
    poppler-utils=22.12.0-2+b1 \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash optimizer
RUN mkdir -p /data/kdv_optimize/input /data/kdv_optimize/output \
    && chown -R optimizer:optimizer /data/kdv_optimize
USER optimizer
WORKDIR /app

COPY --chown=optimizer:optimizer requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=optimizer:optimizer . .

EXPOSE 5001
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:5001", "--timeout", "150", "optimizer_app:app"]
```

**Критерії прийняття:**
- [x] `docker build` завершується без помилок
- [x] `docker run` → `GET /ready` повертає `200`
- [x] Процес всередині контейнера не `root` (`whoami` → `optimizer`)
- [x] `gs --version` виводить очікувану версію

---

## 🔗 Фаза 2 — Зміни в `kdv-api`

---

### Задача 2.1 — `src/services/pdf.py`: sync HTTP-клієнт `PDFOptimizerClient`

**Контекст та обмеження:**
`kdv-api` — синхронний Flask/Gunicorn застосунок. Тому `PDFOptimizerClient` використовує `requests` або `httpx.Client` (sync), **без** `asyncio`/`await`. Клієнт реалізує повний fallback-ланцюжок: будь-яка помилка → логується → повертається шлях до оригіналу. Архівація ніколи не переривається через помилку оптимізатора.

**Файли для створення/зміни:**
- `src/services/pdf.py` — новий файл

**Детальні інструкції:**

```python
class PDFOptimizerClient:
    def __init__(self, base_url: str, timeout: int = 130): ...

    def optimize(self, original_path: str, job_id: str) -> OptimizeResult:
        """
        1. POST /optimize {"job_id": job_id}
        2. Polling GET /optimize/{job_id} кожні 2s, до 130s
        3. При status=done: валідує output_path, повертає OptimizeResult(success=True, path=output_path, stats=...)
        4. При будь-якій помилці: повертає OptimizeResult(success=False, fallback_reason=..., path=original_path)
        5. НІКОЛИ не кидає виняток назовні
        """
```

`OptimizeResult` — dataclass:
```python
@dataclass
class OptimizeResult:
    success: bool
    path: str                        # output_path або original_path при fallback
    fallback_reason: str | None      # timeout|larger_output|empty_output|optimizer_unavailable|exception
    original_mb: float | None = None
    optimized_mb: float | None = None
    optimization_time_ms: int | None = None
    thread_wait_ms: int | None = None
```

Логіка polling:
```python
start = time.monotonic()
while (elapsed := time.monotonic() - start) < self.timeout:
    resp = self._client.get(f"{self.base_url}/optimize/{job_id}")
    data = resp.json()
    if data["status"] == "done":
        return self._validate_and_build_result(data, original_path)
    if data["status"] == "error":
        return OptimizeResult(success=False, fallback_reason=data.get("reason", "exception"), path=original_path)
    time.sleep(2)
return OptimizeResult(success=False, fallback_reason="timeout", path=original_path)
```

**Критерії прийняття:**
- [x] При `ConnectionRefusedError` (optimizer недоступний): `fallback_reason="optimizer_unavailable"`, виняток не поширюється
- [x] При `timeout` polling: `fallback_reason="timeout"`
- [x] При `status="error"` від optimizer: `fallback_reason` береться з відповіді
- [x] `OptimizeResult.path` завжди валідний шлях — або output, або оригінал

---

### Задача 2.2 — `src/app.py`: оновлення схеми запиту

**Контекст та обмеження:**
Backward-compatible зміна. Старі клієнти (`robot.py` до оновлення, Koha без чекбокса) не передають JSON body — мають продовжувати працювати з `skip_optimization=false` за замовчуванням.

**Файли для зміни:**
- `src/app.py`

**Детальні інструкції:**

Знайти існуючу Pydantic або Flask схему для `/integrate` ендпоінту. Додати поле:

```python
class IntegrateRequest(BaseModel):
    # ... існуючі поля ...
    skip_optimization: bool = False   # default = оптимізація увімкнена
```

При `Content-Type` відсутній або body порожній → `skip_optimization=False` (не 400).

**Критерії прийняття:**
- [x] `POST /integrate` без body → `skip_optimization=False`, 200
- [x] `POST /integrate` з `{"skip_optimization": true}` → `skip_optimization=True`
- [x] Існуючі тести `test_app.py` не зламані
- [x] Новий тест `test_integrate_without_payload_defaults_to_optimization` проходить

---

### Задача 2.3 — `src/core.py`: інтеграція `PDFOptimizerClient`

**Контекст та обмеження:**
Оркестратор (`core.py`) — місце де PDF-оптимізація вбудовується в існуючий workflow між "завантаження PDF з Koha" та "upload до DSpace". `finally` блок гарантує cleanup незалежно від результату. Telemetry-поля (`pdf_optimized`, `pdf_fallback_reason` тощо) потрапляють у `task.result`.

**Файли для зміни:**
- `src/core.py`

**Детальні інструкції:**

Знайти в `core.py` місце де відбувається `local_dspace.upload_to_item(item_uuid, file_path)`. Обернути вибір `final_pdf_path` перед цим викликом і передати в DSpace саме оптимізований або fallback-шлях.

Також змінити сигнатури:
- `process_integration_logic(..., skip_optimization: bool = False, ...)`
- `run_dspace_workflow(..., skip_optimization: bool = False, ...)`

```python
import uuid, shutil, contextlib, os
from .services.pdf import PDFOptimizerClient, needs_optimization

optimizer_client = PDFOptimizerClient(
    base_url=os.environ["OPTIMIZER_URL"],
    timeout=int(os.environ.get("OPTIMIZER_TIMEOUT", 130)),
)

# --- Вставити перед upload_to_item ---
job_id = str(uuid.uuid4())
input_tmp = f"/data/kdv_optimize/input/{job_id}.pdf"
output_tmp = f"/data/kdv_optimize/output/{job_id}.pdf"

pdf_telemetry = {
    "pdf_optimized": "false",
    "pdf_fallback_reason": None,
    "pdf_original_mb": round(os.path.getsize(pdf_path) / 1024 / 1024, 2),
    "pdf_final_mb": None,
    "pdf_pages": None,
    "pdf_optimization_time_ms": None,
    "pdf_thread_wait_ms": None,
    "pdf_disk_free_mb": None,
}

final_pdf_path = pdf_path  # default — оригінал

try:
    if needs_optimization(pdf_path, skip=skip_optimization):
        shutil.copy(pdf_path, input_tmp)
        result = optimizer_client.optimize(pdf_path, job_id)
        final_pdf_path = result.path
        pdf_telemetry.update({
            "pdf_optimized": "true" if result.success else "false",
            "pdf_fallback_reason": result.fallback_reason,
            "pdf_final_mb": round(os.path.getsize(final_pdf_path) / 1024 / 1024, 2),
            "pdf_optimization_time_ms": result.optimization_time_ms,
            "pdf_thread_wait_ms": result.thread_wait_ms,
        })
    elif skip_optimization:
        pdf_telemetry["pdf_optimized"] = "skipped_by_user"

    local_dspace.upload_to_item(item_uuid, final_pdf_path)

finally:
    for path in (input_tmp, output_tmp):
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)
```

`pdf_telemetry` додається до `task.result` перед поверненням.

**Критерії прийняття:**
- [x] При падінні `local_dspace.upload_to_item()` — tmp файли все одно видаляються
- [x] При падінні `optimizer_client.optimize()` — `finally` спрацьовує, оригінал завантажується
- [x] `task.result` містить всі `pdf_*` поля після успішної архівації
- [x] `skip_optimization=true` → `pdf_optimized="skipped_by_user"` у результаті

---

## 🖥 Фаза 3 — Оновлення `IntranetUserJS.js` (Koha UI)

---

### Задача 3.1 — Чекбокс "Не оптимізовувати файл"

**Контекст та обмеження:**
Зміна лише у JavaScript, без зміни серверного Koha-коду. Чекбокс додається динамічно поруч з кнопкою "Архівувати DSpace". За замовчуванням — не активний (оптимізація увімкнена).

**Файли для зміни:**
- `/opt/Koha/koha-deploy/IntranetUserJS.js`

**Детальні інструкції:**

Знайти місце де формується `fetch` або `XMLHttpRequest` до `/integrate`. Перед кнопкою "Архівувати DSpace" вставити:

```javascript
const skipCheckbox = document.createElement('input');
skipCheckbox.type = 'checkbox';
skipCheckbox.id = 'kdv-skip-optimization';
skipCheckbox.name = 'skip_optimization';

const skipLabel = document.createElement('label');
skipLabel.htmlFor = 'kdv-skip-optimization';
skipLabel.textContent = ' Не оптимізовувати файл (завантажити оригінал)';

// Вставити перед кнопкою архівації
archiveButton.parentNode.insertBefore(skipCheckbox, archiveButton);
archiveButton.parentNode.insertBefore(skipLabel, archiveButton);
```

Модифікувати payload `fetch`:
```javascript
const payload = {
    // ... існуючі поля ...
    skip_optimization: document.getElementById('kdv-skip-optimization')?.checked ?? false,
};
```

**Критерії прийняття:**
- [x] Чекбокс відображається поруч з кнопкою архівації
- [x] За замовчуванням не активний
- [x] При активному чекбоксі: `skip_optimization: true` у payload
- [x] При неактивному: `skip_optimization: false`
- [x] Якщо чекбокс відсутній у DOM (failsafe): `?? false` запобігає помилці

---

## 🤖 Фаза 4 — Оновлення `scripts/robot.py`

---

### Задача 4.1 — CLI через `argparse` + `--skip-optimization`

**Контекст та обмеження:**
`robot.py` викликає API напряму без UI. Потребує `argparse` для нормалізованого CLI. ENV-змінні (`ROBOT_PARALLELISM`, `ROBOT_MAX_WAIT`) залишаються як backward-compatible fallback.

**Файли для зміни:**
- `scripts/robot.py`

**Детальні інструкції:**

Замінити ручний парсинг аргументів на `argparse`:

```python
parser = argparse.ArgumentParser(description="KDV Integrator batch robot")
parser.add_argument("candidates_file", nargs="?", default="candidates.txt")
parser.add_argument("--skip-optimization", action="store_true", default=False,
                    help="Вимкнути PDF-оптимізацію для всього батчу")
parser.add_argument("--parallelism", type=int,
                    default=int(os.environ.get("ROBOT_PARALLELISM", 1)))
parser.add_argument("--max-wait", type=int,
                    default=int(os.environ.get("ROBOT_MAX_WAIT", 900)))
args = parser.parse_args()
```

Canonical запуск після зміни:

```bash
python3 scripts/robot.py candidates.txt --skip-optimization
```

Окремо перевірити і за потреби оновити runbook-и, які зараз можуть посилатися на `python3 -m src.robot`.

Додати `skip_optimization` у payload кожного запиту:
```python
payload = {
    # ... існуючі поля ...
    "skip_optimization": args.skip_optimization,
}
```

Додати попередження в stdout якщо `args.parallelism > 1` і `not args.skip_optimization`:
```
⚠ ROBOT_PARALLELISM > 1 з увімкненою оптимізацією: задачі чекатимуть чергу optimizer.
  Рекомендовано: --parallelism 1 або --skip-optimization
  Поточний max-wait: {args.max_wait}s. При паралелізмі 2 рекомендовано --max-wait 1200
```

**Критерії прийняття:**
- [x] `python3 scripts/robot.py --help` виводить опис всіх аргументів
- [x] `python3 scripts/robot.py candidates.txt --skip-optimization` → `skip_optimization: true` у кожному запиті
- [x] `python3 scripts/robot.py candidates.txt` (без прапора) → `skip_optimization: false`
- [x] ENV `ROBOT_PARALLELISM=2` без `--parallelism` аргументу → parallelism=2
- [x] При `--parallelism 2` без `--skip-optimization` → попередження у stdout

---

## 🐳 Фаза 5 — Docker Compose та Deploy

---

### Задача 5.1 — Оновлення `docker-compose.yml`

**Контекст та обмеження:**
Додати `kdv-optimizer` як новий сервіс. Shared volume монтується в обидва контейнери. `kdv-optimizer` не публікує порти назовні — лише внутрішня мережа Docker. Ресурсні ліміти `kdv-api` знижуються (CPU-bound задачі тепер у optimizer).

**Файли для зміни:**
- `docker-compose.yml`
- `docker-compose.swarm.yml`
- `.env.example`

**Детальні інструкції:**

```yaml
services:
  kdv-api:
    image: ghcr.io/pinokew/kdv-integrator-event:${KDV_IMAGE_VERSION}
    volumes:
      - kdv_optimize_data:/data/kdv_optimize
    environment:
      - OPTIMIZER_URL=http://kdv-optimizer:5001
      - OPTIMIZER_TIMEOUT=130
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 1G
        reservations:
          cpus: '0.25'
          memory: 256M

  kdv-optimizer:
    image: ghcr.io/pinokew/kdv-optimizer:${KDV_OPTIMIZER_VERSION}
    volumes:
      - kdv_optimize_data:/data/kdv_optimize
    ports: []
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
        reservations:
          cpus: '0.5'
          memory: 512M

volumes:
  kdv_optimize_data:
    driver: local
```

Також додати `KDV_OPTIMIZER_VERSION` у `.env.example` з коментарем.

> Важливо: зміна має бути merge-update, а не переписування compose з нуля. Зберегти існуючі `kdv-drive`, `proxy-net`, Traefik labels, optional `.env`, Swarm override та поточний env/deploy contract.

**Критерії прийняття:**
- [x] `docker compose config` не має помилок
- [x] `docker compose up` поднімає обидва сервіси
- [x] `kdv-optimizer` НЕ доступний з хоста (порт не опублікований)
- [x] `kdv-api` → `http://kdv-optimizer:5001/health` → `200` (через docker network)
- [x] Shared volume монтується в обидва контейнери

---

### Задача 5.2 — Оновлення `deploy-orchestrator-swarm.sh`

**Контекст та обмеження:**
Існуючий orchestrator-скрипт збирає і деплоїть `kdv-api`. Тепер має також збирати, тегувати і деплоїти `kdv-optimizer`. Post-deploy verification має перевіряти обидва сервіси. Rollback — без видалення DSpace/Koha даних.

**Файли для зміни:**
- `scripts/deploy-orchestrator-swarm.sh`

**Детальні інструкції:**

1. **Build section** — додати після build `kdv-api` з урахуванням поточного `ORCHESTRATOR_IMAGE_MODE=local`:
```bash
echo "→ Building kdv-optimizer..."
docker build -t kdv-optimizer:${GIT_SHA} ./kdv-optimizer
export KDV_OPTIMIZER_IMAGE="kdv-optimizer:${GIT_SHA}"
```

2. **Render manifest** — додати `KDV_OPTIMIZER_IMAGE=${KDV_OPTIMIZER_IMAGE}` або `KDV_OPTIMIZER_VERSION=${GIT_SHA}` у rendered env так, щоб `docker-compose.yml`/`docker-compose.swarm.yml` отримали локальний image tag. Для registry mode лишити можливість використати `ghcr.io/...`.

3. **Post-deploy verification** — розширити:
```bash
verify_service() {
    local SERVICE=$1
    local RETRIES=10
    for i in $(seq 1 $RETRIES); do
        RUNNING=$(docker service ls --filter name="${STACK_NAME}_${SERVICE}" --format "{{.Replicas}}")
        if [[ "$RUNNING" == "1/1" ]]; then
            echo "✓ ${SERVICE} is healthy"
            return 0
        fi
        echo "  Waiting for ${SERVICE}... ($i/$RETRIES)"
        sleep 6
    done
    echo "✗ ${SERVICE} failed to start" >&2
    return 1
}

verify_service kdv-api
verify_service kdv-optimizer
```

4. **Rollback section** — додати примітку:
```bash
# ROLLBACK ІНСТРУКЦІЯ:
# 1. Вимкнути оптимізацію без відкату коду:
#    docker service update --env-add OPTIMIZER_URL=disabled ${STACK_NAME}_kdv-api
# 2. Або відкотити compose + передеплоїти попередній GIT_SHA
# 3. DSpace/Koha дані не зачіпаються при будь-якому варіанті
```

**Критерії прийняття:**
- [x] Скрипт збирає обидва локальні образи з однаковим `GIT_SHA` тегом у `ORCHESTRATOR_IMAGE_MODE=local`
- [x] Registry mode не зламаний і може використовувати GHCR image за явним перемиканням
- [x] `verify_service kdv-optimizer` перевіряє `1/1` репліки
- [x] При провалі `verify_service` — скрипт завершується з `exit 1`
- [x] Rollback-інструкція присутня як коментар

---

## 🔒 Фаза 6 — Security та CI/CD

---

### Задача 6.1 — Trivy scan для `kdv-optimizer` у CI/CD

**Контекст та обмеження:**
Ghostscript має активну CVE-активність. Trivy scan має блокувати merge при CRITICAL вразливостях. Додається в існуючий workflow `.github/workflows/main.yml` або в reusable workflow, якщо поточний `main.yml` лише делегує CI/CD у shared pipeline.

**Файли для зміни:**
- `.github/workflows/main.yml`

**Детальні інструкції:**

Додати новий job або відповідний reusable workflow input після `build-kdv-api`:

```yaml
build-and-scan-kdv-optimizer:
  runs-on: ubuntu-latest
  needs: []  # Незалежний від kdv-api build
  steps:
    - uses: actions/checkout@v4

    - name: Build kdv-optimizer image
      run: docker build -t kdv-optimizer:${{ github.sha }} ./kdv-optimizer

    - name: Trivy scan — CRITICAL/HIGH block
      uses: aquasecurity/trivy-action@master
      with:
        image-ref: kdv-optimizer:${{ github.sha }}
        severity: HIGH,CRITICAL
        exit-code: 1
        format: table

    - name: Push to GHCR
      if: github.ref == 'refs/heads/main'
      run: |
        echo ${{ secrets.GITHUB_TOKEN }} | docker login ghcr.io -u ${{ github.actor }} --password-stdin
        docker tag kdv-optimizer:${{ github.sha }} ghcr.io/pinokew/kdv-optimizer:${{ github.sha }}
        docker push ghcr.io/pinokew/kdv-optimizer:${{ github.sha }}
```

**Критерії прийняття:**
- [ ] PR з CRITICAL CVE у `kdv-optimizer` image → CI fails, merge заблоковано
- [ ] PR без CRITICAL → CI passes
- [ ] `kdv-optimizer` image пушиться до GHCR тільки з `main` гілки

---

## 🧪 Фаза 7 — Тести

---

### Задача 7.1 — Тести для `PDFOptimizerService` (`test_services.py`)

**Контекст та обмеження:**
Юніт-тести для бізнес-логіки. Використовувати `pytest` + `unittest.mock`. Не запускати реальний Ghostscript — мокати `subprocess.run`.

**Файли для зміни:**
- `tests/test_services.py`

**Детальні інструкції:**

Реалізувати 5 тест-кейсів:

```python
def test_optimizer_needs_optimization_threshold():
    """Умова A: >50MB AND >500KB/стор. | Умова B: >100MB | skip=True → False"""
    # Граничні значення: 49MB → False, 51MB + 600KB/стор → True, 101MB → True

def test_optimizer_pdfinfo_crash_fallback():
    """pdfinfo timeout/виняток при файлі 60MB → needs_optimization() повертає True (консерват.)"""
    # mock subprocess.run → raise subprocess.TimeoutExpired
    # Перевірити: функція НЕ кидає виняток, повертає True

def test_optimizer_disk_preflight_fail():
    """Недостатньо місця на диску → skipped_no_disk, без виключення"""
    # mock shutil.disk_usage → повертає мало free
    # Перевірити: OptimizeResult.success=False, fallback_reason=None, path=original

def test_optimizer_larger_output_fallback():
    """Після GS: output > input → повертає оригінал"""
    # mock run_ghostscript → створює більший файл
    # Перевірити: result.path == original_path, result.fallback_reason == "larger_output"

def test_optimizer_client_unavailable():
    """kdv-optimizer HTTP 503 → fallback, pipeline не зупиняється"""
    # mock requests.post → raise ConnectionError
    # Перевірити: OptimizeResult.success=False, fallback_reason="optimizer_unavailable"
    # Перевірити: жоден виняток не поширюється назовні
```

**Критерії прийняття:**
- [ ] Всі 5 тестів проходять
- [ ] `needs_optimization()` не кидає виняток ні в одному сценарії

---

### Задача 7.2 — Тести для `core.py` (`test_core.py`)

**Контекст та обмеження:**
Інтеграційні тести оркестратора. Перевіряють що `finally` блок чистить файли при будь-якому сценарії.

**Файли для зміни:**
- `tests/test_core.py`

**Детальні інструкції:**

Реалізувати 4 тест-кейси:

```python
def test_core_cleanup_on_exception():
    """finally видаляє tmp файли навіть якщо optimizer.optimize() кидає виняток"""

def test_core_cleanup_on_dspace_exception():
    """finally видаляє tmp файли навіть якщо dspace upload_to_item() кидає виняток"""

def test_optimizer_fallback_does_not_fail_archive():
    """Fallback оптимізації не переводить задачу архівації в статус error"""
    # mock optimizer → повертає OptimizeResult(success=False, fallback_reason="timeout")
    # mock dspace → успішно завантажує оригінал
    # Перевірити: task.status == "success", task.result["pdf_fallback_reason"] == "timeout"

def test_hard_limit_does_not_prevent_optimization_path():
    """Поточний LIMIT_ERROR задокументований і не змінюється цією ітерацією"""
    # Перевірити, що поведінка hard limit лишається сумісною з поточним core.py
    # Якщо після PoC буде рішення змінити порядок limit/optimization — оновити окремою задачею.
```

**Критерії прийняття:**
- [ ] Всі 4 тести проходять
- [ ] `test_core_cleanup_on_dspace_exception` — перевіряє що `os.remove` викликаний для обох tmp файлів

---

### Задача 7.3 — Тести для `app.py` та `robot.py`

**Файли для зміни:**
- `tests/test_app.py`
- `tests/test_scripts.py`

**Детальні інструкції:**

```python
# test_app.py
def test_integrate_without_payload_defaults_to_optimization():
    """POST /integrate без body → skip_optimization=False, не 400"""
    response = client.post("/kdv/api/integrate/123")  # без JSON body
    assert response.status_code != 400
    # Перевірити що обробник отримав skip_optimization=False

# test_scripts.py
def test_robot_skip_optimization_flag():
    """--skip-optimization прапор передає skip_optimization: true в payload"""
    # mock requests.post, перехопити payload
    # subprocess/argparse парсинг: ["candidates.txt", "--skip-optimization"]
    # Перевірити: payload["skip_optimization"] == True
```

**Критерії прийняття:**
- [ ] Обидва тести проходять
- [ ] Усі існуючі тести + нові optimizer-тести проходять
- [ ] `pytest` завершується з exit code 0

---

## ✅ Definition of Done для всього Milestone 8

Milestone 8 вважається завершеним коли виконані **всі** наступні умови:

**Фаза 0 (Gate):**
- [ ] `scripts/benchmark_results/` містить JSON для всіх 4 рушіїв × 5 файлів
- [ ] Для scan-like файлів: `reduction_pct >= 50` і `quality_ok: true` (заповнено вручну)
- [ ] `validate_poc_results.py` завершується з `exit 0`

**Код:**
- [ ] `kdv-optimizer/` містить повний мінісервіс з Dockerfile
- [ ] `src/services/pdf.py` — `PDFOptimizerClient` з повним fallback
- [x] `src/core.py` — інтеграція з `finally` cleanup і telemetry у `task.result`
- [ ] `src/app.py` — `skip_optimization: bool = False` (backward-compatible)
- [x] `/opt/Koha/koha-deploy/IntranetUserJS.js` — чекбокс з правильним default
- [x] `scripts/robot.py` — `argparse` + `--skip-optimization` + попередження при parallelism > 1

**Інфраструктура:**
- [ ] `docker-compose.yml` — два сервіси + shared volume + resource limits
- [ ] `deploy-orchestrator-swarm.sh` — build обох образів + verify обох сервісів
- [ ] `.github/workflows/main.yml` або shared workflow — Trivy scan `kdv-optimizer`, blocks on CRITICAL CVE

**Якість:**
- [ ] Усі тести проходять (`pytest -v`)
- [ ] `docker compose up` → обидва сервіси healthy
- [ ] `GET /ready` на `kdv-optimizer` → `200`
- [ ] `POST /integrate` зі старим клієнтом (без body) → `200`, оптимізація увімкнена

**SLO baseline (перевірити після першого production деплою):**
- [ ] P95 Integration Time без оптимізації: ≤ 60s
- [ ] P95 Integration Time з оптимізацією: ≤ 240s
- [ ] Optimizer fallback rate: ≤ 10%
