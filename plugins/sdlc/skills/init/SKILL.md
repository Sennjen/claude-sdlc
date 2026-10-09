---
name: init
description: Enable the AI-native SDLC in the current repository - creates sdlc.config.json, the SDLC section in CLAUDE.md (Claude Code) and/or AGENTS.md (Cursor), REVIEW.md and docs/sdlc/. Use when the user asks to set up, enable or install the SDLC workflow in a project, or to add Cursor or Claude Code to an existing setup.
disable-model-invocation: true
---

# Enable the SDLC in this repository

The plugin's hooks stay inert until `sdlc.config.json` exists at the repo root, so write
that file **last** (after it exists, CLAUDE.md, AGENTS.md and REVIEW.md become protected
paths).

**Hosts.** Ask which agent hosts the team uses: Claude Code, Cursor, or both (default: the
one you run in). Claude Code reads `CLAUDE.md`, Cursor reads `AGENTS.md`.

**Existing setup.** If `sdlc.config.json` already exists, only add the instruction file for
a new host (step 2). Keep the verify commands, `REVIEW.md`, approvals and the active feature
as they are. The instruction files are protected now: Claude Code asks the user to confirm
the edit, Cursor blocks it. When an edit is blocked, show the user the section to paste and
the file it goes into, then stop.

1. **Detect the verification commands.** Inspect `package.json` scripts, `Makefile`,
   `pyproject.toml`, `build.gradle`, `Package.swift`, `Cargo.toml`, CI workflows. Find the
   single commands for test, lint/format check, type check and build. Prefer fast,
   non-interactive, non-watch variants (e.g. `npm test -- --watch=false`, `CI=1`).
   Show the list to the user and confirm it before continuing.
2. **Instruction files.** For Claude Code, merge the section from
   [claude-md-snippet.md](claude-md-snippet.md) into the repo-root `CLAUDE.md`. For Cursor,
   merge the section from [agents-md-snippet.md](agents-md-snippet.md) into the repo-root
   `AGENTS.md`. Create a missing file and keep existing content. If the file already has an
   `## SDLC` section, update it in place instead of adding a second one. Fill in the real
   commands. Keep each file under roughly one page: commands, conventions, architecture,
   common mistakes. If an architecture overview is missing, add 3-6 lines.
3. **REVIEW.md.** If absent, create it from [review-template.md](review-template.md) and
   adapt the categories to the stack (for example add accessibility for UI apps).
4. **Git hygiene.** Add `.sdlc/` to `.gitignore` (local session state, audit log). The
   `docs/sdlc/**` artifacts, including `approvals.json`, **are** committed: they are the
   audit trail.
5. **Config (last).** Write `sdlc.config.json` from
   [config-template.json](config-template.json) with the confirmed `verify` commands.
   Adjust `test_paths` and `gated_commands` to the stack: add its own release and deploy
   commands, and remove entries that do not apply.
6. **Report to the user** in a short summary:
   - what was created;
   - the user-only chat commands: `sdlc approve intent|spec|plan`, `sdlc skip spec <reason>`,
     `sdlc trivial [reason|off]`, `sdlc unlock tests`, `sdlc feature <slug>`, `sdlc done`,
     `sdlc status`;
   - recommended repo settings outside the agent: branch protection with required human
     review, CODEOWNERS for critical paths, Claude PR review (the managed Code Review
     service or `anthropics/claude-code-action` driven by REVIEW.md);
   - for Cursor users: the plugin's hooks must be installed and enabled on each machine
     (see the plugin README);
   - that they should commit the new files.
