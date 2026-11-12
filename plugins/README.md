# Declarative plugins

Each `*.json` file is an inert manifest. The GUI discovers, validates and
renders it; **nothing here is executed**. `docs_url` is optional and must be an
ASCII http(s) link.

```json
{
  "id": "example",
  "name": "Example extension",
  "kind": "analysis",
  "description": "One ASCII sentence.",
  "enabled": false,
  "docs_url": "https://example.com/"
}
```

The schema is documented in
[`docs/reference/interfaces/plugin-manifest-v1.md`](../docs/reference/interfaces/plugin-manifest-v1.md).
Operators can point `BINARYREPAIR_PLUGIN_DIR` at another directory.
