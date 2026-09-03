---
summary: Blender Box setup uses an atomic, identity-fenced Windows Job owner.
read_when:
  - Changing setup-owner launch, status, stop, state, or Windows process code.
  - Changing the Blender Box capability contract.
---

# Fenced Windows setup owner

## Context

Blender Box setup crosses Windows OpenSSH before its PowerShell program can
record a process identity. Cancelling the SSH client in that interval can
leave setup work running. A later cleanup cannot safely find that work by
name, command text, or PID because each can identify an unrelated replacement.

The existing Blender Session owner already contains Windows Job Object code,
but setup must remain a narrower operation. It must not become a remote shell.

## Decision

`blendersessiond setup-owner` accepts only one versioned Blender Box setup
operation. The client creates a random Attempt ID and Launch ID before launch.
It sends a bounded raw JSON request on standard input. The request names only a
script artifact derived from the Attempt ID, plus its size, SHA-256, revision,
and a deadline no more than five minutes away. Executable, argument,
environment, working-directory, and output-path choices are not part of the
contract.

Each attempt has three create-once JSON records under `setup-attempts`:

- `request.json` records both identities and the SHA-256 of the exact raw request.
- `launch-receipt.json` records the detached keeper and suspended root process identities before resume.
- `terminal.json` records process outcome, bounded output, and whether cleanup is proven.

The detached keeper creates a kill-on-close Job Object, then creates system
PowerShell suspended with `PROC_THREAD_ATTRIBUTE_JOB_LIST`. The child inherits
only its three standard-stream pipe handles. The keeper durably publishes the
launch receipt before it resumes the root process. It sends the already
validated script bytes through standard input to a fixed bootstrap. That
bootstrap checks the exact byte count and SHA-256, decodes strict UTF-8 in
memory, and only then invokes the script block.

Status and stop require the complete Attempt, request-hash, and Launch fence.
Normal stop asks the exact keeper to terminate its Job. Fallback termination
opens the recorded keeper once and verifies its creation FILETIME through that
handle. PID lookup alone never authorizes termination. Owner-loss recovery
claims `tree_gone` only after the exact keeper and root are absent and the
named Job no longer exists. Otherwise the immutable terminal is
`cleanup_unverified`.

`windows-setup-owner-v1` is a runtime capability. Its probe creates an inert
suspended child with the same atomic Job-list mechanism, verifies membership,
terminates the Job before resume, and proves that it has no members. Missing
API support or an incompatible outer Job policy fails closed.

## Consequences

Blender Box can reconcile lost launch responses and repeat status or stop
without broad process discovery. A stale caller cannot act on a replacement
attempt. The keeper is the only process that retains the Job handle, so its
loss terminates the setup tree.

The protocol is Windows-only and private to Blender Box setup. Staging and its
ACL handoff remain Blender Box responsibilities. The owner rejects a staged
artifact that does not match the immutable request.
