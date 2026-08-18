#!/usr/bin/env python3
"""True Black-box Application Consumer Verification for Toka task-runner (using real toka fetch)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
TARGET_DIR = ROOT / "target"
LOCK_FILE = ROOT / "package.lock"
TOKA_DIR = ROOT / ".toka"

EXPECTED_SHA256 = "087ff3cd90b6b0db7fdb0a197377ccfe3251c8498f10f9893fa603c319f1bcc8"
EXPECTED_VERSION = "0.1.1"


def log(msg: str) -> None:
    print(f"[CONSUMER] {msg}", flush=True)


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
    log(f"Checking global visibility of task-runner@{EXPECTED_VERSION} on {url}...")
    start = time.time()
    while time.time() - start < max_wait_secs:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "toka-consumer-verifier/1.0", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    packages = data.get("packages", [])
                    runner_pkg = next((p for p in packages if p.get("name") == "task-runner"), None)
                    if runner_pkg:
                        versions = runner_pkg.get("versions", [])
                        v011 = next((v for v in versions if v.get("version") == EXPECTED_VERSION), None)
                        if v011 and v011.get("sha256") == EXPECTED_SHA256:
                            log(f"  [+] Confirmed task-runner@{EXPECTED_VERSION} is live on pkg.tokalang.dev")
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


def test_blackbox_task_runner(binary_path: Path) -> None:
    log("  Testing blackbox task-runner application workflows...")

    # 1. Test CLI basics
    res = run_cmd([str(binary_path), "--version"])
    assert f"task-runner {EXPECTED_VERSION}" in res.stdout, "Version mismatch"

    res = run_cmd([str(binary_path), "--help"])
    assert "Usage:" in res.stdout, "Help mismatch"

    # 2. Test DAG execution with temporary workflow
    with tempfile.TemporaryDirectory(prefix="consumer-dag-test-") as tmp_dir:
        test_yaml = os.path.join(tmp_dir, "test.yaml")
        with open(test_yaml, "w") as f:
            f.write("""version: 1
tasks:
  step_init:
    program: "/usr/bin/true"
    args: []
  step_build:
    needs: ["step_init"]
    program: "/bin/echo"
    args: ["consumer workflow step 2"]
  step_final:
    needs: ["step_build"]
    program: "/usr/bin/true"
    args: []
""")

        # Plan check
        plan_res = run_cmd([str(binary_path), "-f", test_yaml, "--plan"])
        assert "1. step_init" in plan_res.stdout, "Plan missing step_init"
        assert "2. step_build" in plan_res.stdout, "Plan missing step_build"
        assert "3. step_final" in plan_res.stdout, "Plan missing step_final"

        # Dry-run check
        dry_res = run_cmd([str(binary_path), "-f", test_yaml, "--dry-run"])
        assert "[DRY-RUN]" in dry_res.stdout, "Dry run missing header"
        assert "Total: 3 | Passed: 3 | Failed: 0 | Skipped: 0" in dry_res.stdout, "Dry run summary mismatch"

        # Live execution check
        exec_res = run_cmd([str(binary_path), "-f", test_yaml])
        assert "Result: SUCCESS" in exec_res.stdout, "Execution failed"
        assert "consumer workflow step 2" in exec_res.stdout, "Echo step did not run"

    # 3. v0.1.1 Specific Behavioral Assertions (Global Pre-flight, Lexical CWD, String ENV)
    log("  Testing v0.1.1 behavioral fixes in resolved binary...")
    
    # Assertion A: Unrelated broken dependency must abort execution before child processes
    with tempfile.TemporaryDirectory(prefix="consumer-v011-preflight-") as tmp_dir:
        marker_file = os.path.join(tmp_dir, "must_never_exist.txt")
        broken_yaml = os.path.join(tmp_dir, "broken.yaml")
        with open(broken_yaml, "w") as f:
            f.write(f"""version: 1
tasks:
  wanted:
    program: "/usr/bin/touch"
    args: ["{marker_file}"]
  unrelated_broken:
    needs: ["ghost_dependency"]
    program: "/usr/bin/true"
""")
        res = run_cmd([str(binary_path), "-f", broken_yaml, "wanted"], check=False)
        assert res.returncode == 1, f"Expected returncode 1 on global broken dep, got {res.returncode}"
        assert not os.path.exists(marker_file), "CRITICAL: Subprocess spawned despite global invalid dependency!"
        assert "ghost_dependency" in res.stdout or "ghost_dependency" in res.stderr

    # Assertion B: Absolute cwd rejection
    with tempfile.TemporaryDirectory(prefix="consumer-v011-cwd-") as tmp_dir:
        abs_yaml = os.path.join(tmp_dir, "abs_cwd.yaml")
        with open(abs_yaml, "w") as f:
            f.write("""version: 1
tasks:
  abs_task:
    program: "/usr/bin/true"
    cwd: "/tmp"
""")
        res = run_cmd([str(binary_path), "-f", abs_yaml], check=False)
        assert res.returncode == 1, "Expected rejection for absolute cwd"
        assert "must be a relative path" in res.stdout or "must be a relative path" in res.stderr

    # Assertion C: Non-string env rejection
    with tempfile.TemporaryDirectory(prefix="consumer-v011-env-") as tmp_dir:
        env_yaml = os.path.join(tmp_dir, "bad_env.yaml")
        with open(env_yaml, "w") as f:
            f.write("""version: 1
tasks:
  env_task:
    program: "/usr/bin/true"
    env:
      PORT: 8080
""")
        res = run_cmd([str(binary_path), "-f", env_yaml], check=False)
        assert res.returncode == 1, "Expected rejection for non-string env"
        assert "must be a string" in res.stdout or "must be a string" in res.stderr

    log("  [+] Blackbox workflow and v0.1.1 behavioral assertions passed successfully!")


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

    # Step 3: Verify unpacked application package
    unpacked_pkg = TOKA_DIR / "packages" / f"task_runner-{EXPECTED_VERSION}"
    assert unpacked_pkg.is_dir(), f"Expected unpacked package at {unpacked_pkg}"
    assert (unpacked_pkg / "package.tk").is_file(), "Missing package.tk in unpacked package"
    assert (unpacked_pkg / "src" / "main.tk").is_file(), "Missing src/main.tk in unpacked package"

    # Step 4: Build consumer binary & unpacked application
    log("Step 4: Compiling consumer entrypoint and unpacked task-runner binary...")
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    
    # Compile consumer main
    consumer_bin = TARGET_DIR / "consumer_app"
    run_cmd([
        str(tokac),
        "-I", str(sdk_lib),
        str(ROOT / "src" / "main.tk"),
        "-o", str(consumer_bin)
    ])
    assert consumer_bin.is_file(), "Consumer binary not generated"

    # Compile application deliverable from unpacked package
    runner_bin = TARGET_DIR / "task-runner"
    run_cmd([
        str(tokac),
        "-I", str(sdk_lib),
        "-I", str(unpacked_pkg),
        str(unpacked_pkg / "src" / "main.tk"),
        "-o", str(runner_bin)
    ])
    assert runner_bin.is_file(), "task-runner binary not generated"

    # Step 5: Test online application execution
    log("Step 5: Executing blackbox application qualification tests (Online Phase)...")
    test_blackbox_task_runner(runner_bin)

    if not args.skip_offline:
        # Step 6: Test strict offline cache replay
        log("Step 6: Testing TOKA_OFFLINE=1 replay with unreachable registry...")
        
        # Remove unpacked packages and build artifacts
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
        assert (unpacked_pkg / "src" / "main.tk").is_file(), "Restored package missing main.tk"
        log("  [+] Successfully replayed package resolution from local .toka/cache")

        # Re-compile and re-test
        TARGET_DIR.mkdir(parents=True, exist_ok=True)
        run_cmd([
            str(tokac),
            "-I", str(sdk_lib),
            "-I", str(unpacked_pkg),
            str(unpacked_pkg / "src" / "main.tk"),
            "-o", str(runner_bin)
        ])
        log("Step 7: Executing blackbox application qualification tests (Offline Replay Phase)...")
        test_blackbox_task_runner(runner_bin)

    log(f"CONSUMER VERIFICATION FOR task-runner@{EXPECTED_VERSION} 100% COMPLETE & PASSING!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
