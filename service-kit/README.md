# Service Kit

`service-kit` is a deliberately bounded reference application.  It demonstrates
how a stateful HTTP service composes Toka's existing layers without introducing
a web framework:

```text
std/net TcpListener
        -> stdx/net/http server connection lifecycle
        -> official/router method/path dispatch
        -> official/sqlite parameterized persistence
```

The project resolves the immutable public-registry releases
`official/router@0.1.0` and `official/sqlite@0.1.0`; it has no dependency on
a Toka source checkout.

## Surface

- `GET /health` returns `{"status":"ok"}`.
- `POST /notes` accepts `{"body":"..."}` and returns a persisted note with
  HTTP `201 Created`.
- `GET /notes/:id` returns that note or a JSON HTTP `404` error.
- `ServiceConfig` supplies the bind address, SQLite path, and log level.

The application intentionally handles one request per connection and closes
that connection.  In addition to `serve_one_async` for small examples,
`serve_until_canceled_async` composes `Canceler`, cancellation-aware accept,
and `TaskScope` into a bounded long-lived lifecycle: cancellation stops the
next accept, completed workers drain until the caller-provided deadline, and
remaining workers receive cooperative cancellation.  Each worker opens its
own SQLite connection; concurrent-write policy remains deliberately out of
scope.

The service does not install process-global signal handlers itself.  A host
that wants POSIX `SIGINT`/`SIGTERM` handling installs `std/signal` explicitly
and calls the supplied `Canceler`; tests can supply any other cancellation
source.

It does not claim routing middleware, authentication, TLS termination, an
ORM, migrations, HTTP/2, or a daemon supervisor. `official/router` supplies
only deterministic method/path recognition; the application retains response,
validation, persistence, and lifecycle policy.

## Qualification

Install Toka `v1.0.0-rc.4`, SQLite, OpenSSL, pkg-config, and Clang 20. Then
provide the published SDK explicitly:

```sh
TOKA=/path/to/bin/toka \
TOKAC=/path/to/bin/tokac \
TOKA_LIB=/path/to/lib \
python3 tests/qualify.py
```

The qualification starts from an empty package cache, resolves the committed
lock through the public registry, and compiles the SQLite bridge from the
materialized locked SQLite package. It builds and executes three native
programs:

1. deterministic in-process dispatch, including malformed JSON, not-found,
   statement/handle cleanup, and restart persistence;
2. loopback TCP requests for health, malformed JSON, create, read, not-found,
   and restart persistence.  Each request waits for its server task and proves
   no SQLite handle or statement remains live.
3. a cancellation-driven long-lived loopback service that accepts a health
   request, stops accepting after cancellation, drains the completed worker,
   and proves no SQLite handle, statement, or accept cancellation token remains.

It then removes only the unpacked package roots, replays the same lock with
`TOKA_OFFLINE=1` from retained immutable archives, verifies the lock bytes did
not change, and reruns all three programs.

This requires the opt-in native SQLite development package (`sqlite3` through
`pkg-config`), just as `official/sqlite` does.

## Migration provenance

The source and all three qualification programs were moved from
[`tokalang/toka/examples/service-kit`](https://github.com/tokalang/toka/tree/37591f94e05cd2efc56bc03d3be5fbc5adbd4883/examples/service-kit)
at source snapshot `37591f94e05cd2efc56bc03d3be5fbc5adbd4883`. The original
application entered Toka in
[`c693a333`](https://github.com/tokalang/toka/commit/c693a3335bc3d455dae3db3741966b2d329f88be);
the last commit affecting the path before migration was
[`d0b53a34`](https://github.com/tokalang/toka/commit/d0b53a34a24026032748d841887789f1c5d6ffd6).
The original repository remains the authoritative history before this move.
