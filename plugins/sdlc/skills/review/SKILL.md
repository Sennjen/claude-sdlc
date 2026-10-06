---
name: review
description: SDLC stage 5 (Deploy) - layered review and PR - independent agent review against plan, spec and REVIEW.md, fixes, then a PR that links the SDLC artifacts. Only in repositories where the SDLC is enabled (prompts carry an [SDLC] status line): use after verification passes and before pushing, opening or merging a pull request.
---

# Stage 5 - Review and ship

Agents review mechanics, humans review intent and risk.

Invoked with `pr` (the SDLC bar's *Create PR*, offered once a review of the current code
passed): check the pre-flight, then go straight to step 4.

1. **Pre-flight.** Verification from **sdlc:verify** has passed in this session and
   `git status` shows only intended changes. Artifacts in `docs/sdlc/<slug>/` are committed.
2. **Independent agent review.** Launch the `sdlc-reviewer` subagent (fresh context, no
   bias from writing the code) with the feature slug and the base branch. If the
   `/code-review` skill is available, run it too. Review categories and severity come
   from `REVIEW.md`. The plugin records the reviewer's verdict line with the code it read:
   `READY FOR HUMAN REVIEW` makes the SDLC bar offer *Create PR*, `CHANGES REQUIRED` makes
   it offer *Review again*. Any later code change makes the verdict stale.
3. **Triage findings.** Fix every Blocker. Fix every Major or justify it in the PR. Re-run
   verification and repeat the review once. If a Blocker is still open after that, report
   it to the user instead of opening the PR. Do not argue with a finding without evidence.
4. **Open the PR.** Push (the deploy gate asks the user to confirm) and open the PR with
   `gh pr create` (GitHub), `glab mr create` (GitLab) or the forge's MCP tool; the plugin
   records the PR URL from its output. The description contains:
   - links to `intent.md`, `spec.md`, `plan.md`;
   - summary of changes and any deviations from the plan;
   - AC → test mapping and pasted verification output;
   - risks, rollback, and what needs human attention (the risky files/decisions).
5. **Human gate.** Merging requires a human approval via branch protection. Address
   reviewer comments (including `@claude` mentions) with new commits, not force-pushes.
6. **Close the loop.** Recurring review findings become rules in `CLAUDE.md` or a skill.
   Once the PR exists, the user closes the feature: *Close feature* in the SDLC bar, or
   `sdlc done`.
