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

## Session lifecycle

```console
uv run blendersessiond start
uv run blendersessiond start --name second --blender /path/to/blender
uv run blendersessiond status
uv run blendersessiond status --name second --json
uv run blendersessiond call get_scene_info --name second
uv run blendersessiond call get_object_info --name second \
  --params '{"name":"Cube"}'
uv run blendersessiond stop --name second
```

The default Session Name is `default`. Each Session has its own directory,
record, stdout/stderr logs, and reserved MCP port beginning at 9876. `stop`
terminates the owned process tree without saving and removes the record; logs
remain. Session Health requires both the owned Blender process and its
loopback MCP addon socket to answer. A live process with a dead or unresponsive
socket is unhealthy with separate process and socket details in `status` and
`doctor`.

`call COMMAND [--params JSON]` opens one short-lived connection to the named
Session, sends the raw BlenderMCP addon command, and prints its JSON result.
Addon-reported errors are printed on stderr and exit 1.

Set `BLENDERSESSIOND_STATE_DIR` to an absolute path to override the platform
data directory. This is intended for isolated automation and tests:

```console
BLENDERSESSIOND_STATE_DIR=/tmp/my-run uv run blendersessiond status --json
```

The fake-Blender lifecycle e2e tests run in the normal test suite. Real Blender
tests are opt-in, require a resolvable Blender binary, verify PATH discovery,
isolate their state, and always stop Sessions they started:

```console
BLENDERSESSIOND_REAL_E2E=1 uv run pytest -m real_blender
```

Real-Blender lifecycle behavior is CI-gated by the required Ubuntu leg in
`.github/workflows/real-blender-smoke.yml`.

- Ubiquitous language: [CONTEXT.md](CONTEXT.md)
- Decisions: [docs/adr/](docs/adr/)
- BlenderMCP pin and patch: [docs/compat.md](docs/compat.md)
- Third-party attribution: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
- v1 scope and plan: [PRD issue #1](https://github.com/BramVR/blendersessiond/issues/1)
