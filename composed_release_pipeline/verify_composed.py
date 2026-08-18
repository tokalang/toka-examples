#!/usr/bin/env python3
"""Composed Release Pipeline Consumer Verification for toka-examples.

Orchestrates multiple verified application consumers via task-runner@0.1.2 DAG execution.
"""

from __future__ import annotations

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
REPO_ROOT = ROOT.parent
PIPELINE_YAML = REPO_ROOT / "composed-release-pipeline.yaml"
LOCK_FILE = ROOT / "package.lock"
TOKA_DIR = ROOT / ".toka"
TARGET_DIR = ROOT / "target"

EXPECTED_TASK_RUNNER_VERSION = "0.1.2"
EXPECTED_TASK_RUNNER_ARCHIVE_SHA256 = "3167714c0c651dfc7a6c9b85dfe5e33b4bdeec2ce13772598289314690919531"


def log(msg: str) -> None:
    print(f"[COMPOSED-PIPELINE] {msg}", flush=True)


def find_sdk() -> tuple[Path, Path, Path]:
    sdk_env = os.environ.get("TOKA_SDK")
    if sdk_env:
        root_path = Path(sdk_env)
        toka = root_path / "bin" / "toka"
        tokac = root_path / "bin" / "tokac"
        lib = root_path / "lib"
        if toka.is_file() and tokac.is_file() and lib.is_dir():
            return toka, tokac, lib

    toka_bin = shutil.which("toka")
    tokac_bin = shutil.which("tokac")
    toka_lib = os.environ.get("TOKA_LIB")
    if toka_bin and tokac_bin:
        toka = Path(toka_bin)
        tokac = Path(tokac_bin)
        lib = Path(toka_lib) if toka_lib else toka.parent.parent / "lib"
        if lib.is_dir():
            return toka, tokac, lib

    fallback = Path("/tmp/toka-sdk-rc6")
    if fallback.is_dir():
        return fallback / "bin" / "toka", fallback / "bin" / "tokac", fallback / "lib"

    raise RuntimeError("Could not find Toka SDK: set TOKA_SDK or add toka/tokac to PATH and set TOKA_LIB")


def run_cmd(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    unset_env_keys: list[str] | None = None
) -> subprocess.CompletedProcess[str]:
    exec_env = os.environ.copy()
    if unset_env_keys:
        for k in unset_env_keys:
            exec_env.pop(k, None)
    if env:
        exec_env.update(env)
    res = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else str(ROOT),
        env=exec_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if res.returncode != 0:
        raise RuntimeError(f"Command failed (exit {res.returncode}): {' '.join(cmd)}\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
    return res


def wait_for_catalog_deployment(max_wait_secs: int = 60) -> None:
    log(f"Checking public registry catalog for task-runner@{EXPECTED_TASK_RUNNER_VERSION}...")
    url = "https://pkg.tokalang.dev/catalog.json"
    start = time.time()
    while time.time() - start < max_wait_secs:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "toka-verifier"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    packages = data.get("packages", [])
                    runner_pkg = next((p for p in packages if p.get("name") == "task-runner"), None)
                    if runner_pkg:
                        versions = runner_pkg.get("versions", [])
                        v_entry = next((v for v in versions if v.get("version") == EXPECTED_TASK_RUNNER_VERSION), None)
                        if v_entry and v_entry.get("sha256") == EXPECTED_TASK_RUNNER_ARCHIVE_SHA256:
                            log(f"Public catalog verified with task-runner@{EXPECTED_TASK_RUNNER_VERSION} ({EXPECTED_TASK_RUNNER_ARCHIVE_SHA256[:16]}...)")
                            return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError(f"task-runner@{EXPECTED_TASK_RUNNER_VERSION} with expected SHA-256 not visible on {url} after {max_wait_secs}s")


def assert_lockfile_tsv() -> None:
    assert LOCK_FILE.is_file(), f"package.lock not found at {LOCK_FILE}"
    content = LOCK_FILE.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
    assert len(lines) == 2, f"Lockfile must contain exactly header and 1 package row, got {len(lines)} lines:\n{content}"
    assert lines[0] == "toka-lock-v1", f"Expected toka-lock-v1 header, got {lines[0]}"

    fields = lines[1].split("\t")
    assert len(fields) == 8, f"Expected 8 TSV fields in lockfile row, got {len(fields)}: {lines[1]}"
    record_type, binding, source_type, pkg_name, version, tarball_sha, tree_hash, deps = fields
    assert record_type == "package", f"Expected record_type 'package', got '{record_type}'"
    assert binding == "task_runner", f"Expected binding 'task_runner', got '{binding}'"
    assert source_type == "registry", f"Expected source_type 'registry', got '{source_type}'"
    assert pkg_name == "task-runner", f"Expected pkg_name 'task-runner', got '{pkg_name}'"
    assert version == EXPECTED_TASK_RUNNER_VERSION, f"Expected version '{EXPECTED_TASK_RUNNER_VERSION}', got '{version}'"
    assert tarball_sha == EXPECTED_TASK_RUNNER_ARCHIVE_SHA256, f"Expected archive sha '{EXPECTED_TASK_RUNNER_ARCHIVE_SHA256}', got '{tarball_sha}'"
    assert deps == "-", f"Expected direct deps '-', got '{deps}'"
    log("Strict 8-field toka-lock-v1 structure (exact single task_runner entry) verified successfully.")


def build_task_runner(tokac: Path, sdk_lib: Path) -> Path:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    main_ll = TARGET_DIR / "task_runner.ll"

    pkg_dir = TOKA_DIR / "packages" / f"task_runner-{EXPECTED_TASK_RUNNER_VERSION}"
    if not pkg_dir.is_dir():
        pkg_dir = TOKA_DIR / "packages" / f"task-runner-{EXPECTED_TASK_RUNNER_VERSION}"
    assert pkg_dir.is_dir(), f"task-runner package dir not found at {pkg_dir}"

    run_cmd([
        str(tokac),
        "-I", str(sdk_lib),
        "-I", str(pkg_dir),
        "--emit-llvm",
        str(pkg_dir / "src" / "main.tk"),
        "-o", str(main_ll)
    ], cwd=ROOT)

    runner_bin = TARGET_DIR / "task-runner"
    rt_obj = sdk_lib / "sys" / "toka_rt.o"
    cc = os.environ.get("CC", "clang")
    link_cmd = [cc, str(main_ll), str(rt_obj)]

    try:
        pkg = subprocess.run(["pkg-config", "--libs", "openssl"], stdout=subprocess.PIPE, text=True)
        if pkg.returncode == 0 and pkg.stdout.strip():
            link_cmd.extend(pkg.stdout.strip().split())
        else:
            link_cmd.extend(["-lssl", "-lcrypto"])
    except Exception:
        link_cmd.extend(["-lssl", "-lcrypto"])

    link_cmd.extend(["-lm", "-lpthread", "-ldl"])

    if platform.system() == "Darwin":
        try:
            sdk_path = subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip()
            link_cmd.extend(["-isysroot", sdk_path])
        except Exception:
            pass

    link_cmd.extend(["-o", str(runner_bin)])
    run_cmd(link_cmd, cwd=ROOT)
    assert runner_bin.is_file() and os.access(runner_bin, os.X_OK)
    return runner_bin


def main() -> int:
    toka, tokac, sdk_lib = find_sdk()
    log(f"Starting composed pipeline verification with toka={toka}, tokac={tokac}")

    assert PIPELINE_YAML.is_file(), f"Root pipeline configuration missing at {PIPELINE_YAML}"

    # Clean local fixture state
    shutil.rmtree(TARGET_DIR, ignore_errors=True)
    shutil.rmtree(TOKA_DIR, ignore_errors=True)
    if LOCK_FILE.is_file():
        LOCK_FILE.unlink()

    # Step 1: Wait for catalog & Online Fetch
    wait_for_catalog_deployment()

    log("=== Step 1: Online Resolution and Lock Generation ===")
    run_cmd([str(toka), "fetch"], cwd=ROOT, env={"TOKA_OFFLINE": "0"}, unset_env_keys=["TOKA_REGISTRY_URL"])
    assert_lockfile_tsv()

    # Step 2: Build task-runner binary
    log("=== Step 2: Build task-runner from Registry package ===")
    runner_bin = build_task_runner(tokac, sdk_lib)
    log(f"Successfully built task-runner binary: {runner_bin}")

    # Step 3: Verify topological plan (--plan)
    log("=== Step 3: Verify Deterministic Topological Execution Plan ===")
    res_plan = run_cmd([str(runner_bin), "--file", str(PIPELINE_YAML), "--plan"], cwd=REPO_ROOT)
    log(f"Plan output:\n{res_plan.stdout}")

    # Assert exact deterministic topological plan order:
    # 1. verify_notifier
    # 2. verify_sqlite
    # 3. verify_migrate (needs verify_sqlite)
    # 4. verify_webhook
    assert "1. verify_notifier" in res_plan.stdout, "Plan must list verify_notifier as step 1"
    assert "2. verify_sqlite" in res_plan.stdout, "Plan must list verify_sqlite as step 2"
    assert "3. verify_migrate" in res_plan.stdout, "Plan must list verify_migrate as step 3 (after verify_sqlite)"
    assert "4. verify_webhook" in res_plan.stdout, "Plan must list verify_webhook as step 4"

    # Step 4: Execute full DAG pipeline
    log("=== Step 4: Execute Composed DAG Pipeline ===")
    start_t = time.time()
    res_run = run_cmd([str(runner_bin), "--file", str(PIPELINE_YAML)], cwd=REPO_ROOT)
    elapsed = time.time() - start_t
    log(f"Pipeline executed in {elapsed:.2f}s:\n{res_run.stdout}")

    assert "Completed: 4/4 tasks succeeded" in res_run.stdout or "0 failed" in res_run.stdout or "SUCCESS" in res_run.stdout
    assert "verify_notifier" in res_run.stdout
    assert "verify_sqlite" in res_run.stdout
    assert "verify_migrate" in res_run.stdout
    assert "verify_webhook" in res_run.stdout
    log("All 4 application consumer verification tasks executed successfully under task-runner DAG.")

    # Step 5: Offline Replay Verification for task-runner
    log("=== Step 5: task-runner Offline Cache Replay (TOKA_OFFLINE=1 & Unreachable Registry URL) ===")
    packages_dir = TOKA_DIR / "packages"
    assert packages_dir.is_dir(), "packages dir must exist before offline replay test"
    shutil.rmtree(packages_dir)
    shutil.rmtree(TARGET_DIR, ignore_errors=True)

    # Fetch offline with unroutable registry URL (guaranteeing 0 network fallback)
    run_cmd(
        [str(toka), "fetch"],
        cwd=ROOT,
        env={"TOKA_OFFLINE": "1", "TOKA_REGISTRY_URL": "http://127.0.0.1:9"}
    )
    assert (packages_dir / f"task_runner-{EXPECTED_TASK_RUNNER_VERSION}").is_dir() or (packages_dir / f"task-runner-{EXPECTED_TASK_RUNNER_VERSION}").is_dir(), "task-runner was not unpacked from local cache in offline mode"

    # Rebuild & test plan in offline mode
    rebuilt_bin = build_task_runner(tokac, sdk_lib)
    res_offline_plan = run_cmd([str(rebuilt_bin), "--file", str(PIPELINE_YAML), "--plan"], cwd=REPO_ROOT)
    assert "1. verify_notifier" in res_offline_plan.stdout
    assert "3. verify_migrate" in res_offline_plan.stdout
    log("task-runner offline cache unpack, rebuild, and plan verification succeeded.")

    log("==========================================================")
    log("ALL COMPOSED RELEASE PIPELINE CHECKS PASSED")
    log("==========================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
