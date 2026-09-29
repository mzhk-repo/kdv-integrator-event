# CHANGELOG 2026 VOL 05

Цей том продовжує `CHANGELOG_2026_VOL_04.md`, який досяг soft limit ротації.

## 2026-09-29 — Initialize cover host storage before Swarm deployment

- **Context:** Future cover and SQLite state bind mounts need their host directories prepared consistently before deployment.
- **Change:** Added `scripts/init-volume.sh` and called it from the orchestrator's existing deploy-adjacent phase before secret rendering and image builds. Both host paths are required and read from environment overrides or the selected dotenv file without sourcing it. The script creates or repairs cover root/assets modes to `0755` and incoming/state modes to `0700`, preserves existing ownership and files, and rejects missing, relative, top-level, overlapping, non-directory and symlink paths. Updated the environment template, contract, AI context and script runbook.
- **Verification:** `.venv/bin/python -m pytest -q tests/test_init_volume.py` — 15 passed, covering repeated initialization, mode repair, file/ownership preservation, path guards and orchestrator dotenv/override behavior. `bash -n scripts/init-volume.sh scripts/deploy-orchestrator-swarm.sh .env.example`, ShellCheck for both scripts and `git diff --check` passed. Filesystem tests used temporary directories; no live storage or Swarm deployment was changed.
- **Risks:** The deployment account needs creation/chmod permissions; initialization fails without them. Existing encrypted environments must define both host paths before their next Swarm deployment. Directory permissions support the current root Integrator; switching to a non-root writer requires an ownership migration. Host storage is node-local; prepare all eligible nodes or constrain service placement.
- **Rollback:** Remove the init script call and associated script/tests/documentation. Created directories and their contents are preserved; revert directory modes manually only after assessing running services.

## 2026-09-29 — External Cover Integrator environment contract (Task 0.1)

- **Context:** The external cover implementation plan needed a repository-specific environment and secret delivery contract before adding CDN/storage/state components. Conceptual Koha variable names differed from existing runtime settings, and CDN examples treated a base URL as a bare hostname.
- **Change:** Added six placeholder cover settings to `.env.example` for the full HTTPS CDN base URL, host/container cover storage, host/container state storage and record retry limit. Added `docs/external-cover-integrator/environment.md` documenting 29 new/reused parameters, existing SOPS/Vault-to-Docker-Secret delivery, component access boundaries and the legacy URL-to-SHA transition in `956$c`. Updated the concept, AI context and implementation plan with consistent URL examples, executable template validation and the outstanding service-account acceptance criterion.
- **Verification:** `bash -n .env.example` passed. Standard-library checks confirmed all 29 documented parameters exist, template keys are unique, new path placeholders are absolute, the CDN URL is HTTPS and the retry limit is positive. The validation embedded in Task 0.1 ran successfully; local contract links resolved; `src/config.py` inspection confirmed required Koha/DSpace endpoint settings without hostname defaults. `git diff --check` passed.
- **Risks:** These settings are configuration preparation; later phases must implement consumers, mounts and runtime validation. Service-account existence and folder-only access are not verified in an environment, so Task 0.1 is not marked fully accepted. Encrypted env files and deployed services were not changed.
- **Rollback:** Remove the cover placeholder block and environment contract, and revert the related concept/context/plan documentation changes. No runtime or infrastructure rollback is required.

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
