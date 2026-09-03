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

The detached keeper creates an unnamed kill-on-close Job Object, then creates
system PowerShell suspended with `PROC_THREAD_ATTRIBUTE_JOB_LIST`. Because the
Job has no name and its handle is not inherited, the child cannot reopen or
retain it. The child inherits only its three standard-stream pipe handles. The
keeper durably publishes the launch receipt before it resumes the root process.
It sends the already validated script bytes through standard input to a fixed
bootstrap. That bootstrap checks the exact byte count and SHA-256, decodes
strict UTF-8 in memory, and only then invokes the script block.

Every state, attempt, record, marker, and script access opens the complete
Windows path chain without delete sharing and with reparse-point traversal
disabled. The owner rejects a reparse point in any ancestor. The authority root
must exist with DACL inheritance disabled. The owner also rejects a target with
a null DACL, an untrusted owner, or write-capable allow entries—including
inherit-only entries—for principals other than the interactive user, Local
System, built-in Administrators, and Creator Owner. Reserved compound allow
entries fail closed instead of being ignored. The handles remain open for the
protected operation so an ancestor cannot be renamed underneath it.

Status and stop require the complete Attempt, request-hash, and Launch fence.
Normal stop asks the exact keeper to terminate its Job. Fallback termination
opens the recorded keeper once and verifies its creation FILETIME through that
handle. PID lookup alone never authorizes termination. If a new unnamed-Job
keeper disappears before publishing a terminal, recovery cannot prove that no
same-token process duplicated its handle, so it records `cleanup_unverified`
even when the recorded keeper and root are gone. Access or query failure is
also unknown, not absence. Status reports the nonterminal,
`ownership_unverified` state so the caller can retry. Stop still attempts the
exact identity-fenced keeper termination, but reports the same retryable state
if process access or termination fails; it does not make that transient failure
immutable. Receipts from the earlier named-Job implementation remain readable
during upgrade; only those legacy attempts may prove `tree_gone` by additionally
proving that the exact named Job no longer exists.

`windows-setup-owner-v1` is a runtime capability. Its probe creates an inert
suspended child with the same atomic Job-list mechanism, verifies membership,
closes the keeper's sole Job handle before resume, and proves kill-on-close by
observing the root process exit. Missing API support or an incompatible outer
Job policy fails closed.

## Consequences

Blender Box can reconcile lost launch responses and repeat status or stop
without broad process discovery. A stale caller cannot act on a replacement
attempt. The keeper is the only process that retains the Job handle, so its
loss terminates the setup tree.

The protocol is Windows-only and private to Blender Box setup. Blender Box must
stage into an authority root with an explicit private DACL; inherited temporary
or profile-directory ACLs are not assumed safe. The owner independently
validates that path authority and rejects an artifact that does not match the
immutable request.
