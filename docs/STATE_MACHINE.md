# Модель станів і правила ідемпотентності

Цей документ пояснює, як задачі та імпорти окремих записів проходять через
стани в KDV Integrator, а також правила обробки повторних запитів і
поведінку при перезапуску сервісу.

## Модель станів на рівні задачі

Задачі представляють собою роботу інтеграції, запитану через
`POST /integrate/{biblionumber}`. Задачу ідентифікує UUID (`task_id`), який
використовується як кореляційний ідентифікатор у всіх логах. Можливі стани:

```
queued ──▶ processing ──▶ success
   │            │            └─┐
   │            └─────────────┘
   └─────────▶ error
```

- **queued** – задача отримана та покладена в пам'ять, але ще не розпочата.
- **processing** – фоновий потік виконує бізнес-логіку; може оновлюватися
  прогрес (генерація обкладинки, завантаження в DSpace тощо).
- **success** – усі необхідні роботи завершено, поле `result` містить результат
  (наприклад, URL handle). Ендпоінт статусу поверне HTTP 200 з
  `"status":"success"`.
- **error** – сталася критична помилка; у полі `error` є повідомлення. Задачу
  можна повторити, знову відправивши POST (див. правила ідемпотентності нижче).

Logs produced by `src/tasks.py` and downstream components always prefix
messages with `[Task <task_id>]` to carry the correlation id.

As shown, once a task leaves `queued` it cannot return; error and success are
terminal states.

### Модель станів на рівні запису

У межах задачі імпорт одного бібліонумера проходить через більш деталізовані
стати для **книги**. Вони відслідковуються внутрішньо та з’являються в логах,
але поки що не доступні у публічному API.

- `processing` – початковий стан, поки готуються метадані та файли.
- `imported` – елемент успішно створено в DSpace.
- `linked_existing` – у DSpace вже був елемент із тим самим PID/handle;
  метадані оновлено відповідно.
- `error` – сталася невідновлювальна помилка (наприклад, мережа, парсинг Koha).
- `warning` – некритична проблема (не вдалося згенерувати обкладинку), але
  імпорт книги пройшов.

The book state moves monotonically; `error`/`warning` are terminal, while
`imported` and `linked_existing` both lead ultimately to task `success`.

## Правила ідемпотентності для `POST /integrate/{biblionumber}`

Клієнти можуть (і повинні) повторювати запит, якщо не отримали відповідь або
сталася мережна помилка. Сервер застосовує такі правила:

1. If there is already a **queued** or **processing** task for the same
   biblionumber (determined by scanning `TASKS` values), the request returns
   `202` with the existing `task_id` and does **not** enqueue a duplicate.
2. If the last task for that biblionumber ended with `success`, a new task
   is still created – we allow re‑import of the same record on demand.
3. If the last task ended with `error`, a new request will create a fresh task
   (errors are not memoized) but may also log a warning about the previous
   failure.
4. `task_id` values are globally unique; clients should always use the ID
   returned to poll status and should not assume sequential numbering.

Ці правила гарантують, що хвиля повторів не породить неконтрольовану
паралельну роботу, але все одно дозволяють відновлення після невдалих задач.

## Поведінка після перезапуску

Because `TASKS` is an in-memory dictionary, a process restart (intentional or
crash) wipes all queued/processing state. The following behavior applies:

- Усі задачі, які були в стані `queued` або `processing` під час вимкнення,
  **втрачаються**. Клієнти, що опитують такі `task_id`, отримають 404 від ендпоінту
  статусу.
- Відновлення очікується через зовнішні системи (Koha/DSpace). Якщо імпорт
  фактично завершився до аварії, книга вже буде в DSpace; клієнт може знову
  відправити POST з тим самим бібліонумером, і наведені вище правила
  ідемпотентності усунуть дублікати.
- Скрипт `robot.py` для пакетної обробки ідемпотентний і може запускатися знову;
  він повторно читає список бібліонумерів та ставить їх у чергу.
- `nightwalker.py` виявить сирітські файли в `Processed/` або `Error/` і може
  допомогти з очищенням після перезапуску.

> **Примітка:** міграція до персистентного сховища задач (Redis, база
> даних) передбачена в пізніших міленіумах roadmap, але цей документ фіксує
> поточну поведінку для M1.

## Посилання
- `src/tasks.py` – реалізація переходів станів і логування.
- `docs/ROADMAP.md` – чеклист міленіума M1.
- `docs/ACCEPTANCE_CRITERIA.md` – описує спостережувану послідовність станів у API.
