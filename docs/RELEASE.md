### 1) Versioning

- Використовуй **SemVer**: `vMAJOR.MINOR.PATCH`.
- Теги **завжди** з префіксом `v`.

**Правила bump:**

- **PATCH** — bugfix без зміни API/контрактів.
- **MINOR** — нова сумісна функціональність.
- **MAJOR** — breaking change, який потребує міграційного плану.

---

### 2) Release types

- **Staging release**: з `main` (без тегу) для перевірки.
- **Production release**: **тільки** через тег `v*`.

> Якщо в репо реалізовано іншу модель (наприклад, `release/*` гілки) — дотримуйся її та онови цей файл.
> 

---

### 3) Pre-release checklist (must pass)

Перед створенням production релізу:

- [ ]  `git status` чистий.
- [ ]  `docker compose up -d --build` пройшов успішно.
- [ ]  `./scripts/healthcheck.sh` пройшов.
- [ ]  Перевірені логи: `docker compose logs --tail=200`.
- [ ]  Тести пройшли (якщо налаштовані): `pytest -q`.
- [ ]  Лінт пройшов (якщо налаштований): `ruff check .`.
- [ ]  Оновлено активний changelog-том у `CHANGELOGS/`:
    - є **Context / Change / Verification / Risks / Rollback**.
- [ ]  Якщо є breaking change:
    - додано секцію **Migration**,
    - оновлено `ARCHITECTURE.md` (якщо змінено контракт/архітектуру).

---

### 4) Create a release (tagging)

> Команди нижче — базові. Якщо CI вимагає інше (підписані теги, окремий release workflow) — виконуй вимоги CI.
> 

1) Переконайся, що локальна `main` актуальна:

```bash
git fetch --tags
git checkout main
git pull --rebase
```

2) Вибери нову версію `vX.Y.Z` згідно SemVer.

3) Створи annotated tag:

```bash
git tag -a vX.Y.Z -m "release: vX.Y.Z"
```

4) Запуш тег:

```bash
git push origin vX.Y.Z
```

5) Переконайся, що CI для тегу пройшов (build/test/scan/publish).

---

### 5) Release notes

У реліз-описі (GitHub Release або changelog-записі) вкажи:

- **What changed** (коротко)
- **Verification** (що реально запускалось)
- **Risks**
- **Rollback**

---

### 5.1) Canary flow (M7)

Мінімальний сценарій після staging прогону:

1. Дати доступ canary-групі (1-2 користувачі).
2. Пропустити контрольну вибірку записів через Koha UI/robot.
3. Спостерігати 24-48 год:
    - `./scripts/healthcheck.sh`
    - `docker compose logs --tail=200`
    - відсоток `success/error` у batch-логах.
4. Якщо деградації немає -> переходити до повного rollout.
5. Якщо є стабільний regression -> одразу rollback.

---

### 6) Rollback procedure (мінімум)

**Ключова ідея:** rollback має бути привʼязаний до попереднього стабільного **tag/digest**.

У поточному deploy path (`git checkout ref` + `docker compose up`) базовий rollback виконується через git tag; якщо використовується registry image, зафіксуйте digest/tag у compose/env і відкочуйтесь на нього.

- [ ]  Знайди попередній стабільний тег (наприклад, `vX.Y.(Z-1)`).
- [ ]  Переконайся, що артефакт доступний (image/tag/digest).
- [ ]  Перемкни деплой на попередній tag/digest (через deploy-репо / compose).
- [ ]  Перевір `./scripts/healthcheck.sh` і логи.
- [ ]  Зафіксуй інцидент і rollback в changelog.

Приклад rollback через git tag (поточний режим):

```bash
git fetch --tags --prune origin
git checkout -f vX.Y.Z
docker compose up -d --build --remove-orphans
./scripts/healthcheck.sh
docker compose logs --tail=200
```

Приклад rollback через image digest (якщо compose працює з image):

```bash
# приклад: export KDV_IMAGE=ghcr.io/org/kdv-api@sha256:...
docker compose pull
docker compose up -d --remove-orphans
./scripts/healthcheck.sh
docker compose logs --tail=200
```

---

### 7) Security notes (release gate)

- Ніяких секретів у клієнтському JS.
- CORS у production не `*`.
- Токени/паролі тільки через env/secret store.

---

### 8) If something is unclear

Якщо перед релізом неочевидно:

- що саме вважати production,
- які сервіси входять у compose,
- як саме публікується image,

…зупинись і постав 3–7 уточнюючих питань замість того, щоб “вгадувати процес”.