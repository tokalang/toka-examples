#!/usr/bin/env python3
"""Verify online and archive-only offline replay without provider access."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parent
TOKA = os.environ.get("TOKA", "toka")


def run(args: list[str], env: dict[str, str] | None = None) -> None:
    subprocess.run(args, cwd=ROOT, env=env, check=True, timeout=120)


def clean_state() -> None:
    for path in (ROOT / "target", ROOT / ".toka"):
        shutil.rmtree(path, ignore_errors=True)
    (ROOT / ".toka_build_exe").unlink(missing_ok=True)


def main() -> int:
    clean_state()
    try:
        run([TOKA, "fetch"])
        expected_lock = (ROOT / "package.lock").read_bytes()
        run([TOKA, "build"])
        run([str(ROOT / "target" / "debug" / "llm_chat_cli"), "--help"])

        shutil.rmtree(ROOT / "target", ignore_errors=True)
        shutil.rmtree(ROOT / ".toka" / "packages", ignore_errors=True)
        shutil.rmtree(ROOT / ".toka" / "build", ignore_errors=True)
        (ROOT / ".toka_build_exe").unlink(missing_ok=True)
        offline = os.environ | {"TOKA_OFFLINE": "1"}
        run([TOKA, "fetch"], offline)
        if (ROOT / "package.lock").read_bytes() != expected_lock:
            raise RuntimeError("offline fetch changed package.lock")
        run([TOKA, "build"], offline)
        if (ROOT / "package.lock").read_bytes() != expected_lock:
            raise RuntimeError("offline build changed package.lock")
        run([str(ROOT / "target" / "debug" / "llm_chat_cli"), "--help"], offline)
    finally:
        clean_state()
    print("llm-chat-cli qualification: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
