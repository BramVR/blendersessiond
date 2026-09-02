# BlenderMCP Compatibility

blendersessiond vendors `addon.py` from
[`ahujasid/blender-mcp`](https://github.com/ahujasid/blender-mcp) at commit
`da4e16d2069ce5154eaa2535bf995e843caf5c73` from its default `main` branch.
The pinned source URL is:

`https://github.com/ahujasid/blender-mcp/blob/da4e16d2069ce5154eaa2535bf995e843caf5c73/addon.py`

The validated server requirement is defined by
[`VALIDATED_SERVER_REQUIREMENT`](../src/blendersessiond/mcp_serve.py), currently
`blender-mcp==1.6.4`. Its compatible MCP SDK is pinned by
`VALIDATED_MCP_REQUIREMENT`, currently `mcp==1.29.1`. The validated invocation
is `uvx --with mcp==1.29.1 blender-mcp==1.6.4`. `mcp-serve` passes both exact
requirements to `uvx`, so dependency drift cannot replace the compatible
`mcp.server.fastmcp` API beneath the pinned BlenderMCP server.

The real-Blender smoke workflow pins Blender `5.2.0`, including per-platform
download URLs and SHA-256 checksums, in
[`.github/workflows/real-blender-smoke.yml`](../.github/workflows/real-blender-smoke.yml).

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
  the upstream auto-start preference as disabled;
- delete the upstream telemetry surface entirely: the `get_telemetry_consent`
  command handler, the `telemetry_consent` addon preference, and the telemetry
  preferences UI. Upstream ships default-on telemetry to a hosted third-party
  backend, and its consent handler falls back to consent-granted whenever the
  addon is not registered through Blender's Preferences — which is exactly how
  managed Sessions load it. blendersessiond deletes the code instead of relying
  on that fallback; a stock `blender-mcp` server asking a managed Session for
  consent now receives an unknown-command error and fails closed on its side.

## Re-pin procedure

Per [ADR 0002](adr/0002-vendor-pinned-blender-mcp-addon.md), addon updates are
deliberate compatibility changes:

1. Select an upstream `ahujasid/blender-mcp` commit and a compatible released `blender-mcp` server version.
2. Download that commit's unmodified `addon.py` to a temporary path; apply only the managed-Session changes summarized above to `src/blendersessiond/vendor/addon.py`.
3. Regenerate `src/blendersessiond/vendor/addon.patch` as the complete unified diff from the pinned upstream file to the vendored file.
4. Update the commit and source URL in this document and the patch header; update `VALIDATED_SERVER_REQUIREMENT`, `VALIDATED_MCP_REQUIREMENT`, and this document when either validated server dependency changes.
5. Run `uv run ruff check .`, `uv run pytest`, and the real-Blender addon, direct-call, MCP stdio, scene, unsaved-changes, and stop-never-saves smoke against the pins.
6. Commit the vendored addon, patch, compatibility record, server pin, and resulting tests together only after the real round trip passes.

To re-pin the smoke Blender runtime itself, select the desired stable Blender
patch, update `BLENDER_VERSION`, every platform URL, and every SHA-256 in the
workflow from Blender's official checksum file, then require the Ubuntu smoke
leg to pass.

## Security posture

The addon protocol is deliberately unauthenticated in v1. The PRD accepts
arbitrary code execution through the addon as an open-by-default capability,
with loopback binding as the boundary, and the stock `uvx blender-mcp` stdio
server speaks this plain protocol — per-request credentials would break the
unmodified-server contract (ADR 0002). Treat any account that can reach
loopback on this machine as able to drive Blender. Hardening options are a
post-v1 decision.

Managed Sessions send no telemetry. The vendored addon has upstream's
telemetry deleted (see the local patch above), and `mcp-serve` sets
`DISABLE_TELEMETRY=true` for the validated stock server, which disables its
telemetry client completely — including the baseline anonymous tier. A
re-pin must preserve both: keep the telemetry removal in the vendored addon
(pinned by `tests/test_vendor_addon.py`) and confirm the new server version
still honors `DISABLE_TELEMETRY`.
