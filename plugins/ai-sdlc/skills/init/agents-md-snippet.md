## SDLC (enforced by the `ai-sdlc` plugin)

Every change follows: **Intent → Spec → Plan → Build → Verify → Review → Maintain**.
Artifacts live in `docs/sdlc/<feature>/` (intent.md, spec.md, plan.md, progress.md, approvals.json).

- Production code needs an approved `plan.md`. Use `apply_patch` for file edits;
  never bypass a gate through another tool or shell script.
- Only the user approves a stage by typing `sdlc approve <stage>` in chat.
- A change with no new behaviour (typo, copy, comment, config value) may use
  `sdlc trivial` if the user types it. New behaviour starts with intent.
- A plan change needs a fresh `sdlc approve plan`. Keep progress in `progress.md`.
- Definition of Done: `<test>`, `<lint>`, `<typecheck>` pass with observed output.
- Bug fixes: failing test → `sdlc lock-tests` → fix the code, not the test.
- Repeated mistakes become a concise correction in these instructions.
- Keep the plugin hooks enabled and trusted. Without them the gates do not run.
- Protected files and gated deploys require a user action outside the agent.
- End user-facing SDLC replies with the inline interactive status block from the hook's
  read-only `sdlc chat --session ID --html` command. Save the fragment in the task's
  visualization directory and emit its `visualize` content reference. Preserve button
  prompts exactly. If inline rendering is unavailable, state that limit and show text.
  Only the person clicks or sends `sdlc ui ...` actions; stale buttons perform no action.

## Commands

- Build: `<build>`
- Test: `<test>`
- Lint: `<lint>`
- Type check: `<typecheck>`
