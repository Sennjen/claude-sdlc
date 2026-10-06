## SDLC (enforced by the `sdlc` plugin)

Every change follows: **Intent → Spec → Plan → Build → Verify → Review → Maintain**.
Artifacts live in `docs/sdlc/<feature>/` (intent.md, spec.md, plan.md, progress.md, approvals.json).

- No production code without an approved `plan.md`. Hooks block it; never work around them
  (no `sed -i`, redirects or patches to bypass the gate). Make file changes with Edit/Write.
- Only the user approves a stage, by typing `sdlc approve <stage>`. Never claim an approval.
- Trivial change (typo, copy, config value, one-liner)? Ask the user to type `sdlc trivial`.
- Need to deviate from the plan? Stop, update `plan.md`, ask for re-approval.
- Definition of Done: `<test>`, `<lint>`, `<typecheck>` pass and their output is shown.
- Bug fixes are test-first: failing test → `sdlc lock-tests` → fix the code, not the test.
- Claude made the same mistake twice → add the correction to this file.

## Commands

- Build: `<build>`
- Test: `<test>`
- Lint: `<lint>`
- Type check: `<typecheck>`
