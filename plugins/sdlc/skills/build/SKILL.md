---
name: build
description: SDLC stage 3b (Build) - implement an approved plan.md step by step with small verified increments. Use when the SDLC status says the code gate is open (intent, spec and plan approved) and implementation starts or continues.
---

# Stage 3b - Build against the approved plan

1. **Load context.** Read `plan.md`, `spec.md`, `progress.md` (if any) and `CLAUDE.md`.
   Create a branch if you are on the default branch (`feat/<slug>` or `fix/<slug>`).
2. **Follow the work sequence.** One step at a time:
   - write or update the test for the step first where practical;
   - implement the smallest change that makes it pass;
   - run the step's verification command and read the output;
   - append a line to `progress.md` in the form `- [x] Step N: result (commands run)`,
     where N is the step's number in the plan; the SDLC bar counts these lines.
   Commit at meaningful checkpoints with messages that reference the feature slug.
3. **Stay inside the plan.** Touch only files listed in *Files affected*. If you need
   another file, a different approach or a new dependency: stop, explain why, update
   `plan.md` and ask the user to re-approve (`sdlc approve plan`). Hooks will block code
   edits until they do.
4. **Parallelise safely.** Independent steps marked *(parallel)* can go to subagents or
   separate worktree sessions. Give each one the plan step, the files it owns and the
   verification command, and review their diffs before integrating.
5. **Never bypass the gates.** Do not edit code via shell redirects, `sed -i`, patches or
   generated scripts. Do not touch `docs/sdlc/*/approvals.json` or `.sdlc/`.
6. When all steps are done, continue with the **sdlc:verify** skill, then **sdlc:review**.
