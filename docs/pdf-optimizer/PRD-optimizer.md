# 📄 PRD: Модуль автоматичної оптимізації PDF (v0.4.0)

**Документ статус:** Draft → In Review  
**Автор:** DevOps / Arch Team  
**Цільовий реліз:** KDV Integrator v0.4.0 (Milestone 8)  
**Версія документу:** 0.4.0-rev3  
**Changelog документу:**
- rev1 → Initial draft
- rev2 → Архітектурний рев'ю: додано `kdv-optimizer` container, ProcessPoolExecutor, виправлено thread starvation, robot.py, disk pre-flight, SLO split, CVE policy, PoC benchmark format
- rev3 → Уточнено sync HTTP-клієнт, безпечний `job_id` контракт, fallback telemetry у `task.result`, `pdfinfo` евристику, TTL cleanup, health/readiness optimizer, orchestrator/deploy вимоги та release gate для PoC

---

## 1. Контекст та Проблема

При використанні потокових сканерів (зокрема ScanSnap) бібліотекарі генерують PDF-файли невиправдано великого розміру (некомпресований растр, 600+ DPI). Завантаження таких файлів у DSpace 7/8 призводить до:

1. Швидкого вичерпання дискового простору репозиторію.
2. Деградації UX для кінцевих користувачів (читачів), які завантажують ці файли (особливо з мобільних пристроїв).
3. Збільшення часу бекапування сховища DSpace.

**Рішення:** Впровадити в KDV Integrator опціональний, але активний за замовчуванням модуль "Shift-Left" оптимізації PDF перед завантаженням бітстріма в DSpace. Оптимізація виконується в **ізольованому** сервісі `kdv-optimizer`, що захищає основний API від деградації.

---

## 2. Користувацький досвід (UX / User Story)

**User Story:** Як бібліотекар (оператор Koha), я хочу, щоб система автоматично зменшувала розмір важких сканів при архівації, але залишала мені можливість примусово завантажити оригінал для рідкісних історичних документів, де важливий кожен піксель.

**Інтерфейс (Koha IntranetUserJS.js):**

- Біля кнопки "Архівувати DSpace" додається чекбокс: `[ ] Не оптимізовувати файл (завантажити оригінал)`.
- За замовчуванням чекбокс **не активний** (тобто оптимізація увімкнена).
- При натисканні "Архівувати" JS-скрипт зчитує стан чекбокса і передає в API параметр: `{"skip_optimization": false/true}`.

---

## 3. Архітектура: `kdv-optimizer` як окремий контейнер

### 3.1. Обґрунтування рішення

Ghostscript — CPU-bound процес, що може тривати до 120 секунд і споживати до 2GB RAM. Запуск у межах `kdv-api` (1 worker × 4 threads) створює наступні ризики:

| Ризик | При запуску в `kdv-api` | При окремому `kdv-optimizer` |
|---|---|---|
| **Thread starvation** | 2 паралельних файли → 50% threads заблоковано на 120s | Threads `kdv-api` не блокуються взагалі |
| **OOM Killer** | Ghostscript убиває Flask процес | OOM зачіпає лише `kdv-optimizer` |
| **Ресурсні ліміти** | `memory: 2G` ділять Flask + Ghostscript | Хірургічні ліміти: Flask окремо, Ghostscript окремо |
| **Розмір образу** | `ghostscript` + `poppler-utils` в API образі | `kdv-api` image без системних PDF-інструментів |
| **Незалежний рестарт** | Неможливий без downtime API | `docker compose restart kdv-optimizer` без впливу на API |

**Висновок:** Окремий контейнер виправданий. Ускладнення мінімальне (одна нова services секція в docker-compose + shared volume), але ізоляція — повна.

### 3.2. Топологія взаємодії

```
kdv-api (Flask, 1w×4t)          kdv-optimizer (Flask mini-API)
┌──────────────────────┐        ┌────────────────────────────────┐
│  core.py             │  HTTP  │  optimizer_app.py              │
│  PDFOptimizerClient  │───────▶│  POST /optimize                │
│  (HTTP клієнт)       │        │  GET  /optimize/{job_id}       │
│                      │        │                                │
│  /data/kdv_optimize/ │◀──────▶│  /data/kdv_optimize/           │
│  (shared volume)     │        │  ProcessPoolExecutor(1)        │
└──────────────────────┘        │  Ghostscript subprocess        │
                                └────────────────────────────────┘
```

**Shared volume:** `/data/kdv_optimize/` — монтується в обидва контейнери. Містить:
- `input/{job_id}.pdf` — оригінальний файл
- `output/{job_id}.pdf` — результат оптимізації
- Обидві директорії чистяться після завершення транзакції (div. секцію 5.4)

**Комунікація:**
- `kdv-api` копіює файл у `/data/kdv_optimize/input/{job_id}.pdf`
- `kdv-api` → `POST /optimize` з `{ "job_id": "..." }`
- `kdv-api` → `GET /optimize/{job_id}` (polling з таймаутом 120s, інтервал 2s)
- `kdv-optimizer` повертає `{ "status": "done|processing|error", "output_path": "...", "stats": { "original_mb": ..., "optimized_mb": ..., "time_ms": ... } }`
- `kdv-api` → `GET /health` і `GET /ready` для діагностики `kdv-optimizer`

> **Безпечний path contract:** `kdv-api` не передає довільний `input_path`. `kdv-optimizer` приймає тільки `job_id`, валідує його як UUID/allowlisted token і сам будує `input/output` шляхи всередині `/data/kdv_optimize`. Це прибирає ризик випадкового читання/запису поза shared volume.

> **Примітка щодо polling vs callback:** Polling обрано свідомо — він відповідає існуючому паттерну `TaskManager` в `kdv-api` і не вимагає callback URL або черги повідомлень. За необхідності в майбутньому можна замінити на webhook без зміни контракту.

> **Sync-контракт для `kdv-api`:** Поточний інтегратор працює як синхронний Flask/Gunicorn застосунок із background threads. Тому `src/services/pdf.py` реалізується як простий sync HTTP client через `requests` або `httpx.Client`, без переведення `kdv-api` на async/await.

### 3.3. ProcessPoolExecutor у `kdv-optimizer`

У `kdv-optimizer` Ghostscript запускається через `ProcessPoolExecutor(max_workers=1)`, що гарантує:
- Максимум один Ghostscript-процес активний одночасно (захист від перевантаження хоста при паралельних запитах)
- HTTP endpoint швидко ставить job у внутрішній стан `processing`, а фактична CPU-bound робота виконується в окремому process pool worker
- GIL не бере участі — справжня CPU-ізоляція

```python
# src/services/pdf.py (всередині kdv-optimizer)
from concurrent.futures import ProcessPoolExecutor
import uuid

_optimizer_pool = ProcessPoolExecutor(max_workers=1)

def build_job_paths(job_id: str) -> tuple[str, str]:
    safe_job_id = str(uuid.UUID(job_id))
    return (
        f"/data/kdv_optimize/input/{safe_job_id}.pdf",
        f"/data/kdv_optimize/output/{safe_job_id}.pdf",
    )

future = _optimizer_pool.submit(
    run_ghostscript,   # top-level функція (pickle-сумісна)
    input_path,
    output_path,
)
```

> **Важливо:** `run_ghostscript` має бути top-level функцією (не методом класу), оскільки `ProcessPoolExecutor` серіалізує аргументи через `pickle`.

---

## 4. Фаза R&D (Proof of Concept)

Перед імплементацією production-коду необхідно створити ізольований скрипт `scripts/poc_optimizer.py` для тестування рушіїв.

> **Release gate:** Без benchmark JSON для еталонного датасету і ручного `quality_ok: true` для основних scan-like файлів production-імплементація не починається. PoC є обов'язковим gate для Milestone 8, а не optional-дослідженням.

### 4.1. Кандидати на тестування

| Рушій | Тип | Найкращий сценарій | Примітка |
|---|---|---|---|
| **Ghostscript** (CLI) | Растр + перекодування | Важкі скани ScanSnap | Фаворит. CVE-ризик — потребує pinned версії |
| **PyMuPDF** (fitz) | Stream compression | Змішані PDF | Дослідити в PoC, але не робити default на старті |
| **pikepdf** (libqpdf) | Stream compression | Текстові PDF | Дослідити в PoC, але практична цінність нижча через scan-heavy корпус |
| **qpdf** (CLI) | Лінеаризація | Будь-який PDF | Додатково після GS: "fast web view", -5..15% без деградації |

**Рекомендована стартова стратегія (перевірити в PoC):**
1. Основний production path: scan-like PDF → `Ghostscript` → потім `qpdf --linearize`
2. Text-like оптимізатори (`pikepdf`, PyMuPDF) залишити як PoC-кандидати, але не ускладнювати першу production-версію, якщо датасет підтвердить, що майже всі документи — відскановані книги.

Детектор типу: якщо `size / pages > 500KB/стор.` — scan-like; інакше — text-like/вже стиснутий.

### 4.2. Тестовий датасет (5 еталонних файлів)

1. Важкий скан ScanSnap (100MB+, 50 сторінок) — основний сценарій
2. Звичайний текстовий PDF (згенерований з Word, 2MB) — перевірка pikepdf/qpdf
3. Кольоровий скан з фотографіями (50MB) — перевірка балансу якість/стиснення
4. Вже оптимізований файл — перевірка що рушій не збільшує розмір
5. Пошкоджений PDF — перевірка обробки винятків на всіх рівнях pipeline

### 4.3. Структурований benchmark output

PoC скрипт генерує JSON-звіт для кожної пари (рушій × файл):

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

Поле `quality_ok` заповнюється вручну після візуального контролю читабельності. Порівняння результатів: `cat results/*.json | jq -s 'sort_by(.reduction_pct) | reverse'`.

### 4.4. Критерії успіху R&D

- Зменшення розміру важкого скану мінімум на **50%**
- Читабельність тексту збережена (візуальний контроль, `quality_ok: true`)
- Споживання RAM не перевищує **500MB** на один файл (`peak_ram_mb`)
- Час обробки не перевищує **120s** (`time_s < 120`)
- Для вже оптимізованого файлу: `output_larger == false`

---

## 5. Функціональні вимоги (Business Logic)

Процес втручається в існуючий DSpace Workflow після завантаження PDF з Koha і перед пушем у DSpace.

> **Поточний `LIMIT_ERROR`:** Існуючий hard limit у `core.py` на цьому етапі не змінюємо в межах PRD-правки. Рішення про підняття або перенесення порогу приймається окремо після PoC benchmark і перевірки реальних розмірів scan-like файлів.

### 5.1. Евристика (Тригери оптимізації)

Оптимізація запускається ТІЛЬКИ якщо виконується **умова A або B**, та **умова C**:

- **Умова A:** Розмір файлу **> 50MB** AND питома вага **> 500KB/сторінку** (важкий скан)
- **Умова B:** Розмір файлу **> 100MB** (незалежно від кількості сторінок — надважкий файл)
- **Умова C:** `skip_optimization == False`

> **Обґрунтування Умови B:** Файл 60MB / 200 сторінок = 300KB/стор. — нижче порогу Умови A, тому без Умови B не оптимізується. Але 60MB текстового PDF вже стиснутий і не потребує обробки GS. Умова B ловить справді аномальні випадки (наприклад, 120MB текстовий PDF без стиснення).

**Підрахунок сторінок — обробка помилок:**

```python
import subprocess

def _count_pages_with_pdfinfo(filepath: str) -> int:
    result = subprocess.run(
        ["pdfinfo", filepath],
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise ValueError("pdfinfo_pages_missing")

def needs_optimization(filepath: str, skip: bool) -> bool:
    if skip:
        return False
    size_mb = os.path.getsize(filepath) / 1024 / 1024
    if size_mb > 100:
        return True                      # Умова B — не потребує підрахунку сторінок
    if size_mb <= 50:
        return False
    try:
        pages = _count_pages_with_pdfinfo(filepath)
        return (size_mb * 1024) / pages > 500   # Умова A
    except Exception as e:
        logger.warning("pdf_page_count_failed", error=str(e), filepath=filepath)
        return True   # Консервативний fallback: файл > 50MB → краще оптимізувати
```

> **Критично:** підрахунок сторінок виконується через `pdfinfo` з timeout, а `needs_optimization()` **ніколи не повинна кидати виняток** назовні. При помилці — консервативний fallback (запускаємо оптимізацію), а не зупинка pipeline.

### 5.2. Disk Pre-flight Check

Перед запуском оптимізації — перевірка вільного місця на томі `/data/kdv_optimize/`:

```python
import shutil

def _check_disk_space(filepath: str) -> bool:
    file_size = os.path.getsize(filepath)
    required = file_size * 2.5  # original + optimized + 50% буфер
    free = shutil.disk_usage("/data/kdv_optimize").free
    if free < required:
        logger.warning(
            "pdf_skip_no_disk_space",
            free_mb=round(free / 1024 / 1024, 1),
            required_mb=round(required / 1024 / 1024, 1),
        )
        return False
    return True
```

Якщо перевірка не пройшла — оптимізація пропускається, завантажується оригінал. Це **не помилка** — логується як `pdf_optimized: skipped_no_disk`.

### 5.3. Fallback механізм (Захист від дурня)

Якщо процес оптимізації:

- Впав з помилкою або Timeout (120s SIGKILL)
- Створив файл, який **більший** за оригінал
- Створив порожній файл (0 bytes)
- `kdv-optimizer` недоступний (HTTP error, connection refused)

👉 Система не перериває архівацію, логує `WARNING` і продовжує завантаження **оригінального файлу**. Помилка не повинна бути "невидимою": результат задачі повертає явні telemetry-поля.

**Обов'язкові поля у `task.result`:**

```json
{
  "handle": "https://dspace.example/handle/...",
  "uuid": "item-uuid",
  "pdf_optimized": "true | false | skipped_by_user | skipped_by_size | skipped_no_disk",
  "pdf_fallback_reason": "timeout | larger_output | empty_output | optimizer_unavailable | exception | null",
  "pdf_original_mb": 120.5,
  "pdf_final_mb": 120.5
}
```

**Ієрархія fallback причин** (`pdf_fallback_reason`):

| Причина | Значення |
|---|---|
| `timeout` | Ghostscript перевищив 120s |
| `larger_output` | Оптимізований файл більший за оригінал |
| `empty_output` | Результат 0 bytes |
| `optimizer_unavailable` | `kdv-optimizer` не відповідає |
| `exception` | Будь-яка інша помилка |

### 5.4. Управління диском (Ефемерні дані)

Тимчасові файли зберігаються у shared volume `/data/kdv_optimize/`:
- `input/{job_id}.pdf` — оригінал (copy, не переміщення)
- `output/{job_id}.pdf` — результат оптимізації

**Гарантоване очищення** — `finally` блок у `core.py` на стороні `kdv-api`, незалежно від результату:

```python
job_id = str(uuid.uuid4())
input_path = f"/data/kdv_optimize/input/{job_id}.pdf"
output_path = f"/data/kdv_optimize/output/{job_id}.pdf"

try:
    shutil.copy(original_pdf_path, input_path)
    result = optimizer_client.optimize(job_id)
    final_path = result.output_path if result.success else original_pdf_path
    dspace_client.upload_bitstream(final_path, ...)
finally:
    for path in (input_path, output_path):
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)
```

**Додатковий cleanup-рівень:** `kdv-optimizer` має мати TTL janitor або startup-cleanup для `/data/kdv_optimize/input|output`. Це закриває сценарій, коли `kdv-api` або task thread впав до `finally`. Рекомендований default: видаляти файли старші за 24 години, TTL задавати через `OPTIMIZER_TMP_TTL_SECONDS`.

---

## 6. Non-Functional Requirements (SRE / DevOps)

### 6.1. Docker Compose — два сервіси

```yaml
services:
  kdv-api:
    image: ghcr.io/pinokew/kdv-integrator-event:${KDV_IMAGE_VERSION}
    volumes:
      - kdv_optimize_data:/data/kdv_optimize
    environment:
      - OPTIMIZER_URL=http://kdv-optimizer:5001
      - OPTIMIZER_TIMEOUT=130  # > 120s щоб HTTP не рвався раніше ніж SIGKILL у optimizer
    deploy:
      resources:
        limits:
          cpus: '1.0'    # Знижено: CPU для GS тепер у kdv-optimizer
          memory: 1G     # Знижено: RAM для GS тепер у kdv-optimizer
        reservations:
          cpus: '0.25'
          memory: 256M

  kdv-optimizer:
    image: ghcr.io/pinokew/kdv-optimizer:${KDV_OPTIMIZER_VERSION}
    volumes:
      - kdv_optimize_data:/data/kdv_optimize
    ports: []            # Внутрішня мережа Docker, зовні не відкритий
    deploy:
      resources:
        limits:
          cpus: '2.0'    # Ghostscript може використовувати 2 ядра
          memory: 2G     # Жорсткий ліміт для Ghostscript
        reservations:
          cpus: '0.5'
          memory: 512M

volumes:
  kdv_optimize_data:
    driver: local
```

> **Безпека:** `kdv-optimizer` не відкритий назовні. Cloudflare Access не потрібен — внутрішня Docker мережа. Мінімальні гарантії: не запускати процес як `root`, не публікувати порт назовні, приймати тільки `job_id`, будувати шляхи лише всередині allowlisted `/data/kdv_optimize`, запускати Ghostscript з `-dSAFER` і pinned версіями пакетів.

### 6.2. Пріоритезація процесів (OS Level, у `kdv-optimizer`)

Ghostscript запускається з низьким пріоритетом, щоб не заважати іншим процесам хоста:

```python
import subprocess

def run_ghostscript(input_path: str, output_path: str) -> None:
    subprocess.run(
        [
            "nice", "-n", "15",
            "ionice", "-c", "3",
            "gs",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            "-dPDFSETTINGS=/ebook",
            "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-sOutputFile={output_path}",
            input_path,
        ],
        timeout=120,      # SIGKILL якщо не встиг
        check=True,
    )
```

### 6.3. Таймаути

| Таймаут | Значення | Де встановлюється |
|---|---|---|
| Ghostscript process | 120s | `subprocess.run(timeout=120)` у `kdv-optimizer` |
| HTTP клієнт `kdv-api` → `kdv-optimizer` | 130s | sync `requests` або `httpx.Client(timeout=130)` у `kdv-api` |
| `ROBOT_MAX_WAIT` | 900s | Без змін — загальний таймаут задачі включає можливу оптимізацію |

### 6.4. Health та Readiness `kdv-optimizer`

`kdv-optimizer` має експонувати внутрішні endpoint-и:

| Endpoint | Очікувана поведінка |
|---|---|
| `GET /health` | `200 {"status":"ok"}` якщо Flask-процес живий |
| `GET /ready` | `200` якщо існують `/data/kdv_optimize/input|output`, є write access, Ghostscript/qpdf/pdfinfo доступні; `503` з деталями, якщо ні |

`kdv-api` може логувати стан optimizer readiness, але недоступність optimizer не повинна автоматично робити `kdv-api` not-ready, доки бізнес-правило дозволяє fallback на оригінал.

### 6.5. SLO — два окремих трека

> **Важливо:** Об'єднання в один P95 ≤ 180s — помилка. Оптимізовані та неоптимізовані задачі мають різний профіль, їх змішування маскує деградацію.

| Метрика | SLO | Умова |
|---|---|---|
| P95 Integration Time (без оптимізації) | ≤ 60s | `pdf_optimized IN (false, skipped_by_user, skipped_by_size, skipped_no_disk)` |
| P95 Integration Time (з оптимізацією) | ≤ 240s | `pdf_optimized = true` |
| API Availability | ≥ 99% | — |
| Error Rate | ≤ 5% | — |
| Optimizer Fallback Rate | ≤ 10% за добу | Алерт при перевищенні |

---

## 7. Оновлення robot.py (Batch Processing)

`robot.py` викликає API напряму, без IntranetUserJS. Потребує оновлень:

### 7.1. Новий CLI параметр

```bash
# За замовчуванням — оптимізація увімкнена (відповідає поведінці UI)
python scripts/robot.py candidates.txt

# Вимкнути оптимізацію для всього батчу (наприклад, термінова масова архівація)
python scripts/robot.py candidates.txt --skip-optimization

# Явно задати паралелізм без ENV
python scripts/robot.py candidates.txt --parallelism 1
```

CLI потрібно нормалізувати через `argparse`: позиційний файл кандидатів (`default: candidates.txt`), `--skip-optimization`, `--parallelism`, опційно `--max-wait`. ENV (`ROBOT_PARALLELISM`, `ROBOT_MAX_WAIT`) лишаються backward-compatible fallback.

### 7.2. Обмеження паралелізму при активному оптимізаторі

`kdv-optimizer` має `ProcessPoolExecutor(max_workers=1)` — черга запитів обробляється послідовно. При `ROBOT_PARALLELISM > 1` кілька задач одночасно чекатимуть оптимізатор, збільшуючи загальний час.

**Рекомендоване значення для batch з важкими PDF:**

```bash
# .env або ENV при запуску robot.py з оптимізацією
ROBOT_PARALLELISM=1      # Послідовно — максимальна передбачуваність
ROBOT_MAX_WAIT=900       # 120s (GS) + 180s (upload) + буфер → достатньо
```

> Якщо потрібна швидша масова архівація — `ROBOT_PARALLELISM=2` допустимо (другий запит чекатиме у черзі оптимізатора), але `ROBOT_MAX_WAIT` треба збільшити до 1200s.

---

## 8. Security: Ghostscript CVE Policy

Ghostscript має задокументовану CVE-активність (приклад: CVE-2023-36664, CVSS 9.8). Встановлення без pinned версії неприпустимо в Production.

**Вимоги до Dockerfile `kdv-optimizer`:**

```dockerfile
# Pinned версія — оновлювати лише після перевірки CVE у trivy
RUN apt-get update && apt-get install -y \
    ghostscript=10.02.1~dfsg-1 \
    poppler-utils=22.12.0-2+b1 \
    qpdf=11.3.0-1 \
    && rm -rf /var/lib/apt/lists/*
```

**CI/CD — додати до існуючого пайплайну:**

```yaml
# .github/workflows/ci.yml — додати step для kdv-optimizer image
- name: Trivy scan kdv-optimizer
  uses: aquasecurity/trivy-action@master
  with:
    image-ref: ghcr.io/pinokew/kdv-optimizer:${{ github.sha }}
    severity: HIGH,CRITICAL
    exit-code: 1   # Блокуємо merge при критичних CVE
```

> Версії залежностей переглядаються при кожному релізі. При виявленні CRITICAL CVE — `kdv-optimizer` оновлюється **незалежно від `kdv-api`** (ще одна перевага окремого контейнера).

> Корпус PDF вважається контрольованим внутрішнім джерелом, а не публічним upload від недовірених користувачів. Тому не вводимо надмірну sandbox-архітектуру на першому етапі, але мінімальні гарантії з секції 6.1 є обов'язковими.

---

## 9. План імплементації (Архітектура)

### Новий репозиторій / директорія

```
kdv-integrator-event/
│
├── 📁 src/
│   ├── services/
│   │   └── pdf.py              # PDFOptimizerClient (HTTP клієнт до kdv-optimizer)
│   └── core.py                 # Інтеграція клієнта перед DSpace upload
│
├── 📁 kdv-optimizer/           # Новий мінісервіс (або окремий репозиторій)
│   ├── optimizer_app.py        # Flask mini-API: POST /optimize, GET /optimize/{id}
│   ├── services/
│   │   └── pdf.py              # PDFOptimizerService: needs_optimization(), optimize()
│   ├── Dockerfile
│   └── requirements.txt
│
├── docker-compose.yml          # Додано kdv-optimizer service + shared volume
└── scripts/
    ├── robot.py                # Оновлено: --skip-optimization прапор
    └── poc_optimizer.py        # НОВИЙ: R&D benchmark скрипт
```

### Кроки імплементації

1. **`scripts/poc_optimizer.py`:** R&D benchmark як release gate перед production-кодом
2. **`kdv-optimizer` мінісервіс:** Flask API + `PDFOptimizerService` + Dockerfile + `ProcessPoolExecutor(1)` + `/health` + `/ready` + TTL cleanup
3. **`docker-compose.yml`:** Новий сервіс, shared volume, resource limits
4. **`IntranetUserJS.js`:** Чекбокс, передача `skip_optimization` у payload
5. **`src/app.py`:** Прочитати optional JSON payload; default `skip_optimization=false`; старі клієнти без body мають працювати без змін
6. **`src/services/pdf.py`:** Sync `PDFOptimizerClient` (`requests` або `httpx.Client`) з fallback логікою і `job_id` контрактом
7. **`src/core.py`:** Інтеграція клієнта перед кроком DSpace upload + `finally` cleanup + telemetry у `task.result`
8. **`scripts/robot.py`:** `argparse`, файл кандидатів, `--skip-optimization`, `--parallelism`
9. **`scripts/deploy-orchestrator-swarm.sh`:** build/tag `kdv-optimizer` image, render env для `OPTIMIZER_URL`/версій, verify `${STACK_NAME}_kdv-optimizer`, rollback/runbook інструкції
10. **CI/CD:** Додати build + trivy scan для `kdv-optimizer` image

### Deploy / rollback вимоги для Swarm

- У local image mode orchestrator має збирати/tag-ити і `kdv-api`, і `kdv-optimizer` сумісними git-SHA тегами.
- Rendered Swarm manifest має містити новий сервіс, shared volume, internal networking і env для `OPTIMIZER_URL`.
- Post-deploy verification має перевіряти не тільки `${STACK_NAME}_kdv-api`, а й `${STACK_NAME}_kdv-optimizer`.
- Runbook має описувати restart/rollback нового сервісу, перевірку `/health`/`/ready`, перегляд логів і cleanup старих temp-файлів.
- Rollback має бути можливим без видалення DSpace/Koha даних: вимкнути оптимізацію env-прапором або відкотити compose/orchestrator зміни, після чого `kdv-api` працює зі старим upload оригіналу.

---

## 10. Моніторинг та Логування

### Нові поля структурованого логу

```json
{
  "pdf_optimized": "true | false | skipped_by_user | skipped_by_size | skipped_no_disk",
  "pdf_fallback_reason": "timeout | larger_output | empty_output | optimizer_unavailable | exception | null",
  "pdf_engine": "ghostscript | pikepdf | null",
  "pdf_original_mb": 120.5,
  "pdf_final_mb": 18.2,
  "pdf_pages": 50,
  "pdf_optimization_time_ms": 45000,
  "pdf_thread_wait_ms": 340,
  "pdf_disk_free_mb": 4096.0
}
```

**`pdf_thread_wait_ms`** — час від виклику `POST /optimize` до початку реальної обробки у `kdv-optimizer` (відображає чергу). Якщо P95 цього значення стабільно > 5s — сигнал для збільшення `max_workers` або виділення більш потужного хоста для оптимізатора.

### Алерти

| Алерт | Умова | Severity |
|---|---|---|
| Optimizer fallback rate | > 10% задач за добу мають `pdf_fallback_reason != null` | WARNING |
| Optimizer timeout spike | P95 `pdf_optimization_time_ms` > 100 000ms (100s) двічі поспіль | WARNING |
| Optimizer unavailable | `pdf_fallback_reason = optimizer_unavailable` будь-яка поява | CRITICAL |
| Disk space low | `pdf_disk_free_mb` < 500 у будь-якому лозі за добу | WARNING |
| SLO breach (optimized) | P95 Integration Time > 240s для `pdf_optimized = true` | WARNING |
| SLO breach (non-optimized) | P95 Integration Time > 60s для `pdf_optimized != true` | CRITICAL |

---

## 11. Тестування

### Нові тест-кейси (додати до існуючих 22 тестів)

| Тест | Файл | Що перевіряється |
|---|---|---|
| `test_optimizer_needs_optimization_threshold` | `test_services.py` | Евристика A, B та гранична умова |
| `test_optimizer_pdfinfo_crash_fallback` | `test_services.py` | `pdfinfo` timeout/виняток → повертає `True` (консервативний fallback) |
| `test_optimizer_disk_preflight_fail` | `test_services.py` | Недостатньо місця → `skipped_no_disk`, без виключення |
| `test_optimizer_larger_output_fallback` | `test_services.py` | Оптимізований > оригінал → завантажується оригінал |
| `test_optimizer_client_unavailable` | `test_services.py` | `kdv-optimizer` HTTP 503 → fallback, pipeline не зупиняється |
| `test_core_cleanup_on_exception` | `test_core.py` | `finally` block видаляє тимчасові файли навіть при виключенні |
| `test_robot_skip_optimization_flag` | `test_scripts.py` | `--skip-optimization` передає `skip_optimization: true` в payload |
| `test_integrate_without_payload_defaults_to_optimization` | `test_app.py` | Старий клієнт без JSON body працює, default `skip_optimization=false` |
| `test_optimizer_fallback_does_not_fail_archive` | `test_core.py` | Fallback оптимізації не переводить задачу архівації в `error` |
| `test_hard_limit_does_not_prevent_optimization_path` | `test_core.py` | Поточний hard limit не ламає прийнятий порядок оптимізації/відмови |
| `test_core_cleanup_on_dspace_exception` | `test_core.py` | Temp input/output видаляються, навіть якщо DSpace upload падає |
