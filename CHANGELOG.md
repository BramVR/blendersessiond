# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `capabilities --require blender-box-v1` reports the exact identity-fencing and bounded-call contract for remote wrappers without touching Session state.

### Changed

- `call` and `stop` now require the exact opaque Session ID returned by `start` or `status`, preventing stale callers from acting on a replacement Session that reused the same name.
- `mcp-serve` now pins its compatible MCP SDK alongside BlenderMCP so upstream dependency drift cannot break the stdio bridge.
- `call --read-timeout` now supports bounded long-running addon work while retaining the 180-second default and enforcing a one-hour maximum.

## [0.1.1] - 2026-07-26

### Removed

- Upstream BlenderMCP telemetry is gone from managed Sessions: the vendored addon no longer contains the telemetry consent handler, preference, or UI (upstream's consent check granted itself consent when the addon was loaded outside Blender's Preferences, as managed Sessions do), and `mcp-serve` now runs the validated server with `DISABLE_TELEMETRY=true`, so no usage data, prompts, code, or screenshots are reported to the upstream backend.

## [0.1.0] - 2026-07-24

### Added

- Dependency-free project website with an interactive Session lifecycle guide, custom-domain search metadata, `llms.txt`, and GitHub Pages publishing.
- Cross-platform `doctor` checks for Blender discovery, platform support, writable state, and recorded Session Health.
- Named `start`, `status`, and `stop` lifecycle commands with isolated state, dynamic loopback ports, ownership checks, stale-record handling, process-tree termination, and retained logs.
- Direct `call` access to the raw BlenderMCP addon protocol with JSON parameters and serialized per-Session connections.
- `mcp-serve` wiring from MCP stdio clients to healthy named Sessions through the validated `blender-mcp` server.
- Optional existing-scene startup, live unsaved-changes reporting, and explicit stop-never-saves behavior.
- Pinned and locally patched BlenderMCP addon installation with third-party attribution and an auditable compatibility record.
- Fake-Blender lifecycle coverage and pinned real-Blender smoke coverage across Ubuntu, macOS, and Windows.
- Repo-owned agent skill for safe Session lifecycle, Blender MCP startup, direct addon calls, and save-before-stop handling.
- Tokenless PyPI Trusted Publishing and GitHub Release automation for validated stable version tags.

### Changed

- Managed Sessions disable Blender's startup splash through the supported Python preference.
- `BLENDERSESSIOND_BASE_MCP_PORT` overrides the first port considered during MCP port allocation, and the test suite now allocates ephemeral per-test port ranges so a real Session running on the developer machine can no longer pollute e2e results.
- Package metadata and installation documentation now support publishing and installing `blendersessiond` from PyPI.
