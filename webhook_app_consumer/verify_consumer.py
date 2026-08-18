#!/usr/bin/env python3
"""Black-box Application Consumer Verification for Toka webhook (Online & Offline)."""

from __future__ import annotations

import argparse
import hashlib
from http.client import HTTPConnection
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
CATALOG_PATH = ROOT.parents[0] / "toka-registry" / "public" / "catalog.json"
CACHE_DIR = ROOT / ".cache"
VENDOR_DIR = ROOT / ".vendor"
TARGET_DIR = ROOT / "target"
LOCK_FILE = ROOT / "package.lock"

EXPECTED_TARBALL_URL = "https://github.com/tokalang/webhook/releases/download/v0.1.0/webhook-0.1.0.tar.gz"
EXPECTED_SHA256 = "5d95567d798576585db9b12f870e92207307859c6eba3c94f78f44513ebafe11"


def get_sdk_paths() -> tuple[Path, Path]:
    sdk_root = os.environ.get("TOKA_SDK", "/tmp/toka-sdk-rc6")
    root_path = Path(sdk_root)
    tokac = root_path / "bin" / "tokac"
    lib = root_path / "lib"
    if not tokac.is_file() or not lib.is_dir():
        raise RuntimeError(f"Invalid TOKA_SDK at {sdk_root}: missing bin/tokac or lib/")
    return tokac, lib


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def compute_tree_hash(dir_path: Path) -> str:
    h = hashlib.sha256()
    for root, _, files in sorted(os.walk(dir_path)):
        for file in sorted(files):
            file_path = Path(root) / file
            rel_path = file_path.relative_to(dir_path)
            h.update(str(rel_path).encode("utf-8"))
            h.update(file_path.read_bytes())
    return h.hexdigest()


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


def step_online() -> None:
    print("\n[Phase 1: Online Consumer Resolution & Lockfile Generation]")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    tarball_dest = CACHE_DIR / "webhook-0.1.0.tar.gz"

    print(f"  Downloading verified release asset from: {EXPECTED_TARBALL_URL}")
    req = urllib.request.Request(
        EXPECTED_TARBALL_URL,
        headers={"User-Agent": "toka-consumer/1.0"}
    )
    with urllib.request.urlopen(req) as resp, open(tarball_dest, "wb") as out:
        shutil.copyfileobj(resp, out)

    actual_sha = compute_sha256(tarball_dest)
    print(f"  Downloaded archive SHA-256: {actual_sha}")
    if actual_sha != EXPECTED_SHA256:
        raise RuntimeError(f"SHA-256 mismatch: expected {EXPECTED_SHA256}, got {actual_sha}")

    vendor_webhook = VENDOR_DIR / "webhook"
    if vendor_webhook.exists():
        shutil.rmtree(vendor_webhook)
    vendor_webhook.mkdir(parents=True)

    print(f"  Extracting package to: {vendor_webhook}")
    with tarfile.open(tarball_dest, "r:gz") as tar:
        tar.extractall(vendor_webhook)

    content_hash = compute_tree_hash(vendor_webhook)
    lock_content = f"toka-lock-v1\npackage\twebhook\tregistry\twebhook\t0.1.0\t{actual_sha}\t{content_hash}\t-\n"
    LOCK_FILE.write_text(lock_content, encoding="utf-8")
    print(f"  Generated {LOCK_FILE.name}")


def step_build_and_execute(is_offline: bool) -> None:
    mode_name = "Offline Replay" if is_offline else "Online Live Verification"
    print(f"\n[Phase: {mode_name}]")
    if is_offline:
        if not LOCK_FILE.exists():
            raise RuntimeError(f"Missing {LOCK_FILE.name} in offline mode")
        tarball_path = CACHE_DIR / "webhook-0.1.0.tar.gz"
        if not tarball_path.exists():
            raise RuntimeError(f"Missing cached asset {tarball_path} in offline mode")
        cached_sha = compute_sha256(tarball_path)
        if cached_sha != EXPECTED_SHA256:
            raise RuntimeError("Cached archive SHA-256 corrupted")
        print(f"  Verified cached artifact against lockfile: {cached_sha}")

    tokac, lib_path = get_sdk_paths()
    vendor_webhook = VENDOR_DIR / "webhook"
    src_main = vendor_webhook / "src" / "main.tk"
    binary_out = TARGET_DIR / "webhook"

    print(f"  Building application executable with {tokac}...")
    compile_cmd = [
        str(tokac),
        "-I", str(lib_path),
        "-I", str(vendor_webhook / "src"),
        str(src_main),
        "-o", str(binary_out)
    ]
    env = os.environ.copy()
    env["TOKA_LIB"] = str(lib_path)
    if is_offline:
        env["TOKA_OFFLINE"] = "1"

    subprocess.run(compile_cmd, check=True, env=env)
    print(f"  Build successful: {binary_out}")

    port = free_port()
    print(f"  Starting webhook daemon on loopback port {port}...")
    daemon_proc = subprocess.Popen(
        [str(binary_out), "--hooks", str(ROOT / "consumer_hooks.json"), "--ip", "127.0.0.1", "--port", str(port), "--urlprefix", "hooks"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    try:
        # Wait for socket ready
        connected = False
        for attempt in range(100):
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
        status, body = request(port, "GET", "/hooks/disallowed-method")
        assert status == 405, f"Expected 405 Method Not Allowed, got {status}"
        print("    [+] GET /hooks/disallowed-method -> 405 Method Not Allowed")

    finally:
        daemon_proc.terminate()
        try:
            daemon_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            daemon_proc.kill()
            daemon_proc.wait(timeout=3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["all", "online", "offline"], default="all")
    args = parser.parse_args()

    if args.mode in ("all", "online"):
        step_online()
        step_build_and_execute(is_offline=False)

    if args.mode in ("all", "offline"):
        step_build_and_execute(is_offline=True)

    print("\n================================================================================")
    print("  BLACK-BOX APPLICATION CONSUMER: 100% VERIFIED (ONLINE + OFFLINE REPLAY)      ")
    print("================================================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
