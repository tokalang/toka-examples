# Public registry Unicode consumer

This cross-repository consumer resolves `official/unicode@0.1.1` through the
public registry rather than a monorepo-relative path. It exercises extended
grapheme counting and slicing, grapheme-to-byte offset mapping, and malformed
UTF-8 error reporting.

Run it with Toka `v1.0.0-rc.4` after the Unicode `0.1.1` catalog entry is live:

```text
toka build
./target/debug/registry_unicode_consumer
```

CI first resolves the immutable release from an empty package cache and checks
that both licenses, the vendored Unicode data, and the generation tools are in
the clean archive. It then removes the build output and unpacked package while
retaining only the downloaded archive. `TOKA_OFFLINE=1` must unpack and rebuild
the consumer without changing `package.lock`.
