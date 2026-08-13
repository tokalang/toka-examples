# Public registry GUI consumer

This macOS-only cross-repository consumer resolves `official/gui@0.1.0`
through the public registry rather than a monorepo-relative path. It exercises
headless layout and grapheme-editor APIs, proving the locked transitive
`official/unicode@0.1.1` dependency without opening an AppKit window.

Run it with Toka `v1.0.0-rc.4` on macOS after the GUI `0.1.0` catalog entry is
live:

```text
toka build
./target/debug/registry_gui_consumer
```

CI resolves both immutable releases from an empty package cache, compiles the
GUI Objective-C bridge, and verifies the executable links AppKit, Metal, and
QuartzCore. It then removes the unpacked packages, native build output, and
executable while retaining only the two downloaded archives. `TOKA_OFFLINE=1`
must unpack and rebuild both packages without changing `package.lock`.

This is a headless package, native-build, and framework-link qualification. It
does not claim that a hosted runner created a window, acquired a Metal device,
or exercised interactive text input.
