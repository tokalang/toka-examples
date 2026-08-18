#!/usr/bin/env python3
"""Black-box qualification for the configuration-driven webhook CLI."""

from __future__ import annotations

from http.client import HTTPConnection
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

from sdk import resolve_sdk


ROOT = Path(__file__).resolve().parents[1]
PREFIX = "callbacks"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def request(port: int, secret: str, path: str = "/hooks/deploy", body: bytes = b"{}", trace: str | None = None, method: str = "POST", extra: dict[str, str] | None = None, content_type: str = "application/json", response_header: str = "X-Webhook-Response") -> tuple[int, bytes, str | None]:
    path = path.replace("/hooks/", f"/{PREFIX}/", 1)
    connection = HTTPConnection("127.0.0.1", port, timeout=1)
    headers = {"X-Hook-Secret": secret}
    if trace is not None:
        headers["X-Trace"] = trace
    if extra is not None:
        headers.update(extra)
    headers["Content-Type"] = content_type
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    configured_header = response.getheader(response_header)
    body = response.read()
    connection.close()
    return response.status, body, configured_header


def qualify_yaml(binary: Path) -> None:
    port = free_port()
    server = subprocess.Popen(
        [str(binary), "--hooks", str(ROOT / "tests" / "hooks.yaml"), "--ip", "127.0.0.1", "--port", str(port), "--urlprefix", PREFIX],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        for _ in range(50):
            if server.poll() is not None:
                stdout, stderr = server.communicate()
                raise RuntimeError(f"YAML webhook exited early: {stdout!r} {stderr!r}")
            try:
                status, body, _ = request(port, "correct-horse", "/hooks/yaml?message=YAML+accepted")
                break
            except OSError:
                time.sleep(0.02)
        else:
            raise RuntimeError("YAML webhook did not accept a loopback connection")
        if status != 200 or body != b"YAML accepted":
            raise RuntimeError(f"YAML webhook response was {(status, body)!r}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=3)


def qualify_template(binary: Path, base_environment: dict[str, str]) -> None:
    port = free_port()
    with tempfile.TemporaryDirectory() as credentials_directory:
        credential = Path(credentials_directory) / "template-secret"
        credential.write_text("from-credential\n", encoding="utf-8")
        environment = base_environment.copy()
        environment["TOKA_WEBHOOK_TEMPLATE_VALUE"] = "from-env"
        environment["CREDENTIALS_DIRECTORY"] = credentials_directory
        server = subprocess.Popen(
            [str(binary), "--hooks", str(ROOT / "tests" / "hooks.template.json"), "--template", "--ip", "127.0.0.1", "--port", str(port), "--urlprefix", PREFIX],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            for _ in range(50):
                if server.poll() is not None:
                    stdout, stderr = server.communicate()
                    raise RuntimeError(f"template webhook exited early: {stdout!r} {stderr!r}")
                try:
                    status, body, _ = request(port, "template-secret", "/hooks/template")
                    break
                except OSError:
                    time.sleep(0.02)
            else:
                raise RuntimeError("template webhook did not accept a loopback connection")
            if status != 200 or body != b"from-env|from-cat|from-credential":
                raise RuntimeError(f"template webhook response was {(status, body)!r}")
        finally:
            server.terminate()
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=3)


def main() -> int:
    sdk = resolve_sdk()
    environment = sdk.environment()
    subprocess.run([str(sdk.toka), "build"], cwd=ROOT, env=environment, check=True)

    port = free_port()
    server = subprocess.Popen(
        [str(ROOT / "target" / "debug" / "webhook"), "--hooks", str(ROOT / "tests" / "hooks.json"), "--hooks", str(ROOT / "tests" / "hooks.additional.json"), "--ip", "127.0.0.1", "--port", str(port), "--header", "Access-Control-Allow-Origin=*", "--http-methods", "GET, POST", "--urlprefix", PREFIX],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        for _ in range(50):
            if server.poll() is not None:
                stdout, stderr = server.communicate()
                raise RuntimeError(f"webhook exited early: {stdout!r} {stderr!r}")
            try:
                status, body, configured_header = request(port, "correct-horse")
                break
            except OSError:
                time.sleep(0.02)
        else:
            raise RuntimeError("webhook did not accept a loopback connection")
        if (status, body) != (202, b"deployment accepted"):
            raise RuntimeError(f"authorized webhook response was {(status, body)!r}")
        if configured_header != "configured":
            raise RuntimeError(f"configured response header was {configured_header!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/additional")
        if status != 200 or body != b"additional accepted":
            raise RuntimeError(f"additional hooks file response was {(status, body)!r}")
        _, _, cors_header = request(port, "correct-horse", response_header="Access-Control-Allow-Origin")
        if cors_header != "*":
            raise RuntimeError(f"global CORS header was {cors_header!r}")
        status, body, _ = request(port, "wrong")
        if status != 403 or body != b"hook trigger rule rejected request":
            raise RuntimeError(f"unauthorized webhook response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/env")
        if status != 200 or body != b"webhook\n":
            raise RuntimeError(f"per-child environment response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/query?message=hello+query")
        if status != 200 or body != b"hello query":
            raise RuntimeError(f"URL query command argument response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/references", b"raw-body", "trace")
        if status != 200 or body != b"trace|raw-body|POST":
            raise RuntimeError(f"header/body/method command argument response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/payload", b'{"repository":{"name":"toka"}}')
        if status != 200 or body != b"toka":
            raise RuntimeError(f"JSON payload command argument response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/get-only", method="GET")
        if status != 200 or body != b"GET accepted":
            raise RuntimeError(f"configured GET method response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/get-only")
        if status != 405 or body != b"":
            raise RuntimeError(f"disallowed method response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/combined")
        if status != 200 or body != b"combined accepted":
            raise RuntimeError(f"nested trigger rule response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/combined", extra={"X-Deny": "deny"})
        if status != 400 or body != b"hook trigger rule rejected request":
            raise RuntimeError(f"negated trigger rule response was {(status, body)!r}")
        status, body, _ = request(port, "", "/hooks/regex", extra={"X-Ref": "release-42"})
        if status != 200 or body != b"regex accepted":
            raise RuntimeError(f"regex trigger rule response was {(status, body)!r}")
        status, body, _ = request(port, "", "/hooks/regex", extra={"X-Ref": "main"})
        if status != 400 or body != b"hook trigger rule rejected request":
            raise RuntimeError(f"regex rejection response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/headers")
        if status != 200 or b'"x-hook-secret":"correct-horse"' not in body:
            raise RuntimeError(f"complete header response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/form", b"message=hello+form", content_type="application/x-www-form-urlencoded")
        if status != 200 or body != b"hello form":
            raise RuntimeError(f"form payload response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/xml", b"<repository><name>toka-xml</name></repository>", content_type="application/xml")
        if status != 200 or body != b"toka-xml":
            raise RuntimeError(f"XML payload response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/patch-only", method="PATCH")
        if status != 200 or body != b"PATCH accepted":
            raise RuntimeError(f"configured PATCH response was {(status, body)!r}")
        status, body, _ = request(port, "", "/hooks/ip-local")
        if status != 200 or body != b"local address accepted":
            raise RuntimeError(f"IP whitelist response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/remote", extra={"X-Toka-Webhook-Remote-Addr": "203.0.113.9"})
        if status != 200 or not body.startswith(b"127.0.0.1:"):
            raise RuntimeError(f"TCP peer address response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/json-array", b'[{"email":"array@example.test"}]')
        if status != 200 or body != b"array@example.test":
            raise RuntimeError(f"top-level JSON array response was {(status, body)!r}")
        multipart = b'--toka-boundary\r\nContent-Disposition: form-data; name="message"\r\n\r\nmultipart value\r\n--toka-boundary--\r\n'
        status, body, _ = request(port, "correct-horse", "/hooks/multipart", multipart, content_type="multipart/form-data; boundary=toka-boundary")
        if status != 200 or body != b"multipart value":
            raise RuntimeError(f"multipart form response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/content-type-override", b'{"repository":{"name":"overridden"}}', content_type="text/plain")
        if status != 200 or body != b"overridden":
            raise RuntimeError(f"incoming content type override response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/default-method", method="GET")
        if status != 200 or body != b"default method accepted":
            raise RuntimeError(f"default HTTP method response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/default-method", method="DELETE")
        if status != 405 or body != b"":
            raise RuntimeError(f"default HTTP method rejection was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/complete-values?message=hello+query", b'{"name":"payload"}')
        if status != 200 or body != b'{"name":"payload"}|{"message":"hello query"}':
            raise RuntimeError(f"complete payload/query response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/complete-values", b'[{"name":"array"}]')
        if status != 200 or body != b'{"root":[{"name":"array"}]}|{}':
            raise RuntimeError(f"complete JSON array response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/file", b"request file payload")
        if status != 200 or body != b"request file payload":
            raise RuntimeError(f"raw temporary file response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/file-base64", b"AGJhcgo=")
        if status != 200 or body != b"\x00bar\n":
            raise RuntimeError(f"base64 temporary file response was {(status, body)!r}")
        status, body, _ = request(port, "correct-horse", "/hooks/file-mode", b"permission test")
        if status != 200 or body.decode().strip() != "0o600":
            raise RuntimeError(f"temporary file permission mode was {(status, body)!r}, expected 0o600")
        status, body, _ = request(port, "correct-horse", "/hooks/file-cleanup", b"cleanup")
        cleanup_path = Path(body.decode().strip())
        if status != 200 or not cleanup_path.is_absolute() or cleanup_path.exists():
            raise RuntimeError(f"temporary file cleanup response was {(status, body)!r}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=3)
    qualify_yaml(ROOT / "target" / "debug" / "webhook")
    qualify_template(ROOT / "target" / "debug" / "webhook", environment)
    print("toka-webhook CLI qualification: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
