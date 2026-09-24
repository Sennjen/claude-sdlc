---
name: plan
description: SDLC stage 3a (Build planning) - produce docs/sdlc/<feature>/plan.md from an approved spec, interviewing the engineer, before any code is written. Use when the spec is approved (or skipped) and plan.md is missing, in draft or stale, or the SDLC status says the current stage is plan.
---

# Stage 3a - Implementation plan

Design review happens here, while changing course is still a matter of editing a
document. Code edits stay blocked by hooks until the user approves this plan.

1. **Read** `spec.md` (or `intent.md` if the spec was skipped), `CLAUDE.md`, and the code
   you will touch. Explore enough to name concrete files and functions.
2. **Interview the engineer.** Ask about the choices that matter: approach alternatives,
   risky areas, migration strategy, test strategy. Recommend an option; don't just list them.
3. **Write `plan.md`** from [template.md](template.md):
   - *Files affected*: every file to create/change/delete, with the reason.
   - *Work sequence*: small ordered steps, each independently verifiable, tests first
     where practical. Mark steps that can run in parallel (subagents/worktrees).
   - *Risks*: breaking changes, security, performance, data, rollback.
   - *Validation*: which tests prove each acceptance criterion, and the exact commands.
   - *Approval level*: routine (engineer) or elevated (architect/tech lead) and why.
4. **Hand over.** Summarise steps and risks and ask the user to type `sdlc approve plan`.
   Keep iterating on the document until they do.

After approval, do **not** edit `plan.md` to track progress: any change invalidates the
approval. Track progress in `progress.md`. If the plan must change, edit it and ask for
re-approval; that is expected, not a failure.
