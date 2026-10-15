---
name: intent
description: SDLC stage 1 (Plan) - capture a new feature, bug fix or change request as docs/sdlc/<feature>/intent.md. Only in repositories where the SDLC is enabled (prompts carry an [SDLC] status line): use when the user brings a new idea, feature request, bug report or ticket and no intent.md exists for it yet, or when the SDLC status says the current stage is intent.
---

# Stage 1 - Capture intent

Goal: a short, human-readable, machine-actionable `intent.md` that a product owner can
approve. It states the **problem and outcome**, not the solution design.

1. **Name it.** Pick a short kebab-case slug (`claims-status-page`, `fix-login-timeout`).
   Run `sdlc new <slug> [feature|bugfix|incident]` in the shell (Bash); if the shell cannot
   find `sdlc`, use the full path the [SDLC] status line gives. It creates
   `docs/sdlc/<slug>/intent.md` from [template.md](template.md) and makes it the active
   feature. If a ticket exists (Jira, GitHub issue), read it first and link it.
2. **Brainstorm with the user.** Ask focused questions, a few at a time, until you can fill
   every section: who has the problem, how they cope today, what "better" looks like, what
   must not change. Look at the code only to name affected systems, not to design.
3. **Write the intent.** Fill the template. Be concrete and brief (roughly half a page).
   - *Proposed outcome* is observable behaviour or a metric, not an implementation.
   - *Constraints* include security, compliance, performance, platforms, deadlines.
   - Anything you are unsure about goes to *Open questions*. Do not invent answers.
4. **Hand over for approval.** Show the user the file path and a short summary, list the
   open questions, and ask them to review/edit it and type `sdlc approve intent`.
   Do not start the spec before that. If the user edits the file, re-read it.

For small, well-understood changes (after approval of the intent) the user may type
`sdlc skip spec <reason>` to go straight to the plan.
