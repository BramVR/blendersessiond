# blendersessiond

Minimal, same-machine CLI that launches Blender with the BlenderMCP addon and
owns that Session for agent workflows: start, status, call, stop, and an
`mcp-serve` shim that connects MCP clients to a named Session. No resident
daemon, no remote control plane. Cross-platform (macOS, Windows, Linux),
GUI Blender only in v1.

## Install and doctor

Python 3.11+ and [uv](https://docs.astral.sh/uv/) are required.

```console
uv sync
uv run blendersessiond doctor
uv run blendersessiond doctor --json
```

`doctor` exits 0 when the machine can host a Session and 1 when any check
fails. Use `--blender PATH` to inspect a specific Blender executable.

The JSON report is versioned and stable within `schema_version: 1`:

```json
{
  "schema_version": 1,
  "status": "pass",
  "platform": {"system": "Linux", "name": "Linux"},
  "checks": [
    {"name": "platform", "status": "pass", "message": "Linux is supported."},
    {"name": "blender", "status": "pass", "message": "..."},
    {"name": "state_directory", "status": "pass", "message": "..."}
  ],
  "blender": {
    "path": "/usr/bin/blender",
    "version": "4.3.0",
    "source": "PATH"
  },
  "state_dir": "/home/user/.local/state/blendersessiond"
}
```

When Blender is unavailable, its `path`, `version`, and `source` are `null`.

- Ubiquitous language: [CONTEXT.md](CONTEXT.md)
- Decisions: [docs/adr/](docs/adr/)
- v1 scope and plan: [PRD issue #1](https://github.com/BramVR/blendersessiond/issues/1)
