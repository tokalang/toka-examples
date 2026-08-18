#!/usr/bin/env python3
"""Compile and execute the bounded direct-dispatch qualification."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile

from sdk import resolve_sdk

ROOT = Path(__file__).resolve().parents[1]


def run(argv: list[str], environment: dict[str, str]) -> None:
    completed = subprocess.run(argv, cwd=ROOT, env=environment, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(
            f"command failed: {' '.join(argv)}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


def main() -> int:
    sdk = resolve_sdk()
    environment = sdk.environment()

    with tempfile.TemporaryDirectory(prefix="toka-webhook-") as temporary:
        for name in ("config", "dispatch", "signatures", "loopback"):
            program = Path(temporary) / name
            run([str(sdk.tokac), "-I", str(sdk.library), "-I", str(ROOT / "src"),
                 str(ROOT / "tests" / (name + ".tk")), "-o", str(program)], environment)
            run([str(program)], environment)
    run([sys.executable, "-B", str(ROOT / "tests" / "cli_qualify.py")], environment)
    print("toka-webhook qualification: PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1)
