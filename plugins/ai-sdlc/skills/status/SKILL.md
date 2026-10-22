---
name: status
description: "Show the current SDLC stage, approvals, progress, verification, review, and an interactive block with action buttons in Codex chat. Use when the user asks for SDLC status, next actions, or a refreshed status card. Showing status does not authorize implementation or approval."
---

# Show SDLC status in chat

Use the read-only `bin/sdlc chat --session ID --html` command from the latest
`[SDLC chat UI]` hook context. If that context is unavailable, run
`../../bin/sdlc chat --html` relative to this skill directory with the repository as cwd.
Without a session, the renderer shows information and a refresh action; do not invent
session identifiers or recreate guarded action prompts yourself.

Add `--language uk` or `--language ru` for Ukrainian or Russian labels. The default is English.
Redirect the literal HTML output into a new `sdlc-status-UNIQUE_ID.html` file in the task's writable
visualization directory outside the repository. If none is supplied in an enabled project,
use `ARTIFACTS_DIR/_ui/` (read `artifacts_dir` from `sdlc.config.json`; default `docs/sdlc`).
Generated files there are excluded from the code fingerprint. Do not save guarded fragments
among code files: a new code file would expire its own snapshot token. In a disabled project,
use a task-owned output directory if no visualization directory is supplied. Read the file
back. Preserve generated `data-prompt` values and snapshot tokens exactly. Render the block
by placing this content reference on its own line in the final response, outside code fences:

```text
visualize{"path":"/absolute/path/sdlc-status.html"}
```

Use the actual absolute file path. Do not open a browser. Do not replace the block with a
Markdown list or `:codex-followup` directives: some Codex surfaces display those as plain text.
The block uses real HTML buttons and `window.openai.sendFollowUpMessage` to request an action
in the same chat. It does not execute shell commands or modify workflow state from JavaScript.
If the client cannot render inline visualizations, clearly state that limitation and show
the plain `bin/sdlc chat` output as text. Do not call that fallback a working button UI.

Do not run verification, start a feature, switch features, approve a document, or unlock
tests merely to show status. Action prompts are for a human click or user message only.
Refresh after any change before rendering another card. Prior messages remain snapshots.
