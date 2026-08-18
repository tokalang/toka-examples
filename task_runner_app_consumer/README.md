# task_runner_app_consumer

An official application deliverable consumer example verifying `task-runner:0.1.0`.

## Verification Flow

1. **Online Phase**:
   - Resolves `task-runner:0.1.0` via `toka fetch` against official `https://pkg.tokalang.dev`.
   - Verifies `package.lock` integrity and SHA-256 hash match (`d9d9bbb379c2839ccba81ede553c7a538a0ded4e1872b33fc7a82ee61bcf2225`).
   - Builds unpacked application binary `target/task-runner` with the RC6 toolchain.
   - Executes multi-step DAG workflows, `--plan`, `--dry-run`, and fail-fast assertions.

2. **Offline Phase**:
   - Clears unpacked packages and build artifacts while preserving `package.lock` and `.toka/cache`.
   - Runs `TOKA_OFFLINE=1 toka fetch` with an unreachable registry endpoint (`http://127.0.0.1:9`).
   - Re-compiles from local cache and validates deterministic execution output.
