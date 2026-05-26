# Codex Context: Koha Export Module

> Source: `docs/koha-export/PRD_Koha_Export_Module.md` v2.2.
> Purpose: compact technical context for Codex before implementing roadmap/tasks.

---

## 1. Module Role

Koha Export Module is a separate CLI/batch module in KDV Integrator for periodic export of Koha bibliographic records to XLSX.

It is not a Flask endpoint and must not replace the current Koha → DSpace pipeline in `src/app.py`, `src/core.py`, `src/koha.py`.

Recommended commands:

```bash
python -m src.export_module --health-check
python -m src.export_module --dry-run
python -m src.export_module
python -m src.export_module --reset-pending <RUN_ID>
```

---

## 2. Current Architecture Decisions

- Export runs as CLI/batch, not as a long-running Flask endpoint.
- Google Drive for export is already mounted inside the container through rclone as `/mnt/drive`.
- XLSX export does not need a Google service account and does not need Google Drive API upload.
- Email is sent through Microsoft Graph API `sendMail`, not SMTP.
- Dry-run is enabled only by CLI flag `--dry-run`; `EXPORT_DRY_RUN` env is not used.
- Koha pagination priority: keyset pagination by `biblionumber > last_seen_id` with stable `biblionumber ASC`; offset pagination is fallback only.
- State tracking uses staged-idempotency, not a simple At-Most-Once promise.

---

## 3. Integration Boundaries

The module must be isolated under `src/export_module/`.

Do not change without a separate decision:

- `src/core.py` DSpace workflow;
- semantics of `956$u`, `956$p`, `956$q`;
- existing read-only `GoogleDriveSource` for PDFs from `956$u`/`956$q`.

Export reads ready `856` fields after successful archive:

- `856$u` where `$y == "Файл"` — direct file/bitstream;
- `856$u` where `$y == "Запис в репозиторії"` — DSpace record/handle.

---

## 4. Target Structure

```text
src/export_module/
  __main__.py
  orchestrator.py
  config.py
  db/
  koha/
  marc/
  xlsx/
  services/
    drive_mount_service.py
    graph_email_service.py
  observability/

config/
  marc_mapping.yaml
  export_dictionaries.yaml
```

---

## 5. Configuration

Required non-secret/runtime contract:

```bash
EXPORT_MODULE_ENABLED=true
EXPORT_GDRIVE_ROOT_PATH=/mnt/drive/KohaExports
MAX_RETRIES=3
MAX_ATTACHMENT_BYTES=15728640
PUSHGATEWAY_URL=http://pushgateway:9091
```

Microsoft Graph secrets/config:

```bash
GRAPH_TENANT_ID=00000000-0000-0000-0000-000000000000
GRAPH_CLIENT_ID=00000000-0000-0000-0000-000000000000
GRAPH_CLIENT_SECRET=REDACTED
GRAPH_SENDER_USER_ID=reports@example.org
GRAPH_TO=library-target@otherdomain.com
```

Rules:

- `EXPORT_GDRIVE_ROOT_PATH` must be inside `/mnt/drive`.
- Store Graph secrets only in SOPS/Swarm secret payload.
- Do not commit real secrets.
- Do not add `GDRIVE_SERVICE_ACCOUNT_FILE` for the export module.
- Do not add SMTP env (`SMTP_HOST`, `SMTP_PASSWORD`, etc.).

---

## 6. Koha Pagination

Preferred approach:

```python
def fetch_all_biblios_keyset(koha_client, page_size: int = 100):
    last_seen_id = 0
    while True:
        batch = koha_client.get(
            "/api/v1/biblios",
            params={
                "_per_page": page_size,
                "_order_by": "biblionumber",
                "biblionumber": {">": last_seen_id},
            },
        )
        if not batch:
            break
        for biblio in batch:
            yield biblio
        last_seen_id = max(int(item["biblionumber"]) for item in batch)
        if len(batch) < page_size:
            break
```

Important: `biblionumber > last_seen_id` syntax depends on the actual Koha endpoint. Implementation must add a contract test for the target Koha filter syntax.

---

## 7. XLSX Mapping And Dictionaries

`config/marc_mapping.yaml` must support:

- MARC-derived columns;
- static/spec columns without a MARC source;
- required columns;
- transforms;
- dictionary references.

Static columns example:

```yaml
static_columns:
  - name: "Бібліотека-отримувач"
    value: "REDACTED_LIBRARY_NAME"
    reason: "Required by downstream library import; no MARC source yet"

  - name: "Статус імпорту"
    value: "Новий"
    reason: "Fixed value for downstream import"
```

`config/export_dictionaries.yaml` must support Koha Authorized values recoding:

```yaml
authorized_values:
  itemtypes:
    BOOK: "Книга"
    BK: "Книга"
    CR: "Періодика"
    VM: "Відеоматеріал"

unknown_policy:
  authorized_value: "keep_code"
```

Rules:

- Do not hardcode `BOOK -> Книга` in Python.
- `dictionary` in mapping must reference a key from `export_dictionaries.yaml`.
- Unknown dictionary id is a validation error.
- `required_columns` must be a subset of `columns + static_columns`.
- If Koha Authorized values change, update `export_dictionaries.yaml` in the same iteration as mapping.

---

## 8. Google Drive Export Through `/mnt/drive`

Service: `ExportDriveMountService`.

Contract:

- root path: `EXPORT_GDRIVE_ROOT_PATH`, e.g. `/mnt/drive/KohaExports`;
- create year directory with `os.makedirs(..., exist_ok=True)`;
- copy XLSX to `.part` first;
- after successful copy/fsync, run `os.replace(.part, .xlsx)`;
- retry/idempotency: if final XLSX with same `run_id` already exists, skip copy;
- on error, remove `.part`.

Do not use:

- `google-api-python-client` for export upload;
- `google-auth` for export upload;
- Google OAuth scope `drive.file` for export upload.

---

## 9. Email Through Microsoft Graph API

Service: `GraphEmailService`.

Contract:

- API: Microsoft Graph `sendMail` over HTTPS;
- small XLSX (`<= MAX_ATTACHMENT_BYTES`) — Graph file attachment;
- large XLSX — email without attachment, with warning and GDrive mount path/link;
- retry on Graph `429`, `500`, `502`, `503`, `504`;
- do not log access token, client secret, or sensitive response headers.

Production recommendation:

- application permission `Mail.Send`;
- restrict sender mailbox through Exchange Application Access Policy.

---

## 10. SQLite State Tracking

Statuses:

```text
pending
xlsx_generated
gdrive_uploaded
email_sent
completed
failed
```

Key fields:

```text
biblionumber
run_id
status
retry_count
failed_reason
xlsx_filename
gdrive_file_path
gdrive_folder_path
email_sent_at
email_message_id
```

Recovery rules:

- before `gdrive_uploaded`: failure → `failed`, `retry_count += 1`;
- after `gdrive_uploaded`: reuse existing `gdrive_file_path`, continue with Graph email;
- after `email_sent`: finish `completed`, do not resend email.

---

## 11. Orchestrator Order

Dry-run must exit before any DB writes.

```text
1. run_id = uuid4; set_run_id(run_id)
2. config.validate()
3. candidates = filter_exportable_biblios(...)
4. if candidates empty → return 0
5. records = marc_parser.parse_all(...)
6. xlsx_path = xlsx_generator.generate(records, run_id)
7. if --dry-run → preserve dry copy, log would-do, return 0 without DB writes
8. db.insert_pending(candidates, run_id)
9. db.mark_xlsx_generated(run_id, basename)
10. drive_result = drive_mount_service.copy_to_mount(xlsx_path, run_id)
11. db.mark_gdrive_uploaded(...)
12. email_result = graph_email_service.send_via_graph(...)
13. db.mark_email_sent(...)
14. db.mark_completed(run_id)
15. metrics.push(...)
16. return 0
```

Delete `xlsx_path` in `finally`.

---

## 12. Dry-run

Dry-run is only started with:

```bash
python -m src.export_module --dry-run
```

Dry-run:

- generates XLSX;
- preserves a copy in `/tmp/dry_run`;
- does not modify SQLite;
- does not write to `/mnt/drive`;
- does not call MS Graph;
- logs `would_copy_to_gdrive_mount`, `would_send_graph_email`, `db_not_modified`.

---

## 13. Required Tests

- `test_static_columns_are_loaded`
- `test_authorized_value_dictionary_maps_code_to_label`
- `test_unknown_dictionary_id_raises`
- `test_required_columns_must_exist`
- `test_keyset_pagination_all_pages_processed`
- `test_drive_mount_copy_fail_marks_failed`
- `test_part_file_cleanup_on_copy_error`
- `test_graph_fail_after_drive_copy_keeps_gdrive_uploaded`
- `test_recovery_after_graph_success_marks_completed_without_resend`
- `test_dry_run_no_side_effects`
- `test_no_duplicate_export_on_second_run`
- `test_static_columns_and_authorized_values_in_xlsx`

---

## 14. Prohibitions

- Do not implement export as a Flask endpoint without a separate decision.
- Do not use Google Drive API/service account for export copy.
- Do not add SMTP transport.
- Do not read `EXPORT_DRY_RUN` from env.
- Do not hardcode Authorized values in Python.
- Do not write secrets to repository, logs, or SQLite.
- Do not change Koha → DSpace workflow in `src/core.py` as part of export module work.

---

## 15. Definition Of Done

- CLI works: `--health-check`, `--dry-run`, normal run, `--reset-pending`.
- Keyset pagination has a contract test for the target Koha endpoint.
- XLSX contains MARC-derived columns, static columns, and recoded Authorized values.
- Copy to `/mnt/drive` is idempotent and uses `.part` + atomic rename.
- MS Graph email supports attachment/link-only logic.
- Staged recovery does not duplicate copy/email.
- Dry-run has no side effects.
- Tests and whitespace checks pass.
- Docs/runbook/changelog are updated.
