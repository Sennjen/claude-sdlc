## SDLC (enforced by the `sdlc` plugin)

Every change follows: **Intent → Spec → Plan → Build → Verify → Review → Maintain**.
Artifacts live in `docs/sdlc/<feature>/` (intent.md, spec.md, plan.md, progress.md, approvals.json).

- The plugin adds an `[SDLC]` status line to every prompt. If `sdlc.config.json` exists and
  no `[SDLC]` line appears, the plugin's hooks are not running: stop and ask the user to
  install or enable the `sdlc` plugin before you change any file.
- No production code without an approved `plan.md`. Hooks block it; never work around them
  (no `sed -i`, redirects or patches to bypass the gate). Make file changes with the
  file-editing tools.
- Only the user approves a stage, by typing `sdlc approve <stage>`. Never claim an approval.
- A change with no new behaviour (typo, copy, comment, config value)? Ask the user to type
  `sdlc trivial`. A new option, flag or rule is a feature, however small: start with intent.
- Need to deviate from the plan? Stop, update `plan.md`, ask for re-approval.
- Definition of Done: `<test>`, `<lint>`, `<typecheck>` pass and their output is shown.
- Bug fixes are test-first: failing test → `sdlc lock-tests` → fix the code, not the test.
- The agent made the same mistake twice → add the correction to this file.

## Commands

- Build: `<build>`
- Test: `<test>`
- Lint: `<lint>`
- Type check: `<typecheck>`
