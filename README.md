![blendersessiond: agent-controlled local Blender Sessions](https://raw.githubusercontent.com/BramVR/blendersessiond/main/docs/assets/blendersessiond-header.png)

<p align="center">
  <a href="https://blendersessiond.bramvanrompuy.be/"><img src="https://img.shields.io/badge/website-blendersessiond.bramvanrompuy.be-64F2B8?style=for-the-badge" alt="blendersessiond website"></a>
  <a href="https://pypi.org/project/blendersessiond/"><img src="https://img.shields.io/pypi/v/blendersessiond?style=for-the-badge&amp;label=PyPI" alt="PyPI version"></a>
  <a href="https://github.com/BramVR/blendersessiond/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/BramVR/blendersessiond/ci.yml?branch=main&amp;style=for-the-badge&amp;label=CI" alt="CI status"></a>
  <a href="https://github.com/BramVR/blendersessiond/actions/workflows/real-blender-smoke.yml"><img src="https://img.shields.io/github/actions/workflow/status/BramVR/blendersessiond/real-blender-smoke.yml?branch=main&amp;style=for-the-badge&amp;label=Blender%20smoke" alt="Real Blender smoke status"></a>
  <a href="https://github.com/BramVR/blendersessiond/blob/main/pyproject.toml"><img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&amp;logo=python&amp;logoColor=white" alt="Python 3.11 or newer"></a>
  <a href="https://github.com/BramVR/blendersessiond/blob/main/.github/workflows/ci.yml"><img src="https://img.shields.io/badge/platforms-macOS%20%7C%20Windows%20%7C%20Linux-blue?style=for-the-badge" alt="Supported platforms: macOS, Windows, and Linux"></a>
  <a href="https://github.com/BramVR/blendersessiond/blob/main/docs/compat.md"><img src="https://img.shields.io/badge/Blender-5.2.0%20validated-E87D0D?style=for-the-badge&amp;logo=blender&amp;logoColor=white" alt="Blender 5.2.0 validated"></a>
</p>

# blendersessiond

`blendersessiond` launches, configures, and owns local GUI Blender Sessions
for agent workflows. It installs a pinned BlenderMCP addon at startup,
allocates an isolated loopback port for each Session, reports process and
addon Health, and stops only the Blender process trees it launched.

It supports macOS, Windows, and Linux with Python 3.11 or newer.

## Highlights

- No manual addon installation or Blender preference setup.
- Named Sessions with independent state, logs, and MCP ports.
- Static MCP client configuration despite dynamically allocated ports.
- Health checks require both a live Blender process and a responsive addon.
- Existing `.blend` scenes and direct raw addon calls are supported.
- `stop` is predictable: it terminates the owned process tree and never saves.

## Requirements and compatibility

- [uv](https://docs.astral.sh/uv/) with `uvx` available on `PATH`.
- Python 3.11 or newer.
- Blender installed on macOS, Windows, or Linux.

blendersessiond is GUI-only and same-machine: the CLI and Blender Session run
on the same machine, and blendersessiond has no remote control plane or
headless mode. The real-Blender smoke workflow currently validates Blender
5.2.0 on all three platforms; macOS and Windows GUI runs are best-effort in
hosted CI. See the [compatibility record](https://github.com/BramVR/blendersessiond/blob/main/docs/compat.md) for the exact Blender,
addon, and MCP server pins.

The addon is vendored from
[`ahujasid/blender-mcp`](https://github.com/ahujasid/blender-mcp) at commit
[`da4e16d`](https://github.com/ahujasid/blender-mcp/commit/da4e16d2069ce5154eaa2535bf995e843caf5c73),
then minimally patched for loopback binding, per-Session ports, managed
startup, and telemetry removal. Upstream BlenderMCP includes default-on
telemetry that reports usage — and, when it believes consent was given,
prompts, code, and screenshots — to the upstream maintainer's hosted backend.
blendersessiond does not want that telemetry: the vendored addon has all of it
deleted, and `mcp-serve` runs the stock server with `DISABLE_TELEMETRY=true`,
so managed Sessions send no telemetry at all. The MCP stdio server itself is
not vendored: `mcp-serve` runs the validated `blender-mcp==1.6.4` package
through `uvx`. The complete patch and re-pin procedure are in the
[compatibility record](https://github.com/BramVR/blendersessiond/blob/main/docs/compat.md), with upstream
licensing in [third-party attribution](https://github.com/BramVR/blendersessiond/blob/main/THIRD_PARTY_NOTICES.md).

## Install

Install the published CLI from PyPI:

```console
uv tool install blendersessiond
```

Or install from a source checkout:

```console
git clone https://github.com/BramVR/blendersessiond.git
cd blendersessiond
uv tool install .
```

Ensure uv's tool executable directory is on `PATH`. Pass Blender explicitly
when automatic discovery does not find it.

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
successful `call` invocations always print the addon's JSON result, while
`call --json` also makes failures machine-readable. `mcp-serve` reserves
standard input and output for the MCP protocol.

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

## Configuration and state

Blender discovery uses the first available source in this order:

1. `--blender PATH` on `doctor` or `start`;
2. the `BLENDERSESSIOND_BLENDER` environment variable;
3. `blender` or `blender.exe` on `PATH`;
4. standard platform installation locations.

When multiple valid standard installations are present, blendersessiond uses
the newest version it can probe.

Session records and Blender stdout/stderr logs live under the per-user state
directory:

- macOS: `~/Library/Application Support/blendersessiond`
- Windows: `%LOCALAPPDATA%\blendersessiond`
- Linux: `$XDG_STATE_HOME/blendersessiond` when set, otherwise
  `~/.local/state/blendersessiond`

Set `BLENDERSESSIOND_STATE_DIR` to an absolute path to override this location.
`blendersessiond status` prints each Session's log paths; use `--json` when a
script needs the complete state record.

Session MCP ports are allocated upward from `9876` on loopback. Set
`BLENDERSESSIOND_BASE_MCP_PORT` to start allocation from a different port —
the test suite uses this to keep fake-Blender Sessions off the ports a real
Session on the same machine may hold.

## Security and limitations

The vendored addon binds to IPv4 loopback (`127.0.0.1`) but its protocol is
deliberately unauthenticated and permits arbitrary Python execution inside
Blender. Any local account or process that can reach a Session's loopback port
can drive that Session. Do not expose or forward the port to an untrusted
network or machine.

The addon protocol is single-client. Give each concurrent MCP client its own
named Session. blendersessiond does not supervise or restart Sessions in the
background; `status` checks their Health on demand.

## Development

Install the locked development environment, then run the same lint and test
gates used by CI:

```console
uv sync --locked
uv run ruff check .
uv run pytest
```

Real-Blender tests are opt-in because they launch the GUI. For example, on
macOS or Linux:

```console
BLENDERSESSIOND_REAL_E2E=1 \
BLENDERSESSIOND_BLENDER=/path/to/blender \
uv run pytest -m real_blender
```

CI runs unit and fake-Blender lifecycle tests on macOS, Windows, and Linux,
plus a pinned real-Blender round trip.

## Reference

- [Agent skill](https://github.com/BramVR/blendersessiond/blob/main/.agents/skills/blendersessiond/SKILL.md)
- [Changelog](https://github.com/BramVR/blendersessiond/blob/main/CHANGELOG.md)
- [Ubiquitous language](https://github.com/BramVR/blendersessiond/blob/main/CONTEXT.md)
- [Decision records](https://github.com/BramVR/blendersessiond/tree/main/docs/adr)
- [BlenderMCP compatibility pins and re-pin procedure](https://github.com/BramVR/blendersessiond/blob/main/docs/compat.md)
- [Release process](https://github.com/BramVR/blendersessiond/blob/main/docs/RELEASING.md)
- [Third-party attribution](https://github.com/BramVR/blendersessiond/blob/main/THIRD_PARTY_NOTICES.md)
- [v1 PRD issue #1](https://github.com/BramVR/blendersessiond/issues/1)
