# Review policy

Used by Claude PR review (managed Code Review / claude-code-action) and by the
`sdlc:review` skill. Humans review intent and risk; agents review mechanics.

## Passes (every PR)

| Pass | Checks |
|------|--------|
| Correctness | Logic errors, edge cases, error handling, concurrency, null/undefined paths |
| Plan conformance | Diff matches `docs/sdlc/<feature>/plan.md`; no unplanned files or scope creep |
| Spec conformance | Every acceptance criterion in `spec.md` is implemented and tested |
| Security | AuthN/AuthZ, input validation, secrets, injection, PII in logs, dependency risk |
| Tests | New behaviour covered; tests assert behaviour, not implementation; no weakened tests |
| Maintainability | Matches codebase conventions (CLAUDE.md); no dead code or duplication |

## Severity

- **Blocker**: incorrect behaviour, security issue, data loss, missing tests for new logic. Must fix.
- **Major**: likely bug, plan/spec deviation, missing error handling. Fix or justify in the PR.
- **Minor**: style, naming, small simplifications. Optional.

Report only findings you can point to a line for. No praise, no summaries of the diff.

## Human review required

- Paths owned in CODEOWNERS, auth, payments, data migrations, infrastructure, public APIs.
- Any PR opened while an agent pass still reports a Blocker.
