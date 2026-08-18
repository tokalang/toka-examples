#!/usr/bin/env python3
"""Online and Offline Verification Suite for notifier_app_consumer."""

from __future__ import annotations

import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import platform
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request


EXPECTED_NOTIFIER_VERSION = "0.1.1"
EXPECTED_NOTIFIER_ARCHIVE_SHA256 = "d449cf4de7cae04ff3c198d773302070e16802457a8a4a6621cdfd7148287c93"


def log(msg: str) -> None:
    print(f"[CONSUMER-VERIFY] {msg}", flush=True)


def get_sdk() -> tuple[Path, Path, Path]:
    sdk_root = os.environ.get("TOKA_SDK")
    if sdk_root:
        root_path = Path(sdk_root)
        toka = root_path / "bin" / "toka"
        tokac = root_path / "bin" / "tokac"
        lib = root_path / "lib"
        if toka.is_file() and tokac.is_file() and lib.is_dir():
            return toka, tokac, lib

    # Try resolving from PATH and TOKA_LIB (e.g. CI environments)
    toka_bin = shutil.which("toka")
    tokac_bin = shutil.which("tokac")
    toka_lib = os.environ.get("TOKA_LIB")
    if toka_bin and tokac_bin:
        toka = Path(toka_bin)
        tokac = Path(tokac_bin)
        lib = Path(toka_lib) if toka_lib else toka.parent.parent / "lib"
        if lib.is_dir():
            return toka, tokac, lib

    # Fallback to local default
    fallback = Path("/tmp/toka-sdk-rc6")
    if fallback.is_dir():
        return fallback / "bin" / "toka", fallback / "bin" / "tokac", fallback / "lib"

    raise RuntimeError("Could not find Toka SDK: set TOKA_SDK or add toka/tokac to PATH and set TOKA_LIB")


def wait_for_catalog_deployment(max_wait_secs: int = 60) -> None:
    log(f"Checking public registry catalog for notifier@{EXPECTED_NOTIFIER_VERSION}...")
    url = "https://pkg.tokalang.dev/catalog.json"
    start = time.time()
    while time.time() - start < max_wait_secs:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "toka-verifier"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    packages = data.get("packages", [])
                    notifier = next((p for p in packages if p.get("name") == "notifier"), None)
                    if notifier:
                        versions = notifier.get("versions", [])
                        v010 = next((v for v in versions if v.get("version") == EXPECTED_NOTIFIER_VERSION), None)
                        if v010 and v010.get("sha256") == EXPECTED_NOTIFIER_ARCHIVE_SHA256:
                            log(f"Public catalog verified with notifier@{EXPECTED_NOTIFIER_VERSION} ({EXPECTED_NOTIFIER_ARCHIVE_SHA256[:16]}...)")
                            return
        except Exception as e:
            pass
        time.sleep(2)
    log("Catalog check timed out; proceeding with fetch attempt.")


def run_cmd(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    res = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"Command failed (exit {res.returncode}): {' '.join(cmd)}\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
    return res


def parse_and_assert_lockfile(lock_path: Path) -> None:
    assert lock_path.is_file(), f"Lockfile not found at {lock_path}"
    content = lock_path.read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if line.strip() and not line.startswith("#")]
    
    assert lines and lines[0] == "toka-lock-v1", f"Expected toka-lock-v1 header, got: {lines[0] if lines else 'empty'}"
    
    records: dict[str, dict] = {}
    for line in lines[1:]:
        fields = line.split("\t")
        assert len(fields) == 8, f"Expected 8 TSV fields in lockfile row, got {len(fields)}: {line}"
        record_type, binding, source_type, pkg_name, version, tarball_sha, tree_hash, deps = fields
        assert record_type == "package"
        assert source_type == "registry"
        records[binding] = {
            "source_type": source_type,
            "pkg_name": pkg_name,
            "version": version,
            "tarball_sha": tarball_sha,
            "deps": deps
        }

    assert "notifier" in records, "notifier record missing from lockfile"
    assert records["notifier"]["version"] == EXPECTED_NOTIFIER_VERSION
    assert records["notifier"]["tarball_sha"] == EXPECTED_NOTIFIER_ARCHIVE_SHA256
    assert records["notifier"]["deps"] == "-"
    log("Strict 8-field toka-lock-v1 structure verified successfully.")


def generate_self_signed_cert(cert_path: Path, key_path: Path, common_name: str = "localhost") -> None:
    san_conf = f"""[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no
[req_distinguished_name]
CN = {common_name}
[v3_req]
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names
[alt_names]
DNS.1 = localhost
IP.1 = 127.0.0.1
"""
    with tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False) as f:
        f.write(san_conf)
        cnf_path = f.name
    try:
        run_cmd([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key_path),
            "-out", str(cert_path),
            "-days", "1",
            "-config", cnf_path
        ])
    finally:
        os.unlink(cnf_path)


class MockServerHandler(BaseHTTPRequestHandler):
    recorded_requests: list[dict] = []
    response_sequence: list[tuple[int, str, dict]] = []
    lock = threading.Lock()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""
        
        with self.lock:
            self.recorded_requests.append({
                "path": self.path,
                "headers": dict(self.headers),
                "body": body
            })
            if self.response_sequence:
                status, resp_body, headers = self.response_sequence.pop(0)
            else:
                status, resp_body, headers = 200, '{"status":"ok"}', {}

        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        if "Content-Type" not in headers:
            self.send_header("Content-Type", "application/json")
        resp_bytes = resp_body.encode("utf-8")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    def log_message(self, format, *args):
        pass


def run_http_server(handler_cls, is_ssl: bool = False, cert_path: Path | None = None, key_path: Path | None = None) -> tuple[HTTPServer, int, threading.Thread]:
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_port
    if is_ssl and cert_path and key_path:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, port, t


def build_consumer(consumer_dir: Path, tokac: Path, sdk_lib: Path) -> Path:
    target_dir = consumer_dir / "target"
    target_dir.mkdir(parents=True, exist_ok=True)
    main_ll = target_dir / "main.ll"
    
    pkg_dir = consumer_dir / ".toka" / "packages" / f"notifier-{EXPECTED_NOTIFIER_VERSION}"
    assert pkg_dir.is_dir(), f"notifier package dir not found at {pkg_dir}"

    run_cmd([
        str(tokac),
        "-I", str(sdk_lib),
        "-I", str(pkg_dir),
        "--emit-llvm",
        str(pkg_dir / "src" / "main.tk"),
        "-o", str(main_ll)
    ], cwd=consumer_dir)

    bin_path = target_dir / "notifier_app_consumer"
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

    link_cmd.extend(["-o", str(bin_path)])
    run_cmd(link_cmd, cwd=consumer_dir)
    assert bin_path.is_file() and os.access(bin_path, os.X_OK)
    return bin_path


def run_consumer_lifecycle_tests(consumer_bin: Path, work_dir: Path) -> None:
    sample_event = work_dir / "event.json"
    raw_event_text = '{"msg":"hello from notifier consumer","code":42}'
    sample_event.write_text(raw_event_text, encoding="utf-8")
    expected_sha = hashlib.sha256(raw_event_text.encode("utf-8")).hexdigest()

    # 1. Dry run
    sample_cfg = work_dir / "config_dryrun.yaml"
    sample_cfg.write_text("""endpoint: "https://api.example.com/consumer_webhook"
headers:
  Authorization: "Bearer secret-auth"
""", encoding="utf-8")
    res = run_cmd([str(consumer_bin), "--config", str(sample_cfg), "--event", str(sample_event), "--dry-run"])
    assert "=== NOTIFIER DRY RUN SIMULATION ===" in res.stdout
    assert f"Idempotency-Key: {expected_sha}" in res.stdout
    assert "authorization: [redacted]" in res.stdout.lower()

    # 2. Local Loopback HTTP 200 delivery
    MockServerHandler.recorded_requests.clear()
    MockServerHandler.response_sequence.clear()
    http_server, http_port, _ = run_http_server(MockServerHandler)
    try:
        http_cfg = work_dir / "config_http.yaml"
        http_cfg.write_text(f"""endpoint: "http://127.0.0.1:{http_port}/notify"
headers:
  X-Consumer: "active"
""", encoding="utf-8")
        res = run_cmd([str(consumer_bin), "--config", str(http_cfg), "--event", str(sample_event)])
        assert res.returncode == 0
        assert "[SUCCESS]" in res.stdout
        with MockServerHandler.lock:
            assert len(MockServerHandler.recorded_requests) == 1
            rec = MockServerHandler.recorded_requests[0]
            rec_headers_lower = {k.lower(): v for k, v in rec["headers"].items()}
            assert rec["path"] == "/notify"
            assert rec["body"] == raw_event_text
            assert rec_headers_lower.get("idempotency-key") == expected_sha
            assert rec_headers_lower.get("x-consumer") == "active"
    finally:
        http_server.shutdown()

    # 3. Local Loopback HTTPS 200 delivery with test CA
    cert_file = work_dir / "consumer_ca.crt"
    key_file = work_dir / "consumer_ca.key"
    generate_self_signed_cert(cert_file, key_file, common_name="localhost")

    MockServerHandler.recorded_requests.clear()
    MockServerHandler.response_sequence.clear()
    https_server, https_port, _ = run_http_server(MockServerHandler, is_ssl=True, cert_path=cert_file, key_path=key_file)
    try:
        https_cfg = work_dir / "config_https.yaml"
        https_cfg.write_text(f"""endpoint: "https://localhost:{https_port}/secure/notify"
ca_file: "{cert_file}"
""", encoding="utf-8")
        res = run_cmd([str(consumer_bin), "--config", str(https_cfg), "--event", str(sample_event)])
        assert res.returncode == 0
        assert "[SUCCESS]" in res.stdout
        with MockServerHandler.lock:
            assert len(MockServerHandler.recorded_requests) == 1
            rec = MockServerHandler.recorded_requests[0]
            assert rec["path"] == "/secure/notify"
    finally:
        https_server.shutdown()


def main() -> int:
    consumer_dir = Path(__file__).resolve().parent
    toka, tokac, sdk_lib = get_sdk()
    log(f"Starting consumer verification with toka={toka}, tokac={tokac}")

    wait_for_catalog_deployment()

    # Step 1: Clean and Online Fetch
    log("=== Step 1: Online Resolution and Lock Generation ===")
    shutil.rmtree(consumer_dir / "target", ignore_errors=True)
    shutil.rmtree(consumer_dir / ".toka", ignore_errors=True)
    lock_file = consumer_dir / "package.lock"
    if lock_file.exists():
        lock_file.unlink()

    env = os.environ.copy()
    env["PATH"] = f"{toka.parent}:{env.get('PATH', '')}"
    run_cmd([str(toka), "fetch"], cwd=consumer_dir, env=env)

    parse_and_assert_lockfile(lock_file)
    saved_lock_content = lock_file.read_text(encoding="utf-8")

    # Step 2: Build & Run Lifecycle Tests in Online Mode
    log("=== Step 2: Build and Validate Delivery Lifecycle (Online Mode) ===")
    consumer_bin = build_consumer(consumer_dir, tokac, sdk_lib)
    
    work_dir = Path(tempfile.mkdtemp(prefix="consumer_test_"))
    try:
        run_consumer_lifecycle_tests(consumer_bin, work_dir)
        log("Online lifecycle tests passed successfully.")

        # Step 3: Offline Replay with TOKA_OFFLINE=1
        log("=== Step 3: Offline Replay Verification (TOKA_OFFLINE=1) ===")
        # Verify cached archive exists
        archive_path = consumer_dir / ".toka" / "cache" / "archives" / f"{EXPECTED_NOTIFIER_ARCHIVE_SHA256}.tar.gz"
        assert archive_path.is_file(), f"Cached archive missing: {archive_path}"

        # Delete extracted packages and build artifacts, keep only archive cache
        shutil.rmtree(consumer_dir / "target", ignore_errors=True)
        shutil.rmtree(consumer_dir / ".toka" / "packages", ignore_errors=True)
        shutil.rmtree(consumer_dir / ".toka" / "build", ignore_errors=True)

        offline_env = env.copy()
        offline_env["TOKA_OFFLINE"] = "1"
        offline_env["TOKA_REGISTRY_URL"] = "http://127.0.0.1:9"  # Dummy unreachable endpoint

        run_cmd([str(toka), "fetch"], cwd=consumer_dir, env=offline_env)
        
        # Verify lockfile unchanged
        replayed_lock = lock_file.read_text(encoding="utf-8")
        assert replayed_lock == saved_lock_content, "Lockfile mutated during offline replay!"

        # Re-build from extracted offline packages
        offline_bin = build_consumer(consumer_dir, tokac, sdk_lib)
        run_consumer_lifecycle_tests(offline_bin, work_dir)
        log("Offline replay lifecycle tests passed with 100% fidelity.")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    log("==========================================================")
    log("ALL NOTIFIER CONSUMER CHECKS PASSED (Online & Offline)")
    log("==========================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
