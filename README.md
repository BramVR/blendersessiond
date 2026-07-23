# blendersessiond

Minimal, same-machine CLI that launches Blender with the BlenderMCP addon and
owns that Session for agent workflows: start, status, call, stop, and an
`mcp-serve` shim that connects MCP clients to a named Session. No resident
daemon, no remote control plane. Cross-platform (macOS, Windows, Linux),
GUI Blender only in v1.

Status: pre-implementation.

- Ubiquitous language: [CONTEXT.md](CONTEXT.md)
- Decisions: [docs/adr/](docs/adr/)
- v1 scope and plan: [PRD issue #1](https://github.com/BramVR/blendersessiond/issues/1)
