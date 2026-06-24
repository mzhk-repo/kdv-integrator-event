# RUNBOOK: Google Drive source для `956$u` і `956$q`

## Призначення

Google Drive source дозволяє оператору Koha вказати PDF URL у `956$u` або в змішаному списку `956$q` через `|`. `kdv-api` скачує файл як read-only source у `GDRIVE_TMP_DIR`, завантажує його в DSpace і не змінює Google Drive.

## Інваріанти безпеки

- Google Drive використовується тільки для читання.
- Заборонено upload/update/delete/move/rename на Google Drive.
- Service account JSON не зберігається в `.env`, `.env.example`, `env.dev.enc`, `env.prod.enc` або git.
- Runtime secret path: `/run/secrets/gdrive_service_account_json`.
- Secret монтується тільки в `kdv-api`; `kdv-optimizer` не отримує Google credentials.
- Не використовувати `cat /run/secrets/gdrive_service_account_json`.

## Runtime ENV

```env
GDRIVE_ENABLED=true
GDRIVE_SERVICE_ACCOUNT_FILE=/run/secrets/gdrive_service_account_json
GDRIVE_TMP_DIR=/data/kdv_sources/gdrive
GDRIVE_ALLOWED_MIME_TYPES=application/pdf
GDRIVE_MAX_BYTES=262144000
GDRIVE_DOWNLOAD_TIMEOUT=300
GDRIVE_TMP_TTL_SECONDS=86400
```

## Підтримувані URL

- `https://drive.google.com/file/d/<file_id>/view`
- `https://drive.google.com/open?id=<file_id>`
- `https://drive.google.com/uc?id=<file_id>`
- ті самі URL з `resourcekey=<key>`

Folder links `https://drive.google.com/drive/folders/...` відхиляються. Google Docs/Sheets export у цій версії не підтримується: дозволено тільки PDF blob `application/pdf`.

## Перевірка secret без виводу payload

На Swarm node, де запущений task `kdv-api`:

```bash
KDV_API_CID=$(docker ps -q --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api | head -n 1)
test -n "${KDV_API_CID}"
docker exec "${KDV_API_CID}" sh -lc 'test -s /run/secrets/gdrive_service_account_json'
```

Для service spec:

```bash
docker service inspect kdv_integrator_event_kdv-api --format '{{json .Spec.TaskTemplate.ContainerSpec.Secrets}}'
docker service inspect kdv_integrator_event_kdv-optimizer --format '{{json .Spec.TaskTemplate.ContainerSpec.Secrets}}'
```

Очікування: `kdv-api` має secret target `gdrive_service_account_json`, `kdv-optimizer` не має Google Drive secret.

## Manual smoke для dev

1. Переконатися, що deploy виконаний через `scripts/deploy-orchestrator-swarm.sh` і `GDRIVE_ENABLED=true`.
2. Перевірити health:

```bash
docker exec "${KDV_API_CID}" sh -lc 'curl -fsS http://localhost:5000/kdv/api/health'
```

3. У dev Koha записі вказати primary PDF у `956$u` як Google Drive URL. Якщо файл link-shared з resource key, URL має містити `resourcekey`.
4. Запустити інтеграцію штатним UI/API/Robot шляхом.
5. Перевірити:
   - DSpace item створений;
   - primary bitstream має назву з Google Drive metadata, а не temp basename;
   - Koha отримала `856` для файлу і Handle;
   - Google Drive файл не змінено.
6. Для mixed additional вказати `956$q` як `local/path.pdf|https://drive.google.com/open?id=<file_id>`.
7. Перевірити, що additional Google PDF доданий у DSpace ORIGINAL, а local additional лишився backward-compatible.

## Логи

Безпечні Google Drive events у `kdv-api`:

- `Google Drive source metadata accepted`
- `Google Drive source cache hit`
- `Google Drive source downloaded`
- `Google Drive source failed`

Поля: `source_type=gdrive`, safe `file_id`, `mime_type`, `size`, `duration_ms`, `reason`. У логах не має бути повного Google Drive URL, `resourcekey`, OAuth token або service account JSON.

## Troubleshooting

| Симптом | Ймовірна причина | Дія |
|---|---|---|
| `Google Drive source is disabled` | `GDRIVE_ENABLED=false` | Увімкнути env і redeploy |
| `service account file is missing` | secret не змонтований у `kdv-api` | Перевірити service spec і `test -s` |
| `mime type is not allowed` | Google Docs/Sheets або не-PDF файл | Використати PDF blob-файл |
| `file is too large` | `size > GDRIVE_MAX_BYTES` | Зменшити файл або погодити новий ліміт |
| `file cannot be downloaded` | Drive capabilities забороняють download | Перевірити права sharing для service account |
| `Unsupported Google Drive URL format` | Непідтриманий URL | Використати `file/d`, `open?id` або `uc?id` |
| `folder URL is not supported` | Передано folder link | Вказати URL конкретного PDF файлу |
| 404/permission denied від Google API | Файл не shared із service account або потрібен `resourcekey` | Надати доступ service account або додати `resourcekey` |

## Rollback

1. Встановити `GDRIVE_ENABLED=false` у runtime env.
2. Виконати redeploy через `scripts/deploy-orchestrator-swarm.sh`.
3. Замінити Google Drive URL у `956$u`/`956$q` на локальні відносні шляхи.
4. Перевірити, що нові інтеграції йдуть local path flow.
5. Не видаляти Ansible Vault дані або Docker secrets без окремого підтвердження.

## Перевірки перед release

```bash
python3 -m py_compile src/services/sources.py src/core.py tests/test_services.py tests/test_core.py
python3 -m pytest tests/test_services.py -q
```

Якщо host Python не має `pymarc`, core/full pytest запускати в актуальному Docker image за repo-патерном.
