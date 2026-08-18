# Toka webhook

An independent, bounded Toka implementation of the core workflow of
[`adnanh/webhook`](https://github.com/adnanh/webhook): receive a webhook,
check a rule, and run a configured program. It is not an upstream release or
an affiliation with that project. See [UPSTREAM.md](UPSTREAM.md) for the fixed
source version, license notice, and compatibility boundary.

## Safety boundary

This program invokes commands only through Toka's structured
`std/process::Command` argv API. Request data is never interpolated into a
shell command: it is passed as individual argv/environment values or through
an exclusively-created request-temporary file.

## Run

```sh
curl -fsSL https://tokalang.dev/install.sh | bash -s -- v1.0.0-rc.6
toka doctor
toka build
./target/debug/webhook --hooks hooks.json --port 9000 --header 'Access-Control-Allow-Origin=*'
```

Each repeatable `--hooks`/`-hooks` configuration is a JSON or YAML array using upstream's `id`,
`execute-command`, `pass-arguments-to-command`, `pass-file-to-command`, and value/header
`trigger-rule.match` keys. File bindings write each request value to a 0600
temporary file; the configured child receives its path through `envname` (or
`HOOK_<UPPERCASE_NAME>`), and the file is removed after the child exits.
The optional repeatable `--header name=value` flag adds a response header to
every result (including CORS headers). The server handles one request per
connection and one connection at a time in this initial implementation.

Pass `-template` (upstream-compatible) or `--template` to render the hooks
file before parsing it. Webhook injects only `getenv`, `cat`, and `credential`:
`cat` returns a file's contents without one trailing newline, while
`credential` reads `<CREDENTIALS_DIRECTORY>/<name>` when that environment
variable is set and otherwise behaves as `getenv`. The template engine is a
restricted Toka subset, not full Go `text/template` compatibility.

## Current scope

The implementation covers a bounded, configuration-driven subset of the
upstream behavior. It supports request references, selected rule types,
structured argv execution, per-child working directories/environments,
request-temporary files, YAML hooks files, opt-in template preprocessing, and configurable request/response policy. The
complete Linux/macOS compatibility contract is in
[docs/compatibility-matrix.md](docs/compatibility-matrix.md).

## Qualification

With a compatible installed SDK on `PATH` (the installer configures
`TOKA_LIB`):

```sh
python3 tests/qualify.py
```

For an extracted SDK archive that is not installed, point the test at it:

```sh
TOKA_SDK=/path/to/toka-sdk python3 tests/qualify.py
```

The qualification compiles and runs both direct dispatch and loopback HTTP
tests against the selected SDK, not a sibling source checkout.

`pass-file-to-command` requires Toka's `std/fs::TempFile` and nominal-resource
ABI fixes. Until those local Toka commits are included in a published SDK, use
the explicit `TOKA`, `TOKAC`, and `TOKA_LIB` toolchain variables when running
the qualification.
