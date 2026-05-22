import threading
import time
from pathlib import Path

import structlog

from kdv_optimizer.config import OptimizerConfig


logger = structlog.get_logger(__name__)


def _mb(size_bytes: int) -> float:
    return round(size_bytes / (1024 * 1024), 2)


class TTLJanitor(threading.Thread):
    def __init__(self, config: OptimizerConfig | None = None) -> None:
        super().__init__(daemon=True)
        self.config = config or OptimizerConfig()

    def run(self) -> None:
        while True:
            self.cleanup_once()
            time.sleep(self.config.TTL_CHECK_INTERVAL_SECONDS)

    def cleanup_once(self) -> None:
        now = time.time()
        for directory in (self.config.INPUT_DIR, self.config.OUTPUT_DIR):
            self._cleanup_directory(Path(directory), now)

    def _cleanup_directory(self, directory: Path, now: float) -> None:
        if not directory.exists() or not directory.is_dir():
            return

        for path in directory.iterdir():
            if not path.is_file():
                continue
            self._cleanup_file(path, now)

    def _cleanup_file(self, path: Path, now: float) -> None:
        try:
            stat = path.stat()
        except OSError:
            return

        age_s = int(now - stat.st_mtime)
        if age_s < self.config.TMP_TTL_SECONDS:
            return

        size_mb = _mb(stat.st_size)
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning(
                "optimizer_tmp_file_delete_failed",
                file=str(path),
                age_s=age_s,
                size_mb=size_mb,
                error=f"{type(exc).__name__}: {exc}",
            )
            return

        logger.warning(
            "optimizer_tmp_file_deleted",
            file=str(path),
            age_s=age_s,
            size_mb=size_mb,
        )
