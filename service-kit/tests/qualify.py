#!/usr/bin/env python3
"""Qualify service-kit against locked public registry packages."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import tempfile


PROJECT = Path(__file__).resolve().parents[1]
SOURCE_LOCK = PROJECT / "package.lock"
EXPECTED = {
    "router": ("router", "0.1.0"),
    "sqlite": ("sqlite", "0.1.0"),
}
SHA256 = re.compile(r"[0-9a-f]{64}")


class QualificationError(RuntimeError):
    pass


def run(argv: list[str], *, env: dict[str, str],
        cwd: Path = PROJECT) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
    )
    if result.returncode != 0:
        raise QualificationError(
            "command failed (%d): %s\nstdout:\n%s\nstderr:\n%s"
            % (result.returncode, shlex.join(argv), result.stdout, result.stderr)
        )
    return result


def find_tool(name: str, env: dict[str, str]) -> str | None:
    return shutil.which(name, path=env.get("PATH"))


def required_path(env: dict[str, str], variable: str) -> Path:
    value = env.get(variable, "").strip()
    if not value:
        raise QualificationError("set " + variable + " to the published Toka SDK path")
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise QualificationError(variable + " does not exist: " + str(path))
    return path


def resolve_toolchain(env: dict[str, str]) -> tuple[str, str, Path, Path, list[str]]:
    toka = env.get("TOKA", "").strip() or find_tool("toka", env)
    tokac = env.get("TOKAC", "").strip() or find_tool("tokac", env)
    if not toka or not Path(toka).expanduser().is_file():
        raise QualificationError("set TOKA or place toka on PATH")
    if not tokac or not Path(tokac).expanduser().is_file():
        raise QualificationError("set TOKAC or place tokac on PATH")

    library = required_path(env, "TOKA_LIB")
    runtime = library / "sys" / "toka_rt.o"
    if not runtime.is_file():
        raise QualificationError("TOKA_LIB has no sys/toka_rt.o")

    configured = env.get("CC", "").strip()
    if configured:
        compiler = shlex.split(configured)
        resolved = find_tool(compiler[0], env)
        if resolved is None:
            raise QualificationError("CC compiler was not found: " + compiler[0])
        compiler[0] = resolved
    else:
        compiler_path = find_tool("clang-20", env) or find_tool("clang", env)
        if compiler_path is None:
            raise QualificationError("service-kit requires CC, clang-20, or clang")
        compiler = [compiler_path]
    return (
        str(Path(toka).expanduser().resolve()),
        str(Path(tokac).expanduser().resolve()),
        library,
        runtime,
        compiler,
    )


def pkg_config(package: str, mode: str, env: dict[str, str]) -> list[str]:
    tool = find_tool("pkg-config", env)
    if tool is None:
        raise QualificationError("service-kit requires pkg-config")
    return shlex.split(run([tool, mode, package], env=env).stdout)


def optional_pkg_libs(package: str, env: dict[str, str]) -> list[str]:
    tool = find_tool("pkg-config", env)
    if tool is None:
        return []
    result = subprocess.run(
        [tool, "--libs", package],
        cwd=PROJECT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    return shlex.split(result.stdout) if result.returncode == 0 else []


def read_locked_roots(resolution: Path) -> dict[str, Path]:
    lock = resolution / "package.lock"
    state = resolution / ".toka"
    if not lock.is_file():
        raise QualificationError("service-kit requires its committed package.lock")
    lines = lock.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "toka-lock-v1":
        raise QualificationError("service-kit package.lock is not toka-lock-v1")

    roots: dict[str, Path] = {}
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) != 8 or fields[0] != "package":
            raise QualificationError("malformed service-kit package.lock entry")
        _, alias, kind, locator, resolved, archive_hash, content_hash, dependencies = fields
        if alias not in EXPECTED:
            continue
        expected_locator, expected_version = EXPECTED[alias]
        if (kind, locator, resolved) != (
            "registry", expected_locator, expected_version
        ):
            raise QualificationError(
                "%s must lock public registry %s@%s"
                % (alias, expected_locator, expected_version)
            )
        if not SHA256.fullmatch(archive_hash) or not SHA256.fullmatch(content_hash):
            raise QualificationError(alias + " lock hashes are not immutable SHA-256 values")
        if dependencies != "-":
            raise QualificationError(alias + " unexpectedly locks transitive dependencies")
        root = state / "packages" / (alias + "-" + resolved)
        if not root.is_dir():
            raise QualificationError(alias + " was not materialized under .toka/packages")
        roots[alias] = root

    if set(roots) != set(EXPECTED):
        missing = ", ".join(sorted(set(EXPECTED) - set(roots)))
        raise QualificationError("package.lock is missing: " + missing)
    return roots


def verify_cached_archives(resolution: Path) -> None:
    cache = resolution / ".toka" / "cache" / "archives"
    lock_lines = (resolution / "package.lock").read_text(
        encoding="utf-8"
    ).splitlines()[1:]
    for line in lock_lines:
        fields = line.split("\t")
        if len(fields) != 8 or fields[1] not in EXPECTED:
            continue
        archive_hash = fields[5]
        archive = cache / (archive_hash + ".tar.gz")
        if not archive.is_file():
            raise QualificationError("locked archive was not cached: " + fields[1])
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != archive_hash:
            raise QualificationError("cached archive hash changed: " + fields[1])


def qualify_materialization(
    label: str,
    roots: dict[str, Path],
    *,
    tokac: str,
    library: Path,
    runtime: Path,
    compiler: list[str],
    env: dict[str, str],
    work: Path,
) -> None:
    sqlite = roots["sqlite"]
    router = roots["router"]
    sqlite_bridge = sqlite / "native" / "sqlite_preflight.c"
    if not sqlite_bridge.is_file():
        raise QualificationError(
            "locked SQLite package has no native/sqlite_preflight.c"
        )

    phase = work / label
    phase.mkdir()
    bridge_object = phase / "sqlite_bridge.o"
    run(
        [
            *compiler,
            "-Wall",
            "-Wextra",
            "-Werror",
            "-c",
            str(sqlite_bridge),
            "-o",
            str(bridge_object),
            *pkg_config("sqlite3", "--cflags", env),
        ],
        env=env,
    )

    for source_name in ("dispatcher", "loopback", "shutdown"):
        program_ir = phase / (source_name + ".ll")
        program = phase / source_name
        run(
            [
                tokac,
                "-I",
                str(library),
                "-I",
                str(router / "lib"),
                "-I",
                str(sqlite / "lib"),
                "-I",
                str(PROJECT / "lib"),
                "--emit-llvm",
                str(PROJECT / "tests" / (source_name + ".tk")),
                "-o",
                str(program_ir),
            ],
            env=env,
        )
        link = [
            *compiler,
            str(program_ir),
            str(bridge_object),
            str(runtime),
            "-o",
            str(program),
            *pkg_config("sqlite3", "--libs", env),
            *optional_pkg_libs("openssl", env),
        ]
        if platform.system() == "Darwin":
            sdk = run(["xcrun", "--show-sdk-path"], env=env).stdout.strip()
            link.extend(["-isysroot", sdk])
        run(link, env=env)
        run([str(program)], env=env)


def main() -> int:
    env = dict(os.environ)
    toka, tokac, library, runtime, compiler = resolve_toolchain(env)
    env.update(
        {
            "TOKA": toka,
            "TOKAC": tokac,
            "TOKA_LIB": str(library),
            "CC": shlex.join(compiler),
        }
    )
    env.pop("TOKA_ROOT", None)
    env.pop("TOKA_OFFLINE", None)
    # This is specifically the public-registry cutover proof.
    env.pop("TOKA_REGISTRY_URL", None)

    locked = SOURCE_LOCK.read_bytes() if SOURCE_LOCK.is_file() else b""
    if not locked:
        raise QualificationError("service-kit requires its committed package.lock")

    with tempfile.TemporaryDirectory(prefix="toka-service-kit-") as temporary:
        work = Path(temporary)
        resolution = work / "registry-project"
        resolution.mkdir()
        shutil.copy2(PROJECT / "package.tk", resolution / "package.tk")
        shutil.copy2(SOURCE_LOCK, resolution / "package.lock")

        run([toka, "fetch"], env=env, cwd=resolution)
        if (resolution / "package.lock").read_bytes() != locked:
            raise QualificationError("online fetch changed package.lock")
        online_roots = read_locked_roots(resolution)
        verify_cached_archives(resolution)
        qualify_materialization(
            "online",
            online_roots,
            tokac=tokac,
            library=library,
            runtime=runtime,
            compiler=compiler,
            env=env,
            work=work,
        )

        shutil.rmtree(resolution / ".toka" / "packages")
        offline_env = dict(env)
        offline_env["TOKA_OFFLINE"] = "1"
        run([toka, "fetch"], env=offline_env, cwd=resolution)
        if (resolution / "package.lock").read_bytes() != locked:
            raise QualificationError("offline fetch changed package.lock")
        offline_roots = read_locked_roots(resolution)
        qualify_materialization(
            "offline",
            offline_roots,
            tokac=tokac,
            library=library,
            runtime=runtime,
            compiler=compiler,
            env=offline_env,
            work=work,
        )

    print("service-kit public registry qualification: PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, QualificationError, subprocess.TimeoutExpired) as error:
        print("FAIL: " + str(error))
        raise SystemExit(1)
