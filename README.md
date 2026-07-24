# blendersessiond

`blendersessiond` launches and owns local GUI Blender Sessions for agent
workflows. It supports macOS, Windows, and Linux with Python 3.11 or newer.

## Install

Install [uv](https://docs.astral.sh/uv/), clone this repository, then install
the CLI from the repository root:

```console
git clone https://github.com/BramVR/blendersessiond.git
cd blendersessiond
uv tool install .
```

Ensure uv's tool executable directory is on `PATH`. Blender must also be
installed; pass its executable explicitly when automatic discovery does not
find it.

## Quickstart

These commands check the machine, start the default Session with a
factory-empty scene, make one raw addon call, and stop the Session:

```console
blendersessiond doctor
blendersessiond start
blendersessiond call get_scene_info
blendersessiond stop
```

Use a specific Blender executable or open an existing scene when starting:

```console
blendersessiond doctor --blender /path/to/blender
blendersessiond start --blender /path/to/blender --scene /path/to/shot.blend
```

`--scene` must name an existing `.blend` file. Without it, Blender opens a
factory-empty file.

## Commands

- `doctor`: Check whether this machine can host a Session and report recorded Session Health.
- `start`: Launch and own a Blender Session, optionally from an existing scene.
- `status`: Report one named Session or list every recorded Session.
- `call`: Send one raw command and optional JSON parameters to a healthy Session's addon.
- `stop`: Terminate an owned Session process tree without saving.
- `mcp-serve`: Connect the validated `blender-mcp` stdio server to one healthy Session.

Run `blendersessiond COMMAND --help` for each command's flags. `doctor`,
`start`, `status`, and `stop` support versioned JSON output with `--json`;
`call` prints the addon's JSON result.

Exit codes follow one convention:

- `0`: success or healthy status;
- `1`: operation failure, including unhealthy, stale, or not-found status;
- `2`: command-line usage error.

## MCP client registration

Start the default Session before launching the MCP client:

```console
blendersessiond start
```

Register the installed CLI in the project's `.mcp.json`:

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

`mcp-serve` requires `uvx` on `PATH`. It checks Session Health, then runs the
validated `blender-mcp` server against that Session's loopback port. Keep the
Session running while the MCP client uses it.

## Multiple Sessions

The default Session Name is `default`. Use `--name` to run independent
Sessions side by side:

```console
blendersessiond start --name modeling
blendersessiond start --name lighting --scene /path/to/lighting.blend
blendersessiond status
blendersessiond call get_scene_info --name modeling
blendersessiond call get_object_info --name lighting --params '{"name":"Cube"}'
blendersessiond stop --name modeling
blendersessiond stop --name lighting
```

Give each Session its own MCP server registration and MCP client connection:

```json
{
  "mcpServers": {
    "blender-modeling": {
      "command": "blendersessiond",
      "args": ["mcp-serve", "--name", "modeling"]
    },
    "blender-lighting": {
      "command": "blendersessiond",
      "args": ["mcp-serve", "--name", "lighting"]
    }
  }
}
```

## Save before stop

`stop` never saves and never prompts. Check `status` for the unsaved-changes
warning, then save explicitly through Blender, MCP, or `call` before stopping
when work must persist.

Save an already named scene through `call`:

```console
blendersessiond call execute_code --params '{"code":"bpy.ops.wm.save_mainfile()"}'
blendersessiond stop
```

Give a factory-empty scene a path before stopping:

```console
blendersessiond call execute_code --params '{"code":"bpy.ops.wm.save_as_mainfile(filepath=\"/absolute/path/to/scene.blend\")"}'
blendersessiond stop
```

Use `--name SESSION` on both commands for a named Session.

## Reference

- [Ubiquitous language](CONTEXT.md)
- [Decision records](docs/adr/)
- [BlenderMCP compatibility pins and re-pin procedure](docs/compat.md)
- [Third-party attribution](THIRD_PARTY_NOTICES.md)
- [v1 PRD issue #1](https://github.com/BramVR/blendersessiond/issues/1)
