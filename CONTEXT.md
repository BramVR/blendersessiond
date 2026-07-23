# blendersessiond — Context

Ubiquitous language for blendersessiond: a minimal, cross-platform CLI that
launches Blender with the MCP addon and owns that process for agent workflows.

## Glossary

### Session

A Blender process launched and owned by blendersessiond, together with its
MCP addon socket. A Session is the unit agents start, reuse, inspect, and
stop. A Session lives on the machine where the CLI runs; blendersessiond has
no remote control plane (a caller may reach that machine via SSH, but that is
outside this tool — see ADR 0001).

### Session Name

The identifier of a Session on its machine. Multiple Sessions may run side by
side; each has a unique name and its own MCP port. When no name is given, the
Session is named `default`.

### Ownership

blendersessiond is the sole lifecycle authority for a Session: it opens
Blender, closes it, and on stop terminates the entire process tree. Stopping
never saves; persisting work is the caller's explicit act before stop.
Blender processes not launched by blendersessiond are never touched, and a
Session has one owner at a time.

### Health

A Session is healthy when its Blender process is alive **and** its MCP addon
socket answers. Process-alive with a dead socket is unhealthy, not healthy.
