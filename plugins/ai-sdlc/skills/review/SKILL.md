---
name: review
description: "SDLC stage 5 (Deploy) - layered review and PR - independent agent code and security reviews against plan, spec and REVIEW.md, fixes, then a PR that links the SDLC artifacts. Only in repositories where the SDLC is enabled (prompts carry an [SDLC] status line): use after verification passes and before pushing, opening or merging a pull request."
---

# Stage 5 - Review and ship

Agents review mechanics, humans review intent and risk.

Invoked with `pr`: check the pre-flight and `sdlc state`. Go straight to step 5 (*Open the
PR*) only if `review` and `security` both have the verdict `ready` and `current` true;
otherwise run the independent reviews first. Claude's SDLC bar offers *Create PR* when both
reviews of the current code are ready.

1. **Pre-flight.** Verification from **ai-sdlc:verify** has passed in this session and
   `git status` shows only intended changes. The change and the artifacts in
   `docs/sdlc/<slug>/` are committed on the feature branch, so the review reads the commits
   the PR will carry.
2. **Independent agent reviews.** If this session already reviewed this change, continue at
   step 3 with those findings. Otherwise note the commit under review (`git rev-parse HEAD`)
   and launch two reviewers in parallel, each with the feature slug and the base branch:
   - **Code review.** In Claude Code, the `sdlc-reviewer` subagent. In Codex, a fresh
     read-only subagent with the [reviewer procedure](../../agents/sdlc-reviewer.md); its
     first output line must be `SDLC REVIEW: <slug>`.
   - **Security review.** In Claude Code, the `sdlc-security-reviewer` subagent. In Codex, a
     fresh read-only subagent with the
     [security procedure](../../agents/sdlc-security-reviewer.md); its first output line
     must be `SDLC SECURITY REVIEW: <slug>`.

   In Codex, each marker carries the actual slug and is followed by the summary, findings
   and the final verdict. The hook uses the markers to tell these reviews apart from other
   subagents. If subagents are unavailable, report that independent reviews are still
   needed; do not present a self-review as independent. Review categories and severity
   come from `REVIEW.md`. The plugin records each reviewer's verdict line with the code it
   read: once both say `READY FOR HUMAN REVIEW`, the SDLC bar offers *Create PR*, and a
   `CHANGES REQUIRED` from either makes it offer *Review again*. Any later code change makes
   both verdicts stale. In your report, quote each reviewer's verdict line exactly as it
   wrote it, then its findings.
3. **Triage findings** from both reviews. Fix every Blocker. Fix every Major or justify it
   in the PR. Minor findings are optional. When a finding names a defect class, fix every
   instance it lists. Do not widen the change to fix a Pre-existing finding: list it in the
   PR as a follow-up, and tell the user if it is severe. Do not argue with a finding without
   evidence.
4. **Repeat review.** Commit the fixes, re-run verification and note the new commit. Launch
   both reviewers once more, the same way as in step 2. Give each the commit the previous
   review read and its earlier findings with what you did about each. Each reads only
   `git diff <that commit>` (and `git status` for new files), checks each fix and reports
   new defects in that diff. It reports an earlier finding again only if its fix does not
   hold. If a Blocker is still open after that, report it to the user instead of opening
   the PR.
5. **Open the PR.** Push the feature branch and open the PR with
   `gh pr create` (GitHub), `glab mr create` (GitLab) or the forge's MCP tool; the plugin
   records the PR URL from its output. The description contains:
   - links to `intent.md`, `spec.md`, `plan.md`;
   - summary of changes and any deviations from the plan;
   - AC → test mapping and pasted verification output;
   - review outcome: both reviewers' verdicts, justified Majors, and Pre-existing findings
     as follow-ups;
   - risks, rollback, and what needs human attention (the risky files/decisions).
   Protected pushes and deploys ask in Claude Code. In Codex they are denied; the user
   must run them outside the agent.
6. **Human gate.** Merging requires a human approval via branch protection. Address
   reviewer comments with new commits, not force-pushes.
7. **Close the loop.** Recurring review findings become rules in `AGENTS.md` (Codex),
   `CLAUDE.md` (Claude Code), or a skill.
   Once the PR exists, the user closes the feature: *Close feature* in the SDLC bar, or
   `sdlc done`.
