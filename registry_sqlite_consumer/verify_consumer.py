#!/usr/bin/env python3
"""True Black-box Library Consumer Verification for Toka sqlite:0.1.1 (using real toka fetch)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
TARGET_DIR = ROOT / "target"
LOCK_FILE = ROOT / "package.lock"
TOKA_DIR = ROOT / ".toka"

EXPECTED_SHA256 = "c398903ecac293d94c7b554a8d181814db5a3581faad6e7bb91dc217a5312801"
EXPECTED_VERSION = "0.1.1"


def log(msg: str) -> None:
    print(f"[SQLITE-CONSUMER] {msg}", flush=True)


def get_sdk() -> tuple[Path, Path, Path]:
    sdk_root = os.environ.get("TOKA_SDK", "/tmp/toka-sdk-rc6")
    root_path = Path(sdk_root)
    toka = root_path / "bin" / "toka"
    tokac = root_path / "bin" / "tokac"
    lib = root_path / "lib"
    if not toka.is_file() or not tokac.is_file() or not lib.is_dir():
        raise RuntimeError(f"Invalid TOKA_SDK at {sdk_root}: missing bin/toka, bin/tokac, or lib/")
    return toka, tokac, lib


def wait_for_catalog_deployment(max_wait_secs: int = 60) -> None:
    url = "https://pkg.tokalang.dev/catalog.json"
    log(f"Checking global visibility of sqlite@{EXPECTED_VERSION} on {url}...")
    start = time.time()
    while time.time() - start < max_wait_secs:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "toka-consumer-verifier/1.0", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    packages = data.get("packages", [])
                    sqlite_pkg = next((p for p in packages if p.get("name") == "sqlite"), None)
                    if sqlite_pkg:
                        versions = sqlite_pkg.get("versions", [])
                        v011 = next((v for v in versions if v.get("version") == EXPECTED_VERSION), None)
                        if v011 and v011.get("sha256") == EXPECTED_SHA256:
                            log(f"  [+] Confirmed sqlite@{EXPECTED_VERSION} is live on pkg.tokalang.dev")
                            return
        except Exception as e:
            log(f"  [-] Waiting for deployment: {e}")
        time.sleep(2)
    log("  [!] CDN deployment wait timeout; proceeding with direct fetch validation...")


def run_cmd(cmd: list[str], env: dict[str, str] | None = None, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)
    res = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        env=proc_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if check and res.returncode != 0:
        raise RuntimeError(f"Command failed (exit {res.returncode}): {' '.join(cmd)}\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
    return res


def compile_and_run(tokac: Path, sdk_lib: Path, unpacked_pkg: Path) -> None:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    c_shim = unpacked_pkg / "native" / "sqlite_preflight.c"
    shim_obj = TARGET_DIR / "sqlite_preflight.o"
    main_ll = TARGET_DIR / "main.ll"
    consumer_bin = TARGET_DIR / "sqlite_consumer_app"
    runtime_obj = sdk_lib / "sys" / "toka_rt.o"

    # 1. Compile C shim with sqlite3 flags
    run_cmd(["clang", "-c", "-O2", str(c_shim), "-o", str(shim_obj)])

    # 2. Compile Toka consumer code to LLVM IR
    run_cmd([
        str(tokac),
        "-I", str(sdk_lib),
        "-I", str(unpacked_pkg / "lib"),
        "--emit-llvm",
        str(ROOT / "src" / "main.tk"),
        "-o", str(main_ll)
    ])
    assert main_ll.is_file(), "main.ll not generated"

    # 3. Link executable with clang
    link_cmd = [
        "clang",
        str(main_ll),
        str(shim_obj),
        str(runtime_obj),
        "-o", str(consumer_bin)
    ]
    
    # Query pkg-config for sqlite3 and openssl flags
    try:
        sqlite_flags = subprocess.check_output(["pkg-config", "--libs", "sqlite3"], text=True).strip()
        link_cmd.extend(sqlite_flags.split())
    except Exception:
        link_cmd.append("-lsqlite3")

    try:
        ssl_flags = subprocess.check_output(["pkg-config", "--libs", "openssl"], text=True).strip()
        link_cmd.extend(ssl_flags.split())
    except Exception:
        pass

    if platform.system() == "Darwin":
        try:
            sdk_path = subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip()
            link_cmd.extend(["-isysroot", sdk_path])
        except Exception:
            pass

    run_cmd(link_cmd)
    assert consumer_bin.is_file(), "Consumer binary not generated"

    res = run_cmd([str(consumer_bin)])
    assert "public SQLite registry consumer passed" in res.stdout, "Consumer test did not pass"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-offline", action="store_true")
    args = parser.parse_args()

    toka, tokac, sdk_lib = get_sdk()
    log(f"Resolver / SDK: toka={toka}, tokac={tokac}, lib={sdk_lib}")

    # Step 0: Ensure deployment visibility
    wait_for_catalog_deployment()

    # Step 1: Clean slate
    log("Step 1: Cleaning previous local consumer artifacts...")
    if TARGET_DIR.exists():
        shutil.rmtree(TARGET_DIR)
    if TOKA_DIR.exists():
        shutil.rmtree(TOKA_DIR)
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()

    # Step 2: Online resolution
    log("Step 2: Running real online `toka fetch` against pkg.tokalang.dev...")
    online_env = {"TOKA_OFFLINE": "0"}
    if "TOKA_REGISTRY_URL" in os.environ:
        del os.environ["TOKA_REGISTRY_URL"]

    run_cmd([str(toka), "fetch"], env=online_env)
    assert LOCK_FILE.exists(), "package.lock was not created by toka fetch"

    lock_content = LOCK_FILE.read_text(encoding="utf-8")
    assert EXPECTED_SHA256 in lock_content, f"package.lock missing expected sha256 {EXPECTED_SHA256}"
    log(f"  [+] package.lock created with verified SHA-256 for v{EXPECTED_VERSION}")

    # Step 3: Verify unpacked library package
    unpacked_pkg = TOKA_DIR / "packages" / f"sqlite-{EXPECTED_VERSION}"
    assert unpacked_pkg.is_dir(), f"Expected unpacked package at {unpacked_pkg}"
    assert (unpacked_pkg / "package.tk").is_file(), "Missing package.tk in unpacked package"
    assert (unpacked_pkg / "lib" / "official" / "sqlite.tk").is_file(), "Missing sqlite.tk"

    # Step 4: Compile & Run (Online Phase)
    log("Step 4: Compiling consumer and executing SQLite lifecycle suite (Online Phase)...")
    compile_and_run(tokac, sdk_lib, unpacked_pkg)
    log("  [+] Online compilation and execution PASSED")

    if not args.skip_offline:
        # Step 5: Test strict offline cache replay
        log("Step 5: Testing TOKA_OFFLINE=1 replay with unreachable registry...")
        
        # Remove unpacked packages and build artifacts, preserve package.lock & .toka/cache
        shutil.rmtree(TOKA_DIR / "packages")
        shutil.rmtree(TARGET_DIR)
        assert not unpacked_pkg.exists(), "Unpacked package should be deleted before offline test"

        # Re-fetch strictly offline pointing registry to unreachable sink
        offline_env = {
            "TOKA_OFFLINE": "1",
            "TOKA_REGISTRY_URL": "http://127.0.0.1:9"
        }
        run_cmd([str(toka), "fetch"], env=offline_env)
        assert unpacked_pkg.is_dir(), "toka fetch failed to restore packages from cache in offline mode"
        assert (unpacked_pkg / "lib" / "official" / "sqlite.tk").is_file(), "Restored package missing sqlite.tk"
        log("  [+] Successfully replayed package resolution from local .toka/cache")

        # Re-compile and re-test
        log("Step 6: Compiling consumer and executing SQLite lifecycle suite (Offline Replay Phase)...")
        compile_and_run(tokac, sdk_lib, unpacked_pkg)
        log("  [+] Offline compilation and execution PASSED")

    log(f"CONSUMER VERIFICATION FOR sqlite@{EXPECTED_VERSION} 100% COMPLETE & PASSING!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
