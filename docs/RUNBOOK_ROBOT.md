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

## 🚀 Крок 2: Запустити robot

### Простий запуск (за замовч. наслідування)

```bash
docker compose exec kdv-api python3 -m src.robot
```

**Що буде:**
- Прочитає `candidates.txt`
- Запустить архівацію **послідовно** (ID за ID, чекаючи результату)
- Показуватиме прогрес та результати на екран
- Напишефуть подробиці в `logs/robot_batch.log`

### Хочемо швидше? Паралелізм!

```bash
# Запустити 4 архівації одночасно
ROBOT_PARALLELISM=4 docker compose exec kdv-api python3 -m src.robot
```

**Умовно:**
- `ROBOT_PARALLELISM=1` → послідовно (за замовч., безпечно)
- `ROBOT_PARALLELISM=4` → по 4 одночасно (швидше, але більше навантаження)
- `ROBOT_PARALLELISM=8` → по 8 одночасно (для продакшену при малій базі)

> ⚠️ **Порада:** На слабкому лічинному сервері залишайте 1-2, на потужному 4-8.

---

## ⚙️ Налаштування (через .env)

```bash
# Затримка між стартами завдань (сек)
ROBOT_BATCH_DELAY=5.0              # за замовч. 5 сек

# Як часто перевіряти статус завдання (сек)
ROBOT_POLL_INTERVAL=3.0            # за замовч. 3 сек

# Максимум часу чекати на завдання (сек)
ROBOT_MAX_WAIT=900                 # за замовч. 15 хвилин (900 сек)

# Скільки завдань паралельно (тільки якщо > 1)
ROBOT_PARALLELISM=1                # за замовч. 1 (послідовно)
```

### Що означає кожна?

| Параметр | За замовч. | Що робить | Коли змінювати |
|----------|-----------|-----------|----------------|
| `BATCH_DELAY` | 5 сек | Пауза між стартами завдань | Збільш якщо Koha повільна, зменш якщо потрібна швидкість |
| `POLL_INTERVAL` | 3 сек | Як часто питати "готово?" | Збільш якщо архівація довга, зменш якщо важко дочекатись |
| `MAX_WAIT` | 900 сек | Скільки геврив чекати | Збільш для великих фото, зменш якщо не хочеш лежатись довге |
| `PARALLELISM` | 1 | Скільки одночасно | Збільш для прискорення (але обережно!) |

### Приклади налаштувань

**Сценарій 1: Швидкий тест (10 книг)**
```bash
ROBOT_PARALLELISM=2 ROBOT_BATCH_DELAY=1 ROBOT_POLL_INTERVAL=1 \
docker compose exec kdv-api python3 -m src.robot
```

**Сценарій 2: Виробничий пуск на ночі (1000+ книг)**
```bash
ROBOT_PARALLELISM=4 ROBOT_BATCH_DELAY=2 ROBOT_POLL_INTERVAL=5 ROBOT_MAX_WAIT=1800 \
docker compose exec kdv-api python3 -m src.robot
```

**Сценарій 3: Консервативний (коли база дуже завантажена)**
```bash
ROBOT_PARALLELISM=1 ROBOT_BATCH_DELAY=10 ROBOT_POLL_INTERVAL=10 ROBOT_MAX_WAIT=1200 \
docker compose exec kdv-api python3 -m src.robot
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

### Проблема: "ERROR_CONN — Connection Error"

**Причина:** API недіступна.

**Що робити:**
1. Перевіри статус контейнера: `docker compose ps kdv-api`
2. Перезапустіти: `docker compose restart kdv-api`
3. Гляй логи: `docker compose logs kdv-api --tail=50`

### Проблема: "ERROR_CLIENT — Client Error"

**Причина:** Невірний ID або інша помилка запiту.

**Що робити:**
1. Перевір ID в `candidates.txt`:
   ```bash
   # Гляни перший лог рядок для ID
   docker compose exec kdv-api tail -20 logs/robot_batch.log | grep "CLIENT"
   ```
2. Видали невірний ID з файлу, запустим знову

### Проблема: "TIMEOUT — waited 900s"

**Причина:** Завдання занадто довге (велики фоток, повільна DSpace).

**Що робити:**
1. Збільш `ROBOT_MAX_WAIT`: `ROBOT_MAX_WAIT=1800 docker compose exec kdv-api python3 -m src.robot`
2. Або запусти той же ID окремо: `docker compose exec kdv-api python3 -c "from src.robot import process_single_biblio; print(process_single_biblio('123'))"`

### Проблема: "FAILED — но error message"

**Причина:** Помилка при архівації (Koha, DSpace або валідація).

**Що робити:**
1. Гляй повний лог: `docker compose exec kdv-api grep "#123 FAILED" logs/robot_batch.log`
2. Перевір status ID в системі
3. Спробуй архівувати ID вручную для деталей
4. Якщо повторне велике помилка → дивись RUNBOOK_MAYDAY.md

---

## 💡 Практичні рецепти

### Рецепт 1: Тест з 10 книг перед великим пуском

```bash
# Лізати candidates.txt, залишити тільки 10 рядків
head -10 candidates.txt > candidates_test.txt

# Запустити
docker compose exec kdv-api python3 -c "from src.robot import run_batch; run_batch('candidates_test.txt')"

# Гляти результати
docker compose exec kdv-api tail -50 logs/robot_batch.log
```

### Рецепт 2: Запустити паралельно в фоні

```bash
# Запустити в tmux (якщо є)
tmux new-session -d -s robot \
  "cd /path/to/kdv && ROBOT_PARALLELISM=4 docker compose exec kdv-api python3 -m src.robot"

# Потім подивитись прогрес
tmux attach -t robot

# Або просто redirect в файл:
nohup docker compose exec kdv-api python3 -m src.robot > robot_run.log 2>&1 &
tail -f robot_run.log
```

### Рецепт 3: Повторити тільки неудачі

```bash
# Оригінальний lог
docker compose exec kdv-api grep "FAILED\|TIMEOUT" logs/robot_batch.log | \
  grep -oP "#\d+" | sed 's/#//' > candidates_retry.txt

# Це дасть тільки ID які провалилась, потім:
docker compose exec kdv-api python3 -c "from src.robot import run_batch; run_batch('candidates_retry.txt')"
```

### Рецепт 4: Під'їднання вставки з одного сеансу

```bash
# Запов'нити candidates.txt за запуском 1
docker compose exec kdv-api python3 -m src.robot

# Потім запов'нити FILE2:
echo "
500-600
" >> candidates.txt

# Запустити знову (буде нові + старі, але old дублі автоматично пропускаються)
docker compose exec kdv-api python3 -m src.robot
```

---

## 🎯 Сценарії запуску

### Сценарій 1: Канарійський пуск (10-50 книг)

```bash
# 1. Готуємо список
cat > candidates.txt << EOF
100-110
200, 210, 220
EOF

# 2. Запускаємо послідовно, детальна спостереження
docker compose exec kdv-api python3 -m src.robot

# 3. Перевіряємо результати
docker compose exec kdv-api tail -30 logs/robot_batch.log
```

### Сценарій 2: Виробничий пуск (500-1000 книг, ночі)

```bash
# 1. Готуємо велику список
cat > candidates.txt << EOF
# Перша хвиля
100-500
# Друга хвиля
600-1100
EOF

# 2. Запускаємо з паралелізмом, більш консервативним чекуванням
ROBOT_PARALLELISM=4 ROBOT_BATCH_DELAY=2 ROBOT_POLL_INTERVAL=5 \
docker compose exec kdv-api python3 -m src.robot

# 3. Контролюємо вживання ресурсів
docker stats kdv-api kdv-koha kdv-dspace

# 4. На утро перевіряємо результати
docker compose exec kdv-api grep "SUCCESS\|FAILED" logs/robot_batch.log | tail -20
```

### Сценарій 3: Вручню вибіркова архівація (спеціальна колекція)

```bash
# 1. Готуємо специфічний список (напр., з певної категорії)
cat > candidates.txt << EOF
# Особливі видання
2000, 2001, 2005, 2010-2020, 2030
# З нової колекції
3500-3510
EOF

# 2. Запускаємо з більш довгим MAX_WAIT (велики файли)
ROBOT_PARALLELISM=2 ROBOT_MAX_WAIT=1800 \
docker compose exec kdv-api python3 -m src.robot

# 3. Перевіряємо, чи все OK
docker compose exec kdv-api cat logs/robot_batch.log | tail -100
```

---

## 🔗 Зв'язок з NightWalker

Після великого robot пуску рекомендується запустити **NightWalker**, щоб перевірити синхронізацію:

```bash
# 1. Robot архівує
docker compose exec kdv-api python3 -m src.robot

# 2. Nightwalker перевіряє і виправляє (на ночі)
NIGHTWALKER_AUTO_DELAY=0.05 \
docker compose exec kdv-api python3 -m src.nightwalker
```

Дивись [RUNBOOK_NIGHTWALKER.md](RUNBOOK_NIGHTWALKER.md) для деталей.

---

## 📞 Що робити якщо щось зламалось?

1. **Гляємо лог**: `docker compose logs kdv-api --tail=100`
2. **Гляємо robot_batch.log**: `docker compose exec kdv-api tail -f logs/robot_batch.log`
3. **Перезапускаємо контейнер**: `docker compose restart kdv-api`
4. **Спробуємо запустити знову** (часто допомагає)
5. **Якщо не допомогло** → дивись RUNBOOK_MAYDAY.md в розділі "Robot зависла"

---

## 📚 Дивись також

- [RUNBOOK_NIGHTWALKER.md](RUNBOOK_NIGHTWALKER.md) — Про синхронізацію і перевірку
- [RUNBOOK_MAYDAY.md](RUNBOOK_MAYDAY.md) — Про надзвичайні ситуації
- [RUNBOOK_TESTING.md](RUNBOOK_TESTING.md) — Про тестування
- [ARCHITECTURE.md](ARCHITECTURE.md) — Деталі реалізації
