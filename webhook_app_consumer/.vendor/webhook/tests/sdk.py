"""Locate an installed Toka SDK for qualification."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil


@dataclass(frozen=True)
class TokaSdk:
    toka: Path
    tokac: Path
    library: Path

    @property
    def runtime(self) -> Path:
        return self.library / "sys" / "toka_rt.o"

    def environment(self) -> dict[str, str]:
        return os.environ | {"TOKA_LIB": str(self.library)}


def _executable(name: str, environment_name: str) -> Path | None:
    configured = os.environ.get(environment_name)
    if configured:
        return Path(configured)
    discovered = shutil.which(name)
    return Path(discovered) if discovered else None


def resolve_sdk() -> TokaSdk:
    configured_root = os.environ.get("TOKA_SDK")
    if configured_root:
        root = Path(configured_root)
        toka = root / "bin" / "toka"
        tokac = root / "bin" / "tokac"
        library = root / "lib"
    else:
        toka = _executable("toka", "TOKA")
        tokac = _executable("tokac", "TOKAC")
        configured_library = os.environ.get("TOKA_LIB")
        library = Path(configured_library) if configured_library else Path()

    missing = [
        name for name, path in (("toka", toka), ("tokac", tokac),
                                ("TOKA_LIB/lib", library),
                                ("toka_rt.o", (library / "sys" / "toka_rt.o") if library else None))
        if path is None
        or (name != "TOKA_LIB/lib" and not path.is_file())
        or (name == "TOKA_LIB/lib" and not path.is_dir())
    ]
    if missing:
        raise RuntimeError(
            "Toka SDK is incomplete (missing %s). Set TOKA_SDK to an extracted "
            "release archive, or set TOKA, TOKAC, and TOKA_LIB for an installed SDK."
            % ", ".join(missing)
        )
    return TokaSdk(toka=toka, tokac=tokac, library=library)
