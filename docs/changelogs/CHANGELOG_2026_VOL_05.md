# CHANGELOG 2026 VOL 05

Цей том продовжує `CHANGELOG_2026_VOL_04.md`, який досяг soft limit ротації.

## 2026-09-22 — Cover generation respects PDF CropBox

- **Context:** PDF readers show the visible `CropBox`, but `pdf2image` used the default full `MediaBox`; for scanned PDFs with a larger source canvas this generated a cover outside the page the reader displays.
- **Change:** `CoverService._generate_image()` now passes `use_cropbox=True` to Poppler through `convert_from_path()`, so the generated cover matches the visible first PDF page.
- **Verification:** Focused mocked regression test asserts that the Poppler call enables `use_cropbox`; manual reproduction with the affected PDF and `pdftoppm -cropbox` matched the reader-visible page.
- **Risks:** PDFs whose CropBox intentionally excludes content will now produce the cropped, reader-visible area; this is the required rendering contract.
- **Rollback:** Remove `use_cropbox=True`, the focused regression test, and this changelog entry.
