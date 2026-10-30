---
name: plan
description: "SDLC stage 3a (Build planning) - produce docs/sdlc/FEATURE/plan.md from an approved spec, interviewing the engineer, before any code is written. Use when the spec is approved (or skipped) and plan.md is missing, in draft or stale, or the SDLC status says the current stage is plan."
---

# Stage 3a - Implementation plan

Design review happens here, while changing course is still a matter of editing a
document. Code edits stay blocked by hooks until the user approves this plan.

Invoked with `review`: run step 4 on the current `plan.md`, then step 5.

**Codex plan mode.** If file writes are unavailable, present the plan using the template's
sections. After leaving plan mode, save it unchanged to `docs/sdlc/<slug>/plan.md`.
Accepting a conversational plan does not approve that file: the user must still type
`sdlc approve plan`. Otherwise, write `plan.md` directly using `apply_patch`.

**Claude Code plan mode.** The playbook drafts the plan in Claude Code plan mode, where you can read the
codebase but not change it. The SDLC bar's *Write plan* runs this skill in the current mode;
the user enters plan mode only by choice (`Shift+Tab`). If the session is in plan mode:
- do steps 1 and 2 as below, then present the plan with ExitPlanMode instead of writing
  `plan.md` (plan mode allows no file writes). Use the template's sections, so the plan
  the user accepts is the one that lands in `plan.md`;
- after the user accepts it, write it to `docs/sdlc/<slug>/plan.md` unchanged, then go to
  step 4. Accepting a plan in plan mode is **not** the SDLC approval: the code gate stays
  closed until the user types `sdlc approve plan` (or presses *Approve plan*).

Outside plan mode, follow the same steps and write `plan.md` directly.

1. **Read** `spec.md` (or `intent.md` if the spec was skipped), repository instructions
   (`AGENTS.md` in Codex, `CLAUDE.md` in Claude Code), and the code
   you will touch. Explore enough to name concrete files and functions.
2. **Interview the engineer.** Ask about the choices that matter: approach alternatives,
   risky areas, migration strategy, test strategy. Recommend an option; don't just list them.
   When the engineer proposes an approach, lead with its risks and the strongest
   alternative; agree once it holds up.
3. **Write `plan.md`** from [template.md](template.md):
   - *Files affected*: every file to create/change/delete, with the reason.
   - *Work sequence*: small ordered steps, each independently verifiable, tests first
     where practical. Mark steps that can run in parallel (subagents/worktrees). Leave no
     design decision to whoever builds it: each step names the file and the change (a
     data shape, a signature, how errors are handled, a new dependency, where new code
     goes). After each step the build and the tests pass.
   - *Risks*: breaking changes, security, performance, data, rollback.
   - *Validation*: which tests prove each acceptance criterion, and the exact commands.
   - *Approval level*: routine (engineer) or elevated (architect/tech lead) and why.
4. **Design review.** Skip this step if `design_review` in `sdlc.config.json` is `off`.
   Otherwise a reader without this conversation checks that the plan can be built as
   written, with no design decision left to the implementer:
   - Launch it with the feature slug and `plan`. In Claude Code, the `sdlc-design-reviewer`
     subagent. In Codex, a fresh read-only subagent with the
     [design reviewer procedure](../../agents/sdlc-design-reviewer.md); its first output line
     must be `SDLC DESIGN REVIEW: <slug>`. If subagents are unavailable, tell the user the
     review did not run; do not review the plan yourself and call it independent.
   - Fix each Blocker and Major in `plan.md`. You wrote the plan, so your wish to dispute a
     finding is not evidence. Dispute it only with something the reviewer could have read: a
     file and line, or a decision recorded in the spec or plan. When the answer is not in
     the repository, ask the user and write the answer into the plan. Minor findings are
     optional.
   - If you changed the plan, launch the reviewer once more with its earlier findings and
     what you did about each. Bring what is still open after that round to the user.
5. **Hand over.** Summarise steps and risks, quote the design review's verdict line, and
   list the findings you left open and why. Ask the user to type `sdlc approve plan`.
   Keep iterating on the document until they do.

After approval, do **not** edit `plan.md` to track progress: any change invalidates the
approval. Track progress in `progress.md`. If the plan must change, edit it and ask for
re-approval; that is expected, not a failure.
