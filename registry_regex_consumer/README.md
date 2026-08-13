# Public registry regex consumer

This cross-repository consumer fixture resolves `official/regex@0.1.1`
through the public registry rather than a monorepo-relative path.

Run it with Toka `v1.0.0-rc.4`:

```text
toka build
./target/debug/registry_regex_consumer
```

The committed lock records the immutable `0.1.1` release. Replaying it with
`TOKA_OFFLINE=1 toka build` must produce the same executable after the archive
has been fetched once.

Migrated from `tokalang/toka` path `examples/registry_regex_consumer` at
commit `8216daa0c9e8463e35ba05219310d520474314e6`.
