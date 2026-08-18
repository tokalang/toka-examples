#!/usr/bin/env python3
"""True Black-box Application Consumer Verification for Toka webhook (using real toka fetch)."""

from __future__ import annotations

import argparse
from http.client import HTTPConnection
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
TARGET_DIR = ROOT / "target"
LOCK_FILE = ROOT / "package.lock"
TOKA_DIR = ROOT / ".toka"

EXPECTED_SHA256 = "886039fd12e5d770afdd262f4e3b43c4e8f24d67973986a0345ff4df98147a03"


def get_sdk() -> tuple[Path, Path, Path]:
    sdk_root = os.environ.get("TOKA_SDK", "/tmp/toka-sdk-rc6")
    root_path = Path(sdk_root)
    toka = root_path / "bin" / "toka"
    tokac = root_path / "bin" / "tokac"
    lib = root_path / "lib"
    if not toka.is_file() or not tokac.is_file() or not lib.is_dir():
        raise RuntimeError(f"Invalid TOKA_SDK at {sdk_root}: missing bin/toka, bin/tokac, or lib/")
    return toka, tokac, lib


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def request(port: int, method: str, path: str, headers: dict[str, str] | None = None, body: bytes = b"") -> tuple[int, bytes]:
    conn = HTTPConnection("127.0.0.1", port, timeout=2)
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    conn.request(method, path, body=body, headers=req_headers)
    resp = conn.getresponse()
    resp_body = resp.read()
    conn.close()
    return resp.status, resp_body


def test_daemon_loopback(binary_path: Path) -> None:
    port = free_port()
    print(f"  Starting webhook daemon on loopback port {port}...")
    daemon_proc = subprocess.Popen(
        [str(binary_path), "--hooks", str(ROOT / "consumer_hooks.json"), "--ip", "127.0.0.1", "--port", str(port), "--urlprefix", "hooks"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    try:
        # Wait for socket ready
        connected = False
        for _ in range(100):
            if daemon_proc.poll() is not None:
                stdout, stderr = daemon_proc.communicate()
                raise RuntimeError(f"Daemon exited prematurely (code {daemon_proc.returncode}):\nSTDOUT: {stdout.decode()}\nSTDERR: {stderr.decode()}")
            try:
                status, body = request(port, "GET", "/hooks/status", headers={"X-Consumer-Secret": "consumer-secret-2026"})
                connected = True
                break
            except OSError:
                time.sleep(0.05)

        if not connected:
            daemon_proc.terminate()
            stdout, stderr = daemon_proc.communicate()
            raise RuntimeError(f"Failed to connect to loopback webhook daemon on port {port}:\nSTDOUT: {stdout.decode()}\nSTDERR: {stderr.decode()}")

        print("  Daemon is listening and ready!")

        # 1. Health Status Request (GET 200 OK)
        print("  Testing GET /hooks/status...")
        status, body = request(port, "GET", "/hooks/status", headers={"X-Consumer-Secret": "consumer-secret-2026"})
        assert status == 200 and body == b"STATUS_HEALTHY\n", f"Expected STATUS_HEALTHY, got {(status, body)}"
        print("    [+] GET /hooks/status -> 200 OK: STATUS_HEALTHY")

        # 2. Authorized Deploy Request (POST with Secret 200 OK)
        print("  Testing POST /hooks/deploy with valid secret...")
        status, body = request(port, "POST", "/hooks/deploy", headers={"X-Consumer-Secret": "consumer-secret-2026"})
        assert status == 200 and body == b"CONSUMER_DEPLOY_SUCCESS\n", f"Expected CONSUMER_DEPLOY_SUCCESS, got {(status, body)}"
        print("    [+] POST /hooks/deploy -> 200 OK: CONSUMER_DEPLOY_SUCCESS")

        # 3. Unauthorized Deploy Request (POST with bad secret 400/403 Rejected)
        print("  Testing POST /hooks/deploy with forged secret...")
        status, body = request(port, "POST", "/hooks/deploy", headers={"X-Consumer-Secret": "wrong-secret"})
        assert status in (400, 403), f"Expected 400/403 rejection, got {status}"
        print(f"    [+] POST /hooks/deploy -> {status} Rejected (Trigger rule enforced)")

        # 4. Disallowed HTTP Method Request (GET on POST-only route 405 Method Not Allowed)
        print("  Testing GET /hooks/disallowed-method (POST-only route)...")
        status, body = request(port, "GET", "/hooks/disallowed-method", headers={"X-Consumer-Secret": "consumer-secret-2026"})
        assert status == 405, f"Expected 405 Method Not Allowed, got {status}"
        print("    [+] GET /hooks/disallowed-method -> 405 Method Not Allowed")

    finally:
        daemon_proc.terminate()
        try:
            daemon_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            daemon_proc.kill()
            daemon_proc.wait(timeout=3)


def run_online_phase(toka: Path, tokac: Path, lib: Path) -> None:
    print("\n[Phase 1: Online Resolution & Lockfile Generation via `toka fetch`]")
    # Clean workspace
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()
    if TOKA_DIR.exists():
        shutil.rmtree(TOKA_DIR)
    if TARGET_DIR.exists():
        shutil.rmtree(TARGET_DIR)

    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["TOKA_LIB"] = str(lib)
    # Prefer local registry gateway if running, else default to pkg.tokalang.dev
    if "TOKA_REGISTRY_URL" not in env:
        env["TOKA_REGISTRY_URL"] = "http://127.0.0.1:4044"

    print(f"  Running `{toka} fetch` (Registry: {env['TOKA_REGISTRY_URL']})...")
    res = subprocess.run([str(toka), "fetch"], cwd=ROOT, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"`toka fetch` failed:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}")

    print(f"  {res.stdout.strip()}")
    if not LOCK_FILE.exists():
        raise RuntimeError("`toka fetch` succeeded but did not generate package.lock")

    lock_content = LOCK_FILE.read_text(encoding="utf-8")
    if EXPECTED_SHA256 not in lock_content:
        raise RuntimeError(f"Lockfile does not contain expected archive SHA-256 {EXPECTED_SHA256}:\n{lock_content}")
    print("  [+] Verified package.lock contains correct digest and package metadata")

    resolved_pkg = TOKA_DIR / "packages" / "webhook-0.1.1"
    if not resolved_pkg.is_dir():
        raise RuntimeError(f"Expected resolved package directory {resolved_pkg} does not exist")

    # Build and test resolved application
    binary_out = TARGET_DIR / "webhook"
    print(f"  Building resolved application with {tokac}...")
    compile_cmd = [
        str(tokac),
        "-I", str(lib),
        "-I", str(resolved_pkg / "src"),
        str(resolved_pkg / "src" / "main.tk"),
        "-o", str(binary_out)
    ]
    subprocess.run(compile_cmd, check=True, env=env)
    print(f"  Build successful: {binary_out}")

    test_daemon_loopback(binary_out)


def run_offline_phase(toka: Path, tokac: Path, lib: Path) -> None:
    print("\n[Phase 2: Offline Replay Verification via `TOKA_OFFLINE=1 toka fetch`]")
    if not LOCK_FILE.exists():
        raise RuntimeError("Missing package.lock for offline test")

    # Wipe unpacked packages and compiled targets to prove reproduction strictly from cache & lockfile
    packages_dir = TOKA_DIR / "packages"
    if packages_dir.exists():
        shutil.rmtree(packages_dir)
    if TARGET_DIR.exists():
        shutil.rmtree(TARGET_DIR)

    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["TOKA_LIB"] = str(lib)
    env["TOKA_OFFLINE"] = "1"
    env["TOKA_REGISTRY_URL"] = "http://127.0.0.1:9999"  # Completely unreachable registry endpoint

    print(f"  Running `{toka} fetch` in offline mode (Network blocked, TOKA_OFFLINE=1)...")
    res = subprocess.run([str(toka), "fetch"], cwd=ROOT, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Offline `toka fetch` failed:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}")

    print(f"  {res.stdout.strip()}")
    resolved_pkg = TOKA_DIR / "packages" / "webhook-0.1.1"
    if not resolved_pkg.is_dir():
        raise RuntimeError(f"Offline fetch failed to unpack package directory {resolved_pkg}")

    print("  [+] Offline resolution succeeded strictly from cached archive and lockfile")

    # Build and test resolved application in offline mode
    binary_out = TARGET_DIR / "webhook"
    print(f"  Building offline application with {tokac}...")
    compile_cmd = [
        str(tokac),
        "-I", str(lib),
        "-I", str(resolved_pkg / "src"),
        str(resolved_pkg / "src" / "main.tk"),
        "-o", str(binary_out)
    ]
    subprocess.run(compile_cmd, check=True, env=env)
    print(f"  Build successful: {binary_out}")

    test_daemon_loopback(binary_out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["all", "online", "offline"], default="all")
    args = parser.parse_args()

    toka, tokac, lib = get_sdk()

    if args.mode in ("all", "online"):
        run_online_phase(toka, tokac, lib)

    if args.mode in ("all", "offline"):
        run_offline_phase(toka, tokac, lib)

    print("\n================================================================================")
    print("  BLACK-BOX APPLICATION CONSUMER: 100% VERIFIED VIA REAL `toka fetch`           ")
    print("================================================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
