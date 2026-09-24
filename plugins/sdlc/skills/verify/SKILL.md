---
name: verify
description: SDLC stage 4 (Test) - give Claude a feedback loop - test-first bug fixes, locked tests, and a Definition of Done backed by real command output. Only in repositories where the SDLC is enabled (prompts carry an [SDLC] status line): use before reporting any coding task complete, when fixing a bug, or when tests, lint, typecheck or build fail.
---

# Stage 4 - Verify your own work

A task is **not done** until the project's verification commands pass and you have shown
their output. The Stop hook re-runs `verify` from `sdlc.config.json` and will send you
back if it fails, so run the checks yourself first.

## Definition of Done
1. Run every command from `sdlc.config.json` → `verify` (see `sdlc status`). Paste the
   relevant tail of the output in your report, including pass counts.
2. Every acceptance criterion from `spec.md` has at least one test that exercises it;
   list the mapping AC → test.
3. For UI changes, verify visually: run the app and take a screenshot (browser or
   simulator tools), compare with the spec's UX description.
4. Report failures honestly with their output, and claim only what a command you ran
   showed; name anything left unverified.

## Bug fix flow (test-first)
1. Write a test that reproduces the bug. Run it and confirm it **fails for the right reason**.
2. Run `sdlc lock-tests`. From now on hooks block edits to test files.
3. Fix the production code until the test passes. Do not weaken, skip or delete tests.
4. If the test itself is wrong, explain why and ask the user to type `sdlc unlock tests`.

## When checks fail
Read the full error, find the root cause, fix it, re-run. After two failed attempts at
the same error, stop and report what you tried instead of looping.
