# CHANGELOG 2026 VOL 05

Цей том продовжує `CHANGELOG_2026_VOL_04.md`, який досяг soft limit ротації.

## 2026-09-24 — Detect optimizer volume ownership drift

- **Context:** Two 109.47 MiB PDF jobs with 200 and 100 DPI reached Ghostscript but returned `missing_output`. Live diagnostics showed optimizer UID/GID `10001:10001`, while the existing shared volume and its `input`/`output` directories were `1000:1000` with mode `755`; `/ready` returned 503 while `/health` returned 200. The pre-existing volume masked the image-layer ownership set in the Dockerfile.
- **Change:** The optimizer container healthcheck now calls `/ready`. Before stack deployment, `scripts/deploy-orchestrator-swarm.sh` idempotently sets owner `10001:10001` on only the shared volume root, `input`, and `output` directories through a running task's mounted volume. It fails closed if an existing service has no local running task. Updated optimizer and script runbooks with automated and manual recovery procedures.
- **Verification:** Live read-only checks confirmed optimizer UID/GID `10001:10001`, API UID `0:0`, all three directories at `1000:1000` mode `755`, both write probes failing, `/ready` HTTP 503, `/health` HTTP 200, and both Swarm tasks on node `pinokew`. `bash -n scripts/deploy-orchestrator-swarm.sh`, `docker compose config -q`, and `git diff --check` passed. The deploy flow and volume were not changed on the live stack.
- **Risks:** The automated repair requires the deployment host to have a local running task mounting the volume. A different-node task fails the deployment before stack update so the wrong node-local volume is never changed.
- **Rollback:** Restore the `/health` healthcheck and remove the ownership repair function and runbook updates. The current volume still requires the documented manual ownership repair if this change is reverted.

## 2026-09-24 — Optional PDF rasterization DPI

- **Context:** Koha archival previously used the fixed Ghostscript `/ebook` optimization profile and offered no output-resolution choice.
- **Change:** Added optional allowlisted DPI selection for single-record and Robot Batch archiving. Selected values use full-page RGB `pdfimage24` rasterization at JPEG quality 85 and the PDF CropBox; omitted DPI preserves the existing searchable `pdfwrite /ebook` path. Invalid DPI is rejected before task creation, raster results must preserve page count and stay within the original file size, and failures fall back to the original. Task telemetry now reports requested and confirmed applied DPI. Updated the optimizer architecture, PRD, context, and runbook.
- **Verification:** `PYTHONPATH=.:kdv-optimizer .venv/bin/pytest -q tests/test_app.py tests/test_robot.py tests/test_core.py tests/test_pdf_optimizer_client.py tests/test_services.py` — 107 passed. Manual two-page Ghostscript smoke at 150 DPI produced two 600×800 RGB JPEG page images at 150 PPI (`pdfinfo`, `pdfimages -list`); default mode retained both searchable text strings (`pdftotext`). `node --check IntranetUser.js`, Python compilation, and `git diff --check` passed. Ruff could not run because the installed Snap launcher cannot write its required runtime directories.
- **Risks:** Explicit rasterization removes the text layer and does not run OCR; high DPI can time out and fall back to the original. Deploy the optimizer before the API and Koha UI.
- **Rollback:** Remove the DPI fields and UI controls, API/client propagation, raster Ghostscript path and associated telemetry/tests/docs; the default no-DPI path remains compatible with the previous behavior.

## 2026-09-22 — Cover generation respects PDF CropBox

- **Context:** PDF readers show the visible `CropBox`, but `pdf2image` used the default full `MediaBox`; for scanned PDFs with a larger source canvas this generated a cover outside the page the reader displays.
- **Change:** `CoverService._generate_image()` now passes `use_cropbox=True` to Poppler through `convert_from_path()`, so the generated cover matches the visible first PDF page.
- **Verification:** Focused mocked regression test asserts that the Poppler call enables `use_cropbox`; manual reproduction with the affected PDF and `pdftoppm -cropbox` matched the reader-visible page.
- **Risks:** PDFs whose CropBox intentionally excludes content will now produce the cropped, reader-visible area; this is the required rendering contract.
- **Rollback:** Remove `use_cropbox=True`, the focused regression test, and this changelog entry.
