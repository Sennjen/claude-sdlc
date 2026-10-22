# Review policy

Read by the `ai-sdlc:review` code and security reviewers in Codex or Claude Code and by PR
review services that read `REVIEW.md`, such as Code Review for Claude Code. Humans review
intent and risk; agents review mechanics. Keep this file short: code owners add the
repository's own rules under *Always check* and *Security* and its skipped paths under
*Do not report*, and move repeated review comments here. Project context belongs in
AGENTS.md or CLAUDE.md.

## Passes (every PR)

| Pass | Checks |
|------|--------|
| Correctness | Logic errors, edge cases, error handling, concurrency, null/undefined paths |
| Plan conformance | Diff matches `docs/sdlc/<feature>/plan.md`; no unplanned files or scope creep |
| Spec conformance | Every acceptance criterion in `spec.md` is implemented and tested |
| Tests | New behaviour covered; tests assert behaviour, not implementation; no weakened tests |
| Maintainability | Matches repository instructions (AGENTS.md or CLAUDE.md); no dead code or duplication |

## Always check

- A change to a public API, CLI flag, config key, stored data format or schema stays backward
  compatible, or the PR says how to migrate and how to revert.
- A change that makes a line in AGENTS.md, CLAUDE.md or README.md out of date also updates it.

## Security

The security reviewer checks every PR separately from the passes above: AuthN/AuthZ, input
validation, injection, secrets, PII in logs, dependency risk. It reports only vulnerabilities
this change introduces, each with a concrete exploit.

## Do not report

- Anything CI or the verify commands in `sdlc.config.json` already enforce: formatting, lint,
  type errors.
- Generated files, lockfiles and vendored dependencies.

## Severity

| Severity | Code Review tag | Meaning | Action |
|----------|-----------------|---------|--------|
| Blocker | Important | Incorrect behaviour, security issue, data loss, missing tests for new logic | Must fix |
| Major | Important | Likely bug, plan/spec deviation, missing error handling | Fix, or justify in the PR |
| Minor | Nit | Style, naming, small simplifications | Optional |
| Pre-existing | Pre-existing | A defect this change did not introduce | Never blocks this change; report it separately |

## Reporting

- Cite a file and line in the source for every finding. A guess from a name is not evidence.
- Report a repeated defect once: name its class and list every file:line where it occurs, so
  one fix covers all of them.
- Report at most five Minor findings per review.
- Do not repeat a finding already reported on this change.
- Open the summary with the number of findings per severity. When there is nothing to fix,
  say so in one line.
- No praise and no recap of the diff.

## Human review required

- Paths owned in CODEOWNERS, auth, payments, data migrations, infrastructure, public APIs.
- Any PR opened while an agent pass still reports a Blocker.
