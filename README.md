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

## License

Apache License 2.0. See [LICENSE](LICENSE).
Standalone examples and integration dogfood for Toka
