# Public registry compression consumer

This cross-repository consumer fixture resolves `official/compress@0.1.0`
through the public registry rather than a monorepo-relative path. It exercises
native Gzip and Zstd encoding plus the opt-in HTTP `Content-Encoding` policy.

Run it with Toka `v1.0.0-rc.4` after installing zlib, libzstd 1.4.0 or newer,
OpenSSL, pkg-config, and Clang 20:

```text
toka build
./target/debug/registry_compress_consumer
```

The committed lock records the immutable `0.1.0` release. CI first resolves it
from an empty package cache, then removes only the unpacked package and target
directories. `TOKA_OFFLINE=1` must rebuild from the retained archive without
changing `package.lock`.
