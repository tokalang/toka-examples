# Public registry PostgreSQL consumer

This cross-repository consumer fixture resolves `official/postgres@0.1.0`
through the public registry rather than a monorepo-relative path. It exercises
the public StartupMessage and simple-query encoders, incremental backend-frame
decoding, malformed-frame rejection, and configuration validation without
connecting to an external PostgreSQL server.

Run it with Toka `v1.0.0-rc.4` after the PostgreSQL package catalog entry is
live:

```text
toka build
./target/debug/registry_postgres_consumer
```

CI first resolves the immutable release from an empty package cache. It then
retains only the downloaded archive while removing the unpacked package and
`target`. `TOKA_OFFLINE=1` must unpack and rebuild the consumer without
changing `package.lock`.
