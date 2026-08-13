# Toka Examples

Standalone examples and integration dogfood for the Toka SDK and package
ecosystem.

## Repository boundary

This repository owns user-facing examples and application-level integration
checks. It is not a language specification, standard-library source, or
compiler qualification authority. Those remain in
[`tokalang/toka`](https://github.com/tokalang/toka).

Each example pins its compatible SDK and package versions and carries a
reproducible build or qualification command.

## Examples

- [`registry_regex_consumer`](registry_regex_consumer) verifies an immutable
  public-registry dependency through online and offline lock replay.
- [`registry_compress_consumer`](registry_compress_consumer) verifies the
  native Gzip, Zstd, and HTTP policy surfaces of the immutable compression
  package through online and offline lock replay.
- [`registry_sqlite_consumer`](registry_sqlite_consumer) verifies the native
  in-memory lifecycle, prepared statements, and transactions of the immutable
  SQLite package through online and archive-only offline lock replay.
- [`registry_postgres_consumer`](registry_postgres_consumer) verifies the
  bounded PostgreSQL wire codec and startup configuration of the immutable
  PostgreSQL package without requiring an external database server.
- [`registry_unicode_consumer`](registry_unicode_consumer) verifies Unicode
  grapheme segmentation, byte-offset mapping, and malformed UTF-8 reporting
  through online and archive-only offline registry resolution.
- [`service-kit`](service-kit) demonstrates a registry-locked HTTP service
  composed from the Router and SQLite packages, with online and archive-only
  offline qualification.

## License

Apache License 2.0. See [LICENSE](LICENSE).
Standalone examples and integration dogfood for Toka
