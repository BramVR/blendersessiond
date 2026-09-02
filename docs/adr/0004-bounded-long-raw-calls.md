# Bounded Long Raw Calls

## Status

Accepted (2026-09-02)

## Context

The addon wire client defaults to a 180-second response timeout and rejects
larger values. Blender renders and Scenario scripts can legitimately take
longer, but an unbounded socket read would make remote orchestration and
cleanup unpredictable.

## Decision

Keep 180 seconds as the default raw-call read timeout. Let callers override it
with `call --read-timeout SECONDS`, bounded to one hour. The same-machine
daemon owns this per-call socket limit; a remote orchestrator still owns the
shorter overall Scenario deadline and cleanup policy.

Connection and Health-probe timeouts remain short and unchanged. The override
applies only after a healthy, exact-identity-fenced Session has accepted a raw
call.

## Consequences

- Long renders and Scenario scripts can complete without removing the wire
  bound.
- Existing callers retain the 180-second behavior unless they opt in.
- A one-hour hard maximum prevents typo-driven indefinite waits.
- Blender Box must choose a timeout no later than its remaining Run deadline.
