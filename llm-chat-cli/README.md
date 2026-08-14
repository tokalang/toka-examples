# Toka LLM chat CLI

A minimal interactive, streaming CLI using DeepSeek's OpenAI-compatible
chat-completions endpoint. It keeps conversation history only in memory.

The project resolves immutable public-registry
`official/openai_compat@0.1.1`; it does not depend on a Toka source checkout.

```sh
toka build
./target/debug/llm_chat_cli --help
```

It requires `DEEPSEEK_API_KEY`. By default it uses:

```text
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

`DEEPSEEK_BASE_URL` is an API base URL; the demo adds `/chat/completions` when
it has no path. A complete compatible HTTPS endpoint path is also accepted.
Set either optional variable to use a compatible endpoint or model.
The key is read only from the environment, placed in the Authorization header,
and never printed or persisted. Use `/reset` to clear in-memory history and
`/quit` to exit.

`--help` is credential-free and prints the local usage text.

## Qualification

With a compatible installed SDK, run:

```sh
TOKA=/path/to/toka python3 qualify.py
```

The script begins with an empty cache, resolves the committed public-registry
lock, builds and invokes the credential-free `--help` path, then retains only
the downloaded archive for an identical `TOKA_OFFLINE=1` replay. It does not
contact a provider or require a credential.

## Migration provenance

This application was moved with its Toka history from
[`tokalang/toka/demos/llm-chat-cli`](https://github.com/tokalang/toka/tree/02c391e73123368d44fad31208021d7c8b84f9ce/demos/llm-chat-cli)
at source snapshot `38b54ae738419a8b85360a43e760f83d555498ef`.
