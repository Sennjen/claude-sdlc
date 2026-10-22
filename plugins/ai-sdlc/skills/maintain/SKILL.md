---
name: maintain
description: "SDLC stage 6 (Maintain) - turn incidents, alerts, metric breaches and repeated mistakes into new intent.md files, regression tests and repository instruction or skill updates. Only in repositories where the SDLC is enabled (prompts carry an [SDLC] status line): use when investigating a production incident, failing metric, flaky CI trend, postmortem, or repeated agent mistakes."
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

## Metrics worth watching
First-pass CI success, time from plan approval to PR, rework cycles per PR, review
comments needing human action, defect escape rate, time from alert to `intent.md`.
`.sdlc/audit.log` records approvals, gate denials and verify results for this repo.
