import os
from pathlib import Path


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    try:
        value = int(raw_value)
    except ValueError:
        return default

    return max(value, minimum)


def _env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


class OptimizerConfig:
    OPTIMIZER_PORT: int
    DATA_DIR: str
    INPUT_DIR: str
    OUTPUT_DIR: str
    GS_TIMEOUT: int
    QPDF_ENABLED: bool
    TMP_TTL_SECONDS: int
    TTL_CHECK_INTERVAL_SECONDS: int

    def __init__(self) -> None:
        data_dir = os.getenv("DATA_DIR", "/data/kdv_optimize")

        self.OPTIMIZER_PORT = _env_int("OPTIMIZER_PORT", 5001)
        self.DATA_DIR = data_dir
        self.INPUT_DIR = os.getenv("INPUT_DIR", str(Path(data_dir) / "input"))
        self.OUTPUT_DIR = os.getenv("OUTPUT_DIR", str(Path(data_dir) / "output"))
        self.GS_TIMEOUT = _env_int("GS_TIMEOUT", 120)
        self.QPDF_ENABLED = _env_bool("QPDF_ENABLED", False)
        self.TMP_TTL_SECONDS = _env_int("TMP_TTL_SECONDS", 86400)
        self.TTL_CHECK_INTERVAL_SECONDS = _env_int(
            "TTL_CHECK_INTERVAL_SECONDS", 3600
        )
