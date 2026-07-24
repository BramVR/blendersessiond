# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Cross-platform `doctor` checks for Blender discovery, platform support, writable state, and recorded Session Health.
- Named `start`, `status`, and `stop` lifecycle commands with isolated state, dynamic loopback ports, ownership checks, stale-record handling, process-tree termination, and retained logs.
- Direct `call` access to the raw BlenderMCP addon protocol with JSON parameters and serialized per-Session connections.
- `mcp-serve` wiring from MCP stdio clients to healthy named Sessions through the validated `blender-mcp` server.
- Optional existing-scene startup, live unsaved-changes reporting, and explicit stop-never-saves behavior.
- Pinned and locally patched BlenderMCP addon installation with third-party attribution and an auditable compatibility record.
- Fake-Blender lifecycle coverage and pinned real-Blender smoke coverage across Ubuntu, macOS, and Windows.

### Changed

- Managed Sessions disable Blender's startup splash through the supported Python preference.
