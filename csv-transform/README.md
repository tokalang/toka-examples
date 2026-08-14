# CSV transform

`csv-transform` is a deliberately small file-to-file application for the
SDK's `stdx/data/csv` streaming API. It copies records without adding a
transformation policy, so ownership of the buffered reader, writer, records,
and flush outcome stays visible.

```sh
toka build
./target/debug/csv_transform input.csv output.csv
```

This example has no external package dependency; `stdx/data/csv` is provided
by the installed Toka SDK.

## Migration provenance

The source was moved with its Toka history from
[`tokalang/toka/examples/csv_transform`](https://github.com/tokalang/toka/tree/02c391e73123368d44fad31208021d7c8b84f9ce/examples/csv_transform)
at source snapshot `38b54ae738419a8b85360a43e760f83d555498ef`.
