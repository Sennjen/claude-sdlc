---
name: init
description: "Enable the AI-native SDLC in the current repository - creates sdlc.config.json, repository instructions, REVIEW.md and docs/sdlc/. Use when the user asks to set up, enable or install the SDLC workflow in a project."
disable-model-invocation: true
---

# Enable the SDLC in this repository

The plugin's hooks stay inert until `sdlc.config.json` exists at the repo root, so write
that file **last** (after it exists, repository instructions and REVIEW.md become protected paths).

In Codex, first confirm the plugin's lifecycle hooks are enabled and trusted. If they
are skipped or unavailable, report that the workflow has no automatic enforcement.

1. **Detect the verification commands.** Inspect `package.json` scripts, `Makefile`,
   `pyproject.toml`, `build.gradle`, `Package.swift`, `Cargo.toml`, CI workflows. Find the
   single commands for test, lint/format check, type check and build. Prefer fast,
   non-interactive, non-watch variants (e.g. `npm test -- --watch=false`, `CI=1`).
   Show the list to the user and confirm it before continuing.
2. **Repository instructions.** In Codex, merge
   [agents-md-snippet.md](agents-md-snippet.md) into repo-root `AGENTS.md`. In Claude Code,
   merge [claude-md-snippet.md](claude-md-snippet.md) into `CLAUDE.md`.
   Create the target if missing and preserve existing content. Fill in the real
   commands. Keep the whole file under roughly one page: commands, conventions,
   architecture, common mistakes. If an architecture overview is missing, add 3-6 lines.
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
   - recommended repo settings: branch protection with required human review,
     CODEOWNERS for critical paths, a CI review integration driven by `REVIEW.md`, a CI
     security review (on GitHub, the `anthropics/claude-code-security-review` Action), and
     the branch protection rule that requires every review conversation to be resolved
     before merge;
   - in supported Codex clients, `$ai-sdlc:status` embeds an interactive status block in chat in place of the Claude bar;
     card buttons send guarded `sdlc ui ...` user messages; protected edits and gated
     deploy commands must be performed by the user outside the agent because Codex
     hooks cannot show an interactive approval request;
   - that they should commit the new files.
