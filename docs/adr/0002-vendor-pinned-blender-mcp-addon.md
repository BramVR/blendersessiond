# Vendor A Pinned blender-mcp Addon

## Status

Accepted (2026-07-23)

## Context

Sessions need the BlenderMCP addon (ahujasid/blender-mcp `addon.py`, MIT,
~2900 lines) running inside Blender. Its wire protocol must match the
`uvx blender-mcp` server, so a custom addon would force a server fork too.
Upstream reads its port from a scene property and expects a manual
install/enable in Blender preferences; per-session ports and deterministic
agent-driven sessions don't fit that.

## Decision

Vendor `addon.py` at a pinned upstream commit into this repo, with MIT
attribution in THIRD_PARTY_NOTICES.md. Patch minimally: bind IPv4 loopback
for all uses; for managed Sessions, take the listen port from an environment
variable, force auto-start even when a loaded scene disabled it, and avoid
writing runtime server state into the scene. Unmanaged upstream scene
settings remain unchanged. blendersessiond installs/enables the vendored
addon at Session launch (`--factory-startup` plus addon registration), never
relying on per-machine Blender preferences. Upstream updates are deliberate
re-pins after local validation, mirroring gg_mayasessiond's
`mcp_compat.json` discipline.

## Consequences

- Sessions are reproducible across machines; no manual Blender prefs step.
- We own a small patch surface against upstream drift; re-pinning is an
  explicit, testable act.
- The `uvx blender-mcp` server version must stay protocol-compatible with
  the pinned addon; the pin record must name the validated server version.
