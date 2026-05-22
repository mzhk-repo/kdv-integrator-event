#!/usr/bin/env python3
"""PoC benchmark PDF-оптимізаторів для ручної R&D Фази 0."""

import argparse
import json
import os
import subprocess
import time
import tracemalloc
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = PROJECT_ROOT / "scripts" / "benchmark_results"
OUTPUTS_DIR = RESULTS_DIR / "outputs"
PDFINFO_TIMEOUT_SECONDS = 10

EngineRunner = Callable[[Path, Path], None]


def _run_ghostscript(input_path: Path, output_path: Path) -> None:
    subprocess.run(
        [
            "nice",
            "-n",
            "15",
            "ionice",
            "-c",
            "3",
            "gs",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            "-dPDFSETTINGS=/ebook",
            "-dSAFER",
            "-dNOPAUSE",
            "-dBATCH",
            f"-sOutputFile={output_path}",
            str(input_path),
        ],
        check=True,
        timeout=120,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _run_pymupdf(input_path: Path, output_path: Path) -> None:
    import fitz

    with fitz.open(input_path) as doc:
        doc.save(output_path, garbage=4, deflate=True)


def _run_pikepdf(input_path: Path, output_path: Path) -> None:
    import pikepdf

    with pikepdf.open(input_path) as pdf:
        pdf.save(output_path, compress_streams=True)


def _run_qpdf(input_path: Path, output_path: Path) -> None:
    subprocess.run(
        ["qpdf", "--linearize", str(input_path), str(output_path)],
        check=True,
        timeout=120,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


ENGINES: dict[str, EngineRunner] = {
    "ghostscript": _run_ghostscript,
    "pymupdf": _run_pymupdf,
    "pikepdf": _run_pikepdf,
    "qpdf": _run_qpdf,
}


def _mb(size_bytes: int | None) -> float | None:
    if size_bytes is None:
        return None
    return round(size_bytes / (1024 * 1024), 2)


def _count_pages_with_pdfinfo(pdf_path: Path) -> int | None:
    try:
        result = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            check=True,
            timeout=PDFINFO_TIMEOUT_SECONDS,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception:
        return None

    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _exception_to_text(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr or stdout
        if details:
            return f"{type(exc).__name__}: {details[:500]}"
    return f"{type(exc).__name__}: {exc}"


def _safe_output_name(engine: str, pdf_path: Path) -> str:
    return f"{engine}_{pdf_path.stem}.pdf"


def run_benchmark(engine: str, pdf_path: str) -> dict:
    input_path = Path(pdf_path)
    if engine not in ENGINES:
        raise ValueError(f"Unsupported engine: {engine}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    output_path = OUTPUTS_DIR / _safe_output_name(engine, input_path)
    original_size = input_path.stat().st_size
    optimized_size = None
    exception = None

    tracemalloc.start()
    started_at = time.perf_counter()

    try:
        ENGINES[engine](input_path, output_path)
        if output_path.exists():
            optimized_size = output_path.stat().st_size
        else:
            raise RuntimeError(f"Output file was not created: {output_path}")
    except Exception as exc:
        exception = _exception_to_text(exc)
    finally:
        time_s = round(time.perf_counter() - started_at, 2)
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    reduction_pct = None
    if optimized_size is not None and original_size > 0:
        reduction_pct = round((1 - (optimized_size / original_size)) * 100, 2)

    return {
        "engine": engine,
        "file": input_path.name,
        "original_mb": _mb(original_size),
        "optimized_mb": _mb(optimized_size),
        "reduction_pct": reduction_pct,
        "pages": _count_pages_with_pdfinfo(input_path),
        "time_s": time_s,
        "peak_ram_mb": round(peak_bytes / (1024 * 1024), 2),
        "output_larger": (
            optimized_size > original_size if optimized_size is not None else False
        ),
        "exception": exception,
        "quality_ok": None,
    }


def _result_path(engine: str, pdf_path: Path) -> Path:
    return RESULTS_DIR / f"{engine}_{pdf_path.stem}.json"


def _discover_pdf_files(dataset_dir: Path) -> list[Path]:
    return sorted(path for path in dataset_dir.iterdir() if path.suffix.lower() == ".pdf")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Запускає PoC benchmark PDF-оптимізаторів."
    )
    parser.add_argument(
        "dataset_dir",
        nargs="?",
        default=os.getenv("DATASET_DIR"),
        help="Директорія з еталонними PDF. Також можна задати DATASET_DIR.",
    )
    args = parser.parse_args()

    if not args.dataset_dir:
        parser.error("Потрібно передати dataset_dir або задати DATASET_DIR")

    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    if not dataset_dir.is_dir():
        parser.error(f"Dataset directory does not exist: {dataset_dir}")

    pdf_files = _discover_pdf_files(dataset_dir)
    if not pdf_files:
        parser.error(f"Dataset directory does not contain PDF files: {dataset_dir}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    for pdf_file in pdf_files:
        for engine in ENGINES:
            result = run_benchmark(engine, str(pdf_file))
            result_path = _result_path(engine, pdf_file)
            result_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            status = "error" if result["exception"] else "ok"
            print(f"[{status}] {engine} {pdf_file.name} -> {result_path}")

    print("cat scripts/benchmark_results/*.json | jq -s 'sort_by(.reduction_pct) | reverse'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
