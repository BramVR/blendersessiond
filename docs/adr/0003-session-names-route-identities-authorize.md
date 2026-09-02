# Session Names Route; Session Identities Authorize

## Status

Accepted (2026-09-02)

## Context

A Session Name is reusable. A controller can inspect one named Session, lose
contact, and later issue a call or stop after another controller has replaced
that Session under the same name. Process ownership prevents blendersessiond
from touching an unrelated Blender process, but it does not prove that a
stale caller still refers to the same blendersessiond-owned lifecycle.

## Decision

Each Session gets an opaque, immutable Session ID when launch begins. `start`
and `status` expose it. Stale-sensitive `call` and destructive `stop` require
the caller to return that exact ID with `--expect-session-id`. The comparison
happens under the Session Name lock, after reading current state and before
socket or process-tree access.

Session Names remain convenient routing labels; Session IDs are authority.
Records created by versions without a Session ID receive a stable opaque ID
derived from their existing private owner token, allowing an upgraded daemon
to inspect and safely finish an already-owned lifecycle.

## Consequences

- A stale caller cannot call or stop a replacement Session with the same name.
- Callers must retain the ID returned by `start`, or obtain it from `status`
  before acting.
- `mcp-serve` remains name-routed for now. Its long-lived stdio bridge cannot
  provide a fresh per-request identity fence without becoming a proxy; remote
  orchestration should use the fenced raw `call` interface.
- Session records remain schema version 1 because the added field is backward
  readable and legacy records receive a deterministic compatibility identity.
