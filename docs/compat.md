# BlenderMCP Compatibility

blendersessiond vendors `addon.py` from
[`ahujasid/blender-mcp`](https://github.com/ahujasid/blender-mcp) at commit
`da4e16d2069ce5154eaa2535bf995e843caf5c73` from its default `main` branch.
The pinned source URL is:

`https://github.com/ahujasid/blender-mcp/blob/da4e16d2069ce5154eaa2535bf995e843caf5c73/addon.py`

The vendored copy is validated against `blender-mcp` server version `1.6.4`,
as resolved from PyPI by `uvx blender-mcp` on 2026-07-23.

## Local patch

[`src/blendersessiond/vendor/addon.patch`](../src/blendersessiond/vendor/addon.patch)
is the complete reviewable delta against the pinned upstream file:

- bind the addon server explicitly to IPv4 loopback (`127.0.0.1`);
- read the Session port from `BLENDERSESSIOND_MCP_PORT`, falling back to the
  upstream scene/default port when unset.

Upstream auto-start remains unchanged. Re-pins require regenerating the
documented patch and validating the real Blender round trip against the named
`blender-mcp` server version.
