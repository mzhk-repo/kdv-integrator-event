# NIGHTWALKER — Ранбук по синхронізації каталогу

**Версія:** 0.1 (M7)  
**Останнє оновлення:** 2026-03-05  
**Мета:** Простий гайд по запуску та налаштуванню `scripts/nightwalker.py` для операторів

---

## 📋 Що це робить?

**NightWalker** — це "охоронець вночі", який:

1. 🔍 **Зупиняє записи** в каталозі Koha по одному
2. 🔗 **Перевіряє зв'язки** — чи синхронізовані з DSpace (чи метадані свіжі)
3. 🧟 **Знаходить "зомбі"** — файли без посилання (Handle)
4. 🔄 **Оновлює метадані** — якщо Koha новіший за DSpace (різниця > 5 сек)
5. 📝 **Логує все** в файл `logs/nightwalker.log`

**Коротко:** Перевіряє здоров'я синхронізації та виправляє (пів)автоматично.

---

## 🚀 Як запустити?

### Режим 1: Авто-сканування (весь каталог)

Просто запустити скрипт без параметрів:

```bash
docker compose exec kdv-api python3 -m src.nightwalker
```

**Що буде:**
- Почне з ID 1
- Пройде послідовно по всіх записах (ID 2, 3, 4...)
- Зупиниться після 201 пустого ID підряд (сигнал що база закінчилась)
- Кожні 100 записів виведе прогрес: `...scanned 100 records...`

### Режим 2: Сканування діапазону (конкретна частина)

Якщо потрібно перевірити тільки кілька сот записів:

```bash
docker compose exec kdv-api python3 -m src.nightwalker 5000 5100
```

**Що буде:**
- Проверит ID від 5000 до 5100 включно
- Зупиниться на кінці діапазону (не чекає 201 пустого ID)
- Швидче за авто-режим (менше записів для проверки)

---

## ⚙️ Налаштування затримок

Під час сканування скрипт робить паузи, щоб не перегрузити Koha/DSpace. Контролюються через `.env`:

```bash
# Auto-режим (весь каталог)
NIGHTWALKER_AUTO_DELAY=0.05        # паза 50ms між ID (за замовч.)

# Range-режим (конкретний діапазон)
NIGHTWALKER_RANGE_DELAY=0.10       # паза 100ms між ID (за замовч.)
```

**Коли збільшувати затримку?**
- Якщо Koha повільниший за обичне → `AUTO_DELAY=0.10` або `0.20`
- Якщо на продакшені → сіндіше консервативно: `AUTO_DELAY=0.15`

**Коли можна скоротити?**
- Тестування в локалі → `AUTO_DELAY=0.01`

---

## 📊 Як читати логи?

Логи пишуться в два місця одночасно:
- **Екран** (стандартний вивід)
- **Файл** `logs/nightwalker.log` (повна архіва)

### Приклад нормального запуску:

```
2026-03-05 14:23:45 [WALKER] INFO: ========================================
2026-03-05 14:23:45 [WALKER] INFO: 🌙 NIGHT WALKER STARTED (Auto-Discovery Mode)
2026-03-05 14:23:45 [WALKER] INFO: ℹ️  Will stop after 201 consecutive empty records.
2026-03-05 14:23:45 [WALKER] INFO: ℹ️  Auto scan delay: 0.05s
2026-03-05 14:23:45 [WALKER] INFO: ========================================
2026-03-05 14:23:50 [WALKER] INFO:    ...scanned 100 records...
2026-03-05 14:24:15 [WALKER] INFO:    ...scanned 200 records...
2026-03-05 14:25:10 [WALKER] INFO: 🛑 STOPPING: Hit 201 empty records in a row.
2026-03-05 14:25:10 [WALKER] INFO:    Last checked ID: 15342
2026-03-05 14:25:10 [WALKER] INFO: ========================================
2026-03-05 14:25:10 [WALKER] INFO: 🏁 WALKER FINISHED.
```

### Що означають символи?

| Символ | Значення | Дія |
|--------|----------|-----|
| 🌙 | Скрипт почався | Нормально |
| ℹ️ | Конфігурація | Інформація |
| 🔄 | `[SYNC NEEDED]` | Koha новіша, оновлюється |
| ✅ | `[SYNC SUCCESS]` | DSpace оновлена успішно |
| 🧟 | `[ZOMBIE]` | Файл без посилання (Handle) |
| ❌ | `[SYNC FAILED]` чи Error | Проблема, потрібна дія |
| 🛑 | STOPPING | Сканування закінчилось |
| 🏁 | FINISHED | Скрипт завершився |

---

## 🔴 Типові проблеми

### Проблема: "Error reading Koha #123"

**Причина:** Koha недіступна або несподіваний формат XML.

**Що робити:**
1. Перевіряємо статус контейнера: `docker compose ps`
2. Гляємо логи Koha: `docker compose logs kdv-koha --tail=50`
3. Перезапускаємо: `docker compose restart kdv-koha`
4. Спробуємо запустити знову

### Проблема: "SYNC FAILED"

**Причина:** Не вдалось оновити метадані в DSpace.

**Що робити:**
1. Перевіряємо статус DSpace: `docker compose ps kdv-dspace`
2. Гляємо його логи: `docker compose logs kdv-dspace --tail=100`
3. Це може бути:
   - DSpace недіступна (перезапустити)
   - Невірна структура метаданих (контактувати розробника)
   - Timeout (збільшити затримку і спробувати Range-режим для цього ID)

### Проблема: "ZOMBIE" (файл без Handle)

**Що це?** Файл завантажений до Koha, але нема посилання на DSpace.

**Що робити:**
- Якщо статус `processing` або `imported` → нормально, чекаємо архівму
- Якщо статус інший → потрібно вручну архівувати черезза `robot.py`

---

## 💡 Практичні рецепти

### Рецепт 1: Швидко перевірити конкретні записи

```bash
# Перевірити записи 100-150
docker compose exec kdv-api python3 -m src.nightwalker 100 150

# із більшою затримкою (якщо система завантажена)
NIGHTWALKER_RANGE_DELAY=0.20 docker compose exec kdv-api python3 -m src.nightwalker 100 150
```

### Рецепт 2: Запустити авто-режим на ночі (низькі затримки)

```bash
# В .env встановити:
NIGHTWALKER_AUTO_DELAY=0.02

# Запустити
docker compose exec kdv-api python3 -m src.nightwalker

# Результати буде в logs/nightwalker.log через кілька годин
```

### Рецепт 3: Скопіювати логи для аналізу

```bash
# Вивести весь лог на екран
docker compose exec kdv-api cat logs/nightwalker.log

# Вивести тільки помилки
docker compose exec kdv-api grep "❌\|Error\|FAILED" logs/nightwalker.log

# Скопіювати до себе на ПК
docker cp kdv-kdv-api-1:/app/logs/nightwalker.log ~/nightwalker_backup.log
```

---

## 🎯 Коли запускати?

| Ситуація | Команда | Режим |
|----------|---------|-------|
| Щоденна перевірка синху | `python3 -m src.nightwalker 1 500` | Range |
| Знайти всі "зомбі" | `python3 -m src.nightwalker` | Auto |
| Перевірити 1000+ записів | `python3 -m src.nightwalker 10000 12000` | Range |
| Після великого robot пуску | `python3 -m src.nightwalker` | Auto |
| На виході з дозвілу | `python3 -m src.nightwalker` | Auto (запустити перед сном) |

---

## 📞 Що робити якщо щось зламалось?

1. **Гляємо лог**: `docker compose logs kdv-api --tail=100`
2. **Гляємо nightwalker.log**: `docker compose exec kdv-api tail -f logs/nightwalker.log`
3. **Перезапускаємо контейнер**: `docker compose restart kdv-api`
4. **Спробуємо запустити знову** (часто helpe)
5. **Якщо не допомогло** → дивись RUNBOOK_MAYDAY.md в розділі "Nightwalker зависла"

---

## 📚 Дивись також

- [RUNBOOK_ROBOT.md](RUNBOOK_ROBOT.md) — Про масову архівацію книг
- [RUNBOOK_MAYDAY.md](RUNBOOK_MAYDAY.md) — Про надзвичайні ситуації
- [RUNBOOK_TESTING.md](RUNBOOK_TESTING.md) — Про тестування
- [ARCHITECTURE.md](ARCHITECTURE.md) — Деталі реалізації
