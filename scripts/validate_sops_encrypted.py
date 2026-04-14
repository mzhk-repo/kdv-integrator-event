#!/usr/bin/env python3
import re
import sys
from pathlib import Path

SOPS_DOTENV_META = re.compile(r"^sops_[A-Za-z0-9_]+=.*$")
PLAINTEXT_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")


def validate(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    has_enc = "ENC[" in text
    has_yaml_json_sops_block = re.search(r"(^|\n)sops:\n", text) is not None
    has_dotenv_sops_meta = any(SOPS_DOTENV_META.match(line) for line in lines)

    if not has_enc:
        print(f"[FAIL] {path}: missing ENC[...] markers")
        return 1

    if not (has_yaml_json_sops_block or has_dotenv_sops_meta):
        print(f"[FAIL] {path}: missing SOPS metadata")
        return 1

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if SOPS_DOTENV_META.match(stripped):
            continue
        if PLAINTEXT_ENV.match(stripped) and "ENC[" not in stripped:
            print(f"[FAIL] {path}: plaintext env line detected -> {stripped.split('=', 1)[0]}")
            return 1

    return 0


def main() -> int:
    files = [Path(p) for p in sys.argv[1:]]
    if not files:
        return 0
    rc = 0
    for f in files:
        rc |= validate(f)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
