# Same-Machine CLI; Remote Control Is A Separate Later Tool

## Status

Accepted (2026-07-23)

## Context

Agents need to launch and own Blender+MCP sessions on both macOS and Windows.
Bram develops on the Mac and will sometimes drive a Windows box. The Maya
toolchain solved this with two repos: `gg_mayasessiond` running on the box
that hosts Maya, and `mac_maya_dev` as the Mac-side SSH/deploy wrapper.
Folding SSH transport, tunnels, and the Windows interactive scheduled-task
launcher into blendersessiond would make every local-only install carry
remote-control code that does nothing.

## Decision

blendersessiond is strictly same-machine: the CLI always runs on the machine
where Blender runs, and contains no SSH, tunnel, Tailscale, or remote-launch
code. Remote use means executing the same CLI on the far machine (e.g. `ssh
blender-win blendersessiond start`). A Mac-side dev wrapper that owns SSH
invocation, the MCP port tunnel, and the Windows non-interactive-session
launch indirection (scheduled task) is a separate future tool, out of scope
here.

## Consequences

- Core stays minimal and identical on macOS/Windows/Linux; no dead code on
  either platform.
- Cold-starting GUI Blender on Windows from an SSH session is not supported
  by core (Windows OpenSSH children land in the wrong desktop session); that
  is explicitly the future wrapper's problem, solved there with an
  interactive scheduled task like mac_maya_dev's.
- The MCP addon socket binds loopback only; any cross-machine data plane is
  the caller's SSH port-forward, not this tool's concern.
- Network choice (Tailscale vs LAN) lives entirely in the operator's
  ssh_config.
