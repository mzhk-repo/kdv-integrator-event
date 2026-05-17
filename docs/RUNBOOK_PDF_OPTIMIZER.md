# Runbook: PDF Optimizer (`kdv-optimizer`)

Мета: описати використання, конфігурацію, перевірку та rollback модуля автоматичної оптимізації PDF у KDV Integrator. Модуль працює як окремий внутрішній сервіс `kdv-optimizer` і оптимізує важкі PDF перед завантаженням у DSpace.

**Пов'язані документи**
- PRD: [docs/pdf-optimizer/PRD-optimizer.md](pdf-optimizer/PRD-optimizer.md)
- Roadmap: [docs/pdf-optimizer/roadmap-optimizer.md](pdf-optimizer/roadmap-optimizer.md)
- Testing: [docs/RUNBOOK_TESTING.md](RUNBOOK_TESTING.md)
- Robot: [docs/RUNBOOK_ROBOT.md](RUNBOOK_ROBOT.md)
- Deploy scripts: [docs/scripts_runbook.md](scripts_runbook.md)

---

## 1. Архітектура

`kdv-optimizer` ізолює CPU/RAM-heavy Ghostscript від основного `kdv-api`.

```text
kdv-api                         kdv-optimizer
core.py                         optimizer_app.py
PDFOptimizerClient   HTTP       POST /optimize
/data/kdv_optimize  <------->   GET /optimize/<job_id>
shared volume                   ProcessPoolExecutor(max_workers=1)
```

Контракт безпеки:
- HTTP передає тільки `job_id` UUID.
- `kdv-optimizer` сам будує шляхи `input/{job_id}.pdf` і `output/{job_id}.pdf` всередині `/data/kdv_optimize`.
- Довільні file paths між сервісами не передаються.
- `kdv-optimizer` не має published ports і доступний тільки через Docker network.

Shared volume:

```text
/data/kdv_optimize/input/<job_id>.pdf
/data/kdv_optimize/output/<job_id>.pdf
```

---

## 2. Коли оптимізація запускається

Оптимізація увімкнена за замовчуванням і запускається тільки якщо виконуються умови:

- `skip_optimization != true`.
- Розмір файлу `> 100MB`, або:
- Розмір файлу `> 50MB` і питома вага `> 500KB/сторінку`.

Підрахунок сторінок виконується через `pdfinfo` з timeout. Якщо `pdfinfo` падає або зависає, `needs_optimization()` не зупиняє pipeline і консервативно повертає `True` для файлів понад 50MB.

Оптимізація пропускається без помилки, якщо:
- користувач передав `skip_optimization=true`;
- файл не проходить size/page heuristic;
- на shared volume недостатньо місця (`required ~= file_size * 2.5`);
- optimizer недоступний, падає або повертає невдалий результат.

У всіх fallback-сценаріях архівація продовжується з оригінальним PDF.

---

## 3. Конфігурація ENV

### `kdv-api`

```bash
OPTIMIZER_URL=http://kdv-optimizer:5001
OPTIMIZER_TIMEOUT=130
```

`OPTIMIZER_TIMEOUT` має бути більшим за `GS_TIMEOUT`, щоб HTTP-клієнт не обривав запит раніше, ніж Ghostscript отримає свій process timeout.

### `kdv-optimizer`

```bash
OPTIMIZER_PORT=5001
DATA_DIR=/data/kdv_optimize
INPUT_DIR=/data/kdv_optimize/input
OUTPUT_DIR=/data/kdv_optimize/output
GS_TIMEOUT=120
QPDF_ENABLED=false
TMP_TTL_SECONDS=86400
TTL_CHECK_INTERVAL_SECONDS=3600
```

Важливо для Swarm: `DATA_DIR`, `INPUT_DIR`, `OUTPUT_DIR` мають збігатися з mount path контейнера. Правильний production/default path — `/data/kdv_optimize`. Якщо у runtime env випадково потрапить host path на кшталт `/opt/kdv-integrator/kdv_optimize`, `/health` може бути `200`, але `/ready` буде `503`.

### Image/deploy змінні

```bash
KDV_OPTIMIZER_IMAGE_REPOSITORY=ghcr.io/OWNER/kdv-optimizer
KDV_OPTIMIZER_VERSION=latest
# або повний override:
KDV_OPTIMIZER_IMAGE=kdv-optimizer:<git-sha>
```

У default `ORCHESTRATOR_IMAGE_MODE=local` deploy script збирає локальні images:

```text
kdv-integrator-event:<git-sha>
kdv-optimizer:<git-sha>
```

і передає їх у Swarm manifest через `KDV_IMAGE` та `KDV_OPTIMIZER_IMAGE`.

---

## 4. Docker Compose / Swarm контракт

Обидва сервіси мають монтувати один volume:

```yaml
volumes:
  - kdv_optimize_data:/data/kdv_optimize
```

`kdv-optimizer`:
- не публікує порт на host;
- має `expose: 5001` для внутрішньої Docker network;
- має healthcheck `GET /health`;
- у Swarm має `replicas: 1`, бо job store in-memory, а ProcessPoolExecutor має `max_workers=1`.

Ресурсні ліміти optimizer-а:

```yaml
limits:
  cpus: "2.0"
  memory: 2G
reservations:
  cpus: "0.5"
  memory: 512M
```

У rendered Swarm manifest `cpus` має лишатися string. Deploy orchestrator sanitizes це перед `docker stack deploy`.

---

## 5. Операторське використання

### Koha UI

У Koha біля кнопки архівації є чекбокс:

```text
[ ] Не оптимізовувати файл (завантажити оригінал)
```

Default: чекбокс не активний, тобто оптимізація увімкнена.

API payload:

```json
{"skip_optimization": false}
```

Для примусового upload оригіналу:

```json
{"skip_optimization": true}
```

### API compatibility

Старі клієнти можуть викликати `/integrate` без JSON body. Це має працювати і означає:

```json
{"skip_optimization": false}
```

Приклад:

```bash
curl -X POST \
  http://127.0.0.1:5000/kdv/api/integrate/123 \
  -H "X-KDV-TOKEN: <token>"
```

### Robot batch

За замовчуванням оптимізація увімкнена:

```bash
python3 scripts/robot.py candidates.txt
```

Вимкнути оптимізацію для batch:

```bash
python3 scripts/robot.py candidates.txt --skip-optimization
```

Рекомендовано для важких PDF:

```bash
ROBOT_PARALLELISM=1
ROBOT_MAX_WAIT=900
```

Якщо `ROBOT_PARALLELISM > 1` і оптимізація увімкнена, задачі можуть чекати в черзі optimizer-а, бо `kdv-optimizer` обробляє один Ghostscript job одночасно.

---

## 6. Health та readiness

Перевірити Swarm services:

```bash
docker service ls --filter name=kdv_integrator_event --format '{{.Name}} {{.Replicas}} {{.Image}}'
```

Очікувано:

```text
kdv_integrator_event_kdv-api        1/1
kdv_integrator_event_kdv-optimizer  1/1
```

Знайти контейнери:

```bash
docker ps --filter name=kdv_integrator_event --format '{{.ID}} {{.Names}} {{.Status}} {{.Image}}'
```

Перевірити network path з `kdv-api` до optimizer:

```bash
docker exec <kdv-api-container-id> \
  curl -fsS -w '\nHTTP=%{http_code}\n' \
  http://kdv-optimizer:5001/health
```

Очікувано:

```json
{"status":"ok"}
HTTP=200
```

Перевірити readiness:

```bash
docker exec <kdv-api-container-id> \
  curl -sS -w '\nHTTP=%{http_code}\n' \
  http://kdv-optimizer:5001/ready
```

Очікувано:

```json
{"status":"ready"}
HTTP=200
```

Перевірити ENV і mount всередині optimizer-а:

```bash
docker exec <kdv-optimizer-container-id> sh -lc '
  env | sort | grep -E "^(DATA_DIR|INPUT_DIR|OUTPUT_DIR|OPTIMIZER_PORT|GS_TIMEOUT|TMP_TTL_SECONDS|TTL_CHECK_INTERVAL_SECONDS|QPDF_ENABLED|TZ)="
  ls -ld /data/kdv_optimize /data/kdv_optimize/input /data/kdv_optimize/output
  gs --version
  pdfinfo -v
'
```

---

## 7. Telemetry у task result

Після архівації `task.result` має містити PDF telemetry:

```json
{
  "pdf_optimized": "true | false | skipped_by_user | skipped_by_size | skipped_no_disk",
  "pdf_fallback_reason": "timeout | larger_output | empty_output | optimizer_unavailable | exception | null",
  "pdf_original_mb": 120.5,
  "pdf_final_mb": 18.2,
  "pdf_pages": null,
  "pdf_optimization_time_ms": 45000,
  "pdf_thread_wait_ms": 340,
  "pdf_disk_free_mb": 4096.0
}
```

Інтерпретація:
- `pdf_optimized=true` — DSpace отримав optimized output.
- `pdf_optimized=false` + `pdf_fallback_reason != null` — optimizer не спрацював, але архівація продовжилася з оригіналом.
- `skipped_by_user` — оператор або robot явно вимкнув оптимізацію.
- `skipped_by_size` — файл не потребував оптимізації за heuristic.
- `skipped_no_disk` — недостатньо місця на shared volume.

---

## 8. Deploy / redeploy

Default Swarm deploy:

```bash
ORCHESTRATOR_MODE=swarm \
ORCHESTRATOR_ENV_FILE=/tmp/env.decrypted \
./scripts/deploy-orchestrator-swarm.sh
```

Що має зробити orchestrator:
- зібрати `kdv-api` image;
- зібрати `kdv-optimizer` image;
- render Swarm manifest;
- виконати `docker stack deploy`;
- перевірити `${STACK_NAME}_kdv-api` і `${STACK_NAME}_kdv-optimizer` до `1/1`.

Після deploy виконати smoke checks із розділу 6.

---

## 9. Rollback / вимкнення оптимізації

### Тимчасово вимкнути optimizer без rollback коду

Найбезпечніший operational fallback — зробити optimizer недоступним для `kdv-api`, щоб workflow завантажував оригінал:

```bash
docker service update \
  --env-add OPTIMIZER_URL=disabled \
  kdv_integrator_event_kdv-api
```

Очікування: архівація не падає, telemetry має показувати fallback/optimizer unavailable або upload оригіналу залежно від path.

### Повний rollback deploy

1. Повернути попередній commit/compose/orchestrator config.
2. Передеплоїти попередній `GIT_SHA`.
3. Не видаляти `kdv-drive` і DSpace/Koha дані.
4. `kdv_optimize_data` містить тільки тимчасові PDF і може бути очищений окремо після перевірки, що jobs не виконуються.

---

## 10. Cleanup тимчасових файлів

Основний cleanup виконує `kdv-api` у `finally`: видаляє `input/{job_id}.pdf` та `output/{job_id}.pdf` незалежно від успіху optimizer-а або DSpace upload.

Додатковий cleanup виконує `TTLJanitor` у `kdv-optimizer`:
- startup cleanup при старті app;
- періодична перевірка кожні `TTL_CHECK_INTERVAL_SECONDS`;
- видалення regular files старших за `TMP_TTL_SECONDS`.

Перевірити залишки:

```bash
docker exec <kdv-optimizer-container-id> sh -lc '
  find /data/kdv_optimize/input /data/kdv_optimize/output -maxdepth 1 -type f -printf "%p %s bytes\n"
'
```

Не видаляйте файли вручну під час активної оптимізації. Якщо потрібно чистити аварійно, спочатку перевірте, що `kdv-optimizer` не обробляє job і немає активного `/integrate` task.

---

## 11. Troubleshooting

### `/health=200`, але `/ready=503`

Отримати body:

```bash
docker exec <kdv-api-container-id> curl -sS http://kdv-optimizer:5001/ready
```

Типові причини:
- `INPUT_DIR`/`OUTPUT_DIR` вказують на host path, а не `/data/kdv_optimize/...`;
- shared volume не змонтований у `kdv-optimizer`;
- директорії не writable для користувача `optimizer`;
- у image відсутні `gs` або `pdfinfo`.

Перевірка:

```bash
docker exec <kdv-optimizer-container-id> sh -lc '
  env | grep -E "^(DATA_DIR|INPUT_DIR|OUTPUT_DIR)="
  ls -ld /data/kdv_optimize /data/kdv_optimize/input /data/kdv_optimize/output
  gs --version
  pdfinfo -v
'
```

### `pdf_fallback_reason=optimizer_unavailable`

Перевірити:

```bash
docker service ls --filter name=kdv_integrator_event_kdv-optimizer
docker service ps kdv_integrator_event_kdv-optimizer --no-trunc
docker logs --tail 100 <kdv-optimizer-container-id>
```

Також перевірити з `kdv-api`:

```bash
docker exec <kdv-api-container-id> curl -fsS http://kdv-optimizer:5001/health
```

### `pdf_fallback_reason=timeout`

Ghostscript не вклався у `GS_TIMEOUT`.

Дії:
- перевірити розмір/тип PDF;
- подивитися `pdf_optimization_time_ms`;
- не збільшувати timeout без оцінки CPU/RAM впливу;
- для batch запуску збільшити `ROBOT_MAX_WAIT`, якщо task-level wait не вистачає.

### `pdf_fallback_reason=larger_output` або `empty_output`

Це штатний fallback. DSpace має отримати original PDF.

Дії:
- перевірити конкретний PDF у PoC benchmark;
- не вмикати додаткові engines у production без benchmark і visual `quality_ok`.

### `pdf_optimized=skipped_no_disk`

Перевірити free space shared volume:

```bash
docker exec <kdv-optimizer-container-id> df -h /data/kdv_optimize
```

Потрібно приблизно `file_size * 2.5` вільного місця.

---

## 12. Тестування

Focused optimizer tests:

```bash
PYTHONPATH=$(pwd):$(pwd)/kdv-optimizer pytest tests/test_services.py -q
```

Core integration tests:

```bash
PYTHONPATH=$(pwd) pytest tests/test_core.py -q
```

App/robot compatibility tests:

```bash
PYTHONPATH=$(pwd) pytest tests/test_app.py tests/test_robot.py -q
```

Якщо на host бракує залежностей (`flask`, `pymarc`), запускати в Docker image:

```bash
docker run --rm --env-file .env.example --entrypoint python \
  -v /opt/kdv-integrator/kdv-integrator-event:/work \
  -w /work \
  kdv-integrator-event:<git-sha> \
  -m pytest tests/test_services.py tests/test_core.py tests/test_app.py tests/test_robot.py -q
```

---

## 13. Security та CVE policy

Ghostscript має активну CVE-історію, тому:
- версії системних пакетів у `kdv-optimizer/Dockerfile` мають бути pinned;
- image має проходити Trivy scan у CI/CD;
- CRITICAL/HIGH findings мають блокувати merge/deploy згідно CI policy;
- `kdv-optimizer` запускається non-root користувачем;
- Ghostscript запускається з `-dSAFER`, `nice -n 15`, `ionice -c 3` і process timeout.

Порт optimizer-а не відкривати назовні. Cloudflare Access для нього не потрібен, бо сервіс внутрішній.

---

## 14. SLO / операційні орієнтири

Базові орієнтири після production deploy:
- P95 Integration Time без оптимізації: `<= 60s`.
- P95 Integration Time з оптимізацією: `<= 240s`.
- Optimizer fallback rate: `<= 10%` за добу.
- `optimizer_unavailable` — критичний сигнал, навіть якщо архівація fallback-ить на original.

Не змішуйте optimized і non-optimized latency в одну P95 метрику: профілі задач різні.
