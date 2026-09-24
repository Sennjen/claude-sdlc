---
name: review
description: SDLC stage 5 (Deploy) - layered review and PR - independent agent review against plan, spec and REVIEW.md, fixes, then a PR that links the SDLC artifacts. Only in repositories where the SDLC is enabled (prompts carry an [SDLC] status line): use after verification passes and before pushing, opening or merging a pull request.
---

# Stage 5 - Review and ship

Agents review mechanics, humans review intent and risk.

1. **Pre-flight.** Verification from **sdlc:verify** has passed in this session and
   `git status` shows only intended changes. Artifacts in `docs/sdlc/<slug>/` are committed.
2. **Independent agent review.** Launch the `sdlc-reviewer` subagent (fresh context, no
   bias from writing the code) with the feature slug and the base branch. If the
   `/code-review` skill is available, run it too. Review categories and severity come
   from `REVIEW.md`.
3. **Triage findings.** Fix every Blocker and Major (or justify it in the PR), re-run
   verification, repeat the review once. Do not argue with a finding without evidence.
4. **Open the PR.** Push (the deploy gate asks the user to confirm) and open a PR whose
   description contains:
   - links to `intent.md`, `spec.md`, `plan.md`;
   - summary of changes and any deviations from the plan;
   - AC → test mapping and pasted verification output;
   - risks, rollback, and what needs human attention (the risky files/decisions).
5. **Human gate.** Merging requires a human approval via branch protection. Address
   reviewer comments (including `@claude` mentions) with new commits, not force-pushes.
6. **Close the loop.** Recurring review findings become rules in `CLAUDE.md` or a skill.
   After merge the user types `sdlc done` to close the feature.
