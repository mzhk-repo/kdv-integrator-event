# ROBOT — Ранбук по масовій архівації книг

**Версія:** 0.1 (M7)  
**Останнє оновлення:** 2026-03-05  
**Мета:** Простий гайд по запуску та налаштуванню `scripts/robot.py` для операторів

---

## 📋 Що це робить?

**Robot** — це "робот архіватор", який:

1. 📖 **Читає список** книг з `candidates.txt`
2. 🚀 **Запускає задачу архівації** для кожної книги (POST запит до API)
3. ⏳ **Чекає результату** (polling задачі)
4. ✅ **Записує результат** (успіх, помилка, дублікат, timeout)
5. 📊 **Видає статистику** — скільки успішно, скільки провалилось

**Коротко:** Автоматично архівує книги з Koha до DSpace за списком.

---

## 🔧 Крок 1: Підготувити список (candidates.txt)

Robot читає файл `candidates.txt` в корені проекту. Формат:

### Синтаксис

```
# Коментарі (ігнорується)
123                    # Одна книга
456, 789, 1000         # Кілька книг в рядку (через кому)
500-510                # Діапазон (від 500 до 510 включно)
2000-2500, 3000, 4000  # Мікс: діапазон + окремі + коментар # коммент
```

### Приклад файлу candidates.txt:

```
# === Канарійський пуск (невелика партія) ===
100-150

# === Друга хвиля ===
200, 210, 215, 220-230

# === Вручну вибрані за якістю ===
5000
5001
5010
```

### Що автоматично буде?
- Лічильник дублів (діапазон 200-230 + окреме 200 → одно 200)
- Сортування внутрішнього списку (виконається в порядку: 100-150, 200, 210, 215, 220-230)
- Ігнорування помилок формату (невалідні рядки пропускаються з warning)

**Готова?** Переходимо до запуску!

---

## 🚀 Крок 2: Перевірити запуск без архівації

У Swarm-середовищі не запускаємо `robot.py` напряму через `docker compose exec` або ручний `docker exec`. Для цього є wrapper:

```bash
SERVER_ENV=dev scripts/run-robot-swarm.sh --dry-run candidates.txt
```

Що робить `--dry-run`:
- знаходить env-контекст у стилі `src/config.py`: `ORCHESTRATOR_ENV_FILE` → `SERVER_ENV`/`ENVIRONMENT_NAME` → `env.dev.enc`/`env.prod.enc` → `.env`;
- передає цей env у `docker exec` через `--env-file`, щоб `robot.py` бачив `KDV_API_TOKEN`;
- визначає Swarm stack/service (`STACK_NAME`, `SWARM_SERVICE_NAME` або default `kdv_integrator_event_kdv-api`);
- знаходить локальний runtime-контейнер `kdv-api` через Docker Swarm label;
- перевіряє `/kdv/api/health` всередині контейнера;
- копіює host `candidates.txt` у контейнер як `/tmp/kdv-candidates.txt`;
- перевіряє, що `robot.py` бачить і парсить файл;
- **не стартує batch**.

Для production-контексту:

```bash
SERVER_ENV=prod scripts/run-robot-swarm.sh --dry-run candidates.txt
```

Якщо env уже розшифрований оркестратором:

```bash
ORCHESTRATOR_ENV_FILE=/path/to/env.decrypted scripts/run-robot-swarm.sh --dry-run candidates.txt
```

---

## 🚀 Крок 3: Запустити robot

### Простий запуск

```bash
SERVER_ENV=dev scripts/run-robot-swarm.sh candidates.txt
```

Production запуск:

```bash
SERVER_ENV=prod scripts/run-robot-swarm.sh candidates.txt
```

**Що буде:**
- wrapper знайде Swarm-контейнер `kdv-api`;
- передасть `candidates.txt` у контейнер;
- запустить `python3 scripts/robot.py /tmp/kdv-candidates.txt`;
- архівація піде **послідовно** за замовчуванням;
- за замовчуванням передасть `skip_optimization=false`, тобто PDF-оптимізація увімкнена;
- подробиці будуть у `/app/logs/robot_batch.log` всередині контейнера;
- після завершення wrapper синхронізує цей файл у host `logs/robot_batch.log`.

### CLI-параметри

```bash
scripts/run-robot-swarm.sh --help
SERVER_ENV=dev scripts/run-robot-swarm.sh candidates.txt --skip-optimization
SERVER_ENV=dev scripts/run-robot-swarm.sh candidates.txt --parallelism 1 --max-wait 900
```

- `--skip-optimization` → передає `skip_optimization=true` для всього batch.
- `--parallelism` → перекриває `ROBOT_PARALLELISM`.
- `--max-wait` → перекриває `ROBOT_MAX_WAIT`.

> Якщо `--parallelism > 1` без `--skip-optimization`, задачі можуть чекати чергу `kdv-optimizer`. Рекомендовано `--parallelism 1` або `--skip-optimization`; для паралелізму 2 збільшуйте `--max-wait` до 1200.

### Хочемо швидше? Паралелізм

```bash
SERVER_ENV=dev scripts/run-robot-swarm.sh candidates.txt --parallelism 4
```

**Умовно:**
- `--parallelism 1` → послідовно, найбезпечніше;
- `--parallelism 4` → по 4 одночасно, швидше, але більше навантаження;
- `--parallelism 8` → тільки після перевірки ресурсів і черги optimizer-а.

---

## 🧪 GUI canary запуск із Koha search results

Канарейковий GUI доступний у Koha staff на сторінці результатів пошуку каталогу, наприклад:

```text
https://koha.pinokew.buzz/cgi-bin/koha/catalogue/search.pl?q=*
```

Блок **Robot Batch** показується тільки на `catalogue/search.pl`. Він викликає KDV API напряму з браузера за тим самим auth/CORS-патерном, що й одиночна архівація з `IntranetUser.js`.

Поля:

| Поле | Значення |
|------|----------|
| `textarea` | Синтаксис як у `candidates.txt`: `100-110`, `200, 210`, коментарі `# ...` |
| `Parallelism` | Дефолт `1`; для канарейки залишати `1` |
| `Max wait` | Дефолт `900` секунд на один запис |
| `Не оптимізовувати файл` | Передає `skip_optimization=true` для всього batch |

Після натискання **Запустити Robot Batch** UI робить:

1. `GET /kdv/api/health` для перевірки access-сесії.
2. `POST /kdv/api/robot/batch` з candidates-текстом і параметрами.
3. Polling `GET /kdv/api/status/<task_id>` до `success` або `error`.

Очікувана відповідь на старт:

```json
{
  "status": "accepted",
  "task_id": "...",
  "candidates_count": 3,
  "preview": ["100", "101", "102"]
}
```

> Це саме канарейкова інтеграція. Вона не додає окремих ролей, CSRF-логіки або rate limit; доступ контролюється існуючим KDV API auth mode.

---

## ⚙️ Налаштування

`ROBOT_*` можна передати в wrapper як env. Wrapper прокине їх у процес `robot.py` всередині контейнера:

```bash
ROBOT_BATCH_DELAY=1 ROBOT_POLL_INTERVAL=1 \
  SERVER_ENV=dev scripts/run-robot-swarm.sh candidates.txt --parallelism 2
```

| Параметр | За замовч. | Що робить | Коли змінювати |
|----------|-----------|-----------|----------------|
| `ROBOT_BATCH_DELAY` | 5 сек | Пауза між стартами завдань | Збільш якщо Koha повільна, зменш якщо потрібна швидкість |
| `ROBOT_POLL_INTERVAL` | 3 сек | Як часто питати статус задачі | Збільш якщо архівація довга |
| `ROBOT_MAX_WAIT` | 900 сек | Максимальний час очікування задачі | Збільш для великих файлів |
| `ROBOT_PARALLELISM` | 1 | Скільки задач одночасно | Краще задавати CLI-прапором `--parallelism` |

### Приклади налаштувань

**Сценарій 1: Швидкий тест**

```bash
ROBOT_BATCH_DELAY=1 ROBOT_POLL_INTERVAL=1 \
  SERVER_ENV=dev scripts/run-robot-swarm.sh candidates.txt --parallelism 2
```

**Сценарій 2: Виробничий пуск на ніч**

```bash
ROBOT_BATCH_DELAY=2 ROBOT_POLL_INTERVAL=5 \
  SERVER_ENV=prod scripts/run-robot-swarm.sh candidates.txt --parallelism 4 --max-wait 1800
```

**Сценарій 3: Консервативний запуск**

```bash
ROBOT_BATCH_DELAY=10 ROBOT_POLL_INTERVAL=10 \
  SERVER_ENV=prod scripts/run-robot-swarm.sh candidates.txt --parallelism 1 --max-wait 1200
```

---

## 📊 Як читати результати?

### Приклад нормального запуску:

```
2026-03-05 14:30:00 [ROBOT] INFO: ========================================
2026-03-05 14:30:00 [ROBOT] INFO: 📋 BATCH STARTED. Candidates: 50
2026-03-05 14:30:00 [ROBOT] INFO:    List: 100, 101, 102, 103, 104, 105, 106, 107, 108, 109 ...
2026-03-05 14:30:00 [ROBOT] INFO:    Controls: parallelism=1, batch_delay=5s, poll_interval=3s, max_wait=900s
2026-03-05 14:30:00 [ROBOT] INFO: ========================================
2026-03-05 14:30:01 [ROBOT] INFO: --- Item 1/50 ---
2026-03-05 14:30:01 [ROBOT] INFO: ▶️ Processing Biblio #100...
2026-03-05 14:30:02 [ROBOT] INFO:    Task started: 7f3c8e0a-1234-5678. Waiting...
2026-03-05 14:30:05 [ROBOT] INFO: ✅ #100 SUCCESS! Handle: 20.500.12345/67890
2026-03-05 14:30:10 [ROBOT] INFO: --- Item 2/50 ---
2026-03-05 14:30:10 [ROBOT] INFO: ▶️ Processing Biblio #101...
2026-03-05 14:30:11 [ROBOT] INFO:    Task started: 9g4d9f1b-5678-9012. Waiting...
2026-03-05 14:30:18 [ROBOT] INFO: ✅ #101 SUCCESS! Handle: 20.500.12345/67891
...
2026-03-05 14:45:30 [ROBOT] INFO: ========================================
2026-03-05 14:45:30 [ROBOT] INFO: 🏁 BATCH COMPLETED.
2026-03-05 14:45:30 [ROBOT] INFO: 📊 Stats: {'SUCCESS': 48, 'FAILED': 1, 'SKIPPED': 1, 'LINKED': 0, 'TIMEOUT': 0}
2026-03-05 14:45:30 [ROBOT] INFO: 📝 See full details in robot_batch.log
```

### Що означають результати?

| Статус | Що це | Нормально? | Дія |
|--------|-------|-----------|-----|
| ✅ **SUCCESS** | Книга успішно архівована до DSpace | Так | Нічого |
| 🔄 **LINKED** | Книга вже в DSpace (дублікат), посилання пов'язано | Так | Нічого (автоматично виявлено) |
| ⚠️ **SKIPPED** | Книга вже в процесі (409 conflict) | Нормально | Спробуй пізніше |
| ❌ **FAILED** | Помилка при архівації | Залежить | Гляй деталі в логі |
| ⏱️ **TIMEOUT** | Чекала > 15 хвилин, не закінчилось | Рідко | Спробуй повторити |
| 🚫 **ERROR_CLIENT** | Невірний запит (400/404) | Ні | Проверь ID в candidates.txt |
| 🔌 **ERROR_CONN** | Мережева помилка | Ні | Перевір доступність API |

### Фінальна статистика

```
📊 Stats: {
  'SUCCESS': 48,         # Архівовано успішно
  'LINKED': 1,           # Вже було (дублікат посилано)
  'SKIPPED': 1,          # Вже в процесі (спробуй пізніше)
  'FAILED': 0,           # Помилки
  'TIMEOUT': 0,          # Таймауты
  'ERROR_CONN': 0,       # Мережеві помилки
  'ERROR_CLIENT': 0      # Помилки запиту
}
```

Якщо `SUCCESS + LINKED = очікуваний результат` → ✅ **Все добре!**

---

## 🔴 Типові проблеми

### Проблема: `container for ... not found on this node`

**Причина:** Swarm task `kdv-api` запущений на іншій node або сервіс не має running replica.

**Що робити:**
1. Перевір сервіс:
   ```bash
   docker service ls --filter name=kdv_integrator_event_kdv-api
   docker service ps kdv_integrator_event_kdv-api --no-trunc
   ```
2. Запускай wrapper на node, де реально працює task `kdv-api`.
3. Якщо сервіс упав, дивись логи:
   ```bash
   docker service logs kdv_integrator_event_kdv-api --tail=100
   ```

### Проблема: `candidates file not found on host`

**Причина:** wrapper читає файл зі сторони host repo до копіювання в контейнер.

**Що робити:**
1. Перевір шлях:
   ```bash
   ls -l candidates.txt
   ```
2. Передай явний файл:
   ```bash
   SERVER_ENV=dev scripts/run-robot-swarm.sh ./candidates.txt --dry-run
   ```

### Проблема: `ERROR_CONN — Connection Error`

**Причина:** API всередині контейнера недоступне або health-check не проходить.

**Що робити:**
1. Перевір сервіс: `docker service ls --filter name=kdv_integrator_event_kdv-api`
2. Глянь логи: `docker service logs kdv_integrator_event_kdv-api --tail=100`
3. Якщо потрібен redeploy/restart, узгодь дію окремо: `docker service update --force kdv_integrator_event_kdv-api`

### Проблема: `ERROR_CLIENT — Client Error`

**Причина:** Невірний ID або інша помилка запиту.

**Що робити:**
1. Перевір ID в `candidates.txt`.
2. Запусти dry-run, щоб переконатися, що список парситься:
   ```bash
   SERVER_ENV=dev scripts/run-robot-swarm.sh --dry-run candidates.txt
   ```
3. Глянь robot-log у контейнері:
   ```bash
   KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
   docker exec "${KDV_API_CID}" tail -50 logs/robot_batch.log
   ```

### Проблема: `TIMEOUT — waited 900s`

**Причина:** Завдання занадто довге: великий файл, черга optimizer-а або повільна DSpace.

**Що робити:**
1. Збільш `--max-wait`:
   ```bash
   SERVER_ENV=dev scripts/run-robot-swarm.sh candidates.txt --max-wait 1800
   ```
2. Для важких PDF зменш паралелізм:
   ```bash
   SERVER_ENV=dev scripts/run-robot-swarm.sh candidates.txt --parallelism 1 --max-wait 1800
   ```

---

## 💡 Практичні рецепти

### Рецепт 1: Тест з 10 книг перед великим пуском

```bash
head -10 candidates.txt > candidates_test.txt
SERVER_ENV=dev scripts/run-robot-swarm.sh --dry-run candidates_test.txt
SERVER_ENV=dev scripts/run-robot-swarm.sh candidates_test.txt --parallelism 1
```

### Рецепт 2: Запустити паралельно в tmux

```bash
tmux new-session -d -s robot \
  "cd /opt/kdv-integrator/kdv-integrator-event && SERVER_ENV=dev scripts/run-robot-swarm.sh candidates.txt --parallelism 4"

tmux attach -t robot
```

### Рецепт 3: Запустити в фоні з логом host-сесії

```bash
nohup env SERVER_ENV=dev scripts/run-robot-swarm.sh candidates.txt --parallelism 2 > robot_run.log 2>&1 &
tail -f robot_run.log
```

### Рецепт 4: Повторити тільки невдалі ID

```bash
KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
docker exec "${KDV_API_CID}" grep "FAILED\|TIMEOUT" logs/robot_batch.log | \
  grep -oP "#\d+" | sed 's/#//' > candidates_retry.txt

SERVER_ENV=dev scripts/run-robot-swarm.sh --dry-run candidates_retry.txt
SERVER_ENV=dev scripts/run-robot-swarm.sh candidates_retry.txt --parallelism 1 --max-wait 1800
```

---

## 🎯 Сценарії запуску

### Сценарій 1: Канарійський пуск

```bash
cat > candidates.txt << EOF
100-110
200, 210, 220
EOF

SERVER_ENV=dev scripts/run-robot-swarm.sh --dry-run candidates.txt
SERVER_ENV=dev scripts/run-robot-swarm.sh candidates.txt --parallelism 1
```

### Сценарій 2: Виробничий пуск на ніч

```bash
cat > candidates.txt << EOF
# Перша хвиля
100-500
# Друга хвиля
600-1100
EOF

ROBOT_BATCH_DELAY=2 ROBOT_POLL_INTERVAL=5 \
  SERVER_ENV=prod scripts/run-robot-swarm.sh candidates.txt --parallelism 4 --max-wait 1800
```

### Сценарій 3: Вибіркова архівація важких файлів

```bash
cat > candidates.txt << EOF
2000, 2001, 2005, 2010-2020, 2030
3500-3510
EOF

SERVER_ENV=prod scripts/run-robot-swarm.sh candidates.txt --parallelism 1 --max-wait 1800
```

---

## 📞 Що робити якщо щось зламалось?

1. **Гляємо host robot log**: `tail -f logs/robot_batch.log`
2. **Гляємо service log**: `docker service logs kdv_integrator_event_kdv-api --tail=100`
3. **Гляємо robot log у контейнері напряму**:
   ```bash
   KDV_API_CID="$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)"
   docker exec "${KDV_API_CID}" tail -f logs/robot_batch.log
   ```
4. **Перевіряємо candidates parsing**: `SERVER_ENV=dev scripts/run-robot-swarm.sh --dry-run candidates.txt`
5. **Якщо не допомогло** → дивись RUNBOOK_MAYDAY.md в розділі "Robot зависла".

---

## 📚 Дивись також

- [RUNBOOK_MAYDAY.md](RUNBOOK_MAYDAY.md) — Про надзвичайні ситуації
- [RUNBOOK_TESTING.md](RUNBOOK_TESTING.md) — Про тестування
- [ARCHITECTURE.md](ARCHITECTURE.md) — Деталі реалізації
