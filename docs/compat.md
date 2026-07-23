# BlenderMCP Compatibility

blendersessiond vendors `addon.py` from
[`ahujasid/blender-mcp`](https://github.com/ahujasid/blender-mcp) at commit
`da4e16d2069ce5154eaa2535bf995e843caf5c73` from its default `main` branch.
The pinned source URL is:

`https://github.com/ahujasid/blender-mcp/blob/da4e16d2069ce5154eaa2535bf995e843caf5c73/addon.py`

The validated server requirement is defined by
[`VALIDATED_SERVER_REQUIREMENT`](../src/blendersessiond/mcp_serve.py), currently
`blender-mcp==1.6.4`. `mcp-serve` passes that explicit requirement to `uvx`, so
the validated version is used even when another `blender-mcp` tool is installed.

## Local patch

[`src/blendersessiond/vendor/addon.patch`](../src/blendersessiond/vendor/addon.patch)
is the complete reviewable delta against the pinned upstream file:

- bind the addon server explicitly to IPv4 loopback (`127.0.0.1`);
- read the Session port from `BLENDERSESSIOND_MCP_PORT`, falling back to the
  upstream scene/default port when unset;
- avoid writing runtime port/server state into the scene for a managed Session,
  so registering the addon does not itself create unsaved file changes. The
  upstream scene properties remain unchanged for unmanaged addon use;
- force server auto-start for managed Sessions even when a loaded scene saved
  the upstream auto-start preference as disabled.

Upstream auto-start remains unchanged. Re-pins require regenerating the
documented patch and validating the real Blender round trip against the named
`blender-mcp` server version.

## Security posture

The addon protocol is deliberately unauthenticated in v1. The PRD accepts
arbitrary code execution through the addon as an open-by-default capability,
with loopback binding as the boundary, and the stock `uvx blender-mcp` stdio
server speaks this plain protocol — per-request credentials would break the
unmodified-server contract (ADR 0002). Treat any account that can reach
loopback on this machine as able to drive Blender. Hardening options are a
post-v1 decision.
