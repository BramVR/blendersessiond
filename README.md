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
uv run blendersessiond start --scene /path/to/shot.blend
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
terminates the owned process tree without saving, without prompting, and
removes the record; logs remain. Saving is the caller's explicit act: use
`call` or MCP to save before `stop` when work must persist.

`start --scene PATH` requires an existing `.blend` file, opens it, and records
its absolute path as `scene_path`. Without `--scene`, Blender starts with its
factory-empty file. `status` shows the scene in human output and reports
`scene_path` in JSON (`null` for a factory-empty Session).

Session Health requires both the owned Blender process and its loopback MCP
addon socket to answer. A live process with a dead or unresponsive socket is
unhealthy with separate process and socket details in `status` and `doctor`.
Each `status` also reads Blender's live `bpy.data.is_dirty` value through the
addon. JSON reports `unsaved_changes` as `true` or `false`; it is the string
`"unknown"` when the live value cannot be obtained, including when the socket
is unreachable. Human output emits a warning when the value is true and never
guesses when it is unknown.

`call COMMAND [--params JSON]` opens one short-lived connection to the named
Session, sends the raw BlenderMCP addon command, and prints its JSON result.
Addon-reported errors are printed on stderr and exit 1.

## MCP client registration

Register the default Session once in `.mcp.json`:

```json
{
  "mcpServers": {
    "blender": {
      "command": "blendersessiond",
      "args": ["mcp-serve"]
    }
  }
}
```

Start the Session before the MCP client launches the server:

```console
blendersessiond start
```

For a named Session, add its name to the registration:

```json
"args": ["mcp-serve", "--name", "second"]
```

`mcp-serve` verifies that the named Session is healthy, sets
`BLENDER_HOST=127.0.0.1` and its dynamic `BLENDER_PORT`, then replaces itself
with the stock `uvx blender-mcp` stdio server. It requires
[uv/uvx](https://docs.astral.sh/uv/) on `PATH`.

Use one MCP client per Session. The addon protocol supports only one client;
`mcp-serve` deliberately does not multiplex. To use two Sessions concurrently,
give each its own MCP registration and `--name`.

Set `BLENDERSESSIOND_STATE_DIR` to an absolute path to override the platform
data directory. This is intended for isolated automation and tests:

```console
BLENDERSESSIOND_STATE_DIR=/tmp/my-run uv run blendersessiond status --json
```

The fake-Blender lifecycle e2e tests run in the normal test suite. Real Blender
tests are opt-in, require a resolvable Blender binary, verify PATH discovery,
isolate their state, and always stop Sessions they started. The Slice 6 fixture
is generated once per test session by real Blender using
`scripts/generate_scene_fixture.py`. The development sandbox could not complete
Blender GPU initialization, so the real gate generates the fixture on demand
rather than relying on an unverified committed binary:

```console
BLENDERSESSIOND_REAL_E2E=1 uv run pytest -m real_blender
```

To generate the fixture manually:

```console
blender --background --factory-startup \
  --python scripts/generate_scene_fixture.py -- /tmp/slice6_scene.blend
```

Real-Blender lifecycle behavior is CI-gated by the required Ubuntu leg in
`.github/workflows/real-blender-smoke.yml`.

- Ubiquitous language: [CONTEXT.md](CONTEXT.md)
- Decisions: [docs/adr/](docs/adr/)
- BlenderMCP pin and patch: [docs/compat.md](docs/compat.md)
- Third-party attribution: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
- v1 scope and plan: [PRD issue #1](https://github.com/BramVR/blendersessiond/issues/1)
