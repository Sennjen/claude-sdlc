---
name: maintain
description: "SDLC stage 6 (Maintain) - turn incidents, alerts, metric breaches and repeated mistakes into new intent.md files, regression tests and repository instruction or skill updates. Only in repositories where the SDLC is enabled (prompts carry an [SDLC] status line): use when investigating a production incident, failing metric, flaky CI trend, postmortem, or repeated agent mistakes, or when checking whether README, CLAUDE.md, AGENTS.md or other docs still match the code."
---

# Stage 6 - Close the loop

## Incident or metric breach → new intent
1. Gather evidence first: logs, metrics, error traces, deploy history, related PRs and
   their `docs/sdlc/*` artifacts. Quote facts, mark hypotheses as hypotheses.
2. Run `sdlc new <slug> incident` and fill `intent.md`, adding an **Evidence** section
   under *Problem*. *Proposed outcome* states the healthy metric/behaviour.
3. Hand over for `sdlc approve intent`; the fix then follows the normal flow
   (a small fix may use `sdlc skip spec <reason>`).

## Prevent recurrence
- **Regression test / eval:** every fixed incident gets a test or eval that would have
  caught it, wired into CI.
- **Institutional knowledge:** if the agent or the team made the same mistake twice, add a
  one-line rule to `AGENTS.md` (Codex), `CLAUDE.md` (Claude Code), or a skill
  (organisation-wide). Keep repository instructions short: replace stale rules.
- **Gates:** if a problem slipped through a gate, propose a change to `REVIEW.md`,
  `sdlc.config.json` (verify commands, gated commands) or CI.

## Documentation drift → document fixes or new intents
Run this when the user asks whether the docs still match the code, or after a large change.
1. List the living documents: repository instructions (`AGENTS.md`, `CLAUDE.md` in any
   directory), README files, `REVIEW.md` and docs outside the SDLC artifacts directory. The
   spec and plan of a closed feature describe one change at its time, and later features
   may supersede them: check those only when the user asks.
2. For each document, find the code it describes: the paths and symbols it names, else a
   search. Where a document names no code, suggest a link that makes the next check cheaper.
3. Read the code as it is now; do not rely on memory. Report only differences, each with the
   document's file:line and the code's file:line:
   - behaviour, commands or options the document describes that the code lacks or names
     differently;
   - fields, flags and config keys that differ;
   - a fixed value where the document describes logic, or the reverse;
   - another structure, pattern or library than the document describes.
   A document without drift gets one line: `No drift`.
4. In a large repository, give each document or directory to a fresh read-only subagent, so
   the main context keeps only the findings.
5. Act on the findings. Where the document is wrong, fix the document (`CLAUDE.md`,
   `AGENTS.md` and `REVIEW.md` ask the user first). Where the code is wrong, start a new
   intent for each problem (`sdlc new <slug> bugfix`); do not fix code from this skill.

## Metrics worth watching
First-pass CI success, time from plan approval to PR, rework cycles per PR, review
comments needing human action, defect escape rate, time from alert to `intent.md`.
`.sdlc/audit.log` records approvals, gate denials and verify results for this repo.
