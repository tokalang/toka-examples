# `registry_redis_consumer`

Minimal consumer validating resolution, lock replay, build, and public API
execution of the official standalone `redis` package (`redis@0.2.0`)
from the public Toka registry (`https://pkg.tokalang.dev`).

## Verification

```sh
toka fetch
toka build
./target/debug/registry_redis_consumer
```
