#!/usr/bin/env python3
"""True Black-box Recursive Application Consumer Verification for Toka migrate:0.1.0."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
TARGET_DIR = ROOT / "target"
LOCK_FILE = ROOT / "package.lock"
TOKA_DIR = ROOT / ".toka"

MIGRATE_VERSION = "0.1.0"
MIGRATE_SHA256 = "ee7e34c1f630114b31593e8d684ba10e6b468935aee79c03b3acbbca2545a21c"

SQLITE_VERSION = "0.1.1"
SQLITE_SHA256 = "c398903ecac293d94c7b554a8d181814db5a3581faad6e7bb91dc217a5312801"


def log(msg: str) -> None:
    print(f"[MIGRATE-CONSUMER] {msg}", flush=True)


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
    log(f"Checking global visibility of migrate@{MIGRATE_VERSION} on {url}...")
    start = time.time()
    while time.time() - start < max_wait_secs:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "toka-consumer-verifier/1.0", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    packages = data.get("packages", [])
                    migrate_pkg = next((p for p in packages if p.get("name") == "migrate"), None)
                    if migrate_pkg:
                        versions = migrate_pkg.get("versions", [])
                        v010 = next((v for v in versions if v.get("version") == MIGRATE_VERSION), None)
                        if v010 and v010.get("sha256") == MIGRATE_SHA256:
                            log(f"  [+] Confirmed migrate@{MIGRATE_VERSION} is live on pkg.tokalang.dev")
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


def compile_migrate_from_packages(tokac: Path, sdk_lib: Path, migrate_pkg: Path, sqlite_pkg: Path) -> Path:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    c_shim = sqlite_pkg / "native" / "sqlite_preflight.c"
    shim_obj = TARGET_DIR / "sqlite_preflight.o"
    main_ll = TARGET_DIR / "migrate_main.ll"
    migrate_bin = TARGET_DIR / "migrate"
    runtime_obj = sdk_lib / "sys" / "toka_rt.o"

    # 1. Compile C shim for sqlite
    run_cmd(["clang", "-c", "-O2", str(c_shim), "-o", str(shim_obj)])

    # 2. Compile Toka migrate sources to LLVM IR
    run_cmd([
        str(tokac),
        "-I", str(sdk_lib),
        "-I", str(sqlite_pkg / "lib"),
        "-I", str(migrate_pkg),
        "--emit-llvm",
        str(migrate_pkg / "src" / "main.tk"),
        "-o", str(main_ll)
    ])
    assert main_ll.is_file(), "main.ll not generated"

    # 3. Link executable
    link_cmd = [
        "clang",
        str(main_ll),
        str(shim_obj),
        str(runtime_obj),
        "-o", str(migrate_bin)
    ]
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
    assert migrate_bin.is_file(), "migrate binary not generated"
    return migrate_bin


def test_blackbox_migrate_lifecycle(bin_path: Path) -> None:
    log("  Executing blackbox migration lifecycle tests on compiled binary...")

    # CLI basics
    res = run_cmd([str(bin_path), "--version"])
    assert f"migrate {MIGRATE_VERSION}" in res.stdout

    with tempfile.TemporaryDirectory(prefix="consumer-migrate-test-") as tmp_dir:
        db_file = os.path.join(tmp_dir, "prod.db")
        mig_dir = os.path.join(tmp_dir, "migrations")
        os.makedirs(mig_dir, exist_ok=True)

        with open(os.path.join(mig_dir, "0001_accounts.up.sql"), "w") as f:
            f.write("CREATE TABLE accounts (id INTEGER PRIMARY KEY, email TEXT NOT NULL);\nINSERT INTO accounts VALUES (1, 'user@example.com');")
        with open(os.path.join(mig_dir, "0002_profiles.up.sql"), "w") as f:
            f.write("CREATE TABLE profiles (id INTEGER PRIMARY KEY, account_id INTEGER, bio TEXT);\nINSERT INTO profiles VALUES (10, 1, 'Hello Toka');")

        # 1. Zero side-effect introspection
        uncreated_db = os.path.join(tmp_dir, "ghost.db")
        res = run_cmd([str(bin_path), "-d", uncreated_db, "-m", mig_dir, "status"])
        assert "[PENDING]  0001_accounts.up.sql" in res.stdout
        assert not os.path.exists(uncreated_db), "status created ghost.db on disk!"

        # 2. Plan
        res = run_cmd([str(bin_path), "-d", db_file, "-m", mig_dir, "plan"])
        assert "1. 0001_accounts.up.sql" in res.stdout
        assert "2. 0002_profiles.up.sql" in res.stdout
        assert "Total pending migrations to apply: 2" in res.stdout

        # 3. Apply
        res = run_cmd([str(bin_path), "-d", db_file, "-m", mig_dir, "apply"])
        assert "Applied '0001_accounts.up.sql' successfully." in res.stdout
        assert "Applied '0002_profiles.up.sql' successfully." in res.stdout
        assert "Successfully applied all 2 migration(s)." in res.stdout

        # Verify tables with sqlite3 native
        conn = sqlite3.connect(db_file)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cur.fetchall()]
        assert "accounts" in tables and "profiles" in tables and "_toka_migrations" in tables
        conn.close()

        # 4. Status
        res = run_cmd([str(bin_path), "-d", db_file, "-m", mig_dir, "status"])
        assert "[APPLIED]  0001_accounts.up.sql" in res.stdout
        assert "[APPLIED]  0002_profiles.up.sql" in res.stdout
        assert "Pending: 0" in res.stdout

        # 5. Verify
        res = run_cmd([str(bin_path), "-d", db_file, "-m", mig_dir, "verify"])
        assert "All 2 applied migrations verified successfully" in res.stdout

        # 6. Tamper Detection
        with open(os.path.join(mig_dir, "0001_accounts.up.sql"), "a") as f:
            f.write("\n-- Tampered")

        res = run_cmd([str(bin_path), "-d", db_file, "-m", mig_dir, "verify"], check=False)
        assert res.returncode == 1, "verify should fail on tampered file"
        assert "checksum mismatch" in res.stdout

        # Add 0003 and assert apply fails-closed in pre-flight
        with open(os.path.join(mig_dir, "0003_settings.up.sql"), "w") as f:
            f.write("CREATE TABLE settings (id INTEGER PRIMARY KEY);")

        res = run_cmd([str(bin_path), "-d", db_file, "-m", mig_dir, "apply"], check=False)
        assert res.returncode == 1, "apply should fail closed in pre-flight on tampered migration"
        assert "PRE-FLIGHT AUDIT FAILED" in res.stdout

        conn = sqlite3.connect(db_file)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cur.fetchall()]
        assert "settings" not in tables, "settings table must not exist due to fail-closed pre-flight"
        conn.close()

    log("  [+] Blackbox migration lifecycle assertions passed successfully!")


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
    log("Step 2b: Asserting recursive lockfile entries for migrate and sqlite...")
    assert MIGRATE_SHA256 in lock_content, f"package.lock missing migrate sha256 {MIGRATE_SHA256}"
    assert SQLITE_SHA256 in lock_content, f"package.lock missing sqlite sha256 {SQLITE_SHA256}"
    assert "migrate" in lock_content and "sqlite" in lock_content, "package.lock missing package names"
    log("  [+] Recursive package.lock successfully verified (migrate@0.1.0 + sqlite@0.1.1)")

    # Step 3: Verify unpacked packages
    migrate_pkg = TOKA_DIR / "packages" / f"migrate-{MIGRATE_VERSION}"
    sqlite_pkg = TOKA_DIR / "packages" / f"sqlite-{SQLITE_VERSION}"
    assert migrate_pkg.is_dir(), f"Expected unpacked package at {migrate_pkg}"
    assert sqlite_pkg.is_dir(), f"Expected unpacked package at {sqlite_pkg}"
    assert (migrate_pkg / "src" / "main.tk").is_file(), "Missing src/main.tk in migrate package"
    assert (sqlite_pkg / "lib" / "official" / "sqlite.tk").is_file(), "Missing sqlite.tk in sqlite package"

    # Step 4: Build & Test (Online Phase)
    log("Step 4: Compiling unpacked migrate deliverable and running lifecycle tests (Online Phase)...")
    migrate_bin = compile_migrate_from_packages(tokac, sdk_lib, migrate_pkg, sqlite_pkg)
    test_blackbox_migrate_lifecycle(migrate_bin)
    log("  [+] Online compilation and execution PASSED")

    if not args.skip_offline:
        # Step 5: Test strict offline cache replay
        log("Step 5: Testing TOKA_OFFLINE=1 replay with unreachable registry...")
        
        # Remove unpacked packages and build artifacts, preserve package.lock & .toka/cache
        shutil.rmtree(TOKA_DIR / "packages")
        shutil.rmtree(TARGET_DIR)
        assert not migrate_pkg.exists(), "Unpacked migrate should be deleted before offline test"
        assert not sqlite_pkg.exists(), "Unpacked sqlite should be deleted before offline test"

        # Re-fetch strictly offline pointing registry to unreachable sink
        offline_env = {
            "TOKA_OFFLINE": "1",
            "TOKA_REGISTRY_URL": "http://127.0.0.1:9"
        }
        run_cmd([str(toka), "fetch"], env=offline_env)
        assert migrate_pkg.is_dir(), "toka fetch failed to restore migrate from cache"
        assert sqlite_pkg.is_dir(), "toka fetch failed to restore sqlite from cache"
        log("  [+] Successfully replayed recursive package resolution from local .toka/cache")

        # Re-compile and re-test
        log("Step 6: Compiling unpacked migrate deliverable and running lifecycle tests (Offline Replay Phase)...")
        migrate_bin = compile_migrate_from_packages(tokac, sdk_lib, migrate_pkg, sqlite_pkg)
        test_blackbox_migrate_lifecycle(migrate_bin)
        log("  [+] Offline compilation and execution PASSED")

    log(f"RECURSIVE CONSUMER VERIFICATION FOR migrate@{MIGRATE_VERSION} & sqlite@{SQLITE_VERSION} 100% COMPLETE & PASSING!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
