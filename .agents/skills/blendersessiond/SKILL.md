---
name: blendersessiond
description: "Blender Session lifecycle and MCP bridge: doctor, start, inspect, automate, save, stop."
---

# blendersessiond

Own local GUI Blender Sessions for agent workflows. Use `blendersessiond` for
Blender lifecycle, Blender MCP startup, and direct addon calls.

## Rules

- Prefer `blendersessiond` on `PATH`. Inside its source repo, use
  `uv run blendersessiond` only when the installed binary is unavailable.
- Use `--json` for `doctor`, `start`, `status`, and `stop`. `call` always prints
  the addon JSON result.
- Treat a Session as healthy only when both its Blender process and addon socket
  are healthy.
- Reuse a healthy Session that matches the requested work. Do not stop a
  pre-existing Session unless the user asks.
- If a requested task-specific name belongs to an unrelated healthy Session,
  choose another name or ask; never replace that Session.
- Give concurrent or task-specific Sessions distinct names. Use the same
  `--name` for every lifecycle command and MCP registration.
- Use absolute paths. `--scene` must identify an existing `.blend` file.
- Prefer Blender MCP tools for normal scene work. Use raw `call` for bootstrap,
  focused fallback, or diagnostics; `execute_code` runs arbitrary Python.
- Never assume `stop` saves. It terminates the owned process tree without saving
  or prompting.
- Before stopping, inspect `unsaved_changes`. Save deliberately when changes
  must persist. Never overwrite an existing file without clear user intent.
- Do not kill Blender processes manually. Let `blendersessiond` enforce
  ownership and process-tree cleanup.
- Run the CLI on the machine hosting Blender. Remote transport such as SSH is
  outside this tool.

## Workflow

1. Check the host when install state or Blender discovery is uncertain:

   ```bash
   command -v blendersessiond
   blendersessiond doctor --json
   ```

2. Inspect recorded Sessions before starting another:

   ```bash
   blendersessiond status --json
   blendersessiond status --name "$SESSION" --json
   ```

   `status` exits `1` for missing, stale, or unhealthy Sessions. Read its JSON
   payload instead of treating every nonzero status as a CLI usage failure.

3. Start only when no suitable healthy Session exists:

   ```bash
   blendersessiond start --name "$SESSION" --json
   blendersessiond start --name "$SESSION" --scene /absolute/shot.blend --json
   ```

4. Perform the requested work. Prefer the configured Blender MCP server. For a
   direct addon command:

   ```bash
   blendersessiond call get_scene_info --name "$SESSION"
   blendersessiond call get_object_info --name "$SESSION" \
     --params '{"name":"Cube"}'
   ```

5. Verify scene state and Session Health after meaningful changes:

   ```bash
   blendersessiond call get_scene_info --name "$SESSION"
   blendersessiond status --name "$SESSION" --json
   ```

6. Save explicitly when work must persist. Save an already named scene:

   ```bash
   blendersessiond call execute_code --name "$SESSION" \
     --params '{"code":"bpy.ops.wm.save_mainfile()"}'
   ```

   Give a new scene a path:

   ```bash
   blendersessiond call execute_code --name "$SESSION" \
     --params '{"code":"bpy.ops.wm.save_as_mainfile(filepath=\"/absolute/scene.blend\")"}'
   ```

7. Stop only when requested or when cleaning up a Session started for this task.
   Check again immediately before stopping:

   ```bash
   blendersessiond status --name "$SESSION" --json
   blendersessiond stop --name "$SESSION" --json
   ```

   If `unsaved_changes` is `true`, save first or ask for the destination. If it
   is `unknown`, do not infer that stopping is safe. Leave the Session running
   when the user requests it or when persistence is unresolved.

## MCP Bridge

Start the named Session before the MCP client. Register one stdio server per
Session:

```json
{
  "mcpServers": {
    "blender": {
      "command": "blendersessiond",
      "args": ["mcp-serve", "--name", "default"]
    }
  }
}
```

`mcp-serve` requires `uvx`, validates Session Health, then connects the pinned
`blender-mcp` server to that Session. It does not start Blender. If an MCP
connection already failed because no healthy Session existed, start the Session
and reconnect or restart the MCP client; use direct `call` as a focused fallback.

## Recovery

- Run `doctor --json`, then targeted `status --name NAME --json`.
- Use the reported stdout and stderr log paths for startup failures.
- Treat process-alive/socket-dead as unhealthy.
- Let `start` reclaim only records it identifies as safely stale.
- Before stopping an unhealthy but owned Session, account for the possibility
  that unsaved state cannot be queried.
- Use `blendersessiond COMMAND --help` for current flags and exit conventions.
