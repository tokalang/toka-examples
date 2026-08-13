# Public registry SQLite consumer

This cross-repository consumer fixture resolves `official/sqlite@0.1.0`
through the public registry rather than a monorepo-relative path. It exercises
the native in-memory database lifecycle, prepared binding and reads, explicit
statement finalization, a committed transaction, and explicit close.

Run it with Toka `v1.0.0-rc.4` after installing SQLite, OpenSSL, pkg-config,
and Clang 20:

```text
toka build
./target/debug/registry_sqlite_consumer
```

CI first resolves the immutable release from an empty package cache and checks
that its private C shim is installed and compiled. It then retains only the
downloaded archive while removing the unpacked package, `.toka/build`, and
`target`. `TOKA_OFFLINE=1` must unpack and rebuild the native object without
changing `package.lock`.
